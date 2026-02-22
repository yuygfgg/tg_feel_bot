from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FeelConfig:
    bot_token: str
    base_image: Path
    font_path: Path | None

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

    return FeelConfig(
        bot_token=bot_token,
        base_image=base_image,
        font_path=font_path,
    )
