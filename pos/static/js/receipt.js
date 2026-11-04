// Builds receipt DOM for on-screen preview + printing.
import { h } from "./ui.js";
import { state, fmt } from "./api.js";

const esc = (s) => String(s ?? "");

export function buildReceipt(receipt, { store = null } = {}) {
  const s = { ...state.settings, ...(receipt.invoice || {}), ...(store || {}) };
  const sym = s.currency || "₹";
  const money = (v) => `${sym}${Number(v || 0).toLocaleString("en-IN", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;

  const rows = receipt.items.map((it) => {
    const name = h("tr", {},
      h("td", { style: "width:auto" }, h("div", { class: "r-item-name" },
        esc(it.name)),
        h("div", {}, `${it.quantity} ${it.unit_code || "NOS"} x ${money(it.unit_price)}`),
        it.hsn_code ? h("div", { class: "r-item-meta" }, `HSN/SAC: ${it.hsn_code}`) : null),
      h("td", { style: "text-align:right; white-space:nowrap" }, money(it.line_total)));
    return name;
  });

  const paymentLines = receipt.payments.map((p) =>
    h("div", { class: "r-row" },
      h("span", {}, methodLabel(p.method)),
      h("span", {}, money(p.amount))));

  const isRefund = receipt.status === "REFUNDED";
  const receiptHeading = isRefund ? "Refund receipt" :
    receipt.status === "VOID" ? "Voided receipt" :
    (s.receipt_heading || "Retail receipt");
  const el = h("div", { class: "receipt" },
    s.store_logo && s.show_receipt_logo === "true"
      ? h("div", { class: "r-center" }, h("img", {
          class: "receipt-logo", alt: "Store logo", src: s.store_logo,
          style: `width:${Math.min(65, Math.max(12, Number(s.receipt_logo_width) || 32))}mm`,
        })) : null,
    h("div", { class: "r-center r-store" }, esc(s.store_name || "My Store")),
    s.store_address ? h("div", { class: "r-center r-muted-line" }, esc(s.store_address)) : null,
    s.store_phone ? h("div", { class: "r-center r-muted-line" }, esc(s.store_phone)) : null,
    s.store_state ? h("div", { class: "r-center r-muted-line" },
      `${esc(s.store_state)}${s.store_state_code ? ` (${esc(s.store_state_code)})` : ""}`) : null,
    s.store_gstin ? h("div", { class: "r-center r-muted-line" }, `GSTIN: ${esc(s.store_gstin)}`) : null,
    s.store_fssai ? h("div", { class: "r-center r-muted-line" }, `FSSAI: ${esc(s.store_fssai)}`) : null,
    h("div", { class: "r-center r-heading" }, receiptHeading),
    h("hr"),
    h("div", { class: "r-row" }, h("span", {}, receipt.invoice_number && !isRefund
      ? `Invoice ${receipt.invoice_number}`
      : `Receipt #${String(receipt.id).padStart(5, "0")}`),
      h("span", {}, fmtTs(receipt.timestamp))),
    receipt.parent_sale_id
      ? h("div", { class: "r-row" }, h("span", {}, "For sale"),
          h("span", {}, `#${String(receipt.parent_sale_id).padStart(5, "0")}`))
      : null,
    (s.customer_name || receipt.customer_name)
      ? h("div", { class: "r-row" }, h("span", {}, "Customer"),
          h("span", {}, esc(s.customer_name || receipt.customer_name))) : null,
    s.customer_gstin ? h("div", { class: "r-muted-line" }, `Buyer GSTIN: ${esc(s.customer_gstin)}`) : null,
    s.customer_address ? h("div", { class: "r-muted-line" }, `Bill to: ${esc(s.customer_address)}`) : null,
    s.supply_type === "inter" && s.place_of_supply
      ? h("div", { class: "r-muted-line" }, `Place of supply: ${esc(s.place_of_supply)}`) : null,
    h("hr"),
    h("table", {}, h("tbody", {}, rows)),
    h("hr"),
    rowLine(s.price_tax_mode === "inclusive" ? "Subtotal (GST inclusive)" :
      "Subtotal (before discount/tax)", money(receipt.subtotal)),
    Number(receipt.discount_total) > 0
      ? rowLine("Discount", "-" + money(receipt.discount_total)) : null,
    rowLine("Taxable value", money(Number(receipt.total) - Number(receipt.tax_total))),
    rowLine(`${s.price_tax_mode === "inclusive" ? "Included " : ""}${s.tax_label || "GST"} (${sumRates(receipt.items)}%)`, money(receipt.tax_total)),
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
    Number(receipt.store_credit_refunded) > 0
      ? h("div", { class: "r-row" }, h("span", {}, "Returned to store credit"),
          h("span", {}, money(receipt.store_credit_refunded)))
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
    s.store_upi_id && !isRefund ? h("div", { class: "r-center r-muted-line" },
      `UPI: ${esc(s.store_upi_id)}`) : null,
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
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}
