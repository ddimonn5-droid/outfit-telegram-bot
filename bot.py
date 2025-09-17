import os
import re
import random
import logging
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

# ====== Конфиг ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", 8443))
APP_URL = os.getenv("RENDER_EXTERNAL_URL")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== Fallback ======
FALLBACK_OUTFITS = [
    "👕 Белая футболка — https://i.imgur.com/Qr71crq.jpg",
    "👖 Синие джинсы — https://i.imgur.com/0rVeh4A.jpg",
    "👟 Кеды Converse — https://i.imgur.com/XxxnmUi.jpg",
    "🧥 Чёрная куртка — https://i.imgur.com/zR5yRZr.jpg",
    "🎒 Рюкзак — https://i.imgur.com/Zk4N1qH.jpg",
]


# ====== GPT ======
async def gpt_outfit_request(user_text: str) -> str:
    """Запрос к GPT, возвращает сырой текст"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты модный стилист. "
                        "Отвечай списком из ровно 5 пунктов: Название вещи — ссылка. "
                        "Используй реальные онлайн-магазины Zara, Lyst, Grailed, Bershka. "
                        "ВСЕГДА придумывай ссылку, даже если не уверен."
                    )
                },
                {"role": "user", "content": f"Подбери аутфит: {user_text}"}
            ],
            max_tokens=600,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        return ""


# ====== Валидация ссылок ======
async def validate_text_links(text: str) -> list[str]:
    """Возвращает список строк с рабочими ссылками"""
    urls = re.findall(r"(https?://\S+)", text)
    valid_urls = set()
    async with httpx.AsyncClient(timeout=10) as client:
        for url in urls:
            try:
                r = await client.head(url, follow_redirects=True)
                if r.status_code == 200:
                    valid_urls.add(url)
            except Exception:
                continue

    lines = text.splitlines()
    clean_lines = []
    for line in lines:
        if any(url in line for url in valid_urls):
            clean_lines.append(line)

    return clean_lines


# ====== Команда /start ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Пример: кэжуал", callback_data="casual")],
        [InlineKeyboardButton("Помощь", callback_data="help")]
    ]
    await update.message.reply_text(
        "👋 Привет! Я бот-стилист.\n\n"
        "Напиши, в каком стиле нужен аутфит (например: «уличный спорт», «офис летом», «вечеринка в стиле 90-х»).\n"
        "Я подберу тебе варианты из онлайн-магазинов.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ====== Команда /help ======
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Как пользоваться:\n\n"
        "1. Просто напиши стиль или ситуацию.\n"
        "   Примеры:\n"
        "   • «casual на каждый день»\n"
        "   • «офисный стиль летом»\n"
        "   • «вечеринка в стиле 90-х»\n\n"
        "2. Я верну список вещей со ссылками.\n"
        "3. Если часть ссылок окажется нерабочей — я дополню их базовыми рабочими ссылками."
    )


# ====== Handler сообщений ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_text("✨ Думаю над твоим образом...")

    gpt_result = await gpt_outfit_request(user_text)
    valid_lines = await validate_text_links(gpt_result)

    # добиваем до 5 пунктов fallback'ом
    while len(valid_lines) < 5:
        valid_lines.append(random.choice(FALLBACK_OUTFITS))

    reply_text = "Вот что я подобрал:\n\n" + "\n".join(valid_lines[:5])
    await update.message.reply_text(reply_text)


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
