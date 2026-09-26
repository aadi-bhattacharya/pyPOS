// Builds receipt DOM for on-screen preview + printing.
import { h } from "./ui.js";
import { state, fmt } from "./api.js";

const esc = (s) => String(s ?? "");

export function buildReceipt(receipt, { store = null } = {}) {
  const s = store || state.settings;
  const sym = s.currency || "$";
  const money = (v) => `${sym}${Number(v || 0).toFixed(2)}`;

  const rows = receipt.items.map((it) => {
    const name = h("tr", {},
      h("td", { style: "width:auto" }, h("div", { class: "r-item-name" },
        esc(it.name)),
        h("div", {}, `${it.quantity} x ${money(it.unit_price)}`)),
      h("td", { style: "text-align:right; white-space:nowrap" }, money(it.line_total)));
    return name;
  });

  const paymentLines = receipt.payments.map((p) =>
    h("div", { class: "r-row" },
      h("span", {}, methodLabel(p.method)),
      h("span", {}, money(p.amount))));

  const el = h("div", { class: "receipt" },
    h("div", { class: "r-center r-store" }, esc(s.store_name || "My Store")),
    s.store_address ? h("div", { class: "r-center r-muted-line" }, esc(s.store_address)) : null,
    s.store_phone ? h("div", { class: "r-center r-muted-line" }, esc(s.store_phone)) : null,
    h("hr"),
    h("div", { class: "r-row" }, h("span", {}, `Receipt #${String(receipt.id).padStart(5, "0")}`),
      h("span", {}, fmtTs(receipt.timestamp))),
    receipt.parent_sale_id
      ? h("div", { class: "r-row" }, h("span", {}, "For sale"),
          h("span", {}, `#${String(receipt.parent_sale_id).padStart(5, "0")}`))
      : null,
    receipt.customer_name
      ? h("div", { class: "r-row" }, h("span", {}, "Customer"),
          h("span", {}, esc(receipt.customer_name)))
      : null,
    h("hr"),
    h("table", {}, h("tbody", {}, rows)),
    h("hr"),
    rowLine("Subtotal", money(receipt.subtotal)),
    Number(receipt.discount_total) > 0
      ? rowLine("Discount", "-" + money(receipt.discount_total)) : null,
    rowLine(`${s.tax_label || "Tax"} (${sumRates(receipt.items)}%)`, money(receipt.tax_total)),
    ...(receipt.tax_breakup || []).map((b) =>
      h("div", { class: "r-row r-breakup" },
        h("span", {}, `${esc(b.name)} @${trimNum(b.rate)}%`),
        h("span", {}, money(b.amount)))),
    h("div", { class: "r-row r-big", style: "margin-top:4px" },
      h("span", {}, "TOTAL"), h("span", {}, money(receipt.total))),
    h("hr"),
    ...paymentLines,
    Number(receipt.store_credit_used) > 0
      ? h("div", { class: "r-row" }, h("span", {}, "Store credit used"),
          h("span", {}, "-" + money(receipt.store_credit_used)))
      : null,
    receipt.refunded_to_credit
      ? h("div", { class: "r-row" }, h("span", {}, "Refunded to"),
          h("span", {}, "Store credit"))
      : null,
    receipt.change && receipt.change > 0
      ? h("div", { class: "r-row r-bold" }, h("span", {}, "Change"),
          h("span", {}, money(receipt.change)))
      : null,
    h("hr"),
    receipt.status !== "COMPLETED"
      ? h("div", { class: "r-center r-status" },
          receipt.status === "REFUNDED" ? "** REFUND **" : "** VOIDED **")
      : null,
    h("div", { class: "r-center r-muted-line" }, esc(s.receipt_footer || "")),
  );
  return el;
}

function rowLine(label, value) {
  return h("div", { class: "r-row" }, h("span", {}, label), h("span", {}, value));
}

function sumRates(items) {
  // % sign added by the caller — returning "5, 12" style here
  const rates = [...new Set(items.map((i) => Number(i.tax_rate || 0)))];
  return rates.map((r) => trimNum(r)).join(", ");
}

function trimNum(n) {
  return String(Number(n.toFixed(2)));
}

export function methodLabel(method) {
  return { CASH: "Cash", CARD: "Card", UPI: "UPI" }[method] || method;
}

function fmtTs(ts) {
  if (!ts) return "";
  const d = new Date(ts.replace(" ", "T") + "Z");
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}
