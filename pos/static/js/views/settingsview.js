import { api, state, refreshSettings, fmt } from "../api.js";
import { h, icon, toast, toastError, confirmDialog, printReceipt } from "../ui.js";
import { fmtDate } from "../api.js";

const FIELDS = [
  ["store_name", "Store name", "text", "My Store"],
  ["store_phone", "Phone", "text", ""],
  ["currency", "Currency symbol", "text", "₹"],
  ["tax_label", "Tax label on receipts", "text", "Tax"],
  ["low_stock_threshold", "Low-stock alert threshold (units)", "number", "5"],
];

export async function renderSettingsView(main) {
  const authState = await api.settingsAuth();
  if (authState.configured && !authState.authenticated) {
    // Locked: bounce to the login page, come back here afterwards.
    location.href = "/login?next=" + encodeURIComponent("#/settings");
    return;
  }
  await refreshSettings();
  const s = state.settings;

  main.innerHTML = "";

  const inputs = {};
  const form = h("div", {});

  for (const [key, label, type, placeholder] of FIELDS) {
    inputs[key] = h("input", {
      class: "input", type,
      value: s[key] ?? "",
      placeholder,
    });
    form.append(h("div", { class: "field" }, h("label", {}, label), inputs[key]));
  }

  const addressIn = h("textarea", { class: "input", rows: "2" });
  addressIn.value = s.store_address ?? "";
  form.append(h("div", { class: "field" },
    h("label", {}, "Address (printed on receipts)"), addressIn));

  const businessFields = [
    ["store_gstin", "Business GSTIN (if registered)", "15-character GSTIN"],
    ["store_state", "Supplier State / UT", "e.g. West Bengal"],
    ["store_state_code", "GST state code", "e.g. 19"],
    ["store_fssai", "FSSAI licence (food businesses, optional)", "14-digit licence"],
    ["store_upi_id", "UPI ID to print on receipts (optional)", "shopname@bank"],
  ];
  const businessInputs = {};
  for (const [key, label, placeholder] of businessFields) {
    const el = h("input", { class: "input", value: s[key] || "", placeholder });
    businessInputs[key] = el;
    form.append(h("div", { class: "field" }, h("label", {}, label), el));
  }

  const footerIn = h("textarea", { class: "input", rows: "2" });
  footerIn.value = s.receipt_footer ?? "";
  form.append(h("div", { class: "field" },
    h("label", {}, "Receipt footer message"), footerIn));

  // Printer branding is stored in the SQLite database and included in backups.
  const priceTaxSelect = h("select", { class: "input" },
    h("option", { value: "exclusive", selected: s.price_tax_mode !== "inclusive" },
      "Prices before GST (add tax at checkout)"),
    h("option", { value: "inclusive", selected: s.price_tax_mode === "inclusive" },
      "Prices include GST (e.g. GST-inclusive MRP)"));
  const paperSelect = h("select", { class: "input" },
    ...[["58mm", "58mm thermal receipt"], ["80mm", "80mm thermal receipt"],
         ["A4", "A4 paper / PDF printing"]].map(([val, label]) =>
      h("option", { value: val, selected: s.receipt_paper === val }, label)));
  const titleSelect = h("select", { class: "input" },
    ...["Retail receipt", "Tax invoice", "Bill of supply"].map((x) =>
      h("option", { value: x, selected: s.receipt_heading === x }, x)));
  const localTaxSelect = h("select", { class: "input" },
    ...["SGST", "UTGST"].map((x) =>
      h("option", { value: x, selected: s.store_local_tax === x }, x)));
  const showLogo = h("input", { type: "checkbox", checked: s.show_receipt_logo === "true" });
  const logoWidth = h("input", { class: "input num", type: "number", min: "12",
    max: "65", value: s.receipt_logo_width || "32", step: "1" });
  const logoInput = h("input", { class: "input", type: "file", accept: "image/png,image/jpeg,image/webp" });
  const logoPreview = h("div", { style: "margin:10px 0;min-height:20px" },
    s.store_logo ? h("img", { src: s.store_logo, alt: "Saved receipt logo",
      style: "max-width:240px;max-height:100px;object-fit:contain" }) :
      h("span", { class: "form-hint" }, "No logo saved"));
  logoInput.addEventListener("change", () => {
    const file = logoInput.files?.[0];
    if (!file) return;
    if (file.size > 200 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      logoInput.value = "";
      return toast("Use a PNG, JPEG or WebP logo up to 200 KiB.", "warn");
    }
    const previewUrl = URL.createObjectURL(file);
    logoPreview.replaceChildren(h("img", {
      src: previewUrl, alt: "Selected logo preview",
      style: "max-width:240px;max-height:100px;object-fit:contain",
      onload: () => URL.revokeObjectURL(previewUrl),
    }));
  });
  const brandingCard = h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Receipt printing & logo"),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "A receipt heading alone does not establish GST compliance. Confirm the required invoice fields, " +
      "e-invoicing applicability and treatment of each supply with your accountant."),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "Choose paper width for browser printing, export to PDF via the print dialog, and optionally print your shop logo. " +
      "Select matching paper size in the printer's own dialog as well."),
    h("div", { class: "field" }, h("label", {}, "How are product prices entered?"), priceTaxSelect,
      h("p", { class: "form-hint" }, "This setting changes how ALL catalog prices are interpreted for NEW sales. " +
        "Do not switch it without reviewing your SKU prices. Past invoices and refunds keep their original mode.")),
    h("div", { class: "field" }, h("label", {}, "Paper / print layout"), paperSelect),
    h("div", { class: "field" }, h("label", {}, "Receipt heading"), titleSelect),
    h("div", { class: "field" }, h("label", {}, "Local tax component"), localTaxSelect),
    h("div", { class: "field" }, h("label", {}, "Upload shop logo (PNG/JPEG/WebP, maximum 200 KiB)"), logoInput),
    logoPreview,
    h("label", { class: "credit-toggle" }, showLogo, h("span", {}, "Print logo on receipts")),
    h("div", { class: "field" }, h("label", {}, "Logo width in mm (12–65)"), logoWidth),
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
      h("button", { class: "btn primary", onclick: async () => {
        try {
          const values = {
            receipt_paper: paperSelect.value,
            price_tax_mode: priceTaxSelect.value,
            receipt_heading: titleSelect.value,
            receipt_logo_width: logoWidth.value,
            show_receipt_logo: showLogo.checked ? "true" : "false",
            store_local_tax: localTaxSelect.value,
          };
          if (logoInput.files?.length) {
            values.store_logo = await new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(reader.result);
              reader.onerror = () => reject(new Error("Could not read the selected logo"));
              reader.readAsDataURL(logoInput.files[0]);
            });
          }
          state.settings = await api.saveSettings(values);
          toast("Receipt printing preferences saved.", "success");
          renderSettingsView(main);
        } catch (e) { toastError(e); }
      } }, icon("i-check"), "Save print settings"),
      h("button", { class: "btn", onclick: () => {
        // Explicitly non-transactional calibration slip, not a fabricated sale.
        const settingsNow = { ...state.settings, receipt_paper: paperSelect.value,
          show_receipt_logo: showLogo.checked ? "true" : "false",
          receipt_logo_width: logoWidth.value };
        const calibration = h("div", { class: "receipt" },
          settingsNow.store_logo && settingsNow.show_receipt_logo === "true" ?
            h("div", { class: "r-center" }, h("img", {
              src: settingsNow.store_logo, class: "receipt-logo",
              style: `width:${Math.min(65, Math.max(12, Number(settingsNow.receipt_logo_width) || 32))}mm`,
              alt: "Store logo" })) : null,
          h("div", { class: "r-center r-store" }, settingsNow.store_name),
          h("div", { class: "r-center" }, settingsNow.store_address || ""),
          h("hr"), h("div", { class: "r-center r-bold" }, "TEST PRINT - NOT A SALE"),
          h("div", { class: "r-center" }, "Adjust paper size, margins and logo alignment on your printer."),
          h("hr"));
        // Use chosen paper without forcing the user to save first.
        const saved = state.settings.receipt_paper;
        state.settings.receipt_paper = paperSelect.value;
        printReceipt(calibration);
        state.settings.receipt_paper = saved;
      } }, icon("i-printer"), "Print test slip"),
      h("button", { class: "btn subtle-danger", onclick: async () => {
        if (!state.settings.store_logo) return toast("No saved logo to remove.", "warn");
        if (!(await confirmDialog({title:"Remove store logo?", message:"The stored receipt logo will be removed.",
          confirmLabel:"Remove logo", danger:true}))) return;
        try {
          state.settings = await api.saveSettings({ store_logo: "" });
          toast("Logo removed.", "success"); renderSettingsView(main);
        } catch (e) { toastError(e); }
      } }, "Remove logo")));

  let productsCount = null;
  try {
    productsCount = (await api.listProducts({ limit: 1 })).length;
  } catch (_) { /* ignore */ }

  // ---- tax breakup (GST-style CGST/SGST lines on receipts) ----
  const breakupOn = h("input", { type: "checkbox", checked: s.tax_breakup === "true" });
  const compRows = [];
  for (const i of [1, 2]) {
    const enabled = h("input", {
      type: "checkbox", checked: s[`tax_comp${i}_enabled`] === "true",
      "aria-label": `Component ${i} enabled`,
    });
    const name = h("input", {
      class: "input", type: "text", value: s[`tax_comp${i}_name`] ?? "",
      placeholder: i === 1 ? "CGST" : "SGST",
      "aria-label": `Component ${i} name`,
    });
    const share = h("input", {
      class: "input num", type: "number", min: "0", max: "100", step: "0.01",
      value: s[`tax_comp${i}_share`] ?? "50",
      title: "Share of each product's tax rate that this component covers",
      "aria-label": `Component ${i} share`,
    });
    compRows.push(h("div", { class: "tax-comp-row" },
      h("label", { class: "tax-comp-on", title: "Include this component" }, enabled),
      name,
      share,
      h("span", { class: "cell-sub", style: "white-space:nowrap" }, "% of rate")));
  }
  const compHead = h("div", { class: "tax-comp-row tax-comp-head" },
    h("span", {}, "On"),
    h("span", {}, "Component name"),
    h("span", {}, "Share"),
    h("span", {}));

  const taxCard = h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Legacy / unregistered tax display"),
    h("label", { class: "credit-toggle", style: "margin-bottom:10px" },
      breakupOn,
      h("span", {}, "Show tax breakup lines on bills (e.g. GST as CGST + SGST)")),
    h("p", { style: "color:var(--text-2);font-size:12.5px;margin-bottom:12px" },
      "Each product's tax rate is split between the enabled components. " +
      "Example: a 5% product with two 50% components prints CGST 2.5% and SGST 2.5%. " +
      "Disable the second component for single-line taxes such as IGST. " +
      "For a store with GSTIN, new sale receipts instead use the selected intra/inter-state type, " +
      "and save the breakup at checkout."),
    compHead,
    ...compRows,
    h("button", {
      class: "btn primary", style: "margin-top:14px",
      onclick: async () => {
        const values = { tax_breakup: breakupOn.checked ? "true" : "false" };
        for (const [i, row] of [1, 2].map((i) => [i, compRows[i - 1]])) {
          const [enabled, name, share] = [
            row.querySelectorAll("input")[0],
            row.querySelector("input[type='text']"),
            row.querySelector("input[type='number']"),
          ];
          values[`tax_comp${i}_enabled`] = enabled.checked ? "true" : "false";
          values[`tax_comp${i}_name`] = name.value.trim();
          values[`tax_comp${i}_share`] = String(
            Math.min(100, Math.max(0, parseFloat(share.value) || 0)));
        }
        try {
          state.settings = await api.saveSettings(values);
          toast("Tax breakup settings saved.", "success");
        } catch (e) {
          toastError(e);
        }
      },
    }, icon("i-check"), "Save tax settings"));

  // ---- admin password (locks the Settings area of the web UI) ----
  const pwConfigured = authState.configured;
  const currentPw = h("input", { class: "input", type: "password",
    autocomplete: "current-password", placeholder: pwConfigured ? "Current password" : "" });
  const newPw = h("input", { class: "input", type: "password",
    autocomplete: "new-password", placeholder: "New password (min 4 chars)" });
  const newPw2 = h("input", { class: "input", type: "password",
    autocomplete: "new-password", placeholder: "Repeat new password" });

  const passwordCard = h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Admin password"),
    h("p", { style: "color:var(--text-2);font-size:12.5px;margin-bottom:12px" },
      pwConfigured
        ? "The Settings area of the web UI is locked behind this password. "
          + "The CLI never needs it."
        : "No password is set, so anyone using this browser can open Settings. "
          + "Set one to lock the area (sales/register stay open for cashiers)."),
    h("div", { class: "field" },
      h("label", {}, pwConfigured ? "Current password" : "Current password (none set)"),
      currentPw),
    h("div", { class: "field" }, h("label", {}, "New password"), newPw),
    h("div", { class: "field" }, h("label", {}, "Repeat new password"), newPw2),
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
      h("button", {
        class: "btn primary",
        onclick: async () => {
          if (newPw.value !== newPw2.value)
            return toast("New passwords don't match.", "warn");
          try {
            await api.setPassword(currentPw.value, newPw.value);
            currentPw.value = newPw.value = newPw2.value = "";
            toast("Admin password saved — Settings are now protected.", "success");
          } catch (e) {
            toastError(e);
          }
        },
      }, icon("i-check"), pwConfigured ? "Change password" : "Set password"),
      pwConfigured ? h("button", {
        class: "btn",
        onclick: async () => {
          const ok = await confirmDialog({
            title: "Remove the admin password?",
            message: "The Settings area of the web UI will be open to " +
              "anyone using this browser until you set a new one.",
            confirmLabel: "Remove password", danger: true,
          });
          if (!ok) return;
          try {
            await api.setPassword(currentPw.value, "");
            toast("Password removed — Settings are open again.", "success");
            renderSettingsView(main);
          } catch (e) {
            toastError(e);
          }
        },
      }, "Remove password") : null,
      pwConfigured ? h("button", {
        class: "btn",
        onclick: async () => { location.href = "/logout"; },
      }, "Log out") : null));

  const loyaltyEnabled = h("input", { type: "checkbox", checked: s.loyalty_enabled === "true" });
  const loyaltySpend = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: s.loyalty_earn_spend_rupees || "100" });
  const loyaltyEarn = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: s.loyalty_earn_points || "1" });
  const loyaltyValue = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: s.loyalty_redeem_paise || "100" });
  const loyaltyCard = h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Customer loyalty programme"),
    h("p", { style: "font-size:12.5px;color:var(--text-2);margin-bottom:12px" },
      "Points are earned on customer-linked sales, excluding store-credit payments. " +
      "Refunds and voids reverse earned points. Admins can correct balances or convert points " +
      "to store credit under Customers. Changes apply to future transactions only."),
    h("label", { class: "credit-toggle" }, loyaltyEnabled, h("span", {}, "Enable loyalty points")),
    h("div", { class: "form-grid" },
      h("div", { class: "field" }, h("label", {}, "Eligible spend per reward (₹)"), loyaltySpend),
      h("div", { class: "field" }, h("label", {}, "Points earned per spend block"), loyaltyEarn),
      h("div", { class: "field" }, h("label", {}, "Store credit value per point (paise)"), loyaltyValue)),
    h("p", { class: "form-hint" },
      "Example: ₹100 per block and 1 point awards 1 point per complete ₹100 spent. " +
      "100 paise per point makes 10 points worth ₹10 of store credit. " +
      "Redemptions settle bills after GST calculation; consult your accountant about scheme treatment."),
    h("button", { class: "btn primary", onclick: async () => {
      const values = {
        loyalty_enabled: loyaltyEnabled.checked ? "true" : "false",
        loyalty_earn_spend_rupees: loyaltySpend.value.trim(),
        loyalty_earn_points: loyaltyEarn.value.trim(),
        loyalty_redeem_paise: loyaltyValue.value.trim(),
      };
      try {
        state.settings = await api.saveSettings(values);
        toast("Loyalty settings saved.", "success");
      } catch (e) { toastError(e); }
    } }, icon("i-check"), "Save loyalty settings"));

  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Settings"),
        h("div", { class: "sub" }, "Store details and receipt preferences"))),

    h("div", { style: "max-width:680px" },
      h("div", { class: "card card-pad" },
        h("h3", { style: "margin-bottom:14px" }, "Store"),
        form,
        h("button", {
          class: "btn primary",
          onclick: async () => {
            const values = {
              store_name: inputs.store_name.value.trim() || "My Store",
              store_address: addressIn.value.trim(),
              store_phone: inputs.store_phone.value.trim(),
              currency: inputs.currency.value.trim() || "₹",
              ...Object.fromEntries(Object.entries(businessInputs).map(([k, el]) =>
                [k, el.value.trim()])) ,
              tax_label: inputs.tax_label.value.trim() || "Tax",
              low_stock_threshold: String(parseInt(inputs.low_stock_threshold.value, 10) || 5),
              receipt_footer: footerIn.value.trim(),
            };
            try {
              state.settings = await api.saveSettings(values);
              toast("Settings saved.", "success");
            } catch (e) {
              toastError(e);
            }
          },
        }, icon("i-check"), "Save settings")),

      brandingCard,
      taxCard,
      loyaltyCard,

      h("div", { class: "card card-pad", style: "margin-top:16px" },
        h("h3", { style: "margin-bottom:8px" }, "Sample data"),
        h("p", { style: "color:var(--text-2);margin-bottom:12px" },
          productsCount > 0
            ? "Your catalog already has products, so demo data can't be loaded."
            : "Load a realistic starter catalog plus two weeks of sales history to explore the app."),
        h("button", {
          class: "btn",
          disabled: productsCount > 0,
          onclick: async () => {
            const ok = await confirmDialog({
              title: "Load sample data?",
              message:
                "Adds sample groups, products and past sales. Only possible while the catalog is empty.",
              confirmLabel: "Load samples",
            });
            if (!ok) return;
            try {
              const r = await api.seedDemo();
              toast(`Added ${r.products} products and ${r.sales_created} sales.`,
                "success");
            } catch (e) {
              toastError(e);
            }
          },
        }, icon("i-catalog"), "Load sample data")),

      passwordCard,
      backupsCard()),
  );
}

function backupsCard() {
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
        await fetch("/api/backups/upload-restore", { method: "POST", body: fd })
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
  const intervalIn = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: String(status.interval_hours || 24), style: "width:90px" });
  const keepIn = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: String(status.keep ?? 14), style: "width:90px" });
  const maxMbIn = h("input", { class: "input", type: "number", min: "1", step: "1",
    value: String(status.storage?.max_mb || 800), style: "width:90px",
    title: "Hard ceiling: oldest snapshots are deleted beyond this total size" });

  const scheduleSaveBtn = h("button", {
    class: "btn",
    onclick: async () => {
      try {
        await api.saveSettings({
          auto_backup_enabled: enabledIn.checked ? "true" : "false",
          auto_backup_interval_hours: String(parseInt(intervalIn.value, 10) || 24),
          backup_keep: String(parseInt(keepIn.value, 10) || 14),
          backup_max_mb: String(parseInt(maxMbIn.value, 10) || 800),
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
          h("label", {}, "Interval (hours)"), intervalIn),
        h("div", { class: "field", style: "margin:0" },
          h("label", {}, "Keep last N"), keepIn),
        h("div", { class: "field", style: "margin:0" },
          h("label", {}, "Size cap (MB)"), maxMbIn),
        scheduleSaveBtn)),
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
            await fetch("/api/backups/gdrive/disconnect", { method: "POST" });
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
          headers: { "Content-Type": "application/json" },
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
