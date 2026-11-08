import { api, state } from "../api.js";
import { h, openModal, toast, toastError, confirmDialog } from "../ui.js";

const SENSITIVE = new Set([
  "cash_movements", "discount_override", "refund_sales", "void_sales",
  "delete_products", "reduce_stock", "import_merge", "stocktakes",
  "delete_customers", "manage_credit", "manage_loyalty", "manage_purchases", "export_data",
]);

export async function renderStaff(main) {
  let status;
  try { status = await api.staffStatus(); } catch (e) { toastError(e); return; }
  state.staff = status;
  const operator = status.operator;
  const owner = operator?.role === "admin";
  const refresh = () => renderStaff(main);
  let users = [], audit = [], schema = null, settingsUnlocked = false;
  if (owner) {
    try {
      const auth = await api.settingsAuth();
      settingsUnlocked = auth.authenticated || !auth.configured;
      [users, audit, schema] = await Promise.all([
        api.staffUsers(), api.staffAudit(), api.staffPermissionSchema(),
      ]);
    } catch (e) { toastError(e); }
  }
  const actions = h("div", { class: "view-actions" },
    owner && schema ? h("button", { class: "btn primary", disabled: !settingsUnlocked,
      onclick: () => staffModal(refresh, schema) },
      "+ Add staff") : null,
    h("a", { class: "btn", href: "/staff-logout" }, "Sign out"));
  const intro = h("div", { class: "card", style: "padding:20px" },
    h("h3", {}, `${operator?.display_name || "Operator"} · ${owner ? "Owner / administrator" : "Staff"}`),
    h("p", { class: "form-hint" }, owner
      ? "Your account administers permissions and store security. Give each employee their own username and PIN."
      : "Your owner chooses what you can do. Authorised staff can start and close the till without the owner being present."));
  main.replaceChildren(
    h("div", { class: "view-head" }, h("div", { class: "view-title" },
      h("h2", {}, "Staff & permissions"),
      h("div", { class: "sub" }, "One staff role · Individual permission switches · Activity history")), actions),
    intro,
    owner && !settingsUnlocked ? h("div", { class: "card", style: "padding:18px;margin-top:14px" },
      h("strong", {}, "Administrator password required for changes"),
      h("p", { class: "form-hint" }, "You can view staff records, but creating or changing operator permissions requires unlocking Settings."),
      h("a", { class: "btn primary", href: "/login?next=%2F%23%2Fstaff" }, "Unlock staff management")) : null,
    owner && schema ? operatorsTable(users, schema, refresh, settingsUnlocked) : null,
    owner ? recentActions(audit) : null,
  );
}

function operatorsTable(users, schema, refresh, settingsUnlocked) {
  const rows = users.map(user => {
    const permissionsBtn = user.role === "admin" ? null : h("button", {
      class: "btn sm", disabled: !settingsUnlocked,
      onclick: () => permissionsModal(user, schema, refresh),
    }, "Permissions");
    const activeBtn = h("button", { class: "btn sm", disabled: !settingsUnlocked,
      onclick: async () => {
      if (!await confirmDialog({ title: "Change staff access?",
        message: `${user.active ? "Disable" : "Enable"} ${user.username}? Existing transaction history will be preserved.`,
        confirmLabel: user.active ? "Disable" : "Enable" })) return;
      try { await api.setStaffActive(user.id, !user.active); refresh(); }
      catch (e) { toastError(e); }
    } }, user.active ? "Disable" : "Enable");
    return h("tr", {},
      h("td", {}, user.username), h("td", {}, user.display_name),
      h("td", {}, user.role === "admin" ? "Owner" : "Staff"),
      h("td", {}, user.active ? "Active" : "Disabled"),
      h("td", {}, user.role === "admin" ? "All" :
        `${Object.values(user.permissions).filter(Boolean).length} capabilities`),
      h("td", {}, h("div", { style: "display:flex;gap:7px;flex-wrap:wrap" }, permissionsBtn, activeBtn)));
  });
  return h("div", { class: "card", style: "margin-top:14px;padding:18px" },
    h("h3", {}, "Operators"),
    h("p", { class: "form-hint" }, "Staff have individual permissions. Owner accounts always retain full access."),
    h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ...["Username", "Name", "Type", "Status", "Granted", "Actions"]
        .map(name => h("th", {}, name)))),
      h("tbody", {}, ...rows))));
}

function recentActions(audit) {
  const rows = audit.slice(0, 100).map(a => h("tr", {},
    h("td", {}, a.username_at_event), h("td", {}, `${a.method} ${a.action}`),
    h("td", {}, a.recorded_at)));
  return h("div", { class: "card", style: "margin-top:14px;padding:18px" },
    h("h3", {}, "Recent operator actions"),
    h("p", { class: "form-hint" }, "Successful actions recorded by operator. Not a tamper-proof ledger."),
    h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ...["Operator", "Action", "Time"].map(x => h("th", {}, x)))),
      h("tbody", {}, ...rows))));
}

function switchGroups(groups, permissions, editable = true) {
  const controls = new Map();
  const node = h("div", { class: "staff-permission-grid" },
    ...Object.entries(groups).map(([group, entries]) => h("section", {
      class: "staff-permission-group",
    }, h("h4", {}, group), ...Object.entries(entries).map(([key, description]) => {
      const checkbox = h("input", {
        type: "checkbox", checked: permissions[key] === true, disabled: !editable,
        "aria-label": description,
      });
      controls.set(key, checkbox);
      return h("label", { class: "staff-permission-toggle" },
        h("span", {}, description,
          SENSITIVE.has(key) ? h("small", { class: "staff-sensitive" }, "Sensitive") : null),
        checkbox);
    }))));
  return { node, values: () => Object.fromEntries([...controls].map(([key, input]) => [key, input.checked])),
    usePreset: preset => { for (const [key, input] of controls) input.checked = preset[key] === true; } };
}

function permissionsModal(user, schema, refresh) {
  const switches = switchGroups(schema.groups, user.permissions);
  let modal;
  modal = openModal({ title: `Permissions · ${user.display_name}`, wide: true,
    body: h("div", {},
      h("p", { class: "form-hint" },
        "Changes take effect on the next API request, including while the employee is signed in. These controls are enforced by the server."),
      h("div", { class: "staff-permission-presets" },
        h("button", { class: "btn sm", onclick: () => switches.usePreset(schema.defaults) }, "Everyday staff"),
        h("button", { class: "btn sm", onclick: () => switches.usePreset(schema.supervisor) }, "Supervisor preset"),
        h("button", { class: "btn sm", onclick: () => switches.usePreset({}) }, "Clear all")),
      switches.node),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        const permissions = switches.values();
        const newlySensitive = [...SENSITIVE].filter(key => permissions[key] && !user.permissions[key]);
        if (newlySensitive.length && !await confirmDialog({
          title: "Grant sensitive permissions?",
          message: `You are allowing ${user.username} to perform ${newlySensitive.length} additional sensitive operations. Confirm that this employee is trusted.`,
          confirmLabel: "Grant access", danger: true,
        })) return;
        try { await api.setStaffPermissions(user.id, permissions); modal.close();
          toast("Permissions saved. They are effective immediately.", "success"); refresh(); }
        catch (e) { toastError(e); }
      } }, "Save permissions")],
  });
}

function staffModal(refresh, schema) {
  const field = (label, control) => h("div", { class: "field" }, h("label", {}, label), control);
  const input = (placeholder, type = "text") => h("input", { class: "input", type, placeholder });
  const username = input("e.g. counter01"), name = input("Display name"), pin = input("6–12 digit PIN", "password");
  const role = h("select", { class: "input" },
    h("option", { value: "staff" }, "Staff (custom permissions)"),
    h("option", { value: "admin" }, "Additional owner / administrator"));
  const switches = switchGroups(schema.groups, schema.defaults);
  const permsSection = h("div", {},
    h("div", { class: "staff-permission-presets" },
      h("button", { class: "btn sm", onclick: () => switches.usePreset(schema.defaults) }, "Everyday staff"),
      h("button", { class: "btn sm", onclick: () => switches.usePreset(schema.supervisor) }, "Supervisor")),
    switches.node);
  role.addEventListener("change", () => { permsSection.hidden = role.value === "admin"; });
  let modal;
  modal = openModal({ title: "Add operator", wide: true,
    body: h("div", {},
      field("Username", username), field("Name", name), field("Account type", role),
      field("Individual sign-in PIN", pin),
      h("p", { class: "form-hint" },
        "Staff may open and close the till on their own when granted those switches. No shared owner PIN is necessary."),
      permsSection),
    footer: [h("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: async () => {
        const permissions = role.value === "admin" ? undefined : switches.values();
        if (permissions && [...SENSITIVE].some(key => permissions[key])) {
          if (!await confirmDialog({ title: "Create staff with sensitive access?",
            message: "This employee will have permissions for refunds, stock changes or other sensitive operations. Review before granting.",
            confirmLabel: "Create operator", danger: true })) return;
        }
        try { await api.createStaff({ username: username.value, display_name: name.value,
          pin: pin.value, role: role.value, permissions });
          modal.close(); toast("Operator created.", "success"); refresh(); }
        catch (e) { toastError(e); }
      } }, "Create operator")],
  });
}
