import { api, fmt, fmtDate, todayISO } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, confirmDialog, printReceipt } from "../ui.js";
import { buildReceipt, methodLabel } from "../receipt.js";
import { showReceiptModal } from "./register.js";

const PAGE_SIZE = 100;
const PRESETS = [["today", "Today"], ["7", "7 days"], ["30", "30 days"],
  ["custom", "Custom"], ["all", "All time"]];
let rows = [];
let preset = "7";
let statusFilter = "";
let searchTerm = "";
let customFrom = todayISO(-6);
let customTo = todayISO();
let tableEl = null;
let statsEl = null;
let summaryEl = null;
let hasMore = false;
let loadSeq = 0;
let searchTimer = null;

function dateRange() {
  if (preset === "all") return {};
  if (preset === "custom") return { from_date: customFrom, to_date: customTo };
  const offset = preset === "today" ? 0 : -(Number(preset) - 1);
  return { from_date: todayISO(offset), to_date: todayISO() };
}

export async function renderSales(main) {
  // Reports -> day-by-day detail lands on a precise date, rather than a
  // generic rolling week. Other filters persist while navigating this view.
  const day = new URLSearchParams((location.hash.split("?")[1] || "")).get("date");
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) {
    preset = "custom";
    customFrom = day;
    customTo = day;
    statusFilter = "";
    searchTerm = "";
  }
  main.replaceChildren();
  const title = h("div", { class: "view-title" },
    h("h2", {}, "Sales"), h("div", { class: "sub", id: "sales-sub" },
      "Find an invoice, check refunds or reprint a receipt"));
  const exportSales = h("a", { class: "btn", title: "Export ALL transactions for the date range (not just loaded rows)" },
    "Sales CSV");
  const exportItems = h("a", { class: "btn", title: "Export ALL sale line items with recorded GST and HSN details" },
    "GST lines CSV");
  const exportUPI = h("a", { class: "btn", title: "Export UPI references and bank UTRs for reconciliation (not verified by pyPOS)" },
    "UPI CSV");
  const syncExports = () => {
    const qs = new URLSearchParams(dateRange()).toString();
    exportSales.href = `/api/export/sales.csv?${qs}`;
    exportItems.href = `/api/export/sale-lines.csv?${qs}`;
    exportUPI.href = `/api/export/upi.csv?${qs}`;
  };
  syncExports();
  const head = h("div", { class: "view-head" }, title,
    h("div", { class: "view-actions" },
      h("a", { class: "btn primary", href: "#/refunds" }, icon("i-undo"), "Returns & refunds"),
      exportSales, exportItems, exportUPI,
      h("button", { class: "btn ghost icon-only", title: "Refresh transactions",
        onclick: () => loadRows(true) }, icon("i-refresh"))));

  const chips = h("div", { class: "chips", role: "group", "aria-label": "Sales date range" });
  const fromInput = h("input", { class: "input", type: "date", value: customFrom,
    "aria-label": "From date (IST)" });
  const toInput = h("input", { class: "input", type: "date", value: customTo,
    "aria-label": "To date (IST)" });
  const dates = h("div", { class: "sales-filter-bar" },
    h("div", { class: "field" }, h("label", {}, "From (IST)"), fromInput),
    h("div", { class: "field" }, h("label", {}, "To (IST)"), toInput));
  function rangeChips() {
    chips.replaceChildren(...PRESETS.map(([value, label]) => h("button", {
      class: `chip${preset === value ? " active" : ""}`,
      "aria-pressed": preset === value ? "true" : "false",
      onclick: () => {
        const current = dateRange();
        preset = value;
        if (value === "custom") {
          customFrom = current.from_date || todayISO(-6);
          customTo = current.to_date || todayISO();
          fromInput.value = customFrom;
          toInput.value = customTo;
        }
        rangeChips();
        dates.hidden = preset !== "custom";
        syncExports();
        loadRows(true);
      },
    }, label)));
  }
  rangeChips();
  dates.hidden = preset !== "custom";
  fromInput.addEventListener("change", () => { customFrom = fromInput.value; syncExports(); loadRows(true); });
  toInput.addEventListener("change", () => { customTo = toInput.value; syncExports(); loadRows(true); });

  const statusSel = h("select", { class: "input", "aria-label": "Sale status",
    onchange: () => { statusFilter = statusSel.value; loadRows(true); } },
    h("option", { value: "" }, "All transactions"),
    h("option", { value: "COMPLETED" }, "Sales only"),
    h("option", { value: "REFUNDED" }, "Refunds only"),
    h("option", { value: "VOID" }, "Voids only"));
  statusSel.value = statusFilter;
  const search = h("input", { class: "input", value: searchTerm,
    placeholder: "Search invoice, customer, salesperson or tender…", autocomplete: "off",
    "aria-label": "Search sales",
    oninput: () => {
      searchTerm = search.value.trim();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadRows(true), 240);
    } });
  const toolbar = h("div", { class: "sales-filter-bar" },
    h("div", { class: "search-wrap" }, icon("i-search"), search),
    statusSel, chips);

  statsEl = h("div", { class: "sales-kpis" });
  summaryEl = h("div", { class: "sales-table-note" });
  tableEl = h("div");
  main.append(head, toolbar, dates, statsEl, summaryEl, tableEl);
  await loadRows(true);
}

async function loadRows(reset = false) {
  if (!tableEl) return;
  const seq = ++loadSeq;
  const params = { ...dateRange(), limit: PAGE_SIZE + 1,
    offset: reset ? 0 : rows.length };
  if (statusFilter) params.status = statusFilter;
  if (searchTerm) params.q = searchTerm;
  if (params.from_date && params.to_date && params.from_date > params.to_date) {
    tableEl.replaceChildren(h("div", { class: "empty-state card" },
      h("h3", {}, "Check the date range"),
      h("p", {}, "The start date must be on or before the end date.")));
    statsEl.replaceChildren();
    summaryEl.replaceChildren();
    return;
  }
  if (reset) {
    rows = [];
    tableEl.replaceChildren(h("div", { class: "skel-rows", style: "padding:10px" },
      ...Array.from({ length: 5 }, () => h("div", { class: "skeleton" }))));
  }
  let fetched;
  try { fetched = await api.listSales(params); }
  catch (e) { if (seq === loadSeq) { toastError(e); tableEl.replaceChildren(); } return; }
  if (seq !== loadSeq || !tableEl.isConnected) return;
  hasMore = fetched.length > PAGE_SIZE;
  rows = rows.concat(fetched.slice(0, PAGE_SIZE));
  renderResults();
}

function renderResults() {
  const gross = rows.filter((r) => r.status === "COMPLETED").reduce((a, r) => a + r.total, 0);
  const refunds = rows.filter((r) => r.status === "REFUNDED").reduce((a, r) => a + r.total, 0);
  const saleCount = rows.filter((r) => r.status === "COMPLETED").length;
  const refundCount = rows.filter((r) => r.status === "REFUNDED").length;
  const kpi = (label, value) => h("div", {}, h("div", { class: "kpi-label" }, label),
    h("div", { class: "kpi-value" }, value));
  statsEl.replaceChildren(
    kpi("Net sales", fmt(gross - refunds)),
    kpi("Completed invoices", `${saleCount} · ${fmt(gross)}`),
    kpi("Refund documents", `${refundCount} · ${fmt(refunds)}`),
    kpi("Average checkout", fmt(saleCount ? gross / saleCount : 0)));
  summaryEl.replaceChildren(
    h("span", {}, `Showing ${rows.length}${hasMore ? "+" : ""} matching transactions${hasMore ? " (more available)" : ""}`),
    h("span", {}, "Totals above use loaded rows only · IST"));
  const sub = document.getElementById("sales-sub");
  if (sub) sub.textContent = `${rows.length}${hasMore ? "+" : ""} matching documents · latest first`;
  if (!rows.length) {
    tableEl.replaceChildren(h("div", { class: "empty-state card" }, icon("i-sales"),
      h("h3", {}, "No transactions found"),
      h("p", {}, "Try a wider date range, another invoice number, or All transactions.")));
    return;
  }
  tableEl.replaceChildren(h("div", { class: "table-wrap" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Invoice / customer"),
        h("th", {}, "Date / time (IST)"), h("th", {}, "Salesperson"), h("th", { class: "num" }, "Units"),
        h("th", {}, "Payment"), h("th", { class: "num" }, "Net effect"),
        h("th", {}, "Status"), h("th", {}, "Actions"))),
      h("tbody", {}, ...rows.map(rowHtml)))),
    hasMore ? h("div", { class: "sales-load-more" },
      h("button", { class: "btn", onclick: () => loadRows(false) },
        "Load 100 more transactions")) : null);
}

function rowHtml(r) {
  const methods = [...new Set((r.methods || "").split(",").filter(Boolean))].map(methodLabel).join(", ");
  const canRefund = r.status === "COMPLETED" && !r.fully_refunded;
  const canVoid = r.status === "COMPLETED" && !r.refunded;
  const effect = r.status === "VOID" ? 0 : r.status === "REFUNDED" ? -r.total : r.total;
  return h("tr", {},
    h("td", {},
      h("button", { class: "sales-invoice-button", onclick: () => detailModal(r.id) },
        r.invoice_number || `#${String(r.id).padStart(5, "0")}`),
      r.parent_sale_id ? h("div", { class: "cell-sub" }, `Return against sale #${r.parent_sale_id}`) : null,
      r.customer_name ? h("div", { class: "cell-sub" }, r.customer_name) : null),
    h("td", {}, fmtDate(r.timestamp)),
    h("td", { class: "cell-sub" }, r.salesperson || "—"),
    h("td", { class: "num" }, String(r.item_count ?? 0)),
    h("td", { class: "cell-sub" }, methods || (r.status === "REFUNDED" ? "Store credit / original" : "Store credit / none")),
    h("td", { class: `num cell-main${effect < 0 ? " sales-money-negative" : ""}` }, fmt(effect)),
    h("td", {}, statusPill(r)),
    h("td", { class: "actions-cell" },
      actionBtn("i-eye", "View and reprint", () => detailModal(r.id)),
      canRefund ? actionBtn("i-undo", "Refund", () => refundDialog(r.id, () => loadRows(true))) : null,
      canVoid ? actionBtn("i-ban", "Void", () => voidFlow(r, () => loadRows(true))) : null));
}

function actionBtn(ic, title, fn) {
  return h("button", { class: "btn ghost icon-only sm", title, onclick: fn }, icon(ic));
}

export function statusPill(r) {
  if (r.status === "REFUNDED") return h("span", { class: "pill warn" }, "REFUND");
  if (r.status === "VOID") return h("span", { class: "pill bad" }, "VOID");
  if (r.refunded && r.partially_refunded)
    return h("span", { class: "pill warn" }, "PARTIAL REFUND");
  if (r.refunded) return h("span", { class: "pill warn" }, "REFUNDED");
  return h("span", { class: "pill ok" }, "COMPLETED");
}

export async function detailModal(id) {
  let sale;
  try {
    sale = await api.getSale(id);
  } catch (e) {
    return toastError(e);
  }

  const canAct = sale.status === "COMPLETED" && !sale.fully_refunded;
  const receiptEl = buildReceipt(sale);
  const partialHint = canAct && sale.refunded
    ? h("p", { class: "form-hint", style: "text-align:center;margin-top:6px" },
        `Partially refunded — ${sale.items.reduce((a, i) => a + i.refundable_qty, 0)} unit(s) still refundable.`)
    : null;

  openModal({
    title: sale.invoice_number ? `Invoice ${sale.invoice_number}` : `Sale #${String(sale.id).padStart(5, "0")}`,
    narrow: true,
    body: h("div", {},
      receiptEl,
      sale.payments?.some(p => p.method === "UPI" && (p.reference || p.upi_utr))
        ? h("div", { class: "upi-internal-record" },
          h("strong", {}, "Internal UPI reconciliation (not on receipt)"),
          ...sale.payments.filter(p => p.method === "UPI").map(p =>
            h("div", {}, `${fmt(p.amount)} · POS note: ${p.reference || "—"} · Bank UTR: ${p.upi_utr || "not entered"}`)),
          h("div", { class: "form-hint" }, "Compare with merchant app or bank statement; these details are not automatic payment verification."))
        : null,
      partialHint,
      sale.children && sale.children.length
        ? h("p", { class: "form-hint", style: "text-align:center;margin-top:10px" },
            sale.children.map((c) =>
              c.status === "REFUNDED"
                ? `Refunded by receipt #${c.id}`
                : `Voided (#${c.id})`).join("; "))
        : null),
    footer: [
      h("button", { class: "btn", onclick: () => printReceipt(receiptEl) },
        icon("i-printer"), "Print"),
      canAct ? h("button", {
        class: "btn subtle-danger",
        onclick: () => voidFlow(sale, () => loadRows(true)),
      }, icon("i-ban"), "Void") : null,
      canAct ? h("button", {
        class: "btn primary",
        onclick: () => refundDialog(sale.id, () => loadRows(true)),
      }, icon("i-undo"), "Refund") : null,
    ],
  });
}

// ================= refunds (partial-aware) =================

export async function refundDialog(saleId, done) {
  let sale;
  try { sale = await api.getSale(saleId); }
  catch (e) { return toastError(e); }
  if (sale.status !== "COMPLETED") return toast("Only completed sales can be returned.", "warn");

  const refundable = sale.items.filter((it) => it.refundable_qty > 0);
  if (!refundable.length) return toast("This invoice has no remaining refundable items.", "warn");

  // Nothing is selected by default: a cashier deliberately counts the items
  // physically returned instead of accidentally returning the full invoice.
  const qtys = new Map(refundable.map((it) => [it.product_id, 0]));
  const originalLabel = sale.invoice_number || `Sale #${sale.id}`;
  const titleLine = h("div", { class: "return-invoice-context" },
    h("strong", {}, originalLabel),
    h("span", {}, ` · ${fmt(sale.total)} originally paid`),
    sale.customer_id ? h("span", {}, ` · Customer ID ${sale.customer_id}`) : null);
  const selectedCount = h("span", { class: "return-count" }, "0 units selected");
  const linesEl = h("div", { class: "refund-lines return-items" });
  const amountEl = h("strong", { class: "amt" }, fmt(0));
  const detailEl = h("div", { class: "return-preview-detail" }, "Select the returned quantities to calculate the refund.");
  const errorEl = h("div", { class: "return-error", role: "status" });
  const previewBox = h("div", { class: "return-summary" },
    h("div", { class: "return-summary-main" }, h("span", {}, "Refund amount"), amountEl),
    detailEl, errorEl);
  const reasonSel = h("select", { class: "input", "aria-label": "Return reason" },
    h("option", { value: "" }, "Choose a reason…"),
    ...["Size / fit issue", "Item defective", "Wrong item supplied",
      "Customer changed mind", "Billing correction", "Other"].map(v =>
      h("option", { value: v }, v)));
  const noteInput = h("input", { class: "input", type: "text", maxlength: "160",
    placeholder: "Additional details (optional)", "aria-label": "Return notes" });
  const paySel = h("select", { class: "input", "aria-label": "Refund destination" },
    h("option", { value: "original" }, "Original payment methods"),
    sale.customer_id ? h("option", { value: "credit" }, "Customer store credit") : null);
  const payHint = h("p", { class: "form-hint return-pay-hint" },
    "Cash must be paid out at the till. Card / UPI repayments must be initiated separately; this POS records them but cannot transfer money.");
  const goBtn = h("button", { class: "btn primary lg", disabled: true },
    icon("i-undo"), "Record refund");
  let previewGeneration = 0;
  let previewOk = false;
  let previewAmount = 0;
  let pendingKey = null;
  let pendingFingerprint = "";
  const lineValues = () => refundable.filter(it => (qtys.get(it.product_id) || 0) > 0)
    .map(it => ({ product_id: it.product_id, quantity: qtys.get(it.product_id) }));
  const reasonValue = () => {
    const base = reasonSel.value;
    const extra = noteInput.value.trim();
    return base ? (extra ? `${base}: ${extra}` : base) : "";
  };
  function updateEnabled() {
    goBtn.disabled = !previewOk || !reasonSel.value || !lineValues().length;
  }
  async function updateTotal() {
    const gen = ++previewGeneration;
    previewOk = false;
    updateEnabled();
    errorEl.textContent = "";
    const lines = lineValues();
    const count = lines.reduce((sum, it) => sum + it.quantity, 0);
    selectedCount.textContent = `${count} unit${count === 1 ? "" : "s"} selected`;
    if (!lines.length) {
      amountEl.textContent = fmt(0);
      detailEl.textContent = "Select the returned quantities to calculate the refund.";
      return;
    }
    amountEl.textContent = "Calculating…";
    detailEl.textContent = "Checking the original sale's recorded discount, GST and payment split…";
    try {
      const p = await api.previewRefund(saleId, lines, paySel.value === "credit");
      if (gen !== previewGeneration) return;
      previewOk = true;
      previewAmount = Number(p.total);
      amountEl.textContent = fmt(p.total);
      const payments = (p.refund_payments || []).map(x =>
        `${methodLabel(x.method)} ${fmt(x.amount)}`);
      if (Number(p.refund_store_credit) > 0)
        payments.push(`Store credit ${fmt(p.refund_store_credit)}`);
      detailEl.replaceChildren(
        h("div", {}, `${p.quantity} unit(s) · Discount ${fmt(p.discount_total)} · GST ${fmt(p.tax_total)}`),
        h("div", { class: "return-payment-breakdown" },
          payments.length ? `Record return via: ${payments.join(" + ")}` : "No cash or digital repayment required"));
      updateEnabled();
    } catch (e) {
      if (gen !== previewGeneration) return;
      amountEl.textContent = "Unavailable";
      detailEl.textContent = "";
      errorEl.textContent = String(e.message || e);
    }
  }
  function setQty(it, value, countEl) {
    qtys.set(it.product_id, Math.min(it.refundable_qty, Math.max(0, value)));
    countEl.textContent = String(qtys.get(it.product_id));
    updateTotal();
  }
  const refreshInputs = [];
  for (const it of refundable) {
    const counter = h("span", { class: "return-qty-value", "aria-live": "polite" }, "0");
    const field = h("div", { class: "refund-line" },
      h("div", { class: "cl-info" },
        h("div", { class: "cl-name" }, it.receipt_name || it.name),
        it.receipt_variant ? h("div", { class: "cl-sub" }, it.receipt_variant) : null,
        h("div", { class: "cl-sub" },
          `${fmt(it.unit_price)} each · ${it.refundable_qty} left to return`)),
      h("div", { class: "stepper return-stepper" },
        h("button", { "aria-label": `Decrease quantity for ${it.receipt_name || it.name}`,
          onclick: () => setQty(it, (qtys.get(it.product_id) || 0) - 1, counter) }, icon("i-minus")),
        counter,
        h("button", { "aria-label": `Increase quantity for ${it.receipt_name || it.name}`,
          onclick: () => setQty(it, (qtys.get(it.product_id) || 0) + 1, counter) }, icon("i-plus"))));
    refreshInputs.push({ it, counter });
    linesEl.append(field);
  }
  const selectAll = h("button", { class: "btn sm", onclick: () => {
    for (const x of refreshInputs) {
      qtys.set(x.it.product_id, x.it.refundable_qty);
      x.counter.textContent = String(x.it.refundable_qty);
    }
    updateTotal();
  } }, "Return all remaining");
  const clearAll = h("button", { class: "btn ghost sm", onclick: () => {
    for (const x of refreshInputs) {
      qtys.set(x.it.product_id, 0);
      x.counter.textContent = "0";
    }
    updateTotal();
  } }, "Clear selection");
  reasonSel.addEventListener("change", updateEnabled);
  paySel.addEventListener("change", () => {
    payHint.textContent = paySel.value === "credit"
      ? "Store credit will be added immediately to the original customer. No cash or bank transfer takes place."
      : "Cash must be paid out at the till. Card / UPI repayments must be initiated separately; this POS records them but cannot transfer money.";
    updateTotal();
  });
  goBtn.addEventListener("click", async () => {
    if (!previewOk || !reasonSel.value) return;
    const lines = lineValues();
    if (!lines.length) return;
    const to_credit = paySel.value === "credit";
    const reason = reasonValue();
    // A stable key makes retry-after-network-failure safe. A changed payload
    // receives a new ID; the server rejects any key reused with new details.
    const payloadKey = JSON.stringify({lines, to_credit, reason});
    if (payloadKey !== pendingFingerprint) {
      pendingKey = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID() : `refund_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      pendingFingerprint = payloadKey;
    }
    const ok = await confirmDialog({
      title: `Confirm return · ${fmt(previewAmount)}`,
      message: `Record a refund of ${fmt(previewAmount)} for ${originalLabel}? Verify the items are back in your possession and arrange any cash, card or UPI payout manually. This cannot be undone.`,
      confirmLabel: "Record refund", danger: true,
    });
    if (!ok) return;
    goBtn.disabled = true;
    try {
      const refund = await api.refundSale(saleId, {
        lines, to_credit, reason, request_id: pendingKey,
      });
      toast(`Refund #${refund.id} · ${fmt(refund.total)} recorded.`, "success",
        { title: "Return recorded" });
      closeModalAll();
      if (done) await done(refund);
      showReceiptModal(refund);
    } catch (e) {
      toastError(e, "Refund not confirmed");
      // Keep the same request key for a retry: the first request might have
      // succeeded even if its response was lost.
      await updateTotal();
    } finally {
      updateEnabled();
    }
  });

  openModal({
    title: `Return items · ${originalLabel}`,
    wide: true,
    body: h("div", { class: "return-workflow" },
      titleLine,
      h("div", { class: "return-section-head" },
        h("strong", {}, "1. Select returned items"),
        h("div", { class: "return-tools" }, selectedCount, selectAll, clearAll)),
      linesEl,
      h("div", { class: "return-form-grid" },
        h("div", { class: "field" }, h("label", {}, "2. Reason for return"), reasonSel),
        h("div", { class: "field" }, h("label", {}, "Details"), noteInput)),
      h("div", { class: "field" }, h("label", {}, "3. Where to refund"), paySel, payHint),
      previewBox,
      h("p", { class: "form-hint" },
        "The original recorded discount and tax are used. Returned stock and relevant loyalty points are reversed. This is an operational refund, not a statutory GST credit note.")),
    footer: [
      h("button", { class: "btn", onclick: () => closeModalAll() }, "Cancel"), goBtn,
    ],
  });
}

async function voidFlow(r, done) {
  const ok = await confirmDialog({
    title: `Void #${String(r.id).padStart(5, "0")}?`,
    message:
      "Voiding cancels the sale entirely and returns all items to stock. " +
      "This cannot be undone.",
    confirmLabel: "Void sale",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.voidSale(r.id);
    toast(`Sale #${r.id} voided — stock returned.`, "success");
    closeModalAll();
    await loadRows(true);
    // The transaction table has already been refreshed.
  } catch (e) {
    toastError(e);
  }
}
