import { api, can, fmt, fmtDate } from "../api.js";
import { h, icon, toastError } from "../ui.js";
import { refundDialog, detailModal } from "./salesview.js";
import { exchangeDialog } from "./exchanges.js";

const PAGE = 75;
let renderSeq = 0;

export async function renderRefunds(main) {
  const viewSeq = ++renderSeq;
  main.replaceChildren();
  const search = h("input", { class: "input", type: "search", autocomplete: "off",
    placeholder: "Invoice number, customer name, phone or sale ID…",
    "aria-label": "Find a sale to return" });
  const result = h("div", { class: "return-lookup-results" });
  const recent = h("div", { class: "return-history-results" });
  const loadMore = h("button", { class: "btn", hidden: true }, "Show older refund receipts");
  const title = h("div", { class: "view-head" },
    h("div", { class: "view-title" }, h("h2", {}, "Returns & refunds"),
      h("div", { class: "sub" }, "Refund to payment or store credit, or exchange for new items")),
    h("div", { class: "view-actions" },
      can("export_data") ? h("a", { class: "btn", href: "/api/export/refunds.csv", download: "" }, "Refunds CSV") : null,
      h("a", { class: "btn", href: "#/sales" }, icon("i-sales"), "All sales")));
  main.append(title,
    h("section", { class: "card return-search-card" },
      h("h3", {}, "Find the original sale"),
      h("p", { class: "form-hint" },
        "Search all recorded invoices, including earlier dates. No return is created until you confirm it."),
      h("div", { class: "search-wrap" }, icon("i-search"), search), result),
    h("section", { class: "card return-history-card" },
      h("div", { class: "return-section-head" }, h("h3", {}, "Recent refund receipts"),
        h("button", { class: "btn ghost sm", onclick: () => loadRecent(true) },
          icon("i-refresh"), "Refresh")),
      h("p", { class: "form-hint" },
        "These are recorded refunds. Cash/card/UPI settlements still need reconciliation against actual payouts."),
      recent, h("div", { class: "sales-load-more" }, loadMore)));

  let searchCount = 0;
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    const seq = ++searchCount;
    timer = setTimeout(() => lookup(seq), 250);
  });
  search.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(timer); lookup(++searchCount); }
  });
  async function lookup(seq) {
    const query = search.value.trim();
    if (!query) { result.replaceChildren(); return; }
    result.replaceChildren(h("p", { class: "form-hint" }, "Searching recorded sales…"));
    try {
      const rows = await api.listSales({q: query, status: "COMPLETED", limit: 50});
      if (seq !== searchCount || viewSeq !== renderSeq || !main.isConnected) return;
      const exact = rows.filter(r => !r.fully_refunded);
      result.replaceChildren(exact.length
        ? h("div", { class: "return-search-list" }, ...exact.map(row =>
            h("div", { class: "return-search-result" },
              h("div", {},
                h("strong", {}, row.invoice_number || `Sale #${row.id}`),
                h("div", { class: "cl-sub" }, `${fmtDate(row.timestamp)} · ${row.item_count} unit(s)`),
                row.customer_id ? h("div", { class: "cl-sub" }, `Customer ID ${row.customer_id}`) : null),
              h("strong", {}, fmt(row.total)),
              h("button", { class: "btn primary sm", onclick: () => refundDialog(row.id, () => loadRecent(true)) },
                icon("i-undo"), row.refunded ? "Continue return" : "Return items"),
              can("refund_sales") && can("checkout")
                ? h("button", { class: "btn sm", onclick: () => exchangeDialog(row.id, () => loadRecent(true)) },
                    icon("i-refresh"), "Exchange") : null)))
        : h("p", { class: "form-hint" },
          "No refundable completed sales matched. Try the full invoice number or check All sales."));
    } catch (e) { if (seq === searchCount) { toastError(e); result.replaceChildren(); } }
  }
  let offset = 0;
  let sequence = 0;
  loadMore.addEventListener("click", () => loadRecent(false));
  async function loadRecent(reset) {
    const seq = ++sequence;
    const nextOffset = reset ? 0 : offset;
    if (reset) recent.replaceChildren(h("p", { class: "form-hint" }, "Loading refunds…"));
    try {
      const rows = await api.listSales({status: "REFUNDED", limit: PAGE + 1, offset: nextOffset});
      if (seq !== sequence || viewSeq !== renderSeq || !main.isConnected) return;
      if (reset) { offset = 0; recent.replaceChildren(); }
      const page = rows.slice(0, PAGE);
      offset += page.length;
      for (const r of page) recent.append(h("div", { class: "return-history-row" },
        h("div", {}, h("strong", {}, r.refund_number || `Refund #${r.id}`),
          h("div", { class: "cl-sub" }, `${fmtDate(r.timestamp)} · Original sale #${r.parent_sale_id || "?"}${r.exchange_refund ? " · Exchange" : ""}`)),
        h("strong", { class: "return-negative" }, `−${fmt(r.total)}`),
        h("button", { class: "btn sm", onclick: () => detailModal(r.id) }, "View / print")));
      if (!offset) recent.replaceChildren(h("p", { class: "form-hint" }, "No recorded refunds yet."));
      loadMore.hidden = rows.length <= PAGE;
    } catch (e) { toastError(e); if (reset) recent.replaceChildren(); }
  }
  await loadRecent(true);
}
