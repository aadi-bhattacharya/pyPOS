// Thin API client + shared app state (settings cache, formatting).

export const state = {
  settings: {
    store_name: "My Store",
    store_address: "",
    store_phone: "",
    currency: "$",
    tax_label: "Tax",
    receipt_footer: "Thank you for shopping with us!",
    low_stock_threshold: "5",
  },
};

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch (e) {
    throw new Error("Cannot reach the POS server. Is it still running?");
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data && data.error) msg = data.error;
    } catch (_) { /* non-JSON error */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // settings
  getSettings: () => request("/settings"),
  saveSettings: (values) => request("/settings", { method: "PUT", body: values }),
  settingsAuth: () => request("/settings/auth"),
  setPassword: (currentPassword, newPassword) =>
    request("/settings/password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    }),

  // groups
  listGroups: () => request("/groups"),
  createGroup: (name) => request("/groups", { method: "POST", body: { name } }),
  renameGroup: (id, name) => request(`/groups/${id}`, { method: "PATCH", body: { name } }),
  deleteGroup: (id) => request(`/groups/${id}`, { method: "DELETE" }),

  // products
  listProducts: (params = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, v);
    }
    const s = qs.toString();
    return request(`/products${s ? "?" + s : ""}`);
  },
  getProduct: (id) => request(`/products/${id}`),
  lookupCode: (code) => request(`/lookup?code=${encodeURIComponent(code)}`),
  createProduct: (data) => request("/products", { method: "POST", body: data }),
  updateProduct: (id, data) => request(`/products/${id}`, { method: "PATCH", body: data }),
  deleteProduct: (id) => request(`/products/${id}`, { method: "DELETE" }),
  setAttribute: (pid, name, value) =>
    request(`/products/${pid}/attributes`, { method: "POST", body: { name, value } }),
  removeAttribute: (pid, name) =>
    request(`/products/${pid}/attributes/${encodeURIComponent(name)}`, { method: "DELETE" }),
  adjustStock: (pid, delta) =>
    request(`/products/${pid}/stock`, { method: "POST", body: { delta } }),

  // sales
  checkout: (payload) => request("/sales", { method: "POST", body: payload }),
  listSales: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/sales${qs ? "?" + qs : ""}`);
  },
  getSale: (id) => request(`/sales/${id}`),
  refundSale: (id, payload = {}) =>
    request(`/sales/${id}/refund`, { method: "POST", body: payload }),
  voidSale: (id) => request(`/sales/${id}/void`, { method: "POST" }),

  // customers & store credit
  listCustomers: (q = "") =>
    request(`/customers${q ? "?q=" + encodeURIComponent(q) : ""}`),
  getCustomer: (id) => request(`/customers/${id}`),
  createCustomer: (data) => request("/customers", { method: "POST", body: data }),
  updateCustomer: (id, data) =>
    request(`/customers/${id}`, { method: "PATCH", body: data }),
  deleteCustomer: (id) => request(`/customers/${id}`, { method: "DELETE" }),
  adjustCredit: (id, deltaCents, reason = "") =>
    request(`/customers/${id}/credit`,
      { method: "POST", body: { delta_cents: deltaCents, reason } }),

  // parked orders
  listParked: () => request("/parked"),
  parkCart: (payload) => request("/parked", { method: "POST", body: payload }),
  getParked: (id) => request(`/parked/${id}`),
  discardParked: (id) => request(`/parked/${id}`, { method: "DELETE" }),

  // cash sessions (X / Z reports)
  currentSession: () => request("/cash-sessions/current"),
  openSession: (openingFloat) =>
    request("/cash-sessions/open", { method: "POST", body: { opening_float: openingFloat } }),
  closeSession: (countedCash, note = "") =>
    request("/cash-sessions/close", { method: "POST", body: { counted_cash: countedCash, note } }),
  listSessions: () => request("/cash-sessions"),
  sessionReport: (id) => request(`/cash-sessions/${id}/report`),

  // reports & demo
  reportSummary: (days = 14) => request(`/reports/summary?days=${days}`),
  seedDemo: () => request("/demo-data", { method: "POST" }),

  // backups
  backupStatus: () => request("/backups/status"),
  listBackups: () => request("/backups"),
  createBackup: () => request("/backups", { method: "POST" }),
  restoreBackup: (name) => request(`/backups/${encodeURIComponent(name)}/restore`, { method: "POST" }),
  deleteBackup: (name) => request(`/backups/${encodeURIComponent(name)}`, { method: "DELETE" }),
};

export async function refreshSettings() {
  try {
    state.settings = await api.getSettings();
  } catch (_) { /* keep defaults */ }
  return state.settings;
}

export function fmt(amount) {
  const n = Number(amount || 0);
  const sym = state.settings.currency || "$";
  const str = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${n < 0 ? "-" : ""}${sym}${str}`;
}

export function fmtDate(ts) {
  if (!ts) return "";
  // SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS"
  const d = new Date(ts.replace(" ", "T") + "Z");
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function todayISO(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

// Mirror of the server's cent-exact totals math so the register preview
// always matches the final receipt. Tax rates are percent points.
export function computeTotals(lines, discountMode, discountValue) {
  const prepared = lines.map((l) => ({
    gross: Math.round(Number(l.price) * l.quantity * 100),
    ratePct: Number(l.tax_rate || 0),
  }));
  const grossTotal = prepared.reduce((a, l) => a + l.gross, 0);

  let disc = 0;
  if (discountMode === "percent") {
    const pct = Number(discountValue || 0);
    if (pct > 0) disc = Math.round((grossTotal * pct) / 100);
  } else if (discountMode === "amount") {
    disc = Math.round(Number(discountValue || 0) * 100);
  }
  disc = Math.max(0, Math.min(disc, grossTotal));

  let allocated = 0;
  const lineDetails = prepared.map((l, i) => {
    let share;
    if (i < prepared.length - 1 && grossTotal > 0) {
      share = Math.floor((disc * l.gross) / grossTotal);
    } else {
      share = disc - allocated;
    }
    allocated += share;
    const net = l.gross - share;
    // net is in cents; tax cents = net × pct/100 (ratePct is percent points)
    return { gross: l.gross, discount: share, net, tax: Math.round((net * l.ratePct) / 100) };
  });

  const taxTotal = lineDetails.reduce((a, l) => a + l.tax, 0);
  return {
    subtotal: grossTotal / 100,
    discount: disc / 100,
    tax: taxTotal / 100,
    total: (grossTotal - disc + taxTotal) / 100,
  };
}
