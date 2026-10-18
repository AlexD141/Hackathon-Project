// Runs on all three pages. Each page says which one it is with <body data-page="...">
const PAGE = document.body.dataset.page || "cook";

// ---------- Settings ----------
// Paths to your JSON files (format: { "categories": { "herbs": ["basil", ...], ... } })
const DATA_FILES = {
  utensils: "../cookingUtensils.json",
  ingredients: "../ingredients.json",
};

const PANEL_TEXT = {
  cook: {
    utensils: { title: "Utensils", search: "Search utensils, e.g. whisk", empty: "No utensils added yet." },
    ingredients: { title: "Ingredients", search: "Search ingredients, e.g. garlic", empty: "No ingredients added yet." },
  },
  profile: {
    utensils: { title: "Utensils you own", search: "Search utensils, e.g. whisk", empty: "No utensils saved yet." },
    ingredients: { title: "Pantry staples", search: "Search staples, e.g. olive oil", empty: "No staples saved yet." },
  },
};
const TEXT = PANEL_TEXT[PAGE] || PANEL_TEXT.cook;

const MAX_OPTIONS = 60; // how many search results to show at once

// Used only if the JSON files can't be loaded (e.g. page opened without the server)
const FALLBACK_DATA = {
  utensils: { categories: { pots_and_pans: ["frying pan", "saucepan", "stockpot"], knives_and_cutting: ["chef's knife", "cutting board"], mixing_and_prep: ["whisk", "mixing bowl"] } },
  ingredients: { categories: { vegetables: ["onion", "tomato", "carrot"], dairy: ["butter", "milk", "cheddar"], eggs: ["egg"], spices_and_seasonings: ["salt", "black pepper"] } },
};

// ---------- State ----------
const state = {
  utensils: { items: [], selected: new Set() },
  ingredients: { items: [], selected: new Set() },
};
let currentUser = null;
let profileDirty = false;

// ---------- Helpers ----------
// "1 utensil", "3 utensils"
const plural = (n, word) => `${n} ${n === 1 ? word.replace(/s$/, "") : word}`;

const prettyCategory = (key) => key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

// Sends JSON to the server and returns the reply, or throws an Error with a readable message
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    throw new Error("Couldn’t reach the server. Make sure server.py is running.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Something went wrong (error ${res.status}).`);
  return data;
}

async function loadData(kind) {
  try {
    const res = await fetch(DATA_FILES[kind]);
    if (!res.ok) throw new Error(res.status);
    return { data: await res.json(), fallback: false };
  } catch (err) {
    console.warn(`Couldn't load ${DATA_FILES[kind]}. Using sample data.`, err);
    return { data: FALLBACK_DATA[kind], fallback: true };
  }
}

// Turns { categories: { cat: [names] } } into [{ name, category }]
function flatten(data) {
  const items = [];
  const seen = new Set();
  for (const [category, names] of Object.entries(data.categories || {})) {
    for (const name of names) {
      const key = name.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({ name, category });
    }
  }
  return items.sort((a, b) => a.name.localeCompare(b.name));
}

// ---------- Account ----------
async function loadUser() {
  try {
    currentUser = (await api("/api/me")).user;
  } catch {
    currentUser = null;
  }
  return currentUser;
}

function renderAccountLinks() {
  const box = document.getElementById("account-links");
  if (!box) return;
  box.innerHTML = "";

  if (currentUser) {
    const profile = document.createElement("a");
    profile.href = "profile.html";
    profile.textContent = "My profile";
    if (PAGE === "profile") profile.setAttribute("aria-current", "page");

    const signOut = document.createElement("button");
    signOut.type = "button";
    signOut.className = "nav-button";
    signOut.textContent = "Sign out";
    signOut.addEventListener("click", async () => {
      await api("/api/logout", { method: "POST" }).catch(() => {});
      location.href = "index.html";
    });
    box.append(profile, signOut);
  } else {
    const signIn = document.createElement("a");
    const here = location.pathname.split("/").pop() || "index.html";
    signIn.href = `login.html?next=${encodeURIComponent(here)}`;
    signIn.textContent = "Sign in";
    box.append(signIn);
  }
}

const getSaved = async (kind) => (await api(`/api/saved/${kind}`)).items;
const putSaved = async (kind, items) =>
  (await api(`/api/saved/${kind}`, { method: "PUT", body: JSON.stringify({ items }) })).items;

// ---------- Panel setup ----------
function buildPanel(kind, data, usedFallback) {
  const panel = document.getElementById(`${kind}-panel`);
  panel.append(document.getElementById("panel-template").content.cloneNode(true));
  const text = TEXT[kind];
  const $ = (sel) => panel.querySelector(sel);

  $(".panel-title").textContent = text.title;
  $(".panel-title").id = `${kind}-title`;
  $(".search").placeholder = text.search;
  $(".search").setAttribute("aria-label", `Search ${kind}`);
  $(".category").setAttribute("aria-label", `Filter ${kind} by category`);
  $(".options").setAttribute("aria-label", `${text.title} options`);

  state[kind].items = flatten(data);

  // Category dropdown, sorted alphabetically by the name shown
  const categories = Object.keys(data.categories || {}).sort((a, b) =>
    prettyCategory(a).localeCompare(prettyCategory(b))
  );
  for (const category of categories) {
    const opt = document.createElement("option");
    opt.value = category;
    opt.textContent = prettyCategory(category);
    $(".category").append(opt);
  }

  if (usedFallback) {
    const warn = document.createElement("p");
    warn.className = "hint";
    warn.textContent = `Showing sample data. To use ${DATA_FILES[kind]}, start server.py and open the page from port 8000.`;
    $(".panel-head").after(warn);
  }

  // Tabs
  panel.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      panel.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t === tab));
      panel.querySelectorAll(".tab-body").forEach((b) => (b.hidden = b.dataset.body !== tab.dataset.tab));
    });
  });

  // Search and filter
  $(".search").addEventListener("input", () => renderOptions(kind));
  $(".category").addEventListener("change", () => renderOptions(kind));
  $(".search").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const first = $(".options .option:not([aria-selected='true'])");
    if (first) first.click();
  });

  $(".clear-btn").addEventListener("click", () => {
    state[kind].selected.clear();
    selectionChanged(kind);
  });

  setupPhoto(kind, panel);
  setupSavedRow(kind, panel);
  refresh(kind);
}

// Saved-list buttons inside each panel (cooking page and profile page)
function setupSavedRow(kind, panel) {
  const $ = (sel) => panel.querySelector(sel);
  if (PAGE !== "cook" && PAGE !== "profile") return;

  if (!currentUser) {
    $(".signin-note").hidden = false;
    return;
  }

  const row = $(".saved-row");
  const status = $(".saved-status");
  const label = kind === "utensils" ? "utensils" : "staples";
  row.hidden = false;

  // The profile page has its own big "Save changes" button
  if (PAGE === "profile") $(".save-btn").hidden = true;

  $(".load-saved-btn").addEventListener("click", async () => {
    try {
      const saved = await getSaved(kind);
      if (saved.length === 0) {
        status.textContent = `Your saved list is empty. Add ${label}, then save them.`;
        return;
      }
      const before = state[kind].selected.size;
      saved.forEach((name) => state[kind].selected.add(name));
      const added = state[kind].selected.size - before;
      status.textContent = added ? `Added ${plural(added, "saved " + label)}.` : `Your saved ${label} are already in the list.`;
      selectionChanged(kind);
    } catch (err) {
      status.textContent = err.message;
    }
  });

  $(".save-btn").addEventListener("click", async () => {
    if (state[kind].selected.size === 0) {
      status.textContent = "Add some items first, then save them.";
      return;
    }
    try {
      const saved = await getSaved(kind);
      const merged = await putSaved(kind, [...new Set([...saved, ...state[kind].selected])]);
      status.textContent = `Saved. Your profile now has ${plural(merged.length, label)}.`;
    } catch (err) {
      status.textContent = err.message;
    }
  });

  $(".clear-saved-btn").addEventListener("click", async () => {
    if (!confirm(`Clear all your saved ${label}? This removes them from your profile.`)) return;
    try {
      await putSaved(kind, []);
      if (PAGE === "profile") {
        state[kind].selected.clear();
        refresh(kind);
      }
      status.textContent = `Your saved ${label} are cleared.`;
    } catch (err) {
      status.textContent = err.message;
    }
  });
}

function renderOptions(kind) {
  const panel = document.getElementById(`${kind}-panel`);
  const query = panel.querySelector(".search").value.trim().toLowerCase();
  const category = panel.querySelector(".category").value;
  const list = panel.querySelector(".options");
  const note = panel.querySelector(".options-note");

  const matches = state[kind].items.filter(
    (item) => (category === "all" || item.category === category) && item.name.toLowerCase().includes(query)
  );

  // Items that start with the query come first
  if (query) {
    matches.sort((a, b) => b.name.toLowerCase().startsWith(query) - a.name.toLowerCase().startsWith(query));
  }

  list.innerHTML = "";
  for (const item of matches.slice(0, MAX_OPTIONS)) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "option";
    btn.setAttribute("role", "option");
    btn.setAttribute("aria-selected", state[kind].selected.has(item.name));
    btn.innerHTML = `<span></span><span class="cat"></span>`;
    btn.firstChild.textContent = item.name;
    btn.lastChild.textContent = prettyCategory(item.category);
    btn.addEventListener("click", () => toggle(kind, item.name));
    li.append(btn);
    list.append(li);
  }

  if (matches.length === 0) {
    note.textContent = `Nothing matches “${query}”. Try a shorter or different word.`;
  } else if (matches.length > MAX_OPTIONS) {
    note.textContent = `Showing ${MAX_OPTIONS} of ${matches.length}. Type more to narrow it down.`;
  } else {
    note.textContent = "";
  }
}

function toggle(kind, name) {
  const sel = state[kind].selected;
  sel.has(name) ? sel.delete(name) : sel.add(name);
  selectionChanged(kind);
}

// Called whenever the user adds or removes items
function selectionChanged(kind) {
  refresh(kind);
  if (PAGE === "profile") {
    profileDirty = true;
    const status = document.getElementById("save-status");
    if (status) status.textContent = "You have unsaved changes.";
  }
}

function renderSelected(kind) {
  const panel = document.getElementById(`${kind}-panel`);
  const list = panel.querySelector(".selected");
  const names = [...state[kind].selected].sort((a, b) => a.localeCompare(b));

  list.innerHTML = "";
  for (const name of names) {
    const li = document.createElement("li");
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.setAttribute("aria-label", `Remove ${name}`);
    chip.innerHTML = `<span></span><span class="x" aria-hidden="true">✕</span>`;
    chip.firstChild.textContent = name;
    chip.addEventListener("click", () => toggle(kind, name));
    li.append(chip);
    list.append(li);
  }

  panel.querySelector(".empty").textContent = names.length ? "" : TEXT[kind].empty;
  panel.querySelector(".count").textContent = `${names.length} selected`;
  panel.querySelector(".clear-btn").hidden = names.length === 0;
}

function refresh(kind) {
  renderOptions(kind);
  renderSelected(kind);
  updateFindButton();
}

// ---------- Photo ----------
const PASTE_HINT = "Or copy an image and press Ctrl + V (⌘ + V on Mac).";
let lastPhotoKind = null; // the panel whose photo tab was used most recently

function setupPhoto(kind, panel) {
  const $ = (sel) => panel.querySelector(sel);
  const input = $(".photo-input");
  const preview = $(".photo-preview");
  const detectBtn = $(".detect-btn");
  const clearBtn = $(".clear-photo-btn");
  const pasteBtn = $(".paste-btn");
  const pasteNote = $(".paste-note");
  const detected = $(".detected");
  const detectedList = $(".detected-list");
  const photoBody = panel.querySelector('[data-body="photo"]');
  let photoFile = null;

  input.setAttribute("aria-label", `Take or upload a photo of your ${kind}`);

  // Shows a photo (uploaded, taken or pasted) and gets it ready to detect
  function setPhoto(file, note = PASTE_HINT) {
    if (!file || !file.type.startsWith("image/")) return;
    photoFile = file;
    if (preview.src.startsWith("blob:")) URL.revokeObjectURL(preview.src);
    preview.src = URL.createObjectURL(file);
    preview.alt = `Your photo of ${kind}`;
    preview.hidden = false;
    $(".plate-text").hidden = true;
    detectBtn.disabled = false;
    clearBtn.hidden = false;
    detected.hidden = true;
    if (pasteNote) pasteNote.textContent = note;
    lastPhotoKind = kind;
  }

  panel.querySelector('.tab[data-tab="photo"]').addEventListener("click", () => (lastPhotoKind = kind));
  input.addEventListener("change", () => setPhoto(input.files[0]));

  // "Paste image" button
  if (pasteBtn) {
    pasteBtn.addEventListener("click", async () => {
      lastPhotoKind = kind;
      if (!navigator.clipboard || !navigator.clipboard.read) {
        pasteNote.textContent = "This browser can’t paste with the button. Press Ctrl + V (⌘ + V on Mac) instead.";
        return;
      }
      try {
        for (const item of await navigator.clipboard.read()) {
          const type = item.types.find((t) => t.startsWith("image/"));
          if (type) {
            const blob = await item.getType(type);
            setPhoto(new File([blob], `pasted.${type.split("/")[1]}`, { type }), "Image pasted. Press Detect items.");
            return;
          }
        }
        pasteNote.textContent = "There’s no image on your clipboard. Copy an image first, then try again.";
      } catch {
        pasteNote.textContent = "The browser blocked the paste. Press Ctrl + V (⌘ + V on Mac) instead, or allow clipboard access.";
      }
    });
  }

  // Ctrl + V / ⌘ + V while this panel's photo tab is open
  document.addEventListener("paste", (e) => {
    if (photoBody.hidden) return;
    if (lastPhotoKind && lastPhotoKind !== kind) {
      const other = document.querySelector(`#${lastPhotoKind}-panel [data-body="photo"]`);
      if (other && !other.hidden) return; // the other panel's photo tab was used last
    }
    const file = [...(e.clipboardData ? e.clipboardData.files : [])].find((f) => f.type.startsWith("image/"));
    if (!file) return;
    e.preventDefault();
    setPhoto(file, "Image pasted. Press Detect items.");
  });

  clearBtn.addEventListener("click", () => {
    photoFile = null;
    input.value = "";
    preview.hidden = true;
    $(".plate-text").hidden = false;
    detectBtn.disabled = true;
    clearBtn.hidden = true;
    detected.hidden = true;
    if (pasteNote) pasteNote.textContent = PASTE_HINT;
  });

  detectBtn.addEventListener("click", async () => {
    detectBtn.disabled = true;
    detectBtn.innerHTML = `<span class="spinner" aria-hidden="true"></span>Detecting…`;
    detectedList.innerHTML = "";

    let found = [];
    let message = "We spotted these. Uncheck anything that’s wrong.";
    try {
      found = await detectItemsInPhoto(kind, photoFile);
      if (found.length === 0) {
        message = `Nothing from your ${kind} list was recognized. Try a closer, brighter photo.`;
      }
    } catch (err) {
      message = err.message;
    }

    detectBtn.textContent = "Detect items";
    detectBtn.disabled = false;
    $(".detected-title").textContent = message;
    $(".add-detected-btn").hidden = found.length === 0;

    for (const name of found) {
      const li = document.createElement("li");
      li.innerHTML = `<label><input type="checkbox" checked><span></span></label>`;
      li.querySelector("span").textContent = name;
      li.querySelector("input").value = name;
      detectedList.append(li);
    }
    detected.hidden = false;
  });

  $(".add-detected-btn").addEventListener("click", () => {
    detectedList.querySelectorAll("input:checked").forEach((box) => state[kind].selected.add(box.value));
    detected.hidden = true;
    selectionChanged(kind);
  });
}

// Sends the photo to server.py, which returns names from your JSON list
async function detectItemsInPhoto(kind, file) {
  const form = new FormData();
  form.append("photo", file);

  let res;
  try {
    res = await fetch(`/api/detect/${kind}`, { method: "POST", body: form });
  } catch {
    throw new Error("Couldn’t reach the server. Make sure server.py is running.");
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `The server couldn’t read the photo (error ${res.status}).`);
  }

  // Only keep names that are actually in the list loaded on this page
  const known = new Set(state[kind].items.map((item) => item.name));
  return (data.items || []).filter((name) => known.has(name));
}

// ---------- Results (cooking page) ----------
function updateFindButton() {
  const btn = document.getElementById("find-btn");
  if (!btn) return;
  const ready = state.ingredients.selected.size > 0;
  btn.disabled = !ready;
  document.getElementById("find-hint").hidden = ready;
}

// Searches Database/recipes_json through server.py (see recipe_search.py).
// preferences = { diets: ["vegetarian"], allergies: ["peanuts", "kiwi"] }
// Each recipe: { id, name, url, image, time, have, missing, utensils, ... }
async function findRecipes(utensils, ingredients, preferences = { diets: [], allergies: [] }) {
  const data = await api("/api/recipes/search", {
    method: "POST",
    body: JSON.stringify({ utensils, ingredients, preferences }),
  });
  return data.recipes || [];
}

function renderRecipes(recipes) {
  const list = document.getElementById("results-list");
  list.innerHTML = "";

  if (!recipes.length) {
    list.innerHTML = `<p class="hint">No recipes match yet. Add a few more ingredients and try again.</p>`;
    return;
  }

  const makeList = (items, cls) => {
    const ul = document.createElement("ul");
    ul.className = cls;
    items.forEach((i) => {
      const li = document.createElement("li");
      li.textContent = i;
      ul.append(li);
    });
    return ul;
  };

  for (const r of recipes) {
    const card = document.createElement("article");
    card.className = "recipe";
    card.innerHTML = `<h3></h3><p class="meta"></p>`;
    if (r.image) {
      const img = document.createElement("img");
      img.className = "recipe-photo";
      img.src = r.image;
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", () => { img.remove(); card.classList.remove("has-photo"); });
      card.classList.add("has-photo");
      card.prepend(img);
    }
    const title = card.querySelector("h3");
    if (r.url) {
      const link = document.createElement("a");
      link.href = r.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = r.name;
      title.append(link);
    } else {
      title.textContent = r.name;
    }
    const utensilText = (r.utensils || []).join(" and ");
    card.querySelector(".meta").textContent =
      [r.time, utensilText && `uses ${utensilText}`].filter(Boolean).join(", ");

    const h1 = document.createElement("h4");
    h1.textContent = "You have";
    card.append(h1, makeList(r.have, "have"));

    const missing = r.missing || [];
    if (missing.length) {
      const h2 = document.createElement("h4");
      h2.textContent = "You’ll also need";

      // Each missing item with its estimated price (see prices.js)
      const ul = document.createElement("ul");
      ul.className = "missing priced";
      for (const name of missing) {
        const li = document.createElement("li");
        li.innerHTML = `<div class="priced-line"><span class="priced-name"></span><span class="priced-price"></span></div><span class="priced-unit"></span>`;
        li.querySelector(".priced-name").textContent = name;
        if (window.Prices) {
          const est = Prices.estimate(name);
          li.querySelector(".priced-price").textContent = `~${Prices.format(est.price)}`;
          li.querySelector(".priced-unit").textContent = est.unit;
        }
        ul.append(li);
      }
      card.append(h2, ul);

      if (window.Prices) {
        const totalLine = document.createElement("p");
        totalLine.className = "est-total";
        totalLine.innerHTML = `<span>Estimated cost</span><strong></strong>`;
        totalLine.querySelector("strong").textContent = `~${Prices.format(Prices.total(missing))}`;
        card.append(totalLine);
      }
    }

    // "Cook this" asks to buy missing ingredients (see shop.js)
    const cook = document.createElement("button");
    cook.type = "button";
    cook.className = "btn btn-solid btn-small cook-btn";
    cook.textContent = "Cook this";
    cook.addEventListener("click", () => {
      if (window.Shop) window.Shop.askToBuy(r);
    });
    card.append(cook);

    list.append(card);
  }
}

function initCook() {
  document.getElementById("find-btn").addEventListener("click", async () => {
    const btn = document.getElementById("find-btn");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner" aria-hidden="true"></span>Finding recipes…`;

    let recipes = [];
    let error = "";
    try {
      recipes = await findRecipes([...state.utensils.selected], [...state.ingredients.selected], state.preferences);
    } catch (err) {
      error = err.message;
    }

    btn.textContent = "Find recipes";
    btn.disabled = false;
    const results = document.getElementById("results");
    results.hidden = false;
    renderRecipes(recipes);
    if (error) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = error;
      document.getElementById("results-list").replaceChildren(p);
    }
    results.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });
}

// ---------- Preferences (diet and allergies) ----------
// Keep these in sync with DIETS and ALLERGIES in server.py
const DIETS = ["vegetarian", "vegan", "pescatarian", "gluten-free", "dairy-free", "halal", "kosher", "keto", "low-carb", "paleo"];
const ALLERGIES = ["peanuts", "tree nuts", "milk", "eggs", "fish", "shellfish", "wheat", "soy", "sesame"];
state.preferences = { diets: [], allergies: [] };

const getPreferences = async () => (await api("/api/preferences")).preferences;
const putPreferences = async (prefs) =>
  (await api("/api/preferences", { method: "PUT", body: JSON.stringify(prefs) })).preferences;

const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function markProfileDirty() {
  profileDirty = true;
  const status = document.getElementById("save-status");
  if (status) status.textContent = "You have unsaved changes.";
}

// Draws the diet and allergy checkboxes from state.preferences
function renderPreferences() {
  const prefs = state.preferences;
  const makeOptions = (boxId, options, chosen, field) => {
    const box = document.getElementById(boxId);
    box.innerHTML = "";
    for (const option of options) {
      const label = document.createElement("label");
      label.className = "pref-pill";
      label.innerHTML = `<input type="checkbox"><span></span>`;
      const input = label.querySelector("input");
      input.value = option;
      input.checked = chosen.includes(option);
      label.querySelector("span").textContent = capitalize(option);
      input.addEventListener("change", () => {
        const list = state.preferences[field];
        if (input.checked && !list.includes(option)) list.push(option);
        if (!input.checked) state.preferences[field] = list.filter((x) => x !== option);
        markProfileDirty();
      });
      box.append(label);
    }
  };
  makeOptions("diet-options", DIETS, prefs.diets, "diets");
  makeOptions("allergy-options", ALLERGIES, prefs.allergies, "allergies");

  // Allergies the person typed in themselves
  const custom = document.getElementById("custom-allergies");
  custom.innerHTML = "";
  for (const allergy of prefs.allergies.filter((a) => !ALLERGIES.includes(a))) {
    const li = document.createElement("li");
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.setAttribute("aria-label", `Remove ${allergy}`);
    chip.innerHTML = `<span></span><span class="x" aria-hidden="true">✕</span>`;
    chip.firstChild.textContent = capitalize(allergy);
    chip.addEventListener("click", () => {
      state.preferences.allergies = state.preferences.allergies.filter((a) => a !== allergy);
      markProfileDirty();
      renderPreferences();
    });
    li.append(chip);
    custom.append(li);
  }
}

async function initPreferences() {
  try {
    state.preferences = await getPreferences();
  } catch (err) {
    document.getElementById("save-status").textContent = err.message;
  }
  renderPreferences();

  const input = document.getElementById("custom-allergy");
  const add = () => {
    const allergy = input.value.trim().toLowerCase();
    if (!allergy) return;
    if (!state.preferences.allergies.includes(allergy)) {
      state.preferences.allergies.push(allergy);
      markProfileDirty();
    }
    input.value = "";
    renderPreferences();
  };
  document.getElementById("add-allergy-btn").addEventListener("click", add);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    }
  });
}

// ---------- Profile page ----------
async function initProfile() {
  await initPreferences();
  document.getElementById("welcome").textContent = `Signed in as ${currentUser.username}`;
  const status = document.getElementById("save-status");
  const saveBtn = document.getElementById("save-profile-btn");

  // Fill the panels with what's already saved
  try {
    for (const kind of ["utensils", "ingredients"]) {
      (await getSaved(kind)).forEach((name) => state[kind].selected.add(name));
      refresh(kind);
    }
  } catch (err) {
    status.textContent = err.message;
  }

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    status.textContent = "Saving…";
    try {
      const utensils = await putSaved("utensils", [...state.utensils.selected]);
      const staples = await putSaved("ingredients", [...state.ingredients.selected]);
      state.preferences = await putPreferences(state.preferences);
      renderPreferences();
      profileDirty = false;
      status.textContent = `Saved ${plural(utensils.length, "utensils")}, ${plural(staples.length, "staples")} and your preferences.`;
    } catch (err) {
      status.textContent = err.message;
    }
    saveBtn.disabled = false;
  });

  // Warn before leaving with unsaved changes
  window.addEventListener("beforeunload", (e) => {
    if (profileDirty) e.preventDefault();
  });
}

// ---------- Sign-in page ----------
function safeNext() {
  // Only allow going back to a page on this site
  const next = new URLSearchParams(location.search).get("next") || "profile.html";
  return /^[\w.-]+\.html(#[\w-]*)?$/.test(next) ? next : "profile.html";
}

function initLogin() {
  if (currentUser) {
    location.replace(safeNext());
    return;
  }

  const form = document.getElementById("auth-form");
  const error = document.getElementById("auth-error");
  const submit = document.getElementById("auth-submit");
  const password = document.getElementById("password");
  let mode = "login";

  const COPY = {
    login: { title: "Welcome back", sub: "Sign in to load the utensils and staples you’ve saved.", button: "Sign in", autocomplete: "current-password" },
    signup: { title: "Create your account", sub: "Save your kitchen once, then load it every time you cook.", button: "Create account", autocomplete: "new-password" },
  };

  document.querySelectorAll(".auth .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      mode = tab.dataset.mode;
      document.querySelectorAll(".auth .tab").forEach((t) => t.setAttribute("aria-selected", t === tab));
      document.getElementById("auth-title").textContent = COPY[mode].title;
      document.getElementById("auth-sub").textContent = COPY[mode].sub;
      document.getElementById("password-hint").hidden = mode !== "signup";
      submit.textContent = COPY[mode].button;
      password.autocomplete = COPY[mode].autocomplete;
      error.textContent = "";
    });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    const username = document.getElementById("username").value.trim();
    if (!username || !password.value) {
      error.textContent = "Enter a username and password.";
      return;
    }

    submit.disabled = true;
    try {
      await api(mode === "login" ? "/api/login" : "/api/signup", {
        method: "POST",
        body: JSON.stringify({ username, password: password.value }),
      });
      location.href = safeNext();
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
}

// ---------- Start ----------
(async () => {
  await loadUser();
  renderAccountLinks();

  if (PAGE === "login") {
    initLogin();
    return;
  }
  if (PAGE === "profile" && !currentUser) {
    location.replace("login.html?next=profile.html");
    return;
  }

  for (const kind of ["utensils", "ingredients"]) {
    const { data, fallback } = await loadData(kind);
    buildPanel(kind, data, fallback);
    if (kind === "ingredients" && window.Prices) Prices.registerCategories(state.ingredients.items);
  }

  if (PAGE === "profile") await initProfile();
  if (PAGE === "cook") {
    if (currentUser) state.preferences = await getPreferences().catch(() => state.preferences);
    initCook();
  }
})();