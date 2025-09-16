import os
import sys
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# Токен из переменных окружения
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Проверка токена
if not TOKEN:
    print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не установлен!")
    print("📝 Установите переменную окружения TELEGRAM_BOT_TOKEN")
    sys.exit(1)

# ... ваш код с аутфитами ...

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... ваш код ...

# Команда /help  
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... ваш код ...

# Команда /styles
async def list_styles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... ваш код ...

# Функция обработки текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... ваш код ...

# Основная функция
def main():
    try:
        # НОВЫЙ СИНТАКСИС для версии 20.7
        application = Application.builder().token(TOKEN).build()

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command)) 
        application.add_handler(CommandHandler("styles", list_styles))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        print("🤖 Бот запускается...")
        print("✅ Бот готов к работе!")
        
        # Запускаем бота
        application.run_polling()

    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
