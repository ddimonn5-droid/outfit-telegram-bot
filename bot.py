


import os
import sys
import logging
from telegram import Update, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Настройка логирования для отладки
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен из переменных окружения
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not TOKEN:
    logger.error("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    sys.exit(1)

# База аутфитов (оставляем вашу версию без изменений)
OUTFITS = {
    "кэжуал": [
        {"name": "Футболка", "url": "https://samokat.ua/images/products/ethic-casual-suspect-t-shirt-01_24w.jpg"},
        {"name": "Джинсы", "url": "https://2hand.com.ua/image/cache/catalog/3111856e8956381546_4406-1365x1024.jpg"},
        {"name": "Кроссовки", "url": "https://yastreb.ua/components/com_jshopping/files/img_products/full_7777.jpg"},
    ],
    "вечеринка": [
        {"name": "Пиджак", "url": "https://ae01.alicdn.com/kf/S772d2377cb7c4c01a69fd7c1d8175df4z.jpg"},
        {"name": "Рубашка", "url": "https://content.rozetka.com.ua/goods/images/big/591651531.jpg"},
        {"name": "Брюки", "url": "https://ae01.alicdn.com/kf/Saaacdd53cdf94f2fab51aab53bab0fecB.jpg"},
        {"name": "Туфли", "url": "https://tufelek.kiev.ua/images/products/kupit_obuv_025.jpg"},
    ],
    "офис": [
        {"name": "Белая рубашка", "url": "https://musthave.ua/uploads/products/17781/00000033510.webp"},
        {"name": "Классические брюки", "url": "https://ager.ua/image/cache/webp/catalog/import_files/be/72992fb7-e030-11e0-80a9-0015c56feef5/bef8931f-585d-11ef-8588-d8cb8a9c13c3-667x1000.webp"},
        {"name": "Туфли", "url": "https://img2.ans-media.com/i/840x1260/AW25-OBM1W0-89X_F1.webp?v=1756274023"}
    ]
}

# Обработчики команд (АДАПТИРОВАНЫ под старый синтаксис)
def start(update: Update, context: CallbackContext):
    welcome_text = ("👕 *Добро пожаловать в бот для подбора аутфитов!* 👖\n\n"
                   "Я помогу вам подобрать стильный комплект одежды.\n\n"
                   "📋 *Доступные команды:*\n"
                   "/start - начать работу\n"
                   "/styles - показать все доступные стили\n"
                   "/help - помощь\n\n"
                   "💡 *Просто напишите один из стилей:*\n"
                   "• кэжуал\n"
                   "• вечеринка\n"
                   "• офис\n\n"
                   "И я покажу вам подходящий комплект одежды!")
    update.message.reply_text(welcome_text, parse_mode='Markdown')

def help_command(update: Update, context: CallbackContext):
    help_text = ("🤖 *Как пользоваться ботом:*\n\n"
                "1. Напишите название стиля (например: 'кэжуал')\n"
                "2. Я пришлю вам подборку одежды для этого стиля\n"
                "3. Каждое изображение будет с описанием элемента одежды\n\n"
                "📝 *Доступные команды:*\n"
                "/start - начать работу\n"
                "/styles - показать все стили\n"
                "/help - эта справка\n\n"
                "🎯 *Доступные стили:* " + ", ".join(f"'{key}'" for key in OUTFITS.keys()))
    update.message.reply_text(help_text, parse_mode='Markdown')

def list_styles(update: Update, context: CallbackContext):
    styles_text = "🎨 *Доступные стили одежды:*\n\n"
    for style in OUTFITS.keys():
        styles_text += f"• {style.capitalize()} - {len(OUTFITS[style])} элементов\n"
    styles_text += "\n📝 Напишите название стиля, чтобы увидеть аутфит!"
    update.message.reply_text(styles_text, parse_mode='Markdown')

def handle_message(update: Update, context: CallbackContext):
    try:
        query = update.message.text.lower().strip()
        if query not in OUTFITS:
            available_styles = ", ".join(f"'{key}'" for key in OUTFITS.keys())
            update.message.reply_text(f"❌ Стиль '{query}' не найден.\n\n📋 Доступные стили: {available_styles}\n\nИспользуйте /styles чтобы увидеть полный список.")
            return

        outfit = OUTFITS[query]
        update.message.reply_text(f"🔄 Загружаю аутфит для стиля '{query}'...\n📦 Всего элементов: {len(outfit)}")

        for item in outfit:
            try:
                update.message.reply_photo(photo=item["url"], caption=f"👕 {item['name']}\n#{(query)}")
            except Exception as e:
                logger.error(f"Ошибка при отправке фото: {e}")
                update.message.reply_text(f"❌ Не удалось загрузить: {item['name']}")

        update.message.reply_text(f"✅ Аутфит для стиля '{query}' готов!\n\nХотите посмотреть другой стиль? Просто напишите его название.")

    except Exception as e:
        logger.error(f"Unexpected error in handle_message: {e}")
        update.message.reply_text("⚠️ Произошла непредвиденная ошибка.\nПопробуйте еще раз или обратитесь к администратору.")

def error_handler(update: Update, context: CallbackContext):
    logger.error(f'Update {update} caused error {context.error}')
    if update and update.message:
        update.message.reply_text("⚠️ Произошла ошибка. Пожалуйста, попробуйте еще раз.")

def main():
    try:
        # СОЗДАЕМ UPDATER вместо Application (старый синтаксис)
        updater = Updater(TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("help", help_command))
        dispatcher.add_handler(CommandHandler("styles", list_styles))
        dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
        dispatcher.add_error_handler(error_handler)

        logger.info("🤖 Бот запускается...")
        logger.info(f"📊 Доступные стили: {list(OUTFITS.keys())}")
        logger.info("✅ Бот готов к работе! Используется стабильная версия 13.15")

        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

