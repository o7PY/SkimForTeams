"""Teams message deep links (P4-D-129, spike S1). Built in exactly the form the human opened on the phone and in the browser
(new-Teams host, raw channel id); the root post and the reply form both landed on the exact message."""
from __future__ import annotations

from urllib.parse import quote

HOST = "teams.cloud.microsoft"


def teams_message_link(team: dict, channel_name: str | None, conversation_id: str, message_id: str,
                       reply_to_id: str | None = None) -> str | None:
    """None when the team lacks the ids a link needs. `message_id` is the epoch-ms id of the post; for a reply the root
    post's id is `reply_to_id` (parentMessageId)."""
    tenant, group = team.get("tenant_id"), team.get("aad_group_id")
    if not (tenant and group and conversation_id and message_id):
        return None
    q = [f"tenantId={tenant}", f"groupId={group}", f"parentMessageId={reply_to_id or message_id}"]
    if team.get("name"):
        q.append(f"teamName={quote(team['name'], safe='')}")
    if channel_name:
        q.append(f"channelName={quote(channel_name, safe='')}")
    q.append(f"createdTime={message_id}")
    return f"https://{HOST}/l/message/{conversation_id}/{message_id}?" + "&".join(q)
