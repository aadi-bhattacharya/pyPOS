/** Backup snapshots, restore controls, and optional Google Drive sync UI. */
import { api, fmtDate, csrfHeaders } from "../api.js";
import { h, icon, toast, toastError, confirmDialog } from "../ui.js";

export function backupsCard() {
  const wrap = h("div", { class: "card card-pad", style: "margin-top:16px", id: "backups-card" });
  renderBackups(wrap);
  return wrap;
}

async function renderBackups(wrap) {
  let status, list;
  try {
    [status, list] = await Promise.all([api.backupStatus(), api.listBackups()]);
  } catch (e) {
    toastError(e);
    return;
  }

  wrap.replaceChildren(
    h("h3", { style: "margin-bottom:4px" }, "Backups"),
    h("p", { style: "color:var(--text-2);margin-bottom:12px" },
      "Snapshots of your whole database. Restoring replaces all current data."),
  );

  // ---- status line ----
  const statusBits = [
    status.last_auto ? `Last backup: ${fmtDate(status.last_auto)}` : "No backups yet",
  ];
  if (status.enabled && status.next_auto) {
    statusBits.push(`Next auto: ${fmtDate(status.next_auto)}`);
  } else if (!status.enabled) {
    statusBits.push("Auto-backup off");
  }
  if (status.storage) {
    const cap = status.storage.max_mb > 0
      ? ` · cap ${status.storage.max_mb} MB`
      : "";
    statusBits.push(`Storage: ${status.storage.used_human} used${cap} · ${status.storage.free_human} free`);
  }
  if (status.replication?.enabled) {
    statusBits.push(`Syncthing outbox: ${status.replication.staged_count || 0} local snapshots; remote delivery not verified`);
  }
  if (status.gdrive?.connected) {
    statusBits.push(`Google Drive ✓ ${status.gdrive.client_id}`);
  }
  wrap.append(h("div", {
    class: "pill muted", style: "align-self:flex-start;margin-bottom:12px;display:inline-flex;flex-direction:column;gap:2px;padding:6px 10px;border-radius:6px",
  }, ...statusBits.map((b) => h("span", {}, b))));

  // ---- actions row ----
  const backupNowBtn = h("button", {
    class: "btn primary",
    onclick: async (e) => {
      e.target.disabled = true;
      try {
        const r = await api.createBackup();
        toast(r.name + (r.pushed ? " (pushed to remote)" : ""), "success",
          { title: "Backup created" });
        await renderBackups(wrap);
      } catch (err) {
        toastError(err);
      } finally {
        e.target.disabled = false;
      }
    },
  }, icon("i-check"), "Back up now");

  const uploadLabel = h("label", { class: "btn" }, icon("i-catalog"), "Restore from file…");
  const fileInput = h("input", {
    type: "file", accept: ".db,.sqlite,.sqlite3", style: "display:none",
    onchange: async (ev) => {
      const f = ev.target.files[0];
      ev.target.value = "";
      if (!f) return;
      const ok = await confirmDialog({
        title: "Restore from file?",
        message:
          `${f.name} will REPLACE all current data (catalog, sales, settings). ` +
          "Take a fresh backup first if unsure.",
        confirmLabel: "Replace everything", danger: true,
      });
      if (!ok) return;
      const fd = new FormData();
      fd.append("file", f);
      try {
        await fetch("/api/backups/upload-restore", { method: "POST", headers: csrfHeaders(), body: fd })
          .then(async (res) => {
            if (!res.ok) throw new Error((await res.json()).error || "Restore failed");
          });
        toast("Data replaced from uploaded file.", "success");
        setTimeout(() => location.reload(), 800);
      } catch (err) {
        toastError(err);
      }
    },
  });
  uploadLabel.append(fileInput);

  wrap.append(h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" },
    backupNowBtn, uploadLabel));

  // ---- auto-backup + Google Drive configuration ----
  const enabledIn = h("input", { type: "checkbox" });
  enabledIn.checked = status.enabled;
  const intervalIn = h("input", { class: "input", type: "number", min: "1", max: "1440", step: "1",
    value: String(status.interval_minutes || 10), style: "width:90px" });
  const daysIn = h("input", { class: "input", type: "number", min: "1", max: "365", step: "1",
    value: String(status.retention_days || 30), style: "width:90px" });
  const maxMbIn = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: String(status.storage?.max_mb || 4096), style: "width:90px",
    title: "Hard storage ceiling; snapshots may be removed before the configured age" });
  const dayEndIn = h("input", { type: "checkbox" });
  dayEndIn.checked = status.day_end_enabled;
  const migrationIn = h("input", { type: "checkbox" });
  migrationIn.checked = status.pre_migration_enabled;
  const replicationIn = h("input", { type: "checkbox" });
  replicationIn.checked = status.replication?.enabled;

  const scheduleSaveBtn = h("button", {
    class: "btn",
    onclick: async () => {
      try {
        await api.saveSettings({
          auto_backup_enabled: enabledIn.checked ? "true" : "false",
          auto_backup_interval_minutes: intervalIn.value,
          backup_retention_days: daysIn.value,
          backup_max_mb: maxMbIn.value,
          backup_day_end_enabled: dayEndIn.checked ? "true" : "false",
          backup_pre_migration_enabled: migrationIn.checked ? "true" : "false",
          backup_replication_enabled: replicationIn.checked ? "true" : "false",
        });
        toast("Backup schedule saved.", "success");
        await renderBackups(wrap);
      } catch (err) { toastError(err); }
    },
  }, icon("i-check"), "Save schedule");

  wrap.append(
    h("div", { style: "display:flex;flex-direction:column;gap:8px;margin-bottom:16px" },
      h("label", { style: "display:flex;align-items:center;gap:8px;font-weight:550" },
        enabledIn, "Automatic backups"),
      h("div", { style: "display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end" },
        h("div", { class: "field", style: "margin:0" },
          h("label", {}, "Interval (minutes)"), intervalIn),
        h("div", { class: "field", style: "margin:0" },
          h("label", {}, "History (days)"), daysIn),
        h("div", { class: "field", style: "margin:0" },
          h("label", {}, "Size cap (MB)"), maxMbIn),
        scheduleSaveBtn)),
      h("label", { style: "display:flex;gap:8px;align-items:center" },
        dayEndIn, "Take an extra snapshot when End Day succeeds"),
      h("label", { style: "display:flex;gap:8px;align-items:center" },
        migrationIn, "Snapshot the existing database before schema migration"),
      h("label", { style: "display:flex;gap:8px;align-items:center" },
        replicationIn, "Stage the latest 3 verified snapshots for Syncthing"),
      h("div", { class: "form-hint", style: "overflow-wrap:anywhere" },
        `Syncthing Send Only folder: ${status.replication?.outbox || "Not configured"}. ` +
        "Configure this folder in Syncthing and set the other device to Receive Only. " +
        "It is not the live database. Syncthing connectivity cannot be verified here."),
      h("div", { class: "form-hint" },
        "Recent backups are kept every 10 minutes, then thinned to hourly and daily " +
        "history through 30 days. Space/count caps can shorten retention."),
    );

  wrap.append(gdriveSection(status, () => renderBackups(wrap)));

  // ---- existing backups table ----
  if (!list.length) {
    wrap.append(h("p", { class: "form-hint" },
      "No backups yet — create one now so you always have a fallback."));
    return;
  }

  wrap.append(h("div", { class: "table-wrap", style: "max-height:280px" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Snapshot"), h("th", { class: "num" }, "Size"),
        h("th", {}, "Remote"), h("th", {}))),
      h("tbody", {}, list.map((b) =>
        h("tr", {},
          h("td", {},
            h("div", { class: "cell-main num" }, b.name.replace(/^pypos-|\.db$/g, "")),
            h("div", { class: "cell-sub" }, fmtDate(b.created_at))),
          h("td", { class: "num cell-sub" }, b.size_human),
          h("td", {}, b.pushed_remote
            ? h("span", { class: "pill ok" }, "synced")
            : h("span", { class: "pill muted" }, "local")),
          h("td", { class: "actions-cell" },
            h("a", { href: `/api/backups/${encodeURIComponent(b.name)}/download`,
                     class: "btn ghost sm", title: "Download" }, icon("i-sales")),
            h("button", {
              class: "btn ghost sm", title: "Restore this snapshot",
              onclick: async () => {
                const ok = await confirmDialog({
                  title: "Restore this backup?",
                  message:
                    `${b.name}\n\nAll current data will be REPLACED by this snapshot.` +
                    " The app reloads after restoring.",
                  confirmLabel: "Restore", danger: true,
                });
                if (!ok) return;
                try {
                  await api.restoreBackup(b.name);
                  toast("Backup restored.", "success");
                  setTimeout(() => location.reload(), 800);
                } catch (err) { toastError(err); }
              },
            }, icon("i-undo")),
            h("button", {
              class: "btn ghost sm", title: "Delete snapshot",
              onclick: async () => {
                const ok = await confirmDialog({
                  title: "Delete snapshot?",
                  message: b.name, confirmLabel: "Delete", danger: true,
                });
                if (!ok) return;
                try {
                  await api.deleteBackup(b.name);
                  await renderBackups(wrap);
                } catch (err) { toastError(err); }
              },
            }, icon("i-trash")))))))));
}

function gdriveSection(status, refresh) {
  const box = h("div", { style: "border:1px solid var(--border);border-radius:6px;padding:12px 14px;margin-bottom:16px" });
  const connected = status.gdrive?.connected;

  box.append(h("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:4px" },
    h("h3", { style: "font-size:13px" }, "Google Drive sync"),
    connected
      ? h("span", { class: "pill ok" }, "Connected")
      : h("span", { class: "pill muted" }, "Not connected")));

  if (status.gdrive?.last_error) {
    box.append(h("p", { style: "color:var(--warning);font-size:12px;margin-bottom:8px" },
      `Last sync error: ${status.gdrive.last_error}`));
  }

  if (connected) {
    box.append(h("div", { style: "display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap" },
      h("p", { style: "color:var(--text-2);font-size:12.5px" },
        "Every backup is uploaded to your Google Drive automatically (folder “pyPOS-backups” files). Old remote copies are pruned with the same retention."),
      h("button", {
        class: "btn subtle-danger",
        onclick: async () => {
          const ok = await confirmDialog({
            title: "Disconnect Google Drive?",
            message:
              "Local backups keep working; new ones just won't be uploaded until you reconnect.",
            confirmLabel: "Disconnect", danger: true,
          });
          if (!ok) return;
          try {
            await fetch("/api/backups/gdrive/disconnect", { method: "POST", headers: csrfHeaders() });
            toast("Google Drive disconnected.", "success");
            refresh();
          } catch (e) { toastError(e); }
        },
      }, icon("i-ban"), "Disconnect")));
    return box;
  }

  // ---- not connected: setup form ----
  const clientIdIn = h("input", { class: "input", type: "text",
    placeholder: "1234567890-abc.apps.googleusercontent.com" });
  const secretIn = h("input", { class: "input", type: "password",
    placeholder: "GOCSPX-…" });

  const redirectUri = `${location.origin}/gdrive/callback`;
  const connectBtn = h("button", {
    class: "btn primary",
    onclick: async () => {
      if (!clientIdIn.value.trim() || !secretIn.value.trim()) {
        return toast("Paste both the Client ID and the Client Secret first.", "warn");
      }
      try {
        const res = await fetch("/api/backups/gdrive/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...csrfHeaders() },
          body: JSON.stringify({ client_id: clientIdIn.value.trim(),
                                 client_secret: secretIn.value.trim() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Could not start Google sign-in");
        toast("Complete the sign-in in the new tab…", "info");
        window.open(data.auth_url, "_blank");
        // Poll until the callback lands.
        let tries = 0;
        const poll = setInterval(async () => {
          tries += 1;
          try {
            const st = await api.backupStatus();
            if (st.gdrive.connected || tries > 60) {
              clearInterval(poll);
              if (st.gdrive.connected) toast("Google Drive connected!", "success");
              refresh();
            }
          } catch (_) { /* transient */ }
        }, 2000);
      } catch (err) { toastError(err); }
    },
  }, icon("i-check"), "Connect Google Drive");

  box.append(
    h("details", { style: "margin-bottom:10px" },
      h("summary", { style: "cursor:pointer;font-size:12.5px;font-weight:550;color:var(--text-2)" },
        "One-time setup — get a Client ID & Secret from Google (2 minutes)"),
      h("ol", { style: "margin:8px 0 0;padding-left:18px;color:var(--text-2);font-size:12.5px;display:flex;flex-direction:column;gap:5px" },
        h("li", {}, "Go to console.cloud.google.com → APIs & Services → enable ",
          h("b", {}, "Google Drive API")),
        h("li", {}, "OAuth consent screen → External → add yourself as test user"),
        h("li", {}, "Credentials → Create credentials → OAuth client ID → Web application"),
        h("li", {},
          "Add this exact ", h("b", {}, "Authorized redirect URI"), ":",
          h("code", { style: "display:block;background:var(--surface-2);padding:3px 7px;border-radius:4px;margin-top:3px;user-select:all" },
            redirectUri)),
        h("li", {}, "Paste the Client ID and Client Secret below"))),
    h("div", { class: "form-row" },
      h("div", { class: "field", style: "flex:2" },
        h("label", {}, "Client ID"), clientIdIn),
      h("div", { class: "field", style: "flex:1" },
        h("label", {}, "Client Secret"), secretIn),
      h("div", { style: "display:flex;align-items:flex-end;padding-bottom:12px" }, connectBtn)),
  );
  return box;
}
