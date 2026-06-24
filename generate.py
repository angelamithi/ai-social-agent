"""
Generation layer: takes a content item (RSS story or manual topic) and
produces platform-specific drafts via the Claude API.
"""

import json
import anthropic

import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are a social media content writer for a tech professional \
who posts daily about AI, blockchain, crypto, and AI agents specifically.

Rules you must follow:
- Never state price predictions, price targets, or imply guaranteed returns.
- Never give financial advice or tell readers to buy/sell anything.
- Be factual and specific; cite the source naturally if a URL is given.
- No hype language ("to the moon", "100x", "guaranteed").
- Each platform has a different voice (see instructions per platform).
- Output ONLY valid JSON, no preamble, no markdown code fences.
"""

USER_PROMPT_TEMPLATE = """Topic/source item:
Title: {title}
Summary: {summary}
URL: {url}

Write three platform-specific drafts about this topic, all consistent in the \
underlying facts/opinion but adapted in tone and length:

1. "x": Twitter/X style. Punchy, max 270 characters. Do NOT include a URL or \
   link of any kind — text only, no "link in bio" or "source:" either. \
   No hashtag spam (0-2 relevant hashtags max).
2. "linkedin": Professional, thoughtful, 3-5 short paragraphs, can include a \
   point of view or analysis, no URL needed inline (will be added separately).
3. "facebook": Conversational, medium length (shorter than LinkedIn, longer \
   than X), accessible to a general audience.

Return JSON exactly in this shape:
{{"x": "...", "linkedin": "...", "facebook": "..."}}
"""


def generate_drafts(item: dict) -> dict:
    """Call Claude to generate platform drafts for a single content item.

    Returns a dict like {"x": ..., "linkedin": ..., "facebook": ...}
    or None if generation failed / response was unparseable.
    """
    prompt = USER_PROMPT_TEMPLATE.format(
        title=item.get("title", ""),
        summary=item.get("summary", "") or "(no summary provided)",
        url=item.get("url") or "(none)",
    )

    try:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[generate] Claude API call failed: {e}")
        return None

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    # Defensive cleanup in case the model wraps output in code fences anyway
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        drafts = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"[generate] Failed to parse Claude response as JSON: {e}\nRaw: {raw_text[:300]}")
        return None

    for key in ("x", "linkedin", "facebook"):
        if key not in drafts or not isinstance(drafts[key], str):
            print(f"[generate] Missing or invalid '{key}' field in response")
            return None

    return drafts
