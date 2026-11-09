"""WhatsApp channel (Meta WhatsApp Cloud API): the same Saathi conversation over text and voice notes.

Nothing here decides anything: each message becomes one turn of the same web-call state machine
(converse.turn), so confirmation, the rules and the model are identical. This module only translates:

    parse(payload)                 Meta's webhook JSON -> [{id, wa_id, kind, text, action, media_id}]
    valid_signature(...)           X-Hub-Signature-256 check (HMAC-SHA256 of the raw body)
    outgoing(wa_id, reply, buttons) the reply as Cloud API messages (text, reply buttons or a list)
    thread_key(wa_id)              a hashed key, so stored threads don't hold phone numbers

Voice notes arrive as OGG/Opus, which Gnani's REST STT reads directly (30 s limit); replies to a voice
note go back as a voice note made by Gnani TTS (OGG/Opus), plus the text and buttons.
"""
import hashlib
import hmac
import re

MAX_BUTTONS = 3        # reply buttons per message (Cloud API limit)
MAX_ROWS = 10          # rows in a list message
BUTTON_TITLE = 20      # characters
ROW_TITLE = 24
ENGLISH = re.compile(r"\b(hello|hi|hey|i|need|want|my|the|is|am|loan|please|how|much|can)\b", re.I)
HINDI = re.compile(r"\b(mujhe|chahiye|hai|hain|kitna|mera|meri|namaste|haan|nahi|lena|kya)\b|[ऀ-ॿ]", re.I)


def thread_key(wa_id):
    return hashlib.sha256(f"loan-saathi:{wa_id}".encode()).hexdigest()[:32]


def valid_signature(app_secret, body, header):
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.split("=", 1)[1])


def guess_language(text):
    """The first message picks the call's language: English only if it reads as English."""
    t = text or ""
    return "en" if ENGLISH.search(t) and not HINDI.search(t) else "hi"


def parse(payload):
    """Incoming user messages from a Cloud API webhook (statuses and other events are ignored)."""
    out = []
    for entry in (payload or {}).get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for m in value.get("messages", []) or []:
                kind, text, action, media = m.get("type"), "", "", None
                if kind == "text":
                    text = (m.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    i = m.get("interactive") or {}
                    picked = i.get("button_reply") or i.get("list_reply") or {}
                    action, text = picked.get("id", ""), picked.get("title", "")
                    kind = "button"
                elif kind == "button":                       # quick-reply buttons on templates
                    b = m.get("button") or {}
                    action, text = b.get("payload", ""), b.get("text", "")
                elif kind == "audio":
                    media = (m.get("audio") or {}).get("id")
                else:
                    kind = "unsupported"
                if m.get("from") and m.get("id"):
                    out.append({"id": m["id"], "wa_id": m["from"], "kind": kind, "text": text.strip()[:1000],
                                "action": action[:60], "media_id": media})
    return out


def _clip(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def outgoing(wa_id, reply, buttons=None):
    """Saathi's reply as Cloud API message bodies: the text, with its tap targets as reply buttons
    (up to 3) or a list (up to 10). Each button's id is the same action the web call sends."""
    buttons = [b for b in (buttons or []) if b.get("action")][:MAX_ROWS]
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": wa_id}
    body = _clip(reply or "…", 1024)
    if not buttons:
        return [{**base, "type": "text", "text": {"body": _clip(reply or "…", 4096), "preview_url": False}}]
    if len(buttons) <= MAX_BUTTONS:
        return [{**base, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["action"], "title": _clip(b["label"], BUTTON_TITLE)}}
                                   for b in buttons]}}}]
    return [{**base, "type": "interactive", "interactive": {
        "type": "list", "body": {"text": body},
        "action": {"button": "Choose", "sections": [{"title": "Options", "rows": [
            {"id": b["action"], "title": _clip(b["label"], ROW_TITLE)} for b in buttons]}]}}}]
