"""
Safety/quality gates applied before any autonomous posting.
These are deliberately conservative — when in doubt, block and log for
manual review rather than post.
"""

import re

import config
import db


# Matches http(s) URLs and bare domain patterns like "example.com/path"
URL_PATTERN = re.compile(
    r"(https?://\S+)|(\b[a-zA-Z0-9-]+\.(com|org|net|io|co|ai|xyz|gg)\b\S*)",
    re.IGNORECASE,
)


class SafetyResult:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason

    def __bool__(self):
        return self.ok


def check_no_url(text: str) -> SafetyResult:
    """X posts should never contain a URL — X charges substantially more
    per post that includes a link (~13x the plain-text rate as of 2026),
    and the generation prompt is instructed to omit them. This is the
    enforcement backstop in case the model includes one anyway."""
    match = URL_PATTERN.search(text)
    if match:
        return SafetyResult(False, f"contains a URL/link ('{match.group(0)}'), which X posts must not include")
    return SafetyResult(True)


def check_blocklist(text: str) -> SafetyResult:
    lowered = text.lower()
    for term in config.BLOCKLIST_KEYWORDS:
        if term.lower() in lowered:
            return SafetyResult(False, f"blocklisted phrase: '{term}'")
    return SafetyResult(True)


def check_financial_advice(text: str) -> SafetyResult:
    lowered = text.lower()
    for pattern in config.FINANCIAL_ADVICE_PATTERNS:
        if pattern.lower() in lowered:
            return SafetyResult(False, f"financial-advice pattern: '{pattern}'")
    return SafetyResult(True)


def check_length(text: str, max_len: int) -> SafetyResult:
    if len(text) > max_len:
        return SafetyResult(False, f"exceeds max length ({len(text)} > {max_len})")
    return SafetyResult(True)


def check_duplicate(text: str, source_url: str = None) -> SafetyResult:
    if db.is_duplicate(text, source_url=source_url):
        return SafetyResult(False, "duplicate of recent post")
    return SafetyResult(True)


def check_rate_limit(platform: str, max_per_day: int) -> SafetyResult:
    if db.posts_today(platform) >= max_per_day:
        return SafetyResult(False, f"daily post limit reached for {platform}")
    return SafetyResult(True)


def run_all_checks(text: str, platform: str, max_len: int, max_per_day: int,
                    source_url: str = None) -> SafetyResult:
    """Run every gate in sequence; return the first failure, or ok if all pass."""
    checks = [
        check_rate_limit(platform, max_per_day),
        check_length(text, max_len),
        check_no_url(text) if platform == "x" else SafetyResult(True),
        check_blocklist(text),
        check_financial_advice(text),
        check_duplicate(text, source_url=source_url),
    ]
    for result in checks:
        if not result:
            return result
    return SafetyResult(True)
