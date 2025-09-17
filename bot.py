# bot.py
import os
import re
import time
import logging
import html
import asyncio
import httpx
import requests
from typing import List, Optional, Tuple
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========== CONFIG ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
XAI_API_KEY = os.getenv("XAI_API_KEY")
PORT = int(os.getenv("PORT", 8443))
APP_URL = os.getenv("RENDER_EXTERNAL_URL")  # https://your-service.onrender.com

# Safety checks
if not TELEGRAM_BOT_TOKEN or not XAI_API_KEY:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN and XAI_API_KEY environment variables")

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grok_bot")

# ========== HELPERS / CONSTANTS ==========
URL_RE = re.compile(r"https?://[^\s\)\]\>]+")
IMG_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|bmp)(?:$|\?)", re.I)
PRODUCT_HINTS = ["product", "productpage", "item", "detail", "p/", "sku", "id=", "prd", "listing", "variant", "/dp/"]  # /dp/ for amazon-like
EMOJIS = ["👕", "👖", "👟", "🧥", "🎒"]

# Timeout & concurrency
HTTP_TIMEOUT = 10
CONCURRENCY = 16

def clean_url(u: str) -> str:
    """Убираем невидимые символы, HTML-энтикоды и завершающие знаки."""
    u = html.unescape(u)
    # remove zero-width spaces etc
    u = u.replace("\u200b", "").replace("\u200e", "").replace("\u200f", "")
    u = u.strip()
    # strip trailing punctuation that often attaches (.,);:)
    while len(u) and u[-1] in ".,;:)]'\"":
        u = u[:-1]
    return u

def extract_urls(text: str) -> List[str]:
    if not text:
        return []
    found = URL_RE.findall(text)
    return [clean_url(u) for u in found]

def looks_like_product(url: str) -> bool:
    url_l = url.lower()
    # must contain some product hint or long slug
    if any(h in url_l for h in PRODUCT_HINTS):
        return True
    # additional heuristic: path length bigger than 2 segments
    try:
        path = requests.utils.urlparse(url).path
        if path and len([p for p in path.split("/") if p]) >= 2:
            return True
    except Exception:
        pass
    return False

# ========== HTTP helpers ==========
async def head_or_get_ok(url: str) -> bool:
    """Проверяем, что страница доступна и не картинка."""
    if IMG_EXT_RE.search(url):
        return False
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            # try GET directly, simpler and more universal
            r = await client.get(url)
            if r.status_code == 200:
                ctype = r.headers.get("content-type", "")
                if "text/html" in ctype:
                    return True
    except Exception as e:
        logger.debug("validate error %s -> %s", url, e)
        return False
    return False

async def fetch_title(url: str) -> Optional[str]:
    """Пытаемся вытащить <title> для красивого отображения."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            text = r.text
            m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I|re.S)
            if m:
                title = html.unescape(m.group(1)).strip()
                # короткая чистка
                title = re.sub(r"\s+", " ", title)
                # урезать длинные
                return title[:120]
    except Exception as e:
        logger.debug("fetch_title error %s -> %s", url, e)
    return None

async def validate_and_title_batch(urls: List[str], needed: int) -> List[Tuple[str, Optional[str]]]:
    """Асинхронно валидируем урлы и получаем title; возвращаем (url, title)."""
    out = []
    seen = set()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def worker(u):
        async with sem:
            ok = await head_or_get_ok(u)
            if not ok:
                return None
            title = await fetch_title(u)
            return (u, title)

    tasks = [asyncio.create_task(worker(u)) for u in urls]
    for t in asyncio.as_completed(tasks):
        try:
            res = await t
        except Exception:
            res = None
        if res:
            url, title = res
            if url not in seen:
                seen.add(url)
                out.append((url, title))
                if len(out) >= needed:
                    break
    return out

# ========== Grok call ==========
def grok_call(payload: dict) -> dict:
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def build_system_prompt(strict: bool = False) -> str:
    base = (
        "Ты модный стилист. Подбирай строго 5 вещей: 👕 футболка, 👖 джинсы, 👟 кроссовки, 🧥 куртка, 🎒 аксессуар.\n"
        "Формат ответа: Emoji Название — ссылка (по одной вещи на строку).\n\n"
        "‼️ Требования к ссылкам:\n"
        "- Ссылка должна вести на страницу реального товара (product page), а не на главную или категорию.\n"
        "- Магазины: Zara, H&M, Bershka, ASOS, Lyst, Grailed, Zalando, Levi's, Converse.\n"
        "- Ссылка должна содержать product ID или slug (например: 'productpage', 'p012345', 'id=12345', '/dp/').\n"
        "- Категории/главные страницы запрещены.\n"
    )
    if strict:
        base += "СТРОГО: верни ТОЛЬКО 5 URL карточек товаров, по одному на строку, никаких описаний."
    return base

def ask_grok(user_text: str, strict: bool = False, max_search_results: int = 20) -> dict:
    payload = {
        "model": "grok-4",
        "messages": [
            {"role": "system", "content": build_system_prompt(strict=strict)},
            {"role": "user", "content": f"Подбери аутфит (футболка, джинсы, кроссовки, куртка, аксессуар): {user_text}"}
        ],
        "max_tokens": 600,
        "search_parameters": {
            "mode": "on",
            "return_citations": True,
            "max_search_results": max_search_results,
            "sources": [{"type": "web"}]
        },
        "temperature": 0.15
    }
    logger.info("Grok request strict=%s", strict)
    return grok_call(payload)

# ========== Bot handlers ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Пример: кэжуал", callback_data="casual")],
          [InlineKeyboardButton("Помощь", callback_data="help")]]
    await update.message.reply_text("Привет! Напиши стиль, и я подберу 5 реальных товарных ссылок.", reply_markup=InlineKeyboardMarkup(kb))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши коротко: например 'casual на каждый день' — верну 5 товарных страниц.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        await update.message.reply_text("Напиши, пожалуйста, что искать (пример: 'casual outfit').")
        return

    await update.message.reply_text("Ищу реальные товарные страницы — займёт пару секунд...")

    found = []            # list of (url, title)
    tried_urls = []       # raw urls we saw (for logs)
    attempts = 0
    # Будем делать до 5 попыток: 1 обычный, 3 строгих, потом расширение результатов
    while len(found) < 5 and attempts < 5:
        strict = attempts >= 1  # первая попытка - обычная, далее strict
        try:
            raw = ask_grok(user_text, strict=strict, max_search_results=20 if strict else 12)
        except Exception as e:
            logger.exception("Grok error")
            await update.message.reply_text("Ошибка при обращении к поиску, повторю попытку...")
            attempts += 1
            continue

        # 1) возьмём citations (если есть) — они часто более релевантны
        citations = raw.get("citations") or []
        # 2) парсим из message.content
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        text = html.unescape(text or "")
        urls_from_text = extract_urls(text)
        # union with citations
        candidates = []
        for u in citations + urls_from_text:
            cu = clean_and_safe(u) if False else u  # placeholder
            cu = u  # we already cleaned earlier
            if cu and cu not in candidates:
                candidates.append(cu)
        # log
        logger.info("Attempt %s: got %s candidates (citations %s, textUrls %s)", attempts, len(candidates), len(citations), len(urls_from_text))
        logger.info("Candidates sample: %s", candidates[:10])
        tried_urls.extend(candidates)

        # filter candidates -- prefer those that look like product pages
        product_candidates = [u for u in candidates if looks_like_product(u)]
        # If none look like product, keep candidates anyway (we'll validate and maybe fetch titles)
        to_check = product_candidates or candidates

        # validate and fetch titles
        validated = await validate_and_title_batch(to_check, needed=5 - len(found))
        # append unique
        for (u, title) in validated:
            if not any(u == f[0] for f in found):
                found.append((u, title))
                logger.info("Validated product: %s (title=%s)", u, title)
                if len(found) >= 5:
                    break

        # small pause between attempts to avoid rate-limits
        attempts += 1
        if len(found) < 5:
            await asyncio.sleep(0.6)

    # Если всё ещё мало — попытаемся взять дополнительные урлы из tried_urls и валидировать их (более широкая проверка)
    if len(found) < 5 and tried_urls:
        additional_candidates = [u for u in tried_urls if u not in [f[0] for f in found]]
        more = await validate_and_title_batch(additional_candidates, needed=5 - len(found))
        for (u, title) in more:
            if not any(u == f[0] for f in found):
                found.append((u, title))
                if len(found) >= 5:
                    break

    # Final guarantee: если всё ещё нет ни одной — честно сообщаем (но бот будет пытаться много раз so should find something)
    if not found:
        await update.message.reply_text("😔 Не удалось найти ни одной товарной страницы. Попробуй указать бренд/цель конкретнее (например: 'Zara белая футболка').")
        return

    # Prepare message: show emoji + title (or 'Товар') and add InlineKeyboardButtons for each URL
    lines = []
    buttons = []
    for i, (url, title) in enumerate(found[:5]):
        emoji = EMOJIS[i] if i < len(EMOJIS) else "🛍️"
        label = title or "Товар"
        # Limit label length
        if len(label) > 80:
            label = label[:77] + "..."
        lines.append(f"{emoji} {label}\n{url}")
        buttons.append([InlineKeyboardButton(f"{emoji} Открыть", url=url)])

    msg_text = "Вот твой аутфит (реальные товарные страницы):\n\n" + "\n\n".join(lines)
    # Send as single message with keyboard, disable web preview so UI not show a big preview (optional)
    await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

# ========== UTIL small helpers ==========
def looks_like_product(url: str) -> bool:
    url_l = url.lower()
    if any(h in url_l for h in PRODUCT_HINTS):
        return True
    try:
        p = requests.utils.urlparse(url).path
        if p and len([s for s in p.split("/") if s]) >= 2:
            return True
    except Exception:
        pass
    return False

def clean_and_safe(u: str) -> str:
    return html.unescape(u).replace("\u200b", "").strip()

# ========== MAIN ==========
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting webhook...")
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TELEGRAM_BOT_TOKEN, webhook_url=f"{APP_URL}/{TELEGRAM_BOT_TOKEN}")

if __name__ == "__main__":
    main()
