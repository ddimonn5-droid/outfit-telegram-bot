import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Если захочешь GPT:
# import openai
# openai.api_key = "YOUR_API_KEY"

logging.basicConfig(level=logging.INFO)

# Загружаем базу стилей
with open("outfits.json", "r", encoding="utf-8") as f:
    OUTFITS = json.load(f)

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
# Обработка нажатия кнопок
async def style_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    style = query.data.replace("style_", "")
    items = OUTFITS.get(style, [])

    if not items:
        await query.edit_message_text("Пока нет вещей для этого стиля 😢")
        return

    # Выводим вещи
    media_group = []
    for item in items:
        media_group.append(InputMediaPhoto(media=item["link"], caption=item["name"]))

    if media_group:
        await query.message.reply_media_group(media_group)

# ------------------------------
# (опционально) Подключение GPT
async def gpt_outfit_request(style: str):
    """Заготовка — можно подключить OpenAI"""
    # response = openai.ChatCompletion.create(
    #     model="gpt-4o-mini",
    #     messages=[{"role": "user", "content": f"Подбери аутфит в стиле {style}"}]
    # )
    # return response["choices"][0]["message"]["content"]
    return f"Пример ответа GPT для стиля {style}"

# ------------------------------
def main():
    app = Application.builder().token(os.getenv("BOT_TOKEN")).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(style_handler))
app.run_polling()

if __name__ == "__main__":
    main()


