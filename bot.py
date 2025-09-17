# bot.py
import os
import re
import html
import logging
import asyncio
import httpx
import requests
from typing import List, Optional, Tuple
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========= CONFIG =========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
XAI_API_KEY = os.getenv("XAI_API_KEY")
PORT = int(os.getenv("PORT", 8443))
APP_URL = os.getenv("RENDER_EXTERNAL_URL")  # https://your-service.onrender.com

if not TELEGRAM_BOT_TOKEN or not XAI_API_KEY or not APP_URL:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN, XAI_API_KEY, RENDER_EXTERNAL_URL")

# ========= LOGGING =========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grok-bot")

# ========= CONSTANTS / HELPERS =========
URL_RE = re.compile(r"https?://[^\s\)\]\>]+")
IMG_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|bmp)(?:$|\?)", re.I)
EMOJIS = ["👕", "👖", "👟", "🧥", "🎒"]
HTTP_TIMEOUT = 10
CONCURRENCY = 16

# Разрешённые домены (чем шире — тем проще найти товары)
ALLOWED_WEBSITES = [
    "zara.com", "hm.com", "bershka.com", "asos.com", "zalando.",
    "lyst.com", "grailed.com", "nike.com", "adidas.com", "uniqlo.com",
    "levi.com", "converse.com"
]

# Регексы для product pages по доменам
PRODUCT_PATTERNS = {
    # Zara: /en/us/...-p012345.html
    "zara.com":      r"/[a-z]{2}/[a-z]{2}/.+-p\d{5,}\.html",
    # H&M: /productpage.123456.html
    "hm.com":        r"/productpage\.\d+\.html",
    # Bershka: /ru/ru/...-c123456/p/123456789.html
    "bershka.com":   r"/[a-z]{2}/[a-z]{2}/[a-z-]+-c\d+/p/\d+\.html",
    # ASOS: /prd/12345678 или /p/<slug>/12345678
    "asos.com":      r"/(prd/\d+|/p/[a-z0-9-]+/\d+)",
    # Zalando: /.../article/<CODE> или /p/<CODE>
    "zalando.":      r"/.*(article|p)/[A-Z0-9]{6,}",
    # Lyst: обычно /clothing|shoes|accessories/...<digits>/
    "lyst.com":      r"/(clothing|shoes|accessories)/.+\d{4,}/?",
    # Grailed: /listings/12345678
    "grailed.com":   r"/listings/\d+",
    # Nike product: /t/<slug>-<code>  или /launch/t/<slug>
    "nike.com":      r"/(launch/)?t/[a-z0-9-]+",
    # Adidas: /en/us/.../<CODE>.html
    "adidas.com":    r"/[a-z]{2}/[a-z]{2}/.+/[A-Z0-9]{6,}\.html",
    # UNIQLO: /products/<slug>  или /product/<slug>
    "uniqlo.com":    r"/products?/[a-z0-9-]+",
    # Levi's: /p/<CODE>  или /product/<slug>
    "levi.com":      r"/(p|product)/[A-Za-z0-9\-]{5,}",
    # Converse: /shop/p/<slug>  или /p/<slug>
    "converse.com":  r"/(shop/)?p/[a-z0-9-]+",
}

def domain_of(url: str) -> str:
    try:
        return requests.utils.urlparse(url).netloc.lower()
    except Exception:
        return ""

def clean_url(u: str) -> str:
    u = html.unescape(u).replace("\u200b", "").replace("\u200e", "").replace("\u200f", "")
    u = u.strip()
    while u and u[-1] in ".,;:)]'\"":
        u = u[:-1]
    return u

def extract_urls(text: str) -> List[str]:
    return [clean_url(u) for u in URL_RE.findall(text or "")]

def looks_like_product(url: str) -> bool:
    if IMG_EXT_RE.search(url):
        return False
    host = domain_of(url)
    path = requests.utils.urlparse(url).path
    if not host or not path or url.endswith("/"):
        return False
    # разрешённые домены
    if not any(allow in host for allow in ALLOWED_WEBSITES):
        return False
    # доменные регексы
    for key, rx in PRODUCT_PATTERNS.items():
        if key in host:
            return re.search(rx, path, flags=re.I) is not None
    return False

# ========= HTTP utils =========
async def http_ok_html(url: str) -> bool:
    if IMG_EXT_RE.search(url):
        return False
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200:
                ctype = r.headers.get("content-type", "")
                return "text/html" in ctype
    except Exception as e:
        logger.debug("validate fail %s -> %s", url, e)
    return False

async def fetch_title(url: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text, flags=re.I | re.S)
            if not m:
                return None
            title = html.unescape(m.group(1)).strip()
            title = re.sub(r"\s+", " ", title)
            return title[:120]
    except Exception:
        return None

async def validate_and_title_batch(urls: List[str], need: int) -> List[Tuple[str, Optional[str]]]:
    out, seen = [], set()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def worker(u):
        async with sem:
            if not looks_like_product(u):
                return None
            ok = await http_ok_html(u)
            if not ok:
                return None
            title = await fetch_title(u)
            return (u, title)

    tasks = [asyncio.create_task(worker(u)) for u in urls]
    for t in asyncio.as_completed(tasks):
        try:
            res = await t
        except Exception:
            res = None
        if res:
            u, ti = res
            if u not in seen:
                seen.add(u)
                out.append((u, ti))
                if len(out) >= need:
                    break
    return out

# ========= Grok =========
def build_system_prompt(strict: bool = False) -> str:
    base = (
        "Ты модный стилист. Подбирай строго 5 вещей: 👕 футболка, 👖 джинсы, 👟 кроссовки, 🧥 куртка, 🎒 аксессуар.\n"
        "Формат ответа: Emoji Название — ссылка (одна вещь на строку).\n\n"
        "‼️ ТОЛЬКО карточки товаров (product pages). Запрещены: статьи, обзоры, блоги, новости, категории и главные страницы.\n"
        "Разрешённые магазины (только эти домены):\n"
        " - Zara: zara.com\n"
        " - H&M: hm.com\n"
        " - Bershka: bershka.com\n"
        " - ASOS: asos.com\n"
        " - Zalando: любые поддомены (zalando.*)\n"
        " - Lyst: lyst.com (только карточки товаров)\n"
        " - Grailed: grailed.com (только listings)\n"
        " - Nike: nike.com\n"
        " - Adidas: adidas.com\n"
        " - UNIQLO: uniqlo.com\n"
        " - Levi's: levi.com\n"
        " - Converse: converse.com\n"
        "Ссылка должна содержать товарный идентификатор или слаг (например: 'productpage.123456', '-p012345', 'id=12345', '/dp/...').\n"
    )
    if strict:
        base += "СТРОГО: верни РОВНО 5 URL карточек товаров, по одному в строке, без описаний."
    return base

def ask_grok(user_text: str, strict: bool = False, max_search_results: int = 20) -> dict:
    payload = {
        "model": "grok-4",
        "messages": [
            {"role": "system", "content": build_system_prompt(strict=strict)},
            {"role": "user", "content": f"Подбери аутфит (футболка, джинсы, кроссовки, куртка, аксессуар): {user_text}"}
        ],
        "max_tokens": 600,
        "search_parameters": {
            "mode": "on",
            "return_citations": True,
            "max_search_results": max_search_results,
            "sources": [{
                "type": "web",
                "allowed_websites": ALLOWED_WEBSITES
            }]
        },
        "temperature": 0.12
    }
    logger.info("Grok request strict=%s", strict)
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

# ========= Bot Handlers =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Пример: кэжуал", callback_data="casual")],
          [InlineKeyboardButton("Помощь", callback_data="help")]]
    await update.message.reply_text(
        "👋 Привет! Напиши стиль (например: «уличный спорт», «офис летом», «вечеринка 90-х»), "
        "и я подберу 5 реальных товарных страниц.",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я возвращаю ровно 5 product pages (футболка, джинсы, кроссовки, куртка, аксессуар) "
        "из магазинов: Zara, H&M, Bershka, ASOS, Zalando, Lyst, Grailed, Nike, Adidas, UNIQLO, Levi's, Converse."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        await update.message.reply_text("Напиши, пожалуйста, что искать (например: 'casual на каждый день').")
        return

    await update.message.reply_text("🔎 Ищу товарные страницы...")

    found: List[Tuple[str, Optional[str]]] = []
    attempts = 0
    tried_urls: List[str] = []

    # До 5 попыток: 1 обычная + 4 строгих (только URL)
    while len(found) < 5 and attempts < 5:
        strict = attempts >= 1
        try:
            raw = ask_grok(user_text, strict=strict, max_search_results=25 if strict else 15)
        except Exception:
            logger.exception("Grok error")
            await asyncio.sleep(0.6)
            attempts += 1
            continue

        citations = raw.get("citations") or []
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = html.unescape(msg.get("content") or "")
        from_text = extract_urls(text)

        candidates = []
        for u in citations + from_text:
            cu = clean_url(u)
            if cu and cu not in candidates:
                candidates.append(cu)

        logger.info("Attempt %s: %d candidates (cit=%d, text=%d)",
                    attempts, len(candidates), len(citations), len(from_text))
        logger.info("Sample: %s", candidates[:8])

        to_check = candidates
        validated = await validate_and_title_batch(to_check, need=5 - len(found))
        for (u, title) in validated:
            if all(u != x[0] for x in found):
                found.append((u, title))
                if len(found) >= 5:
                    break

        tried_urls.extend(candidates)
        attempts += 1
        if len(found) < 5:
            await asyncio.sleep(0.6)

    # если всё ещё меньше 5 — расширяем проверку на всё, что увидели (вдруг часть прошла теперь)
    if len(found) < 5 and tried_urls:
        more = await validate_and_title_batch(
            [u for u in tried_urls if all(u != x[0] for x in found)],
            need=5 - len(found)
        )
        for (u, title) in more:
            if all(u != x[0] for x in found):
                found.append((u, title))
                if len(found) >= 5:
                    break

    if not found:
        await update.message.reply_text(
            "😔 Похоже, магазины ограничили доступ. Уточни бренд/тип вещи (напр. 'Zara белая футболка')."
        )
        return

    # выводим: строка с названием + кнопка с ссылкой
    lines = []
    buttons = []
    for i, (url, title) in enumerate(found[:5]):
        emoji = EMOJIS[i] if i < len(EMOJIS) else "🛍️"
        label = (title or "Товар").strip()
        if len(label) > 80:
            label = label[:77] + "..."
        lines.append(f"{emoji} {label}\n{url}")
        buttons.append([InlineKeyboardButton(f"{emoji} Открыть", url=url)])

    text = "Вот твой аутфит (реальные товарные страницы):\n\n" + "\n\n".join(lines)
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

# ========= MAIN =========
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"{APP_URL}/{TELEGRAM_BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
