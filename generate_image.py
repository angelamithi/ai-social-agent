"""
Image generation layer: takes the topic/content item and produces a
single AI-generated image (via OpenAI's GPT Image models), reused across
X, LinkedIn, and Facebook for that day's post.

Uses gpt-image-1-mini by default — cheap and good enough for social
posts. Swap config.IMAGE_MODEL to "gpt-image-1.5" or "gpt-image-2" for
higher quality at higher cost (see README for current pricing notes).

Note: GPT Image models return base64-encoded image data, not a URL —
the `response_format=url` option that older DALL-E models supported is
not available here.

Images are stored in Postgres (via db.py), not on local disk — the cron
job that generates them and the web service that serves them to Meta
run in separate containers with no shared filesystem on Render.
"""

import base64
import os
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

import random

# A rotating pool of distinct visual styles — picked per-image so the
# feed doesn't look like the same generic "blue tech gradient" every
# single day. Each is specific enough to actually steer the model
# toward a real visual concept instead of defaulting to stock-photo
# abstraction.
IMAGE_STYLE_POOL = [
    "warm flat-design illustration with bold, friendly shapes and a limited "
    "2-3 color palette (think modern editorial illustration, not corporate "
    "tech-stock-photo style) — approachable and human, not sterile or cold",

    "playful isometric-style illustration showing a small scene or everyday "
    "object reimagined to represent the idea (e.g. a piggy bank, a road, a "
    "tool, a maze) — concrete and relatable rather than abstract circuitry",

    "bold, high-contrast poster-style illustration with strong shapes and "
    "confident color blocking, like a print magazine cover — punchy and "
    "memorable, avoid generic glowing-network or circuit-board imagery",

    "warm hand-drawn / sketchbook-style illustration with visible line work "
    "and texture, giving it a human, approachable, slightly imperfect feel "
    "rather than a slick corporate render",

    "minimalist scene-based illustration depicting a simple, everyday "
    "metaphor for the concept (a person at a crossroads, a key unlocking "
    "something, a small robot helper) — favor a clear visual metaphor over "
    "abstract data/network imagery",
]

IMAGE_SYSTEM_GUIDANCE = """Create an illustration for a social media post that helps \
ordinary, non-technical people understand a topic in AI, blockchain, crypto, or AI \
agents. The goal is to make the topic feel approachable and human — NOT like generic \
corporate tech stock art (avoid: glowing blue circuit boards, generic floating \
network nodes, generic robot hands, stock-photo-style globes/grids — these are \
overused and feel cold/impersonal).

Required style for this image: {style}

Find a concrete, specific visual idea tied to the actual topic below — a scene, \
metaphor, or moment that represents what's happening, the way a good editorial \
illustration for a newspaper explainer article would. Prefer one clear, specific idea \
over busy abstract tech imagery.

Avoid: any real company logos, real people's faces, readable body text/numbers that \
could be mistaken for real data, or anything resembling a chart making a price claim. \
Avoid words/text in the image entirely unless absolutely necessary. Leave the bottom \
~8% of the image relatively simple/uncluttered (plain background, sky, or open space) \
— a brand watermark will be added there afterward, so avoid placing important subject \
matter right at the bottom edge.

Topic: {title}
"""


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


def generate_image(item: dict) -> dict:
    """Generate a single image for the given content item.

    Returns a dict {"image_id": <db.py image id>, "prompt": <prompt used>}
    or None if generation failed.
    """
    style = random.choice(IMAGE_STYLE_POOL)
    prompt = IMAGE_SYSTEM_GUIDANCE.format(
        title=item.get("title", "AI and technology"),
        style=style,
    )

    if not config.OPENAI_API_KEY:
        print("[generate_image] OPENAI_API_KEY not set — skipping image generation.")
        return None

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
