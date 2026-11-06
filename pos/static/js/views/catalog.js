import { api, fmt, state } from "../api.js";
import { h, icon, openModal, toast, toastError, confirmDialog } from "../ui.js";
import { publicVariant, needsBillingReview, billingReviewLabel } from "../productLabels.js";

let products = [];
let groups = [];
let searchTerm = "";
let reviewOnly = false;
let reviewBtn = null;
let filterGroup = null;
let catalogPage = 0;
const CATALOG_PAGE_SIZE = 100;
let groupsListEl = null;
let tableWrapEl = null;

export async function renderCatalog(main) {
  await load();
  searchTerm = "";
  reviewOnly = false;
  filterGroup = null;
  catalogPage = 0;

  main.innerHTML = "";

  const searchInput = h("input", {
    class: "input",
    placeholder: "Search SKU / barcode / name…",
    oninput: () => { searchTerm = searchInput.value.trim(); catalogPage = 0; renderTable(); },
  });

  groupsListEl = h("div", { style: "display:flex;flex-direction:column;gap:2px;max-height:65vh;overflow-y:auto" });
  tableWrapEl = h("div", {});
  reviewBtn = h("button", { class: "btn sm", onclick: () => {
    reviewOnly = !reviewOnly; catalogPage = 0; renderSub(); renderTable();
  } }, "Billing review");

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
      h("div", { style: "margin-bottom:10px" }, reviewBtn),
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
          class: "btn", onclick: importCatalogModal,
        }, icon("i-plus"), "Import stock"),
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
  const count = products.filter(p => needsBillingReview(p.attributes || [])).length;
  if (el) {
    el.textContent = `${products.length} SKU(s) across ${groups.length} groups · ${count} need billing review`;
  }
  if (reviewBtn) {
    reviewBtn.textContent = `Billing review (${count})${reviewOnly ? " · showing only" : ""}`;
    reviewBtn.className = `btn sm${reviewOnly ? " primary" : ""}`;
  }
}

// ================= additive CSV/XLSX import =================

function importCatalogModal() {
  const fileInput = h("input", { class: "input", type: "file", accept: ".xlsx,.csv" });
  const mode = h("select", { class: "input" },
    h("option", { value: "skip" }, "Create new products, skip existing SKUs (safest)"),
    h("option", { value: "add_stock" }, "Create new + add stock on existing SKUs"),
    h("option", { value: "merge" }, "Create new + add stock + update supplied fields/attributes"));
  const allowZero = h("input", { type: "checkbox" });
  const forceRepeat = h("input", { type: "checkbox" });
  const repeatReason = h("input", { class: "input", maxlength: "250",
    placeholder: "Why is this an additional delivery of identical stock?" });
  const previewArea = h("div", { style: "margin-top:14px" },
    h("p", { class: "form-hint" }, "Select a file and click Preview. No records change until you import."));
  const previewBtn = h("button", { class: "btn" }, "Preview import");
  const applyBtn = h("button", { class: "btn primary", disabled: true }, "Import now");
  let previewResult = null;
  let busy = false;
  let m;
  const invalidate = () => {
    previewResult = null;
    applyBtn.disabled = true;
    previewArea.replaceChildren(h("p", { class: "form-hint" },
      "Options changed. Preview again before importing."));
  };
  for (const ctl of [fileInput, mode, allowZero]) ctl.addEventListener("change", invalidate);
  const setBusy = (value) => {
    busy = value;
    previewBtn.disabled = value;
    applyBtn.disabled = value || !previewResult?.can_import;
  };
  previewBtn.addEventListener("click", async () => {
    if (busy) return;
    if (!fileInput.files?.length) { toast("Choose an XLSX or CSV first.", "warn"); return; }
    previewResult = null;
    setBusy(true);
    previewArea.replaceChildren(h("p", { class: "form-hint" }, "Checking rows and existing SKUs…"));
    try {
      const r = await api.previewCatalogImport(fileInput.files[0], mode.value, allowZero.checked);
      previewResult = r;
      const stat = (value, name) => h("div", { class: "card", style: "padding:12px;flex:1;min-width:110px" },
        h("div", { style: "font-size:22px;font-weight:750" }, String(value)),
        h("div", { class: "form-hint" }, name));
      const boxes = h("div", { style: "display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px" },
        stat(r.total_rows, "Valid rows"), stat(r.new, "New products"),
        stat(r.existing, "Matching SKUs"), stat(r.skipped, "Skipped"));
      const warnings = h("div", { style: "font-size:13px;line-height:1.65;margin-bottom:10px" },
        h("div", {}, `${r.missing_prices} new product(s) lack selling prices.`),
        h("div", {}, `${r.missing_gst} new product(s) lack GST rates (0 is a database placeholder, NOT a verified zero GST rate).`),
        h("div", {}, `${r.stock_to_add} units would be added to existing stock using this mode.`),
        h("div", {}, "All imported attributes remain editable under Catalog → Edit product."));
      const sampleRows = r.sample.map((x) => h("tr", {},
        h("td", {}, String(x.row)), h("td", {}, x.sku), h("td", {}, x.name),
        h("td", { class: "num" }, String(x.quantity)),
        h("td", { class: "num" }, x.price === null ? "Missing" : fmt(x.price)),
        h("td", {}, x.action)));
      const sample = h("div", { class: "table-wrap", style: "max-height:230px;overflow:auto" },
        h("table", { class: "table" },
          h("thead", {}, h("tr", {}, ...["Row", "SKU", "Product", "Qty", "Price", "Action"]
            .map((x) => h("th", {}, x)))), h("tbody", {}, ...sampleRows)));
      previewArea.replaceChildren(boxes, warnings);
      if (r.error_count) previewArea.append(h("div", {
        style: "color:var(--danger);margin:8px 0;white-space:pre-wrap;font-size:12px",
      }, `${r.error_count} error(s):\n${r.errors.join("\n")}`));
      if (!r.can_import && !r.error_count) previewArea.append(h("p", {
        style: "color:var(--danger);font-weight:650",
      }, "New products without prices are blocked. Fill the spreadsheet or explicitly allow ₹0 draft products."));
      previewArea.append(h("p", { class: "form-hint" }, "Sample rows (first 15):"), sample);
    } catch (e) {
      previewArea.replaceChildren();
      toastError(e, "Import preview failed");
    } finally { setBusy(false); }
  });
  applyBtn.addEventListener("click", async () => {
    if (busy || !previewResult?.can_import) return;
    if (mode.value !== "skip" || allowZero.checked) {
      const caution = mode.value !== "skip"
        ? "Existing SKUs will receive additional stock every time this import runs. Do NOT use this for an old stock snapshot unless the quantities are genuine additions."
        : "Products missing selling prices will be created at ₹0 and flagged for review. You must set their prices before selling.";
      if (!await confirmDialog({ title: "Confirm catalogue import", message: caution,
                                  confirmLabel: "I understand, import" })) return;
    }
    setBusy(true);
    try {
      const r = await api.applyCatalogImport(fileInput.files[0], mode.value, allowZero.checked,
        forceRepeat.checked, repeatReason.value.trim());
      toast(`Imported ${r.created} new products; ${r.skipped} existing skipped; ${r.stock_added} units added to existing stock.`, "success", {duration:6500});
      m.close();
      await load(); rerenderAll();
    } catch (e) { toastError(e, "Import failed"); }
    finally { setBusy(false); }
  });
  m = openModal({
    title: "Import stock (additive)", wide: true,
    body: h("div", {},
      h("p", { class: "form-hint", style: "margin-bottom:12px" },
        "CSV UTF-8 or XLSX with an Import sheet. Nothing is deleted or locked; SKU matches control duplicates."),
      h("a", { class: "btn sm", href: "/static/templates/pyPOS_Import_Template.xlsx", download: "pyPOS_Import_Template.xlsx",
        style: "margin-bottom:14px" }, icon("i-sales"), "Download editable Excel template"),
      h("div", { class: "field" }, h("label", {}, "Import file"), fileInput),
      h("div", { class: "field" }, h("label", {}, "What to do if a SKU already exists"), mode),
      h("label", { style: "display:flex;gap:8px;align-items:flex-start;font-size:13px;margin-top:9px" },
        allowZero, "Allow new products without a price at ₹0 (mark Price Review Required; review before selling)"),
      h("label", { style: "display:flex;gap:8px;align-items:center;font-size:13px;margin-top:12px" },
        forceRepeat, "Authorised repeat delivery (same file was already imported)"),
      h("div", { class: "field" }, h("label", {}, "Reason for repeating the same stock file"), repeatReason),
      h("p", { class: "form-hint", style: "margin-top:9px" },
        "GST/HSN and costs are never inferred. Blank GST is flagged UNVERIFIED (0 is only a storage placeholder). Unverified GST prevents checkout until an operator confirms the rate. Source snapshots are not automatically added together."),
      previewArea),
    footer: [h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
             previewBtn, applyBtn],
  });
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
      onclick: () => { filterGroup = value; catalogPage = 0; renderGroups(); renderTable(); },
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
  if (reviewOnly) list = list.filter(p => needsBillingReview(p.attributes || []));
  if (searchTerm) {
    const q = searchTerm.toLowerCase();
    list = list.filter((p) =>
      (p.SKU || "").toLowerCase().includes(q) ||
      (p.barcode || "").toLowerCase().includes(q) ||
      (p.group_name || "").toLowerCase().includes(q) ||
      (p.attributes || []).some((a) => `${a.name} ${a.value}`.toLowerCase().includes(q)));
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

  const totalPages = Math.ceil(list.length / CATALOG_PAGE_SIZE);
  catalogPage = Math.max(0, Math.min(catalogPage, totalPages - 1));
  const pageRows = list.slice(catalogPage * CATALOG_PAGE_SIZE,
                              (catalogPage + 1) * CATALOG_PAGE_SIZE);
  const tbody = h("tbody", {}, pageRows.map((p) =>
    h("tr", {},
      h("td", {},
        h("div", { class: "cell-main" }, p.group_name || "—"),
        publicVariant(p.attributes || [])
          ? h("div", { class: "cell-sub" }, publicVariant(p.attributes || [])) : null,
        billingReviewLabel(p.attributes || [])
          ? h("div", { class: "cell-sub", style: "color:var(--danger);font-weight:650" },
              billingReviewLabel(p.attributes || [])) : null),
      h("td", {},
        h("div", { class: "cell-main" }, p.SKU),
        p.barcode ? h("div", { class: "cell-sub" }, p.barcode) : null,
        p.hsn_code ? h("div", { class: "cell-sub" }, `HSN/SAC ${p.hsn_code}`) : null),
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
  if (totalPages > 1) {
    const start = catalogPage * CATALOG_PAGE_SIZE + 1;
    const end = Math.min(list.length, (catalogPage + 1) * CATALOG_PAGE_SIZE);
    tableWrapEl.append(h("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 4px" },
      h("span", { class: "form-hint" }, `Showing ${start}–${end} of ${list.length} matching products`),
      h("div", { style: "display:flex;align-items:center;gap:7px" },
        h("button", { class: "btn sm", disabled: catalogPage === 0,
          onclick: () => { catalogPage--; renderTable(); } }, "Previous"),
        h("span", { class: "form-hint" }, `${catalogPage + 1} / ${totalPages}`),
        h("button", { class: "btn sm", disabled: catalogPage >= totalPages - 1,
          onclick: () => { catalogPage++; renderTable(); } }, "Next"))));
  }
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
  const fHsn = fieldInput("HSN / SAC (optional)", existing?.hsn_code || "");
  const fUnit = fieldInput("Quantity unit (UQC, e.g. NOS / PCS)", existing?.unit_code || "NOS");
  const fTax = fieldInput("GST rate %", existing != null ? trimRate(existing.tax_rate) : "0",
    "number", "0.01");
  const fQty = fieldInput("Opening stock", "0", "number", "1");
  const hasGstReview = !!existing?.attributes?.some(a => a.name === "GST Review Required");
  const hasPriceReview = !!existing?.attributes?.some(a => a.name === "Price Review Required");
  const verifyGst = h("input", { type: "checkbox" });
  const verifyPrice = h("input", { type: "checkbox" });

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
      h("div", { class: "form-row" }, fHsn.wrap, fUnit.wrap),
      h("p", { class: "form-hint" }, "Enter the correct applicable GST rate and HSN/SAC for the product. A 0% GST rate must be explicitly verified when appropriate; no rates are inferred."),
      hasGstReview ? h("label", { class: "form-hint", style: "display:flex;align-items:center;gap:8px;margin:8px 0;color:var(--danger)" },
        verifyGst, "I verified the GST rate above, including 0% if applicable (clear import review flag)") : null,
      hasPriceReview ? h("label", { class: "form-hint", style: "display:flex;align-items:center;gap:8px;margin:8px 0;color:var(--danger)" },
        verifyPrice, "I verified the actual selling price above (clear import review flag)") : null,
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
      hsn_code: fHsn.input.value.trim(),
      unit_code: fUnit.input.value.trim().toUpperCase() || "NOS",
      group_id: groupSel.value ? Number(groupSel.value) : null,
      verified_gst: !!(hasGstReview && verifyGst.checked),
      verified_price: !!(hasPriceReview && verifyPrice.checked),
    };

    // inline validation — mark fields in place instead of only toasting
    [fSKU, fBarcode, fPrice, fCost, fTax, fQty, fHsn, fUnit].forEach((f) => setFieldError(f, null));
    let bad = null;
    if (!payload.SKU) bad = setFieldError(fSKU, "SKU is required");
    if (isNaN(payload.price) || payload.price < 0)
      bad = setFieldError(fPrice, "Enter a valid price");
    if (isNaN(payload.tax_rate) || payload.tax_rate < 0)
      bad = setFieldError(fTax, "Tax must be 0 or more");
    if (payload.hsn_code && !/^\d{2,8}$/.test(payload.hsn_code))
      bad = setFieldError(fHsn, "Enter 2–8 digits");
    if (!/^[A-Z]{2,8}$/.test(payload.unit_code))
      bad = setFieldError(fUnit, "Use 2–8 letters, e.g. NOS or PCS");
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
      if (verifyGst.checked) delete desired["GST Review Required"];
      if (verifyPrice.checked) delete desired["Price Review Required"];
      const current = isEdit
        ? Object.fromEntries((existing.attributes || []).map((a) => [a.name, a.value]))
        : {};
      for (const [n, v] of Object.entries(desired)) {
        if (current[n] !== v) await api.setAttribute(pid, n, v);
      }
      for (const n of Object.keys(current)) {
        if (!(n in desired) && !(n === "GST Review Required" && verifyGst.checked)
            && !(n === "Price Review Required" && verifyPrice.checked)) {
          await api.removeAttribute(pid, n);
        }
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
