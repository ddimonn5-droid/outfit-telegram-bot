# bot.py
import os
import re
import html
import logging
import asyncio
import httpx
import requests
from typing import List, Optional, Tuple, Dict
from urllib.parse import quote_plus
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========= CONFIG =========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
XAI_API_KEY = os.getenv("XAI_API_KEY")
PORT = int(os.getenv("PORT", 8443))
APP_URL = os.getenv("RENDER_EXTERNAL_URL")

if not TELEGRAM_BOT_TOKEN or not XAI_API_KEY or not APP_URL:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN, XAI_API_KEY, RENDER_EXTERNAL_URL env vars")

# ========= LOGGING =========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grok-bot")

# ========= CONSTANTS / HELPERS =========
URL_RE = re.compile(r"https?://[^\s\)\]\>]+")
IMG_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|bmp)(?:$|\?)", re.I)
EMOJIS = ["👕", "👖", "👟", "🧥", "🎒"]
HTTP_TIMEOUT = 10
CONCURRENCY = 16

# Доменные регексы product pages
PRODUCT_PATTERNS = {
    "zara.com":      r"/[a-z]{2}/[a-z]{2}/.+-p\d{5,}\.html",
    "hm.com":        r"/productpage\.\d+\.html",
    "bershka.com":   r"/[a-z]{2}/[a-z]{2}/[a-z-]+-c\d+/p/\d+\.html",
    "asos.com":      r"/(prd/\d+|/p/[a-z0-9-]+/\d+)",
    "zalando.":      r"/.*(article|p)/[A-Z0-9]{6,}",
    "lyst.com":      r"/(clothing|shoes|accessories)/.+\d{4,}/?",
    "grailed.com":   r"/listings/\d+",
    "nike.com":      r"/(launch/)?t/[a-z0-9-]+",
    "adidas.com":    r"/[a-z]{2}/[a-z]{2}/.+/[A-Z0-9]{6,}\.html",
    "uniqlo.com":    r"/products?/[a-z0-9-]+",
    "levi.com":      r"/(p|product)/[A-Za-z0-9\-]{5,}",
    "converse.com":  r"/(shop/)?p/[a-z0-9-]+",
}

SEARCH_ENDPOINTS: Dict[str, str] = {
    # простые поисковые URL; мы потом отфильтруем найденные product-ссылки регексами сверху
    "zara.com":     "https://www.zara.com/ww/en/search?searchTerm={q}",
    "hm.com":       "https://www2.hm.com/en_us/search-results.html?q={q}",
    "bershka.com":  "https://www.bershka.com/ww/search?q={q}",
    "asos.com":     "https://www.asos.com/search/?q={q}",
    "zalando.com":  "https://www.zalando.com/catalog/?q={q}",
    "nike.com":     "https://www.nike.com/w?q={q}",
    "adidas.com":   "https://www.adidas.com/us/search?q={q}",
    "uniqlo.com":   "https://www.uniqlo.com/us/en/search/?q={q}",
    "levi.com":     "https://www.levi.com/US/en_US/search/{q}",
    "converse.com": "https://www.converse.com/search?q={q}",
    # аггрегаторы можно оставить на потом, но регексы у нас есть на lyst/grailed
    "lyst.com":     "https://www.lyst.com/search/?q={q}",
    "grailed.com":  "https://www.grailed.com/shop?q={q}",
}

# Карта слотов -> поисковый запрос
ITEM_QUERIES = [
    ("👕", "футболка", ["t shirt", "tee", "tshirt"]),
    ("👖", "джинсы",   ["jeans", "denim jeans"]),
    ("👟", "кроссовки",["sneakers", "trainers"]),
    ("🧥", "куртка",   ["jacket"]),
    ("🎒", "аксессуар",["backpack", "belt", "cap"]),
]

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
            return r.status_code == 200 and "text/html" in (r.headers.get("content-type", ""))
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
        "Допускаются домены известных магазинов (Zara, H&M, Bershka, ASOS, Zalando, Lyst, Grailed, Nike, Adidas, UNIQLO, Levi's, Converse).\n"
        "Ссылка должна содержать товарный идентификатор или слаг (например: 'productpage.123456', '-p012345', 'id=12345', '/dp/...').\n"
    )
    if strict:
        base += "СТРОГО: верни РОВНО 5 URL карточек товаров, по одному в строке, без описаний."
    return base

def grok_call(payload: dict) -> dict:
    url = "https://api.x.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        try:
            logger.error("Grok 4xx/5xx: %s\nResponse: %s", r.status_code, r.text[:2000])
        except Exception:
            logger.error("Grok 4xx/5xx: %s (cannot decode body)", r.status_code)
        r.raise_for_status()
    return r.json()

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
            "sources": [{"type": "web"}]  # без allowed_websites, иначе 400
        },
        "temperature": 0.12
    }
    logger.info("Grok request strict=%s", strict)
    return grok_call(payload)

# ========= Fallback Site Search (без Grok) =========
async def site_search_first_product(site: str, query: str) -> Optional[str]:
    """Открываем поисковую страницу магазина и вытаскиваем первую product-ссылку по нашему доменному регексу."""
    base = SEARCH_ENDPOINTS[site]
    url = base.format(q=quote_plus(query))
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            html_text = r.text
            # достанем все URL этого домена
            host = site
            # общий жадный сбор ссылок этого домена
            urls = re.findall(rf"https?://[^\s\"']*{re.escape(host)}/[^\s\"']+", html_text, flags=re.I)
            # прогон по looks_like_product
            for u in urls:
                cu = clean_url(u)
                if looks_like_product(cu):
                    ok = await http_ok_html(cu)
                    if ok:
                        return cu
    except Exception as e:
        logger.debug("site_search_first_product fail %s -> %s", site, e)
    return None

async def guaranteed_find_products(user_text: str) -> List[Tuple[str, Optional[str]]]:
    """
    Фолбэк, который гарантированно пытается найти 5 карточек:
    Для каждой позиции (👕, 👖, 👟, 🧥, 🎒) пробегаем по магазинам и берём первый найденный product-URL.
    """
    items = ITEM_QUERIES  # фикс: 5 слотов
    sites_priority = [
        "zara.com", "hm.com", "bershka.com", "asos.com",
        "zalando.com", "nike.com", "adidas.com", "uniqlo.com",
        "levi.com", "converse.com", "lyst.com", "grailed.com"
    ]
    results: List[Tuple[str, Optional[str]]] = []

    for emoji, ru_name, queries in items:
        found_url: Optional[str] = None
        # склеим запрос: что попросил юзер + название категории
        q_variants = [f"{user_text} {ru_name}"] + queries
        for q in q_variants:
            for site in sites_priority:
                url = await site_search_first_product(site, q)
                if url:
                    title = await fetch_title(url)
                    results.append((url, title))
                    found_url = url
                    break
            if found_url:
                break
        if not found_url:
            # если даже так не нашли — оставим слот пустым, позже попробуем добрать из общих попыток
            logger.warning("Fallback search: not found for slot %s (%s)", emoji, ru_name)

    # если где-то пусто — попробуем добрать из любых найденных по всем сайтам на общую строку запроса
    if len(results) < 5:
        extra_needed = 5 - len(results)
        pool = []
        for site in sites_priority:
            u = await site_search_first_product(site, user_text)
            if u:
                pool.append(u)
        # валидируем и берём недостающее
        validated = await validate_and_title_batch(pool, need=extra_needed)
        results.extend(validated)

    return results[:5]

# ========= Bot Handlers =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Пример: кэжуал", callback_data="casual")],
          [InlineKeyboardButton("Помощь", callback_data="help")]]
    await update.message.reply_text(
        "👋 Привет! Напиши стиль (например: «уличный спорт», «офис летом», «вечеринка 90-х»), "
        "и я подберу 5 реальных карточек товаров.",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я верну ровно 5 product pages (футболка, джинсы, кроссовки, куртка, аксессуар) "
        "из известных магазинов. Можно уточнить бренд/бюджет/материал."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        await update.message.reply_text("Напиши, пожалуйста, что искать (например: 'casual на каждый день').")
        return

    await update.message.reply_text("🔎 Ищу товарные страницы...")

    # 1) Пытаемся через Grok (несколько попыток)
    found: List[Tuple[str, Optional[str]]] = []
    attempts = 0
    tried_urls: List[str] = []

    while len(found) < 5 and attempts < 5:
        strict = attempts >= 1
        try:
            raw = ask_grok(user_text, strict=strict, max_search_results=25 if strict else 15)
        except Exception:
            logger.exception("Grok error")
            await asyncio.sleep(0.5)
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

        validated = await validate_and_title_batch(candidates, need=5 - len(found))
        for (u, title) in validated:
            if all(u != x[0] for x in found):
                found.append((u, title))
                if len(found) >= 5:
                    break

        tried_urls.extend(candidates)
        attempts += 1
        if len(found) < 5:
            await asyncio.sleep(0.4)

    # 2) Если Grok не добил до 5 — включаем гарантированный оффлайн-фолбэк по сайтам
    if len(found) < 5:
        need = 5 - len(found)
        logger.info("FALLBACK site search enabled (need %s)", need)
        more = await guaranteed_find_products(user_text)
        # привинтим то, чего не хватает, избегая дублей
        for (u, title) in more:
            if all(u != x[0] for x in found):
                found.append((u, title))
                if len(found) >= 5:
                    break

    # 3) Если вообще пусто (почти нереально с фолбэком) — мягко сообщим
    if not found:
        await update.message.reply_text(
            "😔 Не смог найти открываемые карточки товаров. Уточни бренд/модель (например: 'Zara белая футболка, slim fit')."
        )
        return

    # 4) Красивый вывод: строка с title и кнопка-ссылка
    lines = []
    buttons = []
    for i, (url, title) in enumerate(found[:5]):
        emoji = EMOJIS[i] if i < len(EMOJIS) else "🛍️"
        label = (title or "Товар").strip()
        if len(label) > 80:
            label = label[:77] + "..."
        lines.append(f"{emoji} {label}\n{url}")
        buttons.append([InlineKeyboardButton(f"{emoji} Открыть", url=url)])

    text = "Вот твой аутфит (реальные product pages):\n\n" + "\n\n".join(lines)
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
