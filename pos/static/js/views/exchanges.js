import { api, fmt, can } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, confirmDialog } from "../ui.js";
import { showReceiptModal } from "./register.js";
import { creditCustomerPicker } from "./creditCustomerPicker.js";

// An exchange is an atomic linked return and new invoice. Named buyers may
// use wallet credit; walk-ins use a direct invoice offset, never phantom cash.
export async function exchangeDialog(originalId, done) {
  if (!can("refund_sales") || !can("checkout"))
    return toast("Refund and checkout permissions are both required for exchanges.", "warn");
  let original, policy;
  try { [original, policy] = await Promise.all([
    api.getSale(originalId), api.getSettings()]); }
  catch (e) { toastError(e); return; }
  if (policy.exchange_allow === "false")
    return toast("Exchanges are disabled by store policy.", "warn");
  const refundable = (original.items || []).filter(item => item.refundable_qty > 0);
  if (original.status !== "COMPLETED" || !refundable.length) {
    toast("This invoice has no remaining items available for exchange.", "warn");
    return;
  }
  const qty = new Map(refundable.map(item => [item.product_id, 0]));
  const selected = new Map();
  const returnsList = h("div", { class: "refund-lines return-items" });
  const replacementsList = h("div", { class: "refund-lines return-items" });
  const productsList = h("div", { class: "return-search-list" });
  const search = h("input", { class: "input", type: "search", autocomplete: "off",
    placeholder: "Scan barcode or search product / size / SKU…",
    "aria-label": "Find replacement product" });
  const reason = h("select", { class: "input", "aria-label": "Exchange reason" },
    h("option", { value: "" }, "Choose a reason…"),
    ...["Size / fit issue", "Item defective", "Wrong item supplied",
      "Customer changed mind", "Other"].map(t => h("option", { value: t }, t)));
  const reasonNote = h("input", { class: "input", maxlength: "160", type: "text",
    placeholder: "Additional detail (optional)", "aria-label": "Exchange notes" });
  const summary = h("div", { class: "return-summary" },
    h("p", { class: "form-hint" }, "Select returned and replacement items to calculate the difference."));
  const paymentMethod = h("select", { class: "input", "aria-label": "Exchange additional payment method" },
    ...[["CASH", "Cash"], ["CARD", "Card"], ["UPI", "UPI"]].map(([k, v]) => h("option", { value: k }, v)));
  const tender = h("input", { class: "input", type: "number", min: "0", step: "0.01",
    "aria-label": "Exchange additional amount received" });
  const utr = h("input", { class: "input", maxlength: "80", placeholder: "Bank UTR (if available)",
    "aria-label": "Exchange UPI UTR", hidden: true });
  const paymentBox = h("div", { class: "field", hidden: true },
    h("label", {}, "Additional payment (difference only)"),
    h("div", { class: "return-form-grid" }, paymentMethod, tender), utr,
    h("p", { class: "form-hint" },
      "Record actual payment received. Card and UPI transactions must be verified externally."));
  const submit = h("button", { class: "btn primary lg", disabled: true },
    icon("i-refresh"), "Complete exchange");
  const forfeitureAck = h("input", { type: "checkbox", "aria-label": "Acknowledge forfeited exchange difference" });
  const forfeitureLabel = h("label", { class: "credit-toggle", hidden: true }, forfeitureAck,
    h("span", {}, "I have explained and acknowledged the forfeited difference."));
  const context = h("div", { class: "return-invoice-context" },
    h("strong", {}, original.invoice_number || `Sale #${original.id}`),
    h("span", {}, ` · Original ${fmt(original.total)}`),
    original.customer_id ? h("span", {}, ` · Customer ID ${original.customer_id}`) : null);
  let picker, preview, pendingId = null, pendingFingerprint = "", generation = 0, searchSeq = 0;
  let searchTimer, previewTimer;
  const canAnonymous = policy.exchange_allow_anonymous !== "false";
  if (!original.customer_id) picker = creditCustomerPicker(
    () => refreshPreview(), { optional: canAnonymous });
  const customerId = () => original.customer_id || picker?.id() || null;
  const returnLines = () => refundable.filter(item => (qty.get(item.product_id) || 0) > 0)
    .map(item => ({product_id: item.product_id, quantity: qty.get(item.product_id)}));
  const replacementLines = () => [...selected.values()]
    .map(({product, quantity}) => ({product_id: product.id, quantity}));
  const returnReason = () => reason.value.trim() + (reasonNote.value.trim() ? `: ${reasonNote.value.trim()}` : "");

  for (const item of refundable) {
    const counter = h("span", { class: "return-qty-value" }, "0");
    const adjust = change => {
      qty.set(item.product_id, Math.min(item.refundable_qty,
        Math.max(0, (qty.get(item.product_id) || 0) + change)));
      counter.textContent = String(qty.get(item.product_id));
      refreshPreview();
    };
    returnsList.append(h("div", { class: "refund-line" },
      h("div", { class: "cl-info" },
        h("div", { class: "cl-name" }, item.receipt_name || item.name),
        h("div", { class: "cl-sub" },
          `${fmt(item.unit_price)} each · ${item.refundable_qty} refundable`)),
      h("div", { class: "stepper return-stepper" },
        h("button", { "aria-label": `Return fewer ${item.receipt_name || item.name}`,
          onclick: () => adjust(-1) }, icon("i-minus")), counter,
        h("button", { "aria-label": `Return more ${item.receipt_name || item.name}`,
          onclick: () => adjust(1) }, icon("i-plus")))));
  }

  function drawReplacements() {
    replacementsList.replaceChildren(...([...selected.values()].length
      ? [...selected.values()].map(({product, quantity}) => h("div", { class: "refund-line" },
        h("div", { class: "cl-info" },
          h("div", { class: "cl-name" }, product.group_name || product.SKU),
          h("div", { class: "cl-sub" }, `SKU ${product.SKU} · ${fmt(product.price)} · Available ${product.quantity}`)),
        h("div", { class: "stepper return-stepper" },
          h("button", { "aria-label": `Remove one ${product.SKU}`,
            onclick: () => adjustReplacement(product, -1) }, icon("i-minus")),
          h("span", { class: "return-qty-value" }, String(quantity)),
          h("button", { "aria-label": `Add one ${product.SKU}`,
            onclick: () => adjustReplacement(product, 1) }, icon("i-plus")))))
      : [h("p", { class: "form-hint" }, "No replacement items selected.")]));
  }
  function adjustReplacement(product, delta) {
    const n = (selected.get(product.id)?.quantity || 0) + delta;
    if (n <= 0) selected.delete(product.id);
    else if (n <= 1_000_000) selected.set(product.id, {product, quantity: n});
    drawReplacements();
    refreshPreview();
  }
  drawReplacements();

  async function findProducts() {
    const seq = ++searchSeq;
    const q = search.value.trim();
    if (!q) { productsList.replaceChildren(); return; }
    productsList.replaceChildren(h("p", { class: "form-hint" }, "Searching products…"));
    try {
      const options = (await api.listProducts({q, limit: 30})).slice(0, 30);
      if (seq !== searchSeq) return;
      productsList.replaceChildren(...(options.length
        ? options.map(p => h("button", { class: "btn", type: "button",
            onclick: () => { adjustReplacement(p, 1); search.value = ""; productsList.replaceChildren(); } },
            `${p.group_name || p.SKU} · ${p.SKU} · ${fmt(p.price)} · ${p.quantity} in stock`))
        : [h("p", { class: "form-hint" }, "No matching products.")]));
    } catch (e) { if (seq === searchSeq) { productsList.replaceChildren(); toastError(e); } }
  }
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(findProducts, 200);
  });
  search.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(searchTimer); findProducts(); }
  });

  function refreshPreview() {
    clearTimeout(previewTimer);
    const seq = ++generation;
    preview = null;
    submit.disabled = true;
    paymentBox.hidden = true;
    forfeitureAck.checked = false;
    forfeitureLabel.hidden = true;
    if (!returnLines().length || !replacementLines().length || (!customerId() && !canAnonymous)) {
      summary.replaceChildren(h("p", { class: "form-hint" },
        !customerId() && !canAnonymous ? "Store policy requires a customer for exchanges." :
          "Select the returned items and at least one replacement product."));
      return;
    }
    summary.replaceChildren(h("p", { class: "form-hint" }, "Recalculating prices and GST…"));
    previewTimer = setTimeout(async () => {
      try {
        const plan = await api.previewExchange(originalId, {
          returns: returnLines(), replacements: replacementLines(), customer_id: customerId(),
        });
        if (seq !== generation) return;
        preview = plan;
        summary.replaceChildren(
          h("div", { class: "return-summary-main" }, h("span", {}, "Returned goods"), h("strong", {}, fmt(plan.refund_total))),
          h("div", { class: "return-preview-detail" }, `Replacement invoice: ${fmt(plan.replacement_total)} (GST ${fmt(plan.replacement_tax)})`),
          h("div", { class: "return-preview-detail" }, plan.settlement_mode === "wallet"
            ? `Store credit from this return applied: ${fmt(plan.credit_applied)}`
            : `Exchange offset (no customer wallet): ${fmt(plan.exchange_offset)}`),
          h("div", { class: "return-summary-main" }, h("span", {}, "Collect extra"), h("strong", {}, fmt(plan.additional_due))),
          plan.remaining_credit > 0
            ? h("div", { class: "form-hint" }, `${fmt(plan.remaining_credit)} remains as customer store credit.`) : null,
          plan.forfeited_amount > 0
            ? h("div", { class: "return-error" }, `${fmt(plan.forfeited_amount)} will NOT be refunded or stored. The customer forfeits this difference.`) : null);
        forfeitureLabel.hidden = !(plan.forfeited_amount > 0);
        paymentBox.hidden = Number(plan.additional_due) === 0;
        tender.value = Number(plan.additional_due).toFixed(2);
        submit.disabled = !reason.value || (plan.forfeited_amount > 0 && !forfeitureAck.checked);
      } catch (e) {
        if (seq === generation) summary.replaceChildren(
          h("p", { class: "return-error" }, String(e.message || e)));
      }
    }, 180);
  }
  reason.addEventListener("change", () => {
    submit.disabled = !preview || !reason.value || (preview.forfeited_amount > 0 && !forfeitureAck.checked);
  });
  forfeitureAck.addEventListener("change", () => {
    submit.disabled = !preview || !reason.value || (preview.forfeited_amount > 0 && !forfeitureAck.checked);
  });
  paymentMethod.addEventListener("change", () => { utr.hidden = paymentMethod.value !== "UPI"; });
  submit.addEventListener("click", async () => {
    if (!preview || !reason.value || !returnLines().length || !replacementLines().length ||
        (preview.forfeited_amount > 0 && !forfeitureAck.checked)) return;
    let payments = [];
    if (Number(preview.additional_due) > 0) {
      const amount = Number(tender.value);
      if (!Number.isFinite(amount) || amount < Number(preview.additional_due) ||
          (paymentMethod.value !== "CASH" && amount !== Number(preview.additional_due))) {
        toast(`Enter the actual received amount. Card and UPI must equal ${fmt(preview.additional_due)}.`, "warn");
        return;
      }
      payments = [{method: paymentMethod.value, amount,
        ...(paymentMethod.value === "UPI" && utr.value.trim() ? {upi_utr: utr.value.trim()} : {})}];
    }
    const payload = {
      returns: returnLines(), replacements: replacementLines(), customer_id: customerId(),
      reason: returnReason(), payments,
      accept_forfeit: preview.forfeited_amount > 0 && forfeitureAck.checked,
    };
    const fingerprint = JSON.stringify(payload);
    if (!pendingId || fingerprint !== pendingFingerprint) {
      pendingId = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID() : `exchange_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      pendingFingerprint = fingerprint;
    }
    const confirmed = await confirmDialog({
      title: `Confirm exchange · ${fmt(preview.replacement_total)}`,
      message: `Return ${fmt(preview.refund_total)} in goods, issue a NEW invoice for ${fmt(preview.replacement_total)}. ` +
        (preview.settlement_mode === "wallet"
          ? `Apply ${fmt(preview.credit_applied)} from this return as customer credit; ` +
            `${fmt(preview.remaining_credit)} remains in customer ${customerId()}'s wallet. `
          : `Apply ${fmt(preview.exchange_offset)} as a direct exchange offset without customer credit or cash payout. `) +
        `Collect ${fmt(preview.additional_due)} in additional payment. ` +
        (preview.forfeited_amount > 0
          ? `WARNING: ${fmt(preview.forfeited_amount)} is forfeited, with no refund or store credit. ` : "") +
        "Verify goods, customer agreement and payment before continuing.",
      confirmLabel: "Complete exchange",
    });
    if (!confirmed) return;
    submit.disabled = true;
    try {
      const outcome = await api.exchangeSale(originalId, {...payload, request_id: pendingId});
      closeModalAll();
      toast(`Exchange recorded: refund ${outcome.refund.refund_number} and invoice ${outcome.replacement.invoice_number}.`,
        "success", { title: "Exchange completed" });
      if (done) await done(outcome);
      showReceiptModal(outcome.replacement);
    } catch (e) {
      toastError(e, "Exchange not confirmed");
      // Do not generate a new key after a network error: replay is idempotent.
    } finally {
      submit.disabled = !preview || !reason.value || (preview.forfeited_amount > 0 && !forfeitureAck.checked);
    }
  });

  openModal({
    title: `Exchange · ${original.invoice_number || `Sale #${original.id}`}`,
    wide: true,
    body: h("div", { class: "return-workflow" },
      context,
      h("h3", {}, "1. Items returned"), returnsList,
      h("h3", {}, "2. Replacement items"),
      h("div", { class: "search-wrap" }, icon("i-search"), search), productsList,
      replacementsList,
      original.customer_id ? h("p", { class: "form-hint" },
        `The original invoice belongs to customer ID ${original.customer_id}.`) :
        h("div", {},
          h("p", { class: "form-hint" }, canAnonymous
            ? "Customer optional. A walk-in exchange applies the returned amount directly to replacement goods; no store-credit balance is created. A cheaper replacement may require identifying a customer."
            : "Store policy requires customer identification for an exchange."),
          picker.root,
          canAnonymous ? h("button", { class: "btn ghost sm", onclick: () => picker.clear() },
            "Exchange without customer") : null),
      h("div", { class: "return-form-grid" },
        h("div", { class: "field" }, h("label", {}, "3. Reason for exchange"), reason),
        h("div", { class: "field" }, h("label", {}, "Detail"), reasonNote)),
      summary, forfeitureLabel, paymentBox,
      h("p", { class: "form-hint" },
        "The return uses the original tax and discounts. The replacement gets a separate invoice at current catalog prices and GST. This is not a statutory GST credit note. No payment gateway transfer occurs.")),
    footer: [h("button", { class: "btn", onclick: closeModalAll }, "Cancel"), submit],
  });
}
