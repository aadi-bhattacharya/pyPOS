import { api } from "../api.js";
import { h, openModal, toast, toastError, confirmDialog } from "../ui.js";

export async function renderStaff(main) {
  let status;
  try { status = await api.staffStatus(); } catch (e) { return toastError(e); }
  const operator = status.operator;
  const refresh = () => renderStaff(main);
  let users = [], audit = [];
  if (operator?.role === "admin") {
    try { [users,audit] = await Promise.all([api.staffUsers(),api.staffAudit()]); }
    catch (e) { toastError(e); }
  }
  main.replaceChildren(
    h("div", { class: "view-head" }, h("div", { class: "view-title" },
      h("h2", {}, "Staff & permissions"),
      h("div", { class: "sub" }, "Optional individual PINs · Role permissions · Activity history")),
      operator ? h("a", { class: "btn", href: "/staff-logout" }, "Sign out") : null),
    !status.enabled ? h("div", { class: "card", style: "padding:20px" },
      h("h3", {}, "Staff access is not enabled"),
      h("p", { class: "form-hint" }, "Existing single-operator mode remains active. Set an administrator password " +
        "and sign into Settings before enabling staff access. The first staff user must be an admin."),
      h("button", { class: "btn primary", onclick: () => staffModal(refresh, true) }, "Enable staff PINs")) :
    h("div", { class: "card", style: "padding:20px" },
      h("h3", {}, `Signed in: ${operator?.display_name || "—"}`),
      h("p", { class: "form-hint" }, `Role: ${operator?.role || "Sign-in required"}. Cashiers can sell and view history; ` +
        "managers handle refunds/stock/cash; administrators manage settings, backups and staff."),
      operator?.role === "admin" ? h("div", { style: "margin-top:12px" },
        h("button", { class: "btn primary", onclick: () => staffModal(refresh, false) }, "+ Add staff member")) : null),
    operator?.role === "admin" ? h("div", { class: "card", style: "margin-top:14px;padding:18px" },
      h("h3", {}, "Operators"),
      h("div", { class: "table-wrap" },h("table",{class:"table"},
        h("thead",{},h("tr",{},...["Username","Name","Role","Status",""].map(x=>h("th",{},x)))),
        h("tbody",{},...users.map(u=>h("tr",{},
          h("td",{},u.username),h("td",{},u.display_name),h("td",{},u.role),
          h("td",{},u.active?"Active":"Inactive"),
          h("td",{},h("button",{class:"btn sm",onclick:async()=>{
            if(!await confirmDialog({title:"Change staff access?",message:
              `${u.active ? "Disable" : "Enable"} ${u.username}? Existing sale records will not be changed.`,
              confirmLabel:u.active?"Disable":"Enable"}))return;
            try{await api.setStaffActive(u.id,!u.active);refresh();}catch(e){toastError(e);}
          }},u.active?"Disable":"Enable")))))))) : null,
    operator?.role === "admin" ? h("div", { class: "card", style: "margin-top:14px;padding:18px" },
      h("h3",{},"Recent operator actions"),
      h("p",{class:"form-hint"},"Records successful API actions by operator, not a tamper-proof accounting ledger."),
      h("div",{class:"table-wrap"},h("table",{class:"table"},
        h("thead",{},h("tr",{},...["Operator","Action","Time"].map(x=>h("th",{},x)))),
        h("tbody",{},...audit.slice(0,100).map(a=>h("tr",{},
          h("td",{},a.username_at_event),h("td",{},`${a.method} ${a.action}`),
          h("td",{},a.recorded_at))))))) : null);
}

function staffModal(refresh, bootstrap) {
  const text = (placeholder, type="text") => h("input",{class:"input",type,placeholder});
  const field = (label, control) => h("div",{class:"field"},h("label",{},label),control);
  const username=text("e.g. cashier1"), name=text("Display name"), pin=text("6–12 digit PIN","password");
  const role=h("select",{class:"input"},...((bootstrap?["admin"]:["cashier","manager","admin"])
    .map(x=>h("option",{value:x},x[0].toUpperCase()+x.slice(1)))));
  let m;
  m=openModal({title:bootstrap?"Enable staff access":"Add staff operator",narrow:true,
    body:h("div",{},field("Username",username),field("Display name",name),field("Role",role),
      field("New PIN",pin),h("p",{class:"form-hint"},
        bootstrap?"Enabling staff access requires everyone to sign in with a PIN on the next page load.":
        "PINs are hashed; no initial or default staff PIN is created.")),
    footer:[h("button",{class:"btn",onclick:()=>m.close()},"Cancel"),
      h("button",{class:"btn primary",onclick:async()=>{
        try {await api.createStaff({username:username.value,display_name:name.value,
          role:role.value,pin:pin.value});m.close();toast("Staff member saved.","success");
          if(bootstrap)window.location.assign("/staff-login");else refresh();}
        catch(e){toastError(e);}
      }},bootstrap?"Enable PIN access":"Save staff")],
  });
}
