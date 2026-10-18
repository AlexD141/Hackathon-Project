"""
Finds recipes in Database/recipes_json that you can make with what you have.

Every recipe's ingredient lines ("1 pound jumbo shrimp, peeled and deveined")
are turned into names from ingredients.json, the same list people pick from on
the site ("shrimp"). Searching is then just comparing sets:

    have    = what the recipe needs that you've got
    missing = what it needs that you don't (salt, pepper and water are assumed)

Best first: most of your ingredients used for the fewest you'd have to buy,
then rating.

The index is built once (about 30 seconds for 13,000 recipes) and saved in
.data/recipe_index.json; it rebuilds by itself when the database changes.

Feeding a result into Aarush's graph code (Aarush Contrib/LangProcessing.ipynb):

    from recipe_search import RecipeIndex
    index = RecipeIndex.load()
    best = index.search(["chicken breast", "rice", "onion"])[0]
    graph = makeGraph(index.steps(best["id"]), nlp)   # list of step strings

Try it:
    python recipe_search.py chicken rice onion garlic
"""

import hashlib
import html
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATABASE = ROOT / "Database" / "recipes_json"
VOCAB_FILE = ROOT / "ingredients.json"
UTENSILS_FILE = ROOT / "cookingUtensils.json"
INDEX_FILE = ROOT / ".data" / "recipe_index.json"   # .data is never served to the browser

# Bump when matching rules change, so old indexes are rebuilt
INDEX_VERSION = 5

# Everyone has these; they never count as missing
STAPLES = {"water", "salt", "black pepper", "pepper", "ice", "ice cubes", "cooking spray",
           "salt and pepper", "warm water", "cold water", "boiling water", "hot water",
           "kosher salt", "sea salt", "table salt", "ground black pepper"}

# The database has a few recipes for pets; they're not dinner
PETS = re.compile(r"\b(dog|dogs|doggie|doggy|puppy|cat|kitty|pet|bird|horse)\s+(food|treats?|biscuits?|cookies?|cake)\b|\bfor (dogs|cats|pets)\b", re.I)

# Words that don't change what an ingredient is: "white rice" is fine with rice,
# but "brown sugar" isn't sugar and "chicken broth" isn't chicken.
MODIFIERS = {"white", "yellow", "fresh", "large", "small", "medium", "lean", "extra-lean", "boneless",
             "skinless", "whole", "plain", "frozen", "canned", "cooked", "uncooked", "dry", "dried",
             "raw", "ripe", "unsalted", "salted", "organic", "jumbo", "baby", "chopped", "diced",
             "minced", "sliced", "shredded", "grated", "ground", "all-purpose"}
# Cuts that count as the animal ("chicken thigh" covers a recipe that just says chicken)
PROTEINS = {"chicken", "beef", "pork", "turkey", "lamb", "veal", "duck"}
# General names that cover a whole category of ingredients.json ("pasta" covers spaghetti)
COVERS_CATEGORY = {"pasta": "pasta_and_noodles", "noodles": "pasta_and_noodles"}

# Utensils worth showing on a recipe card (matched against the step text)
SHOWN_UTENSILS = 3

# Diets and allergies (keep in sync with DIETS and ALLERGIES in server.py).
# A recipe is filtered out if any ingredient line contains one of these words.
MEAT = ["beef", "pork", "chicken", "turkey", "lamb", "veal", "bacon", "ham", "sausage", "chorizo",
        "pepperoni", "salami", "prosciutto", "duck", "venison", "steak", "meatball", "hot dog",
        "gelatin", "lard", "chuck", "brisket", "ribs", "pancetta", "bratwurst", "kielbasa"]
FISH = ["fish", "salmon", "tuna", "cod", "tilapia", "halibut", "trout", "anchov", "sardine",
        "mahi", "snapper", "catfish", "haddock", "swordfish", "bass"]
SHELLFISH = ["shrimp", "prawn", "crab", "lobster", "scallop", "clam", "mussel", "oyster", "squid",
             "calamari", "crawfish", "octopus"]
DAIRY = ["milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "ghee", "buttermilk", "parmesan",
         "mozzarella", "cheddar", "ricotta", "whey", "custard", "half-and-half"]
EGGS = ["egg", "mayonnaise"]
GLUTEN = ["flour", "bread", "pasta", "noodle", "wheat", "barley", "rye", "couscous", "cracker",
          "tortilla", "breadcrumb", "panko", "biscuit", "pie crust", "spaghetti", "macaroni",
          "beer", "seitan", "farro", "bulgur", "semolina", "croissant", "bun", "roll"]
GLUTEN_FREE_OK = ["gluten-free", "gluten free", "rice flour", "almond flour", "coconut flour",
                  "corn tortilla", "rice noodle", "cornstarch"]
SUGARY = ["sugar", "honey", "syrup", "molasses", "candy", "chocolate chip", "marshmallow"]
STARCHY = ["rice", "potato", "pasta", "noodle", "bread", "flour", "tortilla", "corn", "oat",
           "quinoa", "beans", "cracker", "cereal"]

ALLERGY_WORDS = {
    "peanuts": ["peanut"],
    "tree nuts": ["almond", "walnut", "pecan", "cashew", "pistachio", "hazelnut", "macadamia",
                  "pine nut", "brazil nut", "praline", "marzipan", "nutella"],
    "milk": DAIRY,
    "eggs": EGGS,
    "fish": FISH,
    "shellfish": SHELLFISH,
    "wheat": GLUTEN,
    "soy": ["soy", "tofu", "edamame", "miso", "tempeh", "tamari"],
    "sesame": ["sesame", "tahini"],
}
DIET_WORDS = {
    "vegetarian": MEAT + FISH + SHELLFISH,
    "vegan": MEAT + FISH + SHELLFISH + DAIRY + EGGS + ["honey"],
    "pescatarian": MEAT,
    "gluten-free": GLUTEN,
    "dairy-free": DAIRY,
    "halal": ["pork", "bacon", "ham", "lard", "pepperoni", "prosciutto", "pancetta", "gelatin",
              "wine", "beer", "rum", "vodka", "bourbon", "brandy", "liqueur", "whiskey", "sherry"],
    "kosher": ["pork", "bacon", "ham", "lard", "pepperoni", "prosciutto", "pancetta"] + SHELLFISH,
    "keto": SUGARY + STARCHY,
    "low-carb": SUGARY + STARCHY,
    "paleo": SUGARY + STARCHY + DAIRY + ["soy", "peanut", "lentil", "chickpea"],
}
# Words that look like a banned word but aren't ("butternut squash" is not butter)
NOT_BANNED = ["butternut", "peanut butter", "cream of tartar", "coconut milk", "almond milk",
              "oat milk", "soy milk", "coconut cream", "eggplant", "nutmeg", "buttercup",
              "cocoa butter", "apple butter", "ice cream cone"]


# ---------- Matching text to names ----------
def _forms(name):
    """'egg' -> egg, eggs; 'cherry' -> cherry, cherries; 'tomato' -> tomato, tomatoes."""
    out = {name}
    if name.endswith("y") and not name.endswith(("ay", "ey", "oy", "uy")):
        out.add(name[:-1] + "ies")
    elif name.endswith(("s", "x", "z", "ch", "sh", "o")):
        out.add(name + "es")
    if not name.endswith("s"):
        out.add(name + "s")
    if name.endswith("f"):
        out.add(name[:-1] + "ves")       # leaf -> leaves
    return out


def _names(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    seen = {}
    for items in data["categories"].values():
        for n in items:
            seen.setdefault(n.lower(), n)
    return seen     # lowercase -> spelling on the site


class Matcher:
    """Finds the longest list names inside a piece of text, whole words only."""

    def __init__(self, names):
        self.canonical = {}
        for low, original in names.items():
            for f in _forms(low):
                self.canonical.setdefault(f, original)
        # Longest first, so "black pepper" wins over "pepper" at the same spot
        alternatives = sorted(self.canonical, key=len, reverse=True)
        self.regex = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, alternatives)) + r")(?![\w-])")

    def find(self, text):
        return [self.canonical[m.group(1)] for m in self.regex.finditer(text)]


def clean_line(line):
    """'2 (8 ounce) packages cream cheese, softened' -> ['packages cream cheese', 'softened']"""
    line = line.lower().replace("®", "").replace("™", "").replace("�", "")
    line = re.sub(r"\([^)]*\)", " ", line)            # (8 ounce)
    line = re.sub(r"\bcloves?\s+(?=garlic)", "", line)  # "cloves garlic" is garlic, not the spice
    line = re.sub(r"\b(to taste|or more|as needed|for garnish|divided|optional)\b.*", "", line)
    line = re.sub(r"[\d./¼-¾⅐-⅞]+", " ", line)
    return [p.strip() for p in line.split(",") if p.strip()]


def _plain(name, vocab):
    """'white rice' -> 'rice' (if that's a known name), else None."""
    words = name.lower().split()
    while len(words) > 1 and words[0] in MODIFIERS:
        words = words[1:]
        if " ".join(words) in vocab:
            return vocab[" ".join(words)]
    return None


UNIT_WORDS = r"(?:pounds?|lbs?|ounces?|oz|cups?|tablespoons?|tbsps?|teaspoons?|tsps?|pinch(?:es)?|dash(?:es)?|cans?|packages?|packets?|envelopes?|jars?|bottles?|boxes?|bags?|containers?|sticks?|slices?|pieces?|quarts?|pints?|gallons?|liters?|ml|grams?|g|kg|heads?|bunch(?:es)?|sprigs?|stalks?|large|medium|small|whole)"


def unknown_name(line):
    """A short name for a line we couldn't match: '1 cup Cap'n Crunch cereal' -> "cap'n crunch cereal"."""
    pieces = clean_line(line)
    name = pieces[0] if pieces else ""
    name = re.sub(rf"^(?:{UNIT_WORDS}\b\s*)+", "", name).strip(" -")
    return name[:40]


def _best(names):
    """The most specific name in a phrase: longest, and the last one on a tie
    (English puts the main noun last: 'shredded cheddar cheese')."""
    return max(reversed(names), key=len) if names else None


def parse_ingredient(line, matcher, vocab=None):
    """
    One ingredient line -> list of requirements, each a list of acceptable names.
    'salt and pepper to taste'      -> [["salt"], ["black pepper"]]
    'butter or margarine'           -> [["butter", "margarine"]] (either will do)
    'cooked white rice'             -> [["white rice", "rice"]]
    Lines with no known ingredient return [] and are ignored.
    """
    pieces = clean_line(line)
    # The ingredient is normally before the first comma ("shrimp, peeled"), but
    # not always ("2 skinless, boneless chicken breast halves"): use the first
    # comma piece that names something.
    text = next((p for p in pieces if matcher.find(p)), "")
    needs = []
    for part in re.split(r"\s+and\s+|\s*&\s*", text):
        options = []
        for alt in re.split(r"\s+or\s+|/", part):
            found = _best(matcher.find(alt))
            for name in (found, vocab and found and _plain(found, vocab)):
                if name and name not in options:
                    options.append(name)
        if options and options not in needs:
            needs.append(options)
    return needs


# ---------- Index ----------
def _fingerprint():
    files = sorted(DATABASE.glob("*.json"))
    h = hashlib.sha1(f"{INDEX_VERSION}|{len(files)}".encode())
    h.update(str(max((f.stat().st_mtime for f in files), default=0)).encode())
    for p in (VOCAB_FILE, UTENSILS_FILE):
        h.update(str(p.stat().st_mtime).encode())
    return h.hexdigest()[:16]


def _minutes(value):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", str(value or ""))
    if not m or not any(m.groups()):
        return None
    days, hours, mins = (int(g or 0) for g in m.groups())
    return days * 1440 + hours * 60 + mins


def _image(raw):
    img = raw.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("url")
    return img if isinstance(img, str) and img.startswith("https://") else None


def build_index():
    vocab = _names(VOCAB_FILE)
    ingredient_matcher = Matcher(vocab)
    utensil_names = _names(UTENSILS_FILE)
    utensil_names.update({"oven": "oven"})
    utensil_matcher = Matcher(utensil_names)

    recipes = []
    for path in sorted(DATABASE.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for i, r in enumerate(data.get("recipes", [])):
            steps = [s["text"] if isinstance(s, dict) else s for s in r.get("steps", [])]
            lines = r.get("ingredients_raw", [])
            if not steps or not lines:
                continue
            name = html.unescape(r.get("name") or path.stem).strip()
            if PETS.search(name):
                continue
            needs, unknown = [], []
            for line in lines:
                parsed = parse_ingredient(line, ingredient_matcher, vocab)
                if not parsed:
                    other = unknown_name(line)
                    if other and other not in STAPLES and other not in unknown:
                        unknown.append(other)
                for req in parsed:
                    if req not in needs:
                        needs.append(req)
            utensils = []
            for u in utensil_matcher.find(" ".join(steps).lower()):
                if u not in utensils:
                    utensils.append(u)
            raw = r.get("raw_recipe") or {}
            rating = raw.get("aggregateRating") or {}
            recipes.append({
                "id": path.stem if i == 0 else f"{path.stem}-{i + 1}",
                "file": path.name,
                "n": i,
                "name": name,
                "url": data.get("source_url", ""),
                "image": _image(raw),
                "minutes": _minutes(raw.get("totalTime")),
                "rating": float(rating.get("ratingValue") or 0),
                "ratings": int(float(rating.get("ratingCount") or 0)),
                "needs": needs,
                "unknown": unknown,
                "lines": [l.lower() for l in lines],
                "utensils": utensils,
                "steps": len(steps),
            })
    return recipes


class RecipeIndex:
    def __init__(self, recipes):
        self.recipes = recipes
        self.by_id = {r["id"]: r for r in recipes}
        self.vocab = _names(VOCAB_FILE)
        self.categories = json.loads(VOCAB_FILE.read_text(encoding="utf-8"))["categories"]

    @classmethod
    def load(cls, rebuild=False):
        """Reads .data/recipe_index.json, building it first if missing or out of date."""
        key = _fingerprint()
        if not rebuild and INDEX_FILE.exists():
            try:
                saved = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
                if saved.get("key") == key:
                    return cls(saved["recipes"])
            except ValueError:
                pass
        print(f"Indexing recipes in {DATABASE} (only happens when the database changes)...", file=sys.stderr)
        recipes = build_index()
        INDEX_FILE.parent.mkdir(exist_ok=True)
        INDEX_FILE.write_text(json.dumps({"key": key, "recipes": recipes}), encoding="utf-8")
        print(f"Indexed {len(recipes)} recipes.", file=sys.stderr)
        return cls(recipes)

    # ----- what you have -----
    def _have(self, ingredients):
        """Your names, plus the plainer names they cover: 'white rice' covers 'rice',
        'chicken thigh' covers 'chicken'. ('cream cheese' does not cover 'cream'.)"""
        have = {i.lower().strip() for i in ingredients if i and i.strip()}
        covered = set(have)
        for name in have:
            plain = _plain(name, self.vocab)
            if plain:
                covered.add(plain.lower())
            first = name.split()[0]
            if first in PROTEINS:
                covered.add(first)
            if name in COVERS_CATEGORY:
                covered.update(n.lower() for n in self.categories.get(COVERS_CATEGORY[name], []))
        return covered

    @staticmethod
    def _banned(recipe, preferences):
        words = []
        for d in preferences.get("diets", []):
            words += DIET_WORDS.get(d, [])
        for a in preferences.get("allergies", []):
            words += ALLERGY_WORDS.get(a, [a])          # custom allergies match their own text
        if not words:
            return False
        if "gluten-free" in preferences.get("diets", []) or "wheat" in preferences.get("allergies", []):
            ok = GLUTEN_FREE_OK
        else:
            ok = []
        for line in recipe["lines"]:
            for safe in NOT_BANNED + ok:
                line = line.replace(safe, " ")
            if any(re.search(rf"\b{re.escape(w)}", line) for w in words):
                return True
        return False

    # ----- search -----
    def search(self, ingredients, utensils=(), preferences=None, limit=12, max_missing=None):
        """
        Returns the best recipes for what you have, best first:
        [{"id", "name", "url", "image", "minutes", "rating", "have", "missing", "utensils", ...}]
        """
        have = self._have(ingredients)
        if not have:
            return []
        mine = {u.lower() for u in utensils}
        preferences = preferences or {}

        scored = []
        for r in self.recipes:
            needs = [n for n in r["needs"] if not (set(x.lower() for x in n) & STAPLES)]
            if not needs:
                continue
            got = [n for n in needs if any(x.lower() in have for x in n)]
            if not got:
                continue
            # Lines we couldn't name ("Cap'n Crunch cereal") are missing too, in their own words
            missing = [n[0] for n in needs if n not in got] + r["unknown"]
            if max_missing is not None and len(missing) > max_missing:
                continue
            # Each thing you'd have to buy costs a point, each of yours it uses earns
            # one back, so a dish using 4 of yours and needing 2 more beats one
            # using 1 of yours and needing 1. Ties: fewer to buy, then rating.
            score = (
                len(missing) - len(got),
                len(missing),
                -(r["rating"] * math.log10(r["ratings"] + 1)),   # well rated by many people
            )
            scored.append((score, r, got, missing))

        scored.sort(key=lambda s: s[0])
        results = []
        for score, r, got, missing in scored:
            if preferences and self._banned(r, preferences):
                continue
            uses = r["utensils"]
            results.append({
                "id": r["id"],
                "name": r["name"],
                "url": r["url"],
                "image": r["image"],
                "minutes": r["minutes"],
                "time": f"{r['minutes']} min" if r["minutes"] else "",
                "rating": r["rating"],
                "ratings": r["ratings"],
                "have": [next((x for x in n if x.lower() in have), n[0]) for n in got],
                "missing": missing,
                "utensils": uses[:SHOWN_UTENSILS],
                "missing_utensils": [u for u in uses if mine and u.lower() not in mine and u != "oven"],
                "steps": r["steps"],
            })
            if len(results) == limit:
                break
        return results

    # ----- one recipe -----
    def recipe(self, recipe_id):
        """The full recipe from the database, in the scraper's format, or None."""
        r = self.by_id.get(recipe_id)
        if r is None:
            return None
        data = json.loads((DATABASE / r["file"]).read_text(encoding="utf-8"))
        return {**data["recipes"][r["n"]], "id": recipe_id, "source_url": data.get("source_url", "")}

    def steps(self, recipe_id):
        """Step texts as a list of strings: the input Aarush's makeGraph(steps, nlp) expects."""
        recipe = self.recipe(recipe_id)
        if recipe is None:
            raise KeyError(recipe_id)
        return [s["text"] if isinstance(s, dict) else s for s in recipe.get("steps", [])]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python recipe_search.py ingredient [ingredient ...]   (quote names with spaces)")
    index = RecipeIndex.load(rebuild="--rebuild" in sys.argv)
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    for r in index.search(wanted, limit=10):
        print(f"{r['name'][:45]:45} missing {len(r['missing'])}: {', '.join(r['missing'][:5]) or '-'}")
        print(f"{'':45} uses: {', '.join(r['have'])}   [{r['id']}]")
