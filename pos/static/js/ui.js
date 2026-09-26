// DOM builder, toasts, modals, confirms.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${name}`);
  svg.append(use);
  return svg;
}

// ---------- toasts ----------
export function toast(message, kind = "info", opts = {}) {
  const root = document.getElementById("toast-root");
  const t = h("div", { class: `toast ${kind}` },
    icon(kind === "success" ? "i-check" : kind === "error" ? "i-alert" : "i-alert"),
    h("div", { class: "toast-msg" },
      opts.title ? h("div", { class: "toast-title" }, opts.title) : null,
      h("div", { class: "toast-detail" }, message)),
    h("button", {
      class: "btn ghost icon-only sm",
      onclick: () => t.remove(),
    }, icon("i-x")),
  );
  root.append(t);
  setTimeout(() => {
    t.style.transition = "opacity .25s";
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 260);
  }, opts.duration ?? (kind === "error" ? 6000 : 3500));
}

export function toastError(e, title = "Something went wrong") {
  toast(String(e.message || e), "error", { title });
}

// ---------- modals ----------
let openModals = [];

export function openModal({ title = "", body, footer = [], wide = false, narrow = false,
                            onClose = null }) {
  const overlay = h("div", { class: "modal-overlay" });
  const modal = h("div", {
    class: `modal${wide ? " wide" : ""}${narrow ? " narrow" : ""}`,
    role: "dialog", "aria-modal": "true",
  });
  let closedByUs = false;

  const close = () => {
    if (closedByUs) return;
    closedByUs = true;
    document.removeEventListener("keydown", api._escHandler);
    overlay.remove();
    openModals = openModals.filter((m) => m !== api);
    if (onClose) onClose();
  };
  const api = { close };

  const footEl = footer && footer.length
    ? h("div", { class: "modal-foot" }, ...footer)
    : null;

  modal.append(
    h("div", { class: "modal-head" },
      h("h3", {}, title),
      h("button", { class: "btn ghost icon-only", "aria-label": "Close", onclick: close },
        icon("i-x"))),
    h("div", { class: "modal-body" }, body),
  );
  if (footEl) modal.append(footEl);

  overlay.append(modal);
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) close();
  });
  api._escHandler = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", api._escHandler);

  document.getElementById("modal-root").append(overlay);
  openModals.push(api);
  const focusable = modal.querySelector("input, select, textarea, button.btn.primary");
  if (focusable) setTimeout(() => focusable.focus(), 30);
  return api;
}

export function closeModalAll() {
  [...openModals].forEach((m) => m.close());
}

export function confirmDialog({ title = "Are you sure?", message = "", confirmLabel = "Confirm",
                                danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    const okBtn = h("button", {
      class: `btn ${danger ? "danger" : "primary"}`,
      onclick: () => { result = true; m.close(); },
    }, confirmLabel);
    const m = openModal({
      title,
      narrow: true,
      body: h("p", { style: "color:var(--text-2)" }, message),
      footer: [
        h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
        okBtn,
      ],
      onClose: () => resolve(result),
    });
  });
}

// Renders receipt HTML into the hidden print area and opens the print dialog.
export function printReceipt(receiptEl) {
  const root = document.getElementById("print-root");
  root.innerHTML = "";
  root.append(receiptEl.cloneNode(true));
  window.print();
}
