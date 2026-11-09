/** Image optimization tools for store assets (never touches the sales DB). */
import { h, toast, toastError } from "../ui.js";
import { optimizeImage, readableBytes, optimizedFilename, downloadOptimized } from "../imageOptimizer.js";

export function imageQualitySelect(initial = "0.86") {
  return h("select", { class: "input", "aria-label": "Image compression quality" },
    ...[["0.94", "High quality"], ["0.86", "Balanced"],
      ["0.72", "Smaller file"]].map(([v, label]) =>
      h("option", { value: v, selected: v === initial }, label)));
}

export function imageSizeSelect(initial = "900") {
  return h("select", { class: "input", "aria-label": "Maximum image dimension" },
    ...[["256", "256 px"], ["384", "384 px"], ["512", "512 px"],
      ["900", "900 px"], ["1200", "1200 px"], ["1600", "1600 px"]]
      .map(([v, label]) => h("option", { value: v, selected: v === initial }, label)));
}

export function imageCompressionSettings(maxEdge, quality, hint = "Optimisation runs in your browser; original files are untouched.") {
  return h("div", { class: "image-opt-controls" },
    h("div", { class: "field" }, h("label", {}, "Maximum dimension"), maxEdge),
    h("div", { class: "field" }, h("label", {}, "Image quality"), quality),
    h("p", { class: "form-hint" }, hint));
}

export function imageOptimizerCard() {
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
