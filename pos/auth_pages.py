"""HTML for staff onboarding and Settings sign-in.

Kept separate from Flask route dispatch so changes to the authentication UI
do not require editing the transaction API module. Session CSRF tokens are
issued by the existing security implementation.
"""
import html
import re

from pos.india import LOGO_PATTERN
from pos.security import issue_csrf_token


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title} · {brand_name}</title>
<link rel="stylesheet" href="/static/css/app.css">
<link rel="stylesheet" href="/static/css/login.css">
<script type="module" src="/static/js/login.js"></script>
</head><body class="auth-page auth-page--{mode}" style="--auth-brand-accent:{accent};--auth-brand-foreground:{foreground}">
<main class="auth-layout">
  {intro}
  <section class="card auth-panel" aria-labelledby="auth-title">
    <header class="auth-brand">
      {brand_mark}
      <span class="auth-brand-name">{brand_name}</span>
    </header>
    <div class="auth-head">
      <span class="auth-eyebrow">{eyebrow}</span>
      <h1 id="auth-title">{page_heading}</h1>
      <p>{page_description}</p>
    </div>
    {error}
    {body}
  </section>
</main>
</body></html>"""


_SETUP_INTRO = """
<aside class="auth-intro" aria-label="First-time setup guide">
  <span class="auth-intro-tag">FIRST-TIME SETUP</span>
  <h2>Make this register yours.</h2>
  <p>Create the first administrator account on this computer. You can add
     staff accounts and configure their individual permissions later.</p>
  <ol class="auth-steps">
    <li><span class="auth-step-num">1</span><div><strong>Create your account</strong>
        <span>Choose a name, username and secure login details</span></div></li>
    <li><span class="auth-step-num">2</span><div><strong>Open the register</strong>
        <span>You will be signed in automatically after setup</span></div></li>
    <li><span class="auth-step-num">3</span><div><strong>Start the day</strong>
        <span>Open your cash session before taking payments</span></div></li>
  </ol>
  <p class="auth-preserve">Your existing products, sales and inventory are kept.</p>
</aside>"""


def _login_page(error="", body=None, next="/#/settings", escaped_next=None,
                branding=None, mode="settings"):
    """Shared shell for staff setup, staff sign-in and Settings password."""
    if body is None:
        body = """
    <form class="auth-form" method="post" action="/login">
      <input type="hidden" name="next" value="{next}">
      <div class="field"><label for="pw">Administrator password</label>
        <div class="auth-secret"><input class="input" type="password" id="pw" name="password"
             autofocus autocomplete="current-password" required>
          <button class="auth-reveal" type="button" data-reveal="pw"
             aria-controls="pw" aria-pressed="false" aria-label="Show administrator password">Show</button>
        </div>
        <p class="auth-field-help">This is the Settings password, not your daily register PIN.</p>
      </div>
      <button class="btn primary block auth-submit" type="submit">Unlock Settings</button>
    </form>""".format(
            next=html.escape(escaped_next if escaped_next is not None else next, quote=True))
    # CSRF token is generated for the browser's session, not a global value.
    field = '<input type="hidden" name="_csrf" value="' + issue_csrf_token() + '">'
    body = re.sub(r'(<form\b[^>]*\bmethod=["\']post["\'][^>]*>)',
                  lambda match: match.group(1) + field, body,
                  flags=re.IGNORECASE)
    branding = branding or {}
    name = str(branding.get("pos_display_name") or "pyPOS").strip() or "pyPOS"
    raw_color = str(branding.get("pos_accent_color") or "#1B6BB5")
    color = raw_color if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw_color) else "#1B6BB5"
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    foreground = "#151515" if sum(x * w for x, w in zip(rgb, (0.299, 0.587, 0.114))) / 255 > 0.58 else "#ffffff"
    logo_mode = branding.get("pos_logo_mode") or "default"
    src = (branding.get("pos_logo") if logo_mode == "custom" else
           branding.get("store_logo") if logo_mode == "receipt" else "")
    if isinstance(src, str) and LOGO_PATTERN.fullmatch(src):
        mark = f'<span class="brand-mark has-image"><img alt="" src="{html.escape(src, quote=True)}"></span>'
    else:
        mark = (f'<span class="brand-mark">{html.escape(name[0].upper())}</span>'
                if logo_mode != "none" else "")
    pages = {
        "setup": ("Create administrator account", "Administrator setup",
                  "Create your account", "First-time setup on this computer"),
        "staff": ("Staff sign in", "STAFF SIGN IN", "Welcome back",
                  "Sign in with your staff username and PIN to open the register."),
        "settings": ("Settings sign in", "SETTINGS ACCESS", "Administrator verification",
                     "Enter your Settings password to continue."),
    }
    if mode not in pages:
        mode = "settings"
    title, eyebrow, heading, description = pages[mode]
    return _LOGIN_PAGE.format(
        error=error, body=body, brand_name=html.escape(name),
        brand_mark=mark, accent=color, foreground=foreground,
        intro=_SETUP_INTRO if mode == "setup" else "", mode=mode,
        page_title=html.escape(title), eyebrow=eyebrow,
        page_heading=heading, page_description=description)


def _staff_setup_form(existing_password=False, username="", display_name=""):
    """Explain the two credentials explicitly; retain safe, non-secret input on errors."""
    user = html.escape(str(username or ""), quote=True)
    display = html.escape(str(display_name or ""), quote=True)
    password_label = ("Existing administrator password" if existing_password
                      else "Create administrator password")
    password_help = ("Use the password already configured in Settings. It is different from the register PIN."
                     if existing_password else
                     "Protects Settings, staff permissions and backups. Use a strong password, distinct from your PIN.")
    password_auto = "current-password" if existing_password else "new-password"
    return f"""<form class="auth-form" method="post" action="/staff-setup">
      <section class="auth-form-section" aria-labelledby="setup-identity">
        <h2 id="setup-identity" class="auth-section-heading">Account details</h2>
        <div class="field">
          <label for="setup-name">Your name</label>
          <input class="input" id="setup-name" type="text" name="display_name"
            placeholder="e.g. Store owner" value="{display}" autocomplete="name"
            maxlength="100" required autofocus>
          <p class="auth-field-help">Shown on sales and staff activity records.</p>
        </div>
        <div class="field">
          <label for="setup-username">Login username</label>
          <input class="input" id="setup-username" type="text" name="username"
            placeholder="e.g. owner" value="{user}" autocomplete="username"
            autocapitalize="none" spellcheck="false" maxlength="32"
            pattern="[A-Za-z][A-Za-z0-9_.-]{{2,31}}" required
            aria-describedby="username-help">
          <p class="auth-field-help" id="username-help">Use 3–32 characters. Start with a letter; then letters, numbers, dots, dashes or underscores.</p>
        </div>
      </section>
      <section class="auth-form-section" aria-labelledby="setup-credentials">
        <h2 id="setup-credentials" class="auth-section-heading">Sign-in &amp; security</h2>
        <div class="field">
          <label for="setup-pin">Register PIN</label>
          <div class="auth-secret"><input class="input" id="setup-pin" type="password" name="pin"
            placeholder="6–12 digits" autocomplete="new-password" inputmode="numeric"
            pattern="[0-9]{{6,12}}" minlength="6" maxlength="12" required>
            <button class="auth-reveal" type="button" data-reveal="setup-pin"
              aria-controls="setup-pin" aria-pressed="false" aria-label="Show register PIN">Show</button>
          </div>
          <p class="auth-field-help">Your personal sign-in PIN. You do not need to open the till every day; authorised staff can open and close it using their own PINs.</p>
        </div>
        <div class="field">
          <label for="setup-admin-password">{password_label}</label>
          <div class="auth-secret"><input class="input" id="setup-admin-password" type="password"
            name="admin_password" autocomplete="{password_auto}" required>
            <button class="auth-reveal" type="button" data-reveal="setup-admin-password"
              aria-controls="setup-admin-password" aria-pressed="false"
              aria-label="Show administrator password">Show</button>
          </div>
          <p class="auth-field-help">{password_help}</p>
        </div>
      </section>
      <button class="btn primary block auth-submit" type="submit">Create owner account &amp; continue</button>
      <p class="auth-footnote">This is the first administrator account. Add staff accounts and choose their permissions in Staff &amp; permissions. Staff can start and close the till without the owner.</p>
    </form>"""


def _staff_login_form(username=""):
    safe_username = html.escape(str(username or ""), quote=True)
    return f"""<form class="auth-form" method="post" action="/staff-login">
      <div class="field"><label for="staff-username">Username</label>
        <input class="input" id="staff-username" name="username"
          value="{safe_username}" placeholder="e.g. cashier01"
          autocomplete="username" autocapitalize="none" spellcheck="false"
          required autofocus></div>
      <div class="field"><label for="staff-pin">Register PIN</label>
        <div class="auth-secret"><input class="input" id="staff-pin" name="pin"
          type="password" inputmode="numeric" autocomplete="current-password"
          placeholder="Your 6–12 digit PIN" required>
          <button class="auth-reveal" type="button" data-reveal="staff-pin"
            aria-controls="staff-pin" aria-pressed="false" aria-label="Show register PIN">Show</button>
        </div>
      </div>
      <button class="btn primary block auth-submit" type="submit">Sign in to register</button>
      <p class="auth-footnote">Need an account or having trouble signing in? Ask your store administrator.</p>
    </form>"""
