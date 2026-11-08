import { api, fmt, state, computeTotals } from "../api.js";
import { h, icon, openModal, toast, toastError, printReceipt } from "../ui.js";
import { buildReceipt, methodLabel } from "../receipt.js";
import { publicVariant, publicAttributes } from "../productLabels.js";
import { renderCashStatusStrip } from "./reports.js";
import { tenderPreview } from "../tender.js";

let products = [];
let groups = [];
let categories = [];
let activeFilter = "all";
let browseLimit = 18;
const RECENT_KEY = "pypos.recent-styles";
const FAVOURITE_KEY = "pypos.favourite-styles";
let searchTerm = "";
let cart = new Map();
let discountMode = "percent";
let discountValue = 0;
let selectedLineId = null;
let customer = null;
let salesperson = "";
let useCredit = true;

let rootEl = null;
let cashStripEl = null;
let tilesEl = null;
let chipsEl = null;
let cartLinesEl = null;
let cartSummaryEl = null;
let searchInputEl = null;
let searchTimer = null;
let parkedChipEl = null;

const CART_KEY = "pypos.cart";
const METHODS = [
  ["CASH", "i-cash", "Cash"],
  ["CARD", "i-card", "Card"],
  ["UPI", "i-phone", "UPI"],
];

export async function renderRegister(main) {
  [products, groups, categories] = await Promise.all([
    api.listProducts(), api.listGroups(), api.listCategories(),
  ]);
  await restoreCart();

  main.innerHTML = "";
  rootEl = h("div", { class: "register-root" });
  main.append(rootEl);

  const headerActions = h("div", { class: "view-actions", "aria-label": "Register actions" },
    h("a", { class: "btn sm", href: "#/inventory" }, "Inventory"));
  rootEl.append(h("div", { class: "view-head" },
    h("div", { class: "view-title" },
      h("h2", {}, "Register"),
      h("div", { class: "sub" },
        "Scan an exact SKU or barcode, or browse a style and choose its variant · GST " +
        (state.settings.price_tax_mode === "inclusive" ? "included" : "added at checkout"))),
    headerActions));

  // Cash status stays visible at the till instead of being buried in Reports.
  // An open day is enforced server-side even if the status request fails.
  cashStripEl = h("section", { class: "reg-cash-strip", "aria-label": "Cash drawer" });
  rootEl.append(cashStripEl);
  void renderCashStatusStrip(cashStripEl);

  const grid = h("div", { class: "register-grid" });

  // ---- left pane: catalog ----
  const searchInput = h("input", {
    id: "reg-search",
    class: "input",
    placeholder: "Scan barcode or search style, size, colour, SKU…",
    autocomplete: "off",
    oninput: () => {
      searchTerm = searchInput.value.trim();
      browseLimit = 18;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderTiles, 130);
    },
    onkeydown: (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleScan(searchInput.value.trim());
      } else if (e.key === "Escape") {
        searchInput.value = "";
        searchTerm = "";
        browseLimit = 18;
        renderTiles();
      }
    },
  });
  searchInputEl = searchInput;

  chipsEl = h("div", { class: "reg-filters" });
  tilesEl = h("div", { class: "style-list" });

  grid.append(
    h("div", { class: "catalog-pane" },
      h("div", { class: "search-wrap" }, icon("i-search"), searchInput),
      chipsEl,
      h("div", { class: "reg-browse-hint" }, "One card per style · Choose the exact size and colour before adding"),
      h("div", { class: "product-scroll" }, tilesEl)),
  );

  // ---- right pane: cart ----
  cartLinesEl = h("div", { class: "cart-lines" });
  cartSummaryEl = h("div", { class: "cart-summary" });

  const parkBtn = h("button", {
    class: "btn ghost sm",
    title: "Park this sale and serve the next customer (F6)",
    onclick: parkFlow,
  }, icon("i-pause"), "Park");

  grid.append(
    h("div", { class: "cart-pane" },
      h("div", { class: "cart-head" },
        h("h3", {}, "Current sale"),
        h("div", { style: "display:flex;gap:6px" },
          parkBtn,
          h("button", {
            class: "btn ghost sm",
            onclick: clearCart,
            title: "Clear sale",
          }, icon("i-trash"), "Clear"))),
      cartLinesEl,
      cartSummaryEl),
  );
  rootEl.append(grid);
  rootEl.append(buildStatusbar());

  parkedChipEl = h("button", { class: "btn sm", onclick: openParkedModal,
    title: "View parked sales (F6)" }, "Parked");
  headerActions.append(parkedChipEl);

  renderChips();
  renderTiles();
  renderCart();
  refreshParkedCount();

  wireGlobalKeys();
}

async function refreshParkedCount() {
  try {
    const list = await api.listParked();
    parkedChipEl.textContent =
      `Parked (${list.length})`;
    parkedChipEl.classList.toggle("active", list.length > 0);
  } catch (_) { /* non-fatal */ }
}

function buildStatusbar() {
  const clock = h("span", { class: "sb-clock" });
  const tick = () => {
    if (!clock.isConnected) return;
    const now = new Date();
    clock.textContent = now.toLocaleDateString(undefined,
      { weekday: "short", day: "numeric", month: "short" }) +
      "  " + now.toLocaleTimeString(undefined,
      { hour: "2-digit", minute: "2-digit" });
    setTimeout(tick, 15000);
  };
  tick();
  return h("div", { class: "statusbar", id: "reg-statusbar" },
    h("span", { class: "sb-item" }, h("kbd", {}, "F2"), "Search"),
    h("span", { class: "sb-item" }, h("kbd", {}, "Enter"), "Add item"),
    h("span", { class: "sb-item" }, h("kbd", {}, "+"), h("kbd", {}, "-"), "Qty"),
    h("span", { class: "sb-item" }, h("kbd", {}, "F4"), "Payment"),
    h("span", { class: "sb-item" }, h("kbd", {}, "F6"), "Park"),
    h("span", { class: "sb-item" }, h("kbd", {}, "?"), "Help"),
    h("span", { class: "sb-spacer" }),
    clock);
}

// ================= catalog side =================

function readIds(key) {
  try {
    const ids = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(ids) ? ids.filter(x => Number.isInteger(x)).slice(0, 100) : [];
  } catch (_) { return []; }
}

function saveIds(key, ids) {
  localStorage.setItem(key, JSON.stringify(ids.slice(0, 100)));
}

function rememberStyle(groupId) {
  if (!groupId) return;
  saveIds(RECENT_KEY, [groupId, ...readIds(RECENT_KEY).filter(id => id !== groupId)]);
}

function favouriteStyle(groupId) {
  const ids = readIds(FAVOURITE_KEY);
  saveIds(FAVOURITE_KEY, ids.includes(groupId) ? ids.filter(id => id !== groupId) : [groupId, ...ids]);
  renderTiles();
}

function renderChips() {
  chipsEl.replaceChildren();
  const choices = [
    ["all", "All styles"], ["available", "In stock"],
    ["recent", "Recent"], ["favourites", "Favourites"],
    ...categories.map(c => [`category:${c.id}`, c.name]),
  ];
  for (const [key, label] of choices) {
    chipsEl.append(h("button", {
      class: `chip${activeFilter === key ? " active" : ""}`,
      onclick: () => {
        activeFilter = key; browseLimit = 18;
        renderChips(); renderTiles();
      },
    }, label));
  }
}

function variantText(p) {
  return publicVariant(p.attributes || []);
}

function stockBadge(p) {
  const threshold = p.reorder_point === null || p.reorder_point === undefined
    ? Number(state.settings.low_stock_threshold || 0)
    : Number(p.reorder_point);
  if (p.quantity <= 0) return h("span", { class: "stock-badge out" }, "Out");
  if (p.quantity <= threshold) return h("span", { class: "stock-badge low" }, `${p.quantity} left`);
  return h("span", { class: "stock-badge" }, `${p.quantity} in stock`);
}

function matchingStyles() {
  const byGroup = new Map();
  const q = searchTerm.toLocaleLowerCase("en-IN");
  for (const p of products) {
    const groupKey = p.group_id ? `g:${p.group_id}` : `p:${p.id}`;
    const name = p.group_name || p.SKU;
    const searchable = [name, p.SKU, p.barcode, p.brand_name, p.category_name,
      ...publicAttributes(p.attributes || []).flatMap(a => [a.name, a.value])].join(" ").toLocaleLowerCase("en-IN");
    if (q && !searchable.includes(q)) continue;
    if (activeFilter === "available" && p.quantity <= 0) continue;
    if (activeFilter === "recent" && !readIds(RECENT_KEY).includes(p.group_id)) continue;
    if (activeFilter === "favourites" && !readIds(FAVOURITE_KEY).includes(p.group_id)) continue;
    if (activeFilter.startsWith("category:") &&
        String(p.category_id) !== activeFilter.slice(9)) continue;
    if (!byGroup.has(groupKey)) byGroup.set(groupKey, {
      key: groupKey, id: p.group_id || null, name,
      brand: p.brand_name || "", category: p.category_name || "",
      variants: [],
    });
    byGroup.get(groupKey).variants.push(p);
  }
  const recent = readIds(RECENT_KEY);
  return [...byGroup.values()].sort((a, b) => {
    if (activeFilter === "recent") return recent.indexOf(a.id) - recent.indexOf(b.id);
    return a.name.localeCompare(b.name, "en-IN", { numeric: true });
  });
}

function renderTiles() {
  tilesEl.replaceChildren();
  const styles = matchingStyles();
  const displayed = styles.slice(0, browseLimit);
  if (!styles.length) {
    const emptyCatalog = products.length === 0;
    tilesEl.append(h("div", { class: "empty-state register-empty" },
      icon("i-search"),
      h("h3", {}, emptyCatalog ? "No products in the catalogue" :
        searchTerm ? "No matching styles" : "No styles in this view"),
      h("p", {}, emptyCatalog ? "Add or import products in Catalogue to start selling." :
        searchTerm ? "Search another product name, SKU, barcode or attribute." :
        "Choose All styles or clear the current filter to see more products."),
      emptyCatalog ? h("a", { class: "btn primary", href: "#/catalog" },
        "Open Catalogue / Import") : null));
    return;
  }
  const favs = readIds(FAVOURITE_KEY);
  for (const s of displayed) {
    const available = s.variants.filter(p => p.quantity > 0);
    const totalStock = s.variants.reduce((sum, p) => sum + p.quantity, 0);
    const prices = [...new Set(s.variants.map(p => Number(p.price)))].sort((a, b) => a - b);
    const priceLabel = prices.length === 1 ? fmt(prices[0]) :
      `${fmt(prices[0])} – ${fmt(prices[prices.length - 1])}`;
    const open = () => {
      if (!available.length) { toast("No available stock for this style.", "warn"); return; }
      // Single-SKU styles are added immediately; multiple variants need an exact choice.
      if (s.variants.length === 1) addToCart(s.variants[0]);
      else openVariantPicker(s);
    };
    tilesEl.append(h("div", { class: "style-card" },
      h("button", { class: "style-main", onclick: open, disabled: !available.length },
        h("div", { class: "style-name" }, s.name),
        h("div", { class: "style-meta" },
          [s.brand, s.category, `${s.variants.length} variant${s.variants.length !== 1 ? "s" : ""}`]
            .filter(Boolean).join(" · ")),
        h("div", { class: "style-bottom" },
          h("span", { class: "style-price" }, priceLabel),
          h("span", { class: `stock-badge${totalStock ? "" : " out"}` },
            totalStock ? `${totalStock} total` : "Out of stock"))),
      s.id ? h("button", {
        class: `style-fav${favs.includes(s.id) ? " selected" : ""}`,
        title: favs.includes(s.id) ? "Remove favourite" : "Add favourite",
        "aria-label": favs.includes(s.id) ? "Remove favourite" : "Add favourite",
        onclick: () => favouriteStyle(s.id),
      }, favs.includes(s.id) ? "★" : "☆") : null));
  }
  if (styles.length > browseLimit) {
    tilesEl.append(h("button", { class: "btn block", onclick: () => {
      browseLimit += 24; renderTiles();
    } }, `Show more styles (${browseLimit} of ${styles.length} shown)`));
  }
}

function openVariantPicker(style) {
  const all = style.variants;
  const common = ["colour", "color", "size", "fit"];
  const names = [...new Set(all.flatMap(p => publicAttributes(p.attributes || []).map(a => a.name)))];
  // Receipt-facing attributes only; keep all other attributes editable in Catalog.
  names.sort((a,b) => {
    const ai = common.indexOf(a.toLowerCase()); const bi = common.indexOf(b.toLowerCase());
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.localeCompare(b);
  });
  const facets = names.slice(0, 3);
  const selected = {};
  const filters = h("div", { class: "variant-facets" });
  const variants = h("div", { class: "variant-results" });
  let modal;
  const render = () => {
    variants.replaceChildren();
    const filtered = all.filter(p => facets.every(name => !selected[name] ||
      (p.attributes || []).some(a => a.name === name && a.value === selected[name])));
    variants.append(h("p", { class: "form-hint" },
      `${filtered.length} exact SKU${filtered.length === 1 ? "" : "s"} · Choose one to add`));
    for (const p of filtered) variants.append(h("button", {
      class: "variant-row", disabled: p.quantity <= 0,
      onclick: () => { addToCart(p); modal.close(); },
    }, h("div", {}, h("div", { class: "variant-sku" }, variantText(p) || p.SKU),
       h("div", { class: "style-meta" }, `SKU ${p.SKU}${p.barcode ? ` · ${p.barcode}` : ""}`)),
       h("div", { class: "variant-right" }, h("strong", {}, `MRP ${fmt(p.price)}`), stockBadge(p))));
    if (!filtered.length) variants.append(h("p", { class: "form-hint" },
      "No SKU matches those options. Clear a filter."));
  };
  for (const name of facets) {
    const options = [...new Set(all.flatMap(p => (p.attributes || [])
      .filter(a => a.name === name).map(a => a.value)))].sort((a,b) => a.localeCompare(b, "en-IN"));
    const select = h("select", { class: "input", onchange: e => {
      selected[name] = e.target.value; render();
    } }, h("option", { value: "" }, `All ${name}`),
      ...options.map(v => h("option", { value: v }, v)));
    filters.append(h("label", { class: "variant-filter" },
      h("span", {}, name), select));
  }
  const clear = h("button", { class: "btn sm", onclick: () => {
    for (const key of Object.keys(selected)) delete selected[key];
    filters.querySelectorAll("select").forEach(s => { s.value = ""; });
    render();
  } }, "Clear filters");
  modal = openModal({ title: style.name, wide: true,
    body: h("div", {},
      h("p", { class: "form-hint" },
        "Select the correct size, colour and MRP. If two stock editions share a barcode, scanning asks you to choose the marked price."),
      filters, variants),
    footer: [clear, h("button", { class: "btn", onclick: () => modal.close() }, "Close")],
  });
  render();
}

function choosePriceForBarcode(code, matches) {
  const variants = matches.map(p => products.find(x => x.id === p.id) || normalizeFetched(p));
  const available = variants.filter(p => p.quantity > 0);
  if (!available.length) {
    toast(`All MRP editions of barcode ${code} are out of stock.`, "warn");
    return;
  }
  const entries = h("div", { class: "variant-results" });
  let modal;
  for (const p of variants) {
    entries.append(h("button", {
      class: "variant-row", disabled: p.quantity <= 0,
      onclick: () => {
        addToCart(p);  // exact SKU with its own price and quantity
        modal.close();
        searchInputEl.value = "";
        searchTerm = "";
        renderTiles();
      },
    }, h("div", {},
      h("div", { class: "variant-sku" }, `MRP ${fmt(p.price)}`),
      h("div", { class: "style-meta" },
        `${p.group_name || p.SKU} · ${variantText(p) || p.SKU}`)),
      h("div", { class: "variant-right" }, stockBadge(p))));
  }
  modal = openModal({
    title: "Choose marked price",
    body: h("div", {},
      h("p", { class: "form-hint" },
        `EAN ${code} is shared by old/new MRP stock. Check the actual package MRP before selecting.`),
      entries),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel")],
  });
}

async function handleScan(code) {
  if (!code) return;
  try {
    const response = await api.lookupCode(code);
    if (Array.isArray(response.matches)) {
      choosePriceForBarcode(code, response.matches);
      return;
    }
    const match = products.find(x => x.id === response.id) || normalizeFetched(response);
    if (match.quantity <= 0) {
      toast(`SKU ${match.SKU} is out of stock.`, "warn"); return;
    }
    addToCart(match);
    searchInputEl.value = "";
    searchTerm = "";
    browseLimit = 18;
    renderTiles();
  } catch (e) {
    if (e.status === 404) {
      toast(`No exact barcode/SKU match for “${code}”. Select a style manually below.`, "warn");
    } else toastError(e);
  }
}

function normalizeFetched(raw) {
  raw.attributes = raw.attributes || [];
  const existing = products.find(p => p.id === raw.id);
  if (existing) Object.assign(existing, raw);
  else products.push(raw);
  return raw;
}

// ================= cart side =================

function byId(id) {
  return products.find((p) => p.id === id);
}

function addToCart(p, qty = 1) {
  const current = cart.get(p.id) || 0;
  if (current + qty > p.quantity) {
    toast(`Only ${p.quantity} × ${p.group_name || p.SKU} in stock.`, "warn",
      { title: "Not enough stock" });
    qty = p.quantity - current;
    if (qty <= 0) return;
  }
  cart.set(p.id, current + qty);
  rememberStyle(p.group_id);
  selectedLineId = p.id;
  saveCart();
  renderCart();
}

function setQty(id, qty) {
  if (qty <= 0) {
    cart.delete(id);
  } else {
    cart.set(id, qty);
  }
  saveCart();
  renderCart();
}

function changeQty(id, delta) {
  const p = byId(id);
  const current = cart.get(id) || 0;
  const next = current + delta;
  if (next > p.quantity) {
    toast(`Only ${p.quantity} in stock.`, "warn");
    return;
  }
  setQty(id, next);
}

function clearCart() {
  if (cart.size === 0 && !customer && !salesperson) return;
  cart.clear();
  discountValue = 0;
  selectedLineId = null;
  customer = null;
  salesperson = "";
  useCredit = true;
  saveCart();
  renderCart();
}

function cartTotals() {
  const lines = [...cart.entries()].map(([id, qty]) => {
    const p = byId(id);
    return { price: p.price, tax_rate: p.tax_rate, quantity: qty };
  });
  return computeTotals(lines, discountValue > 0 ? discountMode : null, discountValue,
    state.settings.price_tax_mode === "inclusive");
}

function renderCart() {
  // A saved or recalled manager cart may contain an old discount.  Do not
  // keep a hidden discount active when a cashier takes over the register.
  if (state.staff?.operator?.role === "cashier" &&
      Number(state.settings.cashier_max_discount_percent || 0) === 0 && discountValue !== 0) {
    discountValue = 0;
    saveCart();
  }
  cartLinesEl.innerHTML = "";
  cartSummaryEl.innerHTML = "";

  if (cart.size === 0) {
    cartLinesEl.append(h("div", { class: "cart-empty" },
      icon("i-register"),
      h("div", {}, "Tap a product or scan a barcode"),
      h("div", { style: "font-size:12px" }, "to start a sale")));
  }

  for (const [id, qty] of cart.entries()) {
    const p = byId(id);
    cartLinesEl.append(
      h("div", { class: "cart-line", dataset: { lineId: String(id) },
          onclick: () => { selectedLineId = id; highlightSelected(); } },
        h("div", { class: "cl-info" },
          h("div", { class: "cl-name" }, p.group_name || p.SKU,
            p.attributes.length
              ? h("span", { class: "cl-sub" }, ` · ${variantText(p)}`)
              : null),
          h("div", { class: "cl-sub" }, `${fmt(p.price)} × ${qty}`)),
        h("div", { class: "stepper" },
          h("button", { onclick: (e) => { e.stopPropagation(); changeQty(id, -1); } },
            icon("i-minus")),
          h("span", { class: "st-qty" }, String(qty)),
          h("button", {
            disabled: qty >= p.quantity,
            onclick: (e) => { e.stopPropagation(); changeQty(id, +1); },
          }, icon("i-plus"))),
        h("div", { class: "cl-total" }, fmt(p.price * qty)),
        h("button", {
          class: "cl-remove btn ghost icon-only sm",
          "aria-label": `Remove ${p.group_name}`,
          onclick: (e) => { e.stopPropagation(); setQty(id, 0); },
        }, icon("i-x"))));
  }

  const t = cartTotals();

  const chargeBtnNew = h("button", {
    class: "btn primary lg block",
    disabled: cart.size === 0,
    onclick: openPayment,
  }, icon("i-cash"), " Charge ", fmt(t.total));

  const customerBox = customer
    ? h("div", { class: "customer-box attached" },
        icon("i-users"),
        h("div", { class: "cb-main", onclick: openCustomerPicker,
                   title: "Change customer", role: "button", tabindex: "0" },
          h("div", { class: "cb-name" }, customer.name,
            customer.store_credit > 0
              ? h("span", { class: "pill ok" }, `${fmt(customer.store_credit)} credit`)
              : null),
          h("div", { class: "cb-sub" },
            `${Number(customer.loyalty_points || 0).toLocaleString("en-IN")} loyalty points · Tap to change`)),
        h("button", {
          class: "btn ghost icon-only sm", title: "Detach — sell as walk-in",
          onclick: (e) => {
            e.stopPropagation();
            customer = null;
            useCredit = true;
            saveCart();
            renderCart();
          },
        }, icon("i-x")))
    : h("button", { class: "customer-box", onclick: openCustomerPicker,
                    title: "Attach a customer to this sale" },
        icon("i-users"),
        h("div", { class: "cb-main" },
          h("div", { class: "cb-name" }, "Walk-in customer"),
          h("div", { class: "cb-sub" },
            "Tap to attach — purchase history & store credit")));

  const sumRows = [
    h("div", { class: "sum-row discount-edit" }, customerBox),
    state.staff?.operator?.role === "cashier"
      ? h("div", { class: "reg-salesperson-field" },
          h("label", {}, "Salesperson"),
          h("div", { class: "form-hint" },
            `${state.staff.operator.username} (signed-in operator)`))
      : h("div", { class: "reg-salesperson-field" },
          h("label", { for: "reg-salesperson" }, "Salesperson ",
            h("span", { class: "reg-salesperson-optional" }, "(optional · internal only)")),
          h("input", { id: "reg-salesperson", class: "input", type: "text",
            maxlength: "80", autocomplete: "off", placeholder: "Name for sales tracking",
            value: salesperson, oninput: (e) => {
              salesperson = e.target.value;
              saveCart();
            },
          })),
    (state.staff?.operator?.role === "cashier" && Number(state.settings.cashier_max_discount_percent || 0) === 0)
      ? h("div", { class: "sum-row discount-edit" },
          h("span", {}, "Discount"), h("span", { class: "form-hint" }, "Manager approval required"))
      : h("div", { class: "sum-row discount-edit" },
      h("span", {}, "Discount"),
      h("input", {
        class: "input", type: "number", min: "0", step: "any",
        placeholder: "0", value: discountValue > 0 ? String(discountValue) : "",
        onchange: (e) => {
          const v = parseFloat(e.target.value);
          discountValue = isNaN(v) || v < 0 ? 0 : v;
          renderCart();
        },
      }),
      h("select", {
        class: "input",
        onchange: (e) => { discountMode = e.target.value; renderCart(); },
      },
        h("option", { value: "percent", selected: discountMode === "percent" }, "%"),
        h("option", { value: "amount", selected: discountMode === "amount" }, state.settings.currency || "₹"))),
    h("div", { class: "sum-row" },
      h("span", {}, `Subtotal (${[...cart.values()].reduce((a, b) => a + b, 0)} items)`),
      h("span", {}, fmt(t.subtotal))),
  ];
  if (t.discount > 0) {
    sumRows.push(h("div", { class: "sum-row" },
      h("span", {}, "Discount"),
      h("span", { style: "color:var(--danger)" }, "-" + fmt(t.discount))));
  }
  sumRows.push(
    h("div", { class: "sum-row" },
      h("span", {}, `${state.settings.tax_label || "Tax"}`),
      h("span", {}, fmt(t.tax))),
    h("div", { class: "sum-row total-row" },
      h("span", {}, "Total"), h("span", {}, fmt(t.total))),
    chargeBtnNew,
  );

  cartSummaryEl.replaceChildren(...sumRows);

  highlightSelected();
}

function highlightSelected() {
  cartLinesEl.querySelectorAll(".cart-line").forEach((el) => {
    el.style.background =
      Number(el.dataset.lineId) === selectedLineId ? "var(--accent-soft)" : "";
  });
}

function saveCart() {
  localStorage.setItem(CART_KEY, JSON.stringify({
    items: [...cart.entries()],
    discountMode,
    discountValue,
    customerId: customer ? customer.id : null,
    salesperson,
  }));
}

async function restoreCart() {
  try {
    const saved = JSON.parse(localStorage.getItem(CART_KEY) || "null");
    if (!saved) return;
    for (const [id, qty] of saved.items || []) {
      const p = products.find((x) => x.id === id);
      if (p && p.quantity > 0) cart.set(id, Math.min(qty, p.quantity));
    }
    discountMode = saved.discountMode || "percent";
    discountValue = Math.max(0, Number(saved.discountValue) || 0);
    salesperson = typeof saved.salesperson === "string" ? saved.salesperson.slice(0, 80) : "";
    if (saved.customerId) {
      try {
        customer = await api.getCustomer(saved.customerId);
      } catch (_) { customer = null; }
    }
  } catch (_) { /* corrupted cart — ignore */ }
}

// ================= customer picker =================

function openCustomerPicker() {
  const search = h("input", {
    class: "input", placeholder: "Search name or phone…", autocomplete: "off",
  });
  const results = h("div", { class: "picker-results" });

  let timer = null;
  async function run() {
    results.innerHTML = "";
    results.append(h("div", { class: "skel-rows" },
      ...Array.from({ length: 3 }, () => h("div", { class: "skeleton" }))));
    let list = [];
    try {
      list = await api.listCustomers(search.value.trim());
    } catch (e) {
      toastError(e);
    }
    if (!search.isConnected) return;
    results.innerHTML = "";
    if (!list.length) {
      results.append(h("p", { style: "color:var(--muted);padding:10px 4px;font-size:13px" },
        search.value ? "No customers match." : "No customers yet — add one below."));
    }
    for (const c of list) {
      results.append(h("button", {
        class: "chip",
        style: "width:100%;justify-content:space-between",
        onclick: () => {
          customer = c;
          useCredit = true;
          saveCart();
          renderCart();
          m.close();
        },
      },
        h("span", {}, c.name),
        h("span", { class: "mono", style: "color:var(--muted)" },
          [c.phone, c.store_credit > 0 ? `${fmt(c.store_credit)} credit` : null,
           `${Number(c.loyalty_points || 0).toLocaleString("en-IN")} pts`]
            .filter(Boolean).join(" · ") || "")));
    }
  }

  const nameIn = h("input", { class: "input", placeholder: "Full name", style: "flex:1" });
  const phoneIn = h("input", { class: "input", placeholder: "Phone (optional)", style: "flex:1" });
  const addBtn = h("button", {
    class: "btn sm",
    onclick: async () => {
      if (!nameIn.value.trim()) return toast("Enter a name to add a customer.", "warn");
      try {
        customer = await api.createCustomer({ name: nameIn.value, phone: phoneIn.value });
        useCredit = true;
        saveCart();
        renderCart();
        m.close();
        toast(`${customer.name} added.`, "success");
      } catch (e) {
        toastError(e);
      }
    },
  }, icon("i-plus"), "Add & attach");

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, 160);
  });

  const m = openModal({
    title: "Attach customer",
    narrow: true,
    body: h("div", {},
      customer
        ? h("button", {
            class: "btn sm block", style: "margin-bottom:8px",
            onclick: () => {
              customer = null;
              useCredit = true;
              saveCart();
              renderCart();
              m.close();
            },
          }, icon("i-x"), `Detach ${customer.name} (walk-in)`)
        : null,
      h("div", { class: "search-wrap", style: "margin-bottom:10px" },
        icon("i-search"), search),
      results,
      h("div", { class: "panel-head", style: "margin-top:14px;padding-left:0" },
        h("h3", {}, "Quick add")),
      h("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" },
        nameIn, phoneIn, addBtn)),
  });

  run();
  setTimeout(() => search.focus(), 40);
}

// ================= park / recall =================

async function parkFlow() {
  if (cart.size === 0) return toast("Nothing to park — the cart is empty.", "warn");
  const labelInput = h("input", {
    class: "input",
    value: new Date().toLocaleTimeString(undefined,
      { hour: "2-digit", minute: "2-digit" }),
  });
  const okBtn = h("button", { class: "btn primary" }, icon("i-pause"), "Park sale");
  let parking = false;
  okBtn.addEventListener("click", async () => {
    if (parking) return;
    parking = true;
    okBtn.disabled = true;
    try {
      await api.parkCart({
        items: [...cart.entries()].map(([product_id, quantity]) =>
          ({ product_id, quantity })),
        label: labelInput.value.trim(),
        discount: discountValue > 0
          ? { mode: discountMode, value: discountValue } : null,
        customer_id: customer ? customer.id : null,
        salesperson,
      });
      cart.clear();
      discountValue = 0;
      selectedLineId = null;
      customer = null;
      salesperson = "";
      saveCart();
      renderCart();
      refreshParkedCount();
      m.close();
      toast("Sale parked — pick it up any time from Parked.", "success");
    } catch (e) {
      toastError(e);
    } finally {
      parking = false;
      okBtn.disabled = false;
    }
  });
  const m = openModal({
    title: "Park this sale",
    narrow: true,
    body: h("div", { class: "field" },
      h("label", {}, "Label"),
      labelInput,
      h("div", { class: "form-hint" },
        "Parked orders hold no stock — availability is checked when you recall and charge.")),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      okBtn,
    ],
  });
}

async function openParkedModal() {
  let list = [];
  try {
    list = await api.listParked();
  } catch (e) {
    return toastError(e);
  }

  const bodyEl = h("div", {});
  if (!list.length) {
    bodyEl.append(h("div", { class: "empty-state" },
      icon("i-pause"),
      h("h3", {}, "No parked sales"),
      h("p", {}, "Press F6 at the register to put an in-progress sale on hold.")));
  } else {
    for (const t of list) {
      const stockWarn = t.items.some((it) => it.quantity > it.in_stock);
      const cardEl = h("div", { class: "card", style: "padding:10px 14px;margin-bottom:8px;display:flex;align-items:center;gap:12px" },
        h("div", { style: "flex:1;min-width:0" },
          h("div", { class: "cell-main" }, t.label),
          h("div", { class: "cell-sub" },
            `${t.item_count} item${t.item_count === 1 ? "" : "s"} · ${fmt(t.est_total)}` +
            (t.customer_name ? ` · ${t.customer_name}` : "") +
            (stockWarn ? " · some items low on stock" : ""))),
        h("button", {
          class: "btn sm primary",
          onclick: async () => {
            await recallTicket(t.id);
            m.close();
          },
        }, icon("i-play"), "Recall"),
        h("button", {
          class: "btn ghost icon-only sm", title: "Discard parked sale",
          onclick: async () => {
            try {
              await api.discardParked(t.id);
              cardEl.remove();
              if (!bodyEl.querySelector(".card")) {
                bodyEl.append(h("div", { class: "empty-state" },
                  icon("i-pause"), h("h3", {}, "No parked sales"),
                  h("p", {}, "All clear.")));
              }
              refreshParkedCount();
            } catch (e) {
              toastError(e);
            }
          },
        }, icon("i-trash")));
      bodyEl.append(cardEl);
    }
  }

  const m = openModal({
    title: "Parked sales",
    body: bodyEl,
  });
}

async function recallTicket(id) {
  let ticket;
  try {
    ticket = await api.getParked(id);
  } catch (e) {
    return toastError(e);
  }

  const doLoad = async () => {
    cart.clear();
    for (const it of ticket.items) {
      const p = products.find((x) => x.id === it.product_id) || it;
      if (!products.find((x) => x.id === it.product_id)) {
        products.push({
          id: it.product_id, group_name: it.name, SKU: it.SKU,
          price: it.price, tax_rate: it.tax_rate, quantity: it.in_stock,
          attributes: [],
        });
      }
      const available = p.quantity ?? it.in_stock ?? 0;
      cart.set(it.product_id, Math.min(it.quantity, Math.max(available, 0)));
    }
    discountMode = ticket.discount_mode || "percent";
    discountValue = Number(ticket.discount_value) || 0;
    if (ticket.customer_id) {
      try { customer = await api.getCustomer(ticket.customer_id); }
      catch (_) { customer = null; }
    } else {
      customer = null;
    }
    salesperson = ticket.salesperson || "";
    selectedLineId = null;
    saveCart();
    renderChips();
    renderTiles();
    renderCart();
    try {
      await api.discardParked(ticket.id);
    } catch (_) { /* already gone */ }
    refreshParkedCount();
    toast(`“${ticket.label}” recalled to the register.`, "success");
  };

  if (cart.size > 0) {
    const ok = await confirmDialog({
      title: "Replace current cart?",
      message: "The items currently in the cart will be replaced by the recalled order.",
      confirmLabel: "Recall anyway",
    });
    if (!ok) return;
  }
  await doLoad();
}

// ================= payment =================

async function refreshCatalog() {
  [products, groups, categories] = await Promise.all([
    api.listProducts(), api.listGroups(), api.listCategories(),
  ]);
  renderChips();
  renderTiles();
}

async function openPayment() {
  if (cart.size === 0) return;
  try {
    const session = await api.currentSession();
    if (!session || !session.session || session.session.closed_at) {
      toast("Start Day in Cash Drawer before collecting payment.", "warn");
      return;
    }
  } catch (e) {
    toastError(e, "Cannot verify open cash session");
    return;
  }
  const t = cartTotals();
  const creditAvailable = customer && useCredit !== false
    ? Math.min(Number(customer.store_credit || 0), t.total)
    : 0;
  const dueTotal = Math.round((t.total - creditAvailable) * 100) / 100;

  const rowsWrap = h("div", { class: "pay-rows" });
  const remainingEl = h("div", { class: "pay-remaining uncovered" });
  const quickWrap = h("div", {});
  const changeEl = h("div", { class: "change-line", style: "display:none" });

  const payRows = [];

  const creditToggle = creditAvailable > 0
    ? h("label", { class: "credit-toggle" },
        h("input", {
          type: "checkbox",
          checked: true,
          onchange: (e) => {
            useCredit = e.target.checked;
            syncCredit();
          },
        }),
        h("span", {}, `Use store credit (${fmt(creditAvailable)} available)`))
    : null;

  function syncCredit() {
    // Recompute how much store credit applies and retarget the single
    // default cash row so quick-cash math stays honest.
    const avail = customer
      ? Math.min(Number(customer.store_credit || 0), t.total) : 0;
    creditApplied = useCredit ? avail : 0;
    due = Math.round((t.total - creditApplied) * 100) / 100;
    if (bannerAmt) bannerAmt.textContent = fmt(due);
    if (payRows.length === 1 && payRows[0].select.value === "CASH") {
      payRows[0].input.value = Math.max(0, due).toFixed(2);
    }
    update();
  }

  let creditApplied = creditAvailable;
  let due = dueTotal;
  let bannerAmt = null;

  function addPayRow(method = "CASH", amount = "") {
    const select = h("select", { class: "input" },
      METHODS.map(([v, , label]) =>
        h("option", { value: v, selected: v === method }, label)));
    const input = h("input", {
      class: "input num", type: "number", min: "0", step: "any",
      placeholder: "0.00", value: amount,
      oninput: () => {
        if (select.value === "UPI") {
          checked.checked = false;
          utrInput.value = "";
        }
        update();
      },
    });
    const note = h("span", { class: "upi-reference" }, "Preparing reference…");
    const utrInput = h("input", { class: "input", maxlength: "40", autocomplete: "off",
      placeholder: "Enter actual UPI UTR (from payment details)" });
    const checked = h("input", { type: "checkbox" });
    const showQR = h("button", { class: "btn sm", type: "button", onclick: async () => {
      if (!row.reference) await ensureReference(row);
      if (!row.reference) return;
      if (!state.settings.store_upi_id) {
        toast("Set your UPI ID under Settings → Store first. You can still use the reference note.", "warn");
        return;
      }
      const amount = Number(input.value);
      if (!Number.isFinite(amount) || amount <= 0 ||
          Math.abs(amount * 100 - Math.round(amount * 100)) > 1e-7) {
        return toast("Enter an exact UPI amount in rupees and paise.", "warn");
      }
      const qs = new URLSearchParams({ amount: amount.toFixed(2), ref: row.reference });
      const qrImg = h("img", { src: `/api/upi/qr.svg?${qs.toString()}`,
        alt: `UPI payment QR for ${row.reference}`, class: "upi-qr-image" });
      const m = openModal({ title: "UPI — scan to pay", narrow: true,
        body: h("div", { class: "upi-qr-box" },
          h("b", {}, fmt(amount)), qrImg,
          h("div", {}, `Merchant: ${state.settings.store_upi_id}`),
          h("code", {}, row.reference),
          h("p", { class: "form-hint" },
            "Merchant reference is not a bank UTR. Check payment in your merchant/bank app before completing the sale.")),
        footer: [h("button", { class: "btn primary", onclick: () => m.close() }, "Close QR")],
      });
    } }, "Show QR");
    const copyRef = h("button", { class: "btn sm", type: "button", onclick: async () => {
      if (!row.reference) await ensureReference(row);
      if (!row.reference) return;
      try { await navigator.clipboard.writeText(row.reference); toast("Reference copied.", "success"); }
      catch (_) { toast(`Reference: ${row.reference}`); }
    } }, "Copy reference");
    const extra = h("div", { class: "upi-extra", hidden: method !== "UPI" },
      h("div", { class: "upi-ref-line" },
        h("span", {}, "Ask payer to include note:"), note, copyRef, showQR),
      utrInput,
      h("label", { class: "credit-toggle" }, checked,
        h("span", {}, "I checked that this UPI payment was received (manual confirmation)")),
      h("div", { class: "form-hint" },
        "POS reference is generated locally; the bank issues the actual UTR after payment."));
    const row = { select, input, extra, checked, utrInput, reference: "", refTask: null };
    row.el = h("div", { class: "pay-row" },
      select,
      input,
      h("button", {
        class: "btn ghost icon-only",
        title: "Remove split",
        onclick: () => {
          payRows.splice(payRows.indexOf(row), 1);
          row.el.remove();
          update();
        },
      }, icon("i-x")), extra);
    async function ensureReference(r) {
      if (r.reference) return r.reference;
      if (!r.refTask) r.refTask = api.upiReference().then(data => {
        r.reference = data.reference;
        note.textContent = r.reference;
        update();
        return r.reference;
      }).catch(e => {
        r.refTask = null; // A transient API error must not permanently disable this row.
        note.textContent = "Reference unavailable";
        toastError(e);
        return "";
      });
      return r.refTask;
    }
    const syncMethod = () => {
      // A confirmation applies only to the exact tender method and amount.
      checked.checked = false;
      utrInput.value = "";
      extra.hidden = select.value !== "UPI";
      if (select.value === "UPI") void ensureReference(row);
      update();
    };
    select.addEventListener("change", syncMethod);
    checked.addEventListener("change", update);
    if (method === "UPI") void ensureReference(row);
    payRows.push(row);
    rowsWrap.append(row.el);
    setTimeout(() => input.focus(), 20);
    update();
  }

  function payments() {
    return payRows.map((r) => ({
      method: r.select.value,
      amount: parseFloat(r.input.value) || 0,
    })).filter((p) => p.amount > 0);
  }

  function lastCashRow() {
    return [...payRows].reverse().find((r) => r.select.value === "CASH");
  }

  function update() {
    const result = tenderPreview(due, payments());
    if (!result.valid) {
      remainingEl.textContent = result.error;
      remainingEl.className = "pay-remaining uncovered";
    } else if (result.remaining > 0) {
      remainingEl.textContent = `Remaining due: ${fmt(result.remaining)}`;
      remainingEl.className = "pay-remaining uncovered";
    } else {
      remainingEl.textContent = "Fully covered ✓";
      remainingEl.className = "pay-remaining covered";
    }
    const upiUnconfirmed = payRows.some(r => r.select.value === "UPI" &&
      Number(r.input.value) > 0 && (!r.checked.checked || !r.reference));
    completeBtn.disabled = !result.valid || !result.covered || upiUnconfirmed ||
      (payments().length === 0 && creditApplied <= 0);
    const changeDue = result.valid ? result.change : 0;
    if (changeDue > 0) {
      changeEl.style.display = "";
      changeEl.replaceChildren(h("span", {}, "Change to give"),
        h("span", { class: "amt" }, fmt(changeDue)));
    } else {
      changeEl.style.display = "none";
    }
    renderQuickButtons(result.valid ? Math.max(0, result.remaining) : 0);
  }

  function renderQuickButtons(balance) {
    quickWrap.innerHTML = "";
    if (balance <= 0) return;
    const exact = h("button", { class: "chip active" }, "Exact");
    exact.onclick = () => {
      const last = lastCashRow();
      if (last) {
        last.input.value = ((parseFloat(last.input.value) || 0) + balance).toFixed(2);
        update();
      } else {
        addPayRow("CASH", balance.toFixed(2));
      }
    };
    quickWrap.append(h("div", { class: "quick-cash" }, exact,
      ...[10, 20, 50, 100, 200, 500, 2000].filter((n) => n >= balance).map((note) => {
        const b = h("button", { class: "chip" },
          `${state.settings.currency || "₹"}${note}`);
        b.onclick = () => {
          const last = lastCashRow();
          if (last) {
            last.input.value =
              (Math.ceil((parseFloat(last.input.value) || 0) / note) * note).toFixed(2);
            update();
          } else {
            addPayRow("CASH", note.toFixed(2));
          }
        };
        return b;
      })));
  }

  const supplySelect = h("select", { class: "input" },
    h("option", { value: "intra" }, "Within same state (CGST + SGST/UTGST)"),
    h("option", { value: "inter" }, "Inter-state (IGST)"));
  const posInput = h("input", { class: "input", placeholder: "e.g. Maharashtra (27)",
    value: "" });
  const posField = h("div", { class: "field", style: "display:none" },
    h("label", {}, "Place of supply (State/UT) *"), posInput);
  supplySelect.addEventListener("change", () => {
    posField.style.display = supplySelect.value === "inter" ? "" : "none";
  });
  const supplyBox = h("div", { class: "field" },
    h("label", {}, "GST supply type"), supplySelect,
    h("div", { class: "form-hint" },
      "Use the correct place-of-supply classification for this sale. Verify exceptions separately."));

  const backBtn = h("button", { class: "btn" }, "Back");
  const completeBtn = h("button", {
    class: "btn primary lg",
    onclick: () => finishSale(),
  }, icon("i-check"), "Complete sale");

  // A stable key survives a timeout/retry of the same checkout dialog.
  const checkoutRequestId = (globalThis.crypto?.randomUUID?.() ||
    `ck_${Date.now()}_${Math.random().toString(36).slice(2)}`).replace(/-/g, "_");
  let modal;
  async function finishSale() {
    if (payRows.some(r => r.select.value === "UPI" && Number(r.input.value) > 0 &&
      (!r.reference || !r.checked.checked))) {
      toast("Confirm each UPI payment in the merchant app before completing this sale.", "warn");
      return;
    }
    completeBtn.disabled = true;
    completeBtn.replaceChildren(icon("i-check"), " Saving…");
    try {
      const payload = {
        request_id: checkoutRequestId,
        items: [...cart.entries()].map(([product_id, quantity]) =>
          ({ product_id, quantity })),
        payments: payRows
          .map((r) => ({
            method: r.select.value,
            amount: parseFloat(r.input.value) || 0,
            reference: r.select.value === "UPI" ? r.reference : "",
            upi_utr: r.select.value === "UPI" ? r.utrInput.value.trim() : "",
          }))
          .filter((p) => p.amount > 0),
        discount: t.discount > 0
          ? { mode: discountMode, value: discountValue }
          : null,
        customer_id: customer ? customer.id : null,
        salesperson,
        use_credit: creditApplied > 0,
        supply_type: supplySelect.value,
        place_of_supply: posInput.value.trim(),
      };

      const receipt = await api.checkout(payload);

      cart.clear();
      discountValue = 0;
      selectedLineId = null;
      salesperson = "";
      if (customer) {
        try { customer = await api.getCustomer(customer.id); } catch (_) {}
      } else {
        customer = null;
      }
      saveCart();
      renderCart();
      await refreshCatalog();
      if (cashStripEl?.isConnected) await renderCashStatusStrip(cashStripEl);
      modal.close();
      showReceiptModal(receipt);
    } catch (e) {
      toastError(e, "Sale failed");
      completeBtn.disabled = false;
      completeBtn.replaceChildren(icon("i-check"), "Complete sale");
    }
  }

  // Safe now that update()'s dependencies (incl. completeBtn) exist.
  addPayRow("CASH", dueTotal.toFixed(2));

  const bannerAmtEl = h("span", { class: "amt" }, fmt(dueTotal));
  bannerAmt = bannerAmtEl;

  backBtn.addEventListener("click", () => modal.close());
  modal = openModal({
    title: "Take payment",
    body: h("div", {},
      h("div", { class: "pay-total-banner" },
        h("span", { class: "lbl" }, "Amount due"),
        bannerAmtEl),
      creditToggle,
      supplyBox,
      posField,
      rowsWrap,
      h("button", {
        class: "btn sm",
        onclick: () => addPayRow(),
      }, icon("i-plus"), "Split payment"),
      h("div", { style: "margin-top:10px" }, remainingEl),
      quickWrap,
      changeEl),
    footer: [backBtn, completeBtn],
  });
}

export function showReceiptModal(receipt) {
  const receiptEl = buildReceipt(receipt);
  const m = openModal({
    title: receipt.status === "REFUNDED"
      ? `Refund #${receipt.id}`
      : receipt.invoice_number ? `Invoice ${receipt.invoice_number} complete` :
        `Sale #${String(receipt.id).padStart(5, "0")} complete`,
    narrow: true,
    body: receiptEl,
    footer: [
      h("button", {
        class: "btn",
        onclick: () => printReceipt(receiptEl),
      }, icon("i-printer"), "Print"),
      h("button", { class: "btn primary", onclick: () => m.close() }, "Done"),
    ],
  });
}

// ================= keyboard helpers =================

function wireGlobalKeys() {
  document.addEventListener("keydown", registerKeys);
  window.addEventListener("pos:pay", onPayEvent);
  window.addEventListener("pos:park", onParkEvent);
}

function onPayEvent() {
  if (!rootEl || !rootEl.isConnected) {
    window.removeEventListener("pos:pay", onPayEvent);
    return;
  }
  openPayment();
}

function onParkEvent() {
  if (!rootEl || !rootEl.isConnected) {
    window.removeEventListener("pos:park", onParkEvent);
    return;
  }
  parkFlow();
}

function registerKeys(e) {
  // Only active while this view is mounted.
  if (!rootEl || !rootEl.isConnected) {
    document.removeEventListener("keydown", registerKeys);
    return;
  }
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

  if ((e.key === "+" || e.key === "=") && selectedLineId && cart.has(selectedLineId)) {
    e.preventDefault();
    changeQty(selectedLineId, +1);
  } else if ((e.key === "-" || e.key === "_") && selectedLineId && cart.has(selectedLineId)) {
    e.preventDefault();
    changeQty(selectedLineId, -1);
  }
}
