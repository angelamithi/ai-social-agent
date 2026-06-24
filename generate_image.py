"""
Image generation layer: takes the topic/content item (and the already-
generated text drafts) and produces a single AI-generated infographic-
style image via OpenAI's GPT Image models, reused across X, LinkedIn,
and Facebook for that day's post.

Infographics need denser, smaller, more precise text rendering than a
simple illustration — quality is bumped to "medium" (see config.py)
since "low" quality is unreliable for this much on-image text. Model
stays on gpt-image-1-mini by default; bump config.IMAGE_MODEL to a
flagship tier (gpt-image-1.5 / gpt-image-2) if medium-quality mini
output isn't crisp enough once you see real results.

Note: GPT Image models return base64-encoded image data, not a URL —
the `response_format=url` option that older DALL-E models supported is
not available here.

Images are stored in Postgres (via db.py), not on local disk — the cron
job that generates them and the web service that serves them to Meta
run in separate containers with no shared filesystem on Render.

IMPORTANT — content safety constraints baked into this prompt:
- Never depicts real, identifiable public figures (policy requirement,
  and OpenAI's models restrict this too). Uses a generic/silhouetted
  human figure or no human figure at all instead.
- Never fabricates specific statistics. The optional stat line is only
  included if the source material actually supplied a concrete number
  — otherwise the stat box is omitted entirely rather than invented.
"""

import base64
import os
import random
import re
from io import BytesIO

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

import config
import db

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


# Rotating color/mood palettes so every infographic doesn't look
# identical — each is a complete, coherent aesthetic direction rather
# than a single color tweak, matching the "magazine infographic" brief.
INFOGRAPHIC_PALETTES = [
    "dramatic dark navy and charcoal background with bold gold and white "
    "typography accents, high-contrast and premium-feeling, like a "
    "finance/tech magazine cover",

    "deep midnight blue background with cyan and white accents, glowing "
    "soft light sources, futuristic but clean, not cluttered",

    "warm dark background (near-black with a hint of deep purple) with "
    "bright coral/orange accent color for headlines, energetic and bold",

    "rich forest-green and black background with warm gold accents, "
    "grounded and premium, editorial-magazine feel",
]


def _extract_stat_line(item: dict) -> str:
    """Look for a real, concrete number in the source summary to use as
    an infographic stat callout. Returns None if nothing concrete is
    found — we never invent a statistic that wasn't actually in the
    source material.
    """
    summary = item.get("summary", "") or ""
    # Look for patterns like "$10 million", "50%", "1 billion", "3.5 years"
    match = re.search(
        r"(\$[\d,.]+\s?(?:million|billion|trillion|M|B|T)?|\d+(?:\.\d+)?%|"
        r"\d[\d,]*\s?(?:million|billion|trillion)\b)",
        summary,
        re.IGNORECASE,
    )
    return match.group(0) if match else None


def _build_prompt(item: dict, headline: str, palette: str) -> str:
    stat = _extract_stat_line(item)
    stat_instruction = (
        f'Include one small stat callout box quoting this exact figure from '
        f'the source material: "{stat}" — render it verbatim, no other '
        f'invented numbers anywhere in the image.'
        if stat else
        "Do not include any specific numbers, statistics, or data callouts "
        "anywhere in the image — the source material didn't supply a "
        "concrete figure, so don't invent one."
    )

    return f"""Create a bold magazine-style infographic for a social media post \
explaining an AI topic to ordinary, non-technical people. Mode: infographic, \
intended for Instagram/X/LinkedIn — polished, scroll-stopping, editorial design \
quality, not a casual illustration.

Background/scene: {palette}.

Layout (top to bottom, clearly separated zones with visible structure/dividers \
like a real infographic, not a single blended illustration):

1. HEADLINE ZONE (top ~30% of the image): large, bold, high-contrast headline \
text rendered verbatim: "{headline}" — exact wording, no extra words, no \
duplicated text, no typos. Use strong sans-serif typography, mixed sizes for \
emphasis on key words, similar to a bold magazine cover headline treatment.

2. VISUAL ZONE (middle ~50%): one clear supporting visual metaphor or scene \
related to the topic — favor a concrete, specific visual idea (a robot helper, \
a network of connected devices, a simple human silhouette interacting with \
technology, an abstract data-flow concept) over generic stock-tech imagery. \
{stat_instruction} If icons are used, keep them simple, flat, and clearly \
labeled with very short (1-3 word) text only.

3. FOOTER ZONE (bottom ~10%): leave relatively plain/uncluttered — a brand \
watermark will be added here afterward programmatically, so avoid placing \
important text or subject matter right at the bottom edge.

Hard constraints:
- NEVER depict any real, identifiable named public figure — use a generic, \
anonymous, or silhouetted human figure if a person is shown at all, or skip \
human figures entirely in favor of objects/abstract visuals.
- NEVER show a price chart, price prediction, "buy/sell" signal, or anything \
resembling investment advice.
- No real company logos.
- All on-image text must be exactly as specified above — verbatim, no \
misspellings, no invented additional claims or numbers beyond what's \
explicitly given here.

Topic context: {item.get("title", "AI and technology")}
"""


def generate_image(item: dict, drafts: dict = None) -> dict:
    """Generate a single infographic-style image for the given content item.

    `drafts` (optional) should be the dict returned by generate.generate_drafts
    — when provided, the punchy X draft is used as the infographic headline
    (it's already short and attention-grabbing) instead of the raw, often
    dry RSS/source title.

    Returns a dict {"image_id": <db.py image id>, "prompt": <prompt used>}
    or None if generation failed.
    """
    if not config.OPENAI_API_KEY:
        print("[generate_image] OPENAI_API_KEY not set — skipping image generation.")
        return None

    headline_source = (drafts or {}).get("x") or item.get("title", "AI is changing fast")
    # Trim to a clean, infographic-appropriate headline length — the raw X
    # draft can run up to 270 chars, too long for a bold headline treatment.
    headline = headline_source.strip()
    if len(headline) > 90:
        headline = headline[:87].rsplit(" ", 1)[0] + "..."

    palette = random.choice(INFOGRAPHIC_PALETTES)
    prompt = _build_prompt(item, headline, palette)

    try:
        response = _get_client().images.generate(
            model=config.IMAGE_MODEL,
            prompt=prompt,
            size=config.IMAGE_SIZE,
            quality=config.IMAGE_QUALITY,
            n=1,
        )
    except Exception as e:
        print(f"[generate_image] OpenAI image API call failed: {e}")
        return None

    try:
        image_b64 = response.data[0].b64_json
        if not image_b64:
            print("[generate_image] Response did not include base64 image data.")
            return None
        image_bytes = base64.b64decode(image_b64)
    except (AttributeError, IndexError, TypeError) as e:
        print(f"[generate_image] Unexpected response shape: {e}")
        return None

    try:
        image = Image.open(BytesIO(image_bytes))
        image = _add_watermark(image)
        output_buffer = BytesIO()
        image.save(output_buffer, format="PNG")
        final_bytes = output_buffer.getvalue()
    except Exception as e:
        print(f"[generate_image] Watermarking failed, saving original image instead: {e}")
        final_bytes = image_bytes

    try:
        image_id = db.save_image(final_bytes, content_type="image/png")
    except Exception as e:
        print(f"[generate_image] Failed to save image to database: {e}")
        return None

    return {"image_id": image_id, "prompt": prompt}


def _add_watermark(image: Image.Image) -> Image.Image:
    """Stamp the brand name in a small footer bar at the bottom of the image.

    Done with Pillow (deterministic) rather than asking the image model to
    render the text — model-rendered text is unreliable for exact brand
    names (typos, wrong font, inconsistent placement).
    """
    image = image.convert("RGBA")
    width, height = image.size

    bar_height = max(int(height * 0.06), 36)
    draw = ImageDraw.Draw(image)

    # Semi-transparent dark bar so the brand text stays legible regardless
    # of what's in the underlying image.
    overlay = Image.new("RGBA", (width, bar_height), (0, 0, 0, 140))
    image.paste(overlay, (0, height - bar_height), overlay)

    draw = ImageDraw.Draw(image)
    font_size = max(int(bar_height * 0.5), 18)
    font = None
    for font_path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, font_size)
            break
    if font is None:
        font = ImageFont.load_default()

    text = config.BRAND_NAME
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    margin = int(width * 0.02)
    x = width - text_width - margin
    y = height - bar_height + (bar_height - text_height) // 2 - bbox[1]

    draw.text((x, y), text, font=font, fill=(255, 255, 255, 235))

    return image.convert("RGB")
