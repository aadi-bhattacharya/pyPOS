import { api, fmt, fmtDate, state } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, confirmDialog } from "../ui.js";

let customers = [];
let searchTerm = "";
let tableEl = null;

export async function renderCustomers(main) {
  main.innerHTML = "";
  main.append(h("div", { class: "skel-rows" },
    ...Array.from({ length: 6 }, () => h("div", { class: "skeleton" }))));

  await load();

  const searchInput = h("input", {
    class: "input",
    placeholder: "Search name / phone / email…",
    oninput: () => { searchTerm = searchInput.value.trim(); renderTable(); },
  });
  tableEl = h("div", {});

  main.innerHTML = "";
  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Customers"),
        h("div", { class: "sub", id: "cust-sub" })),
      h("div", { class: "view-actions" },
        h("button", { class: "btn primary", onclick: () => customerModal() },
          icon("i-plus"), "New customer"))),
    h("div", { class: "search-wrap", style: "margin-bottom:12px;max-width:340px" },
      icon("i-search"), searchInput),
    tableEl,
  );

  renderSub();
  renderTable();
}

async function load() {
  try {
    customers = await api.listCustomers();
  } catch (e) {
    toastError(e);
    customers = [];
  }
}

function visible() {
  if (!searchTerm) return customers;
  const q = searchTerm.toLowerCase();
  return customers.filter((c) =>
    (c.name || "").toLowerCase().includes(q) ||
    (c.phone || "").toLowerCase().includes(q) ||
    (c.email || "").toLowerCase().includes(q));
}

function renderSub() {
  const el = document.getElementById("cust-sub");
  if (el) {
    const owing = customers.filter((c) => c.store_credit > 0).length;
    el.textContent = `${customers.length} customer${customers.length === 1 ? "" : "s"}` +
      (owing ? ` · ${owing} with store credit` : "");
  }
}

function renderTable() {
  tableEl.innerHTML = "";
  const list = visible();

  if (!list.length) {
    tableEl.append(h("div", { class: "empty-state card" },
      icon("i-users"),
      h("h3", {}, customers.length ? "No one matches that search" : "No customers yet"),
      h("p", {}, customers.length
        ? "Try a different name, phone or email."
        : "Add regulars to track purchases and hand out store credit."),
      !customers.length
        ? h("button", { class: "btn primary", onclick: () => customerModal() },
            icon("i-plus"), "New customer")
        : null));
    return;
  }

  tableEl.append(h("div", { class: "table-wrap" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Customer"),
        h("th", { class: "num" }, "Store credit"),
        h("th", { class: "num" }, "Orders"),
        h("th", { class: "num" }, "Total spent"),
        h("th", {}))),
      h("tbody", {}, list.map(rowHtml)))));
}

function rowHtml(c) {
  return h("tr", {},
    h("td", {},
      h("div", {
        class: "cell-main", style: "cursor:pointer;color:var(--accent)",
        onclick: () => detailModal(c.id),
      }, c.name),
      h("div", { class: "cell-sub" },
        [c.phone, c.email].filter(Boolean).join(" · ") || "—")),
    h("td", { class: "num cell-main mono" },
      c.store_credit > 0
        ? h("span", { class: "pill ok" }, fmt(c.store_credit))
        : h("span", { style: "color:var(--muted)" }, fmt(0))),
    h("td", { class: "num" }, String(c.orders)),
    h("td", { class: "num" }, fmt(c.total_spent)),
    h("td", { class: "actions-cell" },
      h("button", {
        class: "btn ghost icon-only sm", title: "Adjust credit",
        onclick: () => creditModal(c),
      }, icon("i-cash")),
      h("button", {
        class: "btn ghost icon-only sm", title: "Edit",
        onclick: () => customerModal(c),
      }, icon("i-edit")),
      h("button", {
        class: "btn ghost icon-only sm", title: "Delete",
        onclick: () => deleteFlow(c),
      }, icon("i-trash"))));
}

// ================= modals =================

function customerModal(existing = null) {
  const name = h("input", { class: "input", value: existing?.name || "", placeholder: "Full name" });
  const phone = h("input", { class: "input", value: existing?.phone || "", placeholder: "Phone (optional)" });
  const email = h("input", { class: "input", type: "email", value: existing?.email || "", placeholder: "Optional" });
  const notes = h("textarea", { class: "input", rows: "2", placeholder: "Optional" }, existing?.notes || "");

  const saveBtn = h("button", { class: "btn primary" },
    icon("i-check"), existing ? "Save changes" : "Add customer");
  saveBtn.addEventListener("click", async () => {
    const payload = {
      name: name.value, phone: phone.value,
      email: email.value, notes: notes.value,
    };
    try {
      if (existing) {
        await api.updateCustomer(existing.id, payload);
        toast(`${payload.name} updated.`, "success");
      } else {
        await api.createCustomer(payload);
        toast(`${payload.name} added.`, "success");
      }
      m.close();
      await load();
      renderSub();
      renderTable();
    } catch (e) {
      toastError(e);
    }
  });

  const m = openModal({
    title: existing ? `Edit ${existing.name}` : "New customer",
    narrow: true,
    body: h("div", { class: "form-grid" },
      h("div", { class: "field" }, h("label", {}, "Name *"), name),
      h("div", { class: "field" }, h("label", {}, "Phone"), phone),
      h("div", { class: "field" }, h("label", {}, "Email"), email),
      h("div", { class: "field" }, h("label", {}, "Notes"), notes)),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      saveBtn,
    ],
  });
}

function creditModal(c) {
  const amount = h("input", {
    class: "input num", type: "number", step: "0.01", min: "0",
    placeholder: "0.00", style: "flex:1",
  });
  let direction = 1;
  const dirBtn = h("button", {
    class: "btn sm",
    onclick: () => {
      direction = -direction;
      dirBtn.replaceChildren(
        icon(direction === 1 ? "i-plus" : "i-minus"),
        direction === 1 ? " Add credit" : " Deduct credit");
    },
  }, icon("i-plus"), " Add credit");
  const reason = h("input", {
    class: "input", placeholder:
      direction === 1 ? "e.g. loyalty top-up" : "e.g. correction",
  });

  const applyBtn = h("button", { class: "btn primary" }, "Apply");
  applyBtn.addEventListener("click", async () => {
    const val = Math.round(parseFloat(amount.value || "0") * 100);
    if (!val || val <= 0) return toast("Enter an amount first.", "warn");
    try {
      const res = await api.adjustCredit(c.id, direction * val, reason.value.trim());
      toast(`Balance for ${c.name}: ${fmt(res.store_credit)}`, "success",
        { title: "Store credit updated" });
      m.close();
      await load();
      renderSub();
      renderTable();
    } catch (e) {
      toastError(e);
    }
  });

  const m = openModal({
    title: `Store credit — ${c.name}`,
    narrow: true,
    body: h("div", {},
      h("div", { class: "pay-total-banner" },
        h("span", { class: "lbl" }, "Current balance"),
        h("span", { class: "amt" }, fmt(c.store_credit))),
      h("div", { class: "field" },
        h("label", {}, "Amount"),
        h("div", { style: "display:flex;gap:8px;align-items:center" },
          h("span", { class: "mono", style: "font-weight:700" },
            state.settings.currency || "$"),
          amount,
          dirBtn)),
      h("div", { class: "field" }, h("label", {}, "Reason"), reason)),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      applyBtn,
    ],
  });
}

async function deleteFlow(c) {
  const ok = await confirmDialog({
    title: `Delete ${c.name}?`,
    message: "Their purchase history stays in sales reports, but the customer " +
      "record and credit log are removed. Customers holding store credit cannot be deleted.",
    confirmLabel: "Delete customer",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.deleteCustomer(c.id);
    toast(`${c.name} deleted.`, "success");
    await load();
    renderSub();
    renderTable();
  } catch (e) {
    toastError(e);
  }
}

async function detailModal(id) {
  let c;
  try {
    c = await api.getCustomer(id);
  } catch (e) {
    return toastError(e);
  }

  const creditRows = c.credit_events.map((ev) =>
    h("tr", {},
      h("td", {}, fmtDate(ev.timestamp)),
      h("td", {}, ev.reason ||
        (ev.sale_id ? `Sale #${String(ev.sale_id).padStart(5, "0")}` : "Adjustment")),
      h("td", { class: "num mono", style: "color:" + (ev.delta >= 0 ? "var(--ok)" : "var(--danger)") },
        (ev.delta >= 0 ? "+" : "") + fmt(ev.delta))));

  const saleRows = c.recent_sales.map((s) =>
    h("tr", {},
      h("td", {}, h("a", {
        href: "#/sales",
        style: "color:var(--accent);text-decoration:none",
      }, `#${String(s.id).padStart(5, "0")}`)),
      h("td", {}, fmtDate(s.timestamp)),
      h("td", { class: "num mono" }, fmt(s.total)),
      h("td", {}, s.status === "COMPLETED"
        ? h("span", { class: "pill ok" }, "SALE")
        : s.status === "REFUNDED"
          ? h("span", { class: "pill warn" }, "REFUND")
          : h("span", { class: "pill bad" }, "VOID"))));

  openModal({
    title: c.name,
    body: h("div", {},
      h("div", { class: "pay-total-banner" },
        h("span", { class: "lbl" }, "Store credit"),
        h("span", { class: "amt" }, fmt(c.store_credit))),
      h("div", { style: "display:flex;gap:18px;margin-bottom:12px;font-size:13px;color:var(--text-2)" },
        h("span", {}, h("strong", {}, String(c.orders)), " orders"),
        h("span", {}, h("strong", {}, fmt(c.total_spent)), " lifetime")),
      (c.phone || c.email || c.notes)
        ? h("div", { class: "card", style: "padding:10px 14px;margin-bottom:14px;font-size:13px" },
            c.phone ? h("div", {}, c.phone) : null,
            c.email ? h("div", {}, c.email) : null,
            c.notes ? h("div", { style: "color:var(--text-2)" }, c.notes) : null)
        : null,
      h("h3", { style: "margin-bottom:6px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)" },
        "Credit history"),
      creditRows.length
        ? h("div", { class: "table-wrap", style: "max-height:180px;margin-bottom:14px" },
            h("table", { class: "table" }, creditRows))
        : h("p", { style: "color:var(--muted);font-size:13px;margin-bottom:14px" }, "No credit activity yet."),
      h("h3", { style: "margin-bottom:6px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)" },
        "Recent purchases"),
      saleRows.length
        ? h("div", { class: "table-wrap", style: "max-height:220px" },
            h("table", { class: "table" }, saleRows))
        : h("p", { style: "color:var(--muted);font-size:13px" }, "No purchases yet.")),
    footer: [
      h("button", {
        class: "btn",
        onclick: () => { closeModalAll(); creditModal({ ...c }); },
      }, icon("i-cash"), "Adjust credit"),
      h("button", {
        class: "btn primary",
        onclick: () => { closeModalAll(); customerModal(c); },
      }, icon("i-edit"), "Edit"),
    ],
  });
}
