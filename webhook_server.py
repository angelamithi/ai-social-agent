"""
Always-on FastAPI web service. On Render this runs as a Web Service
(paid tier recommended — see README — so it never sleeps and your
WhatsApp button taps are handled immediately).

Responsibilities:
  1. GET  /webhook            — Meta's one-time webhook verification handshake.
  2. POST /webhook            — receives all inbound WhatsApp events:
       - "pick:<selection_id>:<n>"   button tap -> stage-1 choice made
       - "approve:<id>" / "reject:<id>" button tap -> stage-2 decision
       - plain text message -> Flow B: a new topic you're texting in
  3. GET  /images/{image_id}  — serves a generated image (stored in
     Postgres, not local disk) over HTTPS so Meta's servers can fetch
     it for template image headers.
  4. A background scheduler (APScheduler) checks every few minutes for
     a stage-1 selection that's been pending longer than
     config.SELECTION_TIMEOUT_HOURS, and auto-picks option 1 if so.

This process is also where ALL content generation now happens for
Flow B (you texting a topic) and the image-generation step for BOTH
flows (it only happens after a stage-1 pick, whether by you or by
timeout) — main.py (the cron job) only handles Flow A's initial
RSS-sourced stage-1 selection.

LinkedIn/Facebook drafts and all confirmations are sent as WhatsApp
text messages rather than written to a file — this process and the
cron job run in separate containers with no shared filesystem.
"""

import traceback
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from apscheduler.schedulers.background import BackgroundScheduler

import config
import db
import url_extract
import generate
import generate_image
import publish_x
import safety
import whatsapp_client

app = FastAPI()
_scheduler = BackgroundScheduler()


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

    _scheduler.add_job(
        _check_selection_timeout,
        "interval",
        minutes=15,
        id="selection_timeout_sweep",
        replace_existing=True,
    )
    _scheduler.start()
    log("Background scheduler started (checks stage-1 selection timeout every 15 min).")


@app.on_event("shutdown")
def on_shutdown():
    _scheduler.shutdown(wait=False)


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
    """Receives incoming WhatsApp events: button taps and plain text."""
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
    """Process a single inbound message: button reply (pick / approve /
    reject) or a plain text message (treated as a new Flow B topic if
    nothing relevant is currently pending)."""
    button_reply = message.get("button")
    interactive = message.get("interactive")
    text_body = message.get("text", {}).get("body") if message.get("type") == "text" else None

    payload = None
    if button_reply:
        payload = button_reply.get("payload")
    elif interactive and interactive.get("type") == "button_reply":
        payload = interactive.get("button_reply", {}).get("id")

    if payload and ":" in payload:
        _handle_button_payload(payload)
        return

    if text_body:
        _handle_text_message(text_body)
        return

    log(f"Received message with no recognizable payload or text body: {message}")


def _handle_button_payload(payload: str) -> None:
    parts = payload.split(":")
    action = parts[0]

    if action == "pick" and len(parts) == 3:
        selection_id, option_str = parts[1], parts[2]
        try:
            option_number = int(option_str)
        except ValueError:
            log(f"Unrecognized option number in pick payload: {payload}")
            return
        log(f"Received pick (option {option_number}) for selection_id={selection_id}")
        _process_pick(selection_id, option_number, auto_picked=False)

    elif action in ("approve", "reject") and len(parts) == 2:
        approval_id = parts[1]
        log(f"Received '{action}' for approval_id={approval_id}")
        _process_decision(approval_id, action)

    else:
        log(f"Unrecognized button payload shape: {payload}")


def _handle_text_message(text_body: str) -> None:
    """Flow B: if nothing is currently pending, treat this free-text
    message as a new topic to generate draft options for. If something
    IS pending, this is just incidental chatter (e.g. you typed
    something instead of tapping a button) — log and ignore, since
    button taps are the only supported way to make a pending decision.
    """
    if db.has_pending_selection():
        log(f"Received text message while a selection is already pending — "
            f"ignoring as chatter (use the buttons to pick): {text_body[:80]}")
        return
    if db.has_pending():
        log(f"Received text message while a final approval is already pending — "
            f"ignoring as chatter (use the buttons to approve/reject): {text_body[:80]}")
        return

    log(f"Treating inbound text as a new Flow B topic request: {text_body[:80]}")

    url = url_extract.extract_url(text_body)
    headline = url_extract.strip_url(text_body, url) if url else text_body.strip()

    summary = ""
    final_url = url
    if url:
        log(f"  Message contains a URL, attempting to fetch and extract article text: {url}")
        article = url_extract.fetch_article(url)
        if article:
            summary = article["text"]
            final_url = article["final_url"]
            log(f"  Extracted {len(summary)} chars of article text "
                f"(resolved to {final_url})" if final_url != url else
                f"  Extracted {len(summary)} chars of article text.")
        else:
            log("  Article extraction failed or returned too little content — "
                "falling back to the headline text alone, without inventing "
                "article details.")
            # Tell Claude explicitly that no article content is available,
            # rather than silently giving it an empty summary (which it
            # might fill in with guessed/hallucinated specifics).
            summary = ("(No article content could be retrieved from the link in "
                       "this message — write based on the headline alone, and do "
                       "not invent specific facts, figures, or quotes that would "
                       "need to come from the actual article.)")

    item = {"title": headline or "(see linked article)", "summary": summary,
            "url": final_url, "source": "whatsapp"}

    options = generate.generate_draft_options(item)
    if options is None:
        try:
            whatsapp_client.send_text(
                "I couldn't turn that into a clean AI-education post — it might not "
                "have a strong enough AI angle, or something went wrong generating "
                "drafts. Try rephrasing the topic, or send me a different one."
            )
        except Exception as e:
            log(f"Failed to send Flow B failure notice: {e}")
        return

    surviving_options = []
    for i, option in enumerate(options):
        result = safety.run_all_checks(
            option["x"], platform="x", max_len=config.MAX_X_POST_LENGTH,
            max_per_day=config.MAX_X_POSTS_PER_DAY, source_url=item.get("url"),
        )
        if result.ok:
            surviving_options.append(option)
        else:
            log(f"Flow B option {i+1} BLOCKED: {result.reason}")

    if len(surviving_options) < 3:
        try:
            whatsapp_client.send_text(
                "I generated drafts for that topic, but one or more didn't pass "
                "safety checks (rate limit, blocklist, etc.), so I don't have a "
                "clean set of 3 to show you. Try again, possibly later today."
            )
        except Exception as e:
            log(f"Failed to send Flow B partial-block notice: {e}")
        return

    selection_id = db.create_pending_selection(item, options, source="whatsapp", source_url=item.get("url"))
    log(f"Created pending selection (id={selection_id}) from Flow B topic request.")

    # Flow B is already inside an open session (you just messaged us), so
    # we could use a free interactive message here instead of a template —
    # but reusing the same template keeps the code path identical to Flow A
    # and avoids maintaining two different selection-UI implementations.
    try:
        whatsapp_client.send_options_request(item["title"], options, selection_id)
        log("Options request sent to WhatsApp for Flow B topic.")
    except Exception as e:
        log(f"FAILED to send Flow B options request: {e}\n{traceback.format_exc()}")
        db.resolve_selection(selection_id, "failed_to_notify")


def _process_pick(selection_id: str, option_number: int, auto_picked: bool) -> None:
    decision = "auto_chosen" if auto_picked else "chosen"
    entry = db.resolve_selection(selection_id, decision, chosen_option=option_number)
    if entry is None:
        log(f"selection_id={selection_id} did not match the current pending selection "
            f"(already resolved, or stale webhook retry) — ignoring.")
        return

    option_key = f"option_{option_number}"
    chosen = entry.get(option_key)
    if not chosen:
        log(f"ERROR: resolved selection has no '{option_key}' data: {entry}")
        return

    item = {"title": entry.get("item_title", ""), "summary": "", "url": entry.get("source_url")}

    if auto_picked:
        try:
            whatsapp_client.send_text(
                f"No response after {config.SELECTION_TIMEOUT_HOURS}h, so I went with "
                f"option 1 ({chosen.get('angle_label', '')}) automatically. "
                f"Generating the image now..."
            )
        except Exception as e:
            log(f"Failed to send auto-pick notice: {e}")
    else:
        try:
            whatsapp_client.send_text(
                f"Picked: {chosen.get('angle_label', f'Option {option_number}')}. "
                f"Generating the image now..."
            )
        except Exception as e:
            log(f"Failed to send pick confirmation: {e}")

    # --- Generate the branded image for the chosen option ---
    image_result = generate_image.generate_image(item, drafts=chosen)
    image_id = image_result["image_id"] if image_result else None
    if not image_id:
        log(f"Image generation FAILED for selection_id={selection_id}")
        try:
            whatsapp_client.send_text(
                "Image generation failed for that option — nothing was posted. "
                "You can try texting me the topic again."
            )
        except Exception as e:
            log(f"Failed to send image-failure notice: {e}")
        return

    log(f"Image generated and stored (image_id={image_id}) for selection_id={selection_id}")

    # --- Move into stage-2: the existing final approval flow ---
    approval_id = db.create_pending(
        item, chosen["x"], chosen["linkedin"], chosen["facebook"], image_id,
        source_url=entry.get("source_url"),
    )
    log(f"Created pending approval (id={approval_id}) for the chosen option.")

    if not config.PUBLIC_BASE_URL:
        log("FATAL: PUBLIC_BASE_URL is not set — cannot build a public image URL "
            "for the WhatsApp template. Aborting before sending stage-2 request.")
        return
    image_url = f"{config.PUBLIC_BASE_URL}/images/{image_id}"
    caption = chosen["x"] if len(chosen["x"]) <= 200 else chosen["x"][:197] + "..."

    try:
        whatsapp_client.send_approval_request(image_url, caption, approval_id)
        log("Stage-2 final approval request sent to WhatsApp.")
    except Exception as e:
        log(f"FAILED to send stage-2 approval request: {e}\n{traceback.format_exc()}")
        db.resolve(approval_id, "failed_to_notify")


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

    try:
        whatsapp_client.send_text(
            f"{confirmation}\n\n"
            f"--- LinkedIn draft ---\n{entry['linkedin_text']}\n\n"
            f"--- Facebook draft ---\n{entry['facebook_text']}"
        )
    except Exception as e:
        log(f"Failed to send approval confirmation / drafts: {e}")


def _check_selection_timeout() -> None:
    """Runs every 15 minutes (via APScheduler). If a stage-1 selection
    has been pending longer than config.SELECTION_TIMEOUT_HOURS, auto-
    picks option 1 rather than waiting indefinitely (unlike stage-2,
    which always waits indefinitely by design)."""
    try:
        pending = db.get_pending_selection()
        if pending is None:
            return
        created_at = pending["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created_at
        if age >= timedelta(hours=config.SELECTION_TIMEOUT_HOURS):
            log(f"Stage-1 selection {pending['selection_id']} has been pending for "
                f"{age}, exceeding {config.SELECTION_TIMEOUT_HOURS}h timeout — "
                f"auto-picking option 1.")
            _process_pick(pending["selection_id"], 1, auto_picked=True)
    except Exception as e:
        log(f"ERROR in selection timeout sweep: {e}\n{traceback.format_exc()}")
