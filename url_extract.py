"""
Extracts clean article text from a URL someone pastes into a WhatsApp
message (Flow B). Used so Claude can write from the real article
content instead of guessing/hallucinating details from a bare headline.

Handles:
  - Shortener/redirect links (e.g. share.google, bit.ly, t.co) by
    following HTTP redirects to the final article URL.
  - Sites that block generic scrapers by sending a browser-like
    User-Agent header.
  - Extraction failures gracefully — returns None rather than raising,
    so the caller can fall back to headline-only behavior (today's
    behavior) instead of the whole request failing.

Does NOT execute JavaScript. A small number of shortener/redirect
services use a client-side JS redirect rather than a standard HTTP
3xx — those will fail to resolve here and fall back gracefully.
"""

import re

import requests
import trafilatura

URL_PATTERN = re.compile(r"https?://\S+")

REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (compatible; AfrivanceAgent/1.0)"

# Extracted article text longer than this gets truncated before being
# handed to Claude — full articles can run thousands of words, far more
# than needed for a social post summary, and needlessly inflates token
# usage/cost on every Flow B call that includes a link.
MAX_EXTRACTED_CHARS = 6000

# If extraction succeeds but returns less than this many characters,
# treat it as a likely failure (paywall stub, cookie-consent page,
# bot-block page) rather than real article content.
MIN_USEFUL_CHARS = 200


def extract_url(text: str) -> str:
    """Find the first http(s) URL in `text`, if any. Returns None if
    no URL is present."""
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def strip_url(text: str, url: str) -> str:
    """Remove the URL from the message text, leaving just the
    headline/topic portion the person typed alongside it."""
    return text.replace(url, "").strip()


def fetch_article(url: str) -> dict:
    """Fetch and extract clean article text from a URL.

    Returns {"text": str, "final_url": str} on success, or None if
    fetching/extraction failed or produced too little usable content.
    Never raises — all failures are caught and logged, returning None,
    so callers can fall back to headline-only behavior gracefully.
    """
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        print(f"[url_extract] Timed out after {REQUEST_TIMEOUT_SECONDS}s fetching {url}")
        return None
    except Exception as e:
        print(f"[url_extract] Failed to fetch {url}: {e}")
        return None

    final_url = response.url  # the actual URL after following redirects

    try:
        extracted = trafilatura.extract(response.text, url=final_url)
    except Exception as e:
        print(f"[url_extract] trafilatura extraction raised for {final_url}: {e}")
        return None

    if not extracted or len(extracted.strip()) < MIN_USEFUL_CHARS:
        print(f"[url_extract] Extraction produced too little content "
              f"({len(extracted.strip()) if extracted else 0} chars) for {final_url} "
              f"— likely a paywall, bot-block, or JS-rendered page.")
        return None

    text = extracted.strip()
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS].rsplit(" ", 1)[0] + "..."

    return {"text": text, "final_url": final_url}
