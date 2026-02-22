from __future__ import annotations

import asyncio
import logging
import os
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


def _help_text() -> str:
    return "用法:\n" "- /feel 吃牛排\n" "- Inline: 在任意聊天输入 @bot_name 吃牛排"


async def _ensure_sticker_set(bot, user_id: int) -> str:
    """Ensure the user's sticker set exists, return its name."""
    bot_username = bot.username or bot.name
    # Telegram sticker set name: must end in _by_<bot_username>
    # We use f_{user_id}_by_{bot_username} to make it unique per user.
    set_name = f"f_{user_id}_by_{bot_username}"

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
        title=f"Feel {user_id}",
        stickers=[input_sticker],
    )
    
    # Delete the placeholder sticker so the set is empty
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
    text = normalize_first_line(" ".join(context.args))
    if not text:
        if update.message:
            await update.message.reply_text(_help_text())
        return

    if update.message:
        await update.message.chat.send_action(ChatAction.CHOOSE_STICKER)

    try:
        webp = render_feel_image(
            base_image_path=cfg.base_image,
            first_line=text,
            text_box=cfg.text_box,
            font_path=cfg.font_path,
        )
    except Exception as e:
        log.exception("render failed")
        if update.message:
            await update.message.reply_text(f"生成失败: {e}")
        return

    if update.message:
        await update.message.reply_sticker(sticker=webp)


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: FeelConfig = context.bot_data["cfg"]
    cache: InlineCache = context.bot_data["inline_cache"]

    iq = update.inline_query
    if iq is None:
        return

    query = normalize_first_line(iq.query)
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
        webp = render_feel_image(
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
                input_message_content=InputTextMessageContent(f"生成失败: {e}"),
                description=str(e),
            )
        ]
        await iq.answer(results, cache_time=0, is_personal=True)
        return

    try:
        set_name = await _ensure_sticker_set(context.bot, user_id)
        file_id = await _upload_sticker_via_set(
            bot=context.bot,
            user_id=user_id,
            webp=webp,
            set_name=set_name,
        )
    except Exception as e:
        log.exception("failed to upload sticker")
        msg = str(e)
        if any(x in msg.lower() for x in ["peer_id_invalid", "bot was blocked", "user not found"]):
            error_title = "请先向 Bot 发送 /start"
            error_text = "Bot 无法为您创建贴纸包，请先在私聊中发送 /start。"
        else:
            error_title = "贴纸上传失败"
            error_text = f"贴纸上传失败: {e}"

        results = [
            InlineQueryResultArticle(
                id="upload_failed",
                title=error_title,
                input_message_content=InputTextMessageContent(error_text),
                description=str(e),
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
