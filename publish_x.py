"""
Publishing layer for X (Twitter) using API v2 with OAuth 1.0a user context.

Requires a developer app with "Read and write" permissions, and the four
credentials below generated for the account you want to post as:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET

Note on cost (as of mid-2026): X moved to pay-per-use pricing. Plain text
posts cost ~$0.015 each; posts containing a URL cost ~$0.20 each. Check
the X Developer Console for current rates before relying on these numbers.

Media uploads still go through the older v1.1 media/upload endpoint —
X has not moved media upload to v2 — and the resulting media_id is then
attached to a v2 tweet.
"""

import requests
from requests_oauthlib import OAuth1

import config

X_POST_ENDPOINT = "https://api.twitter.com/2/tweets"
X_MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"


def _auth() -> OAuth1:
    return OAuth1(
        config.X_API_KEY,
        config.X_API_SECRET,
        config.X_ACCESS_TOKEN,
        config.X_ACCESS_TOKEN_SECRET,
    )


def upload_media(image_bytes: bytes) -> str:
    """Upload image bytes to X and return its media_id_string.

    Takes raw bytes rather than a file path — the cron job and the web
    service that calls this don't share a local disk on Render, so
    images are passed around as in-memory bytes (sourced from Postgres
    via db.py), not file paths.

    Raises requests.HTTPError on failure (caller should catch and log).
    """
    response = requests.post(
        X_MEDIA_UPLOAD_ENDPOINT,
        auth=_auth(),
        files={"media": ("image.png", image_bytes, "image/png")},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["media_id_string"]


def post_tweet(text: str, image_bytes: bytes = None) -> dict:
    """Post a single tweet, optionally with an attached image.

    If image_bytes is given, uploads it first and attaches it to the
    tweet. If the image upload fails, raises — caller decides whether
    to fall back to a text-only post.

    Returns the API response JSON. Raises requests.HTTPError on failure.
    """
    payload = {"text": text}

    if image_bytes:
        media_id = upload_media(image_bytes)
        payload["media"] = {"media_ids": [media_id]}

    response = requests.post(
        X_POST_ENDPOINT,
        auth=_auth(),
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
