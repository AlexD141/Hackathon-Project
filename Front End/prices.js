// Estimated grocery prices.
//
// Typical mid-range U.S. supermarket prices (2024–25, stores like Walmart and Kroger)
// for the package size you'd normally buy. Real prices vary by store, region and season,
// so these are always shown as estimates. Update any number here as you like.
//
// Use:  Prices.estimate("olive oil")  ->  { price: 8.99, unit: "16.9 oz bottle", exact: true }
(() => {
  // name: [price in dollars, package]
  const TABLE = {
    // ---------- Fruit ----------
    "apple": [0.8, "each"], "banana": [0.27, "each"], "orange": [0.9, "each"], "lemon": [0.75, "each"],
    "lime": [0.4, "each"], "grapefruit": [1.25, "each"], "tangerine": [0.5, "each"], "clementine": [0.5, "each"],
    "mandarin": [0.5, "each"], "avocado": [1.25, "each"], "mango": [1.0, "each"], "peach": [0.8, "each"],
    "nectarine": [0.8, "each"], "plum": [0.7, "each"], "pear": [0.9, "each"], "kiwi": [0.5, "each"],
    "pineapple": [2.99, "each"], "papaya": [2.99, "each"], "watermelon": [5.99, "each"], "cantaloup": [2.99, "each"],
    "honeydew": [3.49, "each"], "pomegranate": [2.5, "each"], "coconut": [2.99, "each"], "grape": [2.49, "1 lb"],
    "strawberry": [3.49, "1 lb container"], "blueberry": [3.99, "1 pint"], "raspberry": [3.49, "6 oz container"],
    "blackberry": [3.49, "6 oz container"], "cherry": [4.99, "1 lb"], "cranberry": [2.99, "12 oz bag"],
    "raisin": [3.49, "12 oz box"], "date": [4.99, "8 oz package"], "fig": [4.99, "8 oz package"],
    "dried apricot": [4.49, "6 oz bag"], "dried mango": [4.99, "6 oz bag"], "prune": [4.49, "16 oz bag"],

    // ---------- Vegetables ----------
    "tomato": [1.99, "1 lb"], "cherry tomato": [3.49, "1 pint"], "potato": [3.99, "5 lb bag"],
    "russet potato": [3.99, "5 lb bag"], "red potato": [3.99, "3 lb bag"], "yukon gold potato": [4.49, "3 lb bag"],
    "sweet potato": [1.29, "1 lb"], "yam": [1.29, "1 lb"], "carrot": [1.29, "1 lb bag"], "onion": [0.99, "each"],
    "yellow onion": [0.99, "each"], "purple onion": [1.29, "each"], "red onion": [1.29, "each"],
    "white onion": [1.19, "each"], "green onion": [0.99, "bunch"], "scallion": [0.99, "bunch"],
    "shallot": [0.6, "each"], "leek": [1.5, "each"], "garlic": [0.6, "head"], "ginger": [1.0, "3 in. piece"],
    "celery": [1.99, "bunch"], "cucumber": [0.79, "each"], "zucchini": [0.99, "each"], "eggplant": [1.99, "each"],
    "bell pepper": [1.29, "each"], "green pepper": [0.89, "each"], "red pepper": [1.49, "each"],
    "jalapeno": [0.2, "each"], "habanero": [0.25, "each"], "serrano pepper": [0.15, "each"],
    "poblano pepper": [0.6, "each"], "chili": [0.25, "each"], "broccoli": [2.29, "head"],
    "cauliflower": [3.49, "head"], "cabbage": [2.29, "head"], "red cabbage": [2.99, "head"],
    "napa cabbage": [2.99, "head"], "bok choy": [1.99, "1 lb"], "brussels sprouts": [3.49, "1 lb"],
    "kale": [1.99, "bunch"], "spinach": [2.99, "10 oz bag"], "baby spinach": [2.99, "5 oz container"],
    "lettuce": [1.99, "head"], "romaine": [3.49, "3 hearts"], "arugula": [3.49, "5 oz container"],
    "asparagus": [3.99, "1 lb bunch"], "artichoke": [2.49, "each"], "green bean": [2.49, "1 lb"],
    "snap peas": [3.49, "8 oz bag"], "snow pea": [3.49, "8 oz bag"], "edamame": [2.99, "12 oz frozen bag"],
    "corn": [0.6, "ear"], "sweet corn": [0.6, "ear"], "okra": [3.49, "1 lb"], "radish": [1.29, "bunch"],
    "beet": [2.99, "bunch"], "turnip": [1.29, "1 lb"], "parsnip": [2.99, "1 lb"], "rutabaga": [1.49, "1 lb"],
    "daikon": [1.99, "each"], "jicama": [1.99, "each"], "butternut squash": [3.49, "each"],
    "acorn squash": [1.99, "each"], "spaghetti squash": [3.49, "each"], "pumpkin": [4.99, "each"],
    "summer squash": [0.99, "each"], "yellow squash": [0.99, "each"], "mushroom": [2.49, "8 oz package"],
    "white mushroom": [2.49, "8 oz package"], "cremini mushroom": [2.99, "8 oz package"],
    "shiitake mushroom": [4.49, "3.5 oz package"], "portobello mushroom": [3.99, "2 caps"],
    "oyster mushroom": [4.49, "3.5 oz package"], "enoki mushroom": [2.49, "7 oz package"],
    "bean sprouts": [1.99, "12 oz bag"], "fennel": [2.49, "bulb"], "lemongrass": [1.99, "2 stalks"],
    "frozen peas": [1.79, "12 oz bag"], "pea": [1.79, "12 oz frozen bag"],

    // ---------- Fresh herbs ----------
    "basil": [2.49, "0.75 oz package"], "parsley": [0.99, "bunch"], "cilantro": [0.79, "bunch"],
    "mint": [2.49, "0.75 oz package"], "dill": [2.49, "0.75 oz package"], "rosemary": [2.49, "0.75 oz package"],
    "thyme": [2.49, "0.75 oz package"], "sage": [2.49, "0.75 oz package"], "oregano": [2.49, "0.75 oz package"],
    "chives": [2.49, "0.75 oz package"], "tarragon": [2.49, "0.75 oz package"], "bay leaf": [2.99, "jar"],

    // ---------- Eggs and dairy ----------
    "egg": [3.99, "dozen"], "egg white": [3.99, "16 oz carton"], "egg yolk": [3.99, "dozen eggs"],
    "duck eggs": [7.99, "half dozen"], "quail eggs": [4.99, "18 count"], "milk": [2.79, "half gallon"],
    "whole milk": [2.79, "half gallon"], "skim milk": [2.79, "half gallon"], "buttermilk": [2.29, "quart"],
    "butter": [4.99, "1 lb (4 sticks)"], "unsalted butter": [4.99, "1 lb (4 sticks)"],
    "salted butter": [4.99, "1 lb (4 sticks)"], "heavy cream": [4.29, "16 oz"], "whipping cream": [4.29, "16 oz"],
    "half-and-half": [2.49, "16 oz"], "sour cream": [2.49, "16 oz"], "cream cheese": [2.49, "8 oz block"],
    "whipped cream": [3.29, "can"], "yogurt": [3.49, "32 oz tub"], "greek yogurt": [5.99, "32 oz tub"],
    "cheese": [2.99, "8 oz block"], "cheddar": [2.99, "8 oz block"], "mozzarella": [2.99, "8 oz"],
    "parmesan": [4.99, "5 oz wedge"], "feta": [3.99, "6 oz"], "brie": [6.99, "8 oz wheel"],
    "goat cheese": [3.99, "4 oz log"], "blue cheese": [3.99, "5 oz crumbles"], "swiss cheese": [3.99, "8 oz slices"],
    "ricotta": [3.49, "15 oz tub"], "cottage cheese": [2.99, "16 oz tub"], "halloumi": [6.99, "8 oz"],
    "paneer": [5.99, "12 oz"], "burrata": [6.99, "8 oz"], "ice cream": [4.49, "1.5 qt"],
    "almond milk": [3.49, "half gallon"], "oat milk": [4.49, "half gallon"], "soy milk": [3.49, "half gallon"],

    // ---------- Meat and poultry ----------
    "chicken": [8.5, "whole, about 5 lb"], "whole chicken": [8.5, "about 5 lb"],
    "chicken breast": [3.99, "1 lb"], "chicken thigh": [2.49, "1 lb"], "chicken drumstick": [1.79, "1 lb"],
    "chicken wings": [3.49, "1 lb"], "turkey": [4.99, "1 lb ground"], "ground turkey": [4.99, "1 lb"],
    "duck": [14.99, "1 lb breast"], "beef": [6.99, "1 lb stew meat"], "ground beef": [5.49, "1 lb (80/20)"],
    "steak": [10.99, "1 lb sirloin"], "pork": [2.99, "1 lb shoulder"], "porkchop": [3.99, "1 lb"],
    "pork chop": [3.99, "1 lb"], "spareribs": [10.49, "rack, about 3 lb"], "bacon": [5.99, "12 oz"],
    "ham": [4.49, "9 oz deli slices"], "sausage": [4.99, "1 lb"], "italian sausage": [4.99, "1 lb"],
    "hot dog": [3.99, "8 count"], "salami": [4.49, "4 oz"], "pepperoni": [3.49, "6 oz"],
    "prosciutto": [4.99, "3 oz"], "lamb": [9.99, "1 lb ground"], "rack of lamb": [19.99, "about 1.5 lb"],

    // ---------- Seafood ----------
    "salmon": [10.99, "1 lb fillet"], "tuna": [1.29, "5 oz can"], "cod": [9.99, "1 lb"],
    "tilapia": [6.99, "1 lb"], "trout": [9.99, "1 lb"], "shrimp": [8.99, "1 lb frozen"], "prawn": [9.99, "1 lb frozen"],
    "crab": [8.99, "8 oz crab meat"], "lobster": [12.99, "1 tail"], "scallop": [19.99, "1 lb"],
    "mussel": [8.99, "2 lb bag"], "clam": [7.99, "1 lb"], "squid": [7.99, "1 lb"], "octopus": [14.99, "1 lb"],
    "sardine": [1.99, "3.75 oz tin"], "anchovy": [2.99, "2 oz tin"], "smoked salmon": [6.99, "4 oz"],

    // ---------- Grains, pasta and bread ----------
    "rice": [2.49, "2 lb bag"], "white rice": [2.49, "2 lb bag"], "brown rice": [2.79, "2 lb bag"],
    "jasmine rice": [3.49, "2 lb bag"], "basmati rice": [4.49, "2 lb bag"], "arborio rice": [4.49, "2 lb bag"],
    "pasta": [1.49, "1 lb box"], "spaghetti": [1.49, "1 lb box"], "penne": [1.49, "1 lb box"],
    "macaroni": [1.49, "1 lb box"], "fusilli": [1.49, "1 lb box"], "farfalle": [1.49, "1 lb box"],
    "lasagna": [2.29, "1 lb box"], "fresh pasta": [4.99, "9 oz"], "noodle": [1.99, "12 oz"],
    "egg noodles": [2.49, "12 oz bag"], "ramen noodles": [0.5, "1 pack"], "rice noodles": [3.49, "14 oz"],
    "bread": [2.99, "loaf"], "white bread": [1.99, "loaf"], "sourdough bread": [4.49, "loaf"],
    "baguet": [2.99, "each"], "bagel": [3.49, "6 count"], "croissant": [4.99, "4 count"],
    "tortilla": [2.99, "10 count"], "pita bread": [2.99, "6 count"], "naan": [3.49, "2 count"],
    "english muffin": [2.99, "6 count"], "hamburger bun": [2.49, "8 count"], "cracker": [3.49, "box"],
    "flour": [3.49, "5 lb bag"], "all-purpose flour": [3.49, "5 lb bag"], "bread flour": [4.99, "5 lb bag"],
    "cornmeal": [2.99, "2 lb"], "oatmeal": [4.99, "42 oz canister"], "rolled oats": [3.29, "18 oz canister"],
    "quinoa": [3.99, "12 oz"], "couscous": [2.99, "10 oz"], "barley": [2.49, "16 oz"],

    // ---------- Beans, nuts and seeds ----------
    "lentil": [1.79, "1 lb bag"], "chickpea": [0.99, "15 oz can"], "black bean": [0.99, "15 oz can"],
    "kidney bean": [0.99, "15 oz can"], "pinto bean": [0.99, "15 oz can"], "bean curd": [2.29, "14 oz tofu"],
    "tofu": [2.29, "14 oz"], "tempeh": [3.49, "8 oz"], "peanut": [3.49, "16 oz jar"], "almond": [4.99, "6 oz bag"],
    "walnut": [4.49, "6 oz bag"], "cashew": [5.49, "8 oz"], "pistachio": [4.99, "7 oz"], "pecan": [5.49, "6 oz bag"],
    "hazelnut": [5.99, "6 oz"], "pine nuts": [7.99, "4 oz"], "sunflower seeds": [2.99, "8 oz"],
    "pumpkin seeds": [4.49, "8 oz"], "chia seeds": [4.99, "12 oz"], "sesame seed": [3.49, "jar"],
    "peanut butter": [2.99, "16 oz jar"],

    // ---------- Oils, vinegars and sauces ----------
    "olive oil": [8.99, "16.9 oz bottle"], "extra virgin olive oil": [9.49, "16.9 oz bottle"],
    "vegetable oil": [4.49, "48 oz bottle"], "canola oil": [4.49, "48 oz bottle"], "coconut oil": [5.99, "14 oz jar"],
    "sesame oil": [3.99, "5 oz bottle"], "avocado oil": [8.99, "16.9 oz bottle"], "cooking spray": [3.49, "can"],
    "vinegar": [1.49, "32 oz bottle"], "white vinegar": [1.49, "32 oz bottle"],
    "apple cider vinegar": [2.99, "32 oz bottle"], "balsamic vinegar": [4.49, "16 oz bottle"],
    "red wine vinegar": [2.99, "16 oz bottle"], "rice vinegar": [2.99, "12 oz bottle"],
    "soy sauce": [2.99, "15 oz bottle"], "catsup": [3.49, "32 oz bottle"], "ketchup": [3.49, "32 oz bottle"],
    "mayonnaise": [4.99, "30 oz jar"], "mustard": [1.99, "14 oz bottle"], "dijon mustard": [3.49, "8 oz jar"],
    "hot sauce": [2.49, "5 oz bottle"], "sriracha": [4.49, "17 oz bottle"], "barbecue sauce": [2.49, "18 oz bottle"],
    "worcestershire sauce": [3.49, "10 oz bottle"], "fish sauce": [3.99, "7 oz bottle"],
    "oyster sauce": [3.49, "9 oz bottle"], "hoisin sauce": [3.49, "8 oz jar"], "salsa": [3.49, "16 oz jar"],
    "pesto": [4.49, "6 oz jar"], "jam": [3.49, "18 oz jar"], "tomato paste": [0.99, "6 oz can"],
    "canned tomatoes": [2.29, "28 oz can"], "crushed tomatoes": [2.29, "28 oz can"],
    "tomato sauce": [0.99, "15 oz can"], "olive": [3.49, "jar"], "green olive": [3.49, "jar"],
    "black olive": [2.49, "6 oz can"], "pickle": [3.49, "24 oz jar"], "caper": [2.99, "3.5 oz jar"],
    "coconut milk": [2.29, "13.5 oz can"],

    // ---------- Broths ----------
    "chicken broth": [2.49, "32 oz carton"], "beef broth": [2.49, "32 oz carton"],
    "vegetable broth": [2.49, "32 oz carton"], "chicken stock": [2.49, "32 oz carton"],
    "vegetable stock": [2.49, "32 oz carton"],

    // ---------- Sugar, sweeteners and baking ----------
    "sugar": [3.49, "4 lb bag"], "brown sugar": [2.79, "2 lb bag"], "powdered sugar": [2.79, "2 lb bag"],
    "honey": [4.99, "12 oz bottle"], "maple syrup": [7.99, "12 oz pure"], "molasses": [4.49, "12 oz"],
    "baking soda": [1.29, "1 lb box"], "baking powder": [2.99, "10 oz can"], "yeast": [1.99, "3 packets"],
    "cornstarch": [2.29, "16 oz"], "chocolate": [2.99, "4 oz bar"], "chocolate chips": [3.49, "12 oz bag"],
    "cocoa powder": [3.99, "8 oz"], "breadcrumbs": [2.49, "15 oz"], "panko": [2.99, "8 oz"],
    "vanilla extract": [8.99, "2 oz pure"], "vanilla bean": [6.99, "each"], "gelatin": [1.99, "4 packets"],

    // ---------- Salt, pepper and spices ----------
    "salt": [1.29, "26 oz canister"], "kosher salt": [3.99, "3 lb box"], "sea salt": [3.49, "jar"],
    "black pepper": [3.99, "ground, jar"], "cinnamon": [2.99, "jar"], "paprika": [2.99, "jar"],
    "smoked paprika": [3.99, "jar"], "cumin": [3.49, "jar"], "turmeric": [3.49, "jar"], "chili powder": [2.99, "jar"],
    "nutmeg": [4.49, "jar"], "clove": [5.99, "jar"], "star anise": [4.99, "jar"], "garlic powder": [2.99, "jar"],
    "onion powder": [2.99, "jar"], "red pepper flakes": [2.99, "jar"], "italian seasoning": [2.99, "jar"],
    "cayenne": [3.49, "jar"], "curry powder": [3.99, "jar"], "garam masala": [4.99, "jar"],
    "ground ginger": [3.99, "jar"], "dried oregano": [2.99, "jar"],

    // ---------- Drinks used in cooking ----------
    "wine": [9.99, "750 ml bottle"], "red wine": [9.99, "750 ml bottle"], "white wine": [9.99, "750 ml bottle"],
    "dry white wine": [9.99, "750 ml bottle"], "beer": [9.99, "6 pack"], "orange juice": [3.99, "52 oz"],
    "apple juice": [2.99, "64 oz"], "lemon juice": [2.49, "15 oz bottle"], "lime juice": [2.49, "15 oz bottle"],
    "coffee": [7.99, "12 oz ground"], "tea": [2.99, "20 bags"],
  };

  // When a name isn't in the table: guesses from words in the name (checked in order)
  const KEYWORDS = [
    ["vinegar", 2.99, "bottle"], ["olive oil", 8.99, "16.9 oz bottle"], ["oil", 4.99, "bottle"],
    ["sauce", 3.49, "bottle"], ["broth", 2.49, "32 oz carton"], ["stock", 2.49, "32 oz carton"],
    ["flour", 3.99, "bag"], ["sugar", 2.99, "bag"], ["syrup", 4.99, "bottle"], ["juice", 3.49, "bottle"],
    ["wine", 9.99, "750 ml bottle"], ["beer", 9.99, "6 pack"], ["tea", 2.99, "box"], ["coffee", 7.99, "12 oz"],
    ["cheese", 3.99, "8 oz"], ["yogurt", 3.49, "tub"], ["milk", 2.79, "half gallon"], ["cream", 3.49, "16 oz"],
    ["bean", 0.99, "15 oz can"], ["lentil", 1.79, "1 lb bag"], ["rice", 2.99, "2 lb bag"],
    ["noodle", 2.49, "package"], ["pasta", 1.79, "1 lb box"], ["bread", 3.49, "loaf"],
    ["tortilla", 2.99, "10 count"], ["seed", 3.99, "bag"], ["nut", 5.49, "bag"],
    ["mushroom", 2.99, "8 oz package"], ["pepper", 1.29, "each"], ["squash", 2.49, "each"],
    ["lettuce", 1.99, "head"], ["cabbage", 2.29, "head"], ["onion", 0.99, "each"], ["potato", 1.29, "1 lb"],
    ["berry", 3.99, "container"], ["apple", 0.8, "each"], ["melon", 3.49, "each"],
    ["chicken", 3.99, "1 lb"], ["beef", 6.99, "1 lb"], ["steak", 10.99, "1 lb"], ["pork", 3.99, "1 lb"],
    ["lamb", 9.99, "1 lb"], ["turkey", 4.99, "1 lb"], ["sausage", 4.99, "1 lb"], ["fish", 9.99, "1 lb"],
    ["salmon", 10.99, "1 lb"], ["shrimp", 8.99, "1 lb"], ["crab", 8.99, "8 oz"],
    ["powder", 3.49, "jar"], ["seasoning", 3.49, "jar"], ["spice", 3.99, "jar"], ["paste", 2.49, "jar or tube"],
  ];

  // Last resort: a typical price for the item's category in ingredients.json
  const CATEGORY = {
    fruits: [1.99, "typical"], vegetables: [1.99, "typical"], herbs: [2.49, "package"],
    spices_and_seasonings: [3.49, "jar"], condiments_and_sauces: [3.49, "jar or bottle"], dairy: [3.49, "typical"],
    eggs: [3.99, "dozen"], meat: [6.99, "1 lb"], poultry: [4.99, "1 lb"], seafood: [9.99, "1 lb"],
    grains_and_cereals: [2.99, "bag"], flours_and_starches: [3.49, "bag"], pasta_and_noodles: [1.99, "package"],
    breads_and_doughs: [3.49, "package"], sweeteners: [3.49, "package"], chocolate_and_cocoa: [3.49, "package"],
    baking_ingredients: [2.99, "package"], oils_and_fats: [5.49, "bottle"], stocks_and_broths: [2.49, "carton"],
    wine_beer_and_spirits: [12.99, "bottle"], juices_and_other_liquids: [3.49, "bottle"], legumes: [1.49, "can or bag"],
    nuts_and_seeds: [5.49, "bag"], other_foodstuffs: [3.99, "typical"],
  };
  const DEFAULT = [3.99, "typical"];

  let categories = {}; // lowercase name -> category, filled by app.js

  function lookup(key) {
    if (TABLE[key]) return TABLE[key];
    // Try singular and plural forms: "tomatoes" -> "tomato", "egg" -> "eggs"
    const forms = [key.replace(/ies$/, "y"), key.replace(/es$/, ""), key.replace(/s$/, ""), key + "s", key + "es"];
    for (const f of forms) if (TABLE[f]) return TABLE[f];
    return null;
  }

  function estimate(name) {
    const key = String(name).trim().toLowerCase();

    const found = lookup(key);
    if (found) return { price: found[0], unit: found[1], exact: true };

    // A keyword must start a word: "tea" matches "green tea" but not "steak"
    const words = key.split(/[\s-]+/);
    for (const [word, price, unit] of KEYWORDS) {
      const parts = word.trim().split(" ");
      const hit = parts.length > 1 ? key.includes(word.trim()) : words.some((w) => w.startsWith(parts[0]));
      if (hit) return { price, unit, exact: false };
    }

    const cat = CATEGORY[categories[key]] || DEFAULT;
    return { price: cat[0], unit: cat[1], exact: false };
  }

  const format = (price) => `$${price.toFixed(2)}`;

  // Sum of estimates for a list of names
  const total = (names) => names.reduce((sum, n) => sum + estimate(n).price, 0);

  // app.js passes in [{ name, category }] so unknown items can use their category's price
  function registerCategories(items) {
    for (const item of items) categories[item.name.toLowerCase()] = item.category;
  }

  window.Prices = { estimate, format, total, registerCategories };
})();