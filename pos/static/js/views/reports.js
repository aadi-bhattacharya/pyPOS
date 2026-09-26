import { api, fmt } from "../api.js";
import { h, icon, openModal, closeModalAll, toast, toastError, printReceipt } from "../ui.js";
import { methodLabel } from "../receipt.js";
import { buildZReceipt } from "../zreport.js";

const RANGES = [
  ["7", "Last 7 days"],
  ["14", "Last 14 days"],
  ["30", "Last 30 days"],
  ["90", "Last 90 days"],
];

let rangeDays = "14";

export async function renderReports(main) {
  main.innerHTML = "";
  main.append(
    h("div", { class: "skel-stats" },
      ...Array.from({ length: 4 }, () => h("div", { class: "skeleton" }))),
    h("div", { class: "skel-bars skeleton" }));

  let data, session, sessions;
  try {
    [data, session, sessions] = await Promise.all([
      api.reportSummary(Number(rangeDays) || 14),
      api.currentSession(),
      api.listSessions(),
    ]);
  } catch (e) {
    main.innerHTML = "";
    return toastError(e);
  }

  main.innerHTML = "";

  const days = data.days;
  const rangeLabel = `last ${days} day${days === 1 ? "" : "s"}`;
  const refresh = () => renderReports(main);

  // ---- range selector ----
  const rangeChips = h("div", { class: "chips", id: "range-chips" },
    ...RANGES.map(([value, label]) =>
      h("button", {
        class: `chip${rangeDays === value ? " active" : ""}`,
        onclick: () => {
          rangeDays = value;
          renderReports(main);
        },
      }, label)));

  const maxRevenue = Math.max(...data.series.map((d) => d.revenue), 0.01);
  const todayISOStr = data.series[data.series.length - 1]?.date;

  // ---- revenue line chart ----
  const W = 1000, H = 220, PAD = 6;
  const n = data.series.length;
  const x = (i) => ((i + 0.5) * W) / n;
  const y = (v) => H - PAD - (v / maxRevenue) * (H - PAD * 2);

  const pts = data.series.map((d, i) => `${x(i).toFixed(1)},${y(d.revenue).toFixed(1)}`);
  const linePath = "M" + pts.join(" L");
  const areaPath =
    `${linePath} L${x(n - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`;
  const peak = data.series.reduce((a, b) => (b.revenue > a.revenue ? b : a),
    data.series[0] || { revenue: 0, date: "" });

  // Sparse date axis — same fitting rule as before.
  const labelEvery = Math.ceil(n / 14);

  const chart = h("div", { class: "linechart" },
    h("div", { class: "linechart-head" },
      h("span", { class: "linechart-peak" },
        peak.revenue > 0
          ? `Peak ${fmt(peak.revenue)} on ${peak.date.slice(5)}`
          : "No revenue in this period")),
    s("svg", {
      class: "line-svg",
      viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: "none",
      "aria-label": `Daily revenue for the ${rangeLabel}`,
    },
      ...[0.25, 0.5, 0.75].map((f) =>
        s("line", {
          x1: 0, x2: W,
          y1: PAD + (H - PAD * 2) * f, y2: PAD + (H - PAD * 2) * f,
          class: "lc-grid",
        })),
      s("line", { x1: 0, x2: W, y1: H - PAD, y2: H - PAD, class: "lc-baseline" }),
      s("path", { d: areaPath, class: "lc-area" }),
      s("path", { d: linePath, class: "lc-line" }),
      ...data.series.map((d, i) =>
        s("rect", {
          x: (i * W) / n, y: 0,
          width: W / n, height: H, class: "lc-hit",
        },
          s("title", {},
            `${d.date} — ${fmt(d.revenue)} (${d.txns} sales)` +
            (d.refunds > 0 ? `, refunds ${fmt(d.refunds)}` : ""))))),
    h("div", { class: "bars lc-axis" },
      ...data.series.map((d, i) =>
        h("div", { class: "bar-col" },
          h("div", {
            class: `bar-date${d.date === todayISOStr ? " lc-today" : ""}`,
          }, i % labelEvery === 0 || i === n - 1 ? d.date.slice(5) : "")))));

  const lowRows = data.low_stock.map((p) =>
    h("tr", {},
      h("td", {},
        h("div", { class: "cell-main" }, p.group_name || "—"),
        h("div", { class: "cell-sub" }, p.SKU)),
      h("td", {}, p.quantity === 0
        ? h("span", { class: "stock-badge out" }, "Out")
        : h("span", { class: "stock-badge low" }, String(p.quantity)))));

  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Reports"),
        h("div", { class: "sub" }, `Business overview — ${rangeLabel}`)),
      h("div", { class: "view-actions" },
        h("a", {
          class: "btn",
          href: `/api/export/sales.csv?from_date=${data.series[0]?.date || ""}&to_date=${data.series[data.series.length - 1]?.date || ""}`,
          title: "Download this range as CSV",
        }, icon("i-sales"), "Export CSV"))),

    rangeChips,

    endOfDayCard(session, sessions, refresh),

    h("div", { class: "stat-grid" },
      statCard("Today's revenue", fmt(data.today_revenue),
        plural(data.today_transactions, "sale")),
      statCard("Today's transactions", String(data.today_transactions),
        `avg basket ${fmt(data.today_avg_basket)}`),
      statCard(`Items sold (${days}d)`, String(data.period_items_sold),
        `${plural(data.today_transactions, "sale")} today`),
      statCard(`Refunds (${days}d)`, fmt(data.period_refund_total),
        plural(data.period_refund_count, "refund"))),

    h("div", { class: "report-grid" },
      h("div", { class: "card chart-box" },
        h("h3", { style: "margin-bottom:6px" }, `Revenue — ${rangeLabel}`),
        chart),

      h("div", { style: "display:flex;flex-direction:column;gap:16px" },
        h("div", { class: "card" },
          h("div", { class: "panel-head" }, h("h3", {}, "Top products")),
          topTable(data.top_products)),
        h("div", { class: "card" },
          h("div", { class: "panel-head" }, h("h3", {}, "Payment mix")),
          mixTable(data.payment_mix)),

        h("div", { class: "card" },
          h("div", { class: "panel-head" },
            h("h3", {}, "Low stock"),
            data.low_stock.length ? h("span", { class: "pill warn" },
              `${data.low_stock.length} to reorder`) : null),
          data.low_stock.length
            ? h("table", { class: "table" }, lowRows)
            : h("p", { style: "padding:14px 16px;color:var(--text-2)" },
                "Everything is comfortably stocked.")))),
  );
}

function statCard(label, value, hint) {
  return h("div", { class: "stat-card" },
    h("div", { class: "s-label" }, label),
    h("div", { class: "s-value" }, value),
    hint ? h("div", { class: "s-hint" }, hint) : null);
}

// SVG-flavoured element builder (h() creates HTML nodes, which don't render
// inside an <svg> tree).
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

function topTable(items) {
  if (!items.length) {
    return h("p", { style: "padding:14px 16px;color:var(--text-2)" },
      "No sales recorded in this period.");
  }
  return h("table", { class: "table" },
    h("thead", {}, h("tr", {},
      h("th", {}, "Product"), h("th", { class: "num" }, "Qty"),
      h("th", { class: "num" }, "Revenue"))),
    h("tbody", {}, items.map((it) =>
      h("tr", {},
        h("td", {},
          h("div", { class: "cell-main" }, it.group_name || it.SKU),
          it.SKU ? h("div", { class: "cell-sub" }, it.SKU) : null),
        h("td", { class: "num" }, String(it.qty)),
        h("td", { class: "num cell-main" }, fmt(it.revenue))))));
}

function mixTable(mix) {
  if (!mix.length) {
    return h("p", { style: "padding:14px 16px;color:var(--text-2)" },
      "No payments recorded in this period.");
  }
  const total = mix.reduce((a, m) => a + m.amount, 0);
  return h("table", { class: "table" },
    h("tbody", {}, mix.map((m) =>
      h("tr", {},
        h("td", {},
          h("div", { class: "cell-main" }, methodLabel(m.method)),
          h("div", { class: "cell-sub" }, `${m.count} payment(s)`)),
        h("td", { class: "num cell-sub" },
          total > 0 ? `${Math.round((m.amount / total) * 100)}%` : ""),
        h("td", { class: "num cell-main" }, fmt(m.amount))))));
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

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
          h("button", {
            class: "btn sm primary",
            onclick: () => closeDayModal(session, refresh),
          }, icon("i-check"), "End day (Z report)")))
    : h("div", { class: "eod-body" },
        h("div", { class: "eod-hint" },
          icon("i-clock"),
          h("span", {},
            h("strong", {}, "Start of day:"),
            " open with your drawer float. ",
            h("strong", {}, "End of day:"),
            " count the cash and file the printable Z report.")),
        h("button", {
          class: "btn sm primary",
          onclick: () => openDayModal(refresh),
        }, icon("i-play"), "Open day"));

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
      h("button", { class: "btn", onclick: () => printReceipt(el) },
        icon("i-printer"), "Print"),
      h("button", { class: "btn primary", onclick: () => m.close() }, "Done"),
    ],
  });
}
