import { api, fmt, state, computeTotals } from "../api.js";
import { h, icon, openModal, toast, toastError, printReceipt } from "../ui.js";
import { buildReceipt, methodLabel } from "../receipt.js";

let products = [];
let groups = [];
let activeGroup = null;
let searchTerm = "";
let cart = new Map();
let discountMode = "percent";
let discountValue = 0;
let selectedLineId = null;
let customer = null;
let useCredit = true;

let rootEl = null;
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
  [products, groups] = await Promise.all([api.listProducts(), api.listGroups()]);
  await restoreCart();

  main.innerHTML = "";
  rootEl = h("div", { class: "register-root" });
  main.append(rootEl);

  const threshold = Number(state.settings.low_stock_threshold || 5);
  const lowCount = products.filter((p) => p.quantity <= threshold).length;

  rootEl.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Register"),
        h("div", { class: "sub" },
          lowCount > 0 ? `${lowCount} product${lowCount > 1 ? "s" : ""} running low on stock` : "All stocked up"))),
    );

  const grid = h("div", { class: "register-grid" });

  // ---- left pane: catalog ----
  const searchInput = h("input", {
    id: "reg-search",
    class: "input",
    placeholder: "Scan barcode or search name / SKU…",
    autocomplete: "off",
    oninput: () => {
      searchTerm = searchInput.value.trim();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderTiles, 160);
    },
    onkeydown: (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleScan(searchInput.value.trim());
      } else if (e.key === "Escape") {
        searchInput.value = "";
        searchTerm = "";
        renderTiles();
      }
    },
  });
  searchInputEl = searchInput;

  chipsEl = h("div", { class: "chips" });
  tilesEl = h("div", { class: "tile-grid" });

  grid.append(
    h("div", { class: "catalog-pane" },
      h("div", { class: "search-wrap" }, icon("i-search"), searchInput),
      chipsEl,
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

  parkedChipEl = h("button", { class: "chip", onclick: openParkedModal }, "Parked");
  rootEl.querySelector(".view-head").append(parkedChipEl);

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

function renderChips() {
  chipsEl.innerHTML = "";
  const mk = (label, value) =>
    h("button", {
      class: `chip${activeGroup === value ? " active" : ""}`,
      onclick: () => { activeGroup = value; renderChips(); renderTiles(); },
    }, label);

  chipsEl.append(mk(`All (${products.length})`, null));
  for (const g of groups) {
    const count = products.filter((p) => p.group_id === g.id).length;
    if (count === 0 && !activeGroup) continue;
    chipsEl.append(mk(g.name, g.id));
  }
}

function variantText(p) {
  return p.attributes.map((a) => a.value).join(" · ");
}

function stockBadge(p) {
  const threshold = Number(state.settings.low_stock_threshold || 5);
  if (p.quantity <= 0) return h("span", { class: "stock-badge out" }, "Out");
  if (p.quantity <= threshold) return h("span", { class: "stock-badge low" }, `${p.quantity} left`);
  return h("span", { class: "stock-badge" }, `${p.quantity}`);
}

function renderTiles() {
  tilesEl.innerHTML = "";

  let list = products;
  if (activeGroup !== null) list = list.filter((p) => p.group_id === activeGroup);
  if (searchTerm) {
    const q = searchTerm.toLowerCase();
    list = list.filter((p) =>
      (p.group_name || "").toLowerCase().includes(q) ||
      (p.SKU || "").toLowerCase().includes(q) ||
      (p.barcode || "").toLowerCase().includes(q) ||
      p.attributes.some((a) =>
        a.name.toLowerCase().includes(q) || a.value.toLowerCase().includes(q)));
  }

  if (list.length === 0) {
    tilesEl.append(h("div", { class: "empty-state", style: "grid-column:1/-1" },
      icon("i-catalog"),
      h("h3", {}, products.length ? "Nothing matches that search" : "No products yet"),
      h("p", {}, products.length
        ? "Try a different term, or scan a barcode."
        : "Add products in the Catalog to start selling."),
      products.length
        ? null
        : h("a", { href: "#/catalog", style: "color:var(--accent);font-weight:650" },
            "Go to Catalog →")));
    return;
  }

  for (const p of list) {
    const vt = variantText(p);
    tilesEl.append(
      h("button", {
        class: "tile",
        disabled: p.quantity <= 0,
        onclick: () => addToCart(p),
      },
        h("span", { class: "t-name" }, p.group_name || p.SKU),
        vt ? h("span", { class: "t-variant" }, vt) : null,
        h("span", { class: "t-foot" },
          h("span", { class: "t-price" }, fmt(p.price)),
          stockBadge(p))),
    );
  }
}

async function handleScan(code) {
  if (!code) return;
  try {
    const p = await api.lookupCode(code);
    addToCart(products.find((x) => x.id === p.id) || normalizeFetched(p));
    searchInputEl.value = "";
    searchTerm = "";
    renderTiles();
  } catch (e) {
    if (e.status === 404) {
      // Fall back to adding the single visible tile (quick keyboard flow).
      const visible = tilesEl.querySelectorAll(".tile:not([disabled])");
      if (visible.length === 1) {
        visible[0].click();
        searchInputEl.value = "";
        searchTerm = "";
        renderTiles();
      } else {
        toast(`No exact match for “${code}”.`, "warn");
      }
    } else {
      toastError(e);
    }
  }
}

function normalizeFetched(raw) {
  raw.attributes = raw.attributes || [];
  const existing = products.find((p) => p.id === raw.id);
  if (existing) Object.assign(existing, raw);
  else products.push(raw);
  renderChips();
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
  if (cart.size === 0 && !customer) return;
  cart.clear();
  discountValue = 0;
  selectedLineId = null;
  customer = null;
  useCredit = true;
  saveCart();
  renderCart();
}

function cartTotals() {
  const lines = [...cart.entries()].map(([id, qty]) => {
    const p = byId(id);
    return { price: p.price, tax_rate: p.tax_rate, quantity: qty };
  });
  return computeTotals(lines, discountValue > 0 ? discountMode : null, discountValue);
}

function renderCart() {
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
          h("div", { class: "cb-sub" }, "Tap to change customer")),
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
    h("div", { class: "sum-row discount-edit" },
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
        h("option", { value: "amount", selected: discountMode === "amount" }, "$"))),
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
          [c.phone, c.store_credit > 0 ? `${fmt(c.store_credit)} credit` : null]
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
  okBtn.addEventListener("click", async () => {
    try {
      await api.parkCart({
        items: [...cart.entries()].map(([product_id, quantity]) =>
          ({ product_id, quantity })),
        label: labelInput.value.trim(),
        discount: discountValue > 0
          ? { mode: discountMode, value: discountValue } : null,
        customer_id: customer ? customer.id : null,
      });
      cart.clear();
      discountValue = 0;
      selectedLineId = null;
      customer = null;
      saveCart();
      renderCart();
      refreshParkedCount();
      m.close();
      toast("Sale parked — pick it up any time from Parked.", "success");
    } catch (e) {
      toastError(e);
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
  [products, groups] = await Promise.all([api.listProducts(), api.listGroups()]);
  renderChips();
  renderTiles();
}

function openPayment() {
  if (cart.size === 0) return;
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
      oninput: () => update(),
    });
    const row = { select, input };
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
      }, icon("i-x")));
    select.addEventListener("change", update);
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

  const coveredTotal = () =>
    payments().reduce((a, p) => a + p.amount, 0);
  const cashTendered = () =>
    payments().filter((p) => p.method === "CASH").reduce((a, p) => a + p.amount, 0);

  function lastCashRow() {
    return [...payRows].reverse().find((r) => r.select.value === "CASH");
  }

  function update() {
    const paid = coveredTotal();
    const remaining = Math.round((due - paid) * 100) / 100;
    if (remaining > 0.00001) {
      remainingEl.textContent = `Remaining due: ${fmt(remaining)}`;
      remainingEl.className = "pay-remaining uncovered";
    } else {
      remainingEl.textContent = "Fully covered ✓";
      remainingEl.className = "pay-remaining covered";
    }
    completeBtn.disabled = remaining > 0.00001 || (paid <= 0 && creditApplied <= 0);

    const changeDue = Math.round((cashTendered() - due) * 100) / 100;
    if (changeDue > 0) {
      changeEl.style.display = "";
      changeEl.innerHTML =
        `<span>Change to give</span><span class="amt">${fmt(changeDue)}</span>`;
    } else {
      changeEl.style.display = "none";
    }

    renderQuickButtons(Math.max(0, remaining));
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
      ...[5, 10, 20, 50, 100].filter((n) => n >= balance).map((note) => {
        const b = h("button", { class: "chip" },
          `${state.settings.currency || "$"}${note}`);
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

  const backBtn = h("button", { class: "btn" }, "Back");
  const completeBtn = h("button", {
    class: "btn primary lg",
    onclick: () => finishSale(),
  }, icon("i-check"), "Complete sale");

  let modal;
  async function finishSale() {
    completeBtn.disabled = true;
    completeBtn.replaceChildren(icon("i-check"), " Saving…");
    try {
      const payload = {
        items: [...cart.entries()].map(([product_id, quantity]) =>
          ({ product_id, quantity })),
        payments: payRows
          .map((r) => ({
            method: r.select.value,
            amount: parseFloat(r.input.value) || 0,
          }))
          .filter((p) => p.amount > 0),
        discount: t.discount > 0
          ? { mode: discountMode, value: discountValue }
          : null,
        customer_id: customer ? customer.id : null,
        use_credit: creditApplied > 0,
      };

      const receipt = await api.checkout(payload);

      cart.clear();
      discountValue = 0;
      selectedLineId = null;
      if (customer) {
        try { customer = await api.getCustomer(customer.id); } catch (_) {}
      } else {
        customer = null;
      }
      saveCart();
      renderCart();
      await refreshCatalog();
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
      : `Sale #${String(receipt.id).padStart(5, "0")} complete`,
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
