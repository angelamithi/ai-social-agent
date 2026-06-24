"""
Generation layer: takes a content item (RSS story or manual topic) and
produces platform-specific drafts via the Claude API.
"""

import json
import anthropic

import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are writing social media posts for Afrivance.ai, a brand whose \
mission is to educate ordinary, everyday people about AI, blockchain, crypto, and AI \
agents — people who are curious but not technical, and who may feel intimidated or \
talked-down-to by typical crypto/tech content.

Voice and tone — this is the most important part of your job:
- Write like a smart, warm friend explaining something interesting over coffee, not \
  like an analyst, a press release, or a corporate account. Casual, human, a little \
  playful where appropriate — never stiff or robotic.
- Take a "curious teacher" approach: invite the reader to think something through with \
  you. Ask a real question, set up a "here's what that actually means" moment, or pose \
  a "worth asking yourself" at the end. Don't just state facts at people — help them \
  understand WHY it matters to them, a regular person, today.
- NEVER use jargon without immediately explaining it in plain words in the same \
  breath. Assume the reader has heard of crypto/AI but doesn't know the mechanics. If \
  you must use a technical term (e.g. "protocol", "on-chain", "smart contract", \
  "decentralized"), translate it on the spot — e.g. "a smart contract — basically code \
  that runs itself once certain conditions are met."
- Avoid sounding like a press release, a LinkedIn thought-leader post, or a textbook. \
  Avoid words like "leverage," "ecosystem," "paradigm," "synergy," "innovative \
  solution," "robust," "cutting-edge" — these are exactly the dead corporate phrases \
  that make people tune out.
- Write with real sentence rhythm: vary length, use short punchy sentences mixed with \
  longer explanatory ones. Contractions are good ("it's," "that's," "doesn't"). \
  Starting a sentence with "And" or "But" is fine if it reads naturally.
- It is OK, even good, to have a point of view or a little healthy skepticism — \
  "sounds impressive, but here's the catch" is far more engaging and trustworthy than \
  flat neutral reporting.

Length — posts have been coming out too short and too dense; fix that:
- Favor substance and a real explanation over brevity. Don't pad with fluff, but don't \
  rush either — give the reader enough to actually walk away understanding something \
  new, not just a headline restated.

Rules you must still follow:
- Never state price predictions, price targets, or imply guaranteed returns.
- Never give financial advice or tell readers to buy/sell anything.
- Be factual and specific; cite the source naturally if a URL is given.
- No hype language ("to the moon", "100x", "guaranteed").
- Each platform has a different voice/length (see instructions per platform), but the \
  same warm, plain-language, educational personality runs through all three.
- Output ONLY valid JSON, no preamble, no markdown code fences.
"""

USER_PROMPT_TEMPLATE = """Topic/source item:
Title: {title}
Summary: {summary}
URL: {url}

Write three platform-specific drafts about this topic, all consistent in the \
underlying facts/opinion but adapted in tone and length. Remember: the reader is a \
smart but non-technical person — your job is to make them go "oh, THAT'S what that \
means" by the end, not to impress them with technical accuracy.

1. "x": Twitter/X style. Conversational and punchy, but give the idea room to \
   breathe — aim for 200-270 characters, not the bare minimum. Do NOT include a URL \
   or link of any kind — text only, no "link in bio" or "source:" either. No hashtag \
   spam (0-2 relevant hashtags max, only if they add something).
2. "linkedin": Still warm and plain-spoken, not corporate — imagine explaining this to \
   a smart friend who works in a totally unrelated field. 4-6 short paragraphs. Open \
   with a hook or a question, not a headline restatement. Can include a point of view. \
   No URL needed inline (will be added separately).
3. "facebook": Conversational, medium length (shorter than LinkedIn, longer than X), \
   the most casual and friend-to-friend of the three — this is the "explain it like \
   I'm your smart friend" voice at its most relaxed.

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
