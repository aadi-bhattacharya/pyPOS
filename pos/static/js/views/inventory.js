import { api, fmt, state } from "../api.js";
import { h, icon, openModal, toast, toastError, confirmDialog } from "../ui.js";
import { publicVariant, billingReviewLabel } from "../productLabels.js";
import { printProductLabels } from "../labels.js";

let products = [];
let groups = [];
let brands = [];
let categories = [];
let overview = {};
let query = "";
let filter = "all";
let page = 0;
const PAGE_SIZE = 60;
let mainEl;
let tableEl;
let totalsEl;

export async function renderInventory(main) {
  mainEl = main;
  main.replaceChildren(h("div", { class: "view-head" },
    h("div", { class: "view-title" }, h("h2", {}, "Inventory"),
      h("div", { class: "sub" }, "SKU-level stock, movements and classification"))));
  try { await reload(); } catch (err) { toastError(err); throw err; }
  totalsEl = h("div", { class: "inventory-metrics" });
  tableEl = h("div", {});
  const search = h("input", { class: "input", placeholder: "Find product, variant, SKU or barcode…",
    oninput: e => { query = e.target.value.trim().toLocaleLowerCase("en-IN"); page = 0; paint(); },
  });
  const select = h("select", { class: "input", onchange: e => {
    filter = e.target.value; page = 0; paint();
  } },
    ...[["all", "All SKUs"], ["low", "At/below reorder point"], ["out", "Out of stock"],
      ...(state.staff?.operator?.role === "cashier" ? [] : [["uncosted", "Missing purchase cost"]])]
      .map(([value, name]) => h("option", { value }, name)));
  main.append(totalsEl,
    h("div", { class: "inventory-toolbar" },
      h("div", { class: "search-wrap" }, icon("i-search"), search),
      select,
      h("button", { class: "btn", onclick: showMovements }, "Stock history"),
      state.staff?.operator?.role === "cashier" ? null :
        h("a", { class: "btn", href: "#/stocktake" }, "Physical count"),
      state.staff?.operator?.role === "cashier" ? null :
        h("a", { class: "btn", href: "#/purchasing" }, "Supplier receipts"),
      state.staff?.operator?.role === "cashier" ? null :
        h("a", { class: "btn", href: "/api/export/inventory.csv" }, "Export CSV"),
      h("a", { class: "btn", href: "#/catalog" }, "Edit catalogue / Import")),
    h("p", { class: "form-hint inventory-intro" },
      "Counts are SKU-level. Stock changes are recorded; historical balances before migration are not reconstructed. Import stock in Catalogue."),
    tableEl);
  paint();
}

async function reload() {
  [products, groups, brands, categories, overview] = await Promise.all([
    api.listProducts(), api.listGroups(), api.listBrands(),
    api.listCategories(), api.inventoryOverview(),
  ]);
}

function threshold(p) {
  return p.reorder_point === null || p.reorder_point === undefined
    ? Number(state.settings.low_stock_threshold || 0) : Number(p.reorder_point);
}

function paint() {
  totalsEl.replaceChildren();
  for (const [name, value] of [
    ["SKUs", overview.sku_count], ["Units on hand", overview.units_on_hand],
    ["At reorder point", overview.low_stock], ["Out of stock", overview.out_of_stock],
  ]) {
    totalsEl.append(h("div", { class: "card inventory-metric" },
      h("div", { class: "form-hint" }, name),
      h("div", { class: "inventory-metric-value" }, Number(value || 0).toLocaleString("en-IN"))));
  }
  const rows = products.filter(p => {
    if (filter === "out" && p.quantity !== 0) return false;
    if (filter === "low" && !(p.quantity > 0 && p.quantity <= threshold(p))) return false;
    if (filter === "uncosted" && Number(p.cost_price) !== 0) return false;
    if (!query) return true;
    return [p.group_name, p.SKU, p.barcode, p.brand_name, p.category_name,
      ...(p.attributes || []).flatMap(a => [a.name, a.value])]
      .join(" ").toLocaleLowerCase("en-IN").includes(query);
  });
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  page = Math.min(page, totalPages - 1);
  const current = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const table = h("table", { class: "table inventory-table" },
    h("thead", {}, h("tr", {},
      ...["Style / variant", "SKU", "Stock", "Reorder", "Price", "Actions"]
        .map(label => h("th", {}, label)))),
    h("tbody", {}, ...current.map(p => {
      const isLow = p.quantity > 0 && p.quantity <= threshold(p);
      return h("tr", {},
        h("td", {}, h("div", { class: "cell-main" }, p.group_name || p.SKU),
          h("div", { class: "cell-sub" },
            [p.brand_name, p.category_name, publicVariant(p.attributes || [])]
              .filter(Boolean).join(" · ")),
          billingReviewLabel(p.attributes || [])
            ? h("div", { class: "cell-sub", style: "color:var(--danger);font-weight:650" },
                billingReviewLabel(p.attributes || [])) : null),
        h("td", { class: "mono" }, p.SKU),
        h("td", {}, h("span", {
          class: `stock-badge ${p.quantity === 0 ? "out" : isLow ? "low" : ""}`,
        }, `${p.quantity}`)),
        h("td", {}, p.reorder_point === null || p.reorder_point === undefined
          ? `${threshold(p)} (default)` : `${p.reorder_point}`),
        h("td", { class: "num" }, fmt(p.price)),
        h("td", {}, h("div", { class: "inventory-actions" },
          h("button", { class: "btn sm", onclick: () => stockModal(p) },
            state.staff?.operator?.role === "cashier" && state.settings.cashier_allow_stock_reduction !== "true"
              ? "Receive stock" : "Receive / Correct"),
          state.staff?.operator?.role === "cashier" ? null :
            h("button", { class: "btn sm", onclick: () => reorderModal(p) }, "Reorder"),
          p.group_id && state.staff?.operator?.role !== "cashier" ?
            h("button", { class: "btn sm", onclick: () => classifyModal(p) }, "Classify") : null,
          h("button", { class: "btn ghost sm", onclick: () => showMovements(p) }, "History"),
          h("button", { class: "btn ghost sm", onclick: () => labelModal(p) }, "Label"))));
    })));
  tableEl.replaceChildren(
    h("div", { class: "inventory-table-head" }, `${rows.length.toLocaleString("en-IN")} SKU matches`,
      h("span", { class: "form-hint" }, `Page ${page + 1} of ${totalPages}`)),
    h("div", { class: "table-wrap" }, table),
    h("div", { class: "inventory-pagination" },
      h("button", { class: "btn sm", disabled: page === 0, onclick: () => { page--; paint(); } }, "Previous"),
      h("span", { class: "form-hint" }, `${page + 1} / ${totalPages}`),
      h("button", { class: "btn sm", disabled: page >= totalPages - 1,
        onclick: () => { page++; paint(); } }, "Next")));
}

async function refresh() {
  await reload();
  paint();
}

function stockModal(p) {
  const cashier = state.staff?.operator?.role === "cashier";
  const canReduce = !cashier || state.settings.cashier_allow_stock_reduction === "true";
  const type = h("select", { class: "input" },
    h("option", { value: "RECEIPT" }, "Receive delivery"),
    canReduce ? h("option", { value: "ADJUSTMENT" }, "Record documented shortage / damage") : null);
  const amount = h("input", { class: "input", type: "number", min: "1", max: "1000000", step: "1", value: "1" });
  const reason = h("input", { class: "input", maxlength: "300", placeholder: "Supplier invoice / reason" });
  let modal;
  modal = openModal({ title: `Update stock · ${p.SKU}`, narrow: true,
    body: h("div", {},
      h("p", {}, `Current stock: ${p.quantity} units`),
      h("div", { class: "field" }, h("label", {}, "Operation"), type),
      h("div", { class: "field" }, h("label", {}, "Units (positive whole number)"), amount),
      h("div", { class: "field" }, h("label", {}, "Reason / supplier document"), reason),
      h("p", { class: "form-hint" }, "Receiving adds stock. Only an authorised operator can document a shortage. This does not replace a full physical stocktake.")),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        const text = amount.value.trim();
        const units = /^\d+$/.test(text) ? Number(text) : NaN;
        const correction = type.value === "ADJUSTMENT";
        const delta = correction ? -units : units;
        if (!Number.isSafeInteger(units) || units <= 0 || units > 1_000_000 ||
            p.quantity + delta < 0 || (correction && !canReduce)) {
          toast("Enter a valid positive whole quantity.", "warn"); return;
        }
        if (reason.value.trim().length < (correction ? 8 : 4) || reason.value.trim().length > 200) {
          toast("Enter a reason or supplier reference (up to 200 characters).", "warn"); return;
        }
        if (correction && !await confirmDialog({ title: "Confirm stock shortage",
          message: `Record ${units} damaged/missing units of ${p.SKU}? This reduces stock from ${p.quantity} to ${p.quantity - units}. Reason: ${reason.value.trim()}`,
          confirmLabel: "Record shortage" })) return;
        try {
          await api.adjustStock(p.id, delta, reason.value.trim(), type.value);
          modal.close(); await refresh(); toast("Stock updated and recorded.", "success");
        } catch (err) { toastError(err); }
      } }, "Save movement")],
  });
}

function reorderModal(p) {
  const value = h("input", { class: "input", type: "number", min: "0", step: "1",
    placeholder: "Blank = default threshold", value: p.reorder_point ?? "" });
  let modal;
  modal = openModal({ title: `Reorder point · ${p.SKU}`, narrow: true,
    body: h("div", { class: "field" }, h("label", {}, "Alert when stock ≤ this number"), value,
      h("p", { class: "form-hint" }, "Leave blank to use the global Settings threshold. This value is always editable.")),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        if (value.value && (!/^\d+$/.test(value.value) || !Number.isSafeInteger(Number(value.value)))) {
          toast("Enter a non-negative whole number.", "warn"); return;
        }
        try { await api.updateProduct(p.id, { reorder_point: value.value === "" ? null : Number(value.value) });
          modal.close(); await refresh(); toast("Reorder point saved.", "success");
        } catch (err) { toastError(err); }
      } }, "Save")],
  });
}

function classifyModal(p) {
  const family = groups.find(g => g.id === p.group_id);
  if (!family) return;
  const createOption = (items, selected) => [h("option", { value: "" }, "Unclassified"),
    ...items.map(item => h("option", { value: item.id, selected: item.id === selected }, item.name))];
  const brandSel = h("select", { class: "input" }, ...createOption(brands, family.brand_id));
  const categorySel = h("select", { class: "input" }, ...createOption(categories, family.category_id));
  const addBrand = h("input", { class: "input", placeholder: "New brand (optional)" });
  const addCategory = h("input", { class: "input", placeholder: "New category (optional)" });
  let modal;
  modal = openModal({ title: `Classify style · ${family.name}`, narrow: true,
    body: h("div", {},
      h("p", { class: "form-hint" }, "Classification applies to every SKU in this style. Does not modify invoices, stock, or editable variant attributes."),
      h("div", { class: "field" }, h("label", {}, "Brand"), brandSel),
      h("div", { class: "field" }, h("label", {}, "Or create brand"), addBrand),
      h("div", { class: "field" }, h("label", {}, "Category"), categorySel),
      h("div", { class: "field" }, h("label", {}, "Or create category"), addCategory)),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        try {
          let brandId = brandSel.value ? Number(brandSel.value) : null;
          let categoryId = categorySel.value ? Number(categorySel.value) : null;
          if (addBrand.value.trim()) brandId = (await api.createBrand(addBrand.value.trim())).id;
          if (addCategory.value.trim()) categoryId = (await api.createCategory(addCategory.value.trim())).id;
          await api.classifyGroup(family.id, { brand_id: brandId, category_id: categoryId });
          modal.close(); await refresh(); toast("Style classification saved.", "success");
        } catch (err) { toastError(err); }
      } }, "Save classification")],
  });
}

async function showMovements(p = null) {
  let rows;
  try { rows = await api.stockMovements(p ? { product_id: p.id, limit: 100 } : { limit: 100 }); }
  catch (err) { toastError(err); return; }
  const tbody = h("tbody", {}, ...rows.map(row => h("tr", {},
    h("td", {}, row.recorded_at || ""), h("td", {}, row.sku_at_event),
    h("td", {}, row.kind),
    h("td", { class: "num" }, `${row.delta > 0 ? "+" : ""}${row.delta}`),
    h("td", { class: "num" }, row.balance_after),
    h("td", {}, row.reason || (row.reference_id ? `Sale #${row.reference_id}` : "—")))));
  openModal({ title: p ? `Stock history · ${p.SKU}` : "Recent stock movements", wide: true,
    body: h("div", {}, h("p", { class: "form-hint" }, "Most recent 100 entries; recorded timestamps are UTC."),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ...["UTC", "SKU", "Type", "Change", "Balance", "Reference / reason"]
          .map(t => h("th", {}, t)))), tbody))) });
}


function labelModal(p) {
  const count = h("input",{class:"input",type:"number",min:"1",max:"100",step:"1",value:"1"});
  let modal;
  modal = openModal({ title:`Print EAN label · ${p.SKU}`, narrow:true,
    body:h("div",{},h("p",{},`${p.group_name || p.SKU} · ${fmt(p.price)}`),
      h("p",{class:"form-hint"},`EAN: ${p.barcode || "not assigned"}. Each MRP edition receives its own price label. Check the physical item's MRP before labelling.`),
      h("div",{class:"field"},h("label",{},"Number of labels (1–100)"),count)),
    footer:[h("button",{class:"btn",onclick:()=>modal.close()},"Cancel"),
      h("button",{class:"btn primary",onclick:()=>{
        try {printProductLabels(p,count.value);} catch(e){toastError(e);}
      }},"Print labels")],
  });
}
