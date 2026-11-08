import { api, fmt, fmtDate } from "../api.js";
import { h, openModal, toast, toastError, confirmDialog } from "../ui.js";
import { createSkuPicker } from "../skuPicker.js";

const field = (name, control) => h("div", { class: "field" }, h("label", {}, name), control);
const input = (hint, type = "text", value = "") => h("input", {
  class: "input", type, placeholder: hint, value, ...(type === "number" ? { min: "0", step: "0.01" } : {}),
});
const button = (label, fn, cls = "btn") => h("button", { class: cls, onclick: fn }, label);
const money = (c) => fmt((Number(c) || 0) / 100);
let view = null;

export async function renderPurchasing(main) {
  view = main;
  let suppliers, purchases;
  try { [suppliers, purchases] = await Promise.all([api.suppliers(), api.purchases()]); }
  catch (e) { main.replaceChildren(h("p", {}, e.message)); return toastError(e); }
  const refresh = () => renderPurchasing(main);
  const openAmount = purchases.reduce((n, p) => n + (p.total_cents - p.paid_cents), 0);
  main.replaceChildren(
    h("div", { class: "view-head" }, h("div", { class: "view-title" },
      h("h2", {}, "Purchasing"), h("div", { class: "sub" },
        "Supplier invoices · Stock receipts · Outstanding balances")),
      h("div", { class: "view-actions" },
        button("Add supplier", () => supplierModal(refresh)),
        button("Receive goods", () => receiveModal(suppliers, refresh), "btn primary"))),
    h("div", { class: "stat-grid purchasing-kpis" },
      metric("Suppliers", suppliers.length, "Editable supplier records"),
      metric("Receipts", purchases.length, "Recent purchase invoices"),
      metric("Outstanding payables", money(openAmount), "Recorded receipts less payments")),
    h("section", { class: "card section-card", "aria-label": "Purchase receipts" },
      h("div", { class: "section-card-head" },
        h("div", { class: "section-card-heading" },
          h("h3", {}, "Purchase receipts"),
          h("p", { class: "form-hint" }, "Inspect deliveries, record supplier payments and track outstanding amounts.")),
        h("span", { class: "pill muted" }, `${purchases.length} receipt${purchases.length === 1 ? "" : "s"}`)),
      h("div", { class: "section-card-body" },
        purchases.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
          h("thead", {}, h("tr", {}, ...["Supplier", "Invoice", "Received", "Total", "Outstanding", ""].map(t => h("th", {}, t)))),
          h("tbody", {}, ...purchases.map(p => h("tr", {},
            h("td", {}, p.supplier_name), h("td", {}, p.supplier_invoice),
            h("td", {}, fmtDate(p.received_at)),
            h("td", { class: "num" }, money(p.total_cents)),
            h("td", { class: "num" }, money(p.total_cents - p.paid_cents)),
            h("td", { class: "actions-cell" }, button("View", () => detailModal(p.id, refresh), "btn sm")))))))
        : h("div", { class: "section-empty" },
          h("strong", {}, "No supplier receipts yet"),
          h("p", { class: "form-hint" }, "Create a supplier, then record goods only when an actual shipment arrives."),
          h("div", { class: "section-empty-actions" },
            button("Add supplier", () => supplierModal(refresh)),
            button("Receive goods", () => receiveModal(suppliers, refresh), "btn primary"))))),
    h("p", { class: "report-note" },
      "Unit costs are entered from actual supplier invoices. Input GST is stored only as documented; " +
      "this page does not claim ITC eligibility or generate GSTR-2B reconciliations."));
}

function metric(label, value, hint) {
  return h("div", { class: "stat-card" }, h("div", { class: "s-label" }, label),
    h("div", { class: "s-value" }, String(value)), h("div", { class: "s-hint" }, hint));
}

function supplierModal(refresh) {
  const name = input("Supplier name");
  const phone = input("Optional phone");
  const gstin = input("Optional GSTIN");
  const address = input("Optional address");
  let m;
  m = openModal({ title: "New supplier", narrow: true,
    body: h("div", {}, field("Name *", name), field("Phone", phone),
      field("GSTIN (if applicable)", gstin), field("Address", address)),
    footer: [button("Cancel", () => m.close()), button("Save supplier", async () => {
      try { await api.createSupplier({name:name.value,phone:phone.value,gstin:gstin.value,address:address.value});
        m.close(); toast("Supplier saved.", "success"); refresh(); }
      catch (e) { toastError(e); }
    }, "btn primary")],
  });
}

async function receiveModal(suppliers, refresh) {
  if (!suppliers.length) { toast("Create a supplier first.", "warn"); return; }
  let products;
  try { products = await api.listProducts(); } catch (e) { return toastError(e); }
  const supplier = h("select", { class: "input" },
    ...suppliers.map(s => h("option", { value: s.id }, s.name)));
  const invoice = input("Exactly as on the supplier's invoice");
  const invoiceGst = input("From invoice, if stated", "number");
  const note = input("Delivery note or challan reference");
  const updateCost = h("input", { type: "checkbox" });
  const lineHost = h("div", {});
  const rows = [];
  function addLine() {
    const qty = h("input", { class: "input num", type: "number", step: "1", min: "1", value: "1" });
    const picker = createSkuPicker(products, {
      placeholder: "Scan EAN, SKU or find a product…",
      onSelect: () => qty.focus(),
    });
    const unit = input("Landed price per unit ₹", "number");
    const r = {};
    const el = h("div", { class: "purchase-line-grid" },
      field("Exact SKU / MRP edition", picker.element), field("Quantity", qty), field("Unit landed cost ₹", unit),
      button("Remove", () => { el.remove(); rows.splice(rows.indexOf(r), 1); }, "btn sm"));
    Object.assign(r, { picker, qty, unit, el });
    rows.push(r); lineHost.append(el);
  }
  addLine();
  let m;
  m = openModal({ title: "Receive supplier goods", wide: true,
    body: h("div", {},
      h("p", { class: "form-hint" }, "Use exact SKUs. Multiple MRPs sharing an EAN must be received into the correct price edition. " +
        "Amounts are actual landed/unit purchase costs including any GST, not selling MRPs."),
      field("Supplier", supplier), field("Supplier invoice number *", invoice),
      field("Invoice GST ₹ (informational, optional)", invoiceGst), field("Reference / notes", note),
      lineHost, button("+ Add SKU line", addLine, "btn sm"),
      h("label", { style: "display:flex;gap:8px;margin-top:15px" }, updateCost,
        "Also update current catalogue cost from this receipt (historical sale costs remain unchanged)")),
    footer: [button("Cancel", () => m.close()), button("Receive stock", async () => {
      const lines = rows.map(r => ({ product_id: r.picker.getSelected()?.id,
        quantity: r.qty.value, unit_cost: r.unit.value }));
      if (!lines.length || lines.some(l => !l.product_id))
        return toast("Select the exact SKU / MRP edition for every receipt line.", "warn");
      if (lines.some(l => !/^[1-9]\d*$/.test(l.quantity) || !Number.isSafeInteger(Number(l.quantity))))
        return toast("Receipt quantities must be positive whole units.", "warn");
      if (lines.some(l => !/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(l.unit_cost)))
        return toast("Enter each unit's actual landed cost in rupees (max. two decimals).", "warn");
      if (!await confirmDialog({ title: "Confirm stock receipt", message:
        `This will ADD the quantities of ${lines.length} SKU(s). Supplier invoice numbers cannot be received twice.`,
        confirmLabel: "Receive goods" })) return;
      try { await api.receivePurchase({supplier_id: Number(supplier.value),
        supplier_invoice: invoice.value, note: note.value,
        invoice_gst: invoiceGst.value || null, update_catalog_cost: updateCost.checked, lines});
        m.close(); toast("Supplier stock received and audited.", "success"); refresh(); }
      catch (e) { toastError(e); }
    }, "btn primary")],
  });
}

async function detailModal(id, refresh) {
  let rec;
  try { rec = await api.purchaseDetail(id); } catch (e) { return toastError(e); }
  let m;
  const owing = rec.outstanding_cents;
  m = openModal({ title: `Supplier invoice ${rec.supplier_invoice}`, wide: true,
    body: h("div", {},
      h("p", {}, `${rec.supplier_name} · ${fmtDate(rec.received_at)}`),
      h("div", { class: "stat-grid" }, metric("Invoice total", money(rec.total_cents), "Landed costs"),
        metric("Paid", money(rec.paid_cents), "Recorded payments"), metric("Outstanding", money(owing), "Remaining")),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ...["SKU", "Qty", "Unit cost", "Total"].map(t => h("th", {}, t)))),
        h("tbody", {}, ...rec.lines.map(l => h("tr", {},
          h("td", {}, l.sku_at_receipt), h("td", {}, String(l.quantity)),
          h("td", { class: "num" }, money(l.unit_cost_cents)),
          h("td", { class: "num" }, money(l.unit_cost_cents * l.quantity))))))),
      rec.invoice_gst_cents !== null ? h("p", { class: "form-hint" },
        `Supplier-stated invoice GST (information only): ${money(rec.invoice_gst_cents)}`) : null),
    footer: [button("Close", () => m.close()),
      owing ? button("Record payment", () => {m.close(); paymentModal(rec, refresh);}, "btn primary") : null].filter(Boolean),
  });
}

function paymentModal(rec, refresh) {
  const amount = input("Amount ₹", "number", (rec.outstanding_cents / 100).toFixed(2));
  const method = h("select", { class: "input" }, ...["CASH", "UPI", "CARD", "BANK", "OTHER"].map(v =>
    h("option", { value: v }, v)));
  const reference = input("Transaction / voucher reference");
  const requestId = `supplier_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  let m;
  m = openModal({ title: `Pay supplier · ${rec.supplier_invoice}`, narrow: true,
    body: h("div", {}, h("p", {}, `Outstanding: ${money(rec.outstanding_cents)}`),
      field("Payment amount ₹", amount), field("Method", method), field("Reference", reference),
      h("p", { class: "form-hint" }, "Cash payments reduce the open drawer's expected balance; bank/UPI/card payments do not.")),
    footer: [button("Cancel", () => m.close()), button("Record payment", async () => {
      try { await api.paySupplier(rec.id, { amount: amount.value, method: method.value,
        reference: reference.value, request_id: requestId });
        m.close(); toast("Supplier payment recorded.", "success"); refresh(); }
      catch (e) { toastError(e); }
    }, "btn primary")],
  });
}
