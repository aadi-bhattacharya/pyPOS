// Reusable, keyboard-accessible SKU chooser for stocktakes and supplier receipts.
// It resolves a product ID, never a shared barcode: an EAN may have several MRPs.
import { fmt } from "./api.js";
import { publicVariant } from "./productLabels.js";
import { h } from "./ui.js";

let pickerSerial = 0;
const norm = value => String(value ?? "").trim().toLocaleLowerCase("en-IN");
const skuName = p => String(p.group_name || p.SKU || "Product");

export function createSkuPicker(products, { placeholder = "Scan EAN, enter SKU or search product…",
  maxResults = 40, onSelect = null } = {}) {
  const listId = `sku-picker-${++pickerSerial}`;
  const indexed = products.map(p => {
    const sku = String(p.SKU ?? "");
    const barcode = String(p.barcode ?? "");
    const variant = publicVariant(p.attributes || []);
    const name = skuName(p);
    return { p, sku, barcode, variant, name,
      search: norm([sku, barcode, name, p.brand_name, variant].join(" ")) };
  });
  const input = h("input", { class: "input sku-picker-input", type: "text",
    placeholder, autocomplete: "off", spellcheck: "false", role: "combobox",
    "aria-autocomplete": "list", "aria-controls": listId, "aria-expanded": "false",
    "aria-label": "Find exact stock SKU / price edition" });
  const menu = h("div", { id: listId, class: "sku-picker-menu", role: "listbox", hidden: true });
  const summary = h("div", { class: "sku-picker-summary form-hint", "aria-live": "polite" },
    "Choose an exact SKU. A shared EAN may have different MRPs.");
  const root = h("div", { class: "sku-picker" }, input, menu, summary);
  let chosen = null;
  let results = [];
  let cursor = -1;
  let dialog = null;
  let modalScroller = null;
  function closeOnOutside(event) {
    if (!root.contains(event.target) && !menu.contains(event.target)) close();
  }

  function reposition() {
    if (!dialog || menu.hidden) return;
    const r = input.getBoundingClientRect();
    const d = dialog.getBoundingClientRect();
    const below = Math.max(70, window.innerHeight - r.bottom - 14);
    const above = Math.max(70, r.top - 14);
    menu.style.width = `${r.width}px`;
    menu.style.maxHeight = "none";  // measure after wrapping at the actual input width
    const desired = Math.min(menu.scrollHeight, 295);
    const flip = below < Math.min(175, desired) && above > below;
    const height = Math.min(desired, flip ? above : below);
    menu.style.left = `${r.left - d.left}px`;
    menu.style.width = `${r.width}px`;
    menu.style.maxHeight = `${height}px`;
    menu.style.top = `${flip ? r.top - d.top - height - 3 : r.bottom - d.top + 3}px`;
  }
  function close() {
    menu.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    document.removeEventListener("pointerdown", closeOnOutside, true);
    window.removeEventListener("resize", reposition);
    modalScroller?.removeEventListener("scroll", reposition);
    modalScroller = null;
    if (dialog) {
      menu.classList.remove("in-modal");
      menu.removeAttribute("style");
      root.append(menu);
      dialog = null;
    }
    cursor = -1;
  }
  function selectProduct(entry) {
    chosen = entry.p;
    input.value = entry.sku;
    summary.textContent = `${entry.name}${entry.variant ? ` · ${entry.variant}` : ""} · ` +
      `${fmt(entry.p.price)} · ${entry.p.quantity} in stock`;
    close();
    if (onSelect) onSelect(entry.p);
  }
  function search(q) {
    const query = norm(q);
    if (!query) return [];
    const exactSku = [], exactEAN = [], prefix = [], other = [];
    for (const e of indexed) {
      if (norm(e.sku) === query) exactSku.push(e);
      else if (norm(e.barcode) === query) exactEAN.push(e);
      else if (norm(e.sku).startsWith(query) || norm(e.barcode).startsWith(query) ||
        norm(e.name).startsWith(query)) prefix.push(e);
      else if (e.search.includes(query)) other.push(e);
    }
    return [...exactSku, ...exactEAN, ...prefix, ...other];
  }
  function updateActive() {
    for (const [i, el] of [...menu.querySelectorAll('[role="option"]')].entries()) {
      el.classList.toggle("active", i === cursor);
      el.setAttribute("aria-selected", i === cursor ? "true" : "false");
      if (i === cursor) {
        input.setAttribute("aria-activedescendant", el.id);
        el.scrollIntoView?.({ block: "nearest" });
      }
    }
    if (cursor < 0) input.removeAttribute("aria-activedescendant");
  }
  function show() {
    results = search(input.value);
    const visible = results.slice(0, maxResults);
    cursor = -1;
    menu.replaceChildren();
    if (!results.length) {
      menu.append(h("div", { class: "sku-picker-message" },
        input.value.trim() ? "No matching SKU, EAN or product." : "Type an EAN, SKU or product name."));
    } else {
      const exactEANCount = results.filter(e => norm(e.barcode) === norm(input.value)).length;
      if (exactEANCount > 1) menu.append(h("div", { class: "sku-picker-message" },
        `${exactEANCount} price/variant editions share this EAN. Select the correct SKU.`));
      menu.append(...visible.map((e, i) => {
        const btn = h("button", { type: "button", id: `${listId}-option-${i}`,
          class: "sku-picker-option", role: "option", "aria-selected": "false",
          onmousedown: ev => ev.preventDefault(),
          onclick: () => selectProduct(e) },
        h("span", { class: "sku-picker-option-heading" },
          h("strong", {}, e.name), h("span", { class: "sku-picker-price" }, fmt(e.p.price))),
        h("span", { class: "sku-picker-option-detail" },
          `SKU ${e.sku} · ${e.p.quantity} units${e.barcode ? ` · EAN ${e.barcode}` : ""}`),
        e.variant ? h("span", { class: "sku-picker-option-detail" }, e.variant) : null);
        return btn;
      }));
      if (results.length > visible.length) menu.append(h("div", { class: "sku-picker-message" },
        `${results.length - visible.length} more matches; type more to narrow the list.`));
    }
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", closeOnOutside, true);
    const nextDialog = root.closest(".modal");
    if (nextDialog) {
      dialog = nextDialog;
      dialog.style.position = "relative";
      if (menu.parentElement !== dialog) dialog.append(menu);
      menu.classList.add("in-modal");
      reposition();
      window.addEventListener("resize", reposition);
      modalScroller = dialog.querySelector(".modal-body");
      modalScroller?.addEventListener("scroll", reposition, { passive: true });
    }
  }
  input.addEventListener("input", () => {
    chosen = null;
    summary.textContent = "Select the correct SKU and price edition before saving.";
    show();
  });
  input.addEventListener("focus", () => { if (!chosen && input.value.trim()) show(); });
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") { close(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.hidden) show();
      const count = Math.min(results.length, maxResults);
      if (count) {
        cursor = event.key === "ArrowDown" ? Math.min(cursor + 1, count - 1)
          : cursor < 0 ? count - 1 : Math.max(cursor - 1, 0);
        updateActive();
      }
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (chosen && norm(input.value) === norm(chosen.SKU)) return;
    if (menu.hidden) show();
    if (cursor >= 0 && cursor < results.length) {
      selectProduct(results[cursor]); return;
    }
    // A scanner often sends Enter. Auto-pick ONLY an unambiguous exact match.
    const query = norm(input.value);
    const exact = results.filter(e => norm(e.sku) === query || norm(e.barcode) === query);
    if (exact.length === 1) selectProduct(exact[0]);
    else if (results.length === 1) selectProduct(results[0]);
    else {
      summary.textContent = exact.length > 1
        ? "Multiple MRP editions: choose the printed price from the list."
        : "Select an exact SKU from the dropdown.";
    }
  });
  menu.addEventListener("mousedown", event => event.preventDefault());
  input.addEventListener("blur", () => {
    // Click on an option is handled before this callback; it does not lose the selection.
    setTimeout(() => { if (!root.contains(document.activeElement)) close(); }, 0);
  });
  return {
    element: root, input,
    getSelected: () => chosen && norm(input.value) === norm(chosen.SKU) ? chosen : null,
    focus: () => input.focus(),
    clear: () => { chosen = null; input.value = ""; close();
      summary.textContent = "Choose an exact SKU. A shared EAN may have different MRPs."; },
  };
}
