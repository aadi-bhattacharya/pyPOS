"""Minimal Google Drive integration using pure stdlib (urllib).

OAuth 2.1 flow (authorization code):
  1. build_auth_url()      -> send user's browser to Google consent
  2. exchange_code()       -> callback handler trades ?code= for tokens
  3. refresh_access()      -> short-lived access tokens (cached in-process)

Drive uploads are scoped to 'drive.file': the app can only see files it
created, so a leaked token exposes nothing else in the user's Drive.
"""

import json
import time
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
LIST_URL = "https://www.googleapis.com/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"


class GoogleDriveError(Exception):
    """Raised when Google authentication or upload fails."""


# In-process access-token cache: {"token": str, "exp": epoch}
_access_cache = {}


def build_auth_url(client_id, redirect_uri):
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # consent forces Google to hand out a refresh token even if the user
        # approved this app before.
        "prompt": "consent",
    })
    return f"{AUTH_URL}?{params}"


def _post_form(url, fields, timeout=30):
    data = urlencode(fields).encode("utf-8")
    req = Request(url, data=data, method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error_description", "")
        except Exception:
            pass
        raise GoogleDriveError(f"Google rejected the request ({e.code}): {detail}") from e
    except URLError as e:
        raise GoogleDriveError(
            f"Cannot reach Google (check your internet connection): {e.reason}") from e


def exchange_code(code, client_id, client_secret, redirect_uri):
    """Trade the ?code= from the callback for a refresh + access token."""
    resp = _post_form(TOKEN_URL, {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    if "refresh_token" not in resp:
        raise GoogleDriveError(
            "Google did not return a refresh token — reconnect with consent "
            "and make sure access_type=offline is set.")
    return {
        "refresh_token": resp["refresh_token"],
        "access_token": resp.get("access_token"),
        "expires_in": resp.get("expires_in", 3600),
    }


def refresh_access(refresh_token, client_id, client_secret):
    """Return a valid access token, refreshing/caching when possible."""
    cached = _access_cache.get(refresh_token)
    if cached and cached["exp"] > time.time() + 60:
        return cached["token"]
    resp = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    token = resp.get("access_token")
    if not token:
        raise GoogleDriveError("Google did not return an access token")
    _access_cache[refresh_token] = {
        "token": token, "exp": time.time() + int(resp.get("expires_in", 3600)),
    }
    return token


def revoke_token(refresh_token):
    try:
        _post_form("https://oauth2.googleapis.com/revoke",
                   {"token": refresh_token})
    except GoogleDriveError:
        pass  # best effort — clearing locally is what matters
    _access_cache.pop(refresh_token, None)


def _request_json(url, token, method="GET", timeout=30):
    req = Request(url, method=method,
                  headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8"))
            detail = payload.get("error", {}).get("message", "") or str(payload.get("error", ""))
        except Exception:
            pass
        raise GoogleDriveError(f"Drive API error ({e.code}): {detail}") from e
    except URLError as e:
        raise GoogleDriveError(f"Cannot reach Google Drive: {e.reason}") from e


def upload_file(token, filename, content: bytes):
    """Multipart upload of one backup file to the user's Drive root."""
    boundary = "pyPOS" + uuid.uuid4().hex[:16]
    metadata = json.dumps({"name": filename})
    parts = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    body = parts + content + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = Request(
        f"{UPLOAD_URL}?uploadType=multipart&fields=id,name",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        })
    try:
        with urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8"))
            detail = payload.get("error", {}).get("message", "")
        except Exception:
            pass
        raise GoogleDriveError(f"Drive upload failed ({e.code}): {detail}") from e
    except URLError as e:
        raise GoogleDriveError(f"Cannot reach Google Drive during upload: {e.reason}") from e


def list_backups_on_drive(token):
    q = urlencode({
        "q": "name contains 'pypos-' and trashed = false",
        "orderBy": "createdTime desc",
        "fields": "files(id,name,createdTime)",
        "pageSize": "200",
    })
    data = _request_json(f"{LIST_URL}?{q}", token)
    return data.get("files", [])


def delete_file(token, file_id):
    _request_json(f"{LIST_URL}/{file_id}", token, method="DELETE")
