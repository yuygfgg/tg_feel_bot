from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineQueryResultArticle,
    InlineQueryResultCachedSticker,
    InputSticker,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, RetryAfter
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler

from .config import FeelConfig, load_config
from .inline_cache import InlineCache, default_inline_cache
from .render import content_key, normalize_first_line, render_feel_image


log = logging.getLogger("feel_bot")

_LATEST_INLINE_QUERY_ID: dict[int, str] = {}
_VERIFIED_STICKER_SETS: set[str] = set()
_WARNED_STICKER_SALT: bool = False


def _help_text() -> str:
    return "用法:\n" "- /feel 吃牛排\n" "- Inline: 在任意聊天输入 @bot_name 吃牛排"


def _safe_bot_username_for_sticker_set(bot) -> str:
    # TODO: Do we actually need this?
    u = (getattr(bot, "username", None) or "").strip()
    if u:
        return u
    n = (getattr(bot, "name", None) or "bot").strip()
    return re.sub(r"[^A-Za-z0-9_]", "_", n)[:32] or "bot"


def _sticker_salt(cfg: FeelConfig) -> str:
    global _WARNED_STICKER_SALT
    if cfg.sticker_salt:
        return cfg.sticker_salt
    if not _WARNED_STICKER_SALT:
        _WARNED_STICKER_SALT = True
        log.warning(
            "FEEL_STICKER_SALT is not set; falling back to TELEGRAM_BOT_TOKEN as salt"
        )
    return cfg.bot_token


def _sticker_set_name(cfg: FeelConfig, bot, user_id: int) -> tuple[str, str]:
    """
    Returns (set_name, title). Do not embed user_id to avoid leaking a stable identifier.
    """
    bot_username = _safe_bot_username_for_sticker_set(bot)
    digest = hashlib.sha256(
        f"v1:{_sticker_salt(cfg)}:{user_id}".encode("utf-8", errors="replace")
    ).hexdigest()[:16]

    set_name = f"f_{digest}_by_{bot_username}"
    title = "Feel"
    return set_name, title


def _normalize_user_text(text: str, *, max_len: int) -> str:
    t = normalize_first_line(text)
    if not t:
        return t
    if len(t) > max_len:
        raise ValueError(f"文字太长（最多 {max_len} 字）")
    return t


async def _ensure_sticker_set(bot, user_id: int, cfg: FeelConfig) -> str:
    """Ensure the user's sticker set exists, return its name."""
    set_name, set_title = _sticker_set_name(cfg, bot, user_id)

    if set_name in _VERIFIED_STICKER_SETS:
        return set_name

    try:
        await bot.get_sticker_set(set_name)
        _VERIFIED_STICKER_SETS.add(set_name)
        return set_name
    except BadRequest:
        pass

    # If not exists, create it
    # Placeholder 1x1 white WebP
    from PIL import Image
    import io

    placeholder = io.BytesIO()
    Image.new("RGBA", (512, 512), (255, 255, 255, 0)).save(placeholder, "WEBP")
    placeholder_bytes = placeholder.getvalue()

    input_sticker = InputSticker(
        sticker=placeholder_bytes,
        emoji_list=["😊"],
        format="static",
    )

    # This requires the user to have interacted with the bot (e.g. /start)
    # so the bot has permission to create a sticker set for them.
    await bot.create_new_sticker_set(
        user_id=user_id,
        name=set_name,
        title=set_title,
        stickers=[input_sticker],
    )

    # Delete the placeholder sticker so the set is empty
    # TODO: Maybe we should keep sent tickets in the set?
    sticker_set = await bot.get_sticker_set(set_name)
    if sticker_set.stickers:
        await bot.delete_sticker_from_set(sticker_set.stickers[0].file_id)

    _VERIFIED_STICKER_SETS.add(set_name)
    return set_name


async def _upload_sticker_via_set(
    bot,
    user_id: int,
    webp: bytes,
    set_name: str,
) -> str:
    """
    Add sticker to the set, grab its file_id, then delete it from the set.
    """
    input_sticker = InputSticker(
        sticker=webp,
        emoji_list=["😊"],
        format="static",
    )

    for attempt in range(3):
        try:
            await bot.add_sticker_to_set(
                user_id=user_id,
                name=set_name,
                sticker=input_sticker,
            )
            break
        except RetryAfter as e:
            if e.retry_after > 5:
                log.warning(
                    "Rate limit wait %s s is too long, aborting to avoid query timeout",
                    e.retry_after,
                )
                raise RuntimeError(
                    f"Telegram Rate Limit hit ({e.retry_after}s), try again later."
                )
            log.warning("addStickerToSet rate limited, waiting %s s", e.retry_after)
            await asyncio.sleep(e.retry_after)
    else:
        raise RuntimeError("addStickerToSet rate limited after retries")

    sticker_set = await bot.get_sticker_set(set_name)
    # The new sticker is usually the last one
    file_id = sticker_set.stickers[-1].file_id
    sticker_file_id_for_delete = sticker_set.stickers[-1].file_id

    try:
        await bot.delete_sticker_from_set(sticker_file_id_for_delete)
    except Exception:
        log.warning("Failed to delete sticker from set (non-fatal)")

    return file_id


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(_help_text())


async def feel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: FeelConfig = context.bot_data["cfg"]
    render_sem: asyncio.Semaphore = context.bot_data["render_sem"]
    try:
        text = _normalize_user_text(" ".join(context.args), max_len=cfg.max_text_len)
    except ValueError as e:
        if update.message:
            await update.message.reply_text(str(e))
        return
    if not text:
        if update.message:
            await update.message.reply_text(_help_text())
        return

    if update.message:
        await update.message.chat.send_action(ChatAction.CHOOSE_STICKER)

    try:
        async with render_sem:
            webp = await asyncio.to_thread(
                render_feel_image,
                base_image_path=cfg.base_image,
                first_line=text,
                text_box=cfg.text_box,
                font_path=cfg.font_path,
            )
    except Exception as e:
        log.exception("render failed")
        if update.message:
            await update.message.reply_text("生成失败，请稍后再试。")
        return

    if update.message:
        await update.message.reply_sticker(sticker=webp)


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: FeelConfig = context.bot_data["cfg"]
    cache: InlineCache = context.bot_data["inline_cache"]
    render_sem: asyncio.Semaphore = context.bot_data["render_sem"]

    iq = update.inline_query
    if iq is None:
        return

    try:
        query = _normalize_user_text(iq.query, max_len=cfg.max_text_len)
    except ValueError as e:
        results = [
            InlineQueryResultArticle(
                id="too_long",
                title="文字太长",
                input_message_content=InputTextMessageContent(str(e)),
                description=str(e),
            )
        ]
        await iq.answer(results, cache_time=0, is_personal=True)
        return
    if not query:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="输入文字生成表情包",
                input_message_content=InputTextMessageContent(_help_text()),
                description="例如: @bot_name 吃牛排",
            )
        ]
        await iq.answer(results, cache_time=0, is_personal=True)
        return

    # Try to reuse cached result
    try:
        base_bytes = cfg.base_image.read_bytes()
    except Exception:
        base_bytes = b""

    key = content_key(base_bytes, query)
    cached = cache.get(key)
    if cached:
        log.info(f"Inline cache HIT for key={key[:8]}")
        results = [InlineQueryResultCachedSticker(id=key[:64], sticker_file_id=cached)]
        await iq.answer(results, cache_time=24 * 3600, is_personal=True)
        return

    log.info(f"Inline cache MISS for key={key[:8]}")

    user_id = iq.from_user.id
    _LATEST_INLINE_QUERY_ID[user_id] = iq.id

    await asyncio.sleep(0.4)

    if _LATEST_INLINE_QUERY_ID.get(user_id) != iq.id:
        return

    try:
        async with render_sem:
            webp = await asyncio.to_thread(
                render_feel_image,
                base_image_path=cfg.base_image,
                first_line=query,
                text_box=cfg.text_box,
                font_path=cfg.font_path,
            )
    except Exception as e:
        log.exception("render failed (inline)")
        results = [
            InlineQueryResultArticle(
                id="render_failed",
                title="生成失败",
                input_message_content=InputTextMessageContent("生成失败，请稍后再试。"),
                description="生成失败，请稍后再试。",
            )
        ]
        await iq.answer(results, cache_time=0, is_personal=True)
        return

    try:
        set_name = await _ensure_sticker_set(context.bot, user_id, cfg)
        file_id = await _upload_sticker_via_set(
            bot=context.bot,
            user_id=user_id,
            webp=webp,
            set_name=set_name,
        )
    except Exception as e:
        log.exception("failed to upload sticker")
        msg_lower = str(e).lower()
        if any(
            x in msg_lower
            for x in ["peer_id_invalid", "bot was blocked", "user not found"]
        ):
            error_title = "请先向 Bot 发送 /start"
            error_text = "Bot 无法为您创建贴纸包，请先在私聊中发送 /start。"
        else:
            error_title = "贴纸上传失败"
            error_text = "贴纸上传失败，请稍后再试。"

        results = [
            InlineQueryResultArticle(
                id="upload_failed",
                title=error_title,
                input_message_content=InputTextMessageContent(error_text),
                description=error_text,
            )
        ]
        try:
            await iq.answer(results, cache_time=0, is_personal=True)
        except BadRequest:
            pass
        return

    cache.put(key, file_id)
    results = [InlineQueryResultCachedSticker(id=key[:64], sticker_file_id=file_id)]
    try:
        await iq.answer(results, cache_time=24 * 3600, is_personal=True)
    except BadRequest as e:
        if "Query is too old" in str(e):
            log.warning("Inline query timed out (Query is too old)")
        else:
            log.error("Failed to answer inline query: %s", e)


def build_app(cfg: FeelConfig) -> Application:
    app = Application.builder().token(cfg.bot_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["inline_cache"] = default_inline_cache()
    app.bot_data["render_sem"] = asyncio.Semaphore(cfg.render_concurrency)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("feel", feel_cmd))
    app.add_handler(InlineQueryHandler(inline_query))
    return app


def main() -> None:
    load_dotenv(dotenv_path=Path(".env"), override=False)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config()
    if not cfg.base_image.exists():
        raise RuntimeError(f"Base image not found: {cfg.base_image}")

    app = build_app(cfg)
    log.info("bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
