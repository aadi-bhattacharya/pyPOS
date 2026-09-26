# Design System: pyPOS — "Windows Business Classic"

Single source of truth for pyPOS's look and interaction language.
Derived from the cashier's point of reference: the Windows XP/7-era
enterprise register (toolbars, function-key status bars, dense grids,
group panels) — executed with modern restraint and zero internet
dependencies.

## 1. Visual Theme & Atmosphere

Dense, utilitarian, instantly familiar. The register should feel like
trustworthy shop-floor software a cashier has used for years: gray
chrome, crisp hairlines, one confident blue for money-moving actions,
and data presented in tight grids with monospaced figures. Decoration
is budgeted at zero; every pixel either carries information or gets cut.

- **Density:** 8/10 (cockpit — small paddings, tight rows)
- **Variance:** 3/10 (predictable, symmetric, grid-disciplined)
- **Motion:** 2/10 (≤200ms ease-out, transform/opacity only)

## 2. Color Palette & Roles

### Light theme (default)
| Token | Hex | Role |
|---|---|---|
| Workspace | `#EAEAE8` | App background |
| Surface | `#FFFFFF` | Panels, inputs, tiles |
| Chrome strip | `#FCFCFC→#F1F1F0` gradient | Headers, status bar |
| Hairline border | `#D9D9D9` | Panel edges, row lines |
| Control border | `#ADADAD` | Buttons, inputs |
| Ink text | `#1A1A1A` | Primary text (never `#000`) |
| Secondary text | `#444444` | Labels |
| Muted text | `#767676` | Hints, timestamps |
| Hover tint | `#E5F3FB` | Win7-style blue hover wash |
| Selection fill | `#CCE4F7` | Active nav, selected filters |
| **CTA blue** | gradient `#5F9DE9→#3F7FD6→#2F66C4`, border `#245BB5` | Charge/save/primary keys |
| Danger | gradient `#E58A8A→#BB2F2F` | Refund/delete keys |

### Dark theme ("Win10 dark app")
Background `#202020`, surface `#292929`, chrome `#333→#2B2B2B`,
borders `#3F3F3F/#565656`, text `#F1F1F1/#CCC/#969696`. Same CTA blues;
hover tints become translucent blue `rgba(76,144,224,.16)`.

**Rules:** max one accent family (signal blue). Status colors are
functional only (success/warn/danger soft backgrounds + dark readable
text). Never pure black. No neon glows, no glassmorphism blur, no
gradient text.

## 3. Typography Rules

- **UI font:** `Selawik` (self-hosted woff2, SIL license — Microsoft's
  open-source Segoe UI kin). Stack: `"Selawik", "Segoe UI", Tahoma,
  system-ui` — on Windows registers this resolves to genuine Segoe UI.
- **Money / telemetry:** `Consolas, "Courier New", ui-monospace` +
  `font-variant-numeric: tabular-nums`. All amounts, quantities,
  timestamps align in columns.
- **Scale:** body/UI 13px · secondary 12px · micro-labels 11px caps
  (+.04em tracking) · page titles 17px/600 · totals 19px/700.
- **Banned:** Inter-as-identity, decorative display fonts, serif fonts,
  webfont CDNs (offline guarantee).

## 4. Component Stylings

- **Buttons:** vertical chrome gradient (`#FDFDFD→#EBEBEB`), `#ADADAD`
  border, 3px radius. Hover = classic Aero wash (`#EAF6FD→#BEE6FD`) with
  `#3C7FB1` border. Active = pressed gradient + inset shadow + 1px drop.
  Primary = blue gradient tender key, white bold label. Height 32px
  (sm 26, lg/charge 46).
- **Inputs:** white field, `#ABADB3` border, inset top shadow; focus =
  `#3C7FB1` border + 3px blue ring. Label above, error below in red.
- **Panels:** white box, `#D9D9D9` border, gradient header strip with
  12px semibold caption. Group boxes are authentic here — boxed sections
  are allowed where they aid scanning (settings property sheets).
- **Tables:** sticky gradient header row, uppercase micro column caps,
  row hover = blue tint, tabular right-aligned numerics.
- **Tiles (register):** product cards, hover raises a 3px Aero ring,
  disabled at zero stock.
- **Chips:** filter buttons; active = blue gradient selection key.
- **Status badges:** square-ish chips, soft semantic background + dark
  text (`#EDF3EC/#346538`, `#FBF3DB/#956400`, `#FDEBEC/#9F2F2D` light).
- **Loading:** skeleton shimmer matching layout shape (tile grid, table
  rows, stat strip, chart block). Circular spinners are banned.
- **Empty states:** composed guidance with an action, never blank.
- **Modals:** title-strip header, footer on muted strip; pop-in ≤120ms.

## 5. Layout Principles

- Left sidebar nav (200px; icon-only <940px) + per-view toolbar strip.
- Register = two-pane grid: catalog left, cart right, function-key
  **status bar** beneath (F2 search · Enter add · +/- qty · F4 pay · ? help
  · live clock). The status bar is mandatory on transaction screens.
- Dense tables max-height with sticky headers; content scrolls, chrome
  doesn't.
- Single-column collapse below 940px; stats strip folds to 2×2.

## 6. Motion & Interaction

- Durations 100–200ms, `ease-out`; press feedback = `translateY(1px)`
  or inset shadow (physical key metaphor).
- Entry animations limited to modal/toast pop and skeleton shimmer.
- Animate `transform`/`opacity` only; no scroll-triggered motion; no
  perpetual loops except the skeleton shimmer.

## 7. Anti-Patterns (banned)

- Emojis anywhere in code or copy
- Pure `#000000`; neon glows; heavy drop shadows
- Rounded-pill large containers; glassmorphism blur panels
- Gradient text; custom cursors; scroll-hijacking or parallax
- Webfonts/CDNs at runtime (everything vendored)
- Inter as identity font; serif fonts in the app shell
- Generic circular spinners; silent failures without inline errors
- AI copy clichés ("Seamless", "Elevate", "Next-Gen")

## 8. Font Licensing

Selawik — SIL Open Font License 1.1, © Microsoft. Files in
`static/fonts/` with license copy at `static/fonts/LICENSE-Selawik.txt`.
