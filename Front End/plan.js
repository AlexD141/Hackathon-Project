// Shows the optimized cooking plan from planner.py when someone presses "Cook this".
// From app.js:  Plan.show(recipe)   (recipe needs an id from /api/recipes/search)
// Includes a servings control that rescales every amount, cost per serving, and swaps.
(() => {
  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  const section = el(`
    <section class="plan" id="plan" hidden aria-labelledby="plan-title">
      <div class="section-head">
        <p class="label">Your cooking plan</p>
        <h2 id="plan-title"></h2>
        <p class="hint plan-intro">The steps are reordered so you can do things at the same time and reuse pans. Start a timer and follow the clock.</p>
      </div>
      <p class="plan-status hint" aria-live="polite"></p>
      <div class="plan-content" hidden>
        <div class="plan-stats"></div>
        <div class="plan-body">
          <aside class="plan-side">
            <h3>Get out</h3>
            <ul class="plan-dishes"></ul>
            <div class="plan-ingredients-wrap">
              <h3>Ingredients</h3>
              <ul class="plan-ingredients"></ul>
            </div>
            <div class="plan-swaps-wrap">
              <h3>Swaps you can make</h3>
              <ul class="plan-swaps"></ul>
            </div>
            <div class="plan-nutrition-wrap">
              <h3>Nutrition facts</h3>
              <p class="plan-nutrition-note">Per serving, from the original recipe</p>
              <dl class="plan-nutrition"></dl>
            </div>
            <a class="plan-source" target="_blank" rel="noopener">Original recipe ↗</a>
          </aside>
          <ol class="plan-steps"></ol>
        </div>
      </div>
    </section>`);

  const $ = (sel) => section.querySelector(sel);

  // 45 -> "45 min", 120 -> "2 hr", 740 -> "12 hr 20 min"
  function formatDuration(minutes) {
    const m = Math.round(minutes);
    if (m <= 60) return `${m} min`;
    const hours = Math.floor(m / 60);
    const rest = m % 60;
    return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
  }

  let plan = null;      // plan from the server
  let recipe = null;    // the recipe card that was clicked (has missing items and swaps)
  let yieldInfo = null; // { prefix: "Serves", base: 6, unit: "" }
  let factor = 1;       // servings chosen ÷ original servings

  // ---------- Scaling amounts ----------
  const UNICODE = { "¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875 };
  const NICE = [[0.125, "⅛"], [0.25, "¼"], [1 / 3, "⅓"], [0.375, "⅜"], [0.5, "½"], [0.625, "⅝"], [2 / 3, "⅔"], [0.75, "¾"], [0.875, "⅞"]];
  const NUM = "(?:\\d+\\s+\\d+\\/\\d+|\\d+\\/\\d+|\\d+(?:\\.\\d+)?[¼½¾⅓⅔⅛⅜⅝⅞]?|[¼½¾⅓⅔⅛⅜⅝⅞])";
  const QTY_RE = new RegExp(`^(\\s*)(${NUM})(?:(\\s*(?:-|–|to)\\s*)(${NUM}))?`);

  function parseNumber(text) {
    text = text.trim();
    let m = text.match(/^(\d+)\s+(\d+)\/(\d+)$/);
    if (m) return +m[1] + m[2] / m[3];
    m = text.match(/^(\d+)\/(\d+)$/);
    if (m) return m[1] / m[2];
    m = text.match(/^(\d+(?:\.\d+)?)?([¼½¾⅓⅔⅛⅜⅝⅞])?$/);
    if (m && (m[1] || m[2])) return (m[1] ? parseFloat(m[1]) : 0) + (m[2] ? UNICODE[m[2]] : 0);
    return NaN;
  }

  function formatNumber(value) {
    const whole = Math.floor(value + 1e-9);
    const frac = value - whole;
    if (frac < 0.04) return String(whole);
    if (frac > 0.96) return String(whole + 1);
    for (const [target, symbol] of NICE) {
      if (Math.abs(frac - target) < 0.04) return whole ? `${whole}${symbol}` : symbol;
    }
    return String(Math.round(value * 100) / 100);
  }

  // Units that change with the amount: "1 cup" / "2 cups"
  const UNITS = ["cup", "teaspoon", "tablespoon", "pound", "ounce", "clove", "can", "slice", "stalk", "sprig",
    "package", "jar", "bottle", "stick", "quart", "pint", "gallon", "liter", "head", "piece", "container",
    "packet", "envelope", "fillet", "strip", "pinch", "dash", "bunch"];
  const ES_UNITS = ["pinch", "dash", "bunch"];

  function fixUnit(rest, amount) {
    const m = rest.match(/^(\s+)([A-Za-z]+)/);
    if (!m) return rest;
    const word = m[2].toLowerCase();
    const single = ES_UNITS.find((u) => word === u + "es") || UNITS.find((u) => word === u + "s") || (UNITS.includes(word) ? word : null);
    if (!single) return rest;
    const plural = ES_UNITS.includes(single) ? single + "es" : single + "s";
    return m[1] + (amount <= 1 ? single : plural) + rest.slice(m[0].length);
  }

  // "1½ cups milk" × 2 -> "3 cups milk". Only the leading amount changes; "(15 ounce) can" stays.
  function scaleAmount(text) {
    if (factor === 1 || !text) return text;
    const m = text.match(QTY_RE);
    if (!m) return text;
    const a = parseNumber(m[2]);
    if (isNaN(a)) return text;
    let scaled = m[1] + formatNumber(a * factor);
    let top = a * factor;
    if (m[4]) {
      const b = parseNumber(m[4]);
      if (!isNaN(b)) {
        scaled += m[3] + formatNumber(b * factor);
        top = b * factor;
      }
    }
    return scaled + fixUnit(text.slice(m[0].length), top);
  }

  // "Serves 6" / "Makes 24 cookies" -> { prefix, base, unit }
  function readYield(text) {
    const m = (text || "").match(/^(Serves|Makes)\s+(\d+)(?:\s*(?:-|to)\s*\d+)?\s*(.*)$/i);
    if (!m || +m[2] < 1) return null;
    return { prefix: m[1], base: +m[2], unit: m[3] || "" };
  }

  // ---------- Pieces ----------
  function stat(label, value, note) {
    const box = el(`<div class="plan-stat"><span class="plan-stat-label"></span><strong></strong><span class="plan-stat-note"></span></div>`);
    box.querySelector(".plan-stat-label").textContent = label;
    box.querySelector("strong").textContent = value;
    box.querySelector(".plan-stat-note").textContent = note || "";
    return box;
  }

  function servingsControl() {
    const box = el(`
      <div class="plan-stat plan-servings">
        <span class="plan-stat-label"></span>
        <div class="servings-control">
          <button type="button" class="servings-btn" data-step="-1">−</button>
          <strong class="servings-value" aria-live="polite"></strong>
          <button type="button" class="servings-btn" data-step="1">+</button>
        </div>
        <span class="plan-stat-note"></span>
      </div>`);
    const current = yieldInfo ? Math.round(yieldInfo.base * factor) : null;
    box.querySelector(".plan-stat-label").textContent = yieldInfo ? (yieldInfo.prefix === "Makes" ? "Makes" : "Servings") : "Batch size";
    box.querySelector(".servings-value").textContent = yieldInfo ? String(current) : `×${formatNumber(factor)}`;
    box.querySelector(".plan-stat-note").textContent = yieldInfo
      ? (factor === 1 ? (yieldInfo.unit || "As written") : `Recipe makes ${yieldInfo.base}${yieldInfo.unit ? " " + yieldInfo.unit : ""}`)
      : "Scales every amount";
    const down = box.querySelector('[data-step="-1"]');
    const up = box.querySelector('[data-step="1"]');
    down.setAttribute("aria-label", "Fewer servings");
    up.setAttribute("aria-label", "More servings");
    down.disabled = yieldInfo ? current <= 1 : factor <= 0.5;
    up.disabled = yieldInfo ? current >= yieldInfo.base * 8 : factor >= 8;
    box.querySelectorAll(".servings-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        const step = Number(btn.dataset.step);
        if (yieldInfo) factor = Math.max(1, current + step) / yieldInfo.base;
        else factor = Math.min(8, Math.max(0.5, factor + step * 0.5));
        render();
        section.querySelector(`.servings-btn[data-step="${step}"]`)?.focus();
      })
    );
    return box;
  }

  function costStat() {
    const missing = (recipe && recipe.missing) || [];
    if (!window.Prices || !missing.length) {
      return stat("Cost to buy", "$0", "You have everything");
    }
    const total = Prices.total(missing) * factor;
    const servings = yieldInfo && yieldInfo.prefix === "Serves" ? yieldInfo.base * factor : null;
    return stat("Cost to buy", `~${Prices.format(total)}`,
      servings ? `about ${Prices.format(total / servings)} per serving` : "Estimated");
  }

  function list(selector, items, wrapSelector) {
    const ul = $(selector);
    ul.innerHTML = "";
    for (const item of items) {
      const li = document.createElement("li");
      if (typeof item === "string") li.textContent = item;
      else li.append(item);
      ul.append(li);
    }
    if (wrapSelector) $(wrapSelector).hidden = items.length === 0;
  }

  // ---------- Render ----------
  function render() {
    $(".plan-stats").innerHTML = "";
    $(".plan-stats").append(
      stat("Total time", formatDuration(plan.total_minutes), factor === 1 ? "" : "Times stay the same"),
      servingsControl(),
      costStat(),
      stat("Dishes to wash", String(plan.dishes.length),
        plan.dishes_saved ? `${plan.dishes_saved} fewer than as written` : "Already as few as possible")
    );

    list(".plan-dishes", plan.dishes);
    list(".plan-ingredients", (plan.ingredients || []).map(scaleAmount), ".plan-ingredients-wrap");

    const swaps = ((recipe && recipe.swaps) || []).map((s) => {
      const box = el(`<span class="swap-line"><strong></strong> → <span class="swap-use"></span><span class="swap-how"></span></span>`);
      box.querySelector("strong").textContent = s.for;
      box.querySelector(".swap-use").textContent = s.use;
      box.querySelector(".swap-how").textContent = s.how;
      return box;
    });
    list(".plan-swaps", swaps, ".plan-swaps-wrap");

    const nutrition = $(".plan-nutrition");
    nutrition.innerHTML = "";
    for (const fact of plan.nutrition || []) {
      const row = el(`<div class="plan-nutrition-row"><dt></dt><dd></dd></div>`);
      row.querySelector("dt").textContent = fact.label;
      row.querySelector("dd").textContent = fact.value;
      nutrition.append(row);
    }
    $(".plan-nutrition-wrap").hidden = !(plan.nutrition || []).length;

    const source = $(".plan-source");
    source.hidden = !plan.source_url;
    if (plan.source_url) source.href = plan.source_url;

    const steps = $(".plan-steps");
    steps.innerHTML = "";
    for (const s of plan.steps) {
      const li = el(`
        <li class="plan-step">
          <span class="plan-time"></span>
          <div class="plan-step-body">
            <p class="plan-text"></p>
            <p class="plan-amounts" hidden></p>
            <p class="plan-meta"></p>
            <p class="plan-while" hidden></p>
          </div>
        </li>`);
      li.classList.add(`is-${s.kind}`);
      li.querySelector(".plan-time").textContent = s.start;
      // Prep steps carry their amount separately so it can be scaled: "Dice ½ yellow onion"
      li.querySelector(".plan-text").textContent = s.prep
        ? `${s.prep.verb} ${scaleAmount(s.prep.amount)}${s.prep.suffix || ""}.`
        : s.text;
      if (s.amounts && s.amounts.length) {
        const a = li.querySelector(".plan-amounts");
        a.hidden = false;
        a.textContent = s.amounts.map(scaleAmount).join(" · ");
      }
      const kind = s.kind === "prep" ? "Prep" : s.hands_off ? "Runs on its own" : "Hands-on";
      const parts = [kind, s.minutes < 1 ? "<1 min" : formatDuration(s.minutes)];
      if (s.utensils.length) parts.push(s.utensils.join(", "));
      li.querySelector(".plan-meta").textContent = parts.join(" · ");
      if (s.while.length) {
        const w = li.querySelector(".plan-while");
        w.hidden = false;
        w.textContent = `Do this while: ${s.while.join("; ")}`;
      }
      steps.append(li);
    }
  }

  async function show(r) {
    recipe = r;
    section.hidden = false;
    $("#plan-title").textContent = r.name;
    $(".plan-content").hidden = true;
    const status = $(".plan-status");

    if (!r.id) {
      status.textContent = "Cooking plans are available for recipes from your recipe collection.";
      section.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    status.textContent = "Planning the quickest way to cook this…";
    section.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    try {
      const res = await fetch(`/api/plan/${encodeURIComponent(r.id)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.plan) throw new Error(data.error || `Couldn’t make a plan (error ${res.status}).`);
      if (!data.plan.steps.length) throw new Error(data.plan.error || "This recipe has no steps to plan.");
      plan = data.plan;
      yieldInfo = readYield(plan.servings);
      factor = 1;
      render();
      status.textContent = "";
      $(".plan-content").hidden = false;
    } catch (err) {
      status.textContent = err.message.includes("fetch") ? "Couldn’t reach the server. Make sure server.py is running." : err.message;
    }
  }

  function init() {
    const results = document.getElementById("results");
    if (results) results.after(section);
    else document.querySelector("main")?.append(section);
  }

  window.Plan = { show };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();