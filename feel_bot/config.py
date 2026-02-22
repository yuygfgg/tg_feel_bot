from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FeelConfig:
    bot_token: str
    base_image: Path
    font_path: Path | None
    sticker_salt: str | None = None
    max_text_len: int = 80
    render_concurrency: int = 2
    # (x0, y0, x1, y1)
    text_box: tuple[int, int, int, int] = (0, 377, 350, 440)


def load_config() -> FeelConfig:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    base_image = Path(os.environ.get("FEEL_BASE_IMAGE", "./pic.jpeg")).expanduser()

    font_path_raw = os.environ.get("FEEL_FONT_PATH")
    font_path = (
        Path(font_path_raw).expanduser()
        if font_path_raw and font_path_raw.strip()
        else None
    )

    sticker_salt = os.environ.get("FEEL_STICKER_SALT")
    sticker_salt = (
        sticker_salt.strip() if sticker_salt and sticker_salt.strip() else None
    )

    def _read_int(name: str, default: int, *, min_v: int, max_v: int) -> int:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            v = int(raw)
        except ValueError as e:
            raise RuntimeError(f"Invalid {name}: must be int") from e
        if v < min_v or v > max_v:
            raise RuntimeError(f"Invalid {name}: must be in [{min_v}, {max_v}]")
        return v

    max_text_len = _read_int("FEEL_MAX_TEXT_LEN", 80, min_v=1, max_v=256)
    render_concurrency = _read_int("FEEL_RENDER_CONCURRENCY", 2, min_v=1, max_v=16)

    return FeelConfig(
        bot_token=bot_token,
        base_image=base_image,
        font_path=font_path,
        sticker_salt=sticker_salt,
        max_text_len=max_text_len,
        render_concurrency=render_concurrency,
    )
