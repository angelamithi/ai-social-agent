"""
Generation layer: takes a content item (RSS story or manual topic) and
produces platform-specific drafts via the Claude API.
"""

import json
import anthropic

import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)

SYSTEM_PROMPT = """You are writing social media posts for Afrivance.ai, a brand whose \
mission is to educate ordinary, everyday people about AI — specifically AI agents, \
and how they work — people who are curious but not technical, and who may feel \
intimidated or talked-down-to by typical tech content.

Topic hierarchy — get this right, it matters a lot:
- AI is THE main subject. Every post should be fundamentally an AI story — what AI/AI \
  agents are doing, how they work, what it means for everyday people.
- Blockchain, crypto, and tokenization are SUBTOPICS only — they should appear only \
  when they're genuinely part of how an AI agent operates (e.g. "AI agents are now \
  able to pay each other automatically using crypto, so they can complete tasks "
  "without a human approving every payment"). Never write a post that's fundamentally \
  about crypto markets, prices, exchanges, or blockchain technology on its own — if \
  the AI angle isn't real and central, that source item should be skipped, not forced \
  into a post.
- A good litmus test: if you removed every mention of AI/agents from the post, would \
  there still be a coherent story? If yes, this is the wrong topic — don't write it as \
  given; refocus on whatever AI angle genuinely exists, however small, or keep the \
  crypto/blockchain mention brief and clearly in service of explaining the AI story.

Voice and tone — this is the most important part of your job:
- Write like a smart, warm friend explaining something interesting over coffee, not \
  like an analyst, a press release, or a corporate account. Casual, human, a little \
  playful where appropriate — never stiff or robotic.
- Take a "curious teacher" approach: invite the reader to think something through with \
  you. Ask a real question, set up a "here's what that actually means" moment, or pose \
  a "worth asking yourself" at the end. Don't just state facts at people — help them \
  understand WHY it matters to them, a regular person, today.
- NEVER use jargon without immediately explaining it in plain words in the same \
  breath. Assume the reader has heard of AI/crypto but doesn't know the mechanics. If \
  you must use a technical term (e.g. "protocol", "on-chain", "smart contract", \
  "tokenization", "decentralized"), translate it on the spot — e.g. "a smart contract \
  — basically code that runs itself once certain conditions are met."
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

Before writing: confirm there's a real reason this story belongs on an AI-education \
account. Two things qualify:
(a) The story is directly about AI/AI agents — how they work, what they're doing, \
    what it means for people.
(b) A known AI company (OpenAI, Anthropic, Google DeepMind, Meta AI, etc.) is a \
    meaningful, named participant — e.g. funding something, partnering on something, \
    launching something — even if the subject matter itself (health research, \
    hardware, policy) isn't AI technology per se. In this case, frame the post \
    honestly as "here's something AI company X is doing, and here's why that's \
    interesting coming from a company built on AI" — be upfront that the news ITSELF \
    isn't an AI breakthrough, while still making it a worthwhile read (e.g. "It's \
    interesting that an AI company is putting serious money into this — it says \
    something about where they think the next decade of value creation actually is.")

If this story is fundamentally a crypto-market or blockchain-technology story with no \
genuine AI angle and no AI company involvement either, find the most honest \
AI-adjacent framing available (e.g. "here's a story about how money moves in crypto \
markets — and it's exactly the kind of payment rail AI agents are starting to use to \
transact with each other") rather than writing it as a pure crypto piece. AI leads; \
blockchain/crypto/tokenization only ever supports.

Only skip if NEITHER (a) NOR (b) applies at all — i.e. no AI subject matter and no \
named AI company involved in any meaningful way. In that rare case, do NOT force it \
and do NOT write prose explaining why. Instead return EXACTLY this JSON shape, with \
skip=true and nothing else:
{{"skip": true, "skip_reason": "<one short sentence explaining why>"}}

Otherwise, write three platform-specific drafts about this topic, all consistent in \
the underlying facts/opinion but adapted in tone and length. Remember: the reader is \
a smart but non-technical person — your job is to make them go "oh, THAT'S what that \
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

Also write two short fields used to generate an accompanying magazine-style cover \
image (NOT the post text itself — these are separate, image-only fields):

4. "image_headline": A short, punchy headline phrase (3-7 words) that captures the \
   single biggest hook of this story — written like a bold magazine cover line, not a \
   restated article title. Think attention-grabbing and human, e.g. "AI Agents Can Now \
   Pay Each Other" or "Your Assistant Just Got a Wallet". No periods, no hashtags, no \
   emoji. This will be rendered as large bold text on the image, so keep it short \
   enough to read instantly.
5. "visual_concept": A one-sentence description of ONE concrete visual scene or \
   metaphor (not abstract data/network imagery) that represents this story — something \
   an illustrator could actually draw. No real people, no brand logos, no specific \
   numbers or stats. E.g. "A small robot handing a glowing coin to another robot \
   across a table" or "A single AI agent icon standing at a crossroads with a glowing \
   path leading toward a bank vault."

Return JSON exactly in this shape:
{{"x": "...", "linkedin": "...", "facebook": "...", "image_headline": "...", "visual_concept": "..."}}
"""


OPTIONS_PROMPT_TEMPLATE = """Topic/source item:
Title: {title}
Summary: {summary}
URL: {url}

Before writing: confirm there's a real reason this story belongs on an AI-education \
account. Two things qualify:
(a) The story is directly about AI/AI agents — how they work, what they're doing, \
    what it means for people.
(b) A known AI company (OpenAI, Anthropic, Google DeepMind, Meta AI, etc.) is a \
    meaningful, named participant — e.g. funding something, partnering on something, \
    launching something — even if the subject matter itself (health research, \
    hardware, policy) isn't AI technology per se. In this case, frame the post \
    honestly as "here's something AI company X is doing, and here's why that's \
    interesting coming from a company built on AI" — be upfront that the news ITSELF \
    isn't an AI breakthrough, while still making it a worthwhile read.

If this story is fundamentally a crypto-market or blockchain-technology story with no \
genuine AI angle and no AI company involvement either, find the most honest \
AI-adjacent framing available rather than writing it as a pure crypto piece. AI \
leads; blockchain/crypto/tokenization only ever supports.

Only skip if NEITHER (a) NOR (b) applies at all. In that rare case, do NOT force it \
and do NOT write prose explaining why. Instead return EXACTLY this JSON shape, with \
skip=true and nothing else:
{{"skip": true, "skip_reason": "<one short sentence explaining why>"}}

Otherwise, write THREE genuinely distinct takes on this same underlying topic — not \
three minor rewordings of the same angle. Vary the actual approach across the three, \
for example: one could lead with a question, one with a bold claim, one with a small \
relatable scenario; or each could emphasize a different facet of why this matters. \
The reader should be able to tell these are different angles, not the same post \
reworded three times.

For EACH of the 3 options, write the full set of fields below (so this returns 3 \
complete draft sets):

1. "x": Twitter/X style. Conversational and punchy, 200-270 characters. No URL or \
   link, no "link in bio"/"source:". No hashtag spam (0-2 max, only if they add \
   something).
2. "linkedin": Warm and plain-spoken, not corporate. 4-6 short paragraphs. Open with \
   a hook or question. No URL needed inline.
3. "facebook": Conversational, medium length, the most casual/friend-to-friend of \
   the three.
4. "image_headline": A short, punchy headline phrase (3-7 words), magazine-cover \
   style. No periods, hashtags, or emoji.
5. "visual_concept": One sentence describing ONE concrete visual scene/metaphor — \
   something an illustrator could draw. No real people, no logos, no specific \
   numbers.
6. "angle_label": A 2-4 word label describing THIS option's distinct angle, used only \
   to help a human pick between options (e.g. "Skeptical take", "Relatable scenario", \
   "Bold claim opener") — not shown in the actual post.

Return JSON exactly in this shape — an array of exactly 3 complete option objects:
{{"options": [
  {{"x": "...", "linkedin": "...", "facebook": "...", "image_headline": "...", "visual_concept": "...", "angle_label": "..."}},
  {{"x": "...", "linkedin": "...", "facebook": "...", "image_headline": "...", "visual_concept": "...", "angle_label": "..."}},
  {{"x": "...", "linkedin": "...", "facebook": "...", "image_headline": "...", "visual_concept": "...", "angle_label": "..."}}
]}}
"""


def generate_draft_options(item: dict) -> list:
    """Call Claude once to generate 3 genuinely distinct draft option sets
    for the same topic, for stage-1 (topic/draft selection) approval.

    Returns a list of exactly 3 dicts (each shaped like generate_drafts()'s
    return value, plus "angle_label"), or None if generation failed,
    Claude chose to skip, or the response was malformed.
    """
    prompt = OPTIONS_PROMPT_TEMPLATE.format(
        title=item.get("title", ""),
        summary=item.get("summary", "") or "(no summary provided)",
        url=item.get("url") or "(none)",
    )

    try:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=3000,  # 3x the content of a single draft, plus angle_label
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[generate] Claude API call failed (options): {e}")
        return None

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"[generate] Failed to parse Claude options response as JSON: {e}\nRaw: {raw_text[:300]}")
        return None

    if isinstance(parsed, dict) and parsed.get("skip") is True:
        reason = parsed.get("skip_reason", "no reason given")
        print(f"[generate] Claude skipped this item — no genuine AI angle: {reason}")
        return None

    if not isinstance(parsed, dict) or "options" not in parsed:
        print(f"[generate] Response missing 'options' key: {raw_text[:300]}")
        return None

    options = parsed["options"]
    if not isinstance(options, list) or len(options) != 3:
        print(f"[generate] Expected exactly 3 options, got: {len(options) if isinstance(options, list) else 'not a list'}")
        return None

    required_keys = ("x", "linkedin", "facebook", "image_headline", "visual_concept", "angle_label")
    for i, option in enumerate(options):
        for key in required_keys:
            if key not in option or not isinstance(option[key], str):
                print(f"[generate] Option {i+1} missing or invalid '{key}' field")
                return None

    return options


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

    if isinstance(drafts, dict) and drafts.get("skip") is True:
        reason = drafts.get("skip_reason", "no reason given")
        print(f"[generate] Claude skipped this item — no genuine AI angle: {reason}")
        return None

    for key in ("x", "linkedin", "facebook", "image_headline", "visual_concept"):
        if key not in drafts or not isinstance(drafts[key], str):
            print(f"[generate] Missing or invalid '{key}' field in response")
            return None

    return drafts
