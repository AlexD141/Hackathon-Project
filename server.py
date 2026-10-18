"""
Runs the website, accounts, saved kitchen lists, the photo detector, the
recipe search over Database/recipes_json (recipe_search.py) and Aarush's
recipe optimizer (aarush_pipeline.py).

Start it from the project folder:
    python server.py
Then open port 8000 and go to /Front%20End/

Photo detection uses the vision model in recognizer.py, so set GROQ_API_KEY
first (get one at https://console.groq.com/keys).
"""

import html
import io
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, request, send_from_directory, session
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

from aarush_pipeline import optimize
from kitchen_search import KitchenSearch
from recognizer import VOCAB_FILES, get_groq_api_key, load_vocab, recognize

ROOT = Path(__file__).parent
DATA_DIR = ROOT / ".data"          # folders starting with "." are never served to the browser
DB_PATH = DATA_DIR / "app.db"

# Which list each kind is matched against
LISTS = {
    "ingredients": ROOT / "ingredients.json",
    "utensils": ROOT / "cookingUtensils.json",
}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB, enough for phone photos
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


# ---------- Setup ----------
def load_secret_key():
    """Keeps people signed in across restarts. Stored in .data/secret_key."""
    if os.environ.get("FLASK_SECRET_KEY"):
        return os.environ["FLASK_SECRET_KEY"]
    DATA_DIR.mkdir(exist_ok=True)
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    return key_file.read_text().strip()


def load_names(json_path):
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    names = {}
    for items in data["categories"].values():
        for name in items:
            name = name["name"] if isinstance(name, dict) else name
            names.setdefault(name.lower(), name)
    return names  # lowercase -> original spelling


app.secret_key = load_secret_key()
KNOWN = {kind: load_names(path) for kind, path in LISTS.items()}


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS saved_items (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, name)
            );
            CREATE TABLE IF NOT EXISTS preferences (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                data TEXT NOT NULL
            );
        """)


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return db().execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()


# ---------- Accounts ----------
USERNAME_RULE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


def read_credentials():
    body = request.get_json(silent=True) or {}
    return str(body.get("username", "")).strip(), str(body.get("password", ""))


@app.post("/api/signup")
def signup():
    username, password = read_credentials()
    if not USERNAME_RULE.match(username):
        return jsonify(error="Usernames need 3 to 30 characters: letters, numbers, dots, dashes or underscores."), 400
    if len(password) < 8:
        return jsonify(error="Passwords need at least 8 characters."), 400

    try:
        cur = db().execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error="That username is taken. Try another one, or sign in."), 409

    session.clear()
    session["user_id"] = cur.lastrowid
    session.permanent = True
    return jsonify(user={"username": username}), 201


@app.post("/api/login")
def login():
    username, password = read_credentials()
    user = db().execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify(error="That username and password don’t match. Check them and try again."), 401

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify(user={"username": user["username"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    user = current_user()
    return jsonify(user={"username": user["username"]} if user else None)


# ---------- Saved kitchen lists ----------
@app.get("/api/saved/<kind>")
def get_saved(kind):
    if kind not in LISTS:
        abort(404)
    user = current_user()
    if user is None:
        return jsonify(error="Sign in to see your saved list."), 401
    rows = db().execute(
        "SELECT name FROM saved_items WHERE user_id = ? AND kind = ? ORDER BY name COLLATE NOCASE",
        (user["id"], kind),
    ).fetchall()
    return jsonify(items=[r["name"] for r in rows])


@app.put("/api/saved/<kind>")
def put_saved(kind):
    """Replaces the saved list with the one sent. Unknown names are ignored."""
    if kind not in LISTS:
        abort(404)
    user = current_user()
    if user is None:
        return jsonify(error="Sign in to save your list."), 401

    body = request.get_json(silent=True) or {}
    items = []
    for name in body.get("items", []):
        match = KNOWN[kind].get(str(name).strip().lower())
        if match and match not in items:
            items.append(match)

    conn = db()
    conn.execute("DELETE FROM saved_items WHERE user_id = ? AND kind = ?", (user["id"], kind))
    conn.executemany(
        "INSERT INTO saved_items (user_id, kind, name) VALUES (?, ?, ?)",
        [(user["id"], kind, name) for name in items],
    )
    conn.commit()
    return jsonify(items=sorted(items, key=str.lower))


# ---------- Shopping (optional Instacart) ----------
# Get a key from the Instacart Developer Platform, then:
#   export INSTACART_API_KEY="your-key"
# Development keys use the test server:
#   export INSTACART_API_URL="https://connect.dev.instacart.tools"
INSTACART_API_KEY = os.environ.get("INSTACART_API_KEY")
INSTACART_API_URL = os.environ.get("INSTACART_API_URL", "https://connect.instacart.com")


@app.get("/api/shop/config")
def shop_config():
    return jsonify(instacart=bool(INSTACART_API_KEY))


@app.post("/api/shop/instacart")
def instacart_link():
    """Creates an Instacart shopping-list page with the cart items and returns its link."""
    if not INSTACART_API_KEY:
        return jsonify(error="Instacart isn’t set up on this server."), 501

    body = request.get_json(silent=True) or {}
    items = [str(n).strip() for n in body.get("items", []) if str(n).strip()][:100]
    if not items:
        return jsonify(error="Your cart is empty."), 400

    payload = {
        "title": "What can I cook? shopping list",
        "link_type": "shopping_list",
        "line_items": [{"name": name} for name in items],
    }
    req = urllib.request.Request(
        f"{INSTACART_API_URL}/idp/v1/products/products_link",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {INSTACART_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.load(res)
    except urllib.error.HTTPError as err:
        print("Instacart error:", err.code, err.read().decode(errors="replace")[:500])
        return jsonify(error=f"Instacart turned down the request (error {err.code}). Check the terminal running server.py."), 502
    except Exception as err:
        print("Instacart error:", repr(err))
        return jsonify(error="Couldn’t reach Instacart. Check your internet connection and try again."), 502

    url = data.get("products_link_url")
    if not url:
        return jsonify(error="Instacart didn’t send back a link. Check the terminal running server.py."), 502
    return jsonify(url=url)


# ---------- Preferences (diet and allergies) ----------
# Keep these in sync with DIETS and ALLERGIES in Front End/app.js
DIETS = {"vegetarian", "vegan", "pescatarian", "gluten-free", "dairy-free",
         "halal", "kosher", "keto", "low-carb", "paleo"}
ALLERGIES = {"peanuts", "tree nuts", "milk", "eggs", "fish", "shellfish", "wheat", "soy", "sesame"}
EMPTY_PREFERENCES = {"diets": [], "allergies": []}


@app.get("/api/preferences")
def get_preferences():
    user = current_user()
    if user is None:
        return jsonify(error="Sign in to see your preferences."), 401
    row = db().execute("SELECT data FROM preferences WHERE user_id = ?", (user["id"],)).fetchone()
    return jsonify(preferences=json.loads(row["data"]) if row else EMPTY_PREFERENCES)


@app.put("/api/preferences")
def put_preferences():
    user = current_user()
    if user is None:
        return jsonify(error="Sign in to save your preferences."), 401

    body = request.get_json(silent=True) or {}
    diets = sorted({str(d).strip().lower() for d in body.get("diets", [])} & DIETS)

    allergies = []
    for a in body.get("allergies", []):
        a = str(a).strip().lower()
        # Known allergies, or a custom one the person typed (short, sensible text)
        if a and len(a) <= 40 and a not in allergies:
            allergies.append(a)
    allergies = allergies[:30]

    prefs = {"diets": diets, "allergies": allergies}
    db().execute(
        "INSERT INTO preferences (user_id, data) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
        (user["id"], json.dumps(prefs)),
    )
    db().commit()
    return jsonify(preferences=prefs)


# ---------- Photo detection ----------
# Loaded once - recognizer.py matches against these (ingredients use the common subset)
VOCABS = {kind: load_vocab(path) for kind, path in VOCAB_FILES.items()}


@app.post("/api/detect/<kind>")
def detect(kind):
    if kind not in LISTS:
        return jsonify(error=f"Unknown list “{kind}”. Use ingredients or utensils."), 404

    photo = request.files.get("photo")
    if photo is None:
        return jsonify(error="No photo was sent. Send the image in a form field named “photo”."), 400

    data = photo.read()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        return jsonify(error="That file isn’t an image. Upload a JPG or PNG photo."), 400
    mime = Image.MIME.get(image.format, "image/jpeg")

    try:
        found = recognize(data, mime, VOCABS[kind], kind=kind)
    except RuntimeError as err:  # e.g. missing API key, all models busy, no internet
        print("Detection failed:", err)
        return jsonify(error=f"Photo detection failed: {str(err).splitlines()[0][:200]}"), 502

    return jsonify(items=[f["name"] for f in found], scores=found)


# ---------- Recipe search ----------
# recipe_search.py finds and ranks recipes; kitchen_search.py adds the utensil check,
# swaps, ratings, cuisines, servings and the cooking plan from planner.py.
KITCHEN = KitchenSearch()


def recipe_index():
    """The search index (built the first time, about 40 s; then about 1 s)."""
    return KITCHEN.index()


@app.post("/api/recipes/search")
def search_recipes():
    """Body: {"ingredients": [...], "utensils": [...], "preferences": {...},
              "tools_yes": [...], "tools_no": [...], "limit": 60}"""
    body = request.get_json(silent=True) or {}
    clean = lambda items, n: [str(i).strip()[:60] for i in (items or [])[:n] if str(i).strip()]
    ingredients = clean(body.get("ingredients"), 300)
    if not ingredients:
        return jsonify(error="Add at least one ingredient."), 400
    prefs = body.get("preferences") or {}
    preferences = {
        "diets": [d for d in clean(prefs.get("diets"), 20) if d in DIETS],
        "allergies": [a.lower() for a in clean(prefs.get("allergies"), 30)],
    }
    try:
        limit = min(max(int(body.get("limit") or 12), 1), 60)
    except (TypeError, ValueError):
        limit = 12
    result = KITCHEN.search(
        ingredients, clean(body.get("utensils"), 300), preferences, limit,
        tools_yes=clean(body.get("tools_yes"), 50),
        tools_no=clean(body.get("tools_no"), 50),
    )
    return jsonify(recipes=result["recipes"], questions=result["questions"])


@app.route("/api/plan/<path:plan_id>", methods=["GET", "POST"])
def recipe_plan(plan_id):
    """The "Cook this" plan: Aarush's optimized step order in the planner's layout.
    Body (optional, POST): {"utensils": [...the person's kitchen...]}"""
    body = request.get_json(silent=True) or {}
    utensils = [str(u)[:60] for u in (body.get("utensils") or [])[:300]]
    plan = KITCHEN.cook_plan(plan_id, utensils)
    if plan is None:
        return jsonify(error="That recipe wasn’t found."), 404
    return jsonify(plan=plan)


@app.get("/api/recipes/<recipe_id>")
def get_recipe(recipe_id):
    """The full recipe, with "step_texts": the list of strings Aarush's makeGraph expects."""
    recipe = recipe_index().recipe(recipe_id)
    if recipe is None:
        return jsonify(error="No recipe with that id."), 404
    steps = [s["text"] if isinstance(s, dict) else s for s in recipe.get("steps", [])]
    recipe.pop("raw_recipe", None)      # reviews and publisher data: large and not needed here
    return jsonify(recipe={**recipe, "step_texts": steps})


@app.post("/api/recipes/<recipe_id>/optimize")
def optimize_recipe(recipe_id):
    """
    Runs Aarush's pipeline (aarush_pipeline.py) on one recipe: its steps go to a
    .txt file, his makeGraph/render reorder them, and we report the fewest
    utensils it fits in. Body (optional): {"utensils": [...the person's kitchen...]}
    """
    index = recipe_index()
    if recipe_id not in index.by_id:
        return jsonify(error="No recipe with that id."), 404
    body = request.get_json(silent=True) or {}
    utensils = [str(u)[:60] for u in (body.get("utensils") or [])[:300]]
    try:
        result = optimize(recipe_id, [html.unescape(t) for t in index.steps(recipe_id)], utensils)
    except RuntimeError as err:     # e.g. spaCy model not installed
        print("Optimizing failed:", err)
        return jsonify(error=str(err)), 502
    except Exception as err:        # his parser hit something it doesn't handle
        print("Optimizing failed:", repr(err))
        return jsonify(error="Couldn’t optimize this recipe’s steps. Try another one."), 500
    r = index.by_id[recipe_id]
    return jsonify(recipe={"id": recipe_id, "name": r["name"], "url": r["url"], "image": r["image"]}, **result)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="That photo is over 20 MB. Try a smaller one."), 413


# ---------- Website files ----------
@app.get("/")
def home():
    return redirect("/Front%20End/")


@app.get("/<path:path>")
def files(path):
    if any(part.startswith(".") for part in Path(path).parts):
        abort(404)  # keeps .git, .data (accounts) and caches private
    if path.endswith("/") or (ROOT / path).is_dir():
        path = path.rstrip("/") + "/index.html"
    return send_from_directory(ROOT, path)


if __name__ == "__main__":
    init_db()
    if not get_groq_api_key():
        print("Warning: GROQ_API_KEY isn't set, so photo detection will fail. "
              "Get a key at https://console.groq.com/keys")
    recipe_index()  # load up front so the first search isn't slow
    app.run(host="0.0.0.0", port=8000, debug=False)
