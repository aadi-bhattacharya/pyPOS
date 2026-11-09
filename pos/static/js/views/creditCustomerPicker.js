import { api, can } from "../api.js";
import { h, toastError } from "../ui.js";

// Only used for a return without a customer on the original invoice.
// A *selected* ID, not an unverified typed name, is sent to the server.
export function creditCustomerPicker(onChange, {optional = false} = {}) {
  let selected = null;
  let sequence = 0;
  let timer;
  const input = h("input", { class: "input", type: "search", autocomplete: "off",
    placeholder: "Search customer name or phone…", "aria-label": "Exchange / credit customer search" });
  const results = h("div", { class: "return-search-list" });
  const status = h("p", { class: "form-hint" }, optional
    ? "Optional. Leave blank for a direct exchange without customer credit."
    : "Select a customer to hold the store credit.");
  function choose(c) {
    selected = c;
    ++sequence;
    clearTimeout(timer);
    input.value = `${c.name} (ID ${c.id})`;
    status.textContent = `Selected: ${c.name} · customer ID ${c.id}`;
    results.replaceChildren();
    if (onChange) onChange(c.id);
  }
  async function lookup() {
    const seq = ++sequence;
    const q = input.value.trim();
    selected = null;
    status.textContent = "Choose a matching customer. Typed text alone is not a selection.";
    if (onChange) onChange(null);
    if (q.length < 2) { results.replaceChildren(); return; }
    results.replaceChildren(h("p", { class: "form-hint" }, "Searching…"));
    try {
      const matches = (await api.listCustomers(q)).slice(0, 12);
      if (seq !== sequence) return;
      results.replaceChildren(...(matches.length
        ? matches.map(c => h("button", { type: "button", class: "btn", onclick: () => choose(c) },
          `${c.name} · ${c.phone || "no phone"} · ID ${c.id}`))
        : [h("p", { class: "form-hint" }, "No customers found.")]));
    } catch (e) { if (seq === sequence) { results.replaceChildren(); toastError(e); } }
  }
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(lookup, 230);
  });
  const creation = h("div", { class: "field", hidden: true });
  const name = h("input", { class: "input", placeholder: "Customer name", maxlength: "120" });
  const phone = h("input", { class: "input", placeholder: "Phone number", maxlength: "30" });
  if (can("manage_customers")) {
    const save = h("button", { class: "btn primary sm", onclick: async () => {
      if (!name.value.trim() || !phone.value.trim()) {
        status.textContent = "Enter a name and phone number to identify this customer's credit.";
        return;
      }
      save.disabled = true;
      try { choose(await api.createCustomer({name: name.value.trim(), phone: phone.value.trim()}));
        creation.hidden = true;
      } catch (e) { toastError(e); }
      finally { save.disabled = false; }
    } }, "Save customer");
    creation.append(name, phone, save);
  }
  const root = h("div", { class: "field" },
    h("label", {}, optional ? "Customer (optional)" : "Customer receiving credit"), input, status, results,
    can("manage_customers") ? h("button", { type: "button", class: "btn ghost sm",
      onclick: () => { creation.hidden = !creation.hidden; } }, "Create customer") : null,
    creation);
  function clear() {
    selected = null;
    ++sequence;
    clearTimeout(timer);
    input.value = "";
    results.replaceChildren();
    status.textContent = optional
      ? "No customer selected; return will offset replacement directly."
      : "No customer selected.";
    if (onChange) onChange(null);
  }
  return { root, id: () => selected?.id ?? null, choose, clear };
}
