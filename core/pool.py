"""Today's topic pool, from every source.

Google Trends is deliberately NOT the pool. Its daily list is names,
deaths and politics -- "LAUNCH IS NOT WHAT YOU THINK" is not a video.
So topics come from sources that are concept-shaped, and Trends is a
ranking signal: a topic also being searched today ranks higher.

The verdicts here RANK and HARD-DROP. They do not clear anything.
SHORT means shortlisted; core.gate decides what may be built. Three
recently dead people topped this pool when a regex verdict was
mistaken for clearance.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TRENDS_GEOS = [g.strip() for g in config.TRENDS_GEOS if g.strip()]
TRENDS_ENDPOINTS = [
    "https://trends.google.com/trending/rss?geo={geo}",
    "https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}",
]
HT = {"ht": "https://trends.google.com/trending/rss"}

WIKI_TOP = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            "en.wikipedia/all-access/{y}/{m}/{d}")
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

REDDIT_FEEDS = [
    ("til", "https://www.reddit.com/r/todayilearned/top/.rss?t=day"),
    ("eli5", "https://www.reddit.com/r/explainlikeimfive/top/.rss?t=day"),
]
HN_FRONT = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40"


# --------------------------------------------------------------- the filter

RISKY = {
    "death": ["dies", "died", "dead", "death", "deaths", "killed", "kills",
              "kill", "fatal", "fatally", "obituary", "passes away",
              "passed away", "funeral", "mourns", "tribute", "tue", "tué",
              "mort", "décès"],
    "crime": ["arrest", "arrested", "indicted", "court", "trial", "verdict",
              "guilty", "lawsuit", "sues", "suing", "allegation",
              "allegations", "alleged", "accuser", "assault", "abuse",
              "shooting", "shootings", "stabbed", "murder", "homicide",
              "fraud", "scandal", "prison", "jail", "charged with",
              "criminal charges", "faces charges", "shot dead",
              "shot and killed", "police say", "under investigation",
              "vandalise", "vandalize", "vandalised", "vandalized",
              "terror", "terrorist", "bomb threat", "bomb threats"],
    "politics": ["election", "elections", "referendum", "senate", "congress",
                 "parliament", "president", "presidential", "prime minister",
                 "governor", "candidate", "campaign", "impeach", "tariff",
                 "tariffs", "sanction", "sanctions", "protest", "protests",
                 "riot", "rioting", "riots", "war", "ceasefire", "troops",
                 "missile", "missiles", "strike", "strikes", "airstrike",
                 "immigration", "deportation", "abortion", "republican",
                 "republicans", "democrat", "democrats", "gop", "dems",
                 "separatism", "separation from", "minister", "diplomat",
                 "zelenskyy", "trump", "putin", "netanyahu",
                 # Political history is still politics in a 30s video with
                 # a provocative hook on the front.
                 "colonialism", "colonial", "imperialism", "nationalism",
                 "nationalist", "slavery", "slave trade", "genocide",
                 "apartheid", "holocaust", "ethnic cleansing", "partition",
                 "annexation", "occupation", "insurgency", "coup",
                 "dictator", "regime", "communist", "fascist", "nazi",
                 "caste system", "racism", "racist", "supremacy",
                 "reparations", "treaty of", "border dispute"],
    "disaster": ["earthquake", "hurricane", "typhoon", "tornado", "wildfire",
                 "flood", "flooding", "tsunami", "crash", "crashes",
                 "collapse", "collapsed", "explosion", "evacuation",
                 "evacuated", "missing", "rescue", "accident"],
    "medical": ["diagnosis", "diagnosed", "cancer", "tumour", "tumor",
                "hospitalized", "hospitalised", "overdose", "suicide",
                "outbreak", "symptoms", "cure", "coma"],
    "personal": ["divorce", "divorced", "split", "splits", "separation",
                 "dating", "pregnant", "pregnancy", "engaged", "wedding",
                 "breakup", "cheating", "feud", "slams", "clapback",
                 "apologizes", "apologises", "ouster", "under fire"],
}

LOW_VALUE = [
    "powerball", "mega millions", "lottery", "lotto", "jackpot",
    "vs", "score", "scores", "final score", "box score", "standings",
    "where to watch", "live stream", "streaming", "what time", "tv channel",
    "stock", "stock price", "share price", "near me", "login", "sign in",
    "outage", "weather", "forecast", "results today", "winning numbers",
    "horoscope", "zodiac", "connections hints", "wordle", "crossword",
    "mail", "live", "news", "today", "exam", "fixtures", "lineup",
    "highlights", "full match", "episode", "season", "trailer", "renders",
]

# Wikipedia namespaces and list pages that are never a topic.
WIKI_SKIP = re.compile(
    r"^(Main_Page|Special:|Portal:|Wikipedia:|Help:|Category:|Template:|"
    r"File:|Talk:|Draft:)|^List_of|^Lists_of|^Deaths_in|^\d{4}$|"
    r"\(disambiguation\)|^20\d\d_|_filmography$", re.I)

# Wikipedia's most-viewed list is dominated by whatever released this
# week: films, series, games, PPV events. The traffic is real but there is
# nothing to explain -- "Street Fighter (2026 film) is not what you think"
# is a movie page, not a video. Caught on the description, which states the
# type plainly.
MEDIA_DESC = re.compile(
    r"\b(\d{4}\s+)?(american|indian|british|canadian|japanese|korean|"
    r"french|australian|chinese|spanish|italian|german)?\s*"
    r"(film|films|movie|miniseries|series|sitcom|anime|slasher|franchise|"
    r"video game|mobile game|album|studio album|ep|single|"
    r"song|soundtrack|pay-per-view|wrestling event|season|episode|"
    r"documentary|short film|novel|reality competition|game show)\b",
    re.I)

# A dead person's description carries both years: "(1902-1984)".
#
# But "deceased" is NOT the same as "historical". Presley Gerber
# (1999-2026) and Georgina Cates (1975-2026) came top of the pool because
# they died this year -- a recent death is exactly what drives Wikipedia
# pageviews, so the view bonus promoted them for it. Only a long-dead
# figure is safe material; a recent death is the worst case on the list,
# worse than a living person.
DECEASED = re.compile(r"\((?:c\.\s*)?(\d{3,4})\s*[-–—]\s*(\d{3,4})\)")
HISTORICAL_AFTER_YEARS = config.HISTORICAL_AFTER_YEARS

# Nation states, territories and political geography. "UNITED STATES IS
# NOT WHAT YOU THINK" is a politics minefield attached to a non-topic.
PLACE_DESC = re.compile(
    r"\b(countr(?:y|ies)|sovereign state|nation|nation[- ]state|republic|"
    r"kingdom|empire|territory|territories|province|prefecture|"
    r"federal state|federal district|disputed territory|autonomous|"
    r"landlocked|island countr|city[- ]state|military base|capital city|"
    r"city in|town in|village in|region of|region in|state of|"
    r"state in|county in|district of|district in|unincorporated)\b", re.I)

# A GROUP OF PEOPLE is never the subject. "X is not what you think" aimed
# at a nationality, an ethnic group, a religion or a caste is an insult
# with a hook on it, however the facts are framed. Dropped on safety, not
# on quality, so it is never recoverable by loosening the value filter.
GROUP_DESC = re.compile(
    r"\b(ethnic group|ethnic groups|ethnicity|people of|peoples of|"
    r"indigenous|aboriginal|tribe|tribal|caste|clan|"
    r"religion|religious group|religious movement|denomination|sect|"
    r"nationality|demonym|diaspora|minority group|"
    r"racial|race in|ethnic minority)\b", re.I)

# Age-restricted or demonetisation-adjacent brands.
VICE_DESC = re.compile(
    r"\b(beer|lager|ale|brewery|whisky|whiskey|vodka|gin|rum|wine|"
    r"tobacco|cigarette|vape|casino|betting|gambling|sportsbook|"
    r"cannabis|marijuana)\b", re.I)


def deceased_long_ago(detail: str) -> tuple[bool, int]:
    """(is_historical, death_year). A death inside the last
    HISTORICAL_AFTER_YEARS years is never treated as historical."""
    m = DECEASED.search(detail)
    if not m:
        return False, 0
    try:
        died = int(m.group(2))
    except ValueError:
        return False, 0
    return (dt.date.today().year - died) >= HISTORICAL_AFTER_YEARS, died

# A Wikipedia one-line description that means "this is a person".
#
# The occupation list can never be complete -- it missed "mountaineer" and
# "photographer" -- so the reliable signal is Wikipedia's own convention of
# putting life dates in the description: "(1914-1986)" or "(born 1983)".
# Those are checked separately, because an earlier version folded the date
# pattern into this alternation and a trailing \b after the closing
# bracket meant it could never match at all.
PERSON_DESC = re.compile(
    r"\b(actor|actress|singer|rapper|musician|footballer|player|athlete|"
    r"politician|president|senator|minister|businessman|businesswoman|"
    r"entrepreneur|journalist|author|writer|poet|director|producer|model|"
    r"comedian|boxer|wrestler|cricketer|driver|coach|manager|youtuber|"
    r"streamer|influencer|personality|mountaineer|photographer|explorer|"
    r"environmentalist|scientist|physicist|chemist|biologist|astronomer|"
    r"mathematician|philosopher|economist|historian|archaeologist|"
    r"inventor|engineer|painter|sculptor|architect|composer|conductor|"
    r"chef|pilot|astronaut|cosmonaut|surgeon|physician|nurse|activist|"
    r"general|admiral|monarch|king|queen|emperor|empress|pope|saint|"
    r"missionary|rabbi|imam|monk|nun|spy|outlaw|pirate)s?\b", re.I)

BORN_ONLY = re.compile(r"\bborn\s+(?:c\.\s*)?\d{3,4}", re.I)


def is_person(detail: str) -> bool:
    return bool(PERSON_DESC.search(detail)
                or DECEASED.search(detail)
                or BORN_ONLY.search(detail))


def risk_of(text: str) -> list[str]:
    blob = " " + text.lower() + " "
    return [name for name, words in RISKY.items()
            if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", blob)
                   for w in words)]


def low_value(text: str) -> str:
    blob = text.lower()
    for w in LOW_VALUE:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", blob):
            return w
    return ""


# Wikipedia gives a subject ("Daylight saving time"). Reddit and Hacker
# News give a CLAIM ("honey never spoils and 3000-year-old honey is still
# edible"), and pasting that into the template produces "HONEY NEVER
# SPOILS AND ... IS NOT WHAT YOU THINK", which is not a sentence. So the
# hook subject is derived, never copied. The real pipeline hands this to
# the LLM; this heuristic exists so the probe can show whether a source's
# topics are even capable of yielding a subject.
_LEAD = re.compile(
    r"^(til\s+|eli5[:\s]+|why\s+|how\s+|what\s+|when\s+|where\s+|"
    r"does\s+|do\s+|did\s+|is\s+|are\s+|was\s+|were\s+|can\s+|could\s+|"
    r"the\s+|a\s+|an\s+|that\s+|show\s+hn[:\s]+|ask\s+hn[:\s]+)+", re.I)
_STOP_AT = {
    "never", "always", "is", "are", "was", "were", "can", "could", "will",
    "would", "has", "have", "had", "does", "do", "did", "makes", "made",
    "look", "looks", "seem", "seems", "actually", "really", "still",
    "because", "when", "which", "that", "who", "and", "but", "of", "in",
    "on", "for", "to", "from", "with", "why", "how",
}


def hook_subject(topic: str, source: str) -> str:
    """A 1-3 word subject the template can carry."""
    if source == "wikipedia":
        # "Street Fighter (2026 film)" -> "Street Fighter". Wikipedia's
        # disambiguators are for Wikipedia, not for a hook.
        return re.sub(r"\s*\([^)]*\)\s*$", "", topic).strip() or topic
    s = _LEAD.sub("", topic.strip()).strip(" ,.:;?!\"'")
    words = re.findall(r"[\w'\-]+", s)
    picked: list[str] = []
    for w in words:
        if w.lower() in _STOP_AT and picked:
            break
        picked.append(w)
        if len(picked) >= 3:
            break
    return " ".join(picked) or topic.split()[0] if topic.split() else topic


def dedup_key(topic: str) -> str:
    words = sorted(w for w in re.findall(r"[a-z0-9]+", topic.lower())
                   if len(w) > 2)
    return " ".join(words[:8])


# ------------------------------------------------------------------ fetching

def get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_json(url: str, timeout: int = 25) -> dict:
    return json.loads(get(url, timeout))


def _wiki_ranks(day: dt.date) -> dict:
    """{article: rank} for one day's most-viewed list."""
    url = WIKI_TOP.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")
    data = get_json(url)
    arts = (data.get("items") or [{}])[0].get("articles") or []
    return {a.get("article", ""): int(a.get("rank") or i + 1)
            for i, a in enumerate(arts) if a.get("article")}


def src_wikipedia(limit: int = 80) -> tuple[list[dict], str]:
    """Most-viewed articles, scored by how much they MOVED.

    Raw view count is a popularity signal but not a daily one: the same
    perennial articles sit near the top every day, which is how an
    evergreen topic that could have been published any decade ends up
    leading the pool. What makes a topic today's is the JUMP -- an
    article people have suddenly started looking up.

    So yesterday's list is compared with the day before's. A new entrant
    or a big climber is what we want; something flat is being read at its
    usual rate and is not news to anybody.

    Two extra API calls in total, not per article.
    """
    day = dt.date.today() - dt.timedelta(days=1)   # today's counts are partial
    prev = day - dt.timedelta(days=1)
    url = WIKI_TOP.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")

    today_ranks = _wiki_ranks(day)
    try:
        prev_ranks = _wiki_ranks(prev)
    except Exception:
        prev_ranks = {}                            # no baseline, no spike bonus

    out = []
    for title, rank in sorted(today_ranks.items(), key=lambda kv: kv[1]):
        if not title or WIKI_SKIP.search(title):
            continue
        was = prev_ranks.get(title)
        if was is None:
            spike, why = (3.0, "new to the list") if prev_ranks else (0.0, "")
        else:
            climb = was - rank
            # A climb of 50+ places is a real surge; flat or falling is not.
            spike = max(0.0, min(3.0, climb / 40.0))
            why = f"up {climb} places" if climb > 0 else "flat or falling"
        out.append({
            "source": "wikipedia",
            "topic": title.replace("_", " "),
            "key": title,
            "views": 0,          # rank movement is the signal, not the level
            "rank": rank,
            "spike": spike,
            "spike_why": why,
            "detail": "",
            "url": f"https://en.wikipedia.org/wiki/{title}",
        })
        if len(out) >= limit:
            break
    return out, url


def src_trend_topics() -> tuple[list[dict], list[str]]:
    """Google Trends searches as CANDIDATES, not just a ranking signal.

    These are the only inherently same-day topics in the pool, and they
    arrive with news links attached -- which is the multi-source material
    a Wikipedia-only topic lacks. The filter and the gate still apply:
    roughly four in forty survive them, so Trends contributes rather than
    carries the day.
    """
    out, notes = [], []
    for geo in TRENDS_GEOS:
        got = False
        for tmpl in TRENDS_ENDPOINTS:
            try:
                root = ET.fromstring(get(tmpl.format(geo=geo)))
            except Exception:
                continue
            for item in root.findall(".//item"):
                q = text(item, "title")
                if not q:
                    continue
                news = []
                for n in item.findall("ht:news_item", NS):
                    t = text(n, "ht:news_item_title")
                    u = text(n, "ht:news_item_url")
                    if t and u:
                        news.append({"title": t, "url": u,
                                     "outlet": text(n, "ht:news_item_source")})
                out.append({
                    "source": "trends",
                    "topic": q,
                    "key": q,
                    "views": 0,
                    "spike": 3.0,          # a trend is by definition today's
                    "spike_why": f"trending in {geo}",
                    "geo": geo,
                    "detail": "",
                    "news": news,
                    "picture": text(item, "ht:picture"),
                    "url": news[0]["url"] if news else "",
                })
                got = True
            if got:
                break
        notes.append(f"{geo}: {'ok' if got else 'unreachable'}")
    return out, notes


def enrich_wikipedia(items: list[dict], pause: float = 0.35) -> None:
    """Pull each candidate's one-line description. This is what makes the
    person filter work: 'American actor' is unambiguous in a way a title
    never is.

    24 of 80 came back empty at 0.15s between calls, and every miss
    becomes a CHECK the LLM then has to spend a call on, so the pause is
    longer and a 429 is retried once.
    """
    for it in items:
        if it["source"] != "wikipedia":
            continue
        d = None
        for attempt in (1, 2):
            try:
                q = urllib.parse.quote(it["key"], safe="")
                d = get_json(WIKI_SUMMARY.format(title=q), timeout=15)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt == 1:
                    time.sleep(2.0)
                    continue
                it["enrich_error"] = f"HTTP {exc.code}"
                break
            except Exception as exc:
                it["enrich_error"] = type(exc).__name__
                break
        if d is None:
            it["detail"] = ""
            continue
        it["detail"] = (d.get("description") or "").strip()
        it["extract"] = (d.get("extract") or "").strip()
        it["picture"] = ((d.get("thumbnail") or {}).get("source") or "")
        time.sleep(pause)


def src_reddit() -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for i, (tag, url) in enumerate(REDDIT_FEEDS):
        # Reddit 429'd the second feed when both were fetched back to back.
        if i:
            time.sleep(3.0)
        raw = None
        for attempt in (1, 2):
            try:
                raw = get(url)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 1:
                    time.sleep(6.0)
                    continue
                notes.append(f"{tag}: HTTP {exc.code}")
                break
            except Exception as exc:
                notes.append(f"{tag}: {type(exc).__name__}")
                break
        if raw is None:
            continue
        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            notes.append(f"{tag}: not XML ({str(exc)[:40]})")
            continue
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns)[:30]:
            t = entry.find("a:title", ns)
            link = entry.find("a:link", ns)
            title = (t.text or "").strip() if t is not None else ""
            if not title:
                continue
            # "TIL that ..." / "ELI5: ..." -> the claim itself
            claim = re.sub(r"^(TIL\s+(that\s+)?|ELI5[:\s]+)", "", title,
                           flags=re.I).strip()
            out.append({
                "source": f"reddit/{tag}",
                "topic": claim[:110],
                "key": claim[:110],
                "views": 0,
                "detail": "",
                "url": (link.get("href") if link is not None else ""),
            })
        notes.append(f"{tag}: ok")
    return out, notes


def src_hn() -> tuple[list[dict], str]:
    data = get_json(HN_FRONT)
    out = []
    for h in data.get("hits", []):
        title = (h.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "source": "hn",
            "topic": title[:110],
            "key": title[:110],
            "views": h.get("points", 0),
            "detail": "",
            "url": h.get("url") or
                   f"https://news.ycombinator.com/item?id={h.get('objectID')}",
        })
    return out, HN_FRONT


# Common words that must never carry a boost on their own. "pascal" put a
# Pascal compiler at the top of the pool because a Quebec politician named
# Pascal Paradis was trending, and "space" boosted "latent space".
BOOST_STOPWORDS = {
    "news", "live", "today", "now", "new", "the", "and", "for", "with",
    "film", "movie", "game", "season", "show", "time", "day", "world",
    "space", "launch", "update", "best", "top", "free", "full", "open",
    "city", "state", "united", "south", "north", "east", "west", "league",
    "cup", "final", "score", "stock", "price", "weather", "mail", "login",
}


def src_trend_phrases() -> tuple[set[str], list[str]]:
    """Trends as whole queries, not a word bag.

    A boost now needs either the complete trend phrase to appear in the
    topic, or a single distinctive word -- long and not a common one.
    A bare common word matching is how a Quebec politician's first name
    promoted a Pascal compiler to the top of the pool.
    """
    phrases, notes = set(), []
    for geo in TRENDS_GEOS:
        got = False
        for tmpl in TRENDS_ENDPOINTS:
            try:
                raw = get(tmpl.format(geo=geo))
                root = ET.fromstring(raw)
            except Exception:
                continue
            for item in root.findall(".//item"):
                el = item.find("title")
                q = (el.text or "").strip().lower() if el is not None else ""
                q = re.sub(r"\s+", " ", q)
                if not q:
                    continue
                if " " in q:
                    phrases.add(q)
                elif len(q) >= 7 and q not in BOOST_STOPWORDS:
                    phrases.add(q)
                got = True
            if got:
                break
        notes.append(f"{geo}: {'ok' if got else 'unreachable'}")
    return phrases, notes


# ------------------------------------------------------------------ scoring

# Trends has priority: a surviving trend topic should always outrank an
# evergreen one. The other sources fill the day, which on the observed
# yield (about four of forty trends survive) is most of it.
WEIGHT = {"trends": 6.0, "reddit/til": 3.5, "wikipedia": 3.0,
          "reddit/eli5": 3.0, "hn": 2.0}


def hook_line(subject: str, is_person: bool) -> str:
    """The channel's one fixed line. A person is a who, not a what:
    "TENZING NORGAY IS NOT WHO YOU THINK"."""
    return (f"{subject.upper()} IS NOT "
            f"{'WHO' if is_person else 'WHAT'} YOU THINK")


def classify(it: dict) -> tuple[str, str]:
    """Return (verdict, why). SHORT / CHECK / DROP / JUNK.

    SHORT means shortlisted, NOT cleared. Nothing is publishable on the
    strength of these regexes -- the LLM gate decides, and this layer only
    hard-drops the obvious and ranks the rest.
    """
    text = f"{it['topic']} {it.get('detail','')} {it.get('extract','')[:300]}"
    risks = risk_of(text)
    if risks:
        return "DROP", ", ".join(risks)
    junk = low_value(it["topic"])
    if junk:
        return "JUNK", f"nothing to explain ({junk})"
    detail = it.get("detail", "")

    # Order matters: the death check runs before anything that could
    # promote the item, because a recent death is what put it here.
    if detail:
        historical, died = deceased_long_ago(detail)
        if died and not historical:
            return "DROP", f"died {died}, too recent"
        if MEDIA_DESC.search(detail):
            return "JUNK", f"media release ({detail[:38]})"
        if GROUP_DESC.search(detail):
            return "DROP", f"a group of people ({detail[:34]})"
        if PLACE_DESC.search(detail):
            return "JUNK", f"place or state ({detail[:38]})"
        if VICE_DESC.search(detail):
            return "DROP", f"age-restricted subject ({detail[:30]})"
        if is_person(detail):
            it["is_person"] = True
            if historical:
                it["historical"] = True
                return "SHORT", f"historical figure, died {died}"
            return "CHECK", f"living person: {detail[:38]}"
        return "SHORT", ""

    # No description means no person check ran. Every Reddit and HN item
    # lands here, which is how "ACTRESS JODIE FOSTER IS NOT WHAT YOU
    # THINK" reached the shortlist -- those sources carry claims about
    # living people with nothing for a regex to catch. They all go to the
    # gate.
    return "CHECK", ("no description -- person check did not run"
                     if it["source"] == "wikipedia"
                     else f"{it['source']} claim -- unverified subject")


def score(it: dict, trend_phrases: set[str]) -> float:
    s = WEIGHT.get(it["source"], 1.0)
    blob = " " + re.sub(r"[^a-z0-9 ]+", " ", it["topic"].lower()) + " "
    blob = re.sub(r"\s+", " ", blob)
    hits = [p for p in trend_phrases
            if f" {p} " in blob or (" " not in p and f" {p} " in blob)]
    if hits:
        s += 2.5 + 0.3 * min(4, len(hits))
        it["trending_on"] = sorted(hits)[:3]
    if it.get("historical"):
        s += 1.2
    # What makes a topic today's rather than merely popular.
    s += float(it.get("spike") or 0.0)
    # A multi-word subject carries the hook better than a bare noun:
    # "DAYLIGHT SAVING TIME IS NOT WHAT YOU THINK" works, "LAUNCH" does not.
    n = len(it["topic"].split())
    if n == 1:
        s -= 1.5
    elif 2 <= n <= 7:
        s += 0.8
    if it.get("picture"):
        s += 0.4
    if it["source"] == "wikipedia" and it["views"]:
        s += min(1.5, it["views"] / 400_000)
    return s


# --------------------------------------------------------------------- main

def report() -> None:
    enrich = "--no-enrich" not in sys.argv
    print(__doc__.splitlines()[0].rstrip(","))
    print()

    items: list[dict] = []

    # The media filter removes most of Wikipedia's top-80, so the depth of
    # the list is the lever when the pool comes back thin -- not the risk
    # filter, which is the one thing that should never be loosened.
    wiki_limit = 80
    for a in sys.argv[1:]:
        if a.startswith("--wiki="):
            wiki_limit = max(10, min(400, int(a.split("=", 1)[1])))

    for label, fn in (("wikipedia", lambda: src_wikipedia(wiki_limit)),
                      ("hn", src_hn)):
        try:
            got, note = fn()
            items += got
            print(f"  {label:10} {len(got):3} candidates   {note[:62]}")
        except Exception as exc:
            print(f"  {label:10}   0 candidates   FAILED: "
                  f"{type(exc).__name__} {str(exc)[:50]}")

    try:
        got, notes = src_reddit()
        items += got
        print(f"  {'reddit':10} {len(got):3} candidates   {', '.join(notes)}")
    except Exception as exc:
        print(f"  {'reddit':10}   0 candidates   FAILED: {str(exc)[:50]}")

    try:
        trend_phrases, notes = src_trend_phrases()
        print(f"  {'trends':10} {len(trend_phrases):3} phrases (boost only) "
              f"  {', '.join(notes)}")
    except Exception as exc:
        trend_phrases = set()
        print(f"  {'trends':10}   0 words        FAILED: {str(exc)[:50]}")

    if not items:
        sys.exit("\nEvery source failed. Nothing to judge.")

    if enrich:
        wiki = [i for i in items if i["source"] == "wikipedia"]
        print(f"\n  enriching {len(wiki)} Wikipedia candidates with their "
              f"one-line descriptions...")
        enrich_wikipedia(wiki)
        failed = sum(1 for i in wiki if i.get("enrich_error"))
        if failed:
            print(f"  ({failed} descriptions could not be fetched)")

    # Same story often lands in Wikipedia, HN and Reddit at once. Keep the
    # heaviest-weighted copy and record where else it showed up, because
    # appearing in several sources is itself a signal.
    best: dict[str, dict] = {}
    for it in items:
        k = dedup_key(it["topic"])
        if not k:
            continue
        prev = best.get(k)
        if prev is None:
            it["also_in"] = []
            best[k] = it
        else:
            keep, drop = ((it, prev)
                          if WEIGHT.get(it["source"], 1) >
                             WEIGHT.get(prev["source"], 1)
                          else (prev, it))
            keep["also_in"] = sorted(set(prev.get("also_in", [])
                                         + it.get("also_in", [])
                                         + [drop["source"]]))
            best[k] = keep
    merged = len(items) - len(best)
    items = list(best.values())
    if merged:
        print(f"\n  merged {merged} duplicate(s) across sources")

    counts = {"SHORT": 0, "CHECK": 0, "DROP": 0, "JUNK": 0}
    for it in items:
        it["verdict"], it["why"] = classify(it)
        it["subject"] = hook_subject(it["topic"], it["source"])
        it["score"] = score(it, trend_phrases)
        if it.get("also_in"):
            it["score"] += 0.6 * len(it["also_in"])
        counts[it["verdict"]] += 1

    print()
    print("=" * 74)
    print(f"{len(items)} candidates   "
          + "   ".join(f"{k.lower()} {v}" for k, v in counts.items()))
    print("=" * 74)

    keeps = sorted([i for i in items if i["verdict"] == "SHORT"],
                   key=lambda i: -i["score"])
    print(f"\nSHORTLIST -- what the gate would be asked to approve:\n")
    for i, it in enumerate(keeps[:12], 1):
        trend = (" [trending: " + ", ".join(it["trending_on"]) + "]"
                 if it.get("trending_on") else "")
        also = (" also in " + ", ".join(it["also_in"])
                if it.get("also_in") else "")
        print(f"{i:2}. {it['topic'][:66]}")
        print(f"    {it['source']:12} score {it['score']:.1f}{trend}{also}")
        if it.get("detail"):
            print(f"    {it['detail'][:66]}")
        print(f'    HOOK: "{hook_line(it["subject"], it.get("is_person", False))}"')
        print()

    checks = [i for i in items if i["verdict"] == "CHECK"][:6]
    if checks:
        print("SENT TO THE LLM GATE (person-shaped or undescribed):")
        for it in checks:
            print(f"  {it['topic'][:46]:48} {it['why'][:40]}")
        print()

    print(f"Shortlisted today: {counts['SHORT']}, plus {counts['CHECK']} "
          f"the gate must rule on. The plan needs 1 approved topic a day.")
    print("Nothing here is cleared -- SHORT means it survived the regexes, "
          "and no video gets made from a topic the LLM gate has not passed.")
    if counts["SHORT"] < 5:
        print("That is thinner than it should be -- worth loosening the "
              "low-value list rather than the risk list.")




# ------------------------------------------------------- the pipeline's API

def _used() -> set[str]:
    """Topics already made, so the channel never repeats itself."""
    try:
        return set(json.loads(config.USED_FILE.read_text()))
    except (OSError, json.JSONDecodeError):
        return set()


def mark_used(topic: str) -> None:
    used = _used()
    used.add(dedup_key(topic))
    config.USED_FILE.write_text(json.dumps(sorted(used), indent=0))


def gather(wiki_limit: int = 0, enrich: bool = True) -> dict:
    """Every source, filtered, deduped, ranked.

    Returns {"short": [...], "check": [...], "rejected": n, "notes": [...]}
    ordered best first. Each source fails independently: one dead endpoint
    must not cost the day's video.
    """
    wiki_limit = wiki_limit or config.WIKI_DEPTH
    items: list[dict] = []
    notes: list[str] = []

    try:
        got, _ = src_wikipedia(wiki_limit)
        items += got
        notes.append(f"wikipedia {len(got)}")
    except Exception as exc:
        notes.append(f"wikipedia FAILED {type(exc).__name__}")

    try:
        got, _ = src_hn()
        items += got
        notes.append(f"hn {len(got)}")
    except Exception as exc:
        notes.append(f"hn FAILED {type(exc).__name__}")

    try:
        got, rnotes = src_reddit()
        items += got
        notes.append(f"reddit {len(got)} ({'; '.join(rnotes)})")
    except Exception as exc:
        notes.append(f"reddit FAILED {type(exc).__name__}")

    try:
        got, tnotes = src_trend_topics()
        items += got
        notes.append(f"trends {len(got)} topics ({'; '.join(tnotes)})")
    except Exception as exc:
        notes.append(f"trend topics FAILED {type(exc).__name__}")

    try:
        phrases, _ = src_trend_phrases()
        notes.append(f"{len(phrases)} trend phrases for boosting")
    except Exception as exc:
        phrases = set()
        notes.append(f"trend phrases FAILED {type(exc).__name__}")

    if enrich:
        wiki = [i for i in items if i["source"] == "wikipedia"]
        enrich_wikipedia(wiki)
        missing = sum(1 for i in wiki if i.get("enrich_error"))
        if missing:
            notes.append(f"{missing} descriptions unavailable")

    # Dedup across sources, keeping the heaviest-weighted copy.
    best: dict[str, dict] = {}
    for it in items:
        k = dedup_key(it["topic"])
        if not k:
            continue
        prev = best.get(k)
        if prev is None:
            it["also_in"] = []
            best[k] = it
            continue
        keep, drop = ((it, prev) if WEIGHT.get(it["source"], 1) >
                      WEIGHT.get(prev["source"], 1) else (prev, it))
        keep["also_in"] = sorted(set(prev.get("also_in", []) +
                                     it.get("also_in", []) +
                                     [drop["source"]]))
        best[k] = keep

    already = _used()
    short, check, rejected, repeats = [], [], 0, 0
    for it in best.values():
        if dedup_key(it["topic"]) in already:
            repeats += 1
            continue
        it["verdict"], it["why"] = classify(it)
        it["subject"] = hook_subject(it["topic"], it["source"])
        it["hook"] = hook_line(it["subject"], it.get("is_person", False))
        it["score"] = score(it, phrases)
        if it.get("also_in"):
            it["score"] += 0.6 * len(it["also_in"])
        if it["verdict"] == "SHORT":
            short.append(it)
        elif it["verdict"] == "CHECK":
            check.append(it)
        else:
            rejected += 1

    if repeats:
        notes.append(f"{repeats} already used")
    short.sort(key=lambda i: -i["score"])
    check.sort(key=lambda i: -i["score"])
    return {"short": short, "check": check,
            "rejected": rejected, "notes": notes}
