/** Administrator return/exchange policy editor. All rules enforced server-side. */
import { api, state } from "../api.js";
import { h, icon, toast, toastError, confirmDialog } from "../ui.js";

export function returnPolicyCard(saved, rerender) {
  const rules = [
    ["refund_allow_original", "Record cash / card / UPI refunds",
      "Off: staff cannot issue a direct payout/refund. Existing recorded refunds remain unchanged."],
    ["refund_allow_credit", "Issue named-customer store credit",
      "Credit requires a registered customer ID. An anonymous shopper cannot hold a balance without an identity."],
    ["exchange_allow", "Allow item exchanges",
      "An exchange creates a linked return and new sale in one transaction."],
    ["exchange_allow_anonymous", "Allow walk-in exchanges without customer details",
      "No name or phone required; the return offsets the replacement invoice directly, with no cash payout or wallet."],
    ["exchange_allow_forfeit_difference", "Allow forfeiting unused exchange value",
      "Off by default. If the replacement costs less and no credit can be issued, block the exchange. When enabled, the cashier must explicitly acknowledge the forfeited amount; it is recorded for audit. Confirm this policy is lawful before enabling it."],
  ];
  const boxes = Object.fromEntries(rules.map(([key]) => [key,
    h("input", { type: "checkbox", checked: saved[key] !== "false" &&
      (key !== "exchange_allow_forfeit_difference" || saved[key] === "true") })]));
  return h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Returns & exchange rules"),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "Administrator policy. Enforced by the server, not only by the cashier screen. " +
      "Changing a rule affects future returns and exchanges, not historical documents."),
    ...rules.map(([key, label, help]) =>
      h("div", { style: "margin-bottom:12px" },
        h("label", { class: "credit-toggle" }, boxes[key], h("span", {}, label)),
        h("p", { class: "form-hint" }, help))),
    h("button", { class: "btn primary", onclick: async () => {
      const values = Object.fromEntries(rules.map(([key]) =>
        [key, boxes[key].checked ? "true" : "false"]));
      if (values.exchange_allow_forfeit_difference === "true" &&
          saved.exchange_allow_forfeit_difference !== "true") {
        if (!(await confirmDialog({
          title: "Allow forfeiting exchange differences?",
          message: "This permits the store to retain the unused value of a return " +
            "when no customer credit is issued. The cashier must explicitly confirm each case. " +
            "Check consumer protection and GST treatment before enabling this rule.",
          confirmLabel: "Enable with acknowledgement", danger: true,
        }))) return;
      }
      try {
        state.settings = await api.saveSettings(values);
        toast("Returns & exchange rules saved.", "success");
        rerender();
      } catch (e) { toastError(e); }
    } }, icon("i-check"), "Save return rules"));
}
