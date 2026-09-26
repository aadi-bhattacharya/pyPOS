import { refreshSettings } from "./api.js";
import { h, icon, openModal, toastError } from "./ui.js";
import { renderRegister } from "./views/register.js";
import { renderCatalog } from "./views/catalog.js";
import { renderCustomers } from "./views/customers.js";
import { renderSales } from "./views/salesview.js";
import { renderReports } from "./views/reports.js";
import { renderSettingsView } from "./views/settingsview.js";

const routes = {
  register: renderRegister,
  catalog: renderCatalog,
  customers: renderCustomers,
  sales: renderSales,
  reports: renderReports,
  settings: renderSettingsView,
};

let currentView = null;

export function navigate(view) {
  if (location.hash !== `#/${view}`) {
    location.hash = `#/${view}`;
  } else {
    router();
  }
}

async function router() {
  const name = (location.hash.replace(/^#\//, "") || "register").split("?")[0];
  const view = routes[name] ? name : "register";
  const main = document.getElementById("main");

  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === view);
  });

  main.innerHTML = "";
  main.append(h("div", { class: "skel-page" },
    h("div", { class: "skeleton w40" }),
    h("div", { class: "skeleton" }),
    h("div", { class: "skeleton" }),
    h("div", { class: "skeleton" }),
    h("div", { class: "skeleton" }),
    h("div", { class: "skeleton w60" })));
  currentView = view;
  try {
    await routes[view](main);
  } catch (e) {
    toastError(e, `Failed to load ${view}`);
    main.innerHTML = "";
    main.append(h("div", { class: "empty-state" },
      icon("i-alert"),
      h("h3", {}, "Could not load this page"),
      h("p", {}, String(e.message || e))));
  }
}

function initTheme() {
  // Light is the practical default for a bright shop floor; the toggle
  // switches and persists dark mode.
  const theme = localStorage.getItem("pypos.theme") || "light";
  document.documentElement.dataset.theme = theme;
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("pypos.theme", next);
  });
}

function showShortcuts() {
  openModal({
    title: "Keyboard shortcuts",
    narrow: true,
    body: h("div", { class: "shortcut-list" },
      sc([h("kbd", {}, "F2"), h("kbd", {}, "/")], "Focus register search"),
      sc([h("kbd", {}, "Enter")], "Scan / add exact SKU or barcode match"),
      sc([h("kbd", {}, "F4")], "Open payment at the register"),
      sc([h("kbd", {}, "+"), h("kbd", {}, "-")], "Adjust selected cart line quantity"),
      sc([h("kbd", {}, "Esc")], "Close dialogs / clear search"),
      sc([h("kbd", {}, "?")], "This help")),
  });
}

function sc(keys, label) {
  return h("div", { class: "sc" }, h("span", {}, ...keys), h("span", {}, label));
}

function globalKeys(e) {
  const tag = document.activeElement?.tagName;
  const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

  if (e.key === "?" && !typing) {
    e.preventDefault();
    showShortcuts();
    return;
  }
  if (e.key === "F4") {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent("pos:pay"));
    return;
  }
  if (e.key === "F6") {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent("pos:park"));
    return;
  }
  if (e.key === "F2" || (e.key === "/" && !typing)) {
    e.preventDefault();
    navigate("register");
    setTimeout(() => document.getElementById("reg-search")?.focus(), 60);
    return;
  }
}

window.addEventListener("hashchange", router);
document.addEventListener("keydown", globalKeys);

(async function boot() {
  initTheme();
  document.getElementById("help-btn").addEventListener("click", showShortcuts);
  await refreshSettings();
  await router();
})();
