"""
Finds items from your own list (ingredients.json or cookingUtensils.json) in a photo.

It uses CLIP, a free image model that runs on your machine. CLIP compares the
photo against every name in your list and returns the names that fit best,
so every result is guaranteed to be a word from your list.

Test it from the terminal:
    python detector.py path/to/photo.jpg
"""

import hashlib
import json
import sys
from pathlib import Path

import torch
from PIL import Image, ImageOps
from transformers import CLIPModel, CLIPProcessor

MODEL_NAME = "openai/clip-vit-base-patch32"
ROOT = Path(__file__).parent
CACHE_DIR = ROOT / ".detector_cache"

# How each name is described to the model. Wording like this helps CLIP a lot.
PROMPTS = {
    "ingredients": "a photo of {}, a type of food",
    "utensils": "a photo of a {}, a kitchen item",
}

_model = None
_processor = None


def load_model():
    """Loads CLIP once (the first run downloads about 600 MB)."""
    global _model, _processor
    if _model is None:
        print(f"Loading {MODEL_NAME}...")
        _model = CLIPModel.from_pretrained(MODEL_NAME).eval()
        _processor = CLIPProcessor.from_pretrained(MODEL_NAME)
    return _model, _processor


def load_names(json_path):
    """Reads { "categories": { "herbs": ["basil", ...], ... } } into one list of unique names."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    names, seen = [], set()
    for items in data["categories"].values():
        for name in items:
            if name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
    return names


def _as_tensor(output, field):
    # Some versions of transformers return a tensor, others an object holding it
    if isinstance(output, torch.Tensor):
        return output
    value = getattr(output, field, None)
    return value if value is not None else output.pooler_output


def _normalize(t):
    return t / t.norm(dim=-1, keepdim=True)


def _crops(image):
    """The whole photo plus a 2x2 and a 3x3 grid of pieces, so several items in one photo can each be spotted."""
    w, h = image.size
    crops = [image]
    for n in (2, 3):
        for row in range(n):
            for col in range(n):
                box = (col * w // n, row * h // n, (col + 1) * w // n, (row + 1) * h // n)
                crops.append(image.crop(box))
    return crops


class ListDetector:
    def __init__(self, json_path, kind="ingredients"):
        self.model, self.processor = load_model()
        self.names = load_names(json_path)
        self.prompt = PROMPTS[kind]
        self.text_features = self._load_or_build_text_features(kind)

    def _load_or_build_text_features(self, kind):
        # Turning ~2,000 names into vectors takes a little while, so the result is
        # saved and reused until your list changes.
        key = hashlib.md5((MODEL_NAME + self.prompt + "|".join(self.names)).encode()).hexdigest()[:12]
        cache_file = CACHE_DIR / f"{kind}_{key}.pt"
        if cache_file.exists():
            return torch.load(cache_file)

        print(f"Preparing {len(self.names)} {kind} (only happens when the list changes)...")
        features = []
        with torch.no_grad():
            for start in range(0, len(self.names), 256):
                batch = [self.prompt.format(n) for n in self.names[start:start + 256]]
                inputs = self.processor(text=batch, return_tensors="pt", padding=True, truncation=True)
                out = self.model.get_text_features(**inputs)
                features.append(_normalize(_as_tensor(out, "text_embeds")))
        features = torch.cat(features)

        CACHE_DIR.mkdir(exist_ok=True)
        torch.save(features, cache_file)
        return features

    def detect(self, image, max_items=10, min_score=0.12):
        """
        Returns a list like [{"name": "tomato", "score": 0.41}, ...], best match first.

        max_items: the most names to return.
        min_score: raise it (e.g. 0.2) for fewer but safer results,
                   lower it (e.g. 0.07) to catch more items but get more mistakes.
        """
        image = ImageOps.exif_transpose(image).convert("RGB")  # fixes sideways phone photos
        crops = _crops(image)

        with torch.no_grad():
            inputs = self.processor(images=crops, return_tensors="pt")
            out = self.model.get_image_features(**inputs)
            image_features = _normalize(_as_tensor(out, "image_embeds"))
            scale = self.model.logit_scale.exp()
            probs = (scale * image_features @ self.text_features.T).softmax(dim=-1)

        # For each name, keep its best score across all the crops
        best = probs.max(dim=0).values
        top = torch.topk(best, k=min(max_items * 3, len(self.names)))

        results = []
        for score, index in zip(top.values.tolist(), top.indices.tolist()):
            if score >= min_score:
                results.append({"name": self.names[index], "score": round(score, 3)})
            if len(results) == max_items:
                break
        return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python detector.py path/to/photo.jpg")
        sys.exit(1)

    detector = ListDetector(ROOT / "ingredients.json", "ingredients")
    found = detector.detect(Image.open(sys.argv[1]))

    if not found:
        print("No ingredients from your list were recognized.")
    for item in found:
        print(f"{item['name']:30} {item['score']:.2f}")
