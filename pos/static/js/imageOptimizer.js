// Reusable, entirely local image downscaling. Designed for logo uploads and
// future catalogue photos. Never changes a server-side image or original file.
export const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
export const LOGO_LIMIT = 200 * 1024; // Keep in sync with pos/india.py.
const INPUT_LIMIT = 24 * 1024 * 1024;
const PIXEL_LIMIT = 32_000_000;

export function readableBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "Unknown size";
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KiB` : `${(n / 1048576).toFixed(2)} MiB`;
}

export function validateImageFile(file) {
  if (!file || !IMAGE_TYPES.has(file.type)) throw new Error("Choose a PNG, JPEG or WebP image.");
  if (!file.size) throw new Error("The selected image is empty.");
  if (file.size > INPUT_LIMIT) throw new Error("Image exceeds the 24 MiB input limit. Resize it before importing.");
}

async function decode(file) {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(file);
      return { image: bitmap, width: bitmap.width, height: bitmap.height,
        dispose: () => bitmap.close() };
    } catch (_) { /* Fall back for browsers without a compatible decoder. */ }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error("Image could not be decoded."));
      img.src = url;
    });
    return { image: img, width: img.naturalWidth, height: img.naturalHeight,
      dispose: () => {} };
  } finally {
    URL.revokeObjectURL(url);
  }
}

function toBlob(canvas, mime, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (!blob) return reject(new Error("The browser could not encode this image."));
      // Some browsers silently substitute PNG for unsupported output types.
      resolve(blob.type === mime ? blob : null);
    }, mime, quality);
  });
}

export async function asDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Could not prepare image for upload."));
    reader.readAsDataURL(blob);
  });
}

/**
 * @param {File} file - original, never mutated
 * @param {object} options - maxEdge, maxBytes, quality, format, keepSmallOriginal
 * @returns {Promise<{blob, mime, width, height, inputWidth, inputHeight, savedBytes, changed}>}
 */
export async function optimizeImage(file, options = {}) {
  validateImageFile(file);
  const maxEdge = Number(options.maxEdge ?? 900);
  const maxBytes = Number(options.maxBytes ?? LOGO_LIMIT);
  const quality = Number(options.quality ?? 0.86);
  const format = options.format || "auto";
  if (!Number.isInteger(maxEdge) || maxEdge < 64 || maxEdge > 4096)
    throw new Error("Maximum dimension must be 64–4096 pixels.");
  if (!Number.isInteger(maxBytes) || maxBytes < 8192 || maxBytes > INPUT_LIMIT)
    throw new Error("Output byte limit is invalid.");
  if (!Number.isFinite(quality) || quality < 0.4 || quality > 1)
    throw new Error("Image quality must be between 40% and 100%.");
  if (!["auto", "image/png", "image/webp", "image/jpeg"].includes(format))
    throw new Error("Unsupported output format.");

  const decoded = await decode(file);
  try {
    const { width, height } = decoded;
    if (!width || !height || width * height > PIXEL_LIMIT)
      throw new Error("Image has invalid dimensions or exceeds 32 megapixels.");
    if (options.keepSmallOriginal !== false && file.size <= maxBytes &&
        Math.max(width, height) <= maxEdge && (format === "auto" || format === file.type)) {
      return { blob: file, mime: file.type, width, height, inputWidth: width,
        inputHeight: height, savedBytes: 0, changed: false };
    }
    const scale = Math.min(1, maxEdge / Math.max(width, height));
    let w = Math.max(1, Math.round(width * scale));
    let h = Math.max(1, Math.round(height * scale));
    let best = null;
    for (let attempt = 0; attempt < 9; attempt++) {
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) throw new Error("Canvas image optimisation is unavailable in this browser.");
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(decoded.image, 0, 0, w, h);
      // Don't discard transparency from an uploaded logo without explicit opt-in.
      const pixels = ctx.getImageData(0, 0, w, h).data;
      let transparent = false;
      for (let i = 3; i < pixels.length; i += 4) {
        if (pixels[i] < 255) { transparent = true; break; }
      }
      const formats = format === "auto"
        ? (transparent ? ["image/png", "image/webp"] : ["image/webp", "image/jpeg", "image/png"])
        : [format];
      let chosen = null;
      for (const mime of formats) {
        if (mime === "image/jpeg" && transparent) {
          // Explicit JPEG means white background, never black/undefined pixels.
          const whiteCanvas = document.createElement("canvas");
          whiteCanvas.width = w; whiteCanvas.height = h;
          const white = whiteCanvas.getContext("2d");
          white.fillStyle = "#ffffff";
          white.fillRect(0, 0, w, h);
          white.drawImage(canvas, 0, 0);
          const blob = await toBlob(whiteCanvas, mime, quality);
          if (blob && (!chosen || blob.size < chosen.size)) chosen = blob;
        } else {
          const blob = await toBlob(canvas, mime, quality);
          if (blob && (!chosen || blob.size < chosen.size)) chosen = blob;
        }
      }
      canvas.width = canvas.height = 0;
      if (!chosen) throw new Error("This browser cannot encode the selected image format.");
      if (!best || chosen.size < best.blob.size) best = { blob: chosen, width: w, height: h };
      if (chosen.size <= maxBytes) break;
      w = Math.max(1, Math.floor(w * 0.78));
      h = Math.max(1, Math.floor(h * 0.78));
    }
    if (!best || best.blob.size > maxBytes)
      throw new Error(`Could not reduce the image below ${readableBytes(maxBytes)}. Try a smaller dimension.`);
    if (options.keepSmallOriginal !== false && file.size <= maxBytes &&
        Math.max(width, height) <= maxEdge && file.size <= best.blob.size &&
        (format === "auto" || format === file.type)) {
      return { blob: file, mime: file.type, width, height, inputWidth: width,
        inputHeight: height, savedBytes: 0, changed: false };
    }
    return { blob: best.blob, mime: best.blob.type, width: best.width, height: best.height,
      inputWidth: width, inputHeight: height, savedBytes: file.size - best.blob.size,
      changed: true };
  } finally { decoded.dispose(); }
}

export function optimizedFilename(filename, mime) {
  const extension = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" }[mime];
  const base = String(filename || "image").replace(/\.[^.]+$/, "").replace(/[^\w .()-]/g, "_").slice(0, 120);
  return `${base || "image"}-optimized.${extension || "png"}`;
}

export function downloadOptimized(blob, name) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = name;
  document.body.append(anchor);
  anchor.click(); anchor.remove();
  // Firefox needs the blob URL to survive the click dispatch.
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
