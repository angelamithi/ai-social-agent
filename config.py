"""
Central configuration for the social media agent.
Edit this file to tune sources, limits, and safety rules.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Kill switch — set ENABLED=false in .env to stop ALL autonomous posting
# instantly without touching cron or code.
# ---------------------------------------------------------------------------
ENABLED = os.getenv("ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# API Keys (loaded from .env — never hardcode keys here)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")

# WhatsApp Cloud API (Meta) — used for the manual approval step
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")  # for webhook handshake
APPROVER_PHONE_NUMBER = os.getenv("APPROVER_PHONE_NUMBER")  # your number, E.164 format e.g. 2547XXXXXXXX
# Name of the pre-approved WhatsApp template used to send the approval
# request (image header + quick reply buttons). See README for setup.
WHATSAPP_APPROVAL_TEMPLATE_NAME = os.getenv("WHATSAPP_APPROVAL_TEMPLATE_NAME", "post_approval_request")
# Must exactly match the language the template was actually created/
# approved under in WhatsApp Manager — check via the message_templates
# API endpoint if unsure, since the UI's "English (US)" label doesn't
# always mean the underlying code is "en_US"; it can be plain "en".
WHATSAPP_TEMPLATE_LANGUAGE = "en"
# Name of the named body variable used in the template, e.g. {{post_summary}}.
# Must exactly match whatever name you used when creating the template in
# WhatsApp Manager (Meta's newer template builder requires named, not
# numbered, placeholders — lowercase letters/numbers/underscores only).
WHATSAPP_BODY_PARAM_NAME = os.getenv("WHATSAPP_BODY_PARAM_NAME", "post_summary")
# Public base URL this server is reachable at (e.g. https://yourapp.onrender.com
# or an ngrok URL during local testing) — used to build the image URL Meta
# needs to fetch for the template header, since WhatsApp template media
# must be a publicly accessible link, not a local file.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
WEBHOOK_PORT = int(os.getenv("PORT", os.getenv("WEBHOOK_PORT", "8443")))  # Render sets $PORT

# Shared Postgres connection string. On Render, this is injected
# automatically when a Postgres instance is linked via an Environment
# Group shared by both the cron job and the web service. For local
# testing, set it manually in .env to any reachable Postgres instance.
DATABASE_URL = os.getenv("DATABASE_URL")
# Render's managed Postgres requires SSL ("require", the default).
# Override to "disable" in .env only for local development against a
# Postgres instance without SSL configured.
DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "require")

# Optional: where to send run summaries (kept simple — file-based by default)
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL")  # not wired up by default, see README

# ---------------------------------------------------------------------------
# Content sources
# ---------------------------------------------------------------------------
# RSS feeds covering AI, blockchain, crypto, and AI-agent topics.
# Add/remove freely. Keep the list reasonably small — quality over quantity.
RSS_SOURCES = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://www.theblock.co/rss.xml",
    "https://feeds.feedburner.com/TechCrunch/artificial-intelligence",
    "https://www.technologyreview.com/feed/",
    "https://hnrss.org/newest?q=AI+agent",
    "https://hnrss.org/newest?q=blockchain",
]

# Manual topic queue file — add lines anytime, the agent will consume them
MANUAL_TOPICS_FILE = "topics.txt"

# Keywords used to filter generic RSS feeds for relevance
TOPIC_KEYWORDS = [
    "ai agent", "agentic", "llm", "large language model", "autonomous agent",
    "blockchain", "crypto", "web3", "smart contract", "defi",
    "artificial intelligence", "machine learning", "openai", "anthropic",
]

# ---------------------------------------------------------------------------
# Posting limits / rate limiting
# ---------------------------------------------------------------------------
MAX_X_POSTS_PER_DAY = 1
MAX_X_POST_LENGTH = 280  # X's hard limit. No URL reserved — links are excluded from X posts (see safety.py).

# How many days of history to check against for duplicate-content detection
DEDUP_LOOKBACK_DAYS = 30

# ---------------------------------------------------------------------------
# Safety: blocklist — posts containing these (case-insensitive) are blocked
# from auto-posting and flagged in the log for manual review.
# ---------------------------------------------------------------------------
BLOCKLIST_KEYWORDS = [
    "guaranteed returns",
    "to the moon",
    "financial advice",
    "buy now",
    "sell now",
    "100x",
    "not a scam",
    "get rich",
]

# Phrases that indicate the draft is making a price prediction / financial
# advice claim — these get the post auto-rejected, not just flagged.
FINANCIAL_ADVICE_PATTERNS = [
    "will reach $", "price target", "you should buy", "you should sell",
    "guaranteed", "risk-free",
]

# ---------------------------------------------------------------------------
# Local run log — kept per-service (cron container and web service
# container each have their own). Render also captures stdout/stderr in
# its own dashboard logs regardless, so this is a convenience, not the
# only place logs live.
# ---------------------------------------------------------------------------
LOG_FILE = "run_log.txt"

# ---------------------------------------------------------------------------
# Image generation (OpenAI GPT Image)
# ---------------------------------------------------------------------------
# "gpt-image-1-mini" is cheap and fine for social posts. Upgrade to
# "gpt-image-1.5" or "gpt-image-2" for higher quality at higher cost —
# check current per-image pricing before switching, it varies a lot by tier.
IMAGE_MODEL = "gpt-image-1-mini"
IMAGE_SIZE = "1536x1024"  # landscape — fits well in X/LinkedIn/Facebook feeds
IMAGE_QUALITY = "low"  # low/medium/high — low is usually plenty for social posts
BRAND_NAME = "Afrivance.ai"  # stamped in the footer of every generated image

# ---------------------------------------------------------------------------
# Claude model used for generation
# ---------------------------------------------------------------------------
CLAUDE_MODEL = "claude-sonnet-4-6"
