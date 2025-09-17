import os
import json
import logging
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from openai import OpenAI

# ====== Конфиг ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", 8443))  # Render даёт порт
APP_URL = os.getenv("RENDER_EXTERNAL_URL")  # например https://outfit-bot.onrender.com

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ====== GPT ======
async def gpt_outfit_request(user_text: str):
    """Запрос к GPT для подбора аутфита. Возвращает список вещей или []"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты модный стилист. "
                        "Отвечай строго в JSON формате: "
                        "{\"items\":[{\"name\":\"Название\",\"link\":\"https://ссылка\"}]}. "
                        "Используй только реальные ссылки из Zara, Lyst, Grailed, Bershka. "
                        "Не выдумывай ссылки. Если не уверен — пропусти предмет."
                    )
                },
                {"role": "user", "content": f"Подбери аутфит: {user_text}"}
            ],
            max_tokens=400,
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        return []


# ====== Валидация ссылок ======
async def validate_links(items):
    """Проверяет ссылки (возвращает только рабочие)"""
    valid_items = []
    async with httpx.AsyncClient(timeout=10) as client:
        for it in items:
            url = it.get("link", "")
            if not url.startswith("http"):
                continue
            try:
                r = await client.head(url, follow_redirects=True)
                if r.status_code == 200:
                    valid_items.append(it)
            except Exception:
                continue
    return valid_items


# ====== Handler сообщений ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_text("✨ Думаю над твоим аутфитом...")

    items = await gpt_outfit_request(user_text)
    items = await validate_links(items)  # фильтруем битые ссылки

    if not items:
        await update.message.reply_text("😢 Не удалось найти рабочие ссылки для этого стиля.")
        return

    reply_lines = [f"👗 {it.get('name','Без названия')} → {it.get('link','')}" for it in items]
    reply_text = "Вот что я подобрал:\n\n" + "\n".join(reply_lines)
    await update.message.reply_text(reply_text)


# ====== Error handler ======
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Ой, что-то пошло не так. Попробуй ещё раз.")


# ====== Main ======
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Бот запущен в режиме Webhook...")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{APP_URL}/{TELEGRAM_BOT_TOKEN}"
    )


if __name__ == "__main__":
    main()
