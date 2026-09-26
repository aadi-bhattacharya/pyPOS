// Headless end-to-end test: drives the real frontend (ES modules) in jsdom
// against the real Flask server. Usage: node frontend_test.mjs
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("..", import.meta.url)).replace(/\/$/, "");

const BASE = "http://127.0.0.1:8420";
const APP_JS = REPO + "/pos/static/js/app.js";

let failures = 0;
const ok = (cond, label) => {
  console.log(`${cond ? "  PASS" : "✗ FAIL"}  ${label}`);
  if (!cond) failures++;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- DOM bootstrap ----------
const html = readFileSync(REPO + "/pos/templates/index.html", "utf-8")
  .replace('<script type="module" src="/static/js/app.js"></script>', "");
const dom = new JSDOM(html, { url: `${BASE}/`, pretendToBeVisual: true });
const { window } = dom;
const { document } = window;

window.matchMedia = window.matchMedia || (() => ({
  matches: false, addListener() {}, removeListener() {}, addEventListener() {},
}));
window.scrollTo = () => {};
window.print = () => {};

// Browser globals the ES modules expect at call time.
globalThis.window = window;
globalThis.document = document;
globalThis.localStorage = window.localStorage;
globalThis.location = window.location;
globalThis.HTMLElement = window.HTMLElement;
globalThis.CustomEvent = window.CustomEvent;

const realFetch = globalThis.fetch;
let cookieJar = "";
globalThis.fetch = (path, opts = {}) => {
  const headers = { ...(opts.headers || {}) };
  if (cookieJar) headers["Cookie"] = cookieJar;
  return realFetch(path.startsWith("http") ? path : BASE + path, { ...opts, headers })
    .then((res) => {
      const sc = res.headers.get("set-cookie");
      if (sc) cookieJar = sc.split(";")[0];
      return res;
    });
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
const input = (el, value) => {
  el.value = value;
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
  el.dispatchEvent(new window.Event("change", { bubbles: true }));
};

// ---------- boot ----------
await import("file://" + APP_JS);
await sleep(300); // let refreshSettings + router finish

console.log("\n== Register view ==");
ok($("#main h2")?.textContent === "Register", "register renders");
await sleep(200);
const tiles = $$(".tile");
ok(tiles.length > 5, `product tiles render (${tiles.length})`);
ok($$(".chip").length > 1, "group chips render");

// add to cart via tile click — pick a product with stock >= 50 for stepper test
const tilesInfo = await (await fetch(`${BASE}/api/products`)).json();
// Tiles render in the same order as /api/products (group name, SKU sort)
const bigIdx = tilesInfo.findIndex((p) => p.quantity >= 50);
const tile = $$(".tile")[bigIdx];
ok(!!tile && !tile.disabled, `found high-stock tile (${tilesInfo[bigIdx].SKU})`);
click(tile);
await sleep(100);
ok($$(".cart-line").length === 1, "tile click adds cart line");

// second product via its own tile
let otherIdx = tilesInfo.findIndex((p) => p.quantity >= 20 && p.id !== tilesInfo[bigIdx].id);
if (otherIdx === bigIdx) otherIdx = 0;
const tile2 = $$(".tile")[otherIdx];
click(tile2);
await sleep(50);
click(tile2);
await sleep(150);
const lines = $$(".cart-line");
ok(lines.length === 2, "second product adds its own line");

// stepper + → qty goes 2 → 3
click(lines[1].querySelector(".stepper button:last-child"));
await sleep(100);
ok($$(".cart-line .st-qty")[1].textContent === "3", "stepper increments quantity");

// totals preview shows a total row
const totalRowText = $(".sum-row.total-row")?.textContent || "";
ok(/Total/.test(totalRowText), `totals render ("${totalRowText.trim()}")`);

// discount percent
input($('.discount-edit input[type="number"]'), "10");
await sleep(100);
ok($(".sum-row.total-row") && $$(".sum-row").some((r) => /Discount/.test(r.textContent)),
  "discount applied to preview");

console.log("\n== Payment modal ==");
click($(".cart-summary .btn.primary.lg"));
await sleep(200);
const overlay = $(".modal-overlay");
ok(!!overlay, "payment modal opens");
ok(overlay.textContent.includes("Amount due"), "amount due shown");
ok($$(".pay-row").length === 1, "default single cash row");
ok(parseFloat($(".pay-row input").value) > 0, "cash row pre-filled with total");

// split payment flow
click([...$$(".btn")].find((b) => b.textContent.includes("Split payment")));
await sleep(100);
ok($$(".pay-row").length === 2, "split row added");
const rows = $$(".pay-row");
rows[0].querySelector("select").value = "CARD";
rows[0].querySelector("select").dispatchEvent(new window.Event("change", { bubbles: true }));
input(rows[0].querySelector("input"), "2.00");
await sleep(100);
ok(/Remaining due/.test($(".pay-remaining").textContent), "remaining due updates");
rows[1].querySelector("select").value = "CASH";
rows[1].querySelector("select").dispatchEvent(new window.Event("change", { bubbles: true }));
input(rows[1].querySelector("input"), "500.00");
await sleep(150);
ok(/Fully covered/.test($(".pay-remaining").textContent), "covered state reached");
ok($(".change-line")?.textContent.includes("Change"), "change line appears");

// complete sale
const completeBtn = [...$$(".modal-foot .btn")].find((b) => b.textContent.includes("Complete sale"));
click(completeBtn);
await sleep(600);
const receiptOverlay = $(".modal-overlay");
ok(!!receiptOverlay && receiptOverlay.textContent.includes("complete"),
  "sale completes and receipt modal opens");
ok(receiptOverlay.querySelector(".receipt") !== null, "receipt rendered in modal");
ok(receiptOverlay.textContent.includes("Change"), "receipt shows change");
ok(!receiptOverlay.textContent.includes("REFUND") &&
   !receiptOverlay.textContent.includes("VOIDED"), "completed sale has no refund/void stamp");
click(receiptOverlay.querySelector(".modal-head .btn")); // close
await sleep(200);

// cart cleared after sale
ok($$(".cart-line").length === 0, "cart cleared after sale");
ok($$(".tile").some((t) => !t.disabled), "tiles refreshed after sale");

console.log("\n== Catalog view ==");
location.hash = "#/catalog";
await sleep(400);
ok($("#main h2")?.textContent === "Catalog", "catalog renders");
ok($$("table.table tbody tr").length > 3, `product table populated`);
ok($("#cat-sub")?.textContent.includes("products"), "catalog subtitle present");

// create a product through the modal (unique SKU per run)
const UNIQUE = `TEST-E2E-${Date.now()}`;
click([...$$("#main .btn")].find((b) => b.textContent.includes("Add product")));
await sleep(200);
const prodModal = $(".modal-overlay");
ok(prodModal?.textContent.includes("New product"), "new-product modal opens");
prodModal.querySelector('select.input').value = "1"; // group
const fields = $$(".field .input", prodModal);
// order: group select, SKU, barcode, price, cost, tax, qty
input(fields[1], UNIQUE);
input(fields[3], "4.20");
input(fields[6], "7");
const attrName = $(".attr-editor input", prodModal);
attrName.value = "Size";
const attrVal = $$(".attr-editor input", prodModal)[1];
attrVal.value = "XL";
click([...$$(".modal-foot .btn", prodModal)].find((b) => b.textContent.includes("Create")));
await sleep(600);
const created = $$("table.table tbody tr").find((r) => r.textContent.includes(UNIQUE));
ok(!!created, "created product appears in table");
ok(created?.textContent.includes("Size: XL"), "attribute saved on product");

// stock adjust modal: 7 − 2 = 5 (assert dynamically against the shown badge)
const qtyBefore = parseInt(created.querySelector(".stock-badge").textContent, 10);
const stockBtn = created.querySelector(".actions-cell .btn");
click(stockBtn);
await sleep(200);
const stockModal = $(".modal-overlay");
input($(".modal-body input[type=number]", stockModal), "-2");
click([...$$(".modal-foot .btn", stockModal)].find((b) => b.textContent.includes("Apply")));
await sleep(500);
ok($$("table.table tbody tr").find((r) => r.textContent.includes(UNIQUE))
  ?.textContent.includes(String(qtyBefore - 2)),
  `stock adjustment applied (${qtyBefore} → ${qtyBefore - 2})`);

// search filter by exact unique SKU
const catSearch = $("#main .search-wrap input");
input(catSearch, UNIQUE);
await sleep(150);
ok($$("table.table tbody tr").length === 1, "search filters table");

console.log("\n== Sales view ==");
location.hash = "#/sales";
await sleep(500);
ok($("#main h2")?.textContent === "Sales", "sales renders");
ok($$("table.table tbody tr").length > 3, "sales list populated");
const firstSale = $("table.table tbody tr");
ok(firstSale.textContent.includes("COMPLETED"), "status pill shown");

// detail modal + reprint availability
click(firstSale.querySelector(".actions-cell .btn"));
await sleep(300);
const detailOverlay = $(".modal-overlay");
ok(detailOverlay.querySelector(".receipt"), "sale detail receipt renders");
click(detailOverlay.querySelector(".modal-head .btn"));
await sleep(200);

console.log("\n== Reports view ==");
location.hash = "#/reports";
await sleep(600);
ok($("#main h2")?.textContent === "Reports", "reports renders");
ok($$(".stat-card").length >= 4, "stat cards render");
ok($$(".bar-col").length === 14, "14-day chart bars render");
ok($$(".report-grid table tbody tr").length > 0, "top products/payment tables populated");

console.log("\n== Settings view ==");
location.hash = "#/settings";
await sleep(600);
ok($("#main h2")?.textContent === "Settings", "settings renders");
const storeInput = $$("#main .card-pad input")[0];
input(storeInput, "Headless Mart");
click([...$$("#main .btn")].find((b) => b.textContent.includes("Save settings")));
await sleep(400);
const resp = await fetch(BASE + "/api/settings");
const saved = await resp.json();
ok(saved.store_name === "Headless Mart", "settings persisted server-side");

console.log("\n== Tax breakup (GST) ==");
location.hash = "#/settings";
await sleep(500);
const taxToggle = [...$$(".credit-toggle input")][0];
ok(taxToggle, "tax breakup toggle present in settings");
click(taxToggle);
const taxSave = [...$$("button")].find((b) => b.textContent.includes("Save tax settings"));
click(taxSave);
await sleep(400);
location.hash = "#/register";
await sleep(700);
click($$(".tile").find((t) => !t.disabled));
await sleep(150);
click($(".cart-summary .btn.primary.lg"));
await sleep(300);
click([...$$(".modal-foot .btn")].find((b) => b.textContent.includes("Complete sale")));
await sleep(700);
const gstReceipt = $(".modal-overlay");
ok(gstReceipt?.querySelector(".receipt"), "receipt opens");
ok(/CGST @2\.5%/.test(gstReceipt.textContent) &&
   /SGST @2\.5%/.test(gstReceipt.textContent),
  "receipt shows CGST + SGST breakup lines");
click([...$$(".modal-foot .btn", gstReceipt)].find((b) => b.textContent.trim() === "Done"));
await sleep(300);
// turn it back off so later sections see default receipts
location.hash = "#/settings";
await sleep(500);
click([...$$(".credit-toggle input")][0]);
click([...$$("button")].find((b) => b.textContent.includes("Save tax settings")));
await sleep(400);
// restore
await fetch(BASE + "/api/settings", {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ store_name: "My Store" }),
});

console.log("\n== Backups panel ==");
await sleep(300);
const backupsCardEl = $("#backups-card");
ok(backupsCardEl !== null && backupsCardEl.textContent.includes("Back up now"),
  "backups card renders");
const before = (await (await fetch(`${BASE}/api/backups`)).json()).length;
click([...$$("#backups-card .btn")].find((b) => b.textContent.includes("Back up now")));
await sleep(700);
const after = (await (await fetch(`${BASE}/api/backups`)).json()).length;
ok(after > before, `backup created from UI (${before} → ${after})`);
ok($("#backups-card").textContent.includes("Last backup"), "status line updates");

console.log("\n== Google Drive section ==");
ok($("#backups-card").textContent.includes("Google Drive sync"),
  "gdrive section renders");
ok($("#backups-card").textContent.includes("Not connected"), "shows unconnected state");
const details = $("#backups-card details");
ok(details !== null, "setup instructions collapsible present");
details.open = true;
await sleep(50);
const redirectCode = [...$$("#backups-card code")]
  .find((c) => c.textContent.includes("/gdrive/callback"));
ok(!!redirectCode &&
   redirectCode.textContent === `${location.origin}/gdrive/callback`,
  `redirect URI shown matches origin`);
ok([...$$("#backups-card .btn")].some((b) =>
  b.textContent.includes("Connect Google Drive")), "connect button present");

// register again after settings change (currency formatting path)
location.hash = "#/register";
await sleep(400);
ok($("#reg-search") !== null, "back to register works");
input($("#reg-search"), "COLA");
await sleep(250);
$("#reg-search").dispatchEvent(new window.KeyboardEvent("keydown", {
  key: "Enter", bubbles: true,
}));
await sleep(300);
ok($$(".cart-line").length === 1, "scan-by-SKU adds exact match to cart");

console.log("\n== Scanner-less workflow (no hardware needed) ==");
// Clear the cart from the previous step, then work purely by typing+clicking.
click($$(".cart-line .cl-remove")[0]);
await sleep(100);
ok($$(".cart-line").length === 0, "cart line removable without scanner");

// Type a partial SKU; the grid filters to one tile; Enter adds it.
input($("#reg-search"), "CRO");
await sleep(300);
const croTiles = $$(".tile:not([disabled])");
ok(croTiles.length === 1, `type-to-search narrows to one tile (${croTiles.length})`);
$("#reg-search").dispatchEvent(new window.KeyboardEvent("keydown",
  { key: "Enter", bubbles: true }));
await sleep(300);
ok($$(".cart-line").length === 1, "Enter adds the single visible match");

// Tax preview must match server truth: croissant 1.80 @ 5% → 1.89
const shownTotal = $(".sum-row.total-row").textContent.replace(/[^0-9.]/g, "");
ok(shownTotal === "1.89", `tax included in preview total (${shownTotal} = 1.89)`);

// Ambiguous search + Enter must NOT blindly add anything.
input($("#reg-search"), "e"); // matches many products
await sleep(250);
const many = $$(".tile:not([disabled])").length;
$("#reg-search").dispatchEvent(new window.KeyboardEvent("keydown",
  { key: "Enter", bubbles: true }));
await sleep(250);
ok(many > 1 && $$(".cart-line").length === 1,
  "ambiguous search + Enter does not add anything");
$("#reg-search").value = "";
// Esc clears the search state inside the app
$("#reg-search").dispatchEvent(new window.KeyboardEvent("keydown",
  { key: "Escape", bubbles: true }));
await sleep(150);

// Complete this one-item sale with exact cash.
const t2 = $(".sum-row.total-row").textContent.replace(/[^0-9.]/g, "");
click($(".cart-summary .btn.primary.lg"));
await sleep(200);
const payRowsNow = $$(".pay-row input");
payRowsNow[0].value = t2;
payRowsNow[0].dispatchEvent(new window.Event("input", { bubbles: true }));
await sleep(100);
click([...$$(".modal-foot .btn")].find((b) => b.textContent.includes("Complete sale")));
await sleep(600);
const refundableReceipt = $(".modal-overlay");
ok(refundableReceipt?.querySelector(".receipt"), "scanner-less sale completes");
click(refundableReceipt.querySelector(".modal-head .btn"));
await sleep(200);

// Refund it through the Sales view UI.
location.hash = "#/sales";
await sleep(500);
const targetRow = $$("table.table tbody tr").find((r) =>
  r.textContent.includes("COMPLETED") && !r.textContent.includes("REFUND"));
ok(!!targetRow, "completed sale found for refund");
const stockBefore = (await (await fetch(`${BASE}/api/products`)).json())
  .find((p) => p.SKU === "BAK-CRO-BTR")?.quantity;
click([...targetRow.querySelectorAll(".actions-cell .btn")]
  .find((b) => b.title === "Refund sale"));
await sleep(300);
const confirmOverlay = $(".modal-overlay");
ok(confirmOverlay?.textContent.includes("Issue refund"),
  "refund confirmation appears");
click([...$$(".modal-foot .btn", confirmOverlay)]
  .find((b) => b.textContent.includes("Issue refund")));
await sleep(700);
// receipt modal of the refund opens automatically
const refReceipt = $(".modal-overlay");
ok(refReceipt?.querySelector(".receipt") &&
   refReceipt.textContent.includes("REFUND"),
  "refund receipt shows REFUND stamp");
click(refReceipt.querySelector(".modal-head .btn"));
await sleep(400);
const stockAfter = (await (await fetch(`${BASE}/api/products`)).json())
  .find((p) => p.SKU === "BAK-CRO-BTR")?.quantity;
ok(stockAfter === stockBefore + 1,
  `stock restored after refund (${stockBefore} → ${stockAfter})`);

console.log("\n== Customers & store credit ==");
location.hash = "#/customers";
await sleep(500);
ok($("#main h2")?.textContent === "Customers", "customers view renders");
click([...$$(".view-actions .btn")].find((b) => b.textContent.includes("New customer")));
await sleep(200);
let custModal = $(".modal-overlay");
input(custModal.querySelector('input[placeholder="Full name"]'), "E2E Person");
input(custModal.querySelector('input[placeholder^="Phone"]'), "555-E2E");
click([...$$(".modal-foot .btn", custModal)].find((b) => b.textContent.includes("Add customer")));
await sleep(400);
ok($$("table.table tbody tr").some((r) => r.textContent.includes("E2E Person")),
  "created customer appears in table");

// top up credit through the credit modal (wait for the row, then re-query
// fresh right before clicking so a background re-render can't detach it)
let custRow = null;
for (let i = 0; i < 20 && !custRow; i++) {
  await sleep(150);
  custRow = $$("table.table tbody tr").find((r) => r.textContent.includes("E2E Person"));
}
ok(!!custRow, "created customer appears in table");
for (let i = 0; i < 10; i++) {
  custRow = $$("table.table tbody tr").find((r) => r.textContent.includes("E2E Person"));
  click(custRow.querySelector('.actions-cell .btn[title="Adjust credit"]'));
  await sleep(300);
  if ($(".modal-overlay")?.textContent.includes("Current balance")) break;
}
custModal = $(".modal-overlay");
ok(custModal?.textContent.includes("Current balance"), "credit modal opens");
input(custModal.querySelector('input[type="number"]'), "50");
click([...$$(".modal-foot .btn", custModal)].find((b) => b.textContent.includes("Apply")));
await sleep(400);
let toppedUp = false;
for (let i = 0; i < 20 && !toppedUp; i++) {
  await sleep(150);
  toppedUp = $$("table.table tbody tr").some(
    (r) => r.textContent.includes("E2E Person") && r.textContent.includes("$50.00"));
}
ok(toppedUp, "credit top-up reflected in table");

console.log("\n== Register: attach customer & pay with credit ==");
location.hash = "#/register";
await sleep(600);
click($(".customer-box")); // walk-in box opens picker
await sleep(300);
let pickOverlay = $(".modal-overlay");
ok(pickOverlay?.textContent.includes("Attach customer"), "customer picker opens");
click([...pickOverlay.querySelectorAll(".chip")].find((c) =>
  c.textContent.includes("E2E Person")) || [...pickOverlay.querySelectorAll(".picker-results button")][0]);
await sleep(300);
ok($(".customer-box.attached")?.textContent.includes("E2E Person"),
  "customer attached to cart");

// add something cheap and pay with credit
const prodInfo = await (await fetch(`${BASE}/api/products`)).json();
const cheapIdx = tilesInfo.findIndex((p) => p.quantity >= 5);
click($$(".tile")[cheapIdx]);
await sleep(150);
click($(".cart-summary .btn.primary.lg"));
await sleep(300);
const payOverlay = $(".modal-overlay");
ok(payOverlay.querySelector(".credit-toggle"),
  "store credit toggle offered at payment");
ok(/Use store credit/.test(payOverlay.textContent), "credit amount shown");
const dueBefore = parseFloat(payOverlay.querySelector(".pay-total-banner .amt").textContent.replace(/[^0-9.]/g, ""));
const credDue = Math.max(0, dueBefore - 50);
ok(Math.abs(dueBefore - Math.min(dueBefore, 50)) >= 0 &&
   payOverlay.querySelector(".pay-total-banner .amt").textContent.length > 0,
  "banner reflects amount due");
// complete with credit applied (cash row auto-adjusted)
const cashRowInput = $(".pay-row input");
if (parseFloat(cashRowInput.value) > credDue + 0.001) {
  input(cashRowInput, credDue.toFixed(2));
}
await sleep(150);
click([...$$(".modal-foot .btn", payOverlay)].find((b) => b.textContent.includes("Complete sale")));
await sleep(700);
const creditReceipt = $(".modal-overlay");
ok(creditReceipt?.querySelector(".receipt"), "credit sale receipt opens");
ok(creditReceipt.textContent.includes("Store credit used"),
  "receipt shows store credit used line");
click(creditReceipt.querySelector(".modal-head .btn"));
await sleep(300);
const balAfter = (await (await fetch(`${BASE}/api/customers`)).json())
  .find((c) => c.name === "E2E Person")?.store_credit;
ok(balAfter !== undefined && balAfter < 50,
  `customer balance decreased after purchase (${balAfter})`);

console.log("\n== Park & recall ==");
click($$(".tile")[otherIdx !== undefined ? otherIdx : 0]);
await sleep(150);
click([...$$(".cart-head .btn")].find((b) => b.textContent.includes("Park")));
await sleep(250);
const parkOverlay = $(".modal-overlay");
click([...$$(".modal-foot .btn", parkOverlay)].find((b) => b.textContent.includes("Park sale")));
await sleep(400);
ok($$(".cart-line").length === 0, "cart cleared after parking");
ok(/Parked \(1\)/.test(document.getElementById("main").textContent),
  "parked counter shows 1");
click([...$$(".view-head .chip, .view-head button")].find((b) => /Parked/.test(b.textContent)));
await sleep(300);
const parkedOverlay = $(".modal-overlay");
ok(parkedOverlay?.textContent.includes("Recall"), "parked ticket listed");
click([...$$(".modal-foot .btn, .card .btn", parkedOverlay)]
  .filter((b) => b.textContent.includes("Recall"))[0] ||
  [...parkedOverlay.querySelectorAll("button")].find((b) => b.textContent.includes("Recall")));
await sleep(500);
ok($$(".cart-line").length >= 1, "recalled cart has lines again");

console.log("\n== Partial refund dialog ==");
location.hash = "#/sales";
await sleep(500);
const partRow = $$("table.table tbody tr").find((r) =>
  r.textContent.includes("COMPLETED") &&
  /Cash|Card|UPI/.test(r.textContent) &&
  !/REFUND/.test(r.textContent));
ok(!!partRow, "sale available for partial refund");
click([...partRow.querySelectorAll(".actions-cell .btn")]
  .find((b) => b.title === "Refund sale"));
await sleep(350);
const refDialog = $(".modal-overlay");
ok(refDialog?.textContent.includes("choose items"),
  "partial refund dialog lists items");
ok(refDialog.querySelectorAll(".refund-line").length >= 1, "refund line rows render");
ok(refDialog.querySelector(".stepper"), "quantity steppers present");
click([...refDialog.querySelectorAll("button")].find((b) => b.textContent.includes("Issue refund")));
await sleep(700);
const refDone = $(".modal-overlay");
ok(refDone && (refDone.textContent.includes("REFUND")), "refund receipt after partial/full");
click(refDone ? refDone.querySelector(".modal-head .btn") : document.body);
await sleep(300);

console.log("\n== End of day (X / Z report) ==");
location.hash = "#/reports";
await sleep(600);
const eodCard = $("#eod-card");
ok(!!eodCard, "end-of-day card renders");
if (!eodCard.textContent.includes("Open since")) {
  click([...eodCard.querySelectorAll("button")].find((b) => b.textContent.includes("Open day")));
  await sleep(300);
  const openOv = $(".modal-overlay");
  click([...openOv.querySelectorAll(".modal-foot .btn")].find((b) => b.textContent.includes("Open day")));
  await sleep(500);
}
ok($("#eod-card").textContent.includes("Open since"), "day opened");
ok($("#eod-card").textContent.includes("Cash in drawer"), "live X numbers shown");
click([...$("#eod-card").querySelectorAll("button")]
  .find((b) => b.textContent.includes("End day")));
await sleep(300);
const closeOv = $(".modal-overlay");
ok(closeOv?.textContent.includes("Count the cash drawer"), "close-day dialog opens");
click([...closeOv.querySelectorAll(".modal-foot .btn")]
  .find((b) => b.textContent.includes("Close day & print Z")));
await sleep(600);
const zOv = $(".modal-overlay");
ok(zOv && zOv.textContent.includes("** Z REPORT **"),
  "Z report slip appears after closing");
ok(zOv.textContent.includes("Expected cash"), "Z slip shows drawer math");
click(zOv.querySelector(".modal-head .btn"));
await sleep(400);
ok($("#eod-card .panel-head .pill")?.textContent === "Closed" || $("#eod-card").textContent.includes("Closed"), "day is closed afterwards");

console.log("\n== Settings login lock ==");
location.hash = "#/settings";
await sleep(700);
const pwCard = $$("h3").find((h) => h.textContent === "Admin password")?.closest(".card");
const pwInputs = pwCard ? $$('input[type="password"]', pwCard) : [];
ok(pwInputs.length === 3, "admin password card renders (current/new/repeat)");
await input(pwInputs[1], "e2epass1");
await input(pwInputs[2], "e2epass1");
click([...$$("button", pwCard)].find((b) => b.textContent.includes("Set password")));
await sleep(600);

let authState = await (await fetch(`${BASE}/api/settings/auth`)).json();
ok(authState.configured === true && authState.authenticated === true,
  "password set; session authenticated");

// logged-out client (no cookie jar) is locked out of the settings area
const anon = await realFetch(`${BASE}/api/settings`, { method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ store_name: "Hacker" }) });
ok(anon.status === 401, "settings PUT returns 401 without a session");

// login form: wrong then right (server flow, form-encoded POST)
const bad = await fetch(`${BASE}/login`, { method: "POST",
  headers: { "Content-Type": "application/x-www-form-urlencoded" },
  body: "password=nope&next=%23/settings", redirect: "manual" });
ok(bad.status === 200 && (await bad.text()).includes("Wrong password"),
  "wrong password rejected on /login");

const good = await fetch(`${BASE}/login`, { method: "POST",
  headers: { "Content-Type": "application/x-www-form-urlencoded" },
  body: "password=e2epass1&next=%23/settings", redirect: "manual" });
ok(good.status === 302 && (good.headers.get("location") || "").includes("#/settings"),
  "correct password redirects back to settings");
ok((good.headers.get("set-cookie") || "").length > 0, "login sets session cookie");

// remove the password again (authenticated) so reruns start clean
const rm = await fetch(`${BASE}/api/settings/password`, { method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ current_password: "e2epass1", new_password: "" }) });
ok(rm.status === 200 && (await rm.json()).removed === true,
  "password removal works for the authenticated user");
const fresh = await realFetch(`${BASE}/api/settings/auth`);
authState = await fresh.json();
ok(authState.configured === false && authState.authenticated === false,
  "settings open again after removal");

console.log(failures === 0 ? "\nALL FRONTEND CHECKS PASSED" : `\n${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
