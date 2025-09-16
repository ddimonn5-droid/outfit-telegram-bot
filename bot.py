# Monkey-patch для обхода проблем с imghdr и Updater
import sys
import os

# Добавляем текущую директорию в путь Python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Загружаем monkey-patch для imghdr
try:
    from imghdr_monkeypatch import ImghdrModule
    sys.modules['imghdr'] = ImghdrModule()
    print("✅ Monkey-patch для imghdr применен успешно")
except Exception as e:
    print(f"⚠️ Ошибка применения monkey-patch: {e}")

# Явно указываем использовать новую версию API
import telegram
telegram.__version__ = "13.15"


import os
import sys
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext

# Токен из переменных окружения
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Проверка токена
if not TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    print("📝 Установите переменную окружения TELEGRAM_BOT_TOKEN")
    sys.exit(1)

# ... ваш код с OUTFITS ...

# Команда /start
def start(update, context: CallbackContext):
    welcome_text = (
        "👕 *Добро пожаловать в бот для подбора аутфитов!* 👖\n\n"
        "Я помогу вам подобрать стильный комплект одежды.\n\n"
        "📋 *Доступные команды:*\n"
        "/start - начать работу\n"
        "/styles - показать все доступные стили\n"
        "/help - помощь\n\n"
        "💡 *Просто напишите один из стилей:*\n"
        "• кэжуал\n"
        "• вечеринка\n"
        "• офис\n\n"
        "И я покажу вам подходящий комплект одежды!"
    )
    
    update.message.reply_text(welcome_text, parse_mode='Markdown')

# Команда /help
def help_command(update, context: CallbackContext):
    help_text = (
        "🤖 *Как пользоваться ботом:*\n\n"
        "1. Напишите название стиля (например: 'кэжуал')\n"
        "2. Я пришлю вам подборку одежды для этого стиля\n"
        "3. Каждое изображение будет с описанием элемента одежды\n\n"
        "📝 *Доступные команды:*\n"
        "/start - начать работу\n"
        "/styles - показать все стили\n"
        "/help - эта справка\n\n"
        "🎯 *Доступные стили:* " + ", ".join(f"'{key}'" for key in OUTFITS.keys())
    )
    
    update.message.reply_text(help_text, parse_mode='Markdown')

# Команда /styles
def list_styles(update, context: CallbackContext):
    styles_text = "🎨 *Доступные стили одежды:*\n\n"
    for style in OUTFITS.keys():
        styles_text += f"• {style.capitalize()} - {len(OUTFITS[style])} элементов\n"
    
    styles_text += "\n📝 Напишите название стиля, чтобы увидеть аутфит!"
    
    update.message.reply_text(styles_text, parse_mode='Markdown')

# Функция обработки текстовых сообщений
def handle_message(update, context: CallbackContext):
    try:
        query = update.message.text.lower().strip()
        
        if query not in OUTFITS:
            available_styles = ", ".join(f"'{key}'" for key in OUTFITS.keys())
            update.message.reply_text(
                f"❌ Стиль '{query}' не найден.\n\n"
                f"📋 Доступные стили: {available_styles}\n\n"
                "Используйте /styles чтобы увидеть полный список."
            )
            return

        outfit = OUTFITS[query]
        
        update.message.reply_text(
            f"🔄 Загружаю аутфит для стиля '{query}'...\n"
            f"📦 Всего элементов: {len(outfit)}"
        )

        for item in outfit:
            try:
                update.message.reply_photo(
                    photo=item["url"],
                    caption=f"👕 {item['name']}\n#{(query)}"
                )
            except Exception as e:
                print(f"Ошибка при отправке фото: {e}")
                update.message.reply_text(f"❌ Не удалось загрузить: {item['name']}")

        update.message.reply_text(
            f"✅ Аутфит для стиля '{query}' готов!\n\n"
            "Хотите посмотреть другой стиль? Просто напишите его название."
        )

    except Exception as e:
        update.message.reply_text(
            "⚠️ Произошла непредвиденная ошибка.\n"
            "Попробуйте еще раз или обратитесь к администратору."
        )
        print(f"Unexpected error: {e}")

# Основная функция
def main():
    try:
        # СТАРЫЙ СИНТАКСИС - используем Updater
        updater = Updater(TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("help", help_command))
        dispatcher.add_handler(CommandHandler("styles", list_styles))
        dispatcher.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        print("🤖 Бот запускается...")
        print("📊 Доступные стили:", list(OUTFITS.keys()))
        print("✅ Бот готов к работе!")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
