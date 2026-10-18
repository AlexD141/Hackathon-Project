"""
Recipe search + cooking-plan optimizer. No AI model: plain rules and a scheduler.

    RecipeBook.search(...)  finds recipes in Database/recipes_json that match what you have
    RecipeBook.plan(id)     turns a recipe into an optimized plan:
        1. Steps are split into small tasks; ingredient prep ("1 onion, diced") becomes tasks too.
        2. A dependency graph is built: a task waits for the last task that used the same pan
           or the same ingredient; baking waits for the oven.
        3. One cook does the hands-on work; simmering/baking/preheating runs on its own, so
           other tasks fit into those waits. Longest chains go first; preheating is timed to
           finish right when it's needed.
        4. Pans of the same kind are reused when one is finished before the next is needed,
           and one cutting board covers all prep (with a reminder to wash it after raw meat).

Test from the terminal:
    python planner.py                  (lists a few recipes)
    python planner.py paella           (plans the first recipe whose name contains "paella")
"""

import html
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent

# ======================================================================
# Ingredient names
# ======================================================================
FRACTIONS = "¼½¾⅓⅔⅛⅜⅝⅞"
UNIT_WORDS = {
    "cup", "cups", "c", "tablespoon", "tablespoons", "tbsp", "tbsps", "tbs", "teaspoon", "teaspoons", "tsp",
    "tsps", "pound", "pounds", "lb", "lbs", "ounce", "ounces", "oz", "gram", "grams", "g", "kilogram", "kg",
    "ml", "milliliter", "milliliters", "liter", "liters", "l", "pint", "pints", "quart", "quarts", "gallon",
    "gallons", "clove", "cloves", "can", "cans", "package", "packages", "pkg", "jar", "jars", "bottle",
    "bottles", "stick", "sticks", "slice", "slices", "piece", "pieces", "pinch", "pinches", "dash", "dashes",
    "bunch", "bunches", "head", "heads", "stalk", "stalks", "sprig", "sprigs", "handful", "envelope",
    "container", "box", "bag", "fluid", "fl", "large", "medium", "small", "whole", "inch", "inches",
}
FILLER_WORDS = {
    "fresh", "freshly", "chopped", "diced", "minced", "sliced", "finely", "thinly", "coarsely", "roughly",
    "boneless", "skinless", "cooked", "uncooked", "frozen", "thawed", "softened", "melted", "divided",
    "packed", "optional", "about", "plus", "more", "to", "taste", "or", "and", "of", "for", "the", "a", "an",
    "jumbo", "extra", "ripe", "organic", "low-sodium", "reduced-sodium", "unsalted", "salted", "prepared",
}
IGNORED = {"water", "ice", "ice water", "hot water", "cold water", "warm water", "boiling water"}

ALLERGEN_WORDS = {
    "peanuts": ["peanut"],
    "tree nuts": ["almond", "walnut", "cashew", "pecan", "pistachio", "hazelnut", "macadamia", "pine nut"],
    "milk": ["milk", "butter", "cheese", "cream", "yogurt", "parmesan", "mozzarella", "cheddar", "ricotta", "whey", "ghee"],
    "eggs": ["egg", "mayonnaise"],
    "fish": ["salmon", "tuna", "cod", "tilapia", "trout", "anchovy", "sardine", "fish", "halibut", "mahi"],
    "shellfish": ["shrimp", "prawn", "crab", "lobster", "clam", "mussel", "scallop", "oyster", "squid", "calamari"],
    "wheat": ["flour", "bread", "pasta", "wheat", "noodle", "tortilla", "cracker", "breadcrumb", "panko", "couscous", "spaghetti", "macaroni"],
    "soy": ["soy", "tofu", "edamame", "miso", "tempeh"],
    "sesame": ["sesame", "tahini"],
}
MEAT_WORDS = ["beef", "pork", "chicken", "turkey", "lamb", "bacon", "ham", "sausage", "chorizo", "steak",
              "veal", "duck", "prosciutto", "salami", "pepperoni", "venison", "ground meat", "meatball"]
SEAFOOD_WORDS = ALLERGEN_WORDS["fish"] + ALLERGEN_WORDS["shellfish"]
ANIMAL_WORDS = ["milk", "butter", "cheese", "cream", "yogurt", "egg", "honey", "gelatin", "mayonnaise", "ghee"]
DIET_EXCLUDES = {
    "vegetarian": MEAT_WORDS + SEAFOOD_WORDS + ["gelatin", "broth"],
    "vegan": MEAT_WORDS + SEAFOOD_WORDS + ANIMAL_WORDS + ["broth"],
    "pescatarian": MEAT_WORDS,
    "dairy-free": ALLERGEN_WORDS["milk"],
    "gluten-free": ALLERGEN_WORDS["wheat"] + ["barley", "rye"],
}
PORK_WORDS = ["pork", "bacon", "ham", "prosciutto", "pancetta", "lard", "chorizo", "pepperoni", "salami", "gelatin"]
HIGH_CARB_WORDS = ["sugar", "flour", "bread", "pasta", "rice", "potato", "noodle", "tortilla", "honey",
                   "maple syrup", "corn", "oat", "cracker", "cereal", "bun"]
DIET_EXCLUDES.update({
    "halal": PORK_WORDS + ["wine", "beer", "rum", "vodka", "whiskey", "bourbon", "brandy", "liqueur", "sake"],
    "kosher": PORK_WORDS + ALLERGEN_WORDS["shellfish"],
    "keto": HIGH_CARB_WORDS,
    "low-carb": HIGH_CARB_WORDS,
    "paleo": ["sugar", "flour", "bread", "pasta", "rice", "noodle", "bean", "lentil", "peanut", "milk",
              "cheese", "yogurt", "cream", "corn", "oat", "soy", "tofu"],
})


def singular(word):
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def words_of(text):
    return [singular(w) for w in re.findall(r"[a-zà-ÿ'-]+", text.lower())]


def load_known_names(json_path):
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    known = {}
    for items in data.get("categories", {}).values():
        for name in items:
            name = name["name"] if isinstance(name, dict) else name
            known.setdefault(" ".join(words_of(name)), name)
    return known


def clean_tokens(raw):
    """'1 (15 ounce) can black beans, rinsed' -> ['black', 'beans']"""
    text = raw.lower()
    text = re.sub(r"\(.*?\)", " ", text)
    text = text.split(",")[0]
    text = re.sub(rf"[\d{FRACTIONS}][\d{FRACTIONS}./\-\s]*", " ", text)
    return [w for w in re.findall(r"[a-zà-ÿ'-]+", text) if w not in UNIT_WORDS and w not in FILLER_WORDS]


def core_name(raw, known):
    """'1 pound jumbo shrimp, peeled and deveined' -> 'shrimp'."""
    tokens = clean_tokens(raw)
    if not tokens:
        return None
    norm = [singular(w) for w in tokens]
    # Longest run of words that is a known ingredient name wins; ties go to the last one,
    # since the main noun comes last in English ("apple cider" is cider, not an apple)
    for size in range(min(4, len(norm)), 0, -1):
        for i in range(len(norm) - size, -1, -1):
            key = " ".join(norm[i:i + size])
            if key in known:
                if key in IGNORED and len(norm) > size:
                    return " ".join(tokens)  # "seltzer water" is not plain water
                return known[key]
    return " ".join(tokens)


def name_matches(a, b):
    """'onion' matches 'yellow onion'; 'tomatoes' matches 'tomato'."""
    wa, wb = set(words_of(a)), set(words_of(b))
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def contains_word(text, word):
    return re.search(rf"\b{re.escape(word)}", text) is not None



# ======================================================================
# Amounts, servings and nutrition for display
# ======================================================================
NICE_FRACTIONS = [(0.125, "⅛"), (0.25, "¼"), (1 / 3, "⅓"), (0.375, "⅜"), (0.5, "½"),
                  (0.625, "⅝"), (2 / 3, "⅔"), (0.75, "¾"), (0.875, "⅞")]


def _nice_number(match):
    value = float(match.group())
    whole, frac = int(value), value - int(value)
    if frac < 0.03:
        return str(whole)
    for target, symbol in NICE_FRACTIONS:
        if abs(frac - target) < 0.03:
            return f"{whole}{symbol}" if whole else symbol
    return f"{value:.2f}".rstrip("0").rstrip(".")


def pretty_amount(raw, keep_notes=False):
    """'1.3333333730698 cups Arborio rice' -> '1⅓ cups Arborio rice'.
    keep_notes=False drops the part after the first comma ('..., diced')."""
    text = re.sub(r"\d+\.\d+", _nice_number, raw.strip())
    if not keep_notes:
        text = text.split(",")[0].strip()
    return re.sub(r"\s+", " ", text)


def amount_match(clause_words, raw, name):
    """Is this ingredient line used in the clause? 'pepper' alone doesn't count for 'cayenne pepper'."""
    def fits(tokens):
        if not tokens or tokens[-1] not in clause_words:
            return False
        if tokens[-1] in GENERIC_WORDS:
            others = [w for w in tokens[:-1] if len(w) > 2]
            return not others or any(w in clause_words for w in others)
        return True
    # The whole line ('1/2 teaspoon saffron threads') or just its ingredient name ('saffron')
    line = [singular(w) for w in clean_tokens(raw)]
    key = words_of(name)
    return fits(line) or (key != line and all(w in clause_words for w in key) and fits(key)
                          and not (len(key) == 1 and key[0] in GENERIC_WORDS and len(line) > 1))


def servings_text(value):
    """['8', '8 servings'] -> 'Serves 8'; '24 cookies' -> 'Makes 24 cookies'."""
    items = value if isinstance(value, list) else [value]
    items = [str(v).strip() for v in items if v not in (None, "")]
    if not items:
        return None
    worded = next((v for v in items if re.search(r"[A-Za-z]", v)), None)
    text = worded or items[0]
    number = re.search(r"\d+(?:\s*(?:-|to)\s*\d+)?", text)
    if worded and number and not re.search(r"serving|people|portion", worded, re.I):
        return f"Makes {worded}"
    return f"Serves {number.group()}" if number else text


NUTRITION_FIELDS = [
    ("calories", "Calories"), ("fatContent", "Total fat"), ("saturatedFatContent", "Saturated fat"),
    ("cholesterolContent", "Cholesterol"), ("sodiumContent", "Sodium"),
    ("carbohydrateContent", "Carbohydrates"), ("fiberContent", "Fiber"), ("sugarContent", "Sugars"),
    ("proteinContent", "Protein"),
]


def nutrition_facts(raw_recipe):
    info = (raw_recipe or {}).get("nutrition") or {}
    facts = []
    for key, label in NUTRITION_FIELDS:
        value = info.get(key)
        if value not in (None, ""):
            facts.append({"label": label, "value": str(value).replace(" kcal", "")})
    return facts

# ======================================================================
# Pans, bowls and tools
# ======================================================================
# (phrase, kind) - longest phrases are checked first
VESSEL_PHRASES = [
    ("dutch oven", "pot"), ("stockpot", "pot"), ("stock pot", "pot"), ("soup pot", "pot"),
    ("saucepan", "pot"), ("sauce pan", "pot"), ("pot", "pot"),
    ("frying pan", "skillet"), ("fry pan", "skillet"), ("saute pan", "skillet"), ("sauté pan", "skillet"),
    ("skillet", "skillet"), ("wok", "skillet"), ("griddle", "skillet"), ("grill pan", "skillet"),
    ("baking sheet", "sheet"), ("sheet pan", "sheet"), ("cookie sheet", "sheet"), ("jelly roll pan", "sheet"),
    ("baking dish", "dish"), ("casserole dish", "dish"), ("casserole", "dish"), ("baking pan", "dish"),
    ("roasting pan", "dish"), ("cake pan", "dish"), ("loaf pan", "dish"), ("pie plate", "dish"),
    ("pie dish", "dish"), ("muffin tin", "dish"), ("muffin pan", "dish"), ("muffin cups", "dish"),
    ("tart pan", "dish"), ("springform pan", "dish"), ("bundt pan", "dish"), ("9x13", "dish"),
    ("mixing bowl", "bowl"), ("bowl", "bowl"),
    ("food processor", "processor"), ("blender", "blender"), ("stand mixer", "mixer"),
    ("electric mixer", "mixer"), ("mixer", "mixer"),
    ("fine-mesh sieve", "sieve"), ("fine mesh sieve", "sieve"), ("sieve", "sieve"), ("strainer", "sieve"),
    ("colander", "colander"),
    ("platter", "plate"), ("serving dish", "plate"), ("plate", "plate"),
    ("slow cooker", "slow cooker"), ("pressure cooker", "pressure cooker"), ("instant pot", "pressure cooker"),
    ("air fryer", "air fryer"), ("waffle iron", "waffle iron"),
    ("cast iron pie cooker", "pie iron"), ("pie cooker", "pie iron"), ("pie iron", "pie iron"),
    ("pudgy pie iron", "pie iron"), ("sandwich iron", "pie iron"), ("campfire cooker", "pie iron"),
    ("ramekin", "dish"), ("custard cup", "dish"), ("pie pan", "dish"), ("tube pan", "dish"),
    ("bread pan", "dish"), ("jelly-roll pan", "sheet"), ("pizza stone", "sheet"), ("pizza pan", "sheet"),
    ("saucepot", "pot"), ("kettle", "pot"), ("double boiler", "pot"),
    ("steamer basket", "steamer"), ("steamer", "steamer"),
    ("crock pot", "slow cooker"), ("crockpot", "slow cooker"), ("multicooker", "pressure cooker"),
    ("rice cooker", "rice cooker"), ("toaster", "toaster"),
    ("wire rack", "rack"), ("cooling rack", "rack"), ("roasting rack", "rack"),
    ("rolling pin", "rolling pin"), ("pastry bag", "piping bag"), ("piping bag", "piping bag"),
    ("cookie cutter", "cookie cutter"), ("biscuit cutter", "cookie cutter"),
    ("mortar and pestle", "mortar and pestle"), ("skewer", "skewer"),
    ("pan", "pan"),  # generic: resolved to the latest pan-like vessel
    ("cooker", "cooker"),  # generic: resolved to the latest cooker
]
PAN_LIKE = ("skillet", "dish", "sheet")
NEW_WORDS = {"a", "an", "another", "second", "separate", "clean", "new", "medium", "small", "large", "big", "deep", "heavy"}
SAME_WORDS = {"the", "same", "that", "prepared", "reserved", "this", "hot", "skillet's"}
APPLIANCES = {"oven", "microwave", "grill", "broiler", "stove"}

VESSEL_PATTERNS = [(re.compile(rf"\b{re.escape(p)}(?:e?s)?\b"), p, k) for p, k in sorted(VESSEL_PHRASES, key=lambda x: -len(x[0]))]


def find_vessel_mentions(text):
    """Returns [(position, phrase, kind, is_new, size)] in reading order."""
    low = text.lower().replace("oven-proof", "ovenproof").replace("oven proof", "ovenproof")
    taken = [False] * len(low)
    found = []
    for pattern, phrase, kind in VESSEL_PATTERNS:
        for m in pattern.finditer(low):
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            before = re.findall(r"[a-z0-9'-]+", low[max(0, m.start() - 40):m.start()])[-3:]
            size = next((w for w in reversed(before) if w in ("small", "medium", "large", "big")), "")
            new = None
            for w in reversed(before):
                if w in SAME_WORDS:
                    new = False
                    break
                if w in ("a", "an", "another", "second", "separate", "clean", "new"):
                    new = True
                    break
            found.append((m.start(), phrase, kind, new, size))
    found.sort()
    return found



# ======================================================================
# Matching a recipe's tools to the utensils someone has
# ======================================================================
TOOL_PHRASES = [
    ("chef's knife", "knife"), ("paring knife", "knife"), ("bread knife", "knife"), ("santoku knife", "knife"),
    ("santoku", "knife"), ("utility knife", "knife"), ("cleaver", "knife"), ("knife", "knife"),
    ("cutting board", "cutting board"), ("chopping board", "cutting board"), ("breadboard", "cutting board"),
    ("vegetable peeler", "peeler"), ("julienne peeler", "peeler"), ("peeler", "peeler"),
    ("box grater", "grater"), ("microplane grater", "grater"), ("microplane", "grater"), ("zester", "grater"),
    ("grater", "grater"),
    ("citrus juicer", "juicer"), ("citrus press", "juicer"), ("lemon squeezer", "juicer"), ("reamer", "juicer"),
    ("juicer", "juicer"),
    ("toaster oven", "toaster oven"), ("convection oven", "oven"), ("wall oven", "oven"), ("double oven", "oven"),
    ("range", "oven"), ("oven", "oven"), ("microwave", "microwave"), ("fork", "fork"),
]
_TOOL_PATTERNS = [(re.compile(rf"\b{re.escape(p)}(?:e?s)?\b"), k) for p, k in
                  sorted(VESSEL_PHRASES + TOOL_PHRASES, key=lambda x: -len(x[0]))]

# How a tool is asked about, and which utensil from your list a "yes" adds
KIND_LABELS = {
    "pot": "pot or saucepan", "skillet": "skillet or frying pan", "sheet": "baking sheet",
    "dish": "baking dish", "bowl": "mixing bowl", "processor": "food processor", "blender": "blender",
    "mixer": "electric mixer", "sieve": "strainer or sieve", "colander": "colander",
    "slow cooker": "slow cooker", "pressure cooker": "pressure cooker", "air fryer": "air fryer",
    "waffle iron": "waffle iron", "cutting board": "cutting board", "knife": "chef's knife",
    "peeler": "vegetable peeler", "grater": "grater", "juicer": "citrus juicer", "oven": "oven",
    "microwave": "microwave", "pie iron": "pie iron (campfire pie cooker)", "steamer": "steamer basket",
    "rice cooker": "rice cooker", "toaster": "toaster", "rack": "wire cooling rack",
    "rolling pin": "rolling pin", "piping bag": "piping bag", "cookie cutter": "cookie cutter",
    "mortar and pestle": "mortar and pestle", "skewer": "skewers", "cooker": "cooker",
}
KIND_UTENSIL = {
    "pot": "saucepan", "skillet": "skillet", "sheet": "baking sheet", "dish": "baking dish",
    "bowl": "mixing bowl", "processor": "food processor", "blender": "blender", "mixer": "hand mixer",
    "sieve": "strainer", "colander": "colander", "slow cooker": "slow cooker",
    "pressure cooker": "pressure cooker", "air fryer": "air fryer", "waffle iron": "waffle iron",
    "cutting board": "cutting board", "knife": "chef's knife", "peeler": "vegetable peeler",
    "grater": "grater", "juicer": "citrus juicer", "oven": "oven", "microwave": "microwave",
    "steamer": "steamer basket", "rice cooker": "rice cooker", "toaster": "toaster", "rack": "cooling rack",
    "rolling pin": "rolling pin", "piping bag": "piping bag", "cookie cutter": "cookie cutter",
    "mortar and pestle": "mortar and pestle", "skewer": "skewer",
}
ALWAYS_HAVE = {"plate", "fork"}   # never asked about
MIN_RESULTS = 6                   # ask about tools when fewer recipes than this fit


def tool_kind(name):
    """'frying pan' -> 'skillet', 'stockpot' -> 'pot', 'Chef's Knife' -> 'knife'."""
    low = str(name).lower()
    for pattern, kind in _TOOL_PATTERNS:
        if pattern.search(low):
            return "skillet" if kind == "pan" else kind
    return None

# ======================================================================
# Verbs: how long things take and whether they need you
# ======================================================================
# verb: (default minutes, hands-off?)
VERBS = {
    "preheat": (15, True), "heat": (3, True), "bring": (8, True), "boil": (10, True), "simmer": (15, True),
    "bake": (25, True), "roast": (30, True), "broil": (4, True), "chill": (30, True), "refrigerate": (60, True),
    "marinate": (30, True), "rest": (10, True), "let": (10, True), "cool": (10, True), "soak": (30, True),
    "rise": (60, True), "freeze": (60, True), "reduce": (8, True), "steep": (5, True), "set": (5, True),
    "microwave": (2, True), "slow-cook": (240, True), "grill": (10, False), "toast": (5, True),
    "cook": (5, False), "stir": (1, False), "whisk": (2, False), "mix": (2, False), "combine": (1, False),
    "add": (1, False), "pour": (1, False), "season": (0.5, False), "sprinkle": (0.5, False), "toss": (1, False),
    "transfer": (1, False), "place": (1, False), "put": (1, False), "arrange": (2, False), "lay": (1, False),
    "spread": (2, False), "top": (1, False), "garnish": (1, False), "serve": (1, False), "chop": (3, False),
    "dice": (3, False), "mince": (2, False), "slice": (3, False), "cut": (3, False), "peel": (3, False),
    "grate": (3, False), "shred": (3, False), "mash": (3, False), "blend": (2, False), "process": (2, False),
    "puree": (2, False), "beat": (3, False), "fold": (2, False), "knead": (8, False), "roll": (5, False),
    "brown": (6, False), "fry": (6, False), "saute": (5, False), "sauté": (5, False), "sear": (4, False),
    "drain": (1, False), "strain": (2, False), "rinse": (1, False), "pat": (1, False), "measure": (1, False),
    "melt": (2, False), "wrap": (2, False), "coat": (2, False), "dip": (2, False), "fill": (3, False),
    "divide": (2, False), "shape": (5, False), "form": (5, False), "flip": (1, False), "turn": (1, False),
    "remove": (1, False), "return": (1, False), "cover": (0.5, False), "uncover": (0.5, False),
    "increase": (0.25, False), "lower": (0.25, False), "squeeze": (1, False), "stuff": (4, False),
    "grease": (1, False), "line": (1, False), "spray": (0.5, False), "scrape": (1, False), "crack": (1, False),
    "sift": (1, False), "cream": (3, False), "drizzle": (0.5, False), "shake": (0.5, False), "dust": (0.5, False),
    "baste": (1, False), "carve": (4, False), "pound": (3, False), "trim": (2, False), "stir-fry": (5, False),
    "leave": (5, True),
}
# Verbs that act on whatever is already in the current pan
CONTEXT_VERBS = {"add", "stir", "cook", "season", "simmer", "bring", "reduce", "cover", "uncover", "boil", "mix",
                 "combine", "sprinkle", "top", "toss", "whisk", "beat", "fold", "bake", "pour", "brown", "fry",
                 "saute", "sauté", "increase", "lower", "drizzle", "spread", "heat", "melt", "return", "pat",
                 "arrange", "lay", "garnish", "stir-fry", "baste", "flip", "turn", "dust", "cream"}

TIME_RE = re.compile(
    r"(\d+(?:\.\d+)?|\d+/\d+|an?|one|two|three|four|five|ten|half an)"
    r"(?:\s*(?:to|-|–|or)\s*(\d+(?:\.\d+)?))?\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
    re.I,
)
WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "half an": 0.5}


def parse_minutes(text):
    """'about 2 minutes per side' -> 4. '1 to 2 minutes' -> 2. Returns None if no time is given."""
    total = 0.0
    found = False
    for m in TIME_RE.finditer(text):
        first, second, unit = m.group(1).lower(), m.group(2), m.group(3).lower()
        if second:
            value = float(second)
        elif "/" in first:
            a, b = first.split("/")
            value = float(a) / float(b)
        else:
            value = WORD_NUMBERS.get(first, None) or float(first)
        if unit.startswith("sec"):
            value /= 60
        elif unit.startswith("h"):
            value *= 60
        tail = text[m.end():m.end() + 20].lower()
        if "per side" in tail or "each side" in tail:
            value *= 2
        elif "per batch" in tail:
            value *= 2
        total += value
        found = True
    if "overnight" in text.lower():
        total += 480
        found = True
    return total if found else None


# ======================================================================
# Prep work hidden in the ingredient list
# ======================================================================
# descriptor: (verb shown, minutes, hands-off?, tools)
PREP = [
    ("peeled and deveined", ("Peel and devein", 8, False, ["cutting board", "chef's knife"])),
    ("deveined", ("Devein", 6, False, ["cutting board", "chef's knife"])),
    ("finely chopped", ("Finely chop", 4, False, ["cutting board", "chef's knife"])),
    ("chopped", ("Chop", 3, False, ["cutting board", "chef's knife"])),
    ("diced", ("Dice", 3, False, ["cutting board", "chef's knife"])),
    ("minced", ("Mince", 2, False, ["cutting board", "chef's knife"])),
    ("thinly sliced", ("Thinly slice", 3, False, ["cutting board", "chef's knife"])),
    ("sliced", ("Slice", 3, False, ["cutting board", "chef's knife"])),
    ("cut into", ("Cut", 3, False, ["cutting board", "chef's knife"])),
    ("cubed", ("Cube", 4, False, ["cutting board", "chef's knife"])),
    ("julienned", ("Julienne", 4, False, ["cutting board", "chef's knife"])),
    ("halved", ("Halve", 1, False, ["cutting board", "chef's knife"])),
    ("quartered", ("Quarter", 1, False, ["cutting board", "chef's knife"])),
    ("seeded", ("Seed", 2, False, ["cutting board", "chef's knife"])),
    ("trimmed", ("Trim", 2, False, ["cutting board", "chef's knife"])),
    ("peeled", ("Peel", 3, False, ["vegetable peeler"])),
    ("grated", ("Grate", 3, False, ["grater"])),
    ("shredded", ("Shred", 3, False, ["grater"])),
    ("zested", ("Zest", 2, False, ["grater"])),
    ("juiced", ("Juice", 2, False, ["citrus juicer"])),
    ("crushed", ("Crush", 1, False, ["chef's knife"])),
    ("rinsed and drained", ("Rinse and drain", 1, False, ["colander"])),
    ("drained", ("Drain", 1, False, ["colander"])),
    ("rinsed", ("Rinse", 1, False, ["colander"])),
    ("beaten", ("Beat", 1, False, ["small bowl", "fork"])),
    ("melted", ("Melt", 2, True, ["small bowl", "microwave"])),
    ("softened", ("Soften (leave out)", 30, True, [])),
    ("room temperature", ("Bring to room temperature", 30, True, [])),
]


# ======================================================================
# Building the task graph
# ======================================================================
def split_sentences(text):
    text = re.sub(r"\(\s*optional[^)]*\)", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text.strip())
    # Split on ". " but not inside numbers like 0.5
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)
    clauses = []
    for part in parts:
        for clause in re.split(r";\s*|,\s*then\s+|\s+then\s+(?=[a-z]+\b)", part):
            clause = clause.strip(" .")
            if len(clause) > 2:
                clauses.append(clause)
    return clauses


def first_verb(clause):
    for w in re.findall(r"[A-Za-zé-]+", clause.lower())[:5]:
        if w in VERBS:
            return w
    return None


# A clause containing one of these (and taking 5+ minutes) runs on its own
WAIT_WORDS = {"simmer", "simmering", "bake", "baking", "roast", "boil", "boiling", "chill", "refrigerate",
              "marinate", "rest", "soak", "rise", "freeze", "steep", "cool", "covered", "preheat", "reduced"}
HANDS_ON_WORDS = {"stir", "stirring", "brown", "browned", "batch", "batches", "side", "fry", "sear", "whisk", "flip"}
INSTANT_RE = re.compile(r"^(reduce|increase|lower|raise|turn( up| down)?)\s+(the\s+)?heat\b", re.I)
STANDALONE_VERBS = {"preheat", "chop", "dice", "mince", "slice", "cut", "peel", "grate", "shred", "measure",
                    "rinse", "trim", "crack", "sift", "line", "grease", "spray", "zest", "juice"}
SOURCE_VERBS = {"strain", "drain", "transfer", "remove", "pour", "scrape", "spoon", "ladle"}
GENERIC_WORDS = {"oil", "salt", "pepper", "sauce", "juice", "powder", "red", "green", "yellow", "white", "black",
                 "fresh", "dried", "ground", "sweet", "large", "small", "whole", "leaf", "extract", "seed",
                 "cider", "light", "dark", "brown", "hot", "cold", "baby", "bell", "sea", "kosher", "virgin", "extra"}


def ingredient_words(name):
    return [w for w in words_of(name) if len(w) > 2]


def covered_by_longer(clause, name, ingredients):
    """'apple cider' in a clause is about the cider, not the apple."""
    text = " " + " ".join(words_of(clause)) + " "
    short_phrase = " ".join(words_of(name))
    if not short_phrase:
        return False
    total = text.count(f" {short_phrase} ")
    if total == 0:
        return False
    inside = 0
    seen = set()
    for raw, _ in ingredients:
        long_phrase = " ".join(singular(w) for w in clean_tokens(raw))
        if long_phrase in seen or long_phrase == short_phrase or f" {short_phrase} " not in f" {long_phrase} ":
            continue
        seen.add(long_phrase)
        inside += text.count(f" {long_phrase} ")
    return inside >= total


def mentions(clause_words, name, broad=False):
    """Does a clause talk about this ingredient? broad=True also accepts generic words like 'pepper'."""
    words = ingredient_words(name)
    if not words:
        return False
    if all(w in clause_words for w in words):
        return True
    return any(w in clause_words and (broad or w not in GENERIC_WORDS) for w in words)


class Vessels:
    """Keeps track of the pans in a recipe: 'a large pot' is new, 'the pot' is the same one."""

    def __init__(self):
        self.items = []  # {"id", "kind", "label"}
        self.current = None

    def resolve(self, phrase, kind, new, size):
        if kind == "cooker":
            latest = next((v for v in reversed(self.items)
                           if v["kind"] in ("pie iron", "slow cooker", "pressure cooker", "rice cooker")), None)
            if latest and new is not True:
                return latest["id"]
        if kind == "pan":
            latest = next((v for v in reversed(self.items) if v["kind"] in PAN_LIKE), None)
            if latest and new is not True:
                return latest["id"]
            kind = "skillet"
        latest_same = next((v for v in reversed(self.items) if v["kind"] == kind), None)
        if latest_same and new is not True:
            return latest_same["id"]
        label = f"{size} {phrase}".strip() if size else phrase
        vid = f"v{len(self.items) + 1}"
        self.items.append({"id": vid, "kind": kind, "label": label})
        return vid

    def label(self, vid):
        return next(v["label"] for v in self.items if v["id"] == vid)

    def kind(self, vid):
        return next(v["kind"] for v in self.items if v["id"] == vid)


def build_tasks(recipe, known):
    ingredients = []
    for raw in recipe.get("ingredients_raw", []):
        name = core_name(raw, known)
        if name and name.lower() not in IGNORED:
            ingredients.append((raw, name))

    tasks = []
    vessels = Vessels()

    def new_task(**kw):
        kw["id"] = len(tasks)
        kw.setdefault("deps", set())
        tasks.append(kw)
        return kw

    # ---- Prep tasks from the ingredient list ----
    prep_for = {}  # ingredient name -> prep task id
    for raw, name in ingredients:
        low = raw.lower()
        for phrase, (verb, minutes, passive, tools) in PREP:
            if phrase in low:
                is_meat = any(contains_word(name.lower(), w) for w in MEAT_WORDS + SEAFOOD_WORDS)
                text = f"{verb} {pretty_amount(raw)}"
                if "shells reserved" in low or "reserve shells" in low:
                    text += " (keep the shells)"
                t = new_task(text=text + ".", minutes=minutes, passive=passive, kind="prep",
                             vessels=[], tools=tools, ingredients=[name], raw_meat=is_meat, oven=False)
                t["prep"] = {"verb": verb, "amount": pretty_amount(raw),
                             "suffix": text[len(f"{verb} {pretty_amount(raw)}"):]}
                prep_for[name] = t["id"]
                break

    # ---- Tasks from the instructions ----
    oven_task = None       # last task that used the oven
    preheat_task = None
    last_use = {}          # vessel id -> last task id
    last_touch = {}        # ingredient -> last task id
    previous = None
    prepped_used = set()
    # Amounts not shown yet, in recipe order (prepped ingredients already show theirs)
    pending = [(raw, name, pretty_amount(raw)) for raw, name in ingredients if name not in prep_for]

    def take_amounts(cwords, clause):
        found, names = [], set()
        for item in list(pending):
            raw, name, shown = item
            others = [(r, n) for r, n in ingredients if n != name]
            if name in names or not amount_match(cwords, raw, name) or covered_by_longer(clause, name, others):
                continue
            found.append(shown)
            names.add(name)
            pending.remove(item)
        return found
    steps = recipe.get("steps", [])
    for step_no, step in enumerate(steps, start=1):
        text = step["text"] if isinstance(step, dict) else str(step)
        for clause in split_sentences(text):
            verb = first_verb(clause)
            low = clause.lower()
            cwords = set(words_of(clause))
            minutes = parse_minutes(clause)
            default, passive = VERBS.get(verb, (2, False))
            if minutes is None:
                minutes = default
            instant = bool(INSTANT_RE.match(low))
            if instant:
                minutes, passive = 0.25, False
            elif minutes >= 5 and (cwords & WAIT_WORDS) and not (cwords & {"stir", "stirring", "batch", "batches", "side"}) and verb not in ("brown", "fry", "sear"):
                passive = True
            elif verb == "cook" and minutes > 10 and not (cwords & HANDS_ON_WORDS):
                passive = True
            elif cwords & {"brown", "browned", "batch", "batches", "side"} and verb in ("cook", "brown", "fry", "sear"):
                passive = False
            if verb in ("heat", "bring") and "until" in low and minutes > 6:
                passive = True

            is_preheat = verb == "preheat" or ("preheat" in low and "oven" in low and "preheated" not in low)
            uses_oven = (("oven" in low and verb in ("bake", "roast", "broil", "place", "put", "transfer", "return"))
                         or verb in ("bake", "roast")) and not is_preheat

            # Pans this clause uses
            ids = []
            for _, phrase, kind, new, size in find_vessel_mentions(clause):
                vid = vessels.resolve(phrase, kind, new, size)
                if vid not in ids:
                    ids.append(vid)
            current = vessels.current
            if current and not is_preheat:
                if not ids and verb not in STANDALONE_VERBS:
                    ids = [current]
                elif verb in SOURCE_VERBS and current not in ids:
                    ids.insert(0, current)  # the pan it comes out of

            strict = [n for _, n in ingredients if mentions(cwords, n) and not covered_by_longer(clause, n, ingredients)]
            broad = [n for _, n in ingredients if mentions(cwords, n, broad=True) and not covered_by_longer(clause, n, ingredients)]

            t = new_task(text=clause[0].upper() + clause[1:] + ".", minutes=round(minutes, 2), passive=passive,
                         kind="wait" if passive else "cook", vessels=ids, tools=[], ingredients=strict,
                         raw_meat=False, oven=uses_oven or is_preheat, step=step_no,
                         amounts=take_amounts(cwords, clause))

            # ---- Dependencies ----
            if is_preheat:
                preheat_task = t["id"]
                t["kind"], t["passive"] = "wait", True
            else:
                for vid in ids:
                    if vid in last_use:
                        t["deps"].add(last_use[vid])
                for name in broad:
                    if name in prep_for:
                        t["deps"].add(prep_for[name])
                        prepped_used.add(name)
                for name in strict:
                    if name in last_touch:
                        t["deps"].add(last_touch[name])
                if uses_oven:
                    if preheat_task is not None:
                        t["deps"].add(preheat_task)
                    if oven_task is not None and oven_task != preheat_task:
                        t["deps"].add(oven_task)
                if previous is not None and not (t["deps"] - set(prep_for.values())):
                    # Nothing in the text says what it builds on: keep the recipe's order to be safe
                    t["deps"].add(previous)

            for vid in ids:
                last_use[vid] = t["id"]
                if vessels.kind(vid) not in ("plate", "sieve", "colander"):
                    vessels.current = vid
            for name in strict:
                last_touch[name] = t["id"]
            if t["oven"]:
                oven_task = t["id"]
            if not is_preheat:
                previous = t["id"]

    # Prep for ingredients the steps never name still has to happen before the last step
    if previous is not None:
        for name, tid in prep_for.items():
            if name not in prepped_used:
                tasks[previous]["deps"].add(tid)


    return tasks, vessels


# ======================================================================
# Scheduling
# ======================================================================
def hands_on(t):
    if not t["passive"]:
        return t["minutes"]
    return 0.25 if t["text"].lower().startswith("preheat") else min(1.0, t["minutes"])


def schedule(tasks):
    succ = {t["id"]: [] for t in tasks}
    for t in tasks:
        for d in t["deps"]:
            succ[d].append(t["id"])

    @lru_cache(maxsize=None)
    def chain(i):
        return tasks[i]["minutes"] + max((chain(s) for s in succ[i]), default=0)

    start, finish = {}, {}
    remaining = {t["id"] for t in tasks}
    time, cook_free = 0.0, 0.0
    guard = 0
    while remaining and guard < 10000:
        guard += 1
        ready = [i for i in remaining if all(d in finish and finish[d] <= time + 1e-9 for d in tasks[i]["deps"])]
        if ready and cook_free <= time + 1e-9:
            # Longest remaining chain first; then recipe order
            i = max(ready, key=lambda k: (chain(k), tasks[k]["passive"], -k))
            start[i], finish[i] = time, time + tasks[i]["minutes"]
            cook_free = time + hands_on(tasks[i])
            remaining.discard(i)
            continue
        upcoming = [x for x in [cook_free, *finish.values()] if x > time + 1e-9]
        if not upcoming:
            break
        time = min(upcoming)

    # Preheat as late as possible so the oven is hot right when it's needed
    busy = [(start[i], start[i] + hands_on(tasks[i])) for i in start]
    for t in tasks:
        if t["text"].lower().startswith("preheat") and succ[t["id"]]:
            need = min(start[s] for s in succ[t["id"]])
            latest = need - t["minutes"]
            if latest > start[t["id"]]:
                for candidate in (latest - k * 0.5 for k in range(int((latest - start[t["id"]]) * 2) + 1)):
                    if all(not (a < candidate + 0.25 and candidate < b) for a, b in busy if (a, b) != (start[t["id"]], start[t["id"]] + 0.25)):
                        finish[t["id"]] = candidate + t["minutes"]
                        start[t["id"]] = candidate
                        break
    return start, finish


def assign_dishes(tasks, vessels, start, finish):
    """Reuses a pan of the same kind once the earlier one is done with."""
    spans = {}
    for t in tasks:
        for vid in t["vessels"]:
            s, f = spans.get(vid, (1e9, -1))
            spans[vid] = (min(s, start[t["id"]]), max(f, finish[t["id"]]))
    physical = []   # {"kind", "label", "free_at", "uses": [vid]}
    tips = []
    for vid, (s, f) in sorted(spans.items(), key=lambda x: x[1][0]):
        kind, label = vessels.kind(vid), vessels.label(vid)
        reusable = None
        if kind not in ("plate",):
            reusable = next((p for p in physical if p["kind"] == kind and p["free_at"] <= s + 1e-9), None)
        if reusable:
            tips.append(f"Rinse the {reusable['label']} and reuse it instead of getting out the {label}.")
            reusable["free_at"] = f
            reusable["uses"].append(vid)
        else:
            physical.append({"kind": kind, "label": label, "free_at": f, "uses": [vid]})

    tools = []
    for t in tasks:
        for tool in t["tools"]:
            if tool not in ("microwave",) and tool not in tools:
                tools.append(tool)
    as_written = len(spans) + len(tools)
    final = [p["label"] for p in physical] + tools
    return final, as_written, tips


def fmt(minutes):
    m = int(round(minutes))
    return f"{m // 60}:{m % 60:02d}"


def short(text, n=48):
    text = text.rstrip(".")
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def make_plan(recipe, known):
    tasks, vessels = build_tasks(recipe, known)
    if not tasks:
        return {"name": recipe.get("name", "Recipe"), "steps": [], "error": "This recipe has no steps to plan."}

    start, finish = schedule(tasks)
    order = sorted(tasks, key=lambda t: (start[t["id"]], t["id"]))
    dishes, as_written, dish_tips = assign_dishes(tasks, vessels, start, finish)

    total = max(finish.values())
    sequential = sum(t["minutes"] for t in tasks)

    steps = []
    announced = set()
    for t in order:
        s, f = start[t["id"]], finish[t["id"]]
        during = []
        if not t["passive"]:
            for o in tasks:
                if (o is not t and o["passive"] and o["id"] not in announced and o["minutes"] >= 5
                        and not o["text"].lower().startswith("preheat")
                        and start[o["id"]] < s - 1e-9 and finish[o["id"]] > s + 0.5):
                    during.append(o)
                    announced.add(o["id"])
        steps.append({
            "start": fmt(s),
            "end": fmt(f),
            "minutes": round(t["minutes"], 1),
            "text": t["text"],
            "kind": t["kind"],
            "hands_off": t["passive"],
            "utensils": [vessels.label(v) for v in t["vessels"]] + [x for x in t["tools"] if x != "microwave"],
            "while": [short(o["text"]) for o in during][:2],
            "amounts": t.get("amounts", []),
            "prep": t.get("prep"),
        })

    tips = []
    prep_count = sum(1 for t in tasks if t["kind"] == "prep")
    if prep_count:
        moved = [t for t in tasks if t["kind"] == "prep" and any(start[o["id"]] < start[t["id"]] < finish[o["id"]] and o["passive"] for o in tasks)]
        if moved:
            tips.append(f"{len(moved)} prep job{'s' if len(moved) != 1 else ''} fit into waiting time instead of being done up front.")
    board = [t for t in tasks if "cutting board" in t["tools"]]
    meat = [t for t in board if t["raw_meat"]]
    veg = [t for t in board if not t["raw_meat"]]
    if meat and veg:
        first_meat = min(start[t["id"]] for t in meat)
        if any(start[t["id"]] > first_meat for t in veg):
            names = ", ".join(sorted({t["ingredients"][0] for t in meat}))
            tips.append(f"One cutting board is enough: wash it with hot soapy water after cutting the raw {names}, before the vegetables.")
        else:
            tips.append("One cutting board is enough: the vegetables are cut before the raw meat. Wash it afterwards.")
    pre = next((t for t in tasks if t["text"].lower().startswith("preheat")), None)
    if pre:
        tips.append(f"Turn the oven on at {fmt(start[pre['id']])} so it's hot exactly when you need it.")
    tips += dish_tips

    needs = {}
    for t in tasks:
        for vid in t["vessels"]:
            needs.setdefault(vessels.kind(vid), vessels.label(vid))
        for tool in t["tools"]:
            k = tool_kind(tool)
            if k:
                needs.setdefault(k, tool)
        if t["oven"]:
            needs.setdefault("oven", "oven")
    needs = {k: v for k, v in needs.items() if k not in ALWAYS_HAVE}

    raw = recipe.get("raw_recipe", {}) or {}
    return {
        "requires": sorted(needs),
        "requires_labels": needs,
        "name": recipe.get("name", "Recipe"),
        "source_url": recipe.get("source_url"),
        "servings": servings_text(raw.get("recipeYield")),
        "ingredients": [pretty_amount(r, keep_notes=True) for r in recipe.get("ingredients_raw", [])],
        "nutrition": nutrition_facts(raw),
        "total_minutes": round(total),
        "sequential_minutes": round(sequential),
        "minutes_saved": max(0, round(sequential - total)),
        "dishes": dishes,
        "dishes_as_written": as_written,
        "dishes_saved": max(0, as_written - len(dishes)),
        "steps": steps,
        "tips": tips,
    }


# ======================================================================
# Recipe collection + search
# ======================================================================
# Words that can sit in front of an ingredient name without changing what it is
DESCRIPTOR_WORDS = {"ground", "freshly", "fresh", "black", "white", "kosher", "sea", "coarse", "fine", "cracked"}


def is_whole_ingredient(part, known):
    """'pepper to taste' and 'ground black pepper' are whole ingredients; 'cheese mix' is not."""
    tokens = [singular(w) for w in clean_tokens(part)]
    if not tokens or len(tokens) > 3:
        return False
    if " ".join(tokens) in known:
        return True
    stripped = [w for w in tokens if w not in DESCRIPTOR_WORDS]
    return bool(stripped) and " ".join(stripped) in known


def split_ingredient_line(raw, known):
    """'salt and pepper to taste' -> ['salt', 'pepper to taste'] when both sides are whole ingredients."""
    head = raw.split(",")[0]
    parts = re.split(r"\s+(?:and|&)\s+", head, maxsplit=1)
    if len(parts) == 2 and all(is_whole_ingredient(part, known) for part in parts):
        return [parts[0], parts[1]]
    return [raw]


# Lines ending in these words also count when you have the specific kind ("cheddar" -> "cheddar cheese")
CLASS_HEADS = {"cheese"}

# Words that make it a different ingredient: "brown sugar" is not "sugar", "green onion" is not "onion"
IDENTITY_MODIFIERS = {"brown", "powdered", "confectioners", "coconut", "almond", "oat", "soy", "sour", "cream",
                      "evaporated", "condensed", "sweetened", "self-rising", "cake", "sweet", "green", "rice", "wine",
                      "apple", "sesame", "peanut", "garlic", "onion", "chili", "hot", "goat", "cottage", "chocolate",
                      "maple", "corn", "tomato", "fish", "oyster", "worcestershire", "barbecue", "balsamic", "cider"}


def have_matches(have_item, line_tokens):
    """Does something you have count for this ingredient line?
    'flour' -> 'all-purpose flour' yes; 'chocolate' -> 'chocolate syrup' no; 'salt' -> 'pepper' no."""
    h = words_of(have_item)
    line = list(line_tokens)
    if not h or not line:
        return False
    hs, ls = set(h), set(line)
    if hs <= ls and (h[-1] == line[-1] or line[-1] in CLASS_HEADS):
        return not ((ls - hs) & IDENTITY_MODIFIERS)
    if ls <= hs and h[-1] == line[-1]:
        return True
    return False


def as_list(value):
    """'Mexican, Tex-Mex' or ['Dinner'] -> ['Mexican', 'Tex-Mex'] / ['Dinner']"""
    items = value if isinstance(value, list) else [value]
    out = []
    for v in items:
        if not v:
            continue
        for part in str(v).split(","):
            part = html.unescape(part).strip()
            if part and part not in out:
                out.append(part)
    return out


def rating_of(recipe):
    """(4.9, 171) from the recipe's aggregateRating, or (None, 0) if it has none."""
    info = (recipe.get("raw_recipe") or {}).get("aggregateRating") or {}
    try:
        value = float(info.get("ratingValue"))
    except (TypeError, ValueError):
        return None, 0
    try:
        count = int(float(info.get("ratingCount") or info.get("reviewCount") or 0))
    except (TypeError, ValueError):
        count = 0
    return round(value, 1), count


# A rating counts more the more people gave it: a 5.0 from 1 person doesn't beat 4.8 from 2,000.
RATING_PRIOR, RATING_PRIOR_COUNT = 3.5, 10


def rating_rank(recipe):
    value, count = rating_of(recipe)
    if value is None:
        return -1  # unrated recipes go last
    return (value * count + RATING_PRIOR * RATING_PRIOR_COUNT) / (count + RATING_PRIOR_COUNT)


# ======================================================================
# Swaps: missing ingredients you can replace with something you have
# ======================================================================
# (ingredient, [(what you need to have, use, how), ...])  First option you can make is shown.
SWAPS = [
    ("buttermilk", [(["milk", "lemon juice"], "milk + lemon juice", "1 cup milk + 1 tbsp lemon juice, let sit 5 min"),
                    (["milk", "lemon"], "milk + lemon juice", "1 cup milk + 1 tbsp lemon juice, let sit 5 min"),
                    (["milk", "vinegar"], "milk + vinegar", "1 cup milk + 1 tbsp vinegar, let sit 5 min"),
                    (["yogurt"], "plain yogurt", "same amount, thinned with a splash of milk or water")]),
    ("sour cream", [(["greek yogurt"], "Greek yogurt", "same amount"), (["yogurt"], "plain yogurt", "same amount")]),
    ("greek yogurt", [(["sour cream"], "sour cream", "same amount")]),
    ("heavy cream", [(["milk", "butter"], "milk + melted butter", "¾ cup milk + ¼ cup melted butter per cup (won't whip)")]),
    ("half-and-half", [(["milk", "heavy cream"], "milk + cream", "½ cup each per cup"),
                       (["milk", "butter"], "milk + butter", "1 cup milk + 1 tbsp melted butter")]),
    ("evaporated milk", [(["milk"], "milk", "same amount (a little thinner)")]),
    ("milk", [(["almond milk"], "almond milk", "same amount"), (["oat milk"], "oat milk", "same amount"),
              (["soy milk"], "soy milk", "same amount")]),
    ("butter", [(["vegetable oil"], "vegetable oil", "¾ the amount (for cooking, not for baking)"),
                (["olive oil"], "olive oil", "¾ the amount (for cooking, not for baking)"),
                (["canola oil"], "canola oil", "¾ the amount (for cooking, not for baking)"),
                (["coconut oil"], "coconut oil", "same amount")]),
    ("vegetable oil", [(["canola oil"], "canola oil", "same amount"), (["olive oil"], "olive oil", "same amount"),
                       (["avocado oil"], "avocado oil", "same amount"), (["butter"], "melted butter", "same amount")]),
    ("canola oil", [(["vegetable oil"], "vegetable oil", "same amount"), (["olive oil"], "olive oil", "same amount")]),
    ("olive oil", [(["vegetable oil"], "vegetable oil", "same amount"), (["canola oil"], "canola oil", "same amount"),
                   (["avocado oil"], "avocado oil", "same amount")]),
    ("shallot", [(["onion"], "onion", "same amount, finely chopped")]),
    ("green onion", [(["chives"], "chives", "same amount"), (["onion"], "onion", "a little, finely chopped")]),
    ("scallion", [(["chives"], "chives", "same amount"), (["onion"], "onion", "a little, finely chopped")]),
    ("garlic", [(["garlic powder"], "garlic powder", "⅛ tsp per clove")]),
    ("lemon juice", [(["lemon"], "fresh lemons", "about 3 tbsp juice per lemon"), (["lime juice"], "lime juice", "same amount"),
                     (["lime"], "fresh limes", "about 2 tbsp juice per lime")]),
    ("lime juice", [(["lime"], "fresh limes", "about 2 tbsp juice per lime"), (["lemon juice"], "lemon juice", "same amount"),
                    (["lemon"], "fresh lemons", "about 3 tbsp juice per lemon")]),
    ("lemon", [(["lemon juice"], "bottled lemon juice", "about 3 tbsp per lemon"), (["lime"], "lime", "same amount")]),
    ("lime", [(["lime juice"], "bottled lime juice", "about 2 tbsp per lime"), (["lemon"], "lemon", "same amount")]),
    ("brown sugar", [(["sugar", "molasses"], "sugar + molasses", "1 cup sugar + 1 tbsp molasses"),
                     (["sugar"], "white sugar", "same amount (slightly less moist)")]),
    ("powdered sugar", [(["sugar", "cornstarch"], "blended sugar + cornstarch", "blend 1 cup sugar + 1 tbsp cornstarch until fine")]),
    ("honey", [(["maple syrup"], "maple syrup", "same amount")]),
    ("maple syrup", [(["honey"], "honey", "same amount")]),
    ("egg", [(["flaxseed"], "flax egg", "1 tbsp ground flaxseed + 3 tbsp water per egg (baking only)"),
             (["chia seeds"], "chia egg", "1 tbsp chia seeds + 3 tbsp water per egg (baking only)")]),
    ("breadcrumbs", [(["panko"], "panko", "same amount"), (["cracker"], "crushed crackers", "same amount"),
                     (["rolled oats"], "rolled oats", "same amount")]),
    ("panko", [(["breadcrumbs"], "breadcrumbs", "same amount")]),
    ("chicken broth", [(["vegetable broth"], "vegetable broth", "same amount"), (["chicken stock"], "chicken stock", "same amount"),
                       (["bouillon"], "bouillon + water", "1 cube or 1 tsp per cup of water")]),
    ("chicken stock", [(["chicken broth"], "chicken broth", "same amount"), (["vegetable broth"], "vegetable broth", "same amount")]),
    ("vegetable broth", [(["chicken broth"], "chicken broth", "same amount"), (["vegetable stock"], "vegetable stock", "same amount")]),
    ("beef broth", [(["chicken broth"], "chicken broth", "same amount")]),
    ("white wine", [(["chicken broth"], "chicken broth + a splash of vinegar", "same amount + 1 tsp vinegar per cup"),
                    (["vegetable broth"], "vegetable broth + a splash of vinegar", "same amount + 1 tsp vinegar per cup")]),
    ("red wine", [(["beef broth"], "beef broth", "same amount"), (["chicken broth"], "chicken broth", "same amount")]),
    ("cornstarch", [(["flour"], "flour", "2 tbsp flour per 1 tbsp cornstarch")]),
    ("baking powder", [(["baking soda", "cream of tartar"], "baking soda + cream of tartar", "¼ tsp soda + ½ tsp cream of tartar per tsp")]),
    ("self-rising flour", [(["flour", "baking powder", "salt"], "flour + baking powder + salt", "1 cup flour + 1½ tsp baking powder + ¼ tsp salt")]),
    ("cake flour", [(["flour", "cornstarch"], "flour + cornstarch", "1 cup minus 2 tbsp flour + 2 tbsp cornstarch")]),
    ("ricotta", [(["cottage cheese"], "cottage cheese", "same amount, blended smooth")]),
    ("mayonnaise", [(["greek yogurt"], "Greek yogurt", "same amount"), (["yogurt"], "plain yogurt", "same amount")]),
    ("catsup", [(["tomato paste", "vinegar", "sugar"], "tomato paste + vinegar + sugar", "¼ cup paste + 1 tbsp vinegar + 1 tsp sugar + a splash of water")]),
    ("ketchup", [(["tomato paste", "vinegar", "sugar"], "tomato paste + vinegar + sugar", "¼ cup paste + 1 tbsp vinegar + 1 tsp sugar + a splash of water")]),
    ("soy sauce", [(["tamari"], "tamari", "same amount"), (["worcestershire sauce"], "Worcestershire sauce", "half the amount")]),
    ("rice vinegar", [(["apple cider vinegar"], "apple cider vinegar", "same amount"), (["white vinegar"], "white vinegar", "a little less")]),
    ("apple cider vinegar", [(["white vinegar"], "white vinegar", "same amount"), (["rice vinegar"], "rice vinegar", "same amount")]),
    ("tomato sauce", [(["tomato paste"], "tomato paste + water", "1 part paste + 1 part water")]),
    ("canned tomatoes", [(["tomato"], "fresh tomatoes", "about 1½ lb, chopped, per 28 oz can")]),
    ("crushed tomatoes", [(["tomato"], "fresh tomatoes", "about 1½ lb, chopped, per 28 oz can")]),
    ("cayenne pepper", [(["chili powder"], "chili powder", "same amount"), (["red pepper flakes"], "red pepper flakes", "same amount"),
                        (["hot sauce"], "hot sauce", "a few dashes")]),
    ("paprika", [(["chili powder"], "chili powder", "half the amount")]),
    ("chicken breast", [(["chicken thigh"], "chicken thighs", "same weight; cook a few minutes longer")]),
    ("chicken thigh", [(["chicken breast"], "chicken breasts", "same weight; check sooner so they don't dry out")]),
    ("ground beef", [(["ground turkey"], "ground turkey", "same amount"), (["ground pork"], "ground pork", "same amount")]),
    ("ground turkey", [(["ground beef"], "ground beef", "same amount"), (["ground chicken"], "ground chicken", "same amount")]),
    ("spaghetti", [(["linguine"], "linguine", "same amount"), (["fettuccine"], "fettuccine", "same amount"), (["pasta"], "any pasta", "same amount")]),
    ("penne", [(["rigatoni"], "rigatoni", "same amount"), (["macaroni"], "macaroni", "same amount"),
               (["fusilli"], "fusilli", "same amount"), (["pasta"], "any short pasta", "same amount")]),
    ("macaroni", [(["penne"], "penne", "same amount"), (["shells"], "small shells", "same amount"), (["pasta"], "any short pasta", "same amount")]),
    ("parmesan", [(["pecorino romano"], "pecorino romano", "same amount"), (["asiago"], "asiago", "same amount")]),
    ("cheddar", [(["monterey jack"], "Monterey Jack", "same amount"), (["colby"], "Colby", "same amount")]),
    ("feta", [(["goat cheese"], "goat cheese", "same amount")]),
    ("mozzarella", [(["provolone"], "provolone", "same amount")]),
]

# Words that don't change what an ingredient is, for matching a swap to a recipe line
SWAP_FILLER = DESCRIPTOR_WORDS | {"whole", "skim", "cold", "warm", "hot", "large", "plain", "low-fat", "nonfat",
                                  "granulated", "packed", "light", "shredded", "grated", "crumbled", "cheese",
                                  "boneless", "skinless", "lean", "canned", "dry", "uncooked", "jumbo", "extra-virgin"}


def swap_target_match(target, tokens):
    """'buttermilk' matches the line 'cold buttermilk' but 'milk' does not match 'coconut milk'."""
    words = words_of(target)
    extra = [w for w in tokens if w not in words]
    return set(words) <= set(tokens) and all(w in SWAP_FILLER for w in extra)


def find_swaps(recipe, missing, have):
    """For each missing ingredient, the first swap you can make with what you have."""
    found = []
    for name in missing:
        lines = recipe.get("_lines", {}).get(name, [])
        for target, options in SWAPS:
            if not any(swap_target_match(target, toks) for toks in lines):
                continue
            for needs, use, how in options:
                if all(any(have_matches(h, words_of(n)) for h in have) for n in needs):
                    found.append({"name": name, "for": recipe.get("_display", {}).get(name, name), "use": use, "how": how})
                    break
            break
    return found


def servings_count(value):
    """6 for 'Serves 6'; None for 'Makes 24 cookies' (not people) or no yield."""
    text = servings_text(value) or ""
    m = re.match(r"Serves\s+(\d+)", text)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


# Meal labels that mean the same thing are shown as one filter option
CATEGORY_GROUPS = {
    "Drinks": ["drink", "drinks", "beverage", "beverages", "cocktail", "cocktails", "mocktail", "mocktails",
               "smoothie", "smoothies", "shake", "shakes", "punch", "punches", "drinks and beverages"],
    "Dinner": ["dinner", "main dish", "main dishes", "main course", "main courses", "entree", "entrée",
               "entrees", "entrées", "supper", "main"],
    "Lunch": ["lunch"],
    "Breakfast & Brunch": ["breakfast", "brunch", "breakfast and brunch", "breakfast & brunch"],
    "Desserts": ["dessert", "desserts", "sweets", "sweet"],
    "Appetizers & Snacks": ["appetizer", "appetizers", "starter", "starters", "snack", "snacks",
                            "hors d'oeuvre", "hors d'oeuvres", "appetizers and snacks", "appetizers & snacks"],
    "Sides": ["side", "sides", "side dish", "side dishes"],
    "Salads": ["salad", "salads"],
    "Soups & Stews": ["soup", "soups", "stew", "stews", "chili", "soups and stews", "soups & stews",
                      "soups, stews and chili", "stews and chili", "chili and stews"],
    "Breads": ["bread", "breads"],
    "Sauces & Dressings": ["sauce", "sauces", "dressing", "dressings", "condiment", "condiments", "dip", "dips",
                           "sauces and condiments"],
}
_CATEGORY_LOOKUP = {alias: name for name, aliases in CATEGORY_GROUPS.items() for alias in aliases}


def combine_categories(labels):
    """['Beverages', 'Cocktail'] -> ['Drinks']; unknown labels are kept, capitalized."""
    out = []
    for label in labels:
        key = " ".join(label.lower().replace("’", "'").split())
        key = re.sub(r"\s+recipes?$", "", key)  # "Cocktail Recipes" -> "cocktail"
        name = _CATEGORY_LOOKUP.get(key) or label.strip().title()
        if name not in out:
            out.append(name)
    return out


def format_duration(minutes):
    """45 -> '45 min', 120 -> '2 hr', 740 -> '12 hr 20 min'."""
    m = int(round(minutes))
    if m <= 60:
        return f"{m} min"
    hours, rest = divmod(m, 60)
    return f"{hours} hr {rest} min" if rest else f"{hours} hr"


class RecipeBook:
    def __init__(self, folder, ingredients_json, utensils_json=None):
        self.folder = Path(folder)
        self.ingredients_json = ingredients_json
        self.utensils_json = utensils_json
        self._recipes = None
        self._plans = {}

    def _load(self):
        if self._recipes is not None:
            return
        self.known = load_known_names(self.ingredients_json)
        self._recipes = {}
        if not self.folder.exists():
            print(f"Recipe folder not found: {self.folder}")
            return
        for path in sorted(self.folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for i, recipe in enumerate(data.get("recipes", [])):
                if not recipe.get("steps") or not recipe.get("ingredients_raw"):
                    continue
                recipe = dict(recipe)
                recipe["source_url"] = data.get("source_url")
                # Recipe sites store some characters as codes ("&#39;" is an apostrophe)
                recipe["name"] = html.unescape(str(recipe.get("name", "")))
                recipe["ingredients_raw"] = [html.unescape(str(r)) for r in recipe["ingredients_raw"]]
                recipe["steps"] = [
                    {**st, "text": html.unescape(str(st.get("text", "")))} if isinstance(st, dict) else html.unescape(str(st))
                    for st in recipe["steps"]
                ]
                names = []
                recipe["_lines"] = {}
                recipe["_display"] = {}
                for raw in recipe["ingredients_raw"]:
                    for piece in split_ingredient_line(raw, self.known):
                        n = core_name(piece, self.known)
                        if not n or n.lower() in IGNORED:
                            continue
                        if n not in names:
                            names.append(n)
                            # Shown on the cards: "chocolate syrup", not just "syrup"
                            recipe["_display"][n] = " ".join(clean_tokens(piece)) or n
                        recipe["_lines"].setdefault(n, []).append([singular(w) for w in clean_tokens(piece)])
                recipe["_names"] = names
                recipe["_text"] = " ".join(recipe["ingredients_raw"]).lower()
                self._recipes[f"{path.stem}~{i}"] = recipe
        print(f"Loaded {len(self._recipes)} recipes from {self.folder}")

    def get(self, recipe_id):
        self._load()
        return self._recipes.get(recipe_id)

    def plan(self, recipe_id):
        if recipe_id not in self._plans:
            recipe = self.get(recipe_id)
            if recipe is None:
                return None
            self._plans[recipe_id] = make_plan(recipe, self.known)
        return self._plans[recipe_id]

    def _blocked(self, recipe, preferences):
        text = recipe["_text"]
        for allergy in preferences.get("allergies", []):
            words = ALLERGEN_WORDS.get(allergy, [allergy])
            if any(contains_word(text, w) for w in words):
                return True
        for diet in preferences.get("diets", []):
            if any(contains_word(text, w) for w in DIET_EXCLUDES.get(diet, [])):
                return True
        return False

    def _card(self, rid, matched, missing, plan, swaps=()):
        recipe = self._recipes[rid]
        raw = recipe.get("raw_recipe", {}) or {}
        image = raw.get("image")
        if isinstance(image, dict):
            image = image.get("url")
        elif isinstance(image, list):
            image = image[0] if image and isinstance(image[0], str) else (image[0].get("url") if image else None)
        time_text = format_duration(plan["total_minutes"])
        return {
            "id": rid,
            "name": recipe.get("name", rid),
            "time": time_text,
            "utensils": plan["dishes"][:3] or ["basic kitchen tools"],
            "have": [recipe.get("_display", {}).get(n, n) for n in matched],
            "missing": [recipe.get("_display", {}).get(n, n) for n in missing],
            "image": image,
            "minutes": plan["total_minutes"],
            "dishes": len(plan["dishes"]),
            "swaps": list(swaps),
            "servings": servings_count(raw.get("recipeYield")),
            "rating": rating_of(recipe)[0],
            "rating_count": rating_of(recipe)[1],
            "cuisines": as_list(raw.get("recipeCuisine")),
            "categories": combine_categories(as_list(raw.get("recipeCategory"))),
        }

    def search(self, have, utensils=(), preferences=None, limit=12, tools_yes=(), tools_no=()):
        """Returns {"recipes": [...], "questions": [...]}.
        Only recipes whose tools are all covered by `utensils` (plus tools answered "yes") are returned.
        If fewer than MIN_RESULTS fit, "questions" lists up to 3 tools that would unlock the most recipes."""
        self._load()
        preferences = preferences or {}
        have = [h for h in have if h]
        owned = {tool_kind(u) for u in utensils} | set(tools_yes)
        owned.discard(None)
        refused = set(tools_no)

        scored = []
        for rid, recipe in self._recipes.items():
            if self._blocked(recipe, preferences):
                continue
            names = recipe["_names"]
            lines = recipe["_lines"]
            matched = [n for n in names if any(have_matches(h, toks) for h in have for toks in lines.get(n, []))]
            if not matched:
                continue
            missing = [n for n in names if n not in matched]
            swaps = find_swaps(recipe, missing, have)
            swapped = {s["name"] for s in swaps}
            missing = [n for n in missing if n not in swapped]
            # More matching ingredients first (a swap counts), then the share of the recipe you already have
            covered = len(matched) + len(swapped)
            score = 0.1 * covered + covered / len(names)
            scored.append((score, rid, matched, missing, swaps))
        scored.sort(key=lambda x: -x[0])

        ready, waiting = [], []
        for score, rid, matched, missing, swaps in scored[:300]:
            plan = self.plan(rid)
            needed = set(plan.get("requires", [])) - owned
            if needed & refused:
                continue
            if needed:
                waiting.append((needed, plan))
            else:
                ready.append((rid, matched, missing, plan, swaps))

        questions = []
        if len(ready) < MIN_RESULTS and waiting:
            weight, label = {}, {}
            for needed, plan in waiting:
                for k in needed:
                    weight[k] = weight.get(k, 0) + 1 / len(needed)
                    label.setdefault(k, KIND_LABELS.get(k) or plan["requires_labels"].get(k, k))
            for k, _ in sorted(weight.items(), key=lambda x: -x[1])[:3]:
                questions.append({
                    "kind": k,
                    "label": label[k],
                    "utensil": KIND_UTENSIL.get(k),
                    "unlocks": sum(1 for needed, _ in waiting if k in needed),
                })

        ready.sort(key=lambda r: -rating_rank(self._recipes[r[0]]))
        return {
            "recipes": [self._card(*r) for r in ready[:limit]],
            "questions": questions,
        }

# ======================================================================
# Terminal test
# ======================================================================
if __name__ == "__main__":
    book = RecipeBook(ROOT / "Database" / "recipes_json", ROOT / "ingredients.json")
    book._load()
    query = " ".join(sys.argv[1:]).lower()
    if not query:
        for rid in list(book._recipes)[:10]:
            print(rid)
        sys.exit()
    rid = next((r for r, rec in book._recipes.items() if query in rec.get("name", "").lower()), None)
    if not rid:
        print("No recipe name contains", repr(query))
        sys.exit(1)
    plan = book.plan(rid)
    print(f"\n{plan['name']}")
    print(f"Time: {plan['total_minutes']} min (one step at a time: {plan['sequential_minutes']} min)")
    print(f"Dishes: {len(plan['dishes'])} (as written: {plan['dishes_as_written']}) -> {', '.join(plan['dishes'])}\n")
    for s in plan["steps"]:
        flag = "  (runs on its own)" if s["hands_off"] else ""
        extra = f"   while: {'; '.join(s['while'])}" if s["while"] else ""
        print(f"{s['start']}  {s['text']}{flag}{extra}")
    if plan["tips"]:
        print("\nTips:")
        for t in plan["tips"]:
            print(" -", t)