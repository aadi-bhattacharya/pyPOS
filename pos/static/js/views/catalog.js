import { api, fmt, state } from "../api.js";
import { h, icon, openModal, toast, toastError, confirmDialog } from "../ui.js";

let products = [];
let groups = [];
let searchTerm = "";
let filterGroup = null;
let groupsListEl = null;
let tableWrapEl = null;

export async function renderCatalog(main) {
  await load();
  searchTerm = "";
  filterGroup = null;

  main.innerHTML = "";

  const searchInput = h("input", {
    class: "input",
    placeholder: "Search SKU / barcode / name…",
    oninput: () => { searchTerm = searchInput.value.trim(); renderTable(); },
  });

  groupsListEl = h("div", { style: "display:flex;flex-direction:column;gap:2px" });
  tableWrapEl = h("div", {});

  const grid = h("div", {
    style: "display:grid;grid-template-columns:minmax(200px,250px) 1fr;gap:16px;align-items:start",
  },
    h("div", {},
      h("div", { class: "card" },
        h("div", { class: "panel-head" }, h("h3", {}, "Groups")),
        h("div", { style: "padding:8px" },
          h("button", {
            class: "btn sm block", style: "margin-bottom:8px",
            onclick: newGroupModal,
          }, icon("i-plus"), "New group"),
          groupsListEl))),
    h("div", {},
      h("div", { class: "search-wrap", style: "margin-bottom:12px;max-width:340px" },
        icon("i-search"), searchInput),
      tableWrapEl));

  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Catalog"),
        h("div", { class: "sub", id: "cat-sub" })),
      h("div", { class: "view-actions" },
        h("a", {
          class: "btn",
          href: "/api/export/inventory.csv",
          title: "Download inventory as CSV (opens in Excel/Sheets)",
        }, icon("i-sales"), "Export CSV"),
        h("button", {
          class: "btn primary",
          onclick: () => productModal(),
        }, icon("i-plus"), "Add product"))),
    grid,
  );

  renderSub();
  renderGroups();
  renderTable();
}

async function load() {
  [products, groups] = await Promise.all([api.listProducts(), api.listGroups()]);
}

function rerenderAll() {
  renderSub();
  renderGroups();
  renderTable();
}

function renderSub() {
  const el = document.getElementById("cat-sub");
  if (el) {
    el.textContent =
      `${products.length} product${products.length === 1 ? "" : "s"} across ${groups.length} group${groups.length === 1 ? "" : "s"}`;
  }
}

// ================= groups =================

function renderGroups() {
  groupsListEl.innerHTML = "";
  const row = (label, value, count) =>
    h("div", {
      role: "button",
      tabindex: "0",
      style: `display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-radius:9px;cursor:pointer;
              ${filterGroup === value ? "background:var(--accent-soft);color:var(--accent);font-weight:650" : ""}`,
      onclick: () => { filterGroup = value; renderGroups(); renderTable(); },
    },
      h("span", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, label),
      h("span", { style: "color:var(--muted);font-size:12px;font-weight:600" }, String(count)));

  groupsListEl.append(row(`All`, null, products.length));
  for (const g of groups) {
    const count = products.filter((p) => p.group_id === g.id).length;
    groupsListEl.append(
      h("div", { style: "display:flex;align-items:center;gap:2px" },
        h("div", { style: "flex:1;min-width:0" }, row(g.name, g.id, count)),
        h("button", {
          class: "btn ghost icon-only sm",
          title: `Edit ${g.name}`,
          onclick: () => editGroupModal(g),
        }, icon("i-edit"))));
  }
}

function newGroupModal() {
  const input = h("input", { class: "input", placeholder: "e.g. Beverages" });
  let m;
  m = openModal({
    title: "New product group",
    narrow: true,
    body: h("div", { class: "field" }, h("label", {}, "Group name"), input),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      h("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api.createGroup(input.value);
            toast("Group created.", "success");
            m.close();
            await load(); rerenderAll();
          } catch (e) { toastError(e); }
        },
      }, "Create"),
    ],
  });
}

function editGroupModal(g) {
  const input = h("input", { class: "input", value: g.name });
  let m;
  const saveRename = async () => {
    try {
      await api.renameGroup(g.id, input.value);
      toast("Group renamed.", "success");
      m.close();
      await load(); rerenderAll();
    } catch (e) { toastError(e); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveRename(); });

  m = openModal({
    title: `Edit “${g.name}”`,
    narrow: true,
    body: h("div", {},
      h("div", { class: "field" }, h("label", {}, "Name"), input),
      h("p", { class: "form-hint" },
        `${g.product_count} product(s) · ${g.total_stock} units in stock.`)),
    footer: [
      h("button", {
        class: "btn subtle-danger",
        onclick: async () => {
          try {
            await api.deleteGroup(g.id);
            toast("Group deleted.", "success");
            m.close();
            await load(); rerenderAll();
          } catch (e) { toastError(e); }
        },
      }, icon("i-trash"), "Delete"),
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: saveRename }, "Save"),
    ],
  });
}

// ================= products table =================

function renderTable() {
  let list = products;
  if (filterGroup !== null) list = list.filter((p) => p.group_id === filterGroup);
  if (searchTerm) {
    const q = searchTerm.toLowerCase();
    list = list.filter((p) =>
      (p.SKU || "").toLowerCase().includes(q) ||
      (p.barcode || "").toLowerCase().includes(q) ||
      (p.group_name || "").toLowerCase().includes(q));
  }

  tableWrapEl.innerHTML = "";

  if (!list.length) {
    tableWrapEl.append(h("div", { class: "empty-state card" },
      icon("i-catalog"),
      h("h3", {}, products.length ? "No matches" : "Your catalog is empty"),
      h("p", {}, products.length
        ? "Try adjusting the filters."
        : "Create a group and add your first product — or load sample data from Settings."),
      products.length ? null
        : h("button", {
            class: "btn primary",
            onclick: () => productModal(),
          }, icon("i-plus"), "Add product")));
    return;
  }

  const tbody = h("tbody", {}, list.map((p) =>
    h("tr", {},
      h("td", {},
        h("div", { class: "cell-main" }, p.group_name || "—"),
        p.attributes && p.attributes.length
          ? h("div", { class: "cell-sub" },
              p.attributes.map((a) => `${a.name}: ${a.value}`).join(", "))
          : null),
      h("td", {},
        h("div", { class: "cell-main" }, p.SKU),
        p.barcode ? h("div", { class: "cell-sub" }, p.barcode) : null),
      h("td", { class: "num" }, fmt(p.price)),
      h("td", { class: "num cell-sub" },
        Number(p.cost_price) > 0 ? fmt(p.cost_price) : "—"),
      h("td", { class: "num" }, trimRate(p.tax_rate) + "%"),
      h("td", {}, stockStepper(p)),
      h("td", { class: "actions-cell" },
        h("button", { class: "btn ghost icon-only sm", title: "Adjust stock",
          onclick: () => stockModal(p) }, icon("i-plus")),
        h("button", { class: "btn ghost icon-only sm", title: "Edit",
          onclick: () => productModal(p) }, icon("i-edit")),
        h("button", { class: "btn ghost icon-only sm", title: "Delete",
          onclick: () => deleteProduct(p) }, icon("i-trash"))))));

  tableWrapEl.append(h("div", { class: "table-wrap" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Product"), h("th", {}, "SKU / Barcode"),
        h("th", { class: "num" }, "Price"), h("th", { class: "num" }, "Cost"),
        h("th", { class: "num" }, "Tax %"), h("th", {}, "Stock"), h("th", {}))),
      tbody)));
}

async function deleteProduct(p) {
  const ok = await confirmDialog({
    title: "Delete product?",
    message: `${p.group_name || p.SKU} (${p.SKU}) will be removed permanently. ` +
      "Note: products with sales history cannot be deleted.",
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.deleteProduct(p.id);
    toast("Product deleted.", "success");
    await load(); rerenderAll();
  } catch (e) {
    toastError(e);
  }
}

// ================= stock modal =================

function stockModal(p) {
  const qtyInput = h("input", { class: "input", type: "number", value: "+1", step: "1" });
  const preview = h("div", { class: "form-hint", style: "font-weight:650;margin-top:4px" });
  let m;

  const updatePreview = () => {
    const delta = parseInt(qtyInput.value, 10) || 0;
    preview.textContent = `Preview: ${p.quantity} → ${Math.max(0, p.quantity + delta)}`;
  };
  qtyInput.addEventListener("input", updatePreview);

  const chip = (n) => h("button", {
    class: "chip",
    onclick: () => { qtyInput.value = String(n); updatePreview(); },
  }, (n > 0 ? "+" : "") + n);

  m = openModal({
    title: `Adjust stock — ${p.group_name || p.SKU}`,
    narrow: true,
    body: h("div", {},
      h("p", { style: "color:var(--text-2);margin-bottom:12px" },
        "Current stock: ", h("strong", {}, String(p.quantity))),
      h("div", { class: "field" },
        h("label", {}, "Change by (+ adds / − removes)"),
        qtyInput, preview),
      h("div", { class: "chips", style: "margin-top:8px" },
        chip(-10), chip(-5), chip(-1), chip(1), chip(5), chip(10))),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      h("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            const delta = parseInt(qtyInput.value, 10);
            if (!delta) { toast("Enter a non-zero change.", "warn"); return; }
            const r = await api.adjustStock(p.id, delta);
            toast(`Stock updated to ${r.quantity}.`, "success");
            m.close();
            await load(); rerenderAll();
          } catch (e) { toastError(e); }
        },
      }, "Apply"),
    ],
  });
  updatePreview();
}

// ================= product create/edit modal =================

function productModal(existing = null) {
  const isEdit = !!existing;

  const groupSel = h("select", { class: "input" },
    h("option", { value: "" }, "— no group —"),
    ...groups.map((g) =>
      h("option", {
        value: String(g.id),
        selected: existing && existing.group_id === g.id,
      }, g.name)));

  const fSKU = fieldInput("SKU *", existing?.SKU || "");
  const fBarcode = fieldInput("Barcode", existing?.barcode || "");
  const fPrice = fieldInput("Price *", existing != null ? String(existing.price) : "",
    "number", "0.01");
  const fCost = fieldInput("Cost",
    existing?.cost_price ? String(existing.cost_price) : "", "number", "0.01");
  const fTax = fieldInput("Tax %", existing != null ? trimRate(existing.tax_rate) : "5",
    "number", "0.01");
  const fQty = fieldInput("Opening stock", "0", "number", "1");

  const attrRows = [];
  const attrWrap = h("div", { class: "attr-editor" });
  const addAttrRow = (name = "", value = "") => {
    const nameIn = h("input", { class: "input", placeholder: "Name (e.g. Size)", value: name });
    const valIn = h("input", { class: "input", placeholder: "Value (e.g. M)", value });
    const pair = { nameIn, valIn };
    const row = h("div", { class: "attr-row" }, nameIn, valIn,
      h("button", {
        class: "btn ghost icon-only", title: "Remove attribute",
        onclick: () => {
          attrRows.splice(attrRows.indexOf(pair), 1);
          row.remove();
        },
      }, icon("i-x")));
    attrRows.push(pair);
    attrWrap.append(row);
  };
  if (existing) {
    for (const a of existing.attributes || []) addAttrRow(a.name, a.value);
  }
  addAttrRow();

  let m;
  m = openModal({
    title: isEdit ? `Edit — ${existing.group_name || existing.SKU}` : "New product",
    wide: true,
    body: h("div", {},
      h("div", { class: "field" }, h("label", {}, "Group"), groupSel),
      h("div", { class: "form-row" }, fSKU.wrap, fBarcode.wrap),
      h("div", { class: "form-row" }, fPrice.wrap, fCost.wrap, fTax.wrap),
      isEdit
        ? h("p", { class: "form-hint", style: "margin-bottom:13px" },
            `Stock (${existing.quantity}) is adjusted from the catalog table.`)
        : fQty.wrap,
      h("div", { class: "field", style: "margin-bottom:4px" },
        h("label", {}, "Variant attributes (optional)"),
        attrWrap,
        h("button", {
          class: "btn sm", style: "align-self:flex-start;margin-top:6px",
          onclick: () => addAttrRow(),
        }, icon("i-plus"), "Add attribute")),
      ),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: save }, isEdit ? "Save changes" : "Create"),
    ],
  });

  async function save() {
    const payload = {
      SKU: fSKU.input.value.trim(),
      barcode: fBarcode.input.value.trim() || null,
      price: parseFloat(fPrice.input.value),
      cost_price: parseFloat(fCost.input.value) || 0,
      tax_rate: parseFloat(fTax.input.value) || 0,
      group_id: groupSel.value ? Number(groupSel.value) : null,
    };

    // inline validation — mark fields in place instead of only toasting
    [fSKU, fBarcode, fPrice, fCost, fTax, fQty].forEach((f) => setFieldError(f, null));
    let bad = null;
    if (!payload.SKU) bad = setFieldError(fSKU, "SKU is required");
    if (isNaN(payload.price) || payload.price < 0)
      bad = setFieldError(fPrice, "Enter a valid price");
    if (isNaN(payload.tax_rate) || payload.tax_rate < 0)
      bad = setFieldError(fTax, "Tax must be 0 or more");
    if (!isEdit) {
      const q = parseInt(fQty.input.value, 10);
      if (isNaN(q) || q < 0) bad = setFieldError(fQty, "Stock can't be negative");
    }
    if (bad) return;

    try {
      let pid;
      if (isEdit) {
        pid = existing.id;
        await api.updateProduct(pid, payload);
      } else {
        payload.quantity = parseInt(fQty.input.value, 10) || 0;
        const created = await api.createProduct(payload);
        pid = created.id;
      }

      const desired = {};
      for (const { nameIn, valIn } of attrRows) {
        const n = nameIn.value.trim(), v = valIn.value.trim();
        if (n && v) desired[n] = v;
      }
      const current = isEdit
        ? Object.fromEntries((existing.attributes || []).map((a) => [a.name, a.value]))
        : {};
      for (const [n, v] of Object.entries(desired)) {
        if (current[n] !== v) await api.setAttribute(pid, n, v);
      }
      for (const n of Object.keys(current)) {
        if (!(n in desired)) await api.removeAttribute(pid, n);
      }

      toast(isEdit ? "Product updated." : "Product created.", "success");
      m.close();
      await load(); rerenderAll();
    } catch (e) {
      toastError(e);
    }
  }
}

// ================= shared bits =================

function setFieldError(f, msg) {
  if (!f || !f.wrap) return true;
  let err = f.wrap.querySelector(".field-error");
  if (!msg) {
    f.wrap.classList.remove("invalid");
    if (err) err.remove();
    return false;
  }
  f.wrap.classList.add("invalid");
  if (!err) {
    err = h("div", { class: "field-error" });
    f.wrap.append(err);
  }
  err.textContent = msg;
  return true;
}

function fieldInput(labelText, value = "", type = "text", step = undefined) {
  const input = h("input", { class: "input", type, value, step });
  return { wrap: h("div", { class: "field" }, h("label", {}, labelText), input), input };
}

function stockBadge(qty) {
  const threshold = Number(state.settings.low_stock_threshold || 5);
  if (qty <= 0) return h("span", { class: "stock-badge out" }, "Out");
  if (qty <= threshold) return h("span", { class: "stock-badge low" }, `${qty}`);
  return h("span", { class: "stock-badge" }, String(qty));
}

// Inline ^/v stepper on the stock cell — one click to receive or remove a unit.
function stockStepper(p) {
  const threshold = Number(state.settings.low_stock_threshold || 5);
  const badge = h("span", { class: "stock-badge" }, String(p.quantity));
  const down = h("button", {
    class: "spin-btn", title: "Remove 1", disabled: p.quantity <= 0,
    onclick: () => bump(-1),
  }, icon("i-chev-down"));
  const up = h("button", {
    class: "spin-btn", title: "Receive 1",
    onclick: () => bump(+1),
  }, icon("i-chev-up"));

  let busy = false;
  async function bump(delta) {
    if (busy) return;
    busy = true;
    up.disabled = down.disabled = true;
    try {
      const r = await api.adjustStock(p.id, delta);
      p.quantity = r.quantity;              // keep tiles/sidebar in sync
      badge.textContent = String(r.quantity);
      badge.className = "stock-badge" +
        (r.quantity <= 0 ? " out" : r.quantity <= threshold ? " low" : "");
      down.disabled = r.quantity <= 0;
      renderGroups();
    } catch (e) {
      toastError(e);
    } finally {
      busy = false;
      up.disabled = false;
    }
  }

  return h("div", { class: "stock-spin" },
    badge,
    h("div", { class: "spin-col" }, up, down));
}

function trimRate(rate) {
  return String(Number(Number(rate || 0).toFixed(2)));
}
