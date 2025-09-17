import os
import re
import random
import logging
import asyncio
import httpx
import requests
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ====== Конфиг ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
XAI_API_KEY = os.getenv("XAI_API_KEY")
PORT = int(os.getenv("PORT", 8443))
APP_URL = os.getenv("RENDER_EXTERNAL_URL")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== Надёжные fallback ======
FALLBACK_OUTFITS = [
    "👕 Футболка — https://www.zara.com/",
    "👖 Джинсы — https://www.levi.com/",
    "👟 Кроссовки — https://www.converse.com/",
    "🧥 Куртка — https://www.hm.com/",
    "🎒 Рюкзак — https://www.zalando.com/"
]

# ====== Утилиты ======
URL_RE = re.compile(r"(https?://[^\s\)\]\>]+)")
IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|bmp)(?:$|\?)", re.I)
PRODUCT_HINTS = ["product", "item", "shop", "p/", "id=", "ref"]

def extract_urls(text: str):
    return URL_RE.findall(text or "")

async def validate_url(url: str, timeout: int = 8) -> bool:
    """Проверка: страница существует и не является картинкой."""
    if IMAGE_EXT_RE.search(url):
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, follow_redirects=True)
            if r.status_code == 200:
                ctype = r.headers.get("content-type", "")
                if "text/html" in ctype:
                    return True
    except Exception:
        return False
    return False

async def filter_and_validate_urls(candidates: list[str], needed: int = 5) -> list[str]:
    """Оставляем только рабочие product-ссылки."""
    out, seen = [], set()
    sem = asyncio.Semaphore(20)

    async def check(url):
        async with sem:
            if not any(h in url.lower() for h in PRODUCT_HINTS):
                return None
            ok = await validate_url(url)
            return url if ok else None

    tasks = [asyncio.create_task(check(u)) for u in candidates]
    for task in asyncio.as_completed(tasks):
        res = await task
        if res and res not in seen:
            seen.add(res)
            out.append(res)
            if len(out) >= needed:
                break
    return out

# ====== Grok ======
def grok_call(payload: dict) -> dict:
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

async def grok_outfit_request(user_text: str) -> dict:
    payload = {
        "model": "grok-4",
        "messages": [
            {"role": "system", "content": (
                "Ты модный стилист. Подбирай строго 5 вещей (👕 футболка, 👖 джинсы, 👟 кроссовки, 🧥 куртка, 🎒 аксессуар). "
                "Формат ответа: Emoji Название — ссылка. "
                "Ссылка должна вести только на страницу реального товара (product page) "
                "в магазинах Zara, Lyst, Grailed, Bershka, ASOS, H&M, Zalando. "
                "Не используй главные страницы, не придумывай урлы."
            )},
            {"role": "user", "content": f"Подбери аутфит: {user_text}"}
        ],
        "max_tokens": 600,
        "search_parameters": {
            "mode": "on",
            "return_citations": True,
            "max_search_results": 12,
            "sources": [{"type": "web"}]
        },
        "temperature": 0.2
    }
    return grok_call(payload)

# ====== Команды ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Пример: кэжуал", callback_data="casual")],
                [InlineKeyboardButton("Помощь", callback_data="help")]]
    await update.message.reply_text(
        "👋 Привет! Я бот-стилист (на Grok).\n"
        "Напиши стиль (например: «уличный спорт», «офис летом», «вечеринка в стиле 90-х»), "
        "и я подберу тебе аутфит.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Напиши стиль или ситуацию, и я верну список из 5 вещей со ссылками на реальные товары.\n\n"
        "Формат:\n👕 Футболка — ссылка\n👟 Кроссовки — ссылка\n..."
    )

# ====== Сообщения ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    await update.message.reply_text("✨ Ищу реальные лоты в магазинах...")

    try:
        raw = await grok_outfit_request(user_text)
    except Exception as e:
        logger.exception("Ошибка Grok")
        await update.message.reply_text("Ошибка при поиске, попробуй позже.")
        return

    choice = (raw.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""

    candidates = extract_urls(text)
    validated = await filter_and_validate_urls(candidates, needed=5)

    # дополняем fallback, если мало
    while len(validated) < 5:
        fallback = FALLBACK_OUTFITS[len(validated) % len(FALLBACK_OUTFITS)]
        validated.append(fallback)

    # строим ответ
    lines = []
    for line in text.splitlines():
        for url in validated:
            if url in line:
                lines.append(line.strip())
    # если Grok не дал формат, то строим сами
    if not lines:
        icons = ["👕", "👖", "👟", "🧥", "🎒"]
        for i, url in enumerate(validated[:5]):
            lines.append(f"{icons[i]} Товар — {url}")

    reply = "Вот твой аутфит:\n\n" + "\n".join(lines[:5])
    await update.message.reply_text(reply)

# ====== Main ======
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен в режиме Webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{APP_URL}/{TELEGRAM_BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
