import { api, state, refreshSettings, fmt } from "../api.js";
import { h, icon, toast, toastError, confirmDialog, printReceipt } from "../ui.js";
import { DEFAULT_ACCENT, applyBranding, brandingPalette, normalizeAccent } from "../branding.js";
import { optimizeImage, asDataUrl, readableBytes } from "../imageOptimizer.js";
import { imageQualitySelect, imageSizeSelect, imageCompressionSettings, imageOptimizerCard } from "./settingsImageTools.js";
import { returnPolicyCard } from "./settingsReturnRules.js";
import { backupsCard } from "./settingsBackups.js";

let selectedSettingsSection = "store";

const FIELDS = [
  ["store_name", "Store name", "text", "My Store"],
  ["store_phone", "Phone", "text", ""],
  ["currency", "Currency symbol", "text", "₹"],
  ["tax_label", "Tax label on receipts", "text", "Tax"],
  ["low_stock_threshold", "Low-stock alert threshold (units)", "number", "5"],
];

const BRAND_PRESETS = [
  ["Classic blue", "#1B6BB5"], ["Indigo", "#5546C9"],
  ["Emerald", "#176B52"], ["Rose", "#A52A4A"],
  ["Amber", "#975A16"], ["Slate", "#455A64"],
];

function brandingSettingsCard(main, saved) {
  const displayName = h("input", { class: "input", type: "text", maxlength: "48",
    value: saved.pos_display_name || "pyPOS", placeholder: "Your store name" });
  const picker = h("input", { type: "color", value: normalizeAccent(saved.pos_accent_color),
    "aria-label": "Choose accent colour" });
  const hex = h("input", { class: "input mono", type: "text", maxlength: "7",
    value: normalizeAccent(saved.pos_accent_color), placeholder: "#1B6BB5",
    "aria-label": "Accent colour hex value" });
  const mode = h("select", { class: "input", "aria-label": "Logo displayed in POS menu" },
    ...[["default", "Simple coloured initial"], ["receipt", "Use existing receipt logo"],
      ["custom", "Upload separate POS logo"], ["none", "No menu logo"]].map(([value, name]) =>
      h("option", { value, selected: (saved.pos_logo_mode || "default") === value }, name)));
  const logoSize = h("input", { type: "number", class: "input num", min: "24", max: "48", step: "1",
    value: saved.pos_logo_size || "28", "aria-label": "Menu logo width in pixels" });
  const logoFile = h("input", { class: "input", type: "file",
    accept: "image/png,image/jpeg,image/webp", "aria-label": "Upload separate POS logo" });
  const logoHint = h("p", { class: "form-hint" },
    "The separate logo is for the menu and browser tab. Your receipt logo and tax invoice details are unchanged.");
  const previewMark = h("div", { class: "branding-preview-mark" }, "P");
  const previewName = h("div", { class: "branding-preview-title" }, "pyPOS");
  const previewAction = h("div", { class: "branding-preview-cta" }, "Register");
  const preview = h("div", { class: "branding-settings-preview" },
    previewMark, h("div", { style: "min-width:0;flex:1" },
      previewName, h("div", { class: "form-hint" }, "Store menu preview")), previewAction);
  let pendingLogoTask = null;
  let temporaryUrl = null;
  const menuMaxEdge = imageSizeSelect("384");
  const menuQuality = imageQualitySelect();
  const logoCompressionInfo = h("p", { class: "form-hint", "aria-live": "polite" },
    "Large images are automatically compressed to fit the 200 KiB logo limit.");

  function updatePreview() {
    const name = displayName.value.trim() || "pyPOS";
    const palette = brandingPalette(hex.value);
    preview.style.setProperty("--preview-accent", palette["--brand-color"]);
    preview.style.setProperty("--preview-text", palette["--cta-text"]);
    previewName.textContent = name;
    const size = Math.max(24, Math.min(48, Number(logoSize.value) || 28));
    previewMark.style.width = previewMark.style.height = `${size}px`;
    previewMark.classList.remove("has-image");
    previewMark.replaceChildren();
    previewMark.hidden = mode.value === "none";
    const chosen = mode.value === "custom" ? (temporaryUrl || saved.pos_logo) :
      mode.value === "receipt" ? saved.store_logo : "";
    if (chosen && mode.value !== "none") {
      previewMark.classList.add("has-image");
      previewMark.append(h("img", { src: chosen, alt: "" }));
    } else previewMark.textContent = Array.from(name)[0]?.toUpperCase() || "P";
    logoHint.textContent = mode.value === "receipt" && !saved.store_logo
      ? "No receipt logo is saved yet. Upload it in Receipt printing & logo below, or choose a separate POS logo."
      : "The menu logo and receipt logo can be chosen independently. No change to past invoices.";
  }
  picker.addEventListener("input", () => { hex.value = picker.value.toUpperCase(); updatePreview(); });
  hex.addEventListener("input", () => {
    if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) picker.value = hex.value;
    updatePreview();
  });
  displayName.addEventListener("input", updatePreview);
  mode.addEventListener("change", updatePreview);
  logoSize.addEventListener("input", updatePreview);
  logoFile.addEventListener("change", () => {
    const file = logoFile.files?.[0];
    if (!file) { pendingLogoTask = null; return; }
    pendingLogoTask = optimizeImage(file, { maxEdge: Number(menuMaxEdge.value),
      quality: Number(menuQuality.value), maxBytes: 200 * 1024 });
    logoCompressionInfo.textContent = "Optimising logo…";
    pendingLogoTask.then(async (result) => {
      if (logoFile.files?.[0] !== file) return;
      const nextUrl = URL.createObjectURL(result.blob);
      if (temporaryUrl) URL.revokeObjectURL(temporaryUrl);
      temporaryUrl = nextUrl;
      mode.value = "custom";
      logoCompressionInfo.textContent = `${readableBytes(file.size)} → ${readableBytes(result.blob.size)} · ${result.width} × ${result.height}`;
      updatePreview();
    }).catch((err) => {
      if (logoFile.files?.[0] !== file) return;
      pendingLogoTask = null;
      logoFile.value = "";
      logoCompressionInfo.textContent = err.message;
      toastError(err, "Could not optimise menu logo");
    });
  });

  const presets = h("div", { class: "branding-presets" },
    ...BRAND_PRESETS.map(([label, value]) => h("button", {
      class: "branding-swatch", style: `background:${value}`,
      type: "button", title: label, "aria-label": `Select ${label} accent`,
      onclick: () => { picker.value = value; hex.value = value; updatePreview(); },
    })));
  updatePreview();
  return h("div", { class: "card card-pad", style: "margin-bottom:16px", id: "store-branding" },
    h("h3", { style: "margin-bottom:4px" }, "POS appearance & branding"),
    h("p", { class: "form-hint", style: "margin-bottom:14px" },
      "Personalise the menu, logo, browser tab and accent across light and dark mode. " +
      "These are display settings; the store name and GST details printed on invoices are set below."),
    preview,
    h("div", { class: "form-grid", style: "margin-top:16px" },
      h("div", { class: "field" }, h("label", {}, "Name in sidebar and browser tab"), displayName),
      h("div", { class: "field" }, h("label", {}, "Menu logo size (24–48 px)"), logoSize)),
    h("div", { class: "field" }, h("label", {}, "Accent colour"),
      h("div", { class: "branding-accent-row" }, picker, hex),
      h("p", { class: "form-hint" }, "Choose a preset or enter a #RRGGBB colour. Very pale or dark hues are adjusted where needed for readable interface text.")),
    presets,
    h("div", { class: "form-grid" },
      h("div", { class: "field" }, h("label", {}, "Menu logo source"), mode),
      h("div", { class: "field" }, h("label", {}, "Upload menu logo (auto-compressed to 200 KiB)"), logoFile)),
    imageCompressionSettings(menuMaxEdge, menuQuality,
      "Choose settings before selecting the file; select it again to recompress."),
    logoCompressionInfo, logoHint,
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:12px" },
      h("button", { class: "btn primary", onclick: async () => {
        try {
          const values = {
            pos_display_name: displayName.value.trim(),
            pos_accent_color: hex.value.trim(),
            pos_logo_mode: mode.value,
            pos_logo_size: logoSize.value.trim(),
          };
          if (pendingLogoTask && logoFile.files?.length) {
            const processed = await pendingLogoTask;
            values.pos_logo = await asDataUrl(processed.blob);
          }
          state.settings = await api.saveSettings(values);
          applyBranding(state.settings);
          toast("Store appearance saved and applied.", "success");
          if (temporaryUrl) URL.revokeObjectURL(temporaryUrl);
          renderSettingsView(main);
        } catch (e) { toastError(e); }
      } }, icon("i-check"), "Save POS branding"),
      h("button", { class: "btn", onclick: async () => {
        try {
          state.settings = await api.saveSettings({
            pos_display_name: "pyPOS", pos_accent_color: DEFAULT_ACCENT,
            pos_logo_mode: "default", pos_logo_size: "28",
          });
          applyBranding(state.settings);
          toast("Default appearance restored. Uploaded logos were retained.", "success");
          if (temporaryUrl) URL.revokeObjectURL(temporaryUrl);
          renderSettingsView(main);
        } catch (e) { toastError(e); }
      } }, "Reset appearance"),
      h("button", { class: "btn subtle-danger", onclick: async () => {
        if (!saved.pos_logo) return toast("No separate POS logo is saved.", "warn");
        if (!(await confirmDialog({ title: "Remove separate POS logo?",
          message: "Receipt logo and historical invoices will not change.",
          confirmLabel: "Remove logo", danger: true }))) return;
        try {
          state.settings = await api.saveSettings({ pos_logo: "", pos_logo_mode: "default" });
          applyBranding(state.settings);
          toast("Separate POS logo removed.", "success");
          if (temporaryUrl) URL.revokeObjectURL(temporaryUrl);
          renderSettingsView(main);
        } catch (e) { toastError(e); }
      } }, "Remove uploaded POS logo")));
}

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

  // Printer branding is stored in the SQLite database and included in backups.
  const priceTaxSelect = h("select", { class: "input", "aria-label": "How are product prices entered?" },
    h("option", { value: "exclusive", selected: s.price_tax_mode !== "inclusive" },
      "Prices before GST (add tax at checkout)"),
    h("option", { value: "inclusive", selected: s.price_tax_mode === "inclusive" },
      "Prices include GST (e.g. GST-inclusive MRP)"));
  const paperSelect = h("select", { class: "input", "aria-label": "Paper / print layout" },
    ...[["58mm", "58mm thermal receipt"], ["80mm", "80mm thermal receipt"],
         ["A4", "A4 paper / PDF printing"]].map(([val, label]) =>
      h("option", { value: val, selected: s.receipt_paper === val }, label)));
  const titleSelect = h("select", { class: "input", "aria-label": "Receipt heading" },
    ...["Retail receipt", "Tax invoice", "Bill of supply"].map((x) =>
      h("option", { value: x, selected: s.receipt_heading === x }, x)));
  const localTaxSelect = h("select", { class: "input", "aria-label": "Local tax component" },
    ...["SGST", "UTGST"].map((x) =>
      h("option", { value: x, selected: s.store_local_tax === x }, x)));
  const printMode = h("select", { class: "input", "aria-label": "Receipt printer output mode" },
    ...[["thermal", "Epson TM-T81 (direct, no dialog)"],
       ["browser", "Browser / PDF (manual print dialog)"]].map(([v, label]) =>
      h("option", { value: v, selected: s.receipt_print_mode === v }, label)));
  const autoPrint = h("input", { type: "checkbox", checked: s.receipt_auto_print !== "false" });
  const printerQueue = h("input", { class: "input", type: "text", maxlength: "160",
    value: s.receipt_printer_name || "", placeholder: "Blank = default OS printer",
    "aria-label": "Installed printer queue name" });
  const printerInfo = h("p", { class: "form-hint", "aria-live": "polite" },
    "The installed printer must accept RAW ESC/POS, not PDF or ordinary driver-rendered pages.");
  void api.printingInfo().then(info => {
    printerInfo.textContent = `OS: ${info.platform}. Default printer: ${info.default_printer || "none detected"}. ` +
      "You can leave the printer name blank to use this default.";
  }).catch(() => {});
  const showLogo = h("input", { type: "checkbox", checked: s.show_receipt_logo === "true" });
  const logoWidth = h("input", { class: "input num", type: "number", min: "12",
    max: "65", value: s.receipt_logo_width || "32", step: "1" });
  const logoInput = h("input", { class: "input", type: "file",
    accept: "image/png,image/jpeg,image/webp", "aria-label": "Upload receipt logo" });
  const logoPreview = h("div", { style: "margin:10px 0;min-height:20px" },
    s.store_logo ? h("img", { src: s.store_logo, alt: "Saved receipt logo",
      style: "max-width:240px;max-height:100px;object-fit:contain" }) :
      h("span", { class: "form-hint" }, "No logo saved"));
  const receiptMaxEdge = imageSizeSelect("900");
  const receiptQuality = imageQualitySelect();
  const receiptImageInfo = h("p", { class: "form-hint", "aria-live": "polite" },
    "Large images are automatically resized/compressed to fit 200 KiB.");
  let receiptLogoTask = null;
  let receiptPreviewUrl = null;
  logoInput.addEventListener("change", () => {
    const file = logoInput.files?.[0];
    if (!file) { receiptLogoTask = null; return; }
    receiptLogoTask = optimizeImage(file, { maxEdge: Number(receiptMaxEdge.value),
      quality: Number(receiptQuality.value), maxBytes: 200 * 1024 });
    receiptImageInfo.textContent = "Optimising logo…";
    receiptLogoTask.then((result) => {
      if (logoInput.files?.[0] !== file) return;
      if (receiptPreviewUrl) URL.revokeObjectURL(receiptPreviewUrl);
      receiptPreviewUrl = URL.createObjectURL(result.blob);
      logoPreview.replaceChildren(h("img", { src: receiptPreviewUrl,
        alt: "Optimised receipt logo preview", style: "max-width:240px;max-height:100px;object-fit:contain" }));
      receiptImageInfo.textContent = `${readableBytes(file.size)} → ${readableBytes(result.blob.size)} · ${result.width} × ${result.height}`;
    }).catch((err) => {
      if (logoInput.files?.[0] !== file) return;
      receiptLogoTask = null;
      logoInput.value = "";
      receiptImageInfo.textContent = err.message;
      toastError(err, "Could not optimise receipt logo");
    });
  });
  const brandingCard = h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Receipt printing & logo"),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "A receipt heading alone does not establish GST compliance. Confirm the required invoice fields, " +
      "e-invoicing applicability and treatment of each supply with your accountant."),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "Direct thermal printing submits ESC/POS receipts through your computer's printer spooler. " +
      "The Epson TM-T81 has 512 printable dots (about 72 mm) on nominal 80 mm stock. " +
      "Use the printer's RAW queue on Windows, Linux or macOS. A queued job does not guarantee that paper emerged."),
    h("div", { class: "field" }, h("label", {}, "Print output"), printMode),
    h("label", { class: "credit-toggle" }, autoPrint,
      h("span", {}, "Automatically print completed sales, returns and exchanges")),
    h("div", { class: "field" }, h("label", {}, "Installed printer queue (optional)"), printerQueue),
    printerInfo,
    h("div", { class: "field" }, h("label", {}, "Paper / print layout"), paperSelect),
    h("div", { class: "field" }, h("label", {}, "Receipt heading"), titleSelect),
    h("div", { class: "field" }, h("label", {}, "Receipt footer message"), footerIn),
    h("div", { class: "field" }, h("label", {}, "Upload receipt logo (auto-compressed to 200 KiB)"), logoInput),
    imageCompressionSettings(receiptMaxEdge, receiptQuality,
      "Choose settings before selecting the file; select it again to recompress."),
    receiptImageInfo, logoPreview,
    h("label", { class: "credit-toggle" }, showLogo, h("span", {}, "Print logo on receipts")),
    h("div", { class: "field" }, h("label", {}, "Logo width in mm (12–65)"), logoWidth),
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
      h("button", { class: "btn primary", onclick: async () => {
        try {
          const values = {
            receipt_paper: paperSelect.value,
            receipt_print_mode: printMode.value,
            receipt_auto_print: autoPrint.checked ? "true" : "false",
            receipt_printer_name: printerQueue.value.trim(),
            receipt_heading: titleSelect.value,
            receipt_logo_width: logoWidth.value,
            show_receipt_logo: showLogo.checked ? "true" : "false",
            receipt_footer: footerIn.value.trim(),
          };
          if (receiptLogoTask && logoInput.files?.length) {
            const processed = await receiptLogoTask;
            values.store_logo = await asDataUrl(processed.blob);
          }
          state.settings = await api.saveSettings(values);
          toast("Receipt printing preferences saved.", "success");
          renderSettingsView(main);
        } catch (e) { toastError(e); }
      } }, icon("i-check"), "Save print settings"),
      h("button", { class: "btn", onclick: async () => {
        if (state.settings.receipt_print_mode === "thermal") {
          try {
            // Read from SAVED settings, not unsaved form edits, avoiding a
            // silent test to the wrong printer or paper setting.
            const job = await api.testThermalPrinter();
            toast(`Test slip queued for ${job.printer}.`, "success");
          } catch (e) { toastError(e, "Thermal test print failed"); }
          return;
        }
        // Browser/PDF mode is deliberately manual and uses the print dialog.
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
    h("h3", { style: "margin-bottom:6px" }, "Pricing and GST"),
    h("div", { class: "field" }, h("label", {}, "How are product prices entered?"), priceTaxSelect,
      h("p", { class: "form-hint" }, "Applies to NEW sales. Switching between inclusive and exclusive GST changes charged totals. Check catalog MRPs before changing; past invoices are preserved.")),
    h("div", { class: "field" }, h("label", {}, "Local tax component"), localTaxSelect),
    h("h4", { style: "margin:16px 0 8px" }, "Tax breakup display"),
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
        const values = {
          tax_breakup: breakupOn.checked ? "true" : "false",
          price_tax_mode: priceTaxSelect.value,
          store_local_tax: localTaxSelect.value,
        };
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

  const storeCard = h("div", { class: "card card-pad" },
    h("h3", {}, "Business identity"),
    h("p", { class: "form-hint" },
      "Seller details, registration and contact information used on new receipts."),
    form,
    h("button", { class: "btn primary", onclick: async () => {
      const values = {
        store_name: inputs.store_name.value.trim() || "My Store",
        store_address: addressIn.value.trim(),
        store_phone: inputs.store_phone.value.trim(),
        currency: inputs.currency.value.trim() || "₹",
        ...Object.fromEntries(Object.entries(businessInputs).map(([k, el]) => [k, el.value.trim()])),
        tax_label: inputs.tax_label.value.trim() || "Tax",
        low_stock_threshold: String(parseInt(inputs.low_stock_threshold.value, 10) || 5),
      };
      try {
        state.settings = await api.saveSettings(values);
        toast("Business details saved.", "success");
      } catch (e) { toastError(e); }
    } }, icon("i-check"), "Save business details"));

  const staffMenuCard = h("div", { class: "card card-pad" },
    h("h3", {}, "Staff menu visibility"),
    h("p", { class: "form-hint" },
      "Hiding Staff from the menu does not disable PIN login, permissions or audit logs."),
    (() => {
      const toggle = h("input", { type: "checkbox", checked: s.show_staff_menu !== "false" });
      return h("div", {},
        h("label", { class: "credit-toggle" }, toggle, h("span", {}, "Show Staff in main menu")),
        h("div", { class: "settings-actions" },
          h("button", { class: "btn primary", onclick: async () => {
            try {
              state.settings = await api.saveSettings({ show_staff_menu: toggle.checked ? "true" : "false" });
              applyBranding(state.settings);
              toast("Staff menu preference saved.", "success");
            } catch (e) { toastError(e); }
          } }, icon("i-check"), "Save menu preference"),
          h("button", { class: "btn", onclick: () => { location.hash = "#/staff"; } },
            "Open Staff management")));
    })());

  const staffPermissionCard = h("div", { class: "card card-pad" },
    h("h3", {}, "Individual staff permissions"),
    h("p", { class: "form-hint" },
      "Manage stock receiving, shortage corrections, imports, refunds, checkout and opening or closing the till per operator."),
    h("a", { href: "#/staff", class: "btn" }, "Manage staff access"));

  const discountCard = h("div", { class: "card card-pad" },
    h("h3", {}, "Standard staff discount limit"),
    h("p", { class: "form-hint" },
      "Applies to operators without Discount override permission. Grant overrides under Staff."),
    (() => {
      const max = h("input", { type: "number", class: "input", min: "0", max: "100", step: "1",
        style: "max-width:160px", value: s.cashier_max_discount_percent || "0" });
      return h("div", {},
        h("div", { class: "field" }, h("label", {}, "Maximum discount (%)"), max),
        h("button", { class: "btn primary", onclick: async () => {
          if (!/^(?:100|\d{1,2})$/.test(max.value)) {
            toast("Enter a whole percentage between 0 and 100.", "warn"); return;
          }
          try {
            state.settings = await api.saveSettings({ cashier_max_discount_percent: max.value });
            toast("Standard discount policy saved.", "success");
          } catch (e) { toastError(e); }
        } }, "Save discount policy"));
    })());

  const sampleDataCard = h("div", { class: "card card-pad" },
    h("h3", {}, "Sample data (new stores only)"),
    h("p", { class: "form-hint" },
      productsCount > 0
        ? "Your catalog already has products, so sample data cannot be loaded."
        : "Load a sample catalog and sales history to explore the interface. Do not use for live accounting."),
    h("button", { class: "btn", disabled: productsCount > 0, onclick: async () => {
      const ok = await confirmDialog({ title: "Load sample data?",
        message: "Adds sample groups, products and past sales. Only possible while the catalog is empty.",
        confirmLabel: "Load samples" });
      if (!ok) return;
      try {
        const r = await api.seedDemo();
        toast(`Added ${r.products} products and ${r.sales_created} sales.`, "success");
      } catch (e) { toastError(e); }
    } }, icon("i-catalog"), "Load sample data"));

  const updatesToggle = h("input", { type: "checkbox", checked: s.auto_updates_enabled !== "false",
    "aria-label": "Automatically check, download and activate software updates" });
  const updateStatus = h("p", { class: "form-hint", "aria-live": "polite" },
    s.auto_updates_enabled === "false"
      ? "Automatic updates are off. Any previously staged update will remain inactive."
      : "The managed launcher can check for verified releases, stage them and activate on the next launch.");
  const updateCard = h("div", { class: "card card-pad" },
    h("h3", {}, "Software updates"),
    h("p", { class: "form-hint" },
      "Control automatic updates on this device. This preference is shared with the installed launcher, not just this browser."),
    h("label", { class: "credit-toggle" }, updatesToggle,
      h("span", {}, "Enable automatic software updates")),
    updateStatus,
    h("p", { class: "form-hint" },
      "When off, the launcher does not automatically check, download, stage or activate pending releases. " +
      "Current app and sales data remain unchanged. You can still download and install a release manually."),
    h("div", { class: "settings-actions" },
      h("button", { class: "btn primary", onclick: async () => {
        try {
          const enabling = updatesToggle.checked;
          state.settings = await api.saveSettings({
            auto_updates_enabled: enabling ? "true" : "false",
          });
          updateStatus.textContent = enabling
            ? "Automatic updates enabled. A pending verified update may activate on next restart."
            : "Automatic updates disabled. Any previously staged update remains inactive.";
          toast(enabling ? "Automatic updates enabled." : "Automatic updates disabled.", "success");
        } catch (e) { toastError(e); }
      } }, icon("i-check"), "Save update preference"),
      h("a", { class: "btn", target: "_blank", rel: "noopener noreferrer",
        href: "https://github.com/aadi-bhattacharya/pyPOS/releases" }, "View available releases")));

  const sections = [
    { id: "store", title: "Store & tax", subtitle: "Business details, GST treatment and prices",
      cards: [storeCard, taxCard] },
    { id: "receipts", title: "Receipts & printer", subtitle: "Printer queue, paper and receipt branding",
      cards: [brandingCard] },
    { id: "appearance", title: "Appearance", subtitle: "POS name, colours, logos and image tools",
      cards: [brandingSettingsCard(main, s), imageOptimizerCard()] },
    { id: "customers", title: "Customers & returns", subtitle: "Loyalty points, refunds and exchanges",
      cards: [loyaltyCard, returnPolicyCard(s, () => renderSettingsView(main))] },
    { id: "staff", title: "Staff & security", subtitle: "Operator access, discounts and administrator login",
      cards: [staffPermissionCard, discountCard, staffMenuCard, passwordCard] },
    { id: "backups", title: "Backups & recovery", subtitle: "Local snapshots, restore and Google Drive",
      cards: [backupsCard()] },
    { id: "app", title: "App & updates", subtitle: "Version updates and first-run tools",
      cards: [updateCard, sampleDataCard] },
  ];

  const navigation = h("nav", { class: "settings-nav", "aria-label": "Settings categories" });
  const panels = h("div", { class: "settings-content" });
  const items = [];
  for (const group of sections) {
    const button = h("button", { type: "button", class: "settings-nav-button",
      "aria-controls": `settings-panel-${group.id}` },
      h("span", { class: "settings-nav-name" }, group.title),
      h("span", { class: "settings-nav-detail" }, group.subtitle));
    const panel = h("section", { class: "settings-panel", id: `settings-panel-${group.id}`,
      "aria-label": group.title },
      h("div", { class: "settings-panel-head" },
        h("h3", {}, group.title), h("p", { class: "form-hint" }, group.subtitle)),
      ...group.cards);
    button.addEventListener("click", () => showSection(group.id));
    navigation.append(button);
    panels.append(panel);
    items.push({ id: group.id, button, panel });
  }

  function showSection(id) {
    selectedSettingsSection = id;
    for (const item of items) {
      const active = item.id === id;
      item.panel.hidden = !active;
      item.button.classList.toggle("active", active);
      if (active) item.button.setAttribute("aria-current", "page");
      else item.button.removeAttribute("aria-current");
    }
  }

  main.append(
    h("div", { class: "view-head" },
      h("div", { class: "view-title" },
        h("h2", {}, "Settings"),
        h("div", { class: "sub" }, "Choose an area to configure. Save changes in that area."))),
    h("div", { class: "settings-layout" }, navigation, panels));
  showSection(sections.some(group => group.id === selectedSettingsSection)
    ? selectedSettingsSection : "store");
}
