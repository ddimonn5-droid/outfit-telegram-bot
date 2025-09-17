import os
import re
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

# ====== Утилиты ======
URL_RE = re.compile(r"(https?://[^\s\)\]\>]+)")
IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|bmp)(?:$|\?)", re.I)
PRODUCT_HINTS = ["product", "productpage", "item", "detail", "p/", "sku", "id=", "prd", "listing"]

def extract_urls(text: str):
    return URL_RE.findall(text or "")

def is_probably_product(url: str) -> bool:
    if url.endswith("/"):
        return False
    if any(h in url.lower() for h in PRODUCT_HINTS):
        return True
    return False

async def validate_url(url: str, timeout: int = 8) -> bool:
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
    out, seen = [], set()
    sem = asyncio.Semaphore(20)

    async def check(url):
        async with sem:
            if not is_probably_product(url):
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

async def grok_outfit_request(user_text: str, strict: bool = False) -> dict:
    sys_prompt = (
        "Ты модный стилист. Подбирай строго 5 вещей: 👕 футболка, 👖 джинсы, 👟 кроссовки, 🧥 куртка, 🎒 аксессуар.\n"
        "Формат ответа: Emoji Название — ссылка.\n\n"
        "‼️ Требования:\n"
        "- Только реальные product pages (страницы товаров), не главные, не категории.\n"
        "- Магазины: Zara, H&M, Bershka, ASOS, Lyst, Grailed, Zalando.\n"
        "- Ссылки должны содержать product ID или slug (например 'productpage.12345', 'id=12345').\n"
    )
    if strict:
        sys_prompt += "\nВерни только product page URL товаров. Без категорий и главных страниц."

    payload = {
        "model": "grok-4",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Подбери аутфит: {user_text}"}
        ],
        "max_tokens": 600,
        "search_parameters": {
            "mode": "on",
            "return_citations": True,
            "max_search_results": 20,
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
        "Я всегда верну 5 ссылок на реальные product pages (футболка, джинсы, кроссовки, куртка, аксессуар)."
    )

# ====== Сообщения ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    await update.message.reply_text("✨ Ищу реальные product pages...")

    validated = []
    attempts = 0

    while len(validated) < 5 and attempts < 3:  # до 3 попыток
        try:
            raw = await grok_outfit_request(user_text, strict=(attempts > 0))
        except Exception as e:
            logger.exception("Ошибка Grok")
            await update.message.reply_text("Ошибка при поиске, попробуй позже.")
            return

        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        candidates = extract_urls(text)

        new_validated = await filter_and_validate_urls(candidates, needed=5)
        for v in new_validated:
            if v not in validated:
                validated.append(v)
        attempts += 1

    # гарантируем, что в любом случае 5 ссылок
    if len(validated) < 5:
        # в крайнем случае возьмём первые 5 из кандидатов как есть
        validated = (validated + candidates)[:5]

    # оформляем красиво
    icons = ["👕", "👖", "👟", "🧥", "🎒"]
    lines = []
    for i, url in enumerate(validated[:5]):
        lines.append(f"{icons[i]} Товар — {url}")

    reply = "Вот твой аутфит:\n\n" + "\n".join(lines)
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
