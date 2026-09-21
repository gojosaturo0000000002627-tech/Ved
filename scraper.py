"""Scrapes rareanimes.mov (Hindi/Tamil/Telugu dub catalog) + Jikan/MAL (Japanese info)
and builds multi-season anime cards."""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

IST = ZoneInfo("Asia/Kolkata")
BASE = "https://www.rareanimes.mov"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
}

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LANGS = ["Hindi", "Tamil", "Telugu", "English"]


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------- catalog

def search_rareanimes(name):
    """Search the Hindi-dub catalog. Returns [{title, url, sid}]."""
    try:
        html = _get(f"{BASE}/?s={quote_plus(name)}")
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    seen, results = set(), []
    for a in soup.find_all("a", href=True):
        href, title = a["href"], a.get_text(strip=True)
        if BASE in href and "/hindi/" in href and title and href not in seen:
            seen.add(href)
            sid = hashlib.md5(href.encode()).hexdigest()[:10]
            results.append({"title": title, "url": href, "sid": sid})
    return results[:8]


def _title_season(title):
    """'BLACK TORCH Season 1 Hindi Dubbed Episodes Download HD' -> ('Black Torch', 1)"""
    m = re.search(r"Season\s+(\d+)", title, re.I)
    n = int(m.group(1)) if m else 1
    base = re.split(r"\s+Season\s+\d+", title, flags=re.I)[0]
    base = re.split(r"\s+(Hindi|Tamil|Telugu|English|Dubbed|Episodes|Download|Movie)",
                    base, flags=re.I)[0].strip(" -–:.") or title
    return base.strip(), n


def parse_show(url):
    """Parse one catalog season page: totals, per-language episode counts, latest, network."""
    html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    total = None
    m = re.search(r"Episodes:\s*(\d+)", text)
    if m:
        total = int(m.group(1))

    season = 1
    m = re.search(r"Season:\s*(\d+)", text, re.I)
    if m:
        season = int(m.group(1))

    # episode titles (skip duplicate 'Untouched CR' lines)
    episodes = {}
    for l in lines:
        m = re.match(r"Episode\s+(\d+)\s*[–—-]\s*(.+)$", l)
        if not m:
            continue
        n, title = int(m.group(1)), m.group(2)
        if "Untouched" in title:
            continue
        title = re.sub(r"\s*NEw!\s*", "", title, flags=re.I).strip()
        if n not in episodes:
            episodes[n] = title

    # per-language episode counts: lines like 'Hindi – [links]'
    lang_counts = {}
    for lang in LANGS:
        c = sum(1 for l in lines if re.match(rf"^{lang}\s*[–—-]", l, re.I))
        if c:
            lang_counts[lang] = c

    network = None
    m = re.search(r"Network:\s*([A-Za-z0-9 .&]+)", text)
    if m:
        network = m.group(1).strip()
    if not network and "Crunchyroll Series" in text:
        network = "Crunchyroll"
    if not network and "Netflix" in text:
        network = "Netflix"
    if not network and "Anime Times" in text:
        network = "Anime Times (Prime Video add-on)"

    # page's last-update time ~ when the latest episode was added (WordPress <time>, UTC)
    last_drop = None
    t = soup.find("time")
    if t and t.get("datetime"):
        try:
            dt = datetime.fromisoformat(t["datetime"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            last_drop = dt.astimezone(IST)
        except Exception:
            pass

    return {
        "url": url, "total": total, "season": season, "episodes": episodes,
        "latest": max(episodes) if episodes else 0,
        "latest_title": episodes.get(max(episodes), "") if episodes else "",
        "lang_counts": lang_counts, "network": network, "last_drop": last_drop,
    }


def next_hindi_date(show, now=None):
    """Estimate next dub episode: page's last update + 7 days (same weekday)."""
    now = now or datetime.now(IST)
    if not show.get("last_drop"):
        return "TBA (weekly schedule)"
    nxt = show["last_drop"] + timedelta(days=7)
    if nxt.date() == now.date():
        return f"Aaj, shaam tak expected (~{nxt.strftime('%I:%M %p')} IST)"
    if nxt <= now:
        return "Aaj hi expected — thoda late ho sakta hai"
    return nxt.strftime("%a, %d %b %Y")


# ---------------------------------------------------------------- Jikan / MAL

def jikan_search(q):
    """One MAL entry: title, status, totals, broadcast, aired episode count."""
    try:
        r = requests.get("https://api.jikan.moe/v4/anime",
                         params={"q": q, "limit": 1}, headers=HEADERS, timeout=15)
        data = r.json().get("data") or []
        if not data:
            return None
        d = data[0]
        status = "Ongoing" if d.get("status") == "Currently Airing" else (
            "Finished" if d.get("status") == "Finished Airing" else (d.get("status") or "Unknown"))
        return {
            "title": d.get("title") or q,
            "status": status,
            "total": d.get("episodes"),
            "broadcast_jst": (d.get("broadcast") or {}).get("string"),
            "mal_id": d.get("mal_id"),
            "aired": d.get("episodes"),  # fallback; refined below
        }
    except Exception:
        return None


def jikan_episode_count(mal_id):
    """Count of aired episodes for a MAL entry (more accurate than 'episodes')."""
    try:
        r = requests.get(f"https://api.jikan.moe/v4/anime/{mal_id}/episodes",
                         headers=HEADERS, timeout=15)
        eps = [e for e in (r.json().get("data") or []) if e.get("aired")]
        return len(eps) or None
    except Exception:
        return None


def broadcast_to_ist(bstring):
    """'Mondays at 00:00 (JST)' -> 'Mondays ~8:30 PM IST' (JST = IST + 3:30)."""
    if not bstring:
        return None
    m = re.search(r"(Sundays|Mondays|Tuesdays|Wednesdays|Thursdays|Fridays|Saturdays)"
                  r"\s+at\s+(\d{1,2}):(\d{2})", bstring)
    if not m:
        return bstring
    day = m.group(1)
    minutes = int(m.group(2)) * 60 + int(m.group(3)) - 210
    idx = DAYS.index(day[:-1])
    if minutes < 0:
        minutes += 1440
        idx = (idx - 1) % 7
    h, mm = divmod(minutes, 60)
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{DAYS[idx]}s ~{h12}:{mm:02d} {ampm} IST"


# ---------------------------------------------------------------- the card

def build_card(query, seasons, jp_by_season, now=None):
    """Assemble the full card + metadata (ongoing flag, languages, follow target)."""
    now = now or datetime.now(IST)
    now_s = now.strftime("%d %b %Y, %I:%M %p IST")

    base = seasons[0]["base"] if seasons else query.title()
    latest_n = max(s["n"] for s in seasons) if seasons else 1

    ongoing = False
    for s in seasons:
        jp = jp_by_season.get(s["n"])
        sh = s["show"]
        last_season = s["n"] == latest_n
        if last_season and (jp and jp["status"] == "Ongoing"):
            ongoing = True
        if last_season and sh["total"] and sh["latest"] and sh["latest"] < sh["total"]:
            ongoing = True

    if seasons:
        if ongoing and len(seasons) > 1:
            status = f"Ongoing (Season {latest_n} airing; Seasons 1-{latest_n-1} finished)"
        elif ongoing:
            status = "Ongoing"
        else:
            status = "Finished ✅"
    else:
        jp1 = jp_by_season.get(1)
        status = (jp1 or {}).get("status") or "Unknown"

    lines = [f"🎬 {base}", "", f"📌 Status: {status}", "", "📺 Available platforms:"]

    # Japanese block
    jp_entry = jp_by_season.get(latest_n)
    b_ist = broadcast_to_ist(jp_entry["broadcast_jst"]) if jp_entry else None
    lines += ["• Crunchyroll — India",
              "Audio: Japanese",
              "Subtitles: English"]

    # dub block from catalog network
    nets, hindi_seen = [], False
    for s in seasons:
        sh = s["show"]
        if sh.get("lang_counts"):
            hindi_seen = True
        if sh.get("network") and sh["network"] not in nets and sh.get("lang_counts"):
            nets.append(sh["network"])
    for net in nets:
        langs = ", ".join(sorted({l for s in seasons for l in s["show"]["lang_counts"]}))
        lines += [f"• {net} — India", f"Audio: {langs or 'Hindi'}"]
    if not hindi_seen:
        lines += ["• Hindi dub: catalog me nahi mila"]

    lines += [""]

    # season details
    lines += ["🎞 Season details:"]
    for s in seasons:
        sh, jp = s["show"], jp_by_season.get(s["n"])
        jp_aired = (jikan_episode_count(jp["mal_id"]) or jp["total"] or 0) if jp else None
        total = sh["total"] or (jp or {}).get("total") or "?"
        lines += [f"• Season {s['n']}",
                  f"Released: {sh['latest'] or (jp_aired or 0)}/{total} episodes"]
        if jp_aired:
            lines.append(f"Japanese audio: {jp_aired} episodes")
        for lang in sorted(sh["lang_counts"]):
            lines.append(f"{lang} dub: {sh['lang_counts'][lang]} episodes")

    # next episode — only for ongoing
    if ongoing:
        lines += ["", "📅 Next episode:"]
        next_jp = b_ist or "To be announced"
        if jp_entry and jp_entry["total"] and jp_aired and jp_aired >= jp_entry["total"]:
            next_jp = "Season complete"
        lines.append(f"• Japanese audio: {next_jp}")
        last_show = seasons[-1]["show"]
        if last_show.get("lang_counts"):
            nxt_hi = next_hindi_date(last_show, now)
            if last_show["total"] and last_show["latest"] >= last_show["total"]:
                nxt_hi = "Season complete"
            lines.append(f"• Hindi dub: {nxt_hi}")

    lines += ["", f"⏱ Last checked: {now_s}"]

    langs = []
    for s in seasons:
        if s["n"] == latest_n:
            if jp_entry and jp_entry["status"] == "Ongoing":
                langs.append("Japanese")
            langs += [l for l in sorted(s["show"]["lang_counts"])]

    meta = {
        "base": base, "ongoing": ongoing, "languages": langs,
        "follow_target": seasons[-1] if seasons else None,
    }
    return "\n".join(lines), meta


def get_card(query):
    """Full lookup: catalog seasons + MAL entries -> (card_text, meta)."""
    results = search_rareanimes(query)
    seasons = []
    for r in results:
        try:
            show = parse_show(r["url"])
        except Exception:
            continue
        base, n = _title_season(r["title"])
        if any(s["n"] == n for s in seasons):
            continue
        seasons.append({"n": n, "base": base, "show": show,
                        "title": r["title"], "url": r["url"], "sid": r["sid"]})
    seasons.sort(key=lambda s: s["n"])

    jp_by_season = {}
    if seasons:
        base = seasons[0]["base"]
        for s in seasons:
            q = f"{base} season {s['n']}" if s["n"] > 1 else base
            jp = jikan_search(q) or jikan_search(base)
            if jp:
                jp_by_season[s["n"]] = jp
    else:
        jp = jikan_search(query)
        if jp:
            jp_by_season[1] = jp

    return build_card(query, seasons, jp_by_season)
