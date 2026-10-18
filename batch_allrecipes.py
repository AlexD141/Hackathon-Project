"""Batch recipe-to-JSON export. Click Run in VS Code.
Use automated collection only with the source permissions required for your use.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class JsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("type", "").lower() == "application/ld+json"
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.blocks.append("".join(self.parts))
            self.active = False


def find_recipes(value):
    if isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if any(str(t).rstrip("/").split("/")[-1] == "Recipe" for t in (types or [])):
            yield value
        else:
            for child in value.values():
                yield from find_recipes(child)
    elif isinstance(value, list):
        for child in value:
            yield from find_recipes(child)


def instruction_texts(value):
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, list):
        for child in value:
            yield from instruction_texts(child)
    elif isinstance(value, dict):
        if "itemListElement" in value:
            yield from instruction_texts(value["itemListElement"])
        elif "text" in value:
            yield from instruction_texts(value["text"])
        elif "item" in value:
            yield from instruction_texts(value["item"])


def extract(html, source_url):
    parser = JsonLdParser()
    parser.feed(html)
    recipes = []
    for block in parser.blocks:
        try:
            recipes.extend(find_recipes(json.loads(block)))
        except json.JSONDecodeError:
            continue
    if not recipes:
        raise ValueError("No Recipe JSON-LD found. Check that this is a recipe page, not an error or challenge page.")

    records = []
    for recipe in recipes:
        ingredients = recipe.get("recipeIngredient", [])
        ingredients = [ingredients] if isinstance(ingredients, str) else ingredients
        steps = list(instruction_texts(recipe.get("recipeInstructions", [])))
        records.append({
            "name": recipe.get("name"),
            "ingredients_raw": ingredients,
            "steps": [{"id": i, "text": text} for i, text in enumerate(steps, 1)],
            "warnings": ([] if ingredients and steps else ["Missing ingredients or readable instructions; inspect raw_recipe."]),
            "raw_recipe": recipe,
        })
    return {"source_url": source_url,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "recipes": records}


import gzip
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urldefrag
from urllib.robotparser import RobotFileParser

# Change these settings if needed.
TARGET_TOTAL_RECIPES = 40000
MAX_RETRIES = 3
BLOCK_COOLDOWN_SECONDS = 1800
DELAY_SECONDS = 0
# Leave empty to discover recipe URLs from the site's sitemaps.
# Or paste specific recipe URLs here to collect only those pages.
RECIPE_URLS = []

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "recipes_json"
URL_CACHE = ROOT / "allrecipes_urls.json"
AGENT = "PantryRecipePrototype"
ORIGIN = "https://www.allrecipes.com"

class StopBatch(Exception):
    pass

last_request = 0
robot = None

def fetch(url, is_robots=False):
    global last_request
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "www.allrecipes.com":
        raise ValueError("Unexpected host: " + url)
    if not is_robots and not robot.can_fetch(AGENT, url):
        raise ValueError("Disallowed by robots.txt: " + url)
    delay = max(DELAY_SECONDS, robot.crawl_delay(AGENT) or 0) if robot else DELAY_SECONDS
    if robot:
        rate = robot.request_rate(AGENT)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
    time.sleep(max(0, delay - (time.monotonic() - last_request)))
    last_request = time.monotonic()
    with urlopen(Request(url, headers={"User-Agent": AGENT + "/0.1"}), timeout=30) as response:
        return response.read()

from email.utils import parsedate_to_datetime

def retry_delay(error, attempt):
    minimum = min(1800, 60 * (2 ** attempt))
    header = error.headers.get("Retry-After") if isinstance(error, HTTPError) else None
    if header:
        try:
            return max(minimum, float(header))
        except ValueError:
            try:
                return max(minimum, parsedate_to_datetime(header).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    return minimum

def record_failure(url, reason):
    with (ROOT / "failed_urls.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"url": url, "error": str(reason), "time": time.time()}) + "\n")

def load_failures():
    path = ROOT / "failed_urls.jsonl"
    failed = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                failed.add(json.loads(line)["url"])
            except (ValueError, KeyError):
                continue
    return failed


def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)

def discover():
    if URL_CACHE.exists():
        urls = json.loads(URL_CACHE.read_text(encoding="utf-8"))
        print(f"Loaded {len(urls)} cached recipe URLs.", flush=True)
        return urls
    pending = list(robot.site_maps() or [ORIGIN + "/sitemap.xml"])
    seen, urls = set(), set()
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        seen.add(url)
        print("Reading sitemap:", url, flush=True)
        body = fetch(url)
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        tree = ET.fromstring(body)
        kind = tree.tag.split("}")[-1]
        if kind not in ("sitemapindex", "urlset"):
            raise ValueError("Not a supported XML sitemap: " + url)
        for child in tree:
            loc = next((x.text for x in child if x.tag.split("}")[-1] == "loc"), None)
            if not loc:
                continue
            loc = urldefrag(loc.strip())[0]
            p = urlparse(loc)
            if p.scheme != "https" or p.netloc != "www.allrecipes.com":
                continue
            if kind == "sitemapindex":
                pending.append(loc)
            elif p.path.startswith("/recipe/"):
                urls.add(loc)
        print(f"Found {len(urls)} recipe URLs so far.", flush=True)
    urls = sorted(urls)
    atomic_json(URL_CACHE, urls)
    return urls

def destination(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)[:90] or "recipe"
    suffix = hashlib.sha256(url.encode()).hexdigest()[:12]
    return OUTPUT / f"{slug}_{suffix}.json"

def main():
    global robot
    OUTPUT.mkdir(exist_ok=True)
    robot = RobotFileParser()
    robot.parse(fetch(ORIGIN + "/robots.txt", is_robots=True).decode("utf-8").splitlines())
    urls = list(dict.fromkeys(RECIPE_URLS)) if RECIPE_URLS else discover()
    failed = load_failures()
    saved = len(list(OUTPUT.glob("*.json")))
    consecutive_denials = 0
    print(f"Already saved: {saved}. Target: {TARGET_TOTAL_RECIPES}.", flush=True)
    for index, url in enumerate(urls, 1):
        if saved >= TARGET_TOTAL_RECIPES:
            break
        path = destination(url)
        if path.exists() or url in failed:
            continue
        print(f"[{index}/{len(urls)}] {url}", flush=True)
        for attempt in range(MAX_RETRIES + 1):
            try:
                html = fetch(url).decode("utf-8")
                consecutive_denials = 0
                result = extract(html, url)
                result["input_method"] = "http"
                atomic_json(path, result)
                saved += 1
                print(f"Total saved {saved}/{TARGET_TOTAL_RECIPES}: {path.name}", flush=True)
                break
            except HTTPError as error:
                if error.code in (401, 402):
                    raise StopBatch(f"HTTP {error.code}: authorization/payment response; manual review needed.") from error
                if error.code == 403:
                    consecutive_denials += 1
                    if consecutive_denials >= 3:
                        print(f"Repeated 403 responses. Cooling down {BLOCK_COOLDOWN_SECONDS // 60} minutes before ONE retry.", flush=True)
                        time.sleep(BLOCK_COOLDOWN_SECONDS)
                        # One probe of the same URL; no identity changes or bypass.
                        try:
                            html = fetch(url).decode("utf-8")
                        except (HTTPError, URLError, TimeoutError) as probe_error:
                            raise StopBatch("Access remains unavailable after cooldown; manual review needed.") from probe_error
                        consecutive_denials = 0
                        try:
                            result = extract(html, url)
                        except ValueError as parse_error:
                            record_failure(url, parse_error)
                            break
                        result["input_method"] = "http"
                        atomic_json(path, result)
                        saved += 1
                    else:
                        print("Skipping refused page; recorded in failed_urls.jsonl", flush=True)
                        record_failure(url, error)
                    break
                if error.code == 429 or error.code >= 500:
                    if attempt >= MAX_RETRIES:
                        raise StopBatch("Repeated rate-limit/server errors; progress saved. Manual review needed.") from error
                    seconds = retry_delay(error, attempt)
                    print(f"HTTP {error.code}: waiting {seconds:.0f}s before retry {attempt+1}/{MAX_RETRIES}", flush=True)
                    time.sleep(seconds)
                    continue
                record_failure(url, error)
                break
            except (URLError, TimeoutError, ConnectionError) as error:
                if attempt >= MAX_RETRIES:
                    raise StopBatch("Network unavailable after retries; progress saved.") from error
                seconds = retry_delay(error, attempt)
                print(f"Network error: waiting {seconds:.0f}s", flush=True)
                time.sleep(seconds)
            except ValueError as error:
                print("Skipping invalid page:", error, flush=True)
                record_failure(url, error)
                break
    print(f"Finished: {saved} total recipe JSON files in {OUTPUT}")
    if saved < TARGET_TOTAL_RECIPES:
        print("Available URL list exhausted before target; failed pages are listed in failed_urls.jsonl.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Completed recipe JSON files are saved. Run again to continue.")
    except (StopBatch, URLError, OSError, ValueError, ET.ParseError) as error:
        print("Stopped:", error)
        print("Previously saved recipe files remain available.")
