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

    export GEMINI_API_KEY=...        # Windows: set GEMINI_API_KEY=...
    pip install -U google-genai pydantic
    python recognizer.py food.jpg
    python recognizer.py drawer.jpg utensils
"""

import base64
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
VOCAB_FILE = ROOT / "ingredients_common.json"

# Which list each kind is matched against. Ingredients use the curated common
# subset - 500 names keeps the prompt small and the model focused.
VOCAB_FILES = {
    "ingredients": VOCAB_FILE,
    "utensils": ROOT / "cookingUtensils.json",
}

# The free tier is rate-limited, so one call per upload and nothing speculative.
# Tried in order: the free tier returns 503 "high demand" on popular models often
# enough that a single-model client will visibly fail for real users. Falling
# through to the next model costs nothing when the first one works.
MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash",
          "gemini-3.8-pro"]
MODEL = MODELS[0]

# Errors worth trying another model for, rather than giving up on.
TRANSIENT = ("503", "service_unavailable", "high demand", "429",
             "resource_exhausted", "overloaded", "unavailable")

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


def _gemini(image_b64, mime, prompt, api_key):
    from google import genai

    client = genai.Client(api_key=api_key) if api_key else genai.Client()
    last = None
    for model in MODELS:
        try:
            interaction = client.interactions.create(
                model=model,
                input=[
                    {"type": "text", "text": prompt},
                    {"type": "image", "data": image_b64, "mime_type": mime},
                ],
                # Structured output means we parse a schema, not scrape prose.
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": Result.model_json_schema(),
                },
            )
            return interaction.output_text
        except Exception as e:
            last = e
            if any(t in str(e).lower() for t in TRANSIENT):
                print(f"  {model} busy, trying next...", file=sys.stderr)
                continue
            raise  # A real error (bad key, bad schema) should surface immediately.
    raise RuntimeError(f"All models busy. Last error: {last}")


# Swapping provider is one entry here - the rest of the app does not change.
PROVIDERS = {"gemini": _gemini}


def recognize(image_bytes, mime="image/jpeg", vocab=None, provider="gemini", api_key=None,
              kind="ingredients"):
    """
    Returns [{"name": ..., "confidence": ..., "evidence": ...}, ...].

    kind: "ingredients" or "utensils" - picks the prompt and the default list.

    Raises RuntimeError with a readable message if the key is missing or the
    model returns something unparseable - the caller turns that into an HTTP error.
    """
    vocab = vocab or load_vocab(VOCAB_FILES[kind])
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No API key. Set GEMINI_API_KEY in your environment "
            "(get one at https://aistudio.google.com/apikey).")

    prompt = PROMPTS[kind].format(count=len(vocab), vocabulary=", ".join(vocab))
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        raw = PROVIDERS[provider](image_b64, mime, prompt, api_key)
    except Exception as e:
        raise RuntimeError(f"{provider} call failed: {e}") from e

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
