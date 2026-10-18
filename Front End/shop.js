// Shopping cart for missing ingredients.
//
// Works with any recipe object that has a name and a `missing` list, e.g.
//   { name: "Pasta al limone", missing: ["lemon", "Parmesan"] }
// so it keeps working when the placeholder recipes are replaced with real ones.
//
// From app.js, call:  Shop.askToBuy(recipe)
(() => {
  const CART_KEY = "wcic-cart";
  const STORE_KEY = "wcic-store";

  // Stores to search when Instacart isn't set up. {q} becomes the item name.
  const STORES = [
    { id: "walmart", name: "Walmart", url: "https://www.walmart.com/search?q={q}" },
    { id: "amazon", name: "Amazon Fresh", url: "https://www.amazon.com/s?k={q}&i=amazonfresh" },
    { id: "target", name: "Target", url: "https://www.target.com/s?searchTerm={q}" },
    { id: "kroger", name: "Kroger", url: "https://www.kroger.com/search?query={q}" },
    { id: "instacart", name: "Instacart", url: "https://www.instacart.com/store/s?k={q}" },
  ];

  let cart = loadCart(); // [{ name, recipes: ["Recipe name", ...] }]
  let instacartReady = false;

  // ---------- Helpers ----------
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const sameName = (a, b) => a.toLowerCase() === b.toLowerCase();
  const inCart = (name) => cart.some((item) => sameName(item.name, name));

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function loadCart() {
    try {
      const saved = JSON.parse(localStorage.getItem(CART_KEY));
      return Array.isArray(saved) ? saved.filter((i) => i && typeof i.name === "string") : [];
    } catch {
      return [];
    }
  }

  function saveCart() {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(cart));
    } catch {
      // Storage can be full or blocked; the cart still works until the page closes
    }
    updateCount();
  }

  function selectedStore() {
    let id = null;
    try {
      id = localStorage.getItem(STORE_KEY);
    } catch {}
    return STORES.find((s) => s.id === id) || STORES[0];
  }

  // ---------- Cart actions ----------
  function addToCart(names, recipeName) {
    let added = 0;
    for (const name of names) {
      const existing = cart.find((item) => sameName(item.name, name));
      if (existing) {
        if (recipeName && !existing.recipes.includes(recipeName)) existing.recipes.push(recipeName);
      } else {
        cart.push({ name, recipes: recipeName ? [recipeName] : [] });
        added++;
      }
    }
    saveCart();
    renderCart();
    return added;
  }

  function removeFromCart(name) {
    cart = cart.filter((item) => !sameName(item.name, name));
    saveCart();
    renderCart();
  }

  // ---------- Page pieces ----------
  const cartButton = el(`
    <button type="button" class="nav-button cart-button" aria-haspopup="dialog">
      Cart <span class="cart-count">0</span>
    </button>`);

  const askDialog = el(`
    <dialog class="sheet" aria-labelledby="buy-title">
      <form method="dialog" class="sheet-body">
        <p class="label">Missing ingredients</p>
        <h2 id="buy-title"></h2>
        <p class="muted buy-text"></p>
        <ul class="buy-list"></ul>
        <p class="buy-total"></p>
        <p class="hint price-note">Prices are estimates of typical U.S. grocery prices and vary by store.</p>
        <div class="sheet-actions">
          <button type="submit" value="add" class="btn btn-solid">Add to cart</button>
          <button type="submit" value="no" class="btn btn-quiet">No thanks</button>
        </div>
      </form>
    </dialog>`);

  const cartDialog = el(`
    <dialog class="drawer" aria-labelledby="cart-title">
      <div class="drawer-head">
        <h2 id="cart-title">Shopping cart</h2>
        <button type="button" class="drawer-close" aria-label="Close cart">✕</button>
      </div>
      <p class="muted cart-empty">Your cart is empty. When a recipe needs ingredients you don’t have, you can add them here.</p>
      <ul class="cart-list"></ul>
      <div class="cart-list-actions">
        <button type="button" class="btn btn-outline btn-small clear-cart-btn">Clear all</button>
      </div>
      <div class="cart-checkout">
        <p class="cart-total"></p>
        <div class="instacart-box" hidden>
          <button type="button" class="btn btn-solid instacart-btn">Send cart to Instacart</button>
          <p class="hint">Opens Instacart with these items listed, ready to add to your cart.</p>
        </div>
        <label class="field">
          <span>Shop at</span>
          <select class="store-select"></select>
        </label>
        <p class="hint store-hint"></p>
        <div class="cart-actions">
          <button type="button" class="btn btn-outline btn-small copy-btn">Copy list</button>
        </div>
        <p class="cart-status hint" aria-live="polite"></p>
      </div>
    </dialog>`);

  const toast = el(`
    <div class="toast" role="status" hidden>
      <span class="toast-text"></span>
      <button type="button" class="link-btn toast-cart">View cart</button>
    </div>`);

  // ---------- Toast ----------
  let toastTimer = null;
  function showToast(text, withCartLink) {
    toast.querySelector(".toast-text").textContent = text;
    toast.querySelector(".toast-cart").hidden = !withCartLink;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.hidden = true), 6000);
  }

  // ---------- Ask before buying ----------
  let pendingDone = null; // runs after the question box closes (Add to cart, No thanks or Esc)

  function askToBuy(recipe, onDone) {
    const missing = [...new Set((recipe.missing || []).map(String))];

    if (missing.length === 0) {
      showToast(`You have everything for ${recipe.name}. Happy cooking!`, false);
      if (onDone) onDone();
      return;
    }

    const notInCart = missing.filter((name) => !inCart(name));
    if (notInCart.length === 0) {
      showToast(`Everything ${recipe.name} needs is already in your cart.`, true);
      if (onDone) onDone();
      return;
    }

    askDialog.querySelector("#buy-title").textContent =
      `${recipe.name} needs ${plural(missing.length, "more ingredient", "more ingredients")}`;
    askDialog.querySelector(".buy-text").textContent =
      "Do you want to buy them? Uncheck anything you already have.";

    const list = askDialog.querySelector(".buy-list");
    list.innerHTML = "";
    for (const name of missing) {
      const already = inCart(name);
      const li = el(`<li><label><input type="checkbox"><span></span></label></li>`);
      const box = li.querySelector("input");
      box.value = name;
      box.checked = !already;
      box.disabled = already;
      li.querySelector("span").textContent = already ? `${name} (already in your cart)` : name;
      if (window.Prices) {
        const est = Prices.estimate(name);
        const price = document.createElement("span");
        price.className = "buy-price";
        price.textContent = `~${Prices.format(est.price)}`;
        price.title = est.unit;
        const unit = document.createElement("span");
        unit.className = "buy-unit";
        unit.textContent = est.unit;
        li.querySelector("label").append(price);
        li.append(unit);
      }
      list.append(li);
    }

    updateBuyTotal();
    askDialog.dataset.recipe = recipe.name;
    pendingDone = onDone || null;
    askDialog.returnValue = "";
    askDialog.showModal();
  }

  function updateBuyTotal() {
    const el = askDialog.querySelector(".buy-total");
    if (!window.Prices) {
      el.textContent = "";
      return;
    }
    const names = [...askDialog.querySelectorAll(".buy-list input:checked:not(:disabled)")].map((b) => b.value);
    el.innerHTML = `<span>Estimated total</span><strong></strong>`;
    el.querySelector("strong").textContent = `~${Prices.format(Prices.total(names))}`;
  }
  askDialog.addEventListener("change", updateBuyTotal);

  askDialog.addEventListener("close", () => {
    const done = pendingDone;
    pendingDone = null;
    if (done) setTimeout(done, 0);
    if (askDialog.returnValue !== "add") return;
    const names = [...askDialog.querySelectorAll(".buy-list input:checked:not(:disabled)")].map((b) => b.value);
    if (names.length === 0) {
      showToast("Nothing was added to your cart.", false);
      return;
    }
    const added = addToCart(names, askDialog.dataset.recipe);
    showToast(`Added ${plural(added, "item", "items")} to your cart.`, true);
  });

  // ---------- Cart drawer ----------
  function updateCount() {
    cartButton.querySelector(".cart-count").textContent = cart.length;
    cartButton.setAttribute("aria-label", `Cart, ${plural(cart.length, "item", "items")}`);
  }

  function renderCart() {
    const store = selectedStore();
    const list = cartDialog.querySelector(".cart-list");
    list.innerHTML = "";

    for (const item of cart) {
      const li = el(`
        <li class="cart-item">
          <div class="cart-item-text">
            <span class="cart-item-name"></span>
            <span class="cart-item-for"></span>
            <span class="cart-item-price"></span>
          </div>
          <a class="cart-item-link" target="_blank" rel="noopener"></a>
          <button type="button" class="cart-item-remove">✕</button>
        </li>`);
      li.querySelector(".cart-item-name").textContent = item.name;
      li.querySelector(".cart-item-for").textContent = item.recipes.length ? `For ${item.recipes.join(", ")}` : "";
      if (window.Prices) {
        const est = Prices.estimate(item.name);
        li.querySelector(".cart-item-price").textContent = `~${Prices.format(est.price)} · ${est.unit}`;
      }
      const link = li.querySelector(".cart-item-link");
      link.href = store.url.replace("{q}", encodeURIComponent(item.name));
      link.textContent = `Find at ${store.name} ↗`;
      const remove = li.querySelector(".cart-item-remove");
      remove.setAttribute("aria-label", `Remove ${item.name}`);
      remove.addEventListener("click", () => removeFromCart(item.name));
      list.append(li);
    }

    const totalEl = cartDialog.querySelector(".cart-total");
    if (window.Prices && cart.length) {
      totalEl.innerHTML = `<span>Estimated total</span><strong></strong><small>Prices are estimates and vary by store.</small>`;
      totalEl.querySelector("strong").textContent = `~${Prices.format(Prices.total(cart.map((i) => i.name)))}`;
    } else {
      totalEl.textContent = "";
    }

    const empty = cart.length === 0;
    cartDialog.querySelector(".cart-list-actions").hidden = empty;
    cartDialog.querySelector(".cart-empty").hidden = !empty;
    cartDialog.querySelector(".cart-checkout").hidden = empty;
    cartDialog.querySelector(".instacart-box").hidden = !instacartReady;
    cartDialog.querySelector(".store-hint").textContent =
      `Each “Find at ${store.name}” link opens a search there, so you can add the item to your cart.`;
  }

  function openCart() {
    cartDialog.querySelector(".cart-status").textContent = "";
    renderCart();
    cartDialog.showModal();
  }

  // ---------- Set up ----------
  function init() {
    const nav = document.querySelector(".nav-links");
    if (nav) {
      const startBtn = nav.querySelector(".btn");
      nav.insertBefore(cartButton, startBtn || null);
    }
    document.body.append(askDialog, cartDialog, toast);
    updateCount();

    // Store picker
    const select = cartDialog.querySelector(".store-select");
    for (const store of STORES) {
      const opt = document.createElement("option");
      opt.value = store.id;
      opt.textContent = store.name;
      select.append(opt);
    }
    select.value = selectedStore().id;
    select.addEventListener("change", () => {
      try {
        localStorage.setItem(STORE_KEY, select.value);
      } catch {}
      renderCart();
    });

    cartButton.addEventListener("click", openCart);
    toast.querySelector(".toast-cart").addEventListener("click", () => {
      toast.hidden = true;
      openCart();
    });
    cartDialog.querySelector(".drawer-close").addEventListener("click", () => cartDialog.close());
    // Clicking the dimmed area outside the drawer closes it
    cartDialog.addEventListener("click", (e) => {
      if (e.target === cartDialog) cartDialog.close();
    });

    cartDialog.querySelector(".copy-btn").addEventListener("click", async () => {
      const status = cartDialog.querySelector(".cart-status");
      try {
        await navigator.clipboard.writeText(cart.map((i) => i.name).join("\n"));
        status.textContent = `Copied ${plural(cart.length, "item", "items")}. Paste the list into any store’s app.`;
      } catch {
        status.textContent = "Copying was blocked by the browser. Select the items and copy them by hand.";
      }
    });

    cartDialog.querySelector(".clear-cart-btn").addEventListener("click", () => {
      if (!confirm("Remove everything from your cart?")) return;
      cart = [];
      saveCart();
      renderCart();
    });

    cartDialog.querySelector(".instacart-btn").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const status = cartDialog.querySelector(".cart-status");
      btn.disabled = true;
      status.textContent = "Creating your Instacart list…";
      try {
        const res = await fetch("/api/shop/instacart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: cart.map((i) => i.name) }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.url) throw new Error(data.error || `Instacart link failed (error ${res.status}).`);
        location.assign(data.url); // your cart stays saved for when they come back
      } catch (err) {
        status.textContent = err.message.includes("fetch") ? "Couldn’t reach the server. Make sure server.py is running." : err.message;
        btn.disabled = false;
      }
    });

    // Show the Instacart button only if the server has a key
    fetch("/api/shop/config")
      .then((r) => (r.ok ? r.json() : {}))
      .then((cfg) => {
        instacartReady = Boolean(cfg.instacart);
        renderCart();
      })
      .catch(() => {});

    // Keep the count in sync if the cart changes in another tab
    window.addEventListener("storage", (e) => {
      if (e.key === CART_KEY) {
        cart = loadCart();
        updateCount();
        renderCart();
      }
    });
  }

  window.Shop = { askToBuy, addToCart, openCart };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();