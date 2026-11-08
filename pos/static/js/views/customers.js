import { api, can, fmt, fmtDate, state } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, confirmDialog } from "../ui.js";

let customers = [];
let searchTerm = "";
let tableEl = null;
let customerStatsEl = null;
let customerFilter = "all";
let customerSort = "name";
let overview = { total_customers: 0, outstanding_points: 0, point_holders: 0,
  outstanding_credit_paise: 0 };
let customerSearchTimer = null;
let customerFetchSeq = 0;

async function updateCustomerPage() {
  await load();
  renderSub();
  renderCustomerStats();
  renderTable();
}

export async function renderCustomers(main) {
  main.innerHTML = "";
  main.append(h("div", { class: "skel-rows" },
    ...Array.from({ length: 6 }, () => h("div", { class: "skeleton" }))));

  await load();

  const searchInput = h("input", {
    class: "input",
    placeholder: "Search name / phone / email…",
    value: searchTerm,
    oninput: () => {
      searchTerm = searchInput.value.trim();
      clearTimeout(customerSearchTimer);
      customerSearchTimer = setTimeout(updateCustomerPage, 220);
    },
  });
  tableEl = h("div", {});
  customerStatsEl = h("div", { class: "customer-stats", "aria-label": "Customer loyalty overview" });
  const filterChips = h("div", { class: "chips", role: "group", "aria-label": "Customer filters" });
  function buildFilters() {
    filterChips.replaceChildren(...[
      ["all", "All customers"], ["points", "With points"],
      ["credit", "With store credit"],
    ].map(([value, label]) => h("button", {
      class: `chip${customerFilter === value ? " active" : ""}`,
      "aria-pressed": customerFilter === value ? "true" : "false",
      onclick: () => { customerFilter = value; buildFilters(); updateCustomerPage(); },
    }, label)));
  }
  buildFilters();
  const sort = h("select", { class: "input", "aria-label": "Sort customers",
    onchange: () => { customerSort = sort.value; renderTable(); } },
    h("option", { value: "name" }, "Sort: Name"),
    h("option", { value: "points" }, "Sort: Most points"),
    h("option", { value: "spent" }, "Sort: Highest spend"));
  sort.value = customerSort;

  main.innerHTML = "";
  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Customers"),
        h("div", { class: "sub", id: "cust-sub" })),
      h("div", { class: "view-actions" },
        can("export_data") ? h("a", { class: "btn", href: "/api/export/loyalty.csv",
          title: "Download loyalty events as CSV" }, "Loyalty CSV") : null,
        can("manage_customers") ? h("button", { class: "btn primary", onclick: () => customerModal() },
          icon("i-plus"), "New customer") : null)),
    customerStatsEl,
    h("div", { class: "customer-toolbar" },
      h("div", { class: "search-wrap" }, icon("i-search"), searchInput),
      filterChips, sort),
    tableEl,
  );

  renderSub();
  renderCustomerStats();
  renderTable();
}

async function load() {
  const seq = ++customerFetchSeq;
  try {
    const [list, info] = await Promise.all([
      api.listCustomers(searchTerm, customerFilter), api.customerOverview(),
    ]);
    if (seq !== customerFetchSeq) return;
    customers = list;
    overview = info;
  } catch (e) {
    if (seq !== customerFetchSeq) return;
    toastError(e);
    customers = [];
  }
}

function visible() {
  const q = searchTerm.toLowerCase();
  const result = customers.filter((c) =>
    (!q || (c.name || "").toLowerCase().includes(q) ||
      (c.phone || "").toLowerCase().includes(q) ||
      (c.email || "").toLowerCase().includes(q)) &&
    (customerFilter === "all" ||
      (customerFilter === "points" && Number(c.loyalty_points) > 0) ||
      (customerFilter === "credit" && Number(c.store_credit) > 0)));
  if (customerSort === "points") result.sort((a, b) =>
    Number(b.loyalty_points) - Number(a.loyalty_points) || a.name.localeCompare(b.name));
  else if (customerSort === "spent") result.sort((a, b) =>
    Number(b.total_spent) - Number(a.total_spent) || a.name.localeCompare(b.name));
  else result.sort((a, b) => a.name.localeCompare(b.name));
  return result;
}

function renderSub() {
  const el = document.getElementById("cust-sub");
  if (el) el.textContent = `${Number(overview.total_customers).toLocaleString("en-IN")} registered customers · ` +
    `${customers.length}${customers.length >= 500 ? "+" : ""} displayed${searchTerm ? " for this search" : ""}`;
}

function renderCustomerStats() {
  if (!customerStatsEl) return;
  const points = Number(overview.outstanding_points || 0);
  const credit = Number(overview.outstanding_credit_paise || 0) / 100;
  const holders = Number(overview.point_holders || 0);
  const unit = Number(state.settings.loyalty_redeem_paise || 100) / 100;
  const card = (label, val) => h("div", {}, h("span", { class: "label" }, label),
    h("strong", {}, val));
  customerStatsEl.replaceChildren(
    card("Registered customers", Number(overview.total_customers || 0).toLocaleString("en-IN")),
    card("Outstanding loyalty points", `${points.toLocaleString("en-IN")} pts`),
    card("Customers holding points", holders.toLocaleString("en-IN")),
    card("Store credit outstanding", fmt(credit)));
  customerStatsEl.title = `All registered customers, including those not listed on this page. ` +
    `Current conversion rate: ${fmt(unit)} per point. Points are not store credit until redeemed.`;
}

function renderTable() {
  if (!tableEl) return;
  tableEl.innerHTML = "";
  const list = visible();

  if (!list.length) {
    tableEl.append(h("div", { class: "empty-state card" },
      icon("i-users"),
      h("h3", {}, Number(overview.total_customers) ? "No matching customers" : "No customers yet"),
      h("p", {}, Number(overview.total_customers)
        ? "Try another search term or switch back to All customers."
        : "Add customers to track loyalty points, purchases and store credit."),
      !Number(overview.total_customers)
        ? h("button", { class: "btn primary", onclick: () => customerModal() },
            icon("i-plus"), "New customer")
        : null));
    return;
  }

  tableEl.append(
    customers.length >= 500 ? h("div", { class: "report-note", style: "margin-bottom:8px" },
      "Showing the first 500 matches. Search by customer name or phone to find other records. " +
      "The summary above covers every customer, not just the displayed rows.") : null,
    h("div", { class: "table-wrap" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Customer"),
        h("th", { class: "num" }, "Store credit"),
        h("th", { class: "num" }, "Loyalty points"),
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
    h("td", { class: "num cell-main mono" },
      h("button", { class: "customer-points-btn",
        title: `View / manage ${c.name}'s loyalty points`,
        onclick: () => loyaltyModal(c) },
        `${Number(c.loyalty_points || 0).toLocaleString("en-IN")} pts`)),
    h("td", { class: "num" }, String(c.orders)),
    h("td", { class: "num" }, fmt(c.total_spent)),
    h("td", { class: "actions-cell" },
      can("manage_loyalty") ? h("button", {
        class: "btn ghost sm", title: "Manage loyalty points",
        onclick: () => loyaltyModal(c),
      }, "Points") : null,
      can("manage_credit") ? h("button", {
        class: "btn ghost icon-only sm", title: "Adjust credit",
        onclick: () => creditModal(c),
      }, icon("i-cash")) : null,
      can("manage_customers") ? h("button", {
        class: "btn ghost icon-only sm", title: "Edit",
        onclick: () => customerModal(c),
      }, icon("i-edit")) : null,
      can("delete_customers") ? h("button", {
        class: "btn ghost icon-only sm", title: "Delete",
        onclick: () => deleteFlow(c),
      }, icon("i-trash")) : null));
}

// ================= modals =================

async function loyaltyModal(c) {
  let auth = { configured: true, authenticated: true };
  if (state.staff?.operator?.role === "admin") {
    try { auth = await api.settingsAuth(); } catch (e) { return toastError(e); }
  }
  if (!can("manage_loyalty")) return toast("Your account cannot manage loyalty points.", "warn");
  if (!auth.configured) {
    return toast("Set an admin password in Settings before managing loyalty points.", "warn");
  }
  if (!auth.authenticated) {
    toast("Admin login required to manage points.", "warn");
    location.href = "/login?next=" + encodeURIComponent("/#/customers");
    return;
  }
  const latest = await api.getCustomer(c.id).catch(() => c);
  const amount = h("input", { class: "input num", type: "number", step: "1", min: "0",
    max: "1000000", placeholder: "Whole points" });
  const action = h("select", { class: "input" },
    ...[["add", "Add points"], ["deduct", "Deduct points"],
         ["set", "Set exact balance"], ["redeem", "Convert points to store credit"]].map(([v, name]) =>
      h("option", { value: v }, name)));
  const reason = h("input", { class: "input", maxlength: "240",
    placeholder: "Required reason, e.g. goodwill adjustment" });
  const rate = Number(state.settings.loyalty_redeem_paise || 100) / 100;
  const helper = h("p", { style: "font-size:12.5px;color:var(--text-2);margin:8px 0" });
  const submit = h("button", { class: "btn primary" }, "Apply change");
  function sync() {
    const redeem = action.value === "redeem";
    reason.parentElement.style.display = redeem ? "none" : "";
    if (redeem) {
      helper.textContent = `Each point converts to ${fmt(rate)} of store credit. This is not a GST discount. ` +
        `Maximum available: ${Math.max(0, latest.loyalty_points || 0)} points.`;
      submit.textContent = "Convert to store credit";
    } else {
      helper.textContent = action.value === "set" ? "Set the customer's exact whole-point balance." :
        "Adjustments are recorded with the reason and cannot spend more than the available points.";
      submit.textContent = "Apply change";
    }
  }
  action.onchange = sync;
  const modal = openModal({
    title: `Loyalty points — ${latest.name}`,
    narrow: true,
    body: h("div", {},
      h("div", { class: "pay-total-banner" },
        h("span", { class: "lbl" }, "Current balance"),
        h("span", { class: "amt" }, `${Number(latest.loyalty_points || 0).toLocaleString("en-IN")} pts`)),
      h("div", { class: "field" }, h("label", {}, "Action"), action),
      h("div", { class: "field" }, h("label", {}, "Points (whole number)"), amount),
      h("div", { class: "field" }, h("label", {}, "Reason *"), reason),
      helper),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel"), submit],
  });
  sync();
  submit.onclick = async () => {
    const val = Number(amount.value);
    if (!amount.value.trim() || !Number.isSafeInteger(val) || val < 0 || val > 1000000)
      return toast("Enter a valid whole-point amount.", "warn");
    if (action.value !== "set" && val === 0)
      return toast("Points must be greater than zero.", "warn");
    if (action.value !== "redeem" && !reason.value.trim())
      return toast("A reason is required for an admin adjustment.", "warn");
    try {
      submit.disabled = true;
      if (action.value === "redeem") {
        const converted = await api.redeemLoyalty(latest.id, val);
        toast(`${val} points converted to ${fmt(converted.credited_paise / 100)} store credit.`, "success");
      } else {
        const payload = { reason: reason.value.trim() };
        if (action.value === "set") payload.set_points = val;
        else payload.delta_points = action.value === "deduct" ? -val : val;
        const result = await api.adjustLoyalty(latest.id, payload);
        toast(`Loyalty balance: ${result.loyalty_points} points.`, "success");
      }
      modal.close();
      await load(); renderSub(); renderCustomerStats(); renderTable();
      await detailModal(latest.id);
    } catch (e) { toastError(e); submit.disabled = false; }
  };
}


function customerModal(existing = null) {
  const name = h("input", { class: "input", value: existing?.name || "", placeholder: "Full name" });
  const phone = h("input", { class: "input", value: existing?.phone || "", placeholder: "Phone (optional)" });
  const email = h("input", { class: "input", type: "email", value: existing?.email || "", placeholder: "Optional" });
  const gstin = h("input", { class: "input", value: existing?.gstin || "", maxlength: "15",
    placeholder: "Optional, 15 characters" });
  const billingAddress = h("textarea", { class: "input", rows: "2",
    placeholder: "Customer billing address (optional)" });
  billingAddress.value = existing?.billing_address || "";
  const notes = h("textarea", { class: "input", rows: "2", placeholder: "Optional" }, existing?.notes || "");

  const saveBtn = h("button", { class: "btn primary" },
    icon("i-check"), existing ? "Save changes" : "Add customer");
  saveBtn.addEventListener("click", async () => {
    const payload = {
      name: name.value, phone: phone.value,
      email: email.value, notes: notes.value,
      gstin: gstin.value, billing_address: billingAddress.value,
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
      renderCustomerStats();
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
      h("div", { class: "field" }, h("label", {}, "Customer GSTIN"), gstin),
      h("div", { class: "field" }, h("label", {}, "Billing address"), billingAddress),
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
      renderCustomerStats();
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
      "record and credit log are removed. Customers with store credit, loyalty points, or any loyalty transaction history cannot be deleted.",
    confirmLabel: "Delete customer",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.deleteCustomer(c.id);
    toast(`${c.name} deleted.`, "success");
    await load();
    renderSub();
    renderCustomerStats();
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

  const loyaltyRows = (c.loyalty_events || []).map((ev) =>
    h("tr", {},
      h("td", {}, fmtDate(ev.timestamp)),
      h("td", {}, `${ev.event_type}: ${ev.reason || "Adjustment"}`),
      h("td", { class: "num mono", style: "color:" +
        (ev.delta_points >= 0 ? "var(--ok)" : "var(--danger)") },
        `${ev.delta_points > 0 ? "+" : ""}${ev.delta_points}`),
      h("td", { class: "num mono" }, String(ev.balance_after))));

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
      h("div", { class: "pay-total-banner", style: "margin-top:8px" },
        h("span", { class: "lbl" }, "Loyalty points"),
        h("span", { class: "amt" }, `${Number(c.loyalty_points || 0).toLocaleString("en-IN")} pts`)),
      h("div", { style: "display:flex;gap:18px;margin-bottom:12px;font-size:13px;color:var(--text-2)" },
        h("span", {}, h("strong", {}, String(c.orders)), " orders"),
        h("span", {}, h("strong", {}, fmt(c.total_spent)), " lifetime")),
      (c.phone || c.email || c.notes || c.gstin || c.billing_address)
        ? h("div", { class: "card", style: "padding:10px 14px;margin-bottom:14px;font-size:13px" },
            c.phone ? h("div", {}, c.phone) : null,
            c.gstin ? h("div", {}, `GSTIN: ${c.gstin}`) : null,
            c.billing_address ? h("div", {}, c.billing_address) : null,
            c.email ? h("div", {}, c.email) : null,
            c.notes ? h("div", { style: "color:var(--text-2)" }, c.notes) : null)
        : null,
      h("h3", { style: "margin-bottom:6px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)" },
        "Loyalty history"),
      loyaltyRows.length
        ? h("div", { class: "table-wrap", style: "max-height:180px;margin-bottom:14px" },
            h("table", { class: "table" }, loyaltyRows))
        : h("p", { style: "color:var(--muted);font-size:13px;margin-bottom:14px" }, "No loyalty activity yet."),
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
      h("button", { class: "btn",
        onclick: () => { closeModalAll(); loyaltyModal(c); } }, "Manage points"),
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
