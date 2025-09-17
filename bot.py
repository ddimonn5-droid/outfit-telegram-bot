import os
import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import openai

# Загружаем ключи из окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)

# Загружаем базу (fallback JSON)
with open("outfits.json", "r", encoding="utf-8") as f:
    OUTFITS = json.load(f)


# ------------------------------
# GPT генерация аутфита
async def gpt_outfit_request(style: str):
    """Запрос к GPT для подбора аутфита"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты модный стилист. Отвечай строго JSON форматом."},
                {"role": "user", "content": f"Подбери аутфит в стиле {style}. Формат: "
                                            "{\"items\": [{\"name\": \"...\", \"link\": \"...\"}]}"}
            ],
            max_tokens=300
        )
        # GPT возвращает текст → пробуем распарсить как JSON
        content = response["choices"][0]["message"]["content"]
        data = json.loads(content)
        return data.get("items", [])
    except Exception as e:
        logging.error(f"Ошибка GPT: {e}")
        return []


# ------------------------------
# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Casual", callback_data="style_casual")],
        [InlineKeyboardButton("Party", callback_data="style_party")],
        [InlineKeyboardButton("Office", callback_data="style_office")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! 👋 Выбери стиль аутфита:", reply_markup=reply_markup)


# ------------------------------
# Обработка кнопок
async def style_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    style = query.data.replace("style_", "")

    # 1. Пробуем GPT
    items = await gpt_outfit_request(style)

    # 2. Если GPT не дал ответа → берём outfits.json
    if not items:
        items = OUTFITS.get(style, [])

    if not items:
        await query.edit_message_text("Пока нет вещей для этого стиля 😢")
        return

    # Отправляем как альбом
    media_group = []
    for item in items[:10]:  # максимум 10 картинок
        if "link" in item:
            media_group.append(InputMediaPhoto(media=item["link"], caption=item.get("name", "")))

    if media_group:
        await query.message.reply_media_group(media_group)


# ------------------------------
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(style_handler))

    logging.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
