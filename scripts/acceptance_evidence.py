"""Read-only evidence for the Phase 4 live acceptance run (PHASE4.md section 9). Never writes anything.

    set -a; . ./.env; set +a                 # DATABASE_URL, PAIRING_SECRET, STAFF_AAD_IDS
    python scripts/acceptance_evidence.py                  # all sections, for the only app user
    python scripts/acceptance_evidence.py A9 A10           # just those sections
    python scripts/acceptance_evidence.py --email you@x.com
    python scripts/acceptance_evidence.py A15 --user-id <uuid>   # after deleting the account (the user row is gone: pass the id)

Prints facts and a PASS/CHECK/FAIL for the parts a database can judge; the parts only a phone can judge (what the screen looks like) are
marked "ON THE PHONE". Times are India time. Nothing here prints a token or a message body.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "src")]

import asyncpg  # noqa: E402

IST_TS = "to_char({c} at time zone 'Asia/Kolkata', 'Dy DD Mon HH24:MI')"
UPCOMING = ("e.status not in ('cancelled','done') and coalesce(e.ends_at, e.starts_at + interval '1 hour', "
            "((e.event_date + 1)::timestamp at time zone 'Asia/Kolkata')) > now()")
TABLES = ["subscriptions", "join_attempts", "user_item_state", "stars", "reminders", "devices", "push_outbox"]
verdicts: list[tuple[str, str, str]] = []


def say(section: str, verdict: str, text: str) -> None:
    verdicts.append((section, verdict, text))
    print(f"  {verdict:<6} {text}")


def head(title: str) -> None:
    print(f"\n== {title}")


def table(rows, cols: list[str], limit: int = 25) -> None:
    rows = list(rows)
    if not rows:
        print("  (none)")
        return
    widths = [max(len(c), *(len(str(r[i])) for r in rows[:limit])) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows[:limit]:
        print("  " + "  ".join(str(r[i]).ljust(w) for i, w in enumerate(widths)))
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sections", nargs="*", help="e.g. A3 A9 (default: all)")
    ap.add_argument("--email")
    ap.add_argument("--user-id")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    want = {s.upper() for s in a.sections} or None
    on = lambda s: want is None or s in want  # noqa: E731
    c = await asyncpg.connect(dsn, statement_cache_size=0)
    async with c.transaction(readonly=True):
        now = await c.fetchval("select now()")
        print(f"now: {await c.fetchval(IST_TS.format(c='now()').join(['select ', '']))} IST")
        uid = a.user_id
        if not uid:
            rows = await c.fetch("select id, email from app_users where ($1::text is null or email = $1) order by created_at", a.email)
            if len(rows) != 1 and not (want and want <= {"A1", "A14"}):
                print(f"expected exactly one app user, found {len(rows)}; use --email or --user-id", file=sys.stderr)
                if want is None or "A15" not in want:
                    return 2
            uid = str(rows[0]["id"]) if rows else None
        print(f"user: {uid}")

        if on("A1"):
            head("A1  the database is locked")
            bad = await c.fetch("select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind in ('r','p') and not c.relrowsecurity")
            say("A1", "PASS" if not bad else "FAIL", f"{len(bad)} public table(s) without row level security")
            print("  ON THE PHONE/PC: curl the publishable key against /rest/v1/messages -> no rows (done at T0: HTTP 503)")

        if on("A3") and uid:
            head("A3  joining")
            r = await c.fetch("select ok, count(*) n, min(at) first, max(at) last from join_attempts where user_id=$1 group by ok order by ok", uid)
            table([(x["ok"], x["n"], x["first"].strftime("%H:%M:%S"), x["last"].strftime("%H:%M:%S")) for x in r], ["ok", "attempts", "first", "last"])
            n_hour = await c.fetchval("select count(*) from join_attempts where user_id=$1 and at > now() - interval '1 hour'", uid)
            say("A3", "CHECK", f"{n_hour} attempts in the last hour (the 11th inside an hour must have answered 429: ON THE PHONE)")
            sub = await c.fetch("select s.team_id, t.name, to_char(s.joined_at at time zone 'Asia/Kolkata','Dy DD Mon HH24:MI') joined, s.left_at is not null left_, s.muted from subscriptions s join teams t on t.id=s.team_id where s.user_id=$1", uid)
            table([(x["name"], x["joined"], "left" if x["left_"] else "active", "muted" if x["muted"] else "") for x in sub], ["team", "joined", "state", ""])

        if on("A4") and uid:
            head("A4  the first deck = last 7 days + upcoming-event items, newest first")
            rows = await c.fetch(f"""
                select f.id, f.kind, {IST_TS.format(c='f.occurred_at')} at, f.occurred_at,
                       coalesce(e.title, left(m.text, 40), d.name, '?') what, (f.occurred_at >= s.joined_at - interval '7 days') recent,
                       exists (select 1 from feed_item_events fe join events e2 on e2.id = fe.event_id where fe.item_id = f.id and {UPCOMING.replace('e.', 'e2.')}) upcoming
                from subscriptions s join feed_items f on f.team_id = s.team_id and not f.hidden
                left join feed_item_events fe on fe.item_id = f.id left join events e on e.id = fe.event_id
                left join messages m on m.conversation_id = f.conversation_id and m.message_id = f.message_id
                left join document_events de on de.id = f.document_event_id left join documents d on d.id = de.document_id
                where s.user_id = $1 and s.left_at is null order by f.occurred_at desc, f.id desc""", uid)
            seen, expected = set(), []
            for x in rows:
                if x["id"] in seen:
                    continue
                seen.add(x["id"])
                if x["recent"] or x["upcoming"]:
                    expected.append(x)
            table([(x["id"], x["kind"], x["at"], "7d" if x["recent"] else "", "upcoming" if x["upcoming"] else "", str(x["what"]).replace("\n", " ")[:38]) for x in expected], ["card", "kind", "when", "window", "event", "about"], 40)
            say("A4", "CHECK", f"a freshly joined deck must hold exactly these {len(expected)} cards, newest first (leave the team and join again; ON THE PHONE: compare)")

        if on("A5") and uid:
            head("A5  what becomes a card")
            staff = {x.strip() for x in (os.environ.get("STAFF_AAD_IDS") or "").split(",") if x.strip()}
            inst = {r["installed_by_aad"] for r in await c.fetch("select installed_by_aad from teams where installed_by_aad is not null")}
            ids = list(staff | inst)
            q = await c.fetch("""
                select m.message_id, m.from_name, a.category, (f.id is not null) has_card, left(coalesce(m.text,''),30) txt
                from messages m join conversations cv on cv.id = m.conversation_id and cv.conversation_type='channel'
                left join lateral (select category from message_analysis x where x.conversation_id=m.conversation_id and x.message_id=m.message_id and x.status='ok' order by processed_at desc limit 1) a on true
                left join feed_items f on f.kind='message' and f.conversation_id=m.conversation_id and f.message_id=m.message_id
                where m.deleted_at is null and m.from_aad_id is not null and not (m.from_aad_id = any($1::text[]))""", ids)
            table([(x["message_id"], x["from_name"], x["category"], "CARD" if x["has_card"] else "no card", x["txt"]) for x in q], ["message", "author", "category", "card?", "text"])
            wrong = [x for x in q if x["has_card"] != (x["category"] in ("event", "change", "announcement", "resource"))]
            say("A5", "PASS" if not wrong else "FAIL", f"{len(q)} non-staff message(s); {len(wrong)} with a card that disagrees with the rule (student chitchat/questions never; event/change/announcement/resource yes)")
            miss = await c.fetchval("""select count(*) from messages m join conversations cv on cv.id=m.conversation_id and cv.conversation_type='channel' join teams t on t.id=cv.team_id and t.removed_at is null
                where m.deleted_at is null and m.from_aad_id = any($1::text[]) and (coalesce(m.text,'')<>'' or exists (select 1 from attachments x where x.conversation_id=m.conversation_id and x.message_id=m.message_id))
                and not exists (select 1 from feed_items f where f.kind='message' and f.conversation_id=m.conversation_id and f.message_id=m.message_id)""", ids)
            say("A5", "PASS" if miss == 0 else "FAIL", f"{miss} staff message(s) without a card (every staff post must have one; feed-build runs every 15 minutes)")
            fl = await c.fetch("""
                select de.event, d.name, f.id card, f.hidden,
                       exists (select 1 from attachment_document_links adl join attachments a on a.id=adl.attachment_id where adl.document_id=d.id) posted_with_message
                from document_events de join documents d on d.id=de.document_id left join feed_items f on f.kind='document' and f.document_event_id=de.id
                where not d.is_folder order by de.at desc, de.id desc limit 14""")
            table([(x["event"], x["name"][:30], x["card"] or "-", "hidden" if x["hidden"] else "", "with a message" if x["posted_with_message"] else "Files tab") for x in fl], ["file event", "file", "card", "", "came from"])
            dup = await c.fetchval("""select count(*) from feed_items f join document_events de on de.id=f.document_event_id and de.event='created'
                where f.kind='document' and not f.hidden and exists (select 1 from attachment_document_links adl join attachments a on a.id=adl.attachment_id
                join feed_items mf on mf.kind='message' and mf.conversation_id=a.conversation_id and mf.message_id=a.message_id and not mf.hidden where adl.document_id=de.document_id)""")
            say("A5", "PASS" if dup == 0 else "FAIL", f"{dup} file(s) posted with a message that ALSO show their own visible card (must be 0: shown as a chip on the message card)")

        if on("A6") and uid:
            head("A6  swipes")
            r = await c.fetch("select coalesce(state,'(snoozed)') s, count(*) n, max(to_char(acted_at at time zone 'Asia/Kolkata','HH24:MI:SS')) latest from user_item_state where user_id=$1 group by state order by 1", uid)
            table([(x["s"], x["n"], x["latest"]) for x in r], ["state", "cards", "latest"])
            say("A6", "CHECK", "after a left swipe a 'dismissed' row appears; Undo within 5 s removes it; 'Put back' from History turns it into a snooze that ends now")

        if on("A8") and uid:
            head("A8  reminders (what GET /reminders would give right now)")
            from skim.api import user_settings
            from skim.reminders import compute
            from skim.storage.postgres import PostgresStorage
            st = PostgresStorage(dsn)
            await st.start()
            try:
                u = await st.get_app_user(uid)
                settings = user_settings.effective(u["settings"] if u else None)
                events = await st.list_events_for_user(uid, datetime.now(timezone.utc).date())
                rows = await st.list_reminders(uid, include_deleted=True)
                newest = await st.latest_items_for_events(uid, list(events)) if events else {}
                secret = os.environ.get("PAIRING_SECRET") or ""
                sched = compute(uid, rows, events, settings, datetime.now(timezone.utc), item_for_event=newest.get, days=30, secret=secret) if secret else []
            finally:
                await st.close()
            if not os.environ.get("PAIRING_SECRET"):
                say("A8", "CHECK", "PAIRING_SECRET is not set here, so automatic reminders cannot be listed (stored ones still show below)")
            ist = lambda d: d.astimezone(timezone.utc).strftime("%a %d %b %H:%M")  # noqa: E731  (UTC; add 5:30 for India)
            table([(s.kind, "auto" if s.virtual else "stored", f"{s.fire_at.astimezone(timezone.utc) + (datetime(2000,1,1,5,30) - datetime(2000,1,1)):%a %d %b %H:%M} IST", events.get(s.event_id, {}).get("title", "-")) for s in sched], ["kind", "source", "fires", "event"])
            stored = await c.fetch("select kind, offset_minutes, deleted_at is not null deleted from reminders where user_id=$1 order by created_at", uid)
            say("A8", "CHECK", f"{len(sched)} scheduled in the next 30 days; {len(stored)} stored row(s). Quiz 1 (Mon 2 Nov 10:00 IST) must show Sun 1 Nov 20:00 and Mon 2 Nov 07:00 IST. ON THE PHONE: a 2-minute test reminder fires")

        if on("A9") and uid:
            head("A9  a changed date reaches the student")
            ch = await c.fetch("""select ec.id, ec.change_type, e.title, to_char(ec.at at time zone 'Asia/Kolkata','DD Mon HH24:MI') at, ec.old_values::text o, ec.new_values::text n,
                                     f.id card, (select count(*) from user_item_state s where s.folded_into = f.id and s.user_id=$1) folded_swiped
                              from event_changes ec join events e on e.id=ec.event_id left join feed_items f on f.kind='change' and f.event_change_id=ec.id
                              where ec.change_type not in ('created','restated','needs_review') order by ec.id desc limit 6""", uid)
            table([(x["id"], x["change_type"], x["title"], x["at"], x["card"] or "NO CARD", (x["o"] or "")[:30], (x["n"] or "")[:30]) for x in ch], ["change", "type", "event", "posted", "card", "before", "after"])
            po = await c.fetch("select p.id, p.event_change_id ec, p.status, p.receipt_status, to_char(p.not_before at time zone 'Asia/Kolkata','DD Mon HH24:MI') not_before, to_char(p.sent_at at time zone 'Asia/Kolkata','DD Mon HH24:MI') sent, left(p.title,40) title from push_outbox p where p.user_id=$1 order by p.id desc limit 6", uid)
            table([(x["id"], x["ec"], x["status"], x["receipt_status"] or "-", x["not_before"], x["sent"] or "-", x["title"]) for x in po], ["push", "change", "status", "receipt", "held until", "sent", "title"])
            lagging = [x for x in ch if not x["card"]]
            say("A9", "PASS" if not lagging else "CHECK", f"{len(lagging)} change(s) still without a card (feed-build runs inside run-all, every 15 minutes: wait one run)")
            say("A9", "CHECK", "ON THE PHONE: a change card (folding the unswiped originals), the event's reminders at the NEW times, and a push (event starred / reminded / ticked type)")

        if on("A10") and uid:
            head("A10  quiet hours, mute, urgent-only")
            st = await c.fetchrow("select settings->'quiet_hours' q, settings->'urgent_only' u, settings->'important_types' t from app_users where id=$1", uid)
            print(f"  settings: quiet_hours={st['q']} urgent_only={st['u']} important_types={st['t']}")
            mu = await c.fetch("select t.name, s.muted from subscriptions s join teams t on t.id=s.team_id where s.user_id=$1 and s.left_at is null", uid)
            print("  muted:", [(x["name"], x["muted"]) for x in mu])
            q = await c.fetch("select p.id, p.status, to_char(p.created_at at time zone 'Asia/Kolkata','HH24:MI') queued, to_char(p.not_before at time zone 'Asia/Kolkata','DD Mon HH24:MI') held_until, to_char(p.sent_at at time zone 'Asia/Kolkata','HH24:MI') sent from push_outbox p where p.user_id=$1 order by p.id desc limit 6", uid)
            table([(x["id"], x["status"], x["queued"], x["held_until"], x["sent"] or "-") for x in q], ["push", "status", "queued (IST)", "held until (IST)", "sent (IST)"])
            say("A10", "CHECK", "quiet hours: a push queued inside the window has held_until = the window's end and is sent only then; muted team or urgent-only with the type unticked: NO push row for that change, but the change card still exists (A9 table)")

        if on("A11") and uid:
            head("A11  temporary stars and snoozes")
            s = await c.fetch("select s.item_id, to_char(s.expires_at at time zone 'Asia/Kolkata','DD Mon HH24:MI') expires, s.until_event_over, s.expires_at < now() expired from stars s where s.user_id=$1 order by s.created_at desc", uid)
            table([(x["item_id"], x["expires"] or "never", "until over" if x["until_event_over"] else "", "EXPIRED" if x["expired"] else "") for x in s], ["card", "expires", "", ""])
            sn = await c.fetch("select item_id, to_char(snoozed_until at time zone 'Asia/Kolkata','DD Mon HH24:MI') until, snoozed_until < now() over_ from user_item_state where user_id=$1 and snoozed_until is not null order by snoozed_until desc limit 8", uid)
            table([(x["item_id"], x["until"], "ended" if x["over_"] else "waiting") for x in sn], ["card", "until", ""])
            say("A11", "CHECK", "ON THE PHONE: an expired star is gone from Starred; a snooze that has ended is back at the top of the deck (to fast-forward, see PHASE4_ACCEPTANCE.md)")

        if on("A14"):
            head("A14  the bot is removed from a team")
            tm = await c.fetch("select name, installed_at is not null installed, removed_at is not null removed, to_char(removed_at at time zone 'Asia/Kolkata','DD Mon HH24:MI') at from teams")
            table([(x["name"], "REMOVED " + x["at"] if x["removed"] else "live") for x in tm], ["team", "state"])
            late = await c.fetchval("select count(*) from feed_items f join teams t on t.id=f.team_id where t.removed_at is not null and f.created_at > t.removed_at")
            say("A14", "PASS" if late == 0 else "FAIL", f"{late} card(s) created after a team's bot was removed (must be 0)")
            if uid:
                keep = await c.fetchrow("select (select count(*) from user_item_state where user_id=$1) st, (select count(*) from stars where user_id=$1) sr", uid)
                print(f"  kept for the student: {keep['st']} swiped card(s), {keep['sr']} star(s)")
            say("A14", "CHECK", "ON THE PHONE: the team shows 'Ended', gets no new cards, History and Starred still list its cards; future reminders still fire")

        if on("A15"):
            head("A15  account deletion")
            if not uid:
                print("  no user id given and no app user found")
            else:
                left = {"app_users": await c.fetchval("select count(*) from app_users where id=$1", uid)}
                for t in TABLES:
                    left[t] = await c.fetchval(f"select count(*) from {t} where user_id=$1", uid)
                has_auth = await c.fetchval("select to_regclass('auth.users') is not null")
                left["auth.users"] = await c.fetchval("select count(*) from auth.users where id=$1", uid) if has_auth else 0
                table([(k, v) for k, v in left.items()], ["table", "rows for this user"])
                total = sum(left.values())
                say("A15", "PASS" if total == 0 else ("CHECK" if left["app_users"] else "FAIL"), "every row for the user and the auth user are gone" if total == 0 else f"{total} row(s) remain" + (" (account not deleted yet)" if left["app_users"] else ""))
                say("A15", "CHECK", "ON THE PHONE: signing in again with Google starts fresh (join screen, empty history)")
    await c.close()
    fails = [v for v in verdicts if v[1] == "FAIL"]
    print(f"\nsummary: {sum(v[1] == 'PASS' for v in verdicts)} PASS, {sum(v[1] == 'CHECK' for v in verdicts)} CHECK (needs the phone), {len(fails)} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
