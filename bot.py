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

# ====== Надёжные fallback-ссылки (реальные магазины/страницы) ======
FALLBACK_OUTFITS_REAL = [
    "👕 Белая футболка — https://www.zara.com/",
    "👖 Джинсы — https://www.levi.com/",
    "👟 Кеды — https://www.converse.com/",
    "🧥 Куртка — https://www.hm.com/",
    "🎒 Рюкзак — https://www.zalando.com/"
]

# ====== Утилиты ======
URL_RE = re.compile(r"(https?://[^\s\)\]\>]+)")
IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|bmp)(?:$|\?)", re.I)

def extract_urls(text: str):
    return URL_RE.findall(text or "")

async def validate_url_head_or_get(url: str, timeout: int = 8) -> bool:
    """
    Проверяем валидность URL. Сначала HEAD — если не допускают/возвращают 405/403, делаем GET (без загрузки тела полностью).
    Также исключаем прямые ссылки на изображения (если не хотим их).
    """
    if IMAGE_EXT_RE.search(url):
        return False  # исключаем прямые ссылки на изображения

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Попробуем HEAD
            try:
                r = await client.head(url, follow_redirects=True)
            except (httpx.HTTPError, httpx.RequestError):
                r = None

            if r is not None and r.status_code == 200:
                # дополнительно проверим content-type — не картинка
                ctype = r.headers.get("content-type", "")
                if "image/" in ctype:
                    return False
                return True

            # если HEAD вернул 405/403/0 или неудачно — попробуем GET с маленьким ответом
            try:
                r2 = await client.get(url, follow_redirects=True, timeout=timeout)
                if r2.status_code == 200:
                    ctype = r2.headers.get("content-type", "")
                    if "image/" in ctype:
                        return False
                    return True
            except Exception:
                return False
    except Exception:
        return False
    return False

async def filter_and_validate_urls(candidates: list[str], needed: int = 5) -> list[str]:
    """
    Проверяем список candidate URLs асинхронно, возвращаем первые validated (unique) до needed.
    """
    out = []
    seen = set()
    # проверяем параллельно
    sem = asyncio.Semaphore(20)
    async def check(url):
        async with sem:
            ok = await validate_url_head_or_get(url)
            return url if ok else None

    tasks = [asyncio.create_task(check(u)) for u in candidates]
    for task in asyncio.as_completed(tasks):
        res = await task
        if res:
            if res not in seen:
                seen.add(res)
                out.append(res)
            if len(out) >= needed:
                break
    return out

# ====== Grok запросы ======
def grok_call(payload: dict) -> dict:
    """
    Синхронный вызов Grok (через requests) — т.к. python-telegram-bot main flow синхронный при старте webhook.
    Возвращает raw json.
    """
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

async def grok_outfit_request_raw(user_text: str, max_search_results: int = 8, mode: str = "on") -> dict:
    """
    Возвращает raw ответ Grok (json) — включает поле 'citations' если есть.
    """
    payload = {
        "model": "grok-4",
        "messages": [
            {"role": "system", "content": (
                "Ты модный стилист. Отвечай списком из ровно 5 пунктов: Название вещи — ссылка. "
                "Используй реальные онлайн-магазины Zara, Lyst, Grailed, Bershka. "
                "Если не уверен — не придумывай урлы, вместо этого верни релевантные источники и ссылки."
            )},
            {"role": "user", "content": f"Подбери аутфит: {user_text}"}
        ],
        "max_tokens": 600,
        "search_parameters": {
            "mode": mode,
            "return_citations": True,
            "max_search_results": max_search_results,
            "sources": [{"type": "web"}, {"type": "news"}]
        },
    }
    # grok_call — синхронный
    return grok_call(payload)

async def grok_request_only_links(user_text: str, min_links: int = 5, max_search_results: int = 12) -> dict:
    """
    Второй проход — просим Grok вернуть ТОЛЬКО реальные URL по одному на строку, без описаний.
    """
    payload = {
        "model": "grok-4",
        "messages": [
            {"role": "system", "content": "Тебе нужно вывести строго список рабочих URL, по одному в строке. Без описаний."},
            {"role": "user", "content": f"Найди {min_links} релевантных и реальных URL (магазины, product pages или статьи) по теме: {user_text}"}
        ],
        "max_tokens": 300,
        "search_parameters": {
            "mode": "on",
            "return_citations": True,
            "max_search_results": max_search_results,
            "sources": [{"type": "web"}, {"type": "news"}]
        },
        "temperature": 0.0
    }
    return grok_call(payload)

# ====== Команды бота ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Пример: кэжуал", callback_data="casual")],
                [InlineKeyboardButton("Помощь", callback_data="help")]]
    await update.message.reply_text(
        "👋 Привет! Я бот-стилист.\nНапиши стиль или ситуацию, и я подберу аутфит.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Напиши, например: 'офис летом' или 'вечеринка в стиле 90-х'.\n"
        "Я верну до 5 рабочих ссылок на вещи/страницы."
    )

# ====== Обработчик сообщений ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    await update.message.reply_text("✨ Ищу реальные источники и ссылки...")

    # 1) первый проход: общий ответ от Grok
    try:
        raw = await grok_outfit_request_raw(user_text)
    except Exception as e:
        logger.exception("Grok call failed")
        await update.message.reply_text("Ошибка при обращении к поиску. Попробуй позже.")
        return

    # 2) собираем кандидатов: сначала citations (самые предпочтительные), затем урлы из текста
    citations = raw.get("citations") or []
    # иногда Grok отдаёт citations в message: msg.get("citations")
    if not citations:
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        citations = msg.get("citations") or raw.get("citations") or []

    text = ""
    choice = (raw.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or choice.get("text") or ""

    candidates = []
    # ставим цитаты первыми
    for u in citations:
        candidates.append(u)
    # добавляем урлы, найденные в тексте
    for u in extract_urls(text):
        if u not in candidates:
            candidates.append(u)

    # 3) фильтруем и валидируем кандидатов (параллельно)
    validated = await filter_and_validate_urls(candidates, needed=5)

    # 4) если не хватает — делаем второй проход "только ссылки"
    if len(validated) < 5:
        try:
            raw2 = await grok_request_only_links(user_text, min_links=5, max_search_results=12)
            # берем citations первыми
            candidates2 = raw2.get("citations") or []
            if not candidates2:
                choice2 = (raw2.get("choices") or [{}])[0]
                msg2 = choice2.get("message") or {}
                text2 = msg2.get("content") or choice2.get("text") or ""
                candidates2 = extract_urls(text2)
            # дополняем списком кандидатов из второго прохода
            for u in candidates2:
                if u not in candidates:
                    candidates.append(u)
            # проверяем снова (заметим: filter_and_validate_urls проверяет уже уникальность)
            more = await filter_and_validate_urls(candidates, needed=5)
            # объединяем, сохраняя порядок в more (это уже validated subset)
            # we prefer earlier validated items, so we merge intelligently:
            final = []
            for u in validated + more:
                if u not in final:
                    final.append(u)
                if len(final) >= 5:
                    break
            validated = final
        except Exception:
            logger.exception("Second Grok pass failed")

    # 5) Если всё равно не хватает — дополняем реальными fallback ссылками
    final_lines = []
    for u in validated:
        final_lines.append(u)
    # If there are less than 5, append FALLBACK_REAL full lines (name — url)
    idx = 0
    while len(final_lines) < 5 and idx < len(FALLBACK_OUTFITS_REAL):
        final_lines.append(FALLBACK_OUTFITS_REAL[idx])
        idx += 1

    # Формируем ответ: показываем только реальные ссылки (каждый в новой строке).
    # Если Grok изначально дал описательные строки (например "Leather Sneakers — https://..."), мы предпочитаем показать "Название — URL"
    # Поэтому попробуем сопоставить: если в исходном тексте есть строка, содержащая валид url, отобразим её.
    out_lines = []
    # map url->line from original text (if applicable)
    url_to_line = {}
    for line in (text or "").splitlines():
        for u in extract_urls(line):
            url_to_line[u] = line.strip()

    for item in final_lines:
        # если это уже "Name — url" (в FALLBACK_OUTFITS_REAL) — оставить как есть
        if "—" in item and "http" in item:
            out_lines.append(item)
            continue
        # else item is a url, try find matching descriptive line
        if item in url_to_line:
            out_lines.append(url_to_line[item])
        else:
            # форматируем базово: ссылка на отдельной строке
            out_lines.append(f"- {item}")

    # Отправляем только валидированные/реальные ссылки, без автопредпросмотра текста
    reply = "Вот что я подобрал (только реальные ссылки):\n\n" + "\n".join(out_lines[:5])
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
