import os
import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from openai import OpenAI

# ====== Конфиг ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== База (fallback) ======
with open("outfits.json", "r", encoding="utf-8") as f:
    OUTFITS = json.load(f)

# ====== GPT ======
async def gpt_outfit_request(style: str):
    """Запрос к GPT для подбора аутфита. Возвращает list[{"name","link"}] или []"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты стилист. Отвечай строго в JSON: "
                        "{\"items\": [{\"name\": \"...\", \"link\": \"...\"}]}"
                    )
                },
                {
                    "role": "user",
                    "content": f"Подбери аутфит в стиле {style} из онлайн-магазинов (ASOS, Zara, H&M, Farfetch)."
                }
            ],
            max_tokens=300,
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        items = data.get("items", [])
        # Мини-валидация структуры
        if not isinstance(items, list):
            return []
        cleaned = []
        for it in items:
            name = (it.get("name") or "").strip()
            link = (it.get("link") or "").strip()
            if name and link:
                cleaned.append({"name": name, "link": link})
        return cleaned
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        return []

# ====== Handlers ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Casual", callback_data="style_casual")],
        [InlineKeyboardButton("Party", callback_data="style_party")],
        [InlineKeyboardButton("Office", callback_data="style_office")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! 👋 Выбери стиль аутфита:", reply_markup=reply_markup)

async def style_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    style = query.data.replace("style_", "")

    # 1) Пробуем GPT
    items = await gpt_outfit_request(style)

    # 2) Если пусто — берём из JSON
    if not items:
        items = OUTFITS.get(style, [])

    if not items:
        await query.edit_message_text("Пока нет вещей для этого стиля 😢")
        return

    # Разделяем на картинки и текст (чтобы не ловить webpage_media_empty)
    media_group = []
    text_items = []
    for item in items[:10]:  # максимум 10
        link = item.get("link", "")
        name = item.get("name", "Без названия")

        if link.lower().endswith((".jpg", ".jpeg", ".png")):
            media_group.append(InputMediaPhoto(media=link, caption=name))
        else:
            text_items.append(f"{name}: {link}")

    # Отправляем фото-альбом
    if media_group:
        await query.message.reply_media_group(media_group)

    # И текстовые ссылки (если были)
    if text_items:
        await query.message.reply_text("\n".join(text_items))

# Глобальный обработчик ошибок, чтобы ловить и логировать всё
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception in handler", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Ой! Что-то пошло не так, уже чиним ⚙️")
    except Exception:
        pass

# Хук после инициализации: снесём вебхук, чтобы polling не конфликтовал
async def post_init(application: Application):
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён (drop_pending_updates=True). Переходим на polling.")
    except Exception as e:
        logger.warning(f"Не удалось удалить webhook: {e}")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрируем хук post_init
    app.post_init = post_init

    # Регистрируем хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(style_handler))
    app.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    # drop_pending_updates=True заодно чистит очередь
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
