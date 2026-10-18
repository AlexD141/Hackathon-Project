"""
Combines the two recipe engines:

  recipe_search.py (RecipeIndex)  finds and ranks recipes for what you have. Fast on all
                                  13,000 recipes, assumes salt/pepper/water, filters diets
                                  and allergies.
  planner.py (RecipeBook)         adds what the website shows on each card and in the plan:
                                  the utensil check ("Do you have these?"), swaps, rating
                                  order, cuisines and meals, servings, and the cooking plan.

Recipes are read one at a time when a result needs them, so the server starts quickly.

Used by server.py:
    KITCHEN = KitchenSearch()
    KITCHEN.search(ingredients, utensils, preferences, limit, tools_yes, tools_no)
    KITCHEN.cook_plan(plan_id, utensils)   # "Cook this": Aarush's step order in the planner's layout
"""

import copy
import html
import json
import re
from pathlib import Path

import planner
from planner import RecipeBook
from recipe_search import RecipeIndex

ROOT = Path(__file__).parent
DATABASE = ROOT / "Database" / "recipes_json"
CANDIDATES = 300   # how many of the index's best matches get the utensil check


class LazyRecipeBook(RecipeBook):
    """planner.RecipeBook, but recipes are read one at a time instead of all at startup."""

    def _load(self):
        if self._recipes is None:
            self.known = planner.load_known_names(self.ingredients_json)
            self._recipes = {}

    def get(self, recipe_id):
        self._load()
        if recipe_id not in self._recipes:
            stem, _, number = str(recipe_id).rpartition("~")
            path = self.folder / f"{stem}.json"
            # Only plain file names inside the recipe folder
            if not stem or Path(stem).name != stem or not number.isdigit() or not path.is_file():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                recipe = data.get("recipes", [])[int(number)]
            except (ValueError, OSError, IndexError):
                return None
            if not recipe.get("steps") or not recipe.get("ingredients_raw"):
                return None
            self._recipes[recipe_id] = self._prepare(dict(recipe), data)
        return self._recipes.get(recipe_id)

    def _prepare(self, recipe, data):
        """Same preparation planner.RecipeBook._load does for every recipe."""
        recipe["source_url"] = data.get("source_url")
        recipe["name"] = html.unescape(str(recipe.get("name", "")))
        recipe["ingredients_raw"] = [html.unescape(str(r)) for r in recipe["ingredients_raw"]]
        recipe["steps"] = [
            {**st, "text": html.unescape(str(st.get("text", "")))} if isinstance(st, dict) else html.unescape(str(st))
            for st in recipe["steps"]
        ]
        names, lines, display = [], {}, {}
        for raw in recipe["ingredients_raw"]:
            for piece in planner.split_ingredient_line(raw, self.known):
                n = planner.core_name(piece, self.known)
                if not n or n.lower() in planner.IGNORED:
                    continue
                if n not in names:
                    names.append(n)
                    display[n] = " ".join(planner.clean_tokens(piece)) or n
                lines.setdefault(n, []).append([planner.singular(w) for w in planner.clean_tokens(piece)])
        recipe["_names"], recipe["_lines"], recipe["_display"] = names, lines, display
        recipe["_text"] = " ".join(recipe["ingredients_raw"]).lower()
        return recipe


class KitchenSearch:
    def __init__(self):
        self.book = LazyRecipeBook(DATABASE, ROOT / "ingredients.json")
        self._index = None

    def index(self):
        if self._index is None:
            self._index = RecipeIndex.load()
        return self._index

    def plan(self, plan_id):
        return self.book.plan(plan_id)

    # ---------- "Cook this": Aarush's steps, planner's layout ----------
    @staticmethod
    def index_id(plan_id):
        """'some-recipe~0' -> 'some-recipe', 'some-recipe~1' -> 'some-recipe-2' (recipe_search.py's ids)."""
        stem, _, number = str(plan_id).rpartition("~")
        return stem if number == "0" else f"{stem}-{int(number) + 1}"

    def cook_plan(self, plan_id, utensils=()):
        """The planner's plan (time, servings, cost, ingredients, nutrition...) with its steps
        replaced by Aarush's optimized order. Falls back to the planner's own steps if his
        pipeline can't handle the recipe."""
        base = self.book.plan(plan_id)
        if base is None:
            return None
        plan = copy.deepcopy(base)
        recipe = self.book.get(plan_id)
        step_texts = [st["text"] if isinstance(st, dict) else st for st in recipe.get("steps", [])]
        try:
            from aarush_pipeline import optimize
            # Decode "&#39;" etc. first: his parser splits sentences on ";"
            result = optimize(self.index_id(plan_id), [html.unescape(t) for t in step_texts], list(utensils))
            steps = self._timeline(result.get("steps") or [], base["steps"])
        except Exception as err:  # spaCy missing, or his parser hit something it doesn't handle
            print("Aarush's optimizer couldn't plan", plan_id, "-", repr(err))
            plan["note"] = "Showing the standard plan for this recipe (the step optimizer couldn’t read it)."
            return plan
        if not steps:
            plan["note"] = "Showing the standard plan for this recipe."
            return plan

        plan["steps"] = steps
        plan["total_minutes"] = round(max(st["_end"] for st in steps))
        for st in steps:
            st.pop("_end", None)
        dishes = [u for u in result.get("utensils", []) if u]
        if dishes:
            plan["dishes"] = dishes
            plan["dishes_saved"] = max(0, base.get("dishes_as_written", len(dishes)) - len(dishes))
        plan["note"] = "Steps are ordered by the optimizer so you can do things at the same time and reuse pans."
        return plan

    @staticmethod
    def _timeline(texts, planner_steps):
        """Turns Aarush's step sentences into the planner's timeline format."""
        def words(text):
            return set(planner.words_of(text)) - {"the", "and", "a", "to", "in", "of", "with", "until", "about"}

        steps, clock, last_start, last_text = [], 0.0, 0.0, ""
        for raw in texts:
            text = html.unescape(str(raw)).strip()
            if not text:
                continue
            parallel = bool(re.match(r"meanwhile\b", text, re.I))
            if parallel:
                text = re.sub(r"^meanwhile,?\s*", "", text, flags=re.I)
            text = text[:1].upper() + text[1:]   # his splitter can leave "add chicken broth..."
            is_preheat = bool(re.match(r"preheat\b", text, re.I))
            is_wash = bool(re.match(r"wash\b", text, re.I))
            if not text.endswith((".", "!", "?")):
                text += "."

            # Borrow amounts, utensils and hands-on/hands-off from the planner's closest step
            mine = words(text)
            best, overlap = None, 0.0
            for ps in planner_steps:
                if ps.get("prep"):
                    continue
                theirs = words(ps["text"])
                if mine and theirs:
                    score = len(mine & theirs) / len(mine | theirs)
                    if score > overlap:
                        best, overlap = ps, score
            match = best if overlap >= 0.5 else None

            minutes = planner.parse_minutes(text)
            if minutes is None:
                minutes = match["minutes"] if match else (2 if is_wash else 1)
            cwords = set(planner.words_of(text))
            hands_off = (match["hands_off"] if match else False) or (
                minutes >= 5 and bool(cwords & planner.WAIT_WORDS) and not (cwords & {"stir", "stirring"}))
            utensils = match["utensils"] if match else [m[1] for m in planner.find_vessel_mentions(text)]

            start = last_start if parallel else clock
            end = start + minutes
            steps.append({
                "start": planner.fmt(start),
                "end": planner.fmt(end),
                "minutes": round(minutes, 1),
                "text": text,
                "kind": "wait" if hands_off else "cook",
                "hands_off": hands_off,
                "utensils": utensils,
                "while": [planner.short(last_text)] if parallel and last_text else [],
                "amounts": (match.get("amounts") if match else []) or [],
                "prep": None,
                "_end": end,
            })
            # His order means each step waits for the one before it, unless it's a "Meanwhile"
            # step. Preheating the oven is the exception: it always runs in the background.
            clock = max(clock, start + (0.25 if is_preheat else minutes))
            if not parallel:
                last_start, last_text = start, text
        if steps:
            final_end = max(st["_end"] for st in steps)
            steps[-1]["_end"] = final_end
        return steps

    def search(self, ingredients, utensils=(), preferences=None, limit=12, tools_yes=(), tools_no=()):
        """{"recipes": [...cards...], "questions": [...tools that would unlock more...]}"""
        index = self.index()
        candidates = index.search(ingredients, utensils, preferences or {}, limit=CANDIDATES)

        owned = {planner.tool_kind(u) for u in utensils} | set(tools_yes)
        owned.discard(None)
        refused = set(tools_no)

        ready, waiting = [], []
        for c in candidates:
            entry = index.by_id.get(c["id"])
            if entry is None:
                continue
            plan_id = f"{Path(entry['file']).stem}~{entry['n']}"
            recipe = self.book.get(plan_id)
            if recipe is None:
                continue
            plan = self.book.plan(plan_id)

            # Utensil check: hide recipes needing a tool you said "no" to; ask about the rest
            needed = set(plan.get("requires", [])) - owned
            if needed & refused:
                continue
            if needed:
                waiting.append((needed, plan))
                continue

            # Swaps for what's missing, worked out on the planner's view of the recipe
            lines = recipe["_lines"]
            planner_missing = [n for n in recipe["_names"]
                               if not any(planner.have_matches(h, t) for h in ingredients for t in lines.get(n, []))]
            swaps = planner.find_swaps(recipe, planner_missing, ingredients)
            missing = [m for m in c["missing"]
                       if not any(planner.name_matches(m, s["name"]) or planner.name_matches(m, s["for"]) for s in swaps)]
            ready.append((planner.rating_rank(recipe), plan_id, c, missing, plan, swaps))

        # Highest rated first; recipes with the same rating keep the index's order
        ready.sort(key=lambda r: -r[0])

        questions = []
        if len(ready) < planner.MIN_RESULTS and waiting:
            weight, label = {}, {}
            for needed, plan in waiting:
                for k in needed:
                    weight[k] = weight.get(k, 0) + 1 / len(needed)
                    label.setdefault(k, planner.KIND_LABELS.get(k) or plan["requires_labels"].get(k, k))
            for k, _ in sorted(weight.items(), key=lambda x: -x[1])[:3]:
                questions.append({
                    "kind": k,
                    "label": label[k],
                    "utensil": planner.KIND_UTENSIL.get(k),
                    "unlocks": sum(1 for needed, _ in waiting if k in needed),
                })

        cards = []
        for _, plan_id, c, missing, plan, swaps in ready[:limit]:
            card = self.book._card(plan_id, c["have"], missing, plan, swaps)
            card["recipe_id"] = c["id"]      # the id Aarush's "Optimize steps" uses
            card["url"] = c.get("url")
            cards.append(card)
        return {"recipes": cards, "questions": questions}


if __name__ == "__main__":
    import sys
    wanted = sys.argv[1:] or ["chicken breast", "onion", "garlic"]
    k = KitchenSearch()
    result = k.search(wanted, utensils=["skillet", "saucepan", "cutting board", "chef's knife", "oven", "mixing bowl"])
    for card in result["recipes"]:
        print(f"{card['name'][:40]:40} ★{card['rating']} | have {card['have']} | need {card['missing'][:4]} | swaps {len(card['swaps'])}")
    print("asks about:", [q["label"] for q in result["questions"]])