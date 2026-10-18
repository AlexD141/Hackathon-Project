// Search box and filters for the recipe results (cuisine, meal, time, cost to buy,
// cost per serving), plus a note showing the diet and allergy filters from your profile.
// app.js calls RecipeFilters.setRecipes(recipes) after each search.
(() => {
  const TIME_OPTIONS = [
    ["any", "Any time"],
    ["15", "Under 15 min"],
    ["30", "Under 30 min"],
    ["60", "Under 1 hour"],
  ];
  // Estimated cost of what you'd need to buy (prices.js), and that cost per serving
  const COST_OPTIONS = [["any", "Any"], ["5", "Under $5"], ["10", "Under $10"], ["20", "Under $20"], ["40", "Under $40"]];
  const SERVING_OPTIONS = [["any", "Any"], ["1", "Under $1"], ["2", "Under $2"], ["3", "Under $3"], ["5", "Under $5"]];
  let all = [];
  const chosen = { cuisines: new Set(), categories: new Set(), time: "any", cost: "any", perServing: "any", text: "" };

  const costOf = (r) => (window.Prices ? Prices.total(r.missing || []) : 0);
  const perServingOf = (r) => (r.servings ? costOf(r) / r.servings : null);

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  const bar = el(`
    <div class="results-tools" hidden>
      <div class="results-toolbar">
        <input type="search" class="results-search" placeholder="Search these recipes, e.g. chicken" aria-label="Search these recipes">
        <button type="button" class="btn btn-outline btn-small filters-btn" aria-expanded="false" aria-controls="filters-panel">
          Filters <span class="filters-count"></span>
        </button>
      </div>
      <div class="filters-panel" id="filters-panel" hidden>
        <fieldset class="filter-group" data-group="cuisines"><legend>Cuisine</legend><div class="filter-options"></div></fieldset>
        <fieldset class="filter-group" data-group="categories"><legend>Meal</legend><div class="filter-options"></div></fieldset>
        <fieldset class="filter-group" data-group="time"><legend>Time</legend><div class="filter-options"></div></fieldset>
        <fieldset class="filter-group" data-group="cost"><legend>Cost to buy</legend><div class="filter-options"></div></fieldset>
        <fieldset class="filter-group" data-group="perServing">
          <legend>Cost per serving</legend><div class="filter-options"></div>
          <p class="filter-note hint" hidden></p>
        </fieldset>
        <button type="button" class="link-btn clear-filters-btn">Clear filters</button>
      </div>
      <p class="profile-filter-note hint"></p>
      <p class="results-count hint" aria-live="polite"></p>
    </div>`);

  const $ = (sel) => bar.querySelector(sel);
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  function pill(type, name, value, label, checked) {
    const option = el(`<label class="pref-pill"><input><span></span></label>`);
    const input = option.querySelector("input");
    input.type = type;
    input.name = name;
    input.value = value;
    input.checked = checked;
    option.querySelector("span").textContent = label;
    return option;
  }

  // Cuisine and meal options come from the recipes that were found
  function buildOptions() {
    for (const group of ["cuisines", "categories"]) {
      const counts = new Map();
      for (const r of all) for (const v of r[group] || []) counts.set(v, (counts.get(v) || 0) + 1);
      for (const v of [...chosen[group]]) if (!counts.has(v)) chosen[group].delete(v);

      const box = $(`[data-group="${group}"] .filter-options`);
      box.innerHTML = "";
      const sorted = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      for (const [value, count] of sorted) {
        box.append(pill("checkbox", group, value, `${value} (${count})`, chosen[group].has(value)));
      }
      $(`[data-group="${group}"]`).hidden = sorted.length === 0;
    }

    for (const [group, options] of [["time", TIME_OPTIONS], ["cost", COST_OPTIONS], ["perServing", SERVING_OPTIONS]]) {
      const box = $(`[data-group="${group}"] .filter-options`);
      box.innerHTML = "";
      for (const [value, label] of options) {
        box.append(pill("radio", group, value, label, chosen[group] === value));
      }
    }
    // Prices come from prices.js; without it the cost filters can't work
    $('[data-group="cost"]').hidden = !window.Prices;
    $('[data-group="perServing"]').hidden = !window.Prices;
  }

  // "Matching your profile: Vegetarian · No peanuts"
  function profileNote() {
    const note = $(".profile-filter-note");
    const signedIn = typeof currentUser !== "undefined" && currentUser;
    const prefs = typeof state !== "undefined" && state.preferences ? state.preferences : { diets: [], allergies: [] };
    const parts = [];
    if (prefs.diets && prefs.diets.length) parts.push(prefs.diets.map(cap).join(", "));
    if (prefs.allergies && prefs.allergies.length) parts.push(`No ${prefs.allergies.join(", ")}`);
    if (parts.length) {
      note.textContent = `Matching your profile: ${parts.join(" · ")}. Change these on your profile page.`;
    } else if (signedIn) {
      note.textContent = "Add diets or allergies on your profile page to filter recipes by them.";
    } else {
      note.textContent = "Sign in and add your diet and allergies to filter recipes by them.";
    }
  }

  function matches(r) {
    const text = chosen.text;
    if (text) {
      const haystack = [r.name, ...(r.cuisines || []), ...(r.categories || []), ...(r.have || []), ...(r.missing || [])]
        .join(" ")
        .toLowerCase();
      if (!text.split(/\s+/).every((word) => haystack.includes(word))) return false;
    }
    if (chosen.cuisines.size && !(r.cuisines || []).some((c) => chosen.cuisines.has(c))) return false;
    if (chosen.categories.size && !(r.categories || []).some((c) => chosen.categories.has(c))) return false;
    if (chosen.time !== "any" && !(r.minutes <= Number(chosen.time))) return false;
    if (chosen.cost !== "any" && !(costOf(r) < Number(chosen.cost))) return false;
    if (chosen.perServing !== "any") {
      const per = perServingOf(r);
      if (per === null || !(per < Number(chosen.perServing))) return false;
    }
    return true;
  }

  function apply() {
    const shown = all.filter(matches);
    renderRecipes(shown);
    if (!shown.length && all.length) {
      const list = document.getElementById("results-list");
      list.innerHTML = "";
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "No recipes match your search or filters. Try clearing some of them.";
      list.append(p);
    }
    const active = chosen.cuisines.size + chosen.categories.size +
      ["time", "cost", "perServing"].filter((g) => chosen[g] !== "any").length;

    // Recipes that don't say how many they serve can't be filtered by cost per serving
    const note = $('[data-group="perServing"] .filter-note');
    const unknown = all.filter((r) => !r.servings).length;
    note.hidden = chosen.perServing === "any" || unknown === 0;
    note.textContent = unknown === 1
      ? "1 recipe doesn’t say how many it serves, so it’s hidden by this filter."
      : `${unknown} recipes don’t say how many they serve, so they’re hidden by this filter.`;
    $(".filters-count").textContent = active ? `(${active})` : "";
    $(".results-count").textContent =
      shown.length === all.length ? `${all.length} recipe${all.length === 1 ? "" : "s"}` : `Showing ${shown.length} of ${all.length} recipes`;
  }

  function setRecipes(recipes) {
    all = recipes || [];
    bar.hidden = all.length === 0;
    buildOptions();
    profileNote();
    if (all.length) apply();
    else renderRecipes([]);
  }

  function init() {
    const head = document.querySelector("#results .section-head");
    if (!head) return;
    head.after(bar);

    $(".results-search").addEventListener("input", (e) => {
      chosen.text = e.target.value.trim().toLowerCase();
      apply();
    });

    const panel = $(".filters-panel");
    $(".filters-btn").addEventListener("click", (e) => {
      panel.hidden = !panel.hidden;
      e.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
    });

    panel.addEventListener("change", (e) => {
      const input = e.target;
      if (input.name === "time" || input.name === "cost" || input.name === "perServing") {
        chosen[input.name] = input.value;
      } else if (chosen[input.name]) {
        input.checked ? chosen[input.name].add(input.value) : chosen[input.name].delete(input.value);
      }
      apply();
    });

    $(".clear-filters-btn").addEventListener("click", () => {
      chosen.cuisines.clear();
      chosen.categories.clear();
      chosen.time = "any";
      chosen.cost = "any";
      chosen.perServing = "any";
      buildOptions();
      apply();
    });
  }

  window.RecipeFilters = { setRecipes };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();