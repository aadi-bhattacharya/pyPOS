import { api, state, refreshSettings, fmt, csrfHeaders } from "../api.js";
import { h, icon, toast, toastError, confirmDialog, printReceipt } from "../ui.js";
import { fmtDate } from "../api.js";
import { DEFAULT_ACCENT, applyBranding, brandingPalette, normalizeAccent } from "../branding.js";
import { optimizeImage, asDataUrl, readableBytes, optimizedFilename, downloadOptimized } from "../imageOptimizer.js";

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

function imageQualitySelect(initial = "0.86") {
  return h("select", { class: "input", "aria-label": "Image compression quality" },
    ...[["0.94", "High quality"], ["0.86", "Balanced"],
      ["0.72", "Smaller file"]].map(([v, label]) =>
      h("option", { value: v, selected: v === initial }, label)));
}

function imageSizeSelect(initial = "900") {
  return h("select", { class: "input", "aria-label": "Maximum image dimension" },
    ...[["256", "256 px"], ["384", "384 px"], ["512", "512 px"],
      ["900", "900 px"], ["1200", "1200 px"], ["1600", "1600 px"]]
      .map(([v, label]) => h("option", { value: v, selected: v === initial }, label)));
}

function imageCompressionSettings(maxEdge, quality, hint = "Optimisation runs in your browser; original files are untouched.") {
  return h("div", { class: "image-opt-controls" },
    h("div", { class: "field" }, h("label", {}, "Maximum dimension"), maxEdge),
    h("div", { class: "field" }, h("label", {}, "Image quality"), quality),
    h("p", { class: "form-hint" }, hint));
}

function imageOptimizerCard() {
  const fileInput = h("input", { class: "input", type: "file", multiple: true,
    accept: "image/png,image/jpeg,image/webp", "aria-label": "Choose images to optimise" });
  const maxEdge = imageSizeSelect("900");
  const quality = imageQualitySelect();
  const format = h("select", { class: "input", "aria-label": "Output image format" },
    ...[["auto", "Automatic (preserve transparency)"], ["image/webp", "WebP"],
      ["image/png", "PNG"], ["image/jpeg", "JPEG (white background)"],
    ].map(([value, label]) => h("option", { value }, label)));
  const results = h("div", { class: "image-opt-results", "aria-live": "polite" });
  const run = h("button", { class: "btn primary" }, "Optimise selected images");
  run.addEventListener("click", async () => {
    const files = Array.from(fileInput.files || []);
    if (!files.length) return toast("Choose at least one image.", "warn");
    if (files.length > 20) return toast("Optimise up to 20 images at once.", "warn");
    run.disabled = true;
    results.replaceChildren();
    try {
      for (const file of files) {
        try {
          const result = await optimizeImage(file, {
            maxEdge: Number(maxEdge.value), quality: Number(quality.value),
            maxBytes: 24 * 1024 * 1024, format: format.value,
            keepSmallOriginal: false,
          });
          // Auto mode may keep an already-small image; never undo a requested
          // dimension cap or an explicit output-format conversion.
          const keepOriginal = format.value === "auto" && result.blob.size >= file.size &&
            Math.max(result.inputWidth, result.inputHeight) <= Number(maxEdge.value);
          const output = keepOriginal ? file : result.blob;
          const outputName = keepOriginal ? file.name : optimizedFilename(file.name, output.type);
          const outWidth = keepOriginal ? result.inputWidth : result.width;
          const outHeight = keepOriginal ? result.inputHeight : result.height;
          results.append(h("div", { class: "image-opt-result" },
            h("div", {}, h("b", {}, file.name), h("div", { class: "form-hint" },
              `${readableBytes(file.size)} → ${readableBytes(output.size)} · ${outWidth}×${outHeight}`)),
            h("button", { class: "btn", onclick: () => downloadOptimized(output, outputName) },
              "Download")));
        } catch (e) {
          results.append(h("p", { class: "image-opt-error" }, `${file.name}: ${e.message}`));
        }
      }
    } finally { run.disabled = false; }
  });
  return h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Image downscaler & compressor"),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "Prepare logos and future catalogue photos without uploading them anywhere. " +
      "Images are processed entirely on this device. The POS currently stores receipt/menu logos, not product photos."),
    h("div", { class: "field" }, h("label", {}, "Images (PNG, JPEG or WebP; max 24 MiB each)"), fileInput),
    h("div", { class: "image-opt-controls" },
      h("div", { class: "field" }, h("label", {}, "Longest side"), maxEdge),
      h("div", { class: "field" }, h("label", {}, "Quality"), quality),
      h("div", { class: "field" }, h("label", {}, "Format"), format)),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "Original images stay unchanged. Transparent images stay transparent in Auto/WebP/PNG; " +
      "JPEG replaces transparency with white. Image metadata may be removed when recompressing."),
    run, results);
}

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

function returnPolicyCard(main, saved) {
  const rules = [
    ["refund_allow_original", "Record cash / card / UPI refunds",
      "Off: staff cannot issue a direct payout/refund. Existing recorded refunds remain unchanged."],
    ["refund_allow_credit", "Issue named-customer store credit",
      "Credit requires a registered customer ID. An anonymous shopper cannot hold a balance without an identity."],
    ["exchange_allow", "Allow item exchanges",
      "An exchange creates a linked return and new sale in one transaction."],
    ["exchange_allow_anonymous", "Allow walk-in exchanges without customer details",
      "No name or phone required; the return offsets the replacement invoice directly, with no cash payout or wallet."],
    ["exchange_allow_forfeit_difference", "Allow forfeiting unused exchange value",
      "Off by default. If the replacement costs less and no credit can be issued, block the exchange. When enabled, the cashier must explicitly acknowledge the forfeited amount; it is recorded for audit. Confirm this policy is lawful before enabling it."],
  ];
  const boxes = Object.fromEntries(rules.map(([key]) => [key,
    h("input", { type: "checkbox", checked: saved[key] !== "false" &&
      (key !== "exchange_allow_forfeit_difference" || saved[key] === "true") })]));
  return h("div", { class: "card card-pad", style: "margin-top:16px" },
    h("h3", { style: "margin-bottom:6px" }, "Returns & exchange rules"),
    h("p", { class: "form-hint", style: "margin-bottom:12px" },
      "Administrator policy. Enforced by the server, not only by the cashier screen. " +
      "Changing a rule affects future returns and exchanges, not historical documents."),
    ...rules.map(([key, label, help]) =>
      h("div", { style: "margin-bottom:12px" },
        h("label", { class: "credit-toggle" }, boxes[key], h("span", {}, label)),
        h("p", { class: "form-hint" }, help))),
    h("button", { class: "btn primary", onclick: async () => {
      const values = Object.fromEntries(rules.map(([key]) =>
        [key, boxes[key].checked ? "true" : "false"]));
      if (values.exchange_allow_forfeit_difference === "true" &&
          saved.exchange_allow_forfeit_difference !== "true") {
        if (!(await confirmDialog({
          title: "Allow forfeiting exchange differences?",
          message: "This permits the store to retain the unused value of a return " +
            "when no customer credit is issued. The cashier must explicitly confirm each case. " +
            "Check consumer protection and GST treatment before enabling this rule.",
          confirmLabel: "Enable with acknowledgement", danger: true,
        }))) return;
      }
      try {
        state.settings = await api.saveSettings(values);
        toast("Returns & exchange rules saved.", "success");
        renderSettingsView(main);
      } catch (e) { toastError(e); }
    } }, icon("i-check"), "Save return rules"));
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
    h("div", { class: "field" }, h("label", {}, "How are product prices entered?"), priceTaxSelect,
      h("p", { class: "form-hint" }, "This setting changes how ALL catalog prices are interpreted for NEW sales. " +
        "Do not switch it without reviewing your SKU prices. Past invoices and refunds keep their original mode.")),
    h("div", { class: "field" }, h("label", {}, "Paper / print layout"), paperSelect),
    h("div", { class: "field" }, h("label", {}, "Receipt heading"), titleSelect),
    h("div", { class: "field" }, h("label", {}, "Local tax component"), localTaxSelect),
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
            price_tax_mode: priceTaxSelect.value,
            receipt_heading: titleSelect.value,
            receipt_logo_width: logoWidth.value,
            show_receipt_logo: showLogo.checked ? "true" : "false",
            store_local_tax: localTaxSelect.value,
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

    h("div", { style: "max-width:780px" },
      brandingSettingsCard(main, s),
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
      imageOptimizerCard(),
      taxCard,
      loyaltyCard,
      returnPolicyCard(main, s),
      h("div", { class: "card card-pad", style: "margin-top:16px" },
        h("h3", { style: "margin-bottom:8px" }, "Staff menu visibility"),
        h("p", { class: "form-hint", style: "margin-bottom:12px" },
          "Choose whether Staff appears in the sidebar. Staff PIN login, permissions and activity logging continue working when hidden."),
        (() => {
          const toggle = h("input", { type: "checkbox", checked: s.show_staff_menu !== "false" });
          return h("div", {},
            h("label", { class: "credit-toggle" }, toggle, h("span", {}, "Show Staff in main menu")),
            h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:12px" },
              h("button", { class: "btn primary", onclick: async () => {
                try {
                  state.settings = await api.saveSettings({ show_staff_menu: toggle.checked ? "true" : "false" });
                  applyBranding(state.settings);
                  toast("Staff menu visibility saved.", "success");
                } catch (e) { toastError(e); }
              } }, icon("i-check"), "Save menu preference"),
              h("button", { class: "btn", onclick: () => { location.hash = "#/staff"; } },
                "Open Staff management")));
        })()),

      h("div", { class: "card card-pad", style: "margin-top:16px" },
        h("h3", { style: "margin-bottom:8px" }, "Individual staff permissions"),
        h("p", { class: "form-hint" },
          "Stock receiving, shortage corrections, imports, refunds and daily opening/closing are now controlled per operator, not through global cashier/manager roles."),
        h("a", { href: "#/staff", class: "btn" }, "Manage staff access")),

      h("div", { class: "card card-pad", style: "margin-top:16px" },
        h("h3", { style: "margin-bottom:8px" }, "Standard staff discount limit"),
        h("p", { class: "form-hint" },
          "Applies to employees without the Discount override permission. For special approvals, grant that permission individually under Staff."),
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
        })()),

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
