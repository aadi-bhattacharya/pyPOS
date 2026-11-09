import { api, can, fmt, fmtDate, state } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, printReceipt } from "../ui.js";
import { methodLabel } from "../receipt.js";
import { buildZReceipt } from "../zreport.js";

const RANGES = [["7", "7 days"], ["14", "14 days"], ["30", "30 days"], ["90", "90 days"]];
let rangeDays = "14";
let activeSection = "overview";
let chartMeasure = "revenue";

export async function renderReports(main) {
  main.replaceChildren(
    h("div", { class: "skel-stats" }, ...Array.from({ length: 4 }, () => h("div", { class: "skeleton" }))),
    h("div", { class: "skel-bars skeleton" }));
  let data, session, sessions;
  try {
    [data, session, sessions] = await Promise.all([
      api.reportSummary(Number(rangeDays)), api.currentSession(), api.listSessions(),
    ]);
  } catch (e) { main.replaceChildren(); return toastError(e); }

  const first = data.series[0]?.date || "";
  const last = data.series[data.series.length - 1]?.date || "";
  const dates = { from_date: first, to_date: last };
  const qs = new URLSearchParams(dates).toString();
  const refresh = () => renderReports(main);
  const current = data.period;
  const prev = data.previous_period;
  const today = data.today;
  const periodLabel = `${first} to ${last} (IST)`;

  const dayList = h("div", { class: "chips", role: "group", "aria-label": "Report range" },
    ...RANGES.map(([value, label]) => h("button", {
      class: `chip${rangeDays === value ? " active" : ""}`,
      "aria-pressed": rangeDays === value ? "true" : "false",
      onclick: () => { rangeDays = value; refresh(); },
    }, label)));

  const sections = [["overview", "Overview"], ["revenue", "Revenue analysis"],
    ["operations", "Operations & close-out"]];
  const navigation = h("div", { class: "report-tabs", role: "tablist", "aria-label": "Report sections" },
    ...sections.map(([value, label]) => h("button", {
      class: `report-tab${activeSection === value ? " active" : ""}`,
      role: "tab", "aria-selected": activeSection === value ? "true" : "false",
      onclick: () => { activeSection = value; refresh(); },
    }, label)));

  const summaryMetrics = h("div", { class: "stat-grid report-stat-grid" },
    statCard("Net sales after refunds", fmt(current.net_revenue),
      compare(current.net_revenue, prev.net_revenue) + " vs previous equal period"),
    statCard("Sales before refunds", fmt(current.gross_sales),
      plural(current.transactions, "completed checkout")),
    statCard("Average checkout", fmt(current.avg_basket),
      "Completed sales, before later refunds"),
    statCard("Refunds issued", fmt(current.refund_total),
      plural(current.refund_count, "refund document")));

  const todayStrip = h("div", { class: "report-today" },
    h("div", {}, h("strong", {}, "Today in India"), h("span", { class: "cell-sub" }, "Local calendar day · IST")),
    todayChip("Net sales", fmt(today.net_revenue)),
    todayChip("Checkouts", String(today.transactions)),
    todayChip("Refunds", fmt(today.refund_total)),
    todayChip("Average bill", fmt(today.avg_basket)));

  const general = h("div", { class: "report-note" },
    "Net sales = completed invoices minus refund invoices recorded in this period. " +
    "This includes GST, excludes voided invoices, and is not profit, cash deposited, or a GST return. " +
    "Refunds are dated when issued, even if the original sale happened earlier.");

  main.replaceChildren(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" }, h("h2", {}, "Reports"),
        h("div", { class: "sub" }, `Business performance · ${periodLabel}`)),
      h("div", { class: "view-actions" },
        !can("export_data") ? null : h("a", { class: "btn", href: `/api/export/sales.csv?${qs}`,
          title: "Sales and refund records for the selected date range" }, icon("i-sales"), "Sales CSV"),
        !can("export_data") ? null : h("a", { class: "btn", href: `/api/export/sale-lines.csv?${qs}`,
          title: "Invoice item and HSN/SAC detail for the selected range" }, "GST line CSV"),
        h("button", { class: "btn ghost icon-only", title: "Refresh figures", onclick: refresh }, icon("i-refresh")))),
    h("div", { class: "report-toolbar" }, dayList,
      h("span", { class: "report-range" }, `Period: ${periodLabel}`)),
    navigation,
    summaryMetrics,
    activeSection === "overview" ? h("div", { class: "report-page" },
      todayStrip,
      h("div", { class: "report-note" },
        "Daily cash controls: ",
        h("a", { href: "#/cash" }, "Cash Drawer · Start Day / End Day / X–Z reports")),
      h("div", { class: "report-grid" },
        h("div", { class: "card chart-box" },
          panelTitle("Net sales trend", "Sales minus refunds; may fall below zero"),
          revenueChart(data.series, "revenue")),
        h("div", { class: "card" },
          panelTitle("Sales composition", "Amounts for this entire period"),
          breakdown(current, prev),
          h("div", { class: "report-jump" },
            h("button", { class: "btn sm", onclick: () => { activeSection = "revenue"; refresh(); } },
              "See daily breakdown")))),
      h("div", { class: "report-grid" },
        h("div", { class: "card" }, panelTitle("Best-selling items", "Net of returned quantities; tax included"),
          topTable(data.top_products)),
        h("div", { class: "card" }, panelTitle("Stock requiring attention", "Stock at or below reorder threshold"),
          stockTable(data.low_stock))),
      general)
      : activeSection === "revenue" ? h("div", { class: "report-page" },
        todayStrip,
        h("div", { class: "card chart-box" },
          h("div", { class: "report-chart-header" },
            panelTitle("Daily revenue", "Select a measure; hover a day for detail"),
            h("div", { class: "chips", role: "group", "aria-label": "Chart measure" },
              ...[["revenue", "Net"], ["gross", "Gross"], ["refunds", "Refunds"]].map(([v, label]) =>
                h("button", { class: `chip${chartMeasure === v ? " active" : ""}`,
                  "aria-pressed": chartMeasure === v ? "true" : "false",
                  onclick: () => { chartMeasure = v; refresh(); } }, label)))),
          revenueChart(data.series, chartMeasure)),
        h("div", { class: "card" },
          panelTitle("Day-by-day detail", "Click any day to inspect its invoices in Sales"),
          dailyTable(data.series)),
        general)
      : h("div", { class: "report-page" },
        todayStrip,
        endOfDayCard(session, sessions, refresh),
        h("div", { class: "report-grid" },
          h("div", { class: "card" }, panelTitle("Payment methods", "Net tender amount after same-period refunds; excludes store credit"),
            mixTable(data.payment_mix)),
          h("div", { class: "card" }, panelTitle("Inventory alerts", "Check the Catalog to replenish stock"),
            stockTable(data.low_stock))),
        h("div", { class: "stat-grid report-stat-grid" },
          statCard("Net units sold", String(current.net_items), "Quantity after returned units"),
          statCard("Invoice discounts", fmt(current.discount_total), "On completed invoices, before refunds"),
          statCard("GST included in net sales", fmt(current.net_tax), "Recorded tax, net of refunds"),
          statCard("Voided invoices", String(current.void_count), "Excluded from net sales")),
        general),
  );
}

// Cash sessions are operational controls, not merely report analytics. Keep
// them reachable directly from the sidebar and from the checkout screen.
export async function renderCashDrawerPage(main) {
  main.replaceChildren(h("div", { class: "skeleton" }));
  let session, history;
  try {
    session = await api.currentSession();
    history = can("view_reports") ? await api.listSessions() : [];
  } catch (e) {
    main.replaceChildren(h("div", { class: "empty-state" },
      h("h3", {}, "Cash drawer unavailable"),
      h("p", {}, String(e.message || e))));
    return toastError(e);
  }
  const refresh = () => renderCashDrawerPage(main);
  const report = session?.report;
  main.replaceChildren(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Cash Drawer"),
        h("div", { class: "sub" },
          "Start Day · Cash in Hand · X read-out · End Day / Z report")),
      h("a", { class: "btn sm", href: "#/register" }, "Back to Register")),
    h("div", { class: "stat-grid report-stat-grid" },
      statCard("Day status", session ? "Open" : "Closed",
        session ? `Session #${session.id} · ${fmtDate(session.opened_at)}` : "Start day to open a cash session"),
      statCard("Cash in hand (expected)", report ? fmt(report.expected_cash) : "—",
        "Opening float + cash collected − cash refunded"),
      statCard("Cash received", report ? fmt(report.cash_in) : "—",
        "Cash payments for the open session"),
      statCard("Cash refunded", report ? fmt(report.cash_out) : "—",
        "Cash returned during the open session")),
    endOfDayCard(session, history, refresh),
    session && can("cash_movements") ? cashMovementPanel(session.report, refresh) : null,
    h("p", { class: "report-note" },
      "Cash in hand is the system's EXPECTED drawer balance, not a physical cash count. " +
      "Count the actual cash when ending the day. Transactions can still be " +
      "entered without an open session, but they will not be assigned retrospectively " +
      "to a later session."),
  );
}

// Compact, actionable cash-drawer controls beside the checkout workflow.
export async function renderCashStatusStrip(target) {
  if (!target?.isConnected) return;
  let session;
  try {
    session = await api.currentSession();
  } catch (_) {
    if (target.isConnected) target.replaceChildren(
      h("span", { class: "reg-cash-note" }, "Cash drawer status unavailable"),
      h("a", { href: "#/cash", class: "btn sm" }, "View Cash Drawer"));
    return;
  }
  if (!target.isConnected) return;
  const refresh = () => renderCashStatusStrip(target);
  if (!session) {
    if (!can("open_day")) {
      target.replaceChildren(h("span", { class: "reg-cash-note" },
        "Day closed · Your account cannot start the day"),
        h("a", { class: "btn sm", href: "#/cash" }, "Cash Drawer"));
      return;
    }
    target.replaceChildren(
      h("div", { class: "reg-cash-ident" },
        h("span", { class: "pill muted" }, "Day closed"),
        h("span", { class: "reg-cash-note" }, "No active cash session")),
      h("div", { class: "reg-cash-actions" },
        h("button", { class: "btn sm primary", onclick: () => openDayModal(refresh) },
          icon("i-play"), "Start Day"),
        h("a", { href: "#/cash", class: "btn sm" }, "History")));
    return;
  }
  const report = session.report;
  target.replaceChildren(
    h("div", { class: "reg-cash-ident" },
      h("span", { class: "pill ok" }, "Day open"),
      h("span", { class: "reg-cash-balance" },
        "Cash in hand (expected): ",
        h("strong", {}, fmt(report.expected_cash))),
      h("span", { class: "reg-cash-note" }, `Session #${session.id}`)),
    h("div", { class: "reg-cash-actions" },
      h("button", { class: "btn sm", onclick: async () => {
        try { showZModal((await api.currentSession()).report); }
        catch (e) { toastError(e); }
      } }, icon("i-eye"), "X Report"),
      !can("close_day") ? null : h("button", { class: "btn sm primary", onclick: () => closeDayModal(session, refresh) },
        icon("i-check"), "End Day"),
      h("a", { href: "#/cash", class: "btn sm" }, "Details")));
}

function compare(current, previous) {
  if (previous === 0) return current === 0 ? "No change" : "No prior baseline";
  const pct = ((current - previous) / Math.abs(previous)) * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function statCard(label, value, hint) {
  return h("div", { class: "stat-card" },
    h("div", { class: "s-label" }, label), h("div", { class: "s-value" }, value),
    hint ? h("div", { class: "s-hint" }, hint) : null);
}

function todayChip(label, value) {
  return h("div", { class: "report-today-item" },
    h("span", { class: "cell-sub" }, label), h("strong", {}, value));
}

function panelTitle(title, description) {
  return h("div", { class: "report-panel-title" }, h("h3", {}, title),
    description ? h("span", { class: "cell-sub" }, description) : null);
}

function breakdown(p, prev) {
  const rows = [
    ["Completed invoices", p.gross_sales, ""],
    ["Less: refunds", -p.refund_total, "negative"],
    ["Net sales (incl. GST)", p.net_revenue, "emphasis"],
  ];
  return h("div", { class: "report-breakdown" },
    ...rows.map(([label, val, klass]) => h("div", { class: `report-breakdown-row ${klass}` },
      h("span", {}, label), h("strong", {}, fmt(val)))),
    h("div", { class: "report-prev" },
      `Previous equal period: ${fmt(prev.net_revenue)} net sales`));
}

// HTML elements cannot be mounted within an SVG tree.
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const child of children.flat(Infinity))
    if (child !== null && child !== undefined) el.append(child?.nodeType ? child : document.createTextNode(String(child)));
  return el;
}

function revenueChart(series, field) {
  if (!series.length) return h("p", { class: "report-muted" }, "No reporting days.");
  const width = 1000, height = 224, inset = 12, n = series.length;
  const numbers = series.map((d) => Number(d[field] || 0));
  const lo = Math.min(0, ...numbers), hi = Math.max(0, ...numbers);
  const span = Math.max(1, hi - lo);
  const x = (i) => inset + (i / Math.max(1, n - 1)) * (width - 2 * inset);
  const y = (v) => height - inset - ((v - lo) / span) * (height - 2 * inset);
  const zero = y(0);
  const points = series.map((d, i) => [x(i), y(numbers[i])]);
  const line = points.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const area = `${line} L${x(n - 1)},${zero} L${x(0)},${zero} Z`;
  const every = Math.ceil(n / 10);
  return h("div", { class: "linechart" },
    h("div", { class: "linechart-head" },
      h("span", { class: "linechart-peak" },
        `High ${fmt(hi)} · Low ${fmt(lo)} · ${series.length} local days`)),
    s("svg", { class: "line-svg", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none",
      role: "img", "aria-label": `Daily ${field} plotted for ${series.length} days` },
      ...[0.25, 0.5, 0.75].map((f) => s("line", {
        x1: 0, x2: width, y1: inset + (height - inset * 2) * f,
        y2: inset + (height - inset * 2) * f, class: "lc-grid" })),
      s("line", { x1: 0, x2: width, y1: zero, y2: zero, class: "lc-baseline" }),
      s("path", { d: area, class: "lc-area" }),
      s("path", { d: line, class: "lc-line" }),
      ...points.map(([px, py]) => s("circle", { cx: px, cy: py, r: n < 16 ? 3 : 1.8,
        class: "report-chart-dot" })),
      ...series.map((d, i) => s("rect", { x: Math.max(0, x(i) - width / (n * 2)), y: 0,
        width: width / n, height: height, class: "lc-hit" },
        s("title", {}, `${d.date} (IST): net ${fmt(d.revenue)}, gross ${fmt(d.gross)}, refunds ${fmt(d.refunds)}, ${d.txns} checkouts`)))),
    h("div", { class: "bars lc-axis" },
      ...series.map((d, i) => h("div", { class: "bar-col" },
        h("span", { class: "bar-date" }, i % every === 0 || i === n - 1 ? d.date.slice(5) : "")))));
}

function dailyTable(series) {
  return h("div", { class: "table-wrap report-table-wrap" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Date (IST)"), h("th", { class: "num" }, "Bills"),
        h("th", { class: "num" }, "Gross"), h("th", { class: "num" }, "Refunds"),
        h("th", { class: "num" }, "Net"), h("th", {}))),
      h("tbody", {}, ...[...series].reverse().map((d) => h("tr", {},
        h("td", { class: "cell-main" }, d.date),
        h("td", { class: "num" }, String(d.txns)),
        h("td", { class: "num" }, fmt(d.gross)),
        h("td", { class: "num" }, fmt(d.refunds)),
        h("td", { class: "num cell-main" }, fmt(d.revenue)),
        h("td", {}, h("a", { class: "report-detail-link", href: `#/sales?date=${d.date}` }, "View sales")))))));
}

function topTable(items) {
  if (!items.length) return h("p", { class: "report-muted" }, "No sold items in this period.");
  return h("div", { class: "table-wrap report-compact-table" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Item"), h("th", { class: "num" }, "Net qty"),
        h("th", { class: "num" }, "Net value"))),
      h("tbody", {}, ...items.map((it) => h("tr", {},
        h("td", {}, h("div", { class: "cell-main" }, it.group_name || it.SKU),
          it.SKU ? h("div", { class: "cell-sub" }, it.SKU) : null),
        h("td", { class: "num" }, String(it.qty)),
        h("td", { class: "num cell-main" }, fmt(it.revenue)))))));
}

function stockTable(items) {
  if (!items.length) return h("p", { class: "report-muted" }, "No products below the configured stock threshold.");
  return h("div", { class: "table-wrap report-compact-table" },
    h("table", { class: "table" }, h("thead", {}, h("tr", {},
      h("th", {}, "Product"), h("th", { class: "num" }, "On hand"))),
      h("tbody", {}, ...items.map((p) => h("tr", {},
        h("td", {}, h("div", { class: "cell-main" }, p.group_name || p.SKU),
          h("div", { class: "cell-sub" }, p.SKU)),
        h("td", { class: "num" }, h("span", { class: `stock-badge ${p.quantity <= 0 ? "out" : "low"}` },
          p.quantity <= 0 ? "Out" : String(p.quantity))))))));
}

function mixTable(mix) {
  if (!mix.length) return h("p", { class: "report-muted" }, "No recorded tender movements in this period.");
  const positive = mix.reduce((sum, m) => sum + Math.max(0, m.amount), 0);
  return h("div", { class: "report-mix" }, ...mix.map((m) =>
    h("div", { class: "report-mix-row" },
      h("div", { class: "report-mix-text" }, h("strong", {}, methodLabel(m.method)),
        h("span", {}, `${m.count} tender entries · ${fmt(m.amount)}`)),
      h("div", { class: "report-meter", role: "img", "aria-label": `${methodLabel(m.method)} share of positive net tender` },
        h("span", { style: `width:${positive > 0 ? Math.min(100, 100 * Math.max(m.amount, 0) / positive) : 0}%` })))));
}

function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

// ================= start / end of day =================

function endOfDayCard(session, sessions, refresh) {
  const rep = session?.report;
  const history = sessions.filter((s) => s.closed_at).slice(0, 5);

  const statusPill = session
    ? h("span", { class: "pill ok" },
        `Open since ${new Date(session.opened_at.replace(" ", "T") + "Z")
          .toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`)
    : h("span", { class: "pill muted" }, "Closed");

  const body = session
    ? h("div", { class: "eod-body" },
        h("div", { class: "eod-grid" },
          eodStat("Transactions", String(rep.transactions)),
          eodStat("Net revenue", fmt(rep.net_revenue)),
          eodStat("Cash in drawer", fmt(rep.expected_cash)),
          eodStat(`Refunds`, fmt(rep.refund_total))),
        h("div", { class: "eod-actions" },
          h("button", {
            class: "btn sm",
            onclick: async () => {
              try { showZModal(await api.currentSession().then((s) => s.report)); }
              catch (e) { toastError(e); }
            },
            title: "Live read-out — does not close the day",
          }, icon("i-eye"), "X read-out"),
          can("close_day") ? h("button", {
            class: "btn sm primary",
            onclick: () => closeDayModal(session, refresh),
          }, icon("i-check"), "End day (Z report)") : null))
    : h("div", { class: "eod-body" },
        h("div", { class: "eod-hint" },
          icon("i-clock"),
          h("span", {},
            h("strong", {}, "Start of day:"),
            " open with your drawer float. ",
            h("strong", {}, "End of day:"),
            " count the cash and file the printable Z report.")),
        can("open_day") ? h("button", {
          class: "btn sm primary",
          onclick: () => openDayModal(refresh),
        }, icon("i-play"), "Open day") : null);

  const historyEl = history.length
    ? h("table", { class: "table" },
        h("thead", {}, h("tr", {},
          h("th", {}, "Day"), h("th", { class: "num" }, "Net"),
          h("th", { class: "num" }, "Counted"),
          h("th", { class: "num" }, "Diff"), h("th", {}))),
        h("tbody", {}, history.map((s) =>
          h("tr", {},
            h("td", {}, new Date(s.opened_at.replace(" ", "T") + "Z")
              .toLocaleDateString(undefined,
                { weekday: "short", month: "short", day: "numeric" })),
            h("td", { class: "num mono" }, fmt(s.net_revenue)),
            h("td", { class: "num mono" },
              s.counted_cash === null ? "—" : fmt(s.counted_cash)),
            h("td", { class: "num mono" },
              s.drawer_difference === null ? "—"
                : h("span", {
                    style: `color:${Math.abs(s.drawer_difference) < 0.005 ? "inherit" : "var(--danger)"}`,
                  }, (s.drawer_difference > 0 ? "+" : "") + fmt(s.drawer_difference))),
            h("td", { class: "actions-cell" },
              h("button", {
                class: "btn ghost icon-only sm", title: "View Z report",
                onclick: async () => {
                  try { showZModal(await api.sessionReport(s.id)); }
                  catch (e) { toastError(e); }
                },
              }, icon("i-printer")))))))
    : null;

  return h("div", { class: "card eod-card", id: "eod-card" },
    h("div", { class: "panel-head" },
      h("h3", {}, "End of day"),
      statusPill),
    body,
    historyEl ? h("div", { class: "eod-history" },
      h("div", { class: "eod-history-title" }, "Recent days"), historyEl) : null);
}

function eodStat(label, value) {
  return h("div", { class: "eod-stat" },
    h("div", { class: "s-label" }, label),
    h("div", { class: "s-value", style: "font-size:17px" }, value));
}

function openDayModal(refresh) {
  const input = h("input", {
    class: "input num", type: "number", step: "0.01", min: "0",
    placeholder: "0.00", value: "0",
  });
  const go = h("button", { class: "btn primary" }, icon("i-play"), "Open day");
  go.addEventListener("click", async () => {
    try {
      await api.openSession(parseFloat(input.value) || 0);
      m.close();
      toast("Day opened — good selling!", "success");
      refresh();
    } catch (e) {
      toastError(e);
    }
  });
  const m = openModal({
    title: "Start of day — open the register",
    narrow: true,
    body: h("div", { class: "field" },
      h("label", {}, "Cash in the drawer right now"),
      input,
      h("div", { class: "form-hint" },
        "This float is the baseline the Z report compares your closing count against.")),
    footer: [
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      go,
    ],
  });
}

function closeDayModal(session, refresh) {
  const rep = session.report;
  const input = h("input", {
    class: "input num", type: "number", step: "0.01",
    placeholder: "0.00", value: Number(rep.expected_cash).toFixed(2),
  });
  const note = h("input", { class: "input", placeholder: "Optional note for the log" });
  const diffEl = h("p", { class: "form-hint" });

  function updateDiff() {
    const counted = parseFloat(input.value);
    if (isNaN(counted)) { diffEl.textContent = ""; return; }
    const diff = Math.round((counted - rep.expected_cash) * 100) / 100;
    diffEl.innerHTML = Math.abs(diff) < 0.005
      ? "Drawer balances exactly."
      : `<strong style="color:var(--danger)">Off by ${(diff > 0 ? "+" : "") + fmt(diff)}</strong> vs expected ${fmt(rep.expected_cash)}`;
  }
  input.addEventListener("input", updateDiff);

  const go = h("button", { class: "btn primary lg" },
    icon("i-check"), "Close day & print Z");
  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      const result = await api.closeSession(
        parseFloat(input.value) || 0, note.value.trim());
      closeModalAll();
      toast(`Day closed — difference ${(result.report.difference > 0 ? "+" : "") +
        fmt(result.report.difference)}.`, "success", { title: "Z report saved" });
      refresh();
      showZModal(result.report);
    } catch (e) {
      toastError(e);
      go.disabled = false;
    }
  });

  openModal({
    title: "End of day — count the drawer",
    narrow: true,
    body: h("div", {},
      h("div", { class: "pay-total-banner" },
        h("span", { class: "lbl" }, "Expected cash in drawer"),
        h("span", { class: "amt" }, fmt(rep.expected_cash))),
      h("div", { class: "field" }, h("label", {}, "Count the cash drawer"), input),
      diffEl,
      h("div", { class: "field" }, h("label", {}, "Note"), note)),
    footer: [
      h("button", { class: "btn", onclick: () => closeModalAll() }, "Cancel"),
      go,
    ],
  });
}

export function showZModal(rep) {
  const el = buildZReceipt(rep);
  const m = openModal({
    title: rep.is_open ? `X read-out — session #${rep.session_id}` : `Z report — session #${rep.session_id}`,
    narrow: true,
    body: el,
    footer: [
      h("button", { class: "btn", onclick: async () => {
        if (state.settings.receipt_print_mode !== "thermal") return printReceipt(el);
        try {
          const job = await api.printSessionReport(rep.session_id);
          toast(`Report queued for ${job.printer}.`, "success");
        } catch (e) { toastError(e, "Could not print cash report"); }
      } }, icon("i-printer"), "Print"),
      h("button", { class: "btn primary", onclick: () => m.close() }, "Done"),
    ],
  });
}


function cashMovementPanel(rep, refresh) {
  const type = h("select", { class: "input" },
    ...[["CASH_IN", "Add float / cash in"], ["CASH_OUT", "Cash removed"],
      ["PETTY_CASH", "Petty cash expense"], ["BANK_DEPOSIT", "Cash deposited to bank"]]
      .map(([v, label]) => h("option", { value: v }, label)));
  const amount = h("input", { class: "input", type: "number", min: "0.01", step: "0.01", placeholder: "0.00" });
  const reason = h("input", { class: "input", maxlength: "250", placeholder: "Reason (required)" });
  const reference = h("input", { class: "input", maxlength: "120", placeholder: "Voucher / bank deposit reference" });
  const recent = (rep.cash_movements || []).slice(-12).reverse();
  return h("div", { class: "card", style: "padding:18px" },
    panelTitle("Cash movements", "Record cash paid in/out without creating fake sales"),
    h("div", { class: "report-note" },
      `Added: ${fmt(rep.drawer_cash_in || 0)} · Removed: ${fmt(rep.drawer_cash_out || 0)}. Included in expected cash.`),
    h("div", { class: "form-grid" },
      h("div", { class: "field" }, h("label", {}, "Movement"), type),
      h("div", { class: "field" }, h("label", {}, "Amount ₹"), amount),
      h("div", { class: "field" }, h("label", {}, "Reason"), reason),
      h("div", { class: "field" }, h("label", {}, "Reference (optional)"), reference)),
    h("button", { class: "btn primary", onclick: async () => {
      try {
        await api.addCashMovement({ kind: type.value, amount: amount.value,
          reason: reason.value.trim(), reference: reference.value.trim() });
        toast("Drawer movement recorded.", "success"); refresh();
      } catch (e) { toastError(e); }
    } }, "Record cash movement"),
    recent.length ? h("div", { class: "table-wrap", style: "margin-top:12px" },
      h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ...["Type", "Amount", "Reason", "Reference"].map(x => h("th", {}, x)))),
        h("tbody", {}, ...recent.map(m => h("tr", {},
          h("td", {}, m.kind.replaceAll("_", " ")),
          h("td", { class: "num" }, fmt(m.amount)),
          h("td", {}, m.reason), h("td", {}, m.reference || "—")))))) : null);
}
