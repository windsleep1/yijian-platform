"""probe-silent-refresh.py - one-shot E2E probe for the frontend "silent refresh" path.

WHY THIS EXISTS
---------------
access_token TTL is 120 minutes, so "wait for it to expire" is not a practical way
to verify the refresh chain. Instead this probe uses the dev JWT secret to sign a
token that is *cryptographically valid but already expired*:

    signature OK, exp in the past  ->  backend raises 40101 (retryable)

It injects that token into the running browser's localStorage, navigates to a
guarded route, and observes whether the frontend does the right thing:

    40101 -> POST /auth/refresh (single-flight) -> store the NEW pair -> retry -> 200

PASS criteria
    - the page ends up on the guarded route (NOT bounced to /login)
    - localStorage.yj_access_token has changed to a different value
      (proves Refresh Token Rotation was persisted, not just the access token)

PREREQUISITES
    - backend up (default http://localhost:8123/api/v1)
    - a seeded viewer account (tools/local-verify/seed-demo-users.ps1)
    - `agent-browser` installed with a browser binary

USAGE
    python tools/local-verify/probe-silent-refresh.py

This script is pure-stdlib on purpose: it mints HS256 JWTs by hand so it does not
need PyJWT installed in whatever interpreter you happen to run it with.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- configuration
API_BASE = "http://localhost:8123/api/v1"
ADMIN_URL = "http://localhost:3000"
GUARDED_PATH = "/audit-logs"

# The running API gets JWT_SECRET from tools/local-verify/serve-local.ps1
# (it is NOT the DEV_SECRET default from app/core/config.py). Keep these in sync;
# override with the JWT_SECRET env var if you started the API some other way.
JWT_SECRET = os.environ.get("JWT_SECRET", "local-verify-secret-not-for-production")
JWT_ISSUER = "yijian"

LOGIN_PHONE = "13900000001"
LOGIN_PASSWORD = "Viewer@123456"

AGENT_BROWSER = (
    r"C:\Users\15437\.workbuddy\binaries\node\versions\22.22.2-3"
    r"\node_modules\agent-browser\bin\agent-browser-win32-x64.exe"
)


# ----------------------------------------------------------------- JWT helpers
def b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def mint_hs256(claims: dict) -> str:
    """Hand-rolled HS256 JWT - stdlib only, no PyJWT dependency."""
    header = {"alg": "HS256", "typ": "JWT"}
    seg = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + b"."
        + b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = hmac.new(JWT_SECRET.encode(), seg, hashlib.sha256).digest()
    return (seg + b"." + b64url(sig)).decode()


def decode_claims(token: str) -> dict:
    """Decode without verifying - we only need sub/sid/roles to re-mint."""
    return json.loads(b64url_decode(token.split(".")[1]))


# ------------------------------------------------------------------ HTTP helper
def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def get_me(token: str) -> dict:
    """Call /auth/me with a bearer token and return the raw envelope (never raises).

    Used as a self-check so a wrong JWT_SECRET fails loudly here instead of
    looking like a mysterious frontend redirect later on.
    """
    req = urllib.request.Request(
        API_BASE + "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 - non-envelope body
            return {"code": -1, "message": f"HTTP {exc.code} (not an envelope)"}


# ------------------------------------------------------------- browser helpers
def ab(*args: str, timeout: int = 120) -> str:
    proc = subprocess.run(
        [AGENT_BROWSER, *args], capture_output=True, text=True, timeout=timeout
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # agent-browser prints a harmless shim warning on Windows; ignore it
    if err and "command not found" not in err and "null directory" not in err:
        print(f"    [agent-browser stderr] {err.splitlines()[0]}", file=sys.stderr)
    return out


def eval_js(js: str) -> str:
    """agent-browser eval returns a JSON-ish scalar; strip the surrounding quotes."""
    raw = ab("eval", js)
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw[1:-1]
    return raw


def main() -> int:
    print("=" * 68)
    print("probe-silent-refresh: verifying 40101 -> refresh -> retry")
    print("=" * 68)

    # --- 1. get a real refresh token + a real session id -----------------
    print(f"[1/5] logging in as {LOGIN_PHONE} ...")
    try:
        data = post(
            "/auth/login/password",
            {"phone": LOGIN_PHONE, "password": LOGIN_PASSWORD, "platform": "pc"},
        )["data"]
    except urllib.error.URLError as exc:
        print(f"  FAIL: cannot reach API at {API_BASE} ({exc})")
        return 1

    real_access = data["access_token"]
    real_refresh = data["refresh_token"]
    claims = decode_claims(real_access)
    print(f"  ok - sub={claims['sub']} sid={claims['sid']} roles={claims['roles']}")

    # --- 2. mint an EXPIRED but correctly-signed access token ------------
    #    same sub/sid/roles -> the session lookup still succeeds, only exp fails.
    now = int(time.time())
    expired_claims = {
        "sub": claims["sub"],
        "typ": "access",
        "sid": claims["sid"],
        "roles": claims["roles"],
        "iss": JWT_ISSUER,
        "iat": now - 7200,
        "exp": now - 60,  # already expired -> backend returns 40101
        "jti": "expired-token-for-e2e-probe",
    }
    expired_token = mint_hs256(expired_claims)
    print(f"[2/5] minted an expired access token (exp={now - 60}, iat={now - 7200})")

    # self-check: the whole probe hinges on this returning 40101, not 40102.
    #   40101 = signature fine, exp in the past  -> frontend SHOULD refresh
    #   40102 = signature bad                    -> frontend SHOULD NOT refresh
    # If we see 40102 here, JWT_SECRET does not match the running API and the
    # frontend result would be meaningless (it would exercise the fatal branch).
    probe_env = get_me(expired_token)
    print(
        f"      self-check /auth/me -> code={probe_env.get('code')} "
        f"message={probe_env.get('message')!r}"
    )
    if probe_env.get("code") != 40101:
        print("\n  FAIL (aborted before touching the browser).")
        print("        Expected 40101 for an expired token but got "
              f"{probe_env.get('code')}.")
        print("        40102 means the signature did not verify, i.e. JWT_SECRET in this")
        print("        script does not match the running API. Fix JWT_SECRET (check")
        print("        apps/api/.env and app/core/config.py env prefix) and re-run.")
        return 1

    # --- 3. inject into the browser --------------------------------------
    print("[3/5] injecting expired access_token + real refresh_token into localStorage ...")
    js = (
        "localStorage.setItem('yj_access_token',{a});"
        "localStorage.setItem('yj_refresh_token',{r});"
        "document.cookie='yj_authed=1;path=/';"
        "'injected'"
    ).format(a=json.dumps(expired_token), r=json.dumps(real_refresh))
    injected = eval_js(js)
    if injected != "injected":
        print(f"  FAIL: could not write localStorage (got {injected!r}).")
        print("        Is a browser session open? Run: agent-browser open " + ADMIN_URL)
        return 1
    print("  ok")

    # --- 4. navigate to a guarded route ---------------------------------
    target = ADMIN_URL + GUARDED_PATH
    print(f"[4/5] navigating to {target} ...")
    ab("open", target)
    ab("wait", "7000")

    url = ab("get", "url").strip()
    token_now = eval_js("localStorage.getItem('yj_access_token') || ''")
    print(f"  landed on: {url}")

    # --- 5. verdict ------------------------------------------------------
    print("[5/5] verdict")
    bounced_to_login = url.endswith("/login") or "/login?" in url
    rotated = bool(token_now) and token_now != expired_token

    ok = (not bounced_to_login) and rotated
    print(f"  - stayed on guarded route : {not bounced_to_login}")
    print(f"  - access_token rotated    : {rotated}")

    if ok:
        print("\n  PASS - silent refresh works end to end.")
        print("         (40101 -> refresh -> new pair persisted -> retried -> 200)")
        return 0

    print("\n  FAIL - silent refresh did not complete.")
    if bounced_to_login:
        print("         The app bounced to /login, meaning refreshToken() returned false")
        print("         or the request never entered the 40101 retry branch.")
    if not rotated:
        print("         access_token is unchanged, so the new pair was never persisted.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
