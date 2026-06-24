"""
Always-on FastAPI web service. On Render this runs as a Web Service
(paid tier recommended — see README — so it never sleeps and your
WhatsApp button taps are handled immediately).

Responsibilities:
  1. GET  /webhook        — Meta's one-time webhook verification handshake.
  2. POST /webhook         — receives button-tap events when you approve/
     reject a pending post from WhatsApp.
  3. GET  /images/{image_id} — serves a generated image (stored in
     Postgres, not local disk) over HTTPS so Meta's servers can fetch
     it for the template's image header.

This process does NOT generate content or talk to Claude/OpenAI/X
directly for the daily flow — it only reacts to your approval decision
and then calls publish_x.py to finish the job that main.py started and
parked in the shared Postgres database (via db.py).

LinkedIn/Facebook drafts are sent to you as a WhatsApp text message
after approval, rather than written to a file — this process and the
cron job run in separate containers with no shared filesystem.
"""

import traceback
from datetime import datetime

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

import config
import db
import publish_x
import whatsapp_client

app = FastAPI()


def log(message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"[{timestamp}] [webhook] {message}"
    print(line)
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass  # local log file is best-effort; Render's own logs still capture stdout


@app.on_event("startup")
def on_startup():
    if config.DATABASE_URL:
        db.init_schema()
    else:
        log("WARNING: DATABASE_URL not set — the app will fail on any request that touches the database.")
    if not config.WHATSAPP_VERIFY_TOKEN:
        log("WARNING: WHATSAPP_VERIFY_TOKEN is not set — webhook verification will fail.")


@app.get("/images/{image_id}")
def serve_image(image_id: str):
    """Serves generated images so Meta can fetch them for template headers.

    No auth on this route by design — WhatsApp template image headers
    must be fetchable by Meta's servers without credentials. image_ids
    are random UUIDs (not guessable sequential IDs), which is a weak
    but reasonable mitigation given these are just illustrative social
    media graphics, not sensitive data.
    """
    record = db.get_image(image_id)
    if record is None:
        return Response(content="Not found", status_code=404)
    return Response(content=record["data"], media_type=record["content_type"])


@app.get("/webhook")
def verify_webhook(request: Request):
    """Meta's one-time webhook verification handshake."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        log("Webhook verification succeeded.")
        return PlainTextResponse(content=challenge, status_code=200)

    log("Webhook verification FAILED — token mismatch.")
    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/webhook")
async def handle_webhook(request: Request):
    """Receives incoming WhatsApp events, including button-tap replies."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    try:
        entries = data.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for message in messages:
                    _handle_message(message)
    except Exception as e:
        log(f"ERROR handling webhook payload: {e}\n{traceback.format_exc()}")

    # Always 200 quickly — Meta retries aggressively on non-2xx responses
    return {"status": "received"}


def _handle_message(message: dict) -> None:
    """Process a single inbound message, looking for a button reply."""
    button_reply = message.get("button")
    interactive = message.get("interactive")

    payload = None
    if button_reply:
        payload = button_reply.get("payload")
    elif interactive and interactive.get("type") == "button_reply":
        payload = interactive.get("button_reply", {}).get("id")

    if not payload or ":" not in payload:
        log(f"Received message without a recognizable button payload: {message}")
        return

    decision, _, approval_id = payload.partition(":")
    if decision not in ("approve", "reject"):
        log(f"Unrecognized decision '{decision}' in payload: {payload}")
        return

    log(f"Received '{decision}' for approval_id={approval_id}")
    _process_decision(approval_id, decision)


def _process_decision(approval_id: str, decision: str) -> None:
    entry = db.resolve(approval_id, decision)
    if entry is None:
        log(f"approval_id={approval_id} did not match the current pending item "
            f"(already resolved, or stale webhook retry) — ignoring.")
        return

    if decision == "reject":
        db.record_post("x", entry["x_text"], source_url=entry.get("source_url"), status="rejected")
        log(f"Post REJECTED by approver. Nothing posted. (approval_id={approval_id})")
        try:
            whatsapp_client.send_text("Got it — that post has been discarded. Nothing was published.")
        except Exception as e:
            log(f"Failed to send rejection confirmation: {e}")
        return

    # decision == "approve"
    image_id = entry.get("image_id")
    image_record = db.get_image(image_id) if image_id else None
    image_bytes = image_record["data"] if image_record else None

    try:
        response = publish_x.post_tweet(entry["x_text"], image_bytes=image_bytes)
        log(f"Posted to X successfully after approval. Response: {response}")
        db.record_post("x", entry["x_text"], source_url=entry.get("source_url"), status="posted")
        confirmation = "✅ Approved and posted to X!"
    except Exception as e:
        log(f"X POST FAILED after approval: {e}\n{traceback.format_exc()}")
        db.record_post("x", entry["x_text"], source_url=entry.get("source_url"), status="failed")
        confirmation = "⚠️ You approved it, but posting to X failed. Check the logs."

    db.record_post("linkedin", entry["linkedin_text"], source_url=entry.get("source_url"), status="draft")
    db.record_post("facebook", entry["facebook_text"], source_url=entry.get("source_url"), status="draft")

    # Send LinkedIn/Facebook drafts as WhatsApp text (no shared file storage
    # between this process and the cron job, so a file isn't an option).
    try:
        whatsapp_client.send_text(
            f"{confirmation}\n\n"
            f"--- LinkedIn draft ---\n{entry['linkedin_text']}\n\n"
            f"--- Facebook draft ---\n{entry['facebook_text']}"
        )
    except Exception as e:
        log(f"Failed to send approval confirmation / drafts: {e}")
