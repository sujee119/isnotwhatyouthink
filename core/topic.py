"""Widen one approved topic into enough sources for three variations.

The pool gives a subject and an angle but almost no material: Wikipedia's
summary is a sentence, and a Reddit or HN item is a single link. Three
scripts written from one article would be three versions of the same
script, which the similarity guard would reject anyway.

So each topic is widened:

  wikipedia  the article's own lead section, several paragraphs
  news       a Google News search for the subject, links decoded rather
             than scraped, then each publisher page fetched
  origin     whatever the pool item itself linked to

Then the sources are split into overlapping subsets, one per variation.
Overlapping on purpose: three scripts from three disjoint single articles
would each be thin. Each subset leads on a different source and shares the
rest, so the three videos cover the same subject from different weights of
evidence.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

import config
from core.feed import _fetch
from core.sources import domain_of, fetch_article

WIKI_EXTRACT = ("https://en.wikipedia.org/w/api.php?action=query&format=json"
                "&prop=extracts&explaintext=1&exintro=1&redirects=1"
                "&titles={title}")
WIKI_FULL = ("https://en.wikipedia.org/w/api.php?action=query&format=json"
             "&prop=extracts&explaintext=1&redirects=1&titles={title}")
NEWS_RSS = ("https://news.google.com/rss/search?q={q}"
            "&hl=en-US&gl=US&ceid=US:en")

MIN_WORDS = 90          # a source thinner than this is not worth a subset


def _json(url: str) -> dict:
    import json
    return json.loads(_fetch(url, timeout=25).decode("utf-8", "ignore"))


def wikipedia_text(title: str, full: bool = True) -> str:
    """The article text. Falls back to the intro if the full fetch fails."""
    for tmpl in ((WIKI_FULL, WIKI_EXTRACT) if full else (WIKI_EXTRACT,)):
        try:
            data = _json(tmpl.format(title=urllib.parse.quote(title, safe="")))
        except Exception:
            continue
        pages = (data.get("query") or {}).get("pages") or {}
        for page in pages.values():
            text = (page.get("extract") or "").strip()
            if len(text.split()) >= 40:
                # Reference and navigation sections carry no explanation.
                text = re.split(r"\n==\s*(?:See also|References|Notes|"
                                r"External links|Further reading|"
                                r"Bibliography)\s*==", text)[0]
                return text.strip()
    return ""


def news_links(query: str, limit: int = 12) -> list[dict]:
    """Google News search results for the subject."""
    url = NEWS_RSS.format(q=urllib.parse.quote(query))
    try:
        root = ET.fromstring(_fetch(url, timeout=25))
    except Exception:
        return []
    out = []
    for item in root.findall(".//item")[:limit * 2]:
        t = item.find("title")
        l = item.find("link")
        src = item.find("source")
        if t is None or l is None:
            continue
        out.append({
            "title": (t.text or "").strip(),
            "url": (l.text or "").strip(),
            "outlet": (src.text or "").strip() if src is not None else "",
        })
        if len(out) >= limit:
            break
    return out


def widen(item: dict, want: int = 8) -> list[dict]:
    """Collect sources for one topic, best first.

    Each source is {"kind", "title", "url", "text"}. Fetching stops once
    `want` usable sources are in hand, because every extra fetch is a few
    seconds and a chance of a 403.
    """
    subject = item.get("subject") or item["topic"]
    sources: list[dict] = []

    # 1. Wikipedia's own article, the densest single source available.
    if item.get("source") == "wikipedia" and item.get("key"):
        text = wikipedia_text(item["key"])
        if len(text.split()) >= MIN_WORDS:
            sources.append({"kind": "wikipedia", "title": item["topic"],
                            "url": item.get("url", ""), "text": text})
    else:
        # For a Reddit or HN topic, try Wikipedia for the subject anyway.
        text = wikipedia_text(subject.replace(" ", "_"))
        if len(text.split()) >= MIN_WORDS:
            sources.append({"kind": "wikipedia", "title": subject,
                            "url": "", "text": text})

    # 2. Whatever the pool item linked to.
    origin = item.get("url", "")
    if origin and "wikipedia.org" not in origin:
        text = fetch_article(origin)
        if len(text.split()) >= MIN_WORDS:
            sources.append({"kind": "origin", "title": item["topic"],
                            "url": origin, "text": text})

    # 3. News coverage of the subject.
    seen_outlets = {s.get("url", "") and domain_of(s["url"])
                    for s in sources}
    for link in news_links(subject):
        if len(sources) >= want:
            break
        text = fetch_article(link["url"])
        if len(text.split()) < MIN_WORDS:
            continue
        host = domain_of(link["url"])
        if host and host in seen_outlets:
            continue            # one article per outlet keeps angles varied
        seen_outlets.add(host)
        sources.append({"kind": "news", "title": link["title"],
                        "url": link["url"], "text": text})

    return sources


def subsets(sources: list[dict], n: int = 0) -> list[list[dict]]:
    """Split sources into n overlapping subsets, one per variation.

    Variation i leads on source i and then takes every nth source after
    it, so each script has a different primary source and a different
    supporting mix. With fewer sources than variations the extra
    variations get the whole set and lean on the prompt to differ, which
    the similarity guard then has to enforce.
    """
    n = n or config.VARIATIONS
    if not sources:
        return [[] for _ in range(n)]
    if len(sources) < n:
        return [list(sources) for _ in range(n)]

    out: list[list[dict]] = []
    for i in range(n):
        lead = sources[i]
        rest = [s for j, s in enumerate(sources) if j != i][i::n] or \
               [s for j, s in enumerate(sources) if j != i][:2]
        out.append([lead] + rest)
    return out


def describe(sources: list[dict]) -> str:
    bits = []
    for s in sources:
        host = domain_of(s["url"]) if s["url"] else s["kind"]
        bits.append(f"{s['kind']}:{host or '-'}({len(s['text'].split())}w)")
    return " ".join(bits)
