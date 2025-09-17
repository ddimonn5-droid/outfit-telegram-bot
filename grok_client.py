import os
import requests
from utils import extract_urls

XAI_API_KEY = os.getenv("XAI_API_KEY")
if not XAI_API_KEY:
    raise RuntimeError("Set XAI_API_KEY env var")

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"

def grok_live_search(prompt: str, model: str = "grok-4", max_results: int = 6):
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "search_parameters": {
            "mode": "on",                # всегда включён поиск
            "return_citations": True,
            "max_search_results": max_results,
            "sources": [{"type": "web"}, {"type": "news"}, {"type": "x"}],
        },
    }
    r = requests.post(XAI_CHAT_URL, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    resp = r.json()
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    text = msg.get("content") or choice.get("text") or ""
    citations = msg.get("citations") or []
    urls = list(dict.fromkeys(citations + extract_urls(text)))
    return {"text": text, "urls": urls}

def force_links(prompt: str, min_links: int = 3, **kwargs):
    first = grok_live_search(prompt, **kwargs)
    urls = first["urls"]
    if len(urls) >= min_links:
        return {"text": first["text"], "links": urls}

    # второй проход — просим только URL
    ask_links = f"Дай {min_links}–10 релевантных URL по теме: {prompt}. Только ссылки, по одной на строку."
    second = grok_live_search(ask_links, **kwargs)
    urls2 = second["urls"]
    links = (urls + urls2)[:max(min_links, len(urls) + len(urls2))]
    return {"text": first["text"] or second["text"], "links": links}
