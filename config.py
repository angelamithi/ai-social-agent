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
# Name of the second WhatsApp template used for stage-1 (topic/draft
# selection) approval — shows 3 short previews with 3 quick-reply
# buttons ("Option 1"/"Option 2"/"Option 3"). Must be created and
# approved in WhatsApp Manager separately — see README.
WHATSAPP_OPTIONS_TEMPLATE_NAME = os.getenv("WHATSAPP_OPTIONS_TEMPLATE_NAME", "draft_options_request")
# How long to wait for you to pick one of the 3 draft options before
# auto-selecting option 1. The final post-approval step (image + Approve/
# Reject) still waits indefinitely — this timeout is only for the
# earlier topic/draft-selection stage.
SELECTION_TIMEOUT_HOURS = int(os.getenv("SELECTION_TIMEOUT_HOURS", "8"))
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
# RSS feeds. AI is the primary focus — most sources here are AI-first.
# Blockchain/crypto are secondary topics, only relevant through the lens
# of AI agents (e.g. agents executing on-chain transactions), not as
# standalone crypto/market news — see TOPIC_KEYWORDS and the generation
# prompt in generate.py for how that's enforced downstream.
# Add/remove freely. Keep the list reasonably small — quality over quantity.
#
# Note: the old feedburner.com TechCrunch URL was permanently dead (404)
# as of June 2026 — feedburner.com itself appears to be shutting down.
# Replaced with TechCrunch's current category feed. hnrss.org has shown
# occasional transient 502s — that's handled gracefully now (timeout +
# per-feed try/except in ingest.py), so a flaky feed just gets skipped
# for that one run rather than blocking anything.
RSS_SOURCES = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/feed/",
    "https://hnrss.org/newest?q=AI+agent",
    "https://hnrss.org/newest?q=artificial+intelligence",
    "https://hnrss.org/newest?q=LLM",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    # Secondary: AI-agents-using-crypto angle only (not general crypto news)
    "https://hnrss.org/newest?q=AI+agent+blockchain",
]

# Manual topic queue file — add lines anytime, the agent will consume them
MANUAL_TOPICS_FILE = "topics.txt"

# Keywords used to filter generic RSS feeds for relevance.
# AI_KEYWORDS: primary topic — any single match qualifies a story.
# BLOCKCHAIN_KEYWORDS: secondary — these only qualify a story when paired
# with an AI match (see ingest.py), so a pure crypto-market story (price
# moves, exchange listings, etc. with no AI angle) gets filtered out,
# while "AI agents executing blockchain transactions" still gets through.
AI_KEYWORDS = [
    "ai agent", "agentic", "llm", "large language model", "autonomous agent",
    "artificial intelligence", "machine learning", "openai", "anthropic",
    "generative ai", "neural network", "chatbot", "copilot", "claude",
    "gpt", "gemini", "multimodal",
]
BLOCKCHAIN_KEYWORDS = [
    "blockchain", "crypto", "web3", "smart contract", "defi",
    "tokenization", "on-chain", "stablecoin",
]

# ---------------------------------------------------------------------------
# Posting limits / rate limiting
# ---------------------------------------------------------------------------
# Default is 1 (the real production target). Override via the
# MAX_X_POSTS_PER_DAY env var while testing — e.g. set it to 10 in
# Render's dashboard to test multiple runs in one day, then remove the
# env var (or set it back to 1) once testing is done. No redeploy
# needed either way since Render env var changes restart the service.
MAX_X_POSTS_PER_DAY = int(os.getenv("MAX_X_POSTS_PER_DAY", "1"))
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
# "medium" — infographic-style images need denser, smaller, more precise
# text than a simple illustration; "low" quality renders text/numbers
# unreliably (garbled words, wrong digits). Bump to "high" if medium
# output still isn't crisp enough once you see real results — check
# current per-image pricing before switching, cost scales with quality.
IMAGE_QUALITY = "medium"
BRAND_NAME = "Afrivance.ai"  # stamped in the footer of every generated image

# ---------------------------------------------------------------------------
# Claude model used for generation
# ---------------------------------------------------------------------------
CLAUDE_MODEL = "claude-sonnet-4-6"
