"""Read-only smoke test for a deployed LTMS. Safe to run against production:
it creates no members and writes no records.

    python -m scripts.smoke_test https://ltms.example.org --admin-user admin
    (prompts for the admin password; or set LTMS_SMOKE_PASSWORD)

Exit code 0 = everything passed, 1 = at least one check failed.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from urllib.parse import urlparse

import httpx

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}{f'  ({detail})' if detail and not ok else ''}")
    return ok


def warn(label: str) -> None:
    print(f"WARN  {label}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="base URL, e.g. https://ltms.example.org")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--no-admin", action="store_true", help="skip the admin login checks")
    args = ap.parse_args(argv)
    base = args.url.rstrip("/")
    https = urlparse(base).scheme == "https"
    c = httpx.Client(base_url=base, timeout=15, follow_redirects=False)

    # 1. Liveness
    r = c.get("/api/health")
    check(r.status_code == 200 and r.json().get("ok") is True, "health endpoint answers", f"{r.status_code}")

    # 2. Transport security
    if https:
        plain = "http://" + base.split("://", 1)[1]
        try:
            r = httpx.get(plain + "/api/health", timeout=10, follow_redirects=False)
            check(r.status_code in (301, 302, 307, 308) and r.headers.get("location", "").startswith("https://"),
                  "plain HTTP redirects to HTTPS", f"{r.status_code}")
        except httpx.HTTPError as exc:
            warn(f"could not reach plain HTTP to check the redirect: {exc}")
        r = c.get("/api/health")
        if "strict-transport-security" in r.headers:
            check(True, "HSTS header present")
        else:
            warn("no Strict-Transport-Security header (add it in the nginx directives)")
    else:
        warn("URL is not HTTPS; skipping TLS checks (fine for a LAN/dev server only)")

    r = c.get("/api/health")
    check(r.headers.get("x-frame-options") == "DENY", "X-Frame-Options: DENY")
    check(r.headers.get("cache-control") == "no-store", "API responses are not cached")

    # 3. Nothing readable without credentials
    check(c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404,
          "no public API docs")
    r = c.get("/api/records")
    check(r.status_code == 401, "records require a member token", f"{r.status_code}")
    r = c.get("/api/records", headers={"Authorization": "Bearer not-a-real-token"})
    check(r.status_code == 401, "a made-up token is refused", f"{r.status_code}")
    r = c.post("/api/records", json={"records": [{}]})
    check(r.status_code == 401, "writing requires a member token", f"{r.status_code}")
    r = c.get("/api/admin/members")
    check(r.status_code == 401, "admin API requires login", f"{r.status_code}")
    r = c.post("/api/auth/challenge", json={"name": "definitely-not-enrolled"})
    check(r.status_code == 403, "unenrolled member can't start a login", f"{r.status_code}")

    # 4. Dashboard
    r = c.get("/")
    if r.status_code == 200 and "<div id=\"root\">" in r.text:
        check(True, "dashboard is served")
    else:
        warn(f"dashboard not served at / ({r.status_code}); fine if you host it elsewhere")

    # 5. Admin session
    if not args.no_admin:
        password = os.environ.get("LTMS_SMOKE_PASSWORD") or getpass.getpass(f"Admin password for {args.admin_user}: ")
        r = c.post("/api/auth/admin/login", json={"username": args.admin_user, "password": password})
        if check(r.status_code == 200, "admin login", f"{r.status_code} {r.text[:120]}"):
            cookie = r.headers.get("set-cookie", "").lower()
            check("httponly" in cookie and "samesite=strict" in cookie, "admin cookie is HttpOnly + SameSite=Strict")
            if https:
                check("secure" in cookie, "admin cookie is Secure")
            check(c.get("/api/admin/me").status_code == 200, "admin session works")
            r = c.post("/api/admin/members/00000000-0000-0000-0000-000000000000/approve", json={})
            check(r.status_code == 403, "admin writes need the CSRF header", f"{r.status_code}")
            rep = c.get("/api/admin/integrity").json().get("members", [])
            broken = [m["member"] for m in rep if m["status"] == "broken"]
            check(not broken, f"integrity: {len(rep)} chain(s) checked", f"broken: {broken}")
            c.post("/api/auth/admin/logout", headers={"X-LTMS-Admin": "1"})

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
