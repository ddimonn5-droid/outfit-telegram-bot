import re

_URL_RE = re.compile(r"https?://[^\s)>\]]+")

def extract_urls(text: str):
    seen, out = set(), []
    for m in _URL_RE.findall(text or ""):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
