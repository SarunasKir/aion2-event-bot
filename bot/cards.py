"""Images for boss pings, drawn in a style an admin describes or shows with an example image.

Claude turns the admin's instructions or example image into a small style (colors,
font, layout, decoration) once, when the style is set. Each ping is then drawn
locally with Pillow, so pings cost nothing extra and never wait on an API.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import random
import re
from dataclasses import asdict, dataclass, fields
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import fieldboss

log = logging.getLogger("aion2bot.cards")

W, H = 1200, 400
FILENAME = "boss.png"

CHOICES = {
    "font": ("sans", "serif"),
    "layout": ("left", "center"),
    "border": ("none", "thin", "double", "glow"),
    "pattern": ("none", "vignette", "rays", "stripes", "sparks"),
}
TAGLINE_LIMIT = 30
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass
class CardStyle:
    bg_top: str = "#24123d"
    bg_bottom: str = "#0b0714"
    accent: str = "#f2b84b"
    text: str = "#ffffff"
    subtext: str = "#cbbfe3"
    font: str = "serif"
    layout: str = "left"
    border: str = "glow"
    pattern: str = "rays"
    uppercase: bool = True
    tagline: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "CardStyle":
        """Build a style, keeping the default for anything missing or invalid."""
        style = cls()
        for f in fields(cls):
            value = data.get(f.name)
            if f.name in CHOICES:
                if value in CHOICES[f.name]:
                    setattr(style, f.name, value)
            elif f.name == "uppercase":
                if isinstance(value, bool):
                    style.uppercase = value
            elif f.name == "tagline":
                if isinstance(value, str):
                    style.tagline = re.sub(r"\s+", " ", value).strip()[:TAGLINE_LIMIT].strip()
            elif isinstance(value, str) and _HEX.fullmatch(value.strip()):
                setattr(style, f.name, value.strip().lower())
        return style

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class StyleError(Exception):
    """The style could not be made; the message is safe to show in Discord."""


# ---------------------------------------------------------------- drawing

_FONT_FILES = {
    ("sans", True): ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf"),
    ("sans", False): ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf"),
    ("serif", True): ("DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf", "FreeSerifBold.ttf"),
    ("serif", False): ("DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "FreeSerif.ttf"),
}


@lru_cache(maxsize=64)
def _font(family: str, bold: bool, size: int) -> ImageFont.FreeTypeFont:
    """A system font (Pillow searches the system font folders), else Pillow's built-in one."""
    for name in _FONT_FILES[(family, bold)]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _is_light(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b > 150


def _width(text: str, font) -> float:
    return font.getlength(text)


def prepare_background(image: bytes) -> bytes:
    """Crop an example image to the card's shape and store it as a JPEG. Raises StyleError."""
    try:
        with Image.open(io.BytesIO(image)) as src:
            bg = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise StyleError("I couldn't open that image. Try a PNG or JPEG.")
    out = io.BytesIO()
    bg.save(out, "JPEG", quality=85)
    return out.getvalue()


def _background(style: CardStyle, background: bytes | None) -> Image.Image:
    mask = Image.linear_gradient("L").resize((W, H))
    gradient = Image.composite(
        Image.new("RGB", (W, H), _rgb(style.bg_bottom)), Image.new("RGB", (W, H), _rgb(style.bg_top)), mask
    )
    if background:
        try:
            with Image.open(io.BytesIO(background)) as src:
                photo = src.convert("RGB").resize((W, H))
            # Tint the photo with the style's colors so the text stays readable on top of it.
            gradient = Image.blend(photo, gradient, 0.55)
        except OSError:
            log.warning("Stored card background could not be read; drawing without it")
    return gradient.convert("RGBA")


def _pattern(img: Image.Image, style: CardStyle, seed: str) -> Image.Image:
    accent = _rgb(style.accent)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if style.pattern == "rays":
        cx, cy = (W // 2, H + 120) if style.layout == "center" else (W - 160, -60)
        for i in range(18):
            a, b = i * 20, i * 20 + 8
            pts = [(cx, cy)]
            for deg in (a, b):
                rad = math.radians(deg)
                pts.append((cx + 2000 * math.cos(rad), cy + 2000 * math.sin(rad)))
            draw.polygon(pts, fill=accent + (22,))
    elif style.pattern == "stripes":
        for x in range(-H, W, 34):
            draw.line([(x, H), (x + H, 0)], fill=accent + (20,), width=10)
    elif style.pattern == "sparks":
        rng = random.Random(seed)
        for _ in range(70):
            x, y, r = rng.randrange(W), rng.randrange(H), rng.choice((1, 1, 2, 2, 3, 4))
            draw.ellipse([x - r, y - r, x + r, y + r], fill=accent + (rng.randrange(60, 200),))
        layer = Image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(3)), layer)
    if style.pattern in ("vignette", "rays", "sparks"):
        # Darken the edges so the middle stands out.
        edge = Image.new("L", (W, H), 0)
        ImageDraw.Draw(edge).ellipse([-W * 0.15, -H * 0.6, W * 1.15, H * 1.6], fill=255)
        edge = ImageOps.invert(edge.filter(ImageFilter.GaussianBlur(90)))
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shade.putalpha(edge.point(lambda v: v * 150 // 255))
        img = Image.alpha_composite(img, shade)
    return Image.alpha_composite(img, layer)


def _border(img: Image.Image, style: CardStyle) -> Image.Image:
    accent = _rgb(style.accent)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if style.border == "thin":
        draw.rectangle([12, 12, W - 13, H - 13], outline=accent + (255,), width=3)
    elif style.border == "double":
        draw.rectangle([12, 12, W - 13, H - 13], outline=accent + (255,), width=4)
        draw.rectangle([24, 24, W - 25, H - 25], outline=accent + (170,), width=1)
    elif style.border == "glow":
        draw.rectangle([16, 16, W - 17, H - 17], outline=accent + (255,), width=8)
        glow = layer.filter(ImageFilter.GaussianBlur(10))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rectangle([16, 16, W - 17, H - 17], outline=accent + (255,), width=2)
        layer = Image.alpha_composite(glow, layer)
    return Image.alpha_composite(img, layer)


def _fit_title(title: str, family: str, max_width: int) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """The boss name on one line if it fits at a readable size, else on two, else shortened."""
    for size in range(96, 51, -4):
        font = _font(family, True, size)
        if _width(title, font) <= max_width:
            return [title], font
    words = title.split()
    for size in range(68, 35, -4):
        font = _font(family, True, size)
        for cut in range(len(words) - 1, 0, -1):
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
            if all(_width(line, font) <= max_width for line in lines):
                return lines, font
    font = _font(family, True, 36)
    while title and _width(title + "…", font) > max_width:
        title = title[:-1]
    return [title.rstrip() + "…"], font


def render(
    style: CardStyle,
    kind: str,
    title: str,
    when: str,
    detail: str = "",
    background: bytes | None = None,
) -> bytes:
    """Draw a boss card: a small label (kind), the boss name, the spawn time and a detail line. PNG bytes."""
    img = _background(style, background)
    img = _pattern(img, style, title)
    img = _border(img, style)

    pad = 80
    max_width = W - 2 * pad
    title = title.upper() if style.uppercase else title
    title_lines, title_font = _fit_title(title, style.font, max_width)
    label_font = _font(style.font, True, 26)
    when_font = _font(style.font, False, 40)
    detail_font = _font(style.font, False, 30)
    tagline_font = _font(style.font, True, 28)

    # Stack label, title, time and detail, centered vertically in the space above the tagline.
    label_h = 46
    title_step = int(getattr(title_font, "size", 40) * 1.12)
    rows = [("label", label_h + 22)] + [("title", title_step)] * len(title_lines) + [("when", 58)]
    if detail:
        rows.append(("detail", 42))
    bottom = H - (100 if style.tagline else 40)
    y = 40 + max(0, (bottom - 40 - sum(h for _, h in rows)) // 2)

    center = style.layout == "center"
    x = W // 2 if center else pad
    anchor = "ma" if center else "la"
    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw, shadow = ImageDraw.Draw(text_layer), ImageDraw.Draw(shadow_layer)
    # A dark shadow behind light text, a faint light one behind dark text.
    shade = (0, 0, 0, 190) if _is_light(_rgb(style.text)) else (255, 255, 255, 120)

    def write(xy, text, font, color):
        shadow.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=shade, anchor=anchor)
        draw.text(xy, text, font=font, fill=_rgb(color) + (255,), anchor=anchor)

    label = kind.upper()
    label_w = _width(label, label_font) + 36
    left = x - label_w / 2 if center else x
    draw.rounded_rectangle([left, y, left + label_w, y + label_h], radius=label_h // 2, fill=_rgb(style.accent) + (255,))
    draw.text((left + label_w / 2, y + label_h / 2), label, font=label_font, fill=(20, 20, 20, 255) if _is_light(_rgb(style.accent)) else (255, 255, 255, 255), anchor="mm")
    y += label_h + 22
    for line in title_lines:
        write((x, y), line, title_font, style.text)
        y += title_step
    y += 6
    write((x, y), when, when_font, style.text)
    y += 52
    if detail:
        write((x, y), detail, detail_font, style.subtext)
    if style.tagline:
        tx = W // 2 if center else W - pad
        shadow.text((tx + 2, H - 70 + 2), style.tagline, font=tagline_font, fill=shade,
                    anchor="ma" if center else "ra")
        draw.text((tx, H - 70), style.tagline, font=tagline_font, fill=_rgb(style.accent) + (255,),
                  anchor="ma" if center else "ra")

    img = Image.alpha_composite(img, shadow_layer.filter(ImageFilter.GaussianBlur(3)))
    img = Image.alpha_composite(img, text_layer)
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def sample(style: CardStyle, background: bytes | None = None) -> bytes:
    """A preview card with a made-up spawn."""
    return render(style, "Field boss", "Silent Dartan", "Spawns in 10 min · 21:30 server time", "Sample zone", background)


# ---------------------------------------------------------------- designing a style with Claude

DESIGN_PROMPT = """You design the look of an image card that a Discord bot posts when a boss in the game Aion 2 is about to spawn. The card is 1200x400 pixels. It shows a small pill-shaped label (like FIELD BOSS) in the accent color, the boss name in large text, the spawn time below it, and the location in smaller text.

{source}

Pick the settings that match that style as closely as these options allow:
- bg_top, bg_bottom: background gradient colors as #rrggbb, top to bottom.
- accent: color of the label, border and decorations.
- text: color of the boss name and time. It must be easy to read on the background.
- subtext: color of the location line. Also readable on the background.
- font: "serif" for classic, fantasy, medieval or elegant styles; "sans" for modern, clean or techy styles.
- layout: "left" or "center" aligned text.
- border: "none", "thin", "double" or "glow".
- pattern: background decoration: "none", "vignette" (dark edges), "rays" (light beams), "stripes" (diagonal lines) or "sparks" (glowing dots).
- uppercase: true to write the boss name in capitals.
- tagline: a short phrase of at most 30 characters shown at the bottom of the card, only if the instructions ask for one; otherwise an empty string."""

SCHEMA = {
    "type": "object",
    "properties": {
        "bg_top": {"type": "string"},
        "bg_bottom": {"type": "string"},
        "accent": {"type": "string"},
        "text": {"type": "string"},
        "subtext": {"type": "string"},
        "font": {"type": "string", "enum": list(CHOICES["font"])},
        "layout": {"type": "string", "enum": list(CHOICES["layout"])},
        "border": {"type": "string", "enum": list(CHOICES["border"])},
        "pattern": {"type": "string", "enum": list(CHOICES["pattern"])},
        "uppercase": {"type": "boolean"},
        "tagline": {"type": "string"},
    },
    "required": ["bg_top", "bg_bottom", "accent", "text", "subtext", "font", "layout", "border", "pattern",
                 "uppercase", "tagline"],
    "additionalProperties": False,
}


def _source(instructions: str, has_image: bool) -> str:
    if has_image and instructions:
        return ("The admin attached an example image. Copy its style: colors, mood and decoration. They also gave "
                "these instructions, which win where they differ from the image:\n"
                f"<instructions>\n{instructions}\n</instructions>")
    if has_image:
        return "The admin attached an example image. Copy its style: colors, mood and decoration."
    return f"The admin described the style they want:\n<instructions>\n{instructions}\n</instructions>"


async def design_style(instructions: str, image: bytes | None = None, media_type: str = "") -> CardStyle:
    """Ask Claude for a card style matching the instructions and/or example image. Raises StyleError."""
    import anthropic

    instructions = (instructions or "").strip()[:1000]
    content = []
    if image is not None:
        if media_type not in fieldboss.IMAGE_TYPES:
            raise StyleError("Please upload a PNG, JPEG, WebP or GIF image.")
        if len(image) > fieldboss.MAX_IMAGE_BYTES:
            raise StyleError("That image is over 5 MB. Try a smaller one.")
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": base64.standard_b64encode(image).decode("ascii"),
        }})
    if not content and not instructions:
        raise StyleError("Describe the style, attach an example image, or both.")
    if not fieldboss.is_configured():
        raise StyleError("Designing a style needs ANTHROPIC_API_KEY on the bot. "
                         "Without it you can still use an example image as the background.")
    content.append({"type": "text", "text": DESIGN_PROMPT.format(source=_source(instructions, image is not None))})

    client = anthropic.AsyncAnthropic()
    try:
        response = await client.beta.messages.create(
            model=fieldboss.MODEL,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise StyleError("The bot's ANTHROPIC_API_KEY was rejected. Ask the bot owner to check it.")
    except anthropic.RateLimitError:
        raise StyleError("Too many requests at once. Try again in a minute.")
    except anthropic.APIStatusError as e:
        log.error("Style design failed: %s %s", e.status_code, e.message)
        raise StyleError("Couldn't design the style right now. Try again later.")
    except anthropic.APIConnectionError:
        raise StyleError("Couldn't reach the style designer. Try again later.")

    if response.stop_reason == "refusal":
        raise StyleError("That style couldn't be made. Try different instructions or another image.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        log.error("Unexpected style reply: %r", text[:500])
        raise StyleError("Couldn't understand the designer's answer. Try again.")
    log.info("Designed card style: %s", json.dumps(data, ensure_ascii=False))
    return CardStyle.from_dict(data)
