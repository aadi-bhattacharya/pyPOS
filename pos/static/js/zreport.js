// Builds X / Z report receipt DOM for on-screen preview + printing.
import { h } from "./ui.js";
import { state, fmt, fmtDate } from "./api.js";

export function buildZReceipt(rep) {
  const s = state.settings;
  const line = (label, value, cls = "") =>
    h("div", { class: `r-row ${cls}` }, h("span", {}, label), h("span", {}, value));

  const el = h("div", { class: "receipt" },
    h("div", { class: "r-center r-store" }, s.store_name || "My Store"),
    h("div", { class: "r-center r-status" },
      rep.is_open ? "** X REPORT **" : "** Z REPORT **"),
    h("div", { class: "r-center r-muted-line" },
      `Session #${rep.session_id}`),
    h("div", { class: "r-center r-muted-line" },
      `${fmtDate(rep.opened_at)} — ${rep.closed_at ? fmtDate(rep.closed_at) : "open"}`),
    h("hr"),
    line(`Transactions (${rep.items_sold} items)`, String(rep.transactions)),
    line("Gross sales", fmt(rep.gross_sales)),
    Number(rep.discounts) > 0 ? line("Discounts", "-" + fmt(rep.discounts)) : null,
    line(`${s.tax_label || "Tax"} collected`, fmt(rep.taxes)),
    line("Net revenue", fmt(rep.net_revenue), "r-bold"),
    Number(rep.refund_total) > 0
      ? line(`Refunds (${plural(rep.refund_count)})`, "-" + fmt(rep.refund_total))
      : null,
    h("hr"),
    line("Cash in", fmt(rep.cash_in)),
    Number(rep.cash_out) > 0 ? line("Cash refunded", "-" + fmt(rep.cash_out)) : null,
    line("Card", fmt(rep.card_net)),
    line("UPI", fmt(rep.upi_net)),
    Number(rep.credit_spent) > 0
      ? line("Store credit spent", fmt(rep.credit_spent))
      : null,
    h("hr"),
    line("Opening float", fmt(rep.opening_float)),
    line("Expected cash in drawer", fmt(rep.expected_cash), "r-bold"),
    rep.counted_cash !== null
      ? line("Counted cash", fmt(rep.counted_cash))
      : null,
    rep.difference !== null
      ? line("Difference",
          (rep.difference > 0 ? "+" : "") + fmt(rep.difference),
          Math.abs(rep.difference) < 0.005 ? "" : "r-bold")
      : null,
    rep.note ? h("div", { class: "r-muted-line" }, `Note: ${rep.note}`) : null,
    h("hr"),
    h("div", { class: "r-center r-muted-line" },
      rep.is_open
        ? "Live read-out — the day is still open"
        : "Day closed — keep with your records"),
  );
  return el;
}

function plural(n) {
  return `${n} refund${n === 1 ? "" : "s"}`;
}
