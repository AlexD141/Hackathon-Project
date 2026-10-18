"""
Runs Aarush's recipe optimizer (Aarush Contrib/) on a recipe from the database.

His code lives in LangProcessing.ipynb, so this loads the notebook's code
cells directly (skipping the demo cell that calls spacy.load) - when he
updates the notebook, the site picks it up on the next server start. Nothing
of his is copied or changed here.

Flow:
    recipe steps (JSON) -> .data/recipe_steps/<id>.txt, one step per line
    -> his makeGraph(steps, nlp, available) builds the dependency graph and
       assigns physical pots and plates (reusing them, with a wash between)
    -> his render(nodes) writes it back out as numbered steps
       ("Meanwhile, ..." marks steps that don't depend on the one before)

The fewest utensils: his assignVessels raises "No available vessel for 'pot'"
when it runs out, so we start with one of each kind and add one of whatever
it asks for until the recipe fits. What's left is the minimum it needs.

Try it:
    python aarush_pipeline.py american-chop-suey-ii_7bd29a27f951
"""

import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent
AARUSH_DIR = ROOT / "Aarush Contrib"
NOTEBOOK = AARUSH_DIR / "LangProcessing.ipynb"
STEPS_DIR = ROOT / ".data" / "recipe_steps"     # .data is never served to the browser

# Kinds of vessel his code knows (his `utensils` + `final_utensils`), with display names
VESSEL_NAMES = {"pot": "Pot", "skillet": "Skillet", "pan": "Pan", "blender": "Blender",
                "plate": "Plate", "bowl": "Bowl"}

# His model only knows the six above, so baking recipes (dishes, sheets, mixing
# bowls) and saucepans came out as "0 utensils" for about half the database.
# These extend his lists after loading, without changing his files. Each maps to
# what can stand in for it, in his LOGICAL_UTENSIL format.
EXTRA_VESSELS = {
    "saucepan": ["saucepan", "pot"],
    "dish": ["dish", "casserole"],          # baking dish, casserole dish (spaCy lemma is the last word)
    "casserole": ["casserole", "dish"],
    "sheet": ["sheet"],                     # baking sheet
    "wok": ["wok", "skillet", "pan"],
    "bowl": ["bowl", "plate"],              # mixing bowl as a working vessel, as well as a destination
}
VESSEL_NAMES.update({"saucepan": "Saucepan", "dish": "Baking dish", "casserole": "Casserole dish",
                     "sheet": "Baking sheet", "wok": "Wok"})
MAX_PER_KIND = 6    # give up rather than loop if a recipe somehow needs more than this

_module = None
_nlp = None


def load_aarush():
    """Aarush's notebook code as a module (his functions: makeGraph, finishTimes, render, ...)."""
    global _module
    if _module is not None:
        return _module
    if str(AARUSH_DIR) not in sys.path:
        sys.path.insert(0, str(AARUSH_DIR))      # his cells import Node and GraphToAbridgedRecipe
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    module = types.ModuleType("LangProcessing")
    module.__file__ = str(NOTEBOOK)
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        if "spacy.load(" in source:              # the demo cell: loads a model and prints a sample
            continue
        exec(compile(source, str(NOTEBOOK), "exec"), module.__dict__)
    for kind, stand_ins in EXTRA_VESSELS.items():
        if kind not in module.utensils:
            module.utensils.append(kind)
        module.LOGICAL_UTENSIL.setdefault(kind, stand_ins)
    _module = module
    return module


def nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")      # the model his demo uses
        except OSError as e:
            raise RuntimeError("spaCy's English model isn't installed. Run: "
                               "python -m spacy download en_core_web_sm") from e
    return _nlp


def write_steps(recipe_id, steps):
    """Saves the steps as a .txt file, one per line, and returns its path."""
    STEPS_DIR.mkdir(parents=True, exist_ok=True)
    path = STEPS_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', recipe_id)}.txt"
    path.write_text("\n".join(" ".join(s.split()) for s in steps) + "\n", encoding="utf-8")
    return path


def read_steps(path):
    """The .txt back into a list of strings (what his makeGraph takes)."""
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _fit(steps, a):
    """Runs his makeGraph with the fewest vessels that work. Returns (nodes, available)."""
    available = {kind: 1 for kind in VESSEL_NAMES}
    while True:
        try:
            roots, nodes = a.makeGraph(steps, nlp(), available=dict(available))
            return nodes, available
        except ValueError as e:
            m = re.search(r"No available vessel for '([^']+)'", str(e))
            if not m:
                raise
            kind = m.group(1)
            available[kind] = available.get(kind, 0) + 1
            if available[kind] > MAX_PER_KIND:
                raise RuntimeError(f"This recipe needs more than {MAX_PER_KIND} of '{kind}' at once.") from e


def _label(vessel, counts):
    """'pot#0' -> 'Pot' (or 'Pot 1' when two pots are in use)."""
    kind, _, n = vessel.partition("#")
    name = VESSEL_NAMES.get(kind, kind.capitalize())
    return f"{name} {int(n) + 1}" if counts.get(kind, 0) > 1 else name


def optimize(recipe_id, steps, have_utensils=()):
    """
    Runs the pipeline on one recipe. Returns:
      {"steps": ["1. Season pork ...", ...],          his render() output, one line per step
       "utensils": ["Pot", "Plate"], "utensil_count": 2,
       "washes": 1, "minutes": 14.0,                  critical path from his finishTimes()
       "original_steps": [...], "steps_file": ".data/recipe_steps/<id>.txt",
       "missing_kinds": ["blender"]}                   kinds you didn't list in your kitchen
    """
    a = load_aarush()
    path = write_steps(recipe_id, steps)
    lines = read_steps(path)
    nodes, _ = _fit(lines, a)

    used = []
    for n in nodes:
        for v in (n.loc, n.src):
            if v and "#" in v and v not in used:
                used.append(v)
    counts = {}
    for v in used:
        kind = v.split("#")[0]
        counts[kind] = counts.get(kind, 0) + 1

    rendered = [re.sub(r"^\d+\.\s*", "", line) for line in a.render(nodes).splitlines() if line.strip()]
    seconds = a.finishTimes(nodes)[0]

    # Compare with the kitchen the person told us about (names from cookingUtensils.json)
    mine = " ".join(u.lower() for u in have_utensils)
    missing = []
    if mine:
        for kind in counts:
            words = {"pot": ("pot", "saucepan", "dutch oven"), "pan": ("pan",), "skillet": ("skillet", "frying pan"),
                     "plate": ("plate", "platter"), "bowl": ("bowl",), "blender": ("blender",)}.get(kind, (kind,))
            if not any(w in mine for w in words):
                missing.append(kind)

    return {
        "steps": rendered,
        "utensils": [_label(v, counts) for v in used],
        "utensil_count": len(used),
        "washes": sum(1 for n in nodes if n.washBefore),
        "minutes": round(seconds / 60, 1),
        "original_steps": lines,
        "steps_file": str(path.relative_to(ROOT)).replace("\\", "/"),
        "missing_kinds": missing,
    }


if __name__ == "__main__":
    from recipe_search import RecipeIndex
    if len(sys.argv) < 2:
        sys.exit("Usage: python aarush_pipeline.py <recipe id from recipe_search.py>")
    index = RecipeIndex.load()
    rid = sys.argv[1]
    result = optimize(rid, index.steps(rid))
    print(f"{index.by_id[rid]['name']}: {result['utensil_count']} utensils "
          f"({', '.join(result['utensils'])}), {result['washes']} washes, {result['minutes']} min\n")
    for i, line in enumerate(result["steps"], 1):
        print(f"{i}. {line}")
