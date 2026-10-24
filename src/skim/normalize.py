"""Pure functions: Teams activity dict -> rows. No I/O, no SDK imports."""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_AT_TAG = re.compile(r"</?at[^>]*>", re.IGNORECASE)
_THREAD_SUFFIX = re.compile(r";messageid=(\d+)")


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, list) else []


def canonical_json(activity: dict) -> str:
    return json.dumps(activity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dedupe_key(activity: dict) -> str:
    """D-010: SHA-256 of canonical JSON."""
    return hashlib.sha256(canonical_json(activity).encode()).hexdigest()


def parse_ts(value: Any, default: datetime | None = None) -> datetime:
    """Parse an ISO timestamp to aware UTC (D-013); fall back to `default`/now."""
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return default or datetime.now(timezone.utc)


def base_conversation_id(conversation_id: str | None) -> str | None:
    """Strip the ';messageid=N' thread suffix so a thread shares the channel's id."""
    if not conversation_id:
        return conversation_id
    return _THREAD_SUFFIX.sub("", conversation_id)


def event_type(activity: dict) -> str | None:
    return _d(activity.get("channelData")).get("eventType")


NON_STANDARD_CHANNEL_TYPES = {"private", "shared"}


def is_non_standard_channel(activity: dict) -> bool:
    """True if the activity says it comes from a private or shared channel (P2-D-116: such channels are out of scope).

    Reads channelData.channel.type / .membershipType. Teams is not known to always send them (P2-DIFF-004),
    so an activity without the field is treated as standard."""
    channel = _d(_d(activity.get("channelData")).get("channel"))
    for key in ("type", "membershipType"):
        v = channel.get(key)
        if isinstance(v, str) and v.strip().lower() in NON_STANDARD_CHANNEL_TYPES:
            return True
    return False


def raw_fields(activity: dict) -> dict:
    cd = _d(activity.get("channelData"))
    return {
        "dedupe_key": dedupe_key(activity),
        "activity_type": str(activity.get("type") or "unknown"),
        "activity_id": activity.get("id"),
        "event_type": cd.get("eventType"),
        "conversation_id": _d(activity.get("conversation")).get("id"),
        "tenant_id": _d(cd.get("tenant")).get("id") or _d(activity.get("conversation")).get("tenantId"),
        "payload": activity,
    }


def strip_mentions(text: str | None) -> str | None:
    if text is None:
        return None
    return to_plain_text(_AT_TAG.sub("", text).strip())


_AT_ELEMENT = re.compile(r"<at\b[^>]*>.*?</at>", re.I | re.S)
_ANY_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_ENTITY = re.compile(r"&(?:[a-zA-Z]{2,8}|#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6});")
_LINE_BREAK = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h[1-6]|/blockquote|/ul|/ol|/table)\b[^>]*>", re.I)
_BLANKS = re.compile(r"\n{3,}")


def has_markup(text: str | None) -> bool:
    """True if the text carries HTML tags or entities (Teams sends HTML in `text` for rich posts)."""
    return bool(text) and bool(_ANY_TAG.search(text) or _ENTITY.search(text))


def to_plain_text(text: str | None, drop_mentions: bool = False) -> str | None:
    """P2-D-144: HTML -> plain text. <br> and closing block tags become newlines, other tags are removed, entities are
    unescaped, runs of blank lines collapse to one blank line. Text without any tag or entity is returned as is,
    so plain text that Teams sent as text is never altered. `drop_mentions` also removes the mention's name."""
    if text is None or not has_markup(text):
        return text
    s = _AT_ELEMENT.sub("", text) if drop_mentions else text
    s = html_lib.unescape(_ANY_TAG.sub("", _LINE_BREAK.sub("\n", s))).replace("\xa0", " ")
    s = "\n".join(line.rstrip() for line in s.replace("\r\n", "\n").split("\n"))
    return _BLANKS.sub("\n\n", s).strip()


@dataclass
class MessageEvent:
    conversation_id: str
    message_id: str
    event: str  # created | edited | deleted | undeleted
    at: datetime
    text: str | None = None
    html: str | None = None
    reply_to_id: str | None = None
    from_aad_id: str | None = None
    from_name: str | None = None
    importance: str | None = None
    mentions: list | None = None


@dataclass
class Normalized:
    team: dict | None = None
    conversation: dict | None = None
    message_event: MessageEvent | None = None
    attachments: list[dict] = field(default_factory=list)
    reactions: list[dict] = field(default_factory=list)
    install: dict | None = None  # {"action": "add"|"remove", "source", "team_id", "by_aad", "at"}


def _team_row(activity: dict) -> dict | None:
    cd = _d(activity.get("channelData"))
    team = _d(cd.get("team"))
    if not team.get("id"):
        return None
    return {
        "id": team["id"],
        "aad_group_id": team.get("aadGroupId"),
        "name": team.get("name"),
        "tenant_id": _d(cd.get("tenant")).get("id"),
    }


def _conversation_row(activity: dict) -> dict | None:
    conv = _d(activity.get("conversation"))
    cid = base_conversation_id(conv.get("id"))
    if not cid:
        return None
    cd = _d(activity.get("channelData"))
    channel = _d(cd.get("channel"))
    ctype = conv.get("conversationType")
    if ctype is None and channel.get("id"):
        ctype = "channel"
    return {
        "id": cid,
        "conversation_type": ctype,
        "team_id": _d(cd.get("team")).get("id"),
        "channel_id": channel.get("id"),
        "name": conv.get("name") or channel.get("name"),
        "tenant_id": _d(cd.get("tenant")).get("id") or conv.get("tenantId"),
    }


def _reply_to(activity: dict, message_id: str) -> str | None:
    """replyToId if Teams gives one, else the ';messageid=' thread root (if it isn't this message)."""
    if activity.get("replyToId"):
        return activity["replyToId"]
    m = _THREAD_SUFFIX.search(_d(activity.get("conversation")).get("id") or "")
    if m and m.group(1) != message_id:
        return m.group(1)
    return None


def _split_attachments(activity: dict) -> tuple[str | None, list[dict]]:
    """Return (html content, other attachments). text/html is captured on the message itself."""
    html = None
    others = []
    for att in _l(activity.get("attachments")):
        if not isinstance(att, dict):
            continue
        if att.get("contentType") == "text/html" and isinstance(att.get("content"), str):
            html = att["content"]
        else:
            others.append(att)
    return html, others


def _message_event(activity: dict) -> tuple[MessageEvent | None, list[dict]]:
    atype = activity.get("type")
    et = event_type(activity)
    if atype == "message":
        kind = "created"
    elif atype == "messageUpdate":
        kind = "undeleted" if et == "undeleteMessage" else "edited" if et == "editMessage" else None
    elif atype == "messageDelete":
        kind = "deleted"
    else:
        kind = None
    mid = activity.get("id")
    cid = base_conversation_id(_d(activity.get("conversation")).get("id"))
    if kind is None or not mid or not cid:
        return None, []
    frm = _d(activity.get("from"))
    html, others = _split_attachments(activity)
    at = parse_ts(activity.get("timestamp"))
    mentions = [e for e in _l(activity.get("entities")) if isinstance(e, dict) and e.get("type") == "mention"]
    ev = MessageEvent(
        conversation_id=cid,
        message_id=str(mid),
        event=kind,
        at=at,
        text=strip_mentions(activity.get("text")),
        html=html,
        reply_to_id=_reply_to(activity, str(mid)),
        from_aad_id=frm.get("aadObjectId"),
        from_name=frm.get("name"),
        importance=activity.get("importance") or _d(activity.get("channelData")).get("importance"),
        mentions=mentions or None,
    )
    atts = [
        {
            "conversation_id": cid,
            "message_id": str(mid),
            "content_type": a.get("contentType"),
            "name": a.get("name"),
            "content_url": a.get("contentUrl"),
            "payload": a,
        }
        for a in others
    ]
    return ev, atts


def _reactions(activity: dict) -> list[dict]:
    if activity.get("type") != "messageReaction":
        return []
    cid = base_conversation_id(_d(activity.get("conversation")).get("id"))
    mid = activity.get("replyToId")
    if not cid or not mid:
        return []
    frm = _d(activity.get("from"))
    at = parse_ts(activity.get("timestamp"))
    out = []
    for key, action in (("reactionsAdded", "added"), ("reactionsRemoved", "removed")):
        for r in _l(activity.get(key)):
            out.append(
                {
                    "conversation_id": cid,
                    "message_id": str(mid),
                    "user_aad_id": frm.get("aadObjectId"),
                    "user_name": frm.get("name"),
                    "reaction_type": _d(r).get("type"),
                    "action": action,
                    "at": at,
                }
            )
    return out


def _bot_added(activity: dict) -> bool:
    """conversationUpdate whose membersAdded includes the bot itself (member.id == recipient.id)."""
    bot_id = _d(activity.get("recipient")).get("id")
    return bool(bot_id) and any(_d(m).get("id") == bot_id for m in _l(activity.get("membersAdded")))


def _install(activity: dict, team: dict | None) -> dict | None:
    """Bot added to / removed from a team. Two signals: installationUpdate, and the bot appearing in
    conversationUpdate.membersAdded (Teams does not always send the former)."""
    if not team:
        return None
    atype = activity.get("type")
    if atype == "installationUpdate":
        action = str(activity.get("action") or "")
        if action.startswith("add"):  # includes "add-upgrade"
            kind, source = "add", "installationUpdate"
        elif action.startswith("remove"):
            kind, source = "remove", "installationUpdate"
        else:
            return None
    elif atype == "conversationUpdate" and _bot_added(activity):
        kind, source = "add", "membersAdded"
    else:
        return None
    return {
        "action": kind,
        "source": source,
        "team_id": team["id"],
        "by_aad": _d(activity.get("from")).get("aadObjectId"),
        "at": parse_ts(activity.get("timestamp")),
    }


def normalize(activity: dict) -> Normalized:
    team = _team_row(activity)
    ev, atts = _message_event(activity)
    return Normalized(
        team=team,
        conversation=_conversation_row(activity),
        message_event=ev,
        attachments=atts,
        reactions=_reactions(activity),
        install=_install(activity, team),
    )


def log_line(activity: dict, raw_id: int | None, include_text: bool = True) -> dict:
    """FR-12: one structured record per activity."""
    cd = _d(activity.get("channelData"))
    text = activity.get("text") if include_text else None
    return {
        "type": activity.get("type"),
        "event_type": cd.get("eventType"),
        "team": _d(cd.get("team")).get("name") or _d(cd.get("team")).get("id"),
        "channel": _d(cd.get("channel")).get("name") or _d(cd.get("channel")).get("id"),
        "from_name": _d(activity.get("from")).get("name"),
        "text": text[:120] if isinstance(text, str) else None,
        "attachment_types": [
            a.get("contentType") for a in _l(activity.get("attachments")) if isinstance(a, dict)
        ],
        "raw_id": raw_id,
    }
