from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import functools


def normalize_first_line(text: str) -> str:
    t = (text or "").strip().replace("\n", " ").replace("\r", " ")
    while "  " in t:
        t = t.replace("  ", " ")
    return t


def content_key(base_image_bytes: bytes, first_line: str) -> str:
    h = hashlib.sha256()
    h.update(b"WEBP_v2_")
    h.update(base_image_bytes)
    h.update(b"\0")
    h.update(first_line.encode("utf-8", errors="replace"))
    return h.hexdigest()


def _pick_font_path(user_font: Path | None) -> Path:
    if user_font and user_font.exists():
        return user_font

    # TODO: Add system font fallback
    raise RuntimeError(
        "No usable CJK font found. Set FEEL_FONT_PATH to a .ttf/.otf/.ttc that supports Chinese."
    )


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
) -> ImageFont.FreeTypeFont:
    size = max_size
    while size >= min_size:
        font = ImageFont.truetype(str(font_path), size=size)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        if (right - left) <= max_width and (bottom - top) <= max_height:
            return font
        size -= 1
    return ImageFont.truetype(str(font_path), size=min_size)


def _truncate_to_fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    if not text:
        return text

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    if (right - left) <= max_width:
        return text

    ell = "…"

    lo, hi = 1, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        l, t, r, b = draw.textbbox((0, 0), cand, font=font)
        if (r - l) <= max_width:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


@functools.lru_cache(maxsize=1)
def _get_scaled_base_image(
    base_image_path: Path, text_box: tuple[int, int, int, int]
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """
    Loads base image and scales it down to max 512px.
    """
    base_bytes = base_image_path.read_bytes()
    im = Image.open(io.BytesIO(base_bytes)).convert("RGBA")

    w, h = im.size
    max_dim = max(w, h)

    scale_factor = 1.0
    if max_dim > 512:
        scale_factor = 512.0 / max_dim
        new_w = int(w * scale_factor)
        new_h = int(h * scale_factor)
        im = im.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)

    if scale_factor != 1.0:
        x0, y0, x1, y1 = text_box
        text_box = (
            int(x0 * scale_factor),
            int(y0 * scale_factor),
            int(x1 * scale_factor),
            int(y1 * scale_factor),
        )
    return im, text_box


def render_feel_image(
    *,
    base_image_path: Path,
    first_line: str,
    text_box: tuple[int, int, int, int] = (0, 377, 350, 440),
    font_path: Path | None = None,
) -> bytes:
    """
    Returns WEBP bytes.
    """
    first_line = normalize_first_line(first_line)
    if not first_line:
        raise ValueError("first_line is empty")

    base_im, scaled_text_box = _get_scaled_base_image(base_image_path, text_box)
    im = base_im.copy()
    draw = ImageDraw.Draw(im)

    x0, y0, x1, y1 = scaled_text_box
    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 255))

    fp = _pick_font_path(font_path)
    scale_factor = im.size[0] / base_im.size[0] if base_im.size[0] > 0 else 1.0
    pad_left_env = int(os.environ.get("FEEL_PAD_LEFT", "0"))
    pad_right_env = int(os.environ.get("FEEL_PAD_RIGHT", "0"))

    scaled_w = scaled_text_box[2] - scaled_text_box[0]
    orig_w = text_box[2] - text_box[0]
    box_scale = scaled_w / orig_w if orig_w > 0 else 1.0

    pad_left = int(pad_left_env * box_scale)
    pad_right = int(pad_right_env * box_scale)

    max_w = max(1, (x1 - x0) - pad_left - pad_right)
    max_h = max(1, (y1 - y0))

    scaled_max_size = max(10, int(52 * box_scale))
    scaled_min_size = max(10, int(16 * box_scale))

    font = _fit_font(
        draw,
        first_line,
        fp,
        max_size=scaled_max_size,
        min_size=scaled_min_size,
        max_width=max_w,
        max_height=max_h,
    )
    first_line = _truncate_to_fit(draw, first_line, font, max_w)

    x_end = x1 - pad_right
    y_mid = (y0 + y1) // 2
    draw.text((x_end, y_mid), first_line, font=font, fill=(0, 0, 0, 255), anchor="rm")

    out = io.BytesIO()
    im.save(out, format="WEBP", quality=85)  # TODO: tune quality
    return out.getvalue()
