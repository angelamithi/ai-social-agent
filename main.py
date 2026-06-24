"""
Main orchestrator. Run this once daily (via Render Cron Job) to:
  1. Pull a candidate topic (manual queue first, then RSS)
  2. Generate platform drafts via Claude
  3. Run safety checks on the X draft
  4. Generate one branded image (with Afrivance.ai watermark), stored in Postgres
  5. Save the candidate as a pending approval and send it to you on
     WhatsApp for a manual yes/no — NOTHING posts automatically anymore.
  6. The actual posting + LinkedIn/Facebook draft messaging happens
     later, in webhook_server.py, once you tap Approve.

Designed to be safe to run repeatedly: if a pending approval is already
waiting on your decision, this run skips entirely rather than piling up
a second request — you'll never get two competing approval messages.

State (history, pending approval, image bytes) lives in Postgres via
db.py, not local files — this process and webhook_server.py run in
separate containers on Render with no shared filesystem.
"""

import sys
import traceback
from datetime import datetime

import config
import db
import ingest
import generate
import generate_image
import safety
import whatsapp_client


def log(message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(config.LOG_FILE, "a") as f:
        f.write(line + "\n")


def run() -> None:
    log("=== Daily run started ===")

    if not config.ENABLED:
        log("ENABLED=false in .env — kill switch is active. Exiting without posting.")
        return

    if not config.DATABASE_URL:
        log("FATAL: DATABASE_URL is not set. Cannot proceed without the shared database. See README.")
        return

    db.init_schema()

    if db.has_pending():
        log("A pending approval is already waiting on your WhatsApp decision. "
            "Skipping this run rather than sending a second request.")
        return

    items = ingest.get_daily_items()
    if not items:
        log("No candidate items found (manual queue empty, no fresh relevant RSS items). Exiting.")
        return

    log(f"Found {len(items)} candidate item(s). Trying in order until one succeeds.")

    for item in items:
        title = item.get("title", "(untitled)")
        url = item.get("url")
        log(f"Trying item: '{title}' (source: {item['source']})")

        drafts = generate.generate_drafts(item)
        if drafts is None:
            log(f"  Generation failed for this item, skipping.")
            continue

        x_text = drafts["x"]
        linkedin_text = drafts["linkedin"]
        facebook_text = drafts["facebook"]

        # --- Safety gates on the X draft ---
        result = safety.run_all_checks(
            x_text,
            platform="x",
            max_len=config.MAX_X_POST_LENGTH,
            max_per_day=config.MAX_X_POSTS_PER_DAY,
            source_url=url,
        )

        if not result.ok:
            log(f"  X draft BLOCKED: {result.reason}")
            db.record_post("x", x_text, source_url=url, status="blocked")
            log("  Trying next candidate item instead of sending a blocked draft for approval.")
            continue

        # --- Generate one branded image, reused across all three platforms ---
        image_result = generate_image.generate_image(item, drafts=drafts)
        image_id = image_result["image_id"] if image_result else None
        if image_id:
            log(f"  Image generated and stored (image_id={image_id})")
        else:
            log("  Image generation failed.")

        if not image_id:
            log("  No image available — cannot use the image-header approval template. "
                "Skipping this item; trying the next one.")
            continue

        # --- Park the candidate as a pending approval (nothing posts yet) ---
        approval_id = db.create_pending(
            item, x_text, linkedin_text, facebook_text, image_id, source_url=url,
        )
        log(f"  Created pending approval (id={approval_id}). Sending to WhatsApp for review.")

        if not config.PUBLIC_BASE_URL:
            log("  FATAL: PUBLIC_BASE_URL is not set in .env — cannot build a public image URL "
                "for the WhatsApp template. See README. Aborting this run.")
            return
        image_url = f"{config.PUBLIC_BASE_URL}/images/{image_id}"

        caption = x_text if len(x_text) <= 200 else x_text[:197] + "..."

        try:
            whatsapp_client.send_approval_request(image_url, caption, approval_id)
            log(f"  Approval request sent to WhatsApp. Waiting for your decision "
                f"(no timeout — nothing posts until you respond).")
        except Exception as e:
            log(f"  FAILED to send WhatsApp approval request: {e}\n{traceback.format_exc()}")
            log("  Rolling back pending approval since you were never notified.")
            db.resolve(approval_id, "failed_to_notify")

        log("=== Daily run completed — awaiting your approval ===")
        return

    log("No item succeeded through generation/safety checks. Nothing sent for approval today.")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"FATAL ERROR: {e}\n{traceback.format_exc()}")
        sys.exit(1)
