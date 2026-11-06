// Store-facing POS branding. All colours are derived locally; no CDN or network assets.
// The official invoice/store name remains separate from the sidebar display name.
export const DEFAULT_ACCENT = "#1B6BB5";
const DEFAULT_NAME = "pyPOS";
const LOGO_DATA = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/;

const clamp = (x) => Math.max(0, Math.min(255, Math.round(x)));
const toRgb = (hex) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const toHex = (rgb) => `#${rgb.map(x => clamp(x).toString(16).padStart(2, "0")).join("").toUpperCase()}`;
const blend = (a, b, ratio) => toHex(toRgb(a).map((v, i) => v * (1 - ratio) + toRgb(b)[i] * ratio));
const channel = (v) => { const c = v / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
const luminosity = (hex) => {
  const [r, g, b] = toRgb(hex).map(channel);
  return .2126 * r + .7152 * g + .0722 * b;
};
const contrast = (a, b) => {
  const x = luminosity(a), y = luminosity(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
};
const rgbCss = (hex, opacity) => `rgba(${toRgb(hex).join(", ")}, ${opacity})`;

export function normalizeAccent(value) {
  return /^#[\da-fA-F]{6}$/.test(String(value || "")) ? value.toUpperCase() : DEFAULT_ACCENT;
}

function readableAccent(raw, dark) {
  const bg = dark ? "#292929" : "#FFFFFF";
  const toward = dark ? "#FFFFFF" : "#000000";
  for (let i = 0; i <= 100; i++) {
    const candidate = blend(raw, toward, i / 100);
    if (contrast(candidate, bg) >= 4.5) return candidate;
  }
  return toward;
}

export function brandingPalette(value, dark = false) {
  const base = normalizeAccent(value);
  const accent = readableAccent(base, dark);
  const surface = dark ? "#292929" : "#FFFFFF";
  const hover = blend(base, surface, dark ? .74 : .87);
  const foreground = contrast(base, "#FFFFFF") >= contrast(base, "#151515")
    ? "#FFFFFF" : "#151515";
  return {
    "--brand-color": base,
    "--accent": accent,
    "--accent-hover": readableAccent(blend(accent, dark ? "#FFFFFF" : "#000000", .12), dark),
    "--accent-text": foreground,
    "--accent-soft": blend(base, surface, dark ? .77 : .84),
    "--accent-ring": rgbCss(base, .38),
    "--focus": accent,
    "--hover": hover,
    "--cta-grad-top": blend(base, "#FFFFFF", .06),
    "--cta-grad-mid": base,
    "--cta-grad-bot": blend(base, "#000000", .08),
    "--cta-border": blend(base, "#000000", dark ? .18 : .22),
    "--cta-text": foreground,
    "--grad-btn-hover": `linear-gradient(180deg, ${blend(hover, surface, .35)}, ${hover})`,
    "--grad-btn-active": `linear-gradient(180deg, ${hover}, ${blend(base, surface, .78)})`,
  };
}

export function brandingName(settings = {}) {
  const text = String(settings.pos_display_name || "").trim();
  return text || DEFAULT_NAME;
}

export function brandingLogo(settings = {}) {
  const mode = settings.pos_logo_mode || "default";
  const source = mode === "custom" ? settings.pos_logo :
    mode === "receipt" ? settings.store_logo : "";
  return typeof source === "string" && LOGO_DATA.test(source) ? source : "";
}

export function applyBranding(settings = {}, theme) {
  // Navigation preference does not change staff authentication or authorisation.
  const staffMenu = document.querySelector('#nav a[data-view="staff"]');
  if (staffMenu) staffMenu.hidden = settings.show_staff_menu === "false";
  const dark = (theme ?? document.documentElement.dataset.theme) === "dark";
  const palette = brandingPalette(settings.pos_accent_color || DEFAULT_ACCENT, dark);
  const root = document.documentElement;
  for (const [name, value] of Object.entries(palette)) root.style.setProperty(name, value);
  const requestedSize = Number(settings.pos_logo_size);
  const size = Number.isFinite(requestedSize) && requestedSize >= 24 && requestedSize <= 48 ? requestedSize : 28;
  root.style.setProperty("--brand-mark-size", `${size}px`);

  const name = brandingName(settings);
  const nameNode = document.getElementById("app-brand-name");
  const mark = document.getElementById("app-brand-mark");
  if (nameNode) {
    nameNode.textContent = name;
    nameNode.title = name;
  }
  if (mark) {
    const mode = settings.pos_logo_mode || "default";
    mark.hidden = mode === "none";
    mark.classList.remove("has-image");
    mark.replaceChildren();
    const logo = brandingLogo(settings);
    if (logo && mode !== "none") {
      const img = document.createElement("img");
      img.src = logo;
      img.alt = "";
      img.className = "brand-img";
      mark.append(img);
      mark.classList.add("has-image");
    } else {
      mark.textContent = Array.from(name)[0]?.toUpperCase() || "P";
    }
  }
  document.title = name;
  const favicon = document.getElementById("app-favicon");
  if (favicon) {
    const logo = brandingLogo(settings);
    if (logo) favicon.href = logo;
    else {
      // Use a fixed monogram in the generated SVG: no user text can enter markup.
      const fill = palette["--brand-color"];
      const fg = palette["--cta-text"];
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="${fill}"/><text x="16" y="23" text-anchor="middle" font-family="sans-serif" font-size="21" font-weight="bold" fill="${fg}">P</text></svg>`;
      favicon.href = "data:image/svg+xml," + encodeURIComponent(svg);
    }
  }
}
