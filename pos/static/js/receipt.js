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

  // A compact two-line receipt item: name/quantity/rate on the first line,
  // variant and the recorded line amount beneath.  The sale values are never
  // recomputed here, so historical receipts retain their original figures.
  const rows = (receipt.items || []).flatMap((it) => [
    h("tr", { class: "r-item-main" },
      h("td", { class: "r-item-name" }, esc(it.receipt_name || it.name)),
      h("td", { class: "r-item-qty" },
        esc(it.quantity),
        it.unit_code ? h("span", { class: "r-item-unit" }, ` ${esc(it.unit_code)}`) : null),
      h("td", { class: "r-item-price" }, money(it.unit_price))),
    h("tr", { class: "r-item-detail" },
      h("td", { colspan: "3" },
        h("div", { class: "r-item-detail-row" },
          h("div", { class: "r-item-meta" },
            it.receipt_variant ? esc(it.receipt_variant) : "",
            it.hsn_code ? h("div", {}, `HSN/SAC: ${esc(it.hsn_code)}`) : null),
          h("div", { class: "r-item-line-total" },
            h("span", { class: "r-item-total-label" }, "Total "),
            h("span", {}, money(it.line_total)))))),
  ]);

  const paymentLines = receipt.payments.map((p) =>
    h("div", { class: "r-row" },
      h("span", {}, `${receipt.status === "REFUNDED" ? "Refund via " : ""}${methodLabel(p.method)}`),
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
    h("div", { class: "r-invoice-id" }, isRefund
      ? `Refund: ${receipt.refund_number || `#${String(receipt.id).padStart(5, "0")}`}`
      : receipt.invoice_number
        ? `Invoice: ${receipt.invoice_number}`
        : `Receipt: #${String(receipt.id).padStart(5, "0")}`),
    h("div", { class: "r-datetime" }, fmtTs(receipt.timestamp)),
    receipt.parent_sale_id
      ? h("div", { class: "r-row" }, h("span", {}, "For sale"),
          h("span", {}, receipt.parent_invoice_number ||
            `#${String(receipt.parent_sale_id).padStart(5, "0")}`))
      : null,
    isRefund && receipt.refund_reason
      ? h("div", { class: "r-muted-line" }, `Return reason: ${esc(receipt.refund_reason)}`)
      : null,
    receipt.customer_id != null
      ? h("div", { class: "r-row" }, h("span", {}, "Customer ID"),
          h("span", {}, esc(receipt.customer_id))) : null,
    // A registered buyer's legal identity is still printed in the invoice
    // address block, but retail receipts identify customers by their POS ID.
    s.customer_gstin && s.customer_name
      ? h("div", { class: "r-muted-line" }, `Buyer: ${esc(s.customer_name)}`) : null,
    s.customer_gstin ? h("div", { class: "r-muted-line" }, `Buyer GSTIN: ${esc(s.customer_gstin)}`) : null,
    s.customer_address ? h("div", { class: "r-muted-line" }, `Bill to: ${esc(s.customer_address)}`) : null,
    s.supply_type === "inter" && s.place_of_supply
      ? h("div", { class: "r-muted-line" }, `Place of supply: ${esc(s.place_of_supply)}`) : null,
    h("hr"),
    h("table", { class: "r-items" },
      h("colgroup", {}, h("col", { class: "r-item-name-col" }),
        h("col", { class: "r-item-qty-col" }), h("col", { class: "r-item-price-col" })),
      h("thead", {}, h("tr", {},
        h("th", { scope: "col" }, "Item"),
        h("th", { scope: "col" }, "Qty"),
        h("th", { scope: "col" }, "Price"))),
      h("tbody", {}, rows)),
    h("hr"),
    rowLine("Subtotal", money(receipt.subtotal)),
    Number(receipt.discount_total) > 0
      ? rowLine("Discount", "-" + money(receipt.discount_total)) : null,
    rowLine("Taxable value", money(Number(receipt.total) - Number(receipt.tax_total))),
    rowLine(`${s.price_tax_mode === "inclusive" ? "Included " : ""}${s.tax_label || "GST"} (${sumRates(receipt.items)}%)`, money(receipt.tax_total)),
    receipt.tax_review_warning ? h("div", { class: "r-tax-warning" },
      "Original sale had unverified GST rate(s). Recorded tax was not verified; review the original invoice and accounts.") : null,
    ...(receipt.tax_breakup || []).map((b) =>
      h("div", { class: "r-row r-breakup" },
        h("span", {}, `${esc(b.name)} @${trimNum(b.rate)}%`),
        h("span", {}, money(b.amount)))),
    h("div", { class: "r-row r-big", style: "margin-top:4px" },
      h("span", {}, isRefund ? "TOTAL REFUNDED" : "TOTAL"), h("span", {}, money(receipt.total))),
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
    receipt.loyalty_points_earned > 0
      ? h("div", { class: "r-row" }, h("span", {}, "Loyalty points earned"),
          h("span", {}, `+${receipt.loyalty_points_earned} pts`))
      : null,
    receipt.loyalty_points_reversed > 0
      ? h("div", { class: "r-row" }, h("span", {}, "Loyalty points reversed"),
          h("span", {}, `-${receipt.loyalty_points_reversed} pts`))
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
  }).replace(/\b(am|pm)\b/gi, (period) => period.toUpperCase());
}
