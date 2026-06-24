"""
WhatsApp Cloud API client (Meta, official/direct).

Sends the approval request as a pre-approved template message with an
image header and quick-reply buttons (Approve / Reject). A template is
required here because the daily run is not inside an active 24-hour
session with you — Meta requires template messages for any
business-initiated contact outside that window.

Template media headers must point to a publicly accessible URL, not a
local file — so the generated image is served over HTTP by
webhook_server.py, and that public URL is what gets passed here.

See README for the one-time setup: creating + getting Meta's approval
for the "post_approval_request" template (typically minutes to ~24h).
"""

import requests

import config

GRAPH_API_BASE = "https://graph.facebook.com/v23.0"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _messages_url() -> str:
    return f"{GRAPH_API_BASE}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"


def send_approval_request(image_url: str, caption_text: str, approval_id: str) -> dict:
    """Send the approval-request template: image header + body text +
    two quick-reply buttons (Approve / Reject).

    The template body uses a NAMED placeholder ({{post_summary}}), not
    the older positional ({{1}}) style. Confirmed via live testing
    against the real API: Meta does NOT accept a top-level
    "parameter_format" field on the template object (it's rejected
    outright as an unexpected key, regardless of API version) — naming
    is instead inferred automatically from the "parameter_name" field
    present on each body parameter, which must match the named
    variable used in the approved template exactly (see
    WHATSAPP_BODY_PARAM_NAME in config.py).

    `caption_text` should be a short summary (the template body has a
    character limit) — full drafts are sent via WhatsApp text after
    approval, this message just needs to be enough for you to decide.

    The button payloads are encoded as "approve:<approval_id>" and
    "reject:<approval_id>" so the webhook can match the reply back to
    the right pending item even if a new one is created later.

    Returns the API response JSON. Raises requests.HTTPError on failure.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": config.APPROVER_PHONE_NUMBER,
        "type": "template",
        "template": {
            "name": config.WHATSAPP_APPROVAL_TEMPLATE_NAME,
            "language": {"code": config.WHATSAPP_TEMPLATE_LANGUAGE},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {"type": "image", "image": {"link": image_url}}
                    ],
                },
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": caption_text,
                            "parameter_name": config.WHATSAPP_BODY_PARAM_NAME,
                        },
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": 0,
                    "parameters": [
                        {"type": "payload", "payload": f"approve:{approval_id}"}
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": 1,
                    "parameters": [
                        {"type": "payload", "payload": f"reject:{approval_id}"}
                    ],
                },
            ],
        },
    }

    response = requests.post(_messages_url(), headers=_headers(), json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def send_text(text: str) -> dict:
    """Send a plain session-message (only works within an active 24h
    window, e.g. right after the person has just replied to a button).
    Used for confirmations like "Posted to X!" after a decision.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": config.APPROVER_PHONE_NUMBER,
        "type": "text",
        "text": {"body": text},
    }
    response = requests.post(_messages_url(), headers=_headers(), json=payload, timeout=15)
    response.raise_for_status()
    return response.json()
