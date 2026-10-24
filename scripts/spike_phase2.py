"""Phase 2 T1 verification spike (PHASE2.md §3.3). READ-ONLY: only GETs against Graph, only SELECTs on the DB.

Usage (credentials come from the same env vars as the bot; DATABASE_URL is optional but needed for items 1/4):
    python scripts/spike_phase2.py [--team-id <aad_group_id>] [--max-items 200] [--out Bookkeeping/PHASE2_SPIKE.raw.json]

Output is sanitised: ids are shortened to a hash prefix, no names, no message text, no file names, no tokens.
The JSON goes to --out (git-ignored location); paste/summarise into Bookkeeping/PHASE2_SPIKE.md.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import time

import aiohttp
import msal

P = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__"
BASE = os.environ.get("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")


def h(value) -> str | None:
    """Short stable fingerprint so evidence can be correlated without exposing ids."""
    return None if value is None else hashlib.sha256(str(value).encode()).hexdigest()[:8]


class Spike:
    def __init__(self, session: aiohttp.ClientSession, token: str):
        self.s, self.token, self.requests, self.t0 = session, token, 0, time.monotonic()

    async def get(self, path: str, **params) -> tuple[int, dict]:
        """GET; never raises on HTTP errors. Returns (status, body-or-{'error':{code,message-class}})."""
        self.requests += 1
        url = path if path.startswith("http") else BASE + path
        async with self.s.get(url, params=params or None, headers={"Authorization": f"Bearer {self.token}"}) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:
                body = {}
            if r.status >= 400:
                err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
                body = {"error": {"code": err.get("code"), "message": (err.get("message") or "")[:200]}}
            return r.status, body

    async def pages(self, path: str, limit: int, **params):
        """Yield items across @odata.nextLink up to `limit`; returns on first error (status recorded by caller)."""
        n, url, first = 0, path, True
        while url and n < limit:
            st, body = await self.get(url, **(params if first else {}))
            first = False
            if st >= 400:
                yield {"__error__": st, **body.get("error", {})}
                return
            for it in body.get("value", []):
                n += 1
                yield it
                if n >= limit:
                    return
            url = body.get("@odata.nextLink")


def token_roles(token: str) -> list[str]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return sorted(json.loads(base64.urlsafe_b64decode(payload)).get("roles", []))


async def db_rows(sql: str, *args):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    import asyncpg

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        return [dict(r) for r in await conn.fetch(sql, *args)]
    finally:
        await conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-id", help="aad_group_id; default: all installed teams from the DB")
    ap.add_argument("--max-items", type=int, default=200, help="cap per listing (keeps the spike light)")
    ap.add_argument("--out", default="Bookkeeping/PHASE2_SPIKE.raw.json")
    args = ap.parse_args()

    cid, secret, tenant = (os.environ.get(P + k) for k in ("CLIENTID", "CLIENTSECRET", "TENANTID"))
    if not (cid and secret and tenant):
        raise SystemExit(f"{P}CLIENTID/CLIENTSECRET/TENANTID must be set")
    app = msal.ConfidentialClientApplication(cid, secret, authority=f"https://login.microsoftonline.com/{tenant}")
    tok = await asyncio.to_thread(app.acquire_token_for_client, ["https://graph.microsoft.com/.default"])
    if "access_token" not in tok:
        raise SystemExit(f"token request failed: {tok.get('error')} {tok.get('error_description', '')[:200]}")

    out: dict = {"graph_base": BASE, "token_roles": token_roles(tok["access_token"]), "teams": []}
    teams = ([{"id": None, "aad_group_id": args.team_id}] if args.team_id else
             await db_rows("select id, aad_group_id from teams where removed_at is null"))
    if teams is None:
        raise SystemExit("Pass --team-id or set DATABASE_URL")
    out["teams_in_db"] = len(teams)

    live = await db_rows("select conversation_id, message_id from messages") if os.environ.get("DATABASE_URL") else None
    live_keys = {(r["conversation_id"].split(";messageid=")[0], r["message_id"]) for r in live or []}
    live_ids = {r["message_id"] for r in live or []}

    async with aiohttp.ClientSession() as session:
        sp = Spike(session, tok["access_token"])
        for t in teams:
            gid = t["aad_group_id"]
            tr: dict = {"team": h(gid), "bot_team_id": h(t["id"]), "channels": []}
            out["teams"].append(tr)
            if not gid:
                tr["error"] = "no aad_group_id in DB (spec §3.1: skip)"
                continue
            chans = [c async for c in sp.pages(f"/teams/{gid}/channels", args.max_items)]
            if chans and "__error__" in chans[0]:
                tr["channels_error"] = chans[0]
                continue
            for c in chans:
                cr: dict = {"channel": h(c["id"]), "membership_type": c.get("membershipType"),
                            "channel_id_is_19_thread": c["id"].startswith("19:") and c["id"].endswith("@thread.tacv2")}
                tr["channels"].append(cr)
                base = f"/teams/{gid}/channels/{c['id']}"

                # --- 1/6/7: filesFolder + children (works for standard? private? shared?)
                st, ff = await sp.get(base + "/filesFolder")
                cr["filesFolder"] = {"status": st, **({"error": ff["error"]} if st >= 400 else {})}
                if st < 400:
                    drive, folder = ff["parentReference"]["driveId"], ff["id"]
                    cr["filesFolder"].update(drive=h(drive), item=h(folder), has_path=bool(ff["parentReference"].get("path")))
                    t1, n_items, n_folders = time.monotonic(), 0, 0
                    req0 = sp.requests
                    queue = [folder]
                    while queue and n_items < args.max_items:
                        cur = queue.pop(0)
                        async for it in sp.pages(f"/drives/{drive}/items/{cur}/children", args.max_items):
                            if "__error__" in it:
                                cr["children_error"] = it
                                break
                            n_items += 1
                            if "folder" in it:
                                n_folders += 1
                                queue.append(it["id"])
                    cr["children"] = {"items": n_items, "folders": n_folders, "requests": sp.requests - req0,
                                      "seconds": round(time.monotonic() - t1, 2),
                                      "capped": n_items >= args.max_items}

                    # --- 2: folder-scoped delta vs root delta (token=latest: cheap, no enumeration)
                    st, body = await sp.get(f"/drives/{drive}/items/{folder}/delta", token="latest")
                    cr["folder_delta"] = {"status": st, "has_deltaLink": "@odata.deltaLink" in body,
                                          **({"error": body["error"]} if st >= 400 else {})}
                    st, body = await sp.get(f"/drives/{drive}/root/delta", token="latest")
                    cr["root_delta"] = {"status": st, "has_deltaLink": "@odata.deltaLink" in body,
                                        **({"error": body["error"]} if st >= 400 else {})}

                # --- 3/4/5: messages + replies under the RSC-only grant
                msgs = [m async for m in sp.pages(base + "/messages", min(args.max_items, 50), **{"$top": 50})]
                if msgs and "__error__" in msgs[0]:
                    cr["messages"] = {"status": msgs[0]["__error__"], "code": msgs[0].get("code"),
                                      "message": msgs[0].get("message")}
                    continue
                cid_clean = c["id"]
                shapes: set[str] = set()
                matched_id = matched_key = 0
                for m in msgs:
                    matched_id += m["id"] in live_ids
                    matched_key += (cid_clean, m["id"]) in live_keys
                    for a in m.get("attachments", []):
                        shapes.add(json.dumps(sorted(a.keys())) + " contentType=" + str(a.get("contentType")))
                cr["messages"] = {"status": 200, "count": len(msgs), "with_subject": sum(bool(m.get("subject")) for m in msgs),
                                  "deleted": sum(bool(m.get("deletedDateTime")) for m in msgs),
                                  "id_in_live_messages": matched_id, "channel+id_in_live_messages": matched_key,
                                  "live_rows_loaded": live is not None, "attachment_shapes": sorted(shapes),
                                  "sample_id_format": (msgs[0]["id"][:3] + "…" + str(len(msgs[0]["id"]))) if msgs else None}
                if msgs:
                    rep = [r async for r in sp.pages(f"{base}/messages/{msgs[0]['id']}/replies", 20)]
                    cr["replies"] = ({"status": rep[0]["__error__"], "code": rep[0].get("code")}
                                     if rep and "__error__" in rep[0] else {"status": 200, "first_message_replies": len(rep)})
        out["total_requests"] = sp.requests
        out["total_seconds"] = round(time.monotonic() - sp.t0, 2)

    out["sites_selected_procedure"] = (
        "Not testable without the grant. Procedure: (1) in Entra, give the app the Graph application permission "
        "Sites.Selected (admin consent) instead of Files.Read.All; (2) POST /sites/{siteId}/permissions with "
        "{roles:['read'], grantedToIdentities:[{application:{id:<appId>,displayName:'Skim'}}]} using a token that has "
        "Sites.FullControl.All (or via Graph Explorer as a SharePoint admin) for each team's SharePoint site "
        "(GET /groups/{gid}/sites/root gives the site id); (3) re-run this spike. Expected: /filesFolder and /children succeed "
        "for granted sites, 403 for others."
    )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
