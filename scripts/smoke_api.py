"""Smoke-test the deployed student API (/api/v1) from your PC. Read-only unless you pass --join.

    python scripts/smoke_api.py --base https://<service-url>                 # public checks only (health, 401s)
    python scripts/smoke_api.py --base https://<service-url> --token-prompt  # + signed-in checks; you paste a Supabase access token

The token is typed/pasted at a hidden prompt (or taken from the SKIM_TOKEN environment variable), used for this run only, never
written to disk and never printed. Get one from the app once T8 exists (the app's session access_token), or for an early check
create a test user in Supabase (Authentication > Users) and POST {email,password} to
<SUPABASE_URL>/auth/v1/token?grant_type=password with your publishable key as the `apikey` header; copy `access_token`.
Exit code 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request

FAILED = 0


def call(base: str, method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(base.rstrip("/") + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, None


def check(ok: bool, what: str, detail: str = "") -> bool:
    global FAILED
    print(("PASS  " if ok else "FAIL  ") + what + (f"   [{detail}]" if detail and not ok else ""))
    FAILED += 0 if ok else 1
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BASE_URL"), help="https://<cloud run service url>")
    ap.add_argument("--token-prompt", action="store_true", help="ask for a Supabase access token (hidden) and run the signed-in checks")
    ap.add_argument("--join", metavar="CODE", help="also join the team with this pairing code (the only write)")
    a = ap.parse_args()
    if not a.base:
        print("give --base https://<service-url> (or set BASE_URL)", file=sys.stderr)
        return 2
    base = a.base

    st, h = call(base, "GET", "/health")
    check(st == 200 and h and h.get("ok") and h.get("db") is True, "GET /health ok and db true", str(st))
    check(h is not None and h.get("db_schema") == "ok", "database schema is up to date (db_schema=ok)", str(h))
    check(call(base, "GET", "/api/v1/me")[0] == 401, "no token -> 401")
    check(call(base, "GET", "/api/v1/me", "not-a-real-token")[0] == 401, "garbage token -> 401")
    check(call(base, "GET", "/api/v1/deck")[0] == 401, "deck without token -> 401")
    check(call(base, "GET", "/api/v1/nothing-here")[0] == 401, "unknown api path without a token -> 401 (no route enumeration)")

    token = os.environ.get("SKIM_TOKEN") or (getpass.getpass("Supabase access token (hidden, not stored): ").strip() if a.token_prompt else None)
    if not token:
        print("\n(no token given: signed-in checks skipped)")
        return 1 if FAILED else 0

    st, me = call(base, "GET", "/api/v1/me", token)
    if not check(st == 200 and me and me.get("id") and me.get("settings"), "GET /me -> profile with settings", str(st)):
        print("\nThe token was rejected or the API is off. Check API_ENABLED / SUPABASE_URL / SUPABASE_JWT_SECRET on Cloud Run.")
        return 1
    print(f"      signed in as {me.get('email')}")
    check(me["settings"]["timezone"] == "Asia/Kolkata" and me["settings"]["important_types"], "settings have the defaults")
    if a.join:
        st, j = call(base, "POST", "/api/v1/subscriptions", token, {"code": a.join})
        check(st in (201, 409) and j and j.get("team"), f"join with the pairing code -> {st}", str(j))
    st, subs = call(base, "GET", "/api/v1/subscriptions", token)
    check(st == 200 and isinstance(subs.get("teams"), list), "GET /subscriptions", str(st))
    print(f"      teams: {[(t['name'], 'ended' if t['ended'] else 'live') for t in subs.get('teams', [])]}")
    st, deck = call(base, "GET", "/api/v1/deck?limit=5", token)
    check(st == 200 and "cards" in deck, "GET /deck", str(st))
    if deck and deck.get("cards"):
        c = deck["cards"][0]
        check({"id", "kind", "team", "occurred_at", "events", "links", "user"} <= set(c), "a card has the documented fields")
        print(f"      deck: {deck['total']} cards; first is a {c['kind']} card from {c['team']['name']}")
        st, d = call(base, "GET", f"/api/v1/items/{c['id']}", token)
        check(st == 200 and "recommended_reminders" in d and "event_history" in d, "GET /items/{id} detail", str(st))
    for path in ("/api/v1/history", "/api/v1/starred"):
        st, b = call(base, "GET", path, token)
        check(st == 200 and "cards" in b, f"GET {path}", str(st))
    st, rem = call(base, "GET", "/api/v1/reminders", token)
    check(st == 200 and isinstance(rem.get("reminders"), list) and rem.get("etag"), "GET /reminders", str(st))
    print(f"      reminders scheduled: {len(rem.get('reminders', []))}; next: {(rem.get('reminders') or [{}])[0].get('fire_at')}")
    st, sync = call(base, "GET", "/api/v1/sync", token)
    check(st == 200 and sync.get("reminders_etag") == rem.get("etag"), "GET /sync etag matches /reminders", str(st))
    check(call(base, "GET", "/api/v1/items/999999999", token)[0] == 404, "an item you cannot see -> 404")
    check(call(base, "POST", "/api/v1/devices", token, {"expo_push_token": "nope", "platform": "android"})[0] == 400, "a bad push token -> 400")
    print("\nall checks passed" if not FAILED else f"\n{FAILED} check(s) FAILED")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
