"""
Identifies ingredients in an uploaded photo by calling a vision model.

Why this replaced the local pipeline: CLIP produces one vector per image, so a
photo containing fifteen things averages into a vector that matches everything
food-shaped equally well. Adding an object detector in front of it got us from
4/21 to 12/21 on the test photo, but it still cannot read the label on a tin -
and a pantry shelf is mostly tins, jars and boxes. A vision model reads labels
and handles many objects per photo natively, which is the whole problem.

It also keeps the guarantee the original detector.py had: every name returned is
checked against your list before it goes out, so the output can never contain a
word you did not supply.

    export GROQ_API_KEY=...          # Windows: set GROQ_API_KEY=...
    pip install -U pydantic pillow
    python recognizer.py food.jpg
    python recognizer.py drawer.jpg utensils
"""

import base64
import io
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Literal

from PIL import Image, ImageOps
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
VOCAB_FILE = ROOT / "ingredients_common.json"
GROQ_API_KEY_FILE = ROOT / ".data" / "groq_api_key"


def get_groq_api_key():
    api_key = os.environ.get("GROQ_API_KEY")
    if api_key:
        return api_key
    try:
        api_key = GROQ_API_KEY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return api_key or None

# Which list each kind is matched against. Ingredients use the curated common
# subset - 500 names keeps the prompt small and the model focused.
VOCAB_FILES = {
    "ingredients": VOCAB_FILE,
    "utensils": ROOT / "cookingUtensils.json",
}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# The free tier is rate-limited, so one call per upload and nothing speculative.
# Tried in order; add more vision models here as Groq offers them. Falling
# through to the next model costs nothing when the first one works.
MODELS = ["qwen/qwen3.8-27b"]
MODEL = MODELS[0]

# HTTP statuses worth trying another model for, rather than giving up on.
TRANSIENT = (429, 500, 502, 503)

# Longest rate-limit pause (seconds) worth sitting through before giving up.
MAX_WAIT = 30

# Groq caps base64 images at 4 MB, and phone photos are often bigger. Labels
# stay readable at this size.
MAX_SIDE = 1600

PROMPT = """You are identifying food ingredients in a photograph for a kitchen \
inventory app.

List every distinct food item you can see. Work from two kinds of evidence:
- what the food looks like, for loose or unpackaged items
- text printed on packaging, for anything in a tin, jar, box or bag - read the \
label and use it, since a sealed container cannot be identified by shape alone

Rules:
- Use ONLY names from the allowed list below. If you see something real that is \
not on the list, leave it out rather than substituting a near-miss.
- One entry per distinct item. Do not list the same thing twice under two names.
- Do not guess at things you cannot actually see. An empty shelf should return \
an empty list.
- Prefer the general name over an oddly specific one: "salmon", not "Atlantic salmon".

Allowed list ({count} names):
{vocabulary}"""

UTENSIL_PROMPT = """You are identifying cooking utensils and kitchen equipment in \
a photograph for a kitchen inventory app.

List every distinct utensil, tool, pot, pan or appliance you can see. Use its \
shape, material and any printed branding or labels as evidence.

Rules:
- Use ONLY names from the allowed list below. If you see something real that is \
not on the list, leave it out rather than substituting a near-miss.
- One entry per distinct item. Do not list the same thing twice under two names.
- Do not guess at things you cannot actually see. An empty counter should return \
an empty list.
- Do not list food.

Allowed list ({count} names):
{vocabulary}"""

PROMPTS = {"ingredients": PROMPT, "utensils": UTENSIL_PROMPT}


class Item(BaseModel):
    name: str = Field(description="The ingredient, copied exactly from the allowed list.")
    confidence: Literal["high", "medium", "low"] = Field(
        description="How certain you are this item is present in the photo.")
    evidence: Literal["appearance", "label"] = Field(
        description="'label' if you read it off packaging, 'appearance' if from how it looks.")


class Result(BaseModel):
    items: List[Item]


def load_vocab(path=VOCAB_FILE):
    """Flat list of allowed names. Falls back to the full list if the curated one is absent."""
    if not path.exists() and path == VOCAB_FILE:
        path = ROOT / "ingredients.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    names, seen = [], set()
    for items in data["categories"].values():
        for n in items:
            if n.lower() not in seen:
                seen.add(n.lower())
                names.append(n)
    return names


# Written out by hand: Groq's strict mode rejects the $defs/$ref that pydantic
# generates for nested models. Result still validates the reply afterwards.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "evidence": {"type": "string", "enum": ["appearance", "label"]},
                },
                "required": ["name", "confidence", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _shrink(image_bytes):
    """Upright JPEG no bigger than MAX_SIDE, so it fits Groq's size limit."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def _post_with_wait(req):
    """
    Sends req and returns the reply text. The free tier allows ~2 photos a
    minute; when Groq says the window frees up in a few seconds, waiting beats
    failing - up to MAX_WAIT seconds in total.
    """
    waited = 0.0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.load(res)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            wait = float(e.headers.get("retry-after") or MAX_WAIT + 1) + 1
            if e.code != 429 or waited + wait > MAX_WAIT:
                raise
            print(f"  rate limited, retrying in {wait:.0f}s...", file=sys.stderr)
            time.sleep(wait)
            waited += wait


def _groq(image_b64, mime, prompt, api_key):
    last = None
    for model in MODELS:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ]}],
            # Structured output means we parse a schema, not scrape prose.
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "result", "strict": True, "schema": RESULT_SCHEMA}},
            "temperature": 0,
        }
        req = urllib.request.Request(GROQ_URL, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "kitchen-app/1.0",  # Groq's firewall blocks urllib's default agent
        })
        try:
            return _post_with_wait(req)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            last = f"{model} returned {e.code}: {detail}"
            if e.code in TRANSIENT:
                print(f"  {model} busy, trying next...", file=sys.stderr)
                continue
            raise RuntimeError(last)  # A real error (bad key, bad request) should surface immediately.
    if last and " 429:" in last:
        raise RuntimeError("Groq's rate limit for this API key is used up. Wait a bit and try again.")
    raise RuntimeError(f"All models busy. Last error: {last}")


# Swapping provider is one entry here - the rest of the app does not change.
PROVIDERS = {"groq": _groq}


def recognize(image_bytes, mime="image/jpeg", vocab=None, provider="groq", api_key=None,
              kind="ingredients"):
    """
    Returns [{"name": ..., "confidence": ..., "evidence": ...}, ...].

    kind: "ingredients" or "utensils" - picks the prompt and the default list.

    Raises RuntimeError with a readable message if the key is missing or the
    model returns something unparseable - the caller turns that into an HTTP error.
    """
    vocab = vocab or load_vocab(VOCAB_FILES[kind])
    api_key = api_key or get_groq_api_key()
    if not api_key:
        raise RuntimeError(
            "No API key. Set GROQ_API_KEY in your environment or add it to .data/groq_api_key "
            "(get one at https://console.groq.com/keys).")

    prompt = PROMPTS[kind].format(count=len(vocab), vocabulary=", ".join(vocab))
    image_bytes, mime = _shrink(image_bytes)
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        raw = PROVIDERS[provider](image_b64, mime, prompt, api_key)
    except RuntimeError:
        raise
    except Exception as e:  # e.g. no internet, timeout
        raise RuntimeError(f"Couldn't reach {provider}: {e}") from e

    try:
        parsed = Result.model_validate_json(raw)
    except Exception as e:
        raise RuntimeError(f"Could not parse model output: {e}\nGot: {raw[:400]}") from e

    # The prompt asks it to stay inside the list; this enforces it. A model can
    # always ignore an instruction, so the guarantee has to live in code.
    allowed = {n.lower(): n for n in vocab}
    out, seen = [], set()
    for item in parsed.items:
        key = item.name.lower().strip()
        if key in allowed and key not in seen:
            seen.add(key)
            out.append({"name": allowed[key], "confidence": item.confidence,
                        "evidence": item.evidence})
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python recognizer.py path/to/photo.jpg [ingredients|utensils]")

    path = Path(sys.argv[1])
    kind = sys.argv[2] if len(sys.argv) > 2 else "ingredients"
    if kind not in VOCAB_FILES:
        sys.exit(f"Unknown kind {kind!r}. Use ingredients or utensils.")
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    vocab = load_vocab(VOCAB_FILES[kind])
    print(f"vocabulary: {len(vocab)} names | model: {MODEL}\n")

    try:
        found = recognize(path.read_bytes(), mime, vocab, kind=kind)
    except RuntimeError as e:
        sys.exit(str(e))

    if not found:
        print(f"No {kind} from your list were recognized.")
    for item in found:
        print(f"{item['name']:26} {item['confidence']:8} ({item['evidence']})")
