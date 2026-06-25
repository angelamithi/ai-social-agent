"""
Main orchestrator. Run this once daily (via Render Cron Job) to:
  1. Pull a candidate topic (manual queue first, then RSS)
  2. Generate 3 distinct draft OPTIONS via Claude (not a single draft)
  3. Run safety checks on each option's X draft; drop any that fail
  4. Send the surviving options to WhatsApp as a stage-1 selection
     request (3 short previews + 3 buttons) — NOTHING is generated as
     an image or posted yet.
  5. Once you pick an option (or 8 hours pass and option 1 auto-wins —
     see config.SELECTION_TIMEOUT_HOURS), webhook_server.py generates
     the image for your chosen option and sends the existing stage-2
     final-approval request (image + Approve/Reject).

This is "Flow A" — the autonomous, RSS-sourced path. "Flow B" (you
texting the agent a topic directly on WhatsApp) is handled entirely in
webhook_server.py, since it's triggered by an inbound message, not by
this scheduled cron job — but it shares the exact same stage-1/stage-2
pipeline and the same generate.generate_draft_options() function.

Designed to be safe to run repeatedly: if a stage-1 selection OR a
stage-2 approval is already pending, this run skips entirely rather
than piling up competing requests.

State (history, pending selections/approvals, image bytes) lives in
Postgres via db.py, not local files — this process and
webhook_server.py run in separate containers on Render with no shared
filesystem.
"""

import sys
import traceback
from datetime import datetime

import config
import db
import ingest
import generate
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

    if db.has_pending_selection():
        log("A draft-option selection is already waiting on your WhatsApp decision "
            "(stage 1). Skipping this run rather than sending a second request.")
        return

    if db.has_pending():
        log("A final post is already waiting on your WhatsApp approval (stage 2). "
            "Skipping this run rather than sending a second request.")
        return

    log("Fetching candidate items (manual queue + RSS — each feed bounded to "
        f"{ingest.RSS_FETCH_TIMEOUT_SECONDS}s timeout)...")
    items = ingest.get_daily_items()
    if not items:
        log("No candidate items found (manual queue empty, no fresh relevant RSS items). Exiting.")
        return

    log(f"Found {len(items)} candidate item(s). Trying in order until one succeeds.")

    for item in items:
        title = item.get("title", "(untitled)")
        url = item.get("url")
        log(f"Trying item: '{title}' (source: {item['source']})")

        options = generate.generate_draft_options(item)
        if options is None:
            log("  Generation failed (or Claude skipped this item), trying next candidate.")
            continue

        # --- Safety gate each option's X draft individually; drop failures ---
        surviving_options = []
        for i, option in enumerate(options):
            result = safety.run_all_checks(
                option["x"],
                platform="x",
                max_len=config.MAX_X_POST_LENGTH,
                max_per_day=config.MAX_X_POSTS_PER_DAY,
                source_url=url,
            )
            if result.ok:
                surviving_options.append(option)
            else:
                log(f"  Option {i+1} ('{option.get('angle_label', '?')}') BLOCKED: {result.reason}")

        if len(surviving_options) < 3:
            log(f"  Only {len(surviving_options)}/3 options passed safety checks — "
                f"need all 3 for a clean selection. Trying next candidate item instead "
                f"of sending a partial/blocked set for selection.")
            if surviving_options:
                # Record the ones that did pass as blocked-context, so dedup/
                # history still reflects that this content existed.
                for option in surviving_options:
                    db.record_post("x", option["x"], source_url=url, status="blocked_partial_set")
            continue

        # --- Park as a pending selection (nothing generated/posted yet) ---
        selection_id = db.create_pending_selection(item, options, source=item.get("source", "rss"), source_url=url)
        log(f"  Created pending selection (id={selection_id}) with 3 options. "
            f"Sending to WhatsApp for your pick.")

        try:
            whatsapp_client.send_options_request(title, options, selection_id)
            log(f"  Options request sent to WhatsApp. Waiting for your pick "
                f"(auto-picks option 1 after {config.SELECTION_TIMEOUT_HOURS}h if no response).")
        except Exception as e:
            log(f"  FAILED to send WhatsApp options request: {e}\n{traceback.format_exc()}")
            log("  Rolling back pending selection since you were never notified.")
            db.resolve_selection(selection_id, "failed_to_notify")
            continue

        log("=== Daily run completed — awaiting your topic/draft pick ===")
        return

    log("No item succeeded through generation/safety checks. Nothing sent for selection today.")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"FATAL ERROR: {e}\n{traceback.format_exc()}")
        sys.exit(1)
