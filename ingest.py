"""
Ingestion layer: pulls candidate content from RSS feeds and the manual
topic queue. Returns a unified list of "items" for the generation layer.
"""

import os
import feedparser
from datetime import datetime, timedelta, timezone

import config
import db


def fetch_rss_items(max_per_feed: int = 10) -> list:
    """Pull recent entries from configured RSS feeds, filtered by relevance."""
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)  # only fresh news

    for feed_url in config.RSS_SOURCES:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[ingest] Failed to fetch {feed_url}: {e}")
            continue

        for entry in feed.entries[:max_per_feed]:
            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            link = entry.get("link", "")

            # Relevance filter — AI is the primary topic. A story must
            # mention something AI-related to qualify at all. Blockchain/
            # crypto terms alone are NOT sufficient on their own (that's
            # what was letting pure crypto-market news dominate before) —
            # they only matter as a secondary angle on an AI story (e.g.
            # "AI agents executing on-chain transactions").
            text_blob = f"{title} {summary}".lower()
            has_ai_match = any(kw in text_blob for kw in config.AI_KEYWORDS)
            if not has_ai_match:
                continue

            # Recency filter (best-effort; skip if no parseable date)
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue

            if db.is_duplicate(title, source_url=link):
                continue

            items.append({
                "source": "rss",
                "title": title,
                "summary": summary,
                "url": link,
            })

    return items


def fetch_manual_topics() -> list:
    """Read and consume topics from the manual queue file.

    Each non-empty line is one topic. Lines are removed after being read
    so the same manual topic isn't reused daily.
    """
    if not os.path.exists(config.MANUAL_TOPICS_FILE):
        return []

    with open(config.MANUAL_TOPICS_FILE, "r") as f:
        raw_lines = [line.rstrip("\n") for line in f]

    # Keep comments/blank lines in place (so the file stays self-documenting)
    # but only treat real content lines as topics.
    content_line_indices = [
        i for i, line in enumerate(raw_lines)
        if line.strip() and not line.strip().startswith("#")
    ]

    if not content_line_indices:
        return []

    # Take the first content line only (we post 1x/day/platform —
    # no need to drain the queue) and remove just that line, leaving
    # comments and the rest of the queue untouched.
    first_idx = content_line_indices[0]
    topic = raw_lines[first_idx].strip()
    remaining_lines = raw_lines[:first_idx] + raw_lines[first_idx + 1:]

    with open(config.MANUAL_TOPICS_FILE, "w") as f:
        f.write("\n".join(remaining_lines) + ("\n" if remaining_lines else ""))

    return [{"source": "manual", "title": topic, "summary": "", "url": None}]


def get_daily_items() -> list:
    """Combine manual topics (priority) with RSS items."""
    manual = fetch_manual_topics()
    rss = fetch_rss_items()
    return manual + rss
