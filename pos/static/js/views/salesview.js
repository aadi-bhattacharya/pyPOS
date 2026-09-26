import { api, fmt, fmtDate } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, confirmDialog, printReceipt } from "../ui.js";
import { buildReceipt, methodLabel } from "../receipt.js";
import { showReceiptModal } from "./register.js";

let rows = [];
let preset = "7";
let statusFilter = "";
let tableEl = null;

const PRESETS = [
  ["today", "Today"],
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["all", "All time"],
];

function dateRange() {
  if (preset === "all") return {};
  const to = new Date();
  let from = new Date();
  if (preset === "today") {
    // same day
  } else {
    from.setDate(from.getDate() - (Number(preset) - 1));
  }
  return {
    from_date: isoLocal(from),
    to_date: isoLocal(to),
  };
}

function isoLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export async function renderSales(main) {
  main.innerHTML = "";

  const statusSel = h("select", {
    class: "input", style: "width:auto",
    onchange: () => { statusFilter = statusSel.value; loadRows(); },
  },
    h("option", { value: "" }, "All statuses"),
    h("option", { value: "COMPLETED" }, "Completed"),
    h("option", { value: "REFUNDED" }, "Refunds"),
    h("option", { value: "VOID" }, "Voided"));

  statusSel.value = statusFilter;

  const presetChips = h("div", { class: "chips" });
  const renderPresetChips = () => {
    presetChips.innerHTML = "";
    for (const [value, label] of PRESETS) {
      presetChips.append(h("button", {
        class: `chip${preset === value ? " active" : ""}`,
        onclick: () => { preset = value; renderPresetChips(); loadRows(); },
      }, label));
    }
  };
  renderPresetChips();

  tableEl = h("div", {});

  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Sales"),
        h("div", { class: "sub", id: "sales-sub" })),
      h("div", { class: "view-actions" },
        statusSel,
        h("button", { class: "btn ghost icon-only", title: "Refresh",
          onclick: () => loadRows() }, icon("i-refresh")))),
    h("div", { style: "margin-bottom:14px" }, presetChips),
    tableEl,
  );

  await loadRows();
}

async function loadRows() {
  tableEl.innerHTML = "";
  tableEl.append(h("div", { class: "skel-rows", style: "padding:10px" },
    ...Array.from({ length: 8 }, () => h("div", { class: "skeleton" }))));

  const params = { ...dateRange(), limit: 500 };
  if (statusFilter) params.status = statusFilter;

  try {
    rows = await api.listSales(params);
  } catch (e) {
    toastError(e);
    rows = [];
  }

  const sub = document.getElementById("sales-sub");
  if (sub) sub.textContent = `${rows.length} transaction${rows.length === 1 ? "" : "s"} in range`;

  tableEl.innerHTML = "";

  if (!rows.length) {
    tableEl.append(h("div", { class: "empty-state card" },
      icon("i-sales"),
      h("h3", {}, "No sales in this period"),
      h("p", {}, "Completed checkouts will appear here.")));
    return;
  }

  const total = rows.filter((r) => r.status === "COMPLETED")
    .reduce((a, r) => a + r.total, 0);
  tableEl.append(
    h("div", { style: "display:flex;justify-content:flex-end;margin-bottom:8px" },
      h("span", { class: "pill muted" },
        `Net completed: ${fmt(total)}`)),
    h("div", { class: "table-wrap" },
      h("table", { class: "table" },
        h("thead", {}, h("tr", {},
          h("th", {}, "Receipt"), h("th", {}, "When"), h("th", { class: "num" }, "Items"),
          h("th", {}, "Paid via"), h("th", { class: "num" }, "Total"),
          h("th", {}, "Status"), h("th", {}))),
        h("tbody", {}, rows.map(rowHtml)))));
}

function rowHtml(r) {
  const methods = [...new Set((r.methods || "").split(",").filter(Boolean))]
    .map(methodLabel).join(", ");

  const actions = [];
  const canRefund =
    r.status === "COMPLETED" && (!r.refunded || r.partially_refunded);
  if (canRefund) {
    actions.push(
      actionBtn("i-undo", "Refund sale", () => refundDialog(r.id, () => {})));
  }
  if (r.status === "COMPLETED") {
    actions.push(actionBtn("i-ban", "Void sale", () => voidFlow(r)));
  }
  actions.unshift(actionBtn("i-eye", "View details", () => detailModal(r.id)));

  return h("tr", {},
    h("td", {}, h("div", { class: "cell-main" },
      `#${String(r.id).padStart(5, "0")}`),
      r.parent_sale_id ? h("div", { class: "cell-sub" },
        `refund of #${String(r.parent_sale_id).padStart(5, "0")}`) : null,
      r.customer_name ? h("div", { class: "cell-sub" }, r.customer_name) : null),
    h("td", {}, fmtDate(r.timestamp)),
    h("td", { class: "num" }, String(r.item_count ?? 0)),
    h("td", { class: "cell-sub" }, methods || "—"),
    h("td", { class: "num cell-main" }, fmt(r.total)),
    h("td", {}, statusPill(r)),
    h("td", { class: "actions-cell" }, ...actions));
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

async function detailModal(id) {
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
    title: `Sale #${String(sale.id).padStart(5, "0")}`,
    narrow: true,
    body: h("div", {},
      receiptEl,
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
        onclick: () => voidFlow(sale, () => {}),
      }, icon("i-ban"), "Void") : null,
      canAct ? h("button", {
        class: "btn primary",
        onclick: () => refundDialog(sale.id, () => {}),
      }, icon("i-undo"), "Refund") : null,
    ],
  });
}

// ================= refunds (partial-aware) =================

async function refundDialog(saleId, done) {
  let sale;
  try {
    sale = await api.getSale(saleId);
  } catch (e) {
    return toastError(e);
  }

  const refundable = sale.items.filter((it) => it.refundable_qty > 0);
  if (!refundable.length) {
    return toast("Nothing left to refund on this sale.", "warn");
  }

  // qty selectors per line, prefilled to "refund everything"
  const qtys = new Map(refundable.map((it) => [it.product_id, it.refundable_qty]));
  const rowsEl = h("div", { class: "refund-lines" });
  const totalEl = h("div", { class: "pay-total-banner" },
    h("span", { class: "lbl" }, "Refund amount"),
    h("span", { class: "amt" }, fmt(0)));

  const creditOpt = sale.customer_id
    ? h("label", { class: "credit-toggle" },
        h("input", { type: "checkbox", onchange: (e) => { toCredit = e.target.checked; } }),
        h("span", {}, `Refund to ${sale.customer_name}'s store credit`))
    : null;
  let toCredit = false;

  function moneyFor(it, k) {
    const gross = Number(it.unit_price) * k;
    return Math.round(gross * 100) / 100;
  }

  function updateTotal() {
    let cents = 0;
    for (const it of refundable) {
      const k = qtys.get(it.product_id) || 0;
      if (!k) continue;
      // mirror server math: proportional share of the line's recorded
      // discount + tax is not shown here; preview uses gross as a floor.
      cents += Math.round(moneyFor(it, k) * 100);
    }
    totalEl.querySelector(".amt").textContent =
      fmt(cents / 100) + (toCredit ? " → store credit" : "");
  }
  updateTotal();

  for (const it of refundable) {
    const qtySpan = h("span", { class: "st-qty" }, String(it.refundable_qty));
    const row = h("div", { class: "refund-line" },
      h("div", { class: "cl-info" },
        h("div", { class: "cl-name" }, it.name),
        h("div", { class: "cl-sub" },
          `${fmt(it.unit_price)} each · sold ${it.quantity}` +
          (it.refunded_qty ? ` · refunded ${it.refunded_qty}` : ""))),
      h("div", { class: "stepper" },
        h("button", {
          onclick: () => bump(it, -1),
        }, icon("i-minus")),
        qtySpan,
        h("button", {
          onclick: () => bump(it, +1),
        }, icon("i-plus"))));
    rowsEl.append(row);

    function bump(it, delta) {
      const next = Math.min(Math.max((qtys.get(it.product_id) || 0) + delta, 0),
        it.refundable_qty);
      qtys.set(it.product_id, next);
      qtySpan.textContent = String(next);
      updateTotal();
    }
  }

  const goBtn = h("button", { class: "btn primary lg" },
    icon("i-undo"), "Issue refund");
  goBtn.addEventListener("click", async () => {
    const lines = refundable
      .filter((it) => (qtys.get(it.product_id) || 0) > 0)
      .map((it) => ({ product_id: it.product_id,
                      quantity: qtys.get(it.product_id) }));
    if (!lines.length) return toast("Pick at least one unit to refund.", "warn");
    goBtn.disabled = true;
    try {
      const refund = await api.refundSale(saleId, { lines, to_credit: toCredit });
      toast(`Refund #${refund.id} of ${fmt(refund.total)} recorded.`, "success",
        { title: "Refunded" });
      closeModalAll();
      await loadRows();
      showReceiptModal(refund);
    } catch (e) {
      toastError(e);
      goBtn.disabled = false;
    }
    if (done) done();
  });

  openModal({
    title: `Refund #${String(saleId).padStart(5, "0")} — choose items`,
    body: h("div", {},
      totalEl,
      h("p", { class: "form-hint", style: "margin-bottom:10px" },
        "Dial back quantities to make a partial refund. Refunded units go back to stock."),
      rowsEl,
      creditOpt),
    footer: [
      h("button", { class: "btn", onclick: () => closeModalAll() }, "Cancel"),
      goBtn,
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
    await loadRows();
  } catch (e) {
    toastError(e);
  }
  if (done) done();
}
