"""AniNidhi — official Hindi dub tracker (PyPI: aninidhi).

488+ anime ka Hindi dub record: Crunchyroll, Netflix, Prime Video, Muse India.
Daily auto-refresh + offline snapshot fallback — koi API key nahi chahiye.

Isse milta hai:
  - Hindi dub kis platform par hai + kab start hua + status (Airing/Finished/TBA)
  - Weekly release math -> kitne episodes aa chuke + next episode ki date
  - Upcoming dubs -> "Starts 4 Oct 2026" (future release date)

NOTE: aninidhi.search() apne andar prefix-match karta hai, isliye hum
season-suffix hata kar multiple variants try karte hain (e.g.
"Jujutsu Kaisen 2nd Season" -> base "Jujutsu Kaisen" -> season-aware match).
"""
import re
import time
from datetime import date, timedelta

# list_all() ka cache — 488 records ka network call har baar nahi karna
# chahiye. Not-found fallback isse bahut baar chalta hai (e.g. Dark Gathering).
_LIST_TTL = 6 * 3600  # 6 ghante
_list_cache: dict = {"ts": 0.0, "data": None}


def list_all_cached() -> list:
    """aninidhi.list_all() — 6 ghante cache ke saath."""
    try:
        import aninidhi
    except ImportError:
        return []
    now = time.time()
    if _list_cache["data"] is None or now - _list_cache["ts"] > _LIST_TTL:
        try:
            _list_cache["data"] = aninidhi.list_all() or []
            _list_cache["ts"] = now
        except Exception:
            pass
    return _list_cache["data"] or []


def _norm(s: str) -> str:
    """Sirf alphanumeric, sab lowercase — spacing/punctuation sab ignore."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _words(s: str) -> list:
    return [w for w in re.sub(r"[^a-z0-9\s]", " ", (s or "").lower()).split()
            if len(w) > 1]


def _season_num(title: str) -> int | None:
    """'Jujutsu Kaisen 2nd Season' / 'Blue Box (Season 2)' -> 2"""
    m = re.search(r"(?:season|part|cour)\s*(\d+)", title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)(?:st|nd|rd|th)\s+(?:season|part|cour)", title,
                  re.IGNORECASE)
    if m:
        return int(m.group(1))
    words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7}
    m = re.search(r"\b(" + "|".join(words) + r")\s+(?:season|part)",
                  title, re.IGNORECASE)
    if m:
        return words[m.group(1).lower()]
    return None


def _title_variants(title: str) -> list:
    """'Mushoku Tensei: Jobless Reincarnation Season 3' ->
    ['...Season 3', '...Reincarnation', 'Mushoku Tensei']

    1. Original naam
    2. Season/part/cour suffix hata kar
    3. Colon se pehle wala hissa (subtitle hata kar) — aninidhi ka
       search lambe official naam pe fail hota hai, chhota naam chahiye
    """
    v = [title]
    for pat in (r"\s*[\(\[]?\s*(?:season|part|cour)\s*\d+[^\)\]]*[\)\]]?\s*$",
                r"\s*[-–—:]\s*(?:season|part|cour)\s*\d+.*$",
                r"\s*\d+(?:st|nd|rd|th)\s+(?:season|part|cour).*$"):
        s = re.sub(pat, "", title, flags=re.IGNORECASE).strip(" -–—:")
        if s and s.lower() != title.lower() and s not in v:
            v.append(s)
    # Colon-prefix: "Mushoku Tensei: Jobless Reincarnation" -> "Mushoku Tensei"
    extra = []
    for t in v:
        if ":" in t:
            pre = t.split(":")[0].strip(" -–—")
            if len(pre) >= 3 and pre not in v and pre not in extra:
                extra.append(pre)
    v.extend(extra)
    return v


def _strip_season(s: str) -> str:
    """'Jujutsu Kaisen 2nd Season' / 'Jujutsu Kaisen (Season 2)' -> 'Jujutsu Kaisen'"""
    s = re.sub(r"\([^)]*(?:season|cour|part)[^)]*\)", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d+(?:st|nd|rd|th)\s+(?:season|part|cour)\b", " ", s,
               flags=re.IGNORECASE)
    s = re.sub(r"\b(?:season|cour|part)\s*\d+\b", " ", s, flags=re.IGNORECASE)
    return s.strip(" -–—:")


def _best_match(records: list, query: str) -> dict | None:
    """Normalized title match — season awareness ke saath.

    Season suffix dono taraf strip hota hai (query aur record), phir
    season number se bonus/penalty — taki '2nd Season' wala query
    '(Season 2)' record se hi mile, kisi aur season se nahi.
    """
    if not records:
        return None
    qs = _norm(_strip_season(query))
    qw = _words(_strip_season(query))
    qseason = _season_num(query)
    best, best_score = None, -1
    for r in records:
        t = r.get("title") or ""
        ts = _norm(_strip_season(t))
        if not ts:
            continue
        if qs and ts == qs:
            score = 100
        elif qs and (qs in ts or ts in qs):
            score = 75
        elif qw and all(w in ts for w in qw):
            score = 60
        else:
            continue
        tseason = _season_num(t)
        if qseason is not None and tseason is not None:
            score += 10 if qseason == tseason else -25
        elif qseason is not None and tseason is None:
            score -= 5
        if score > best_score:
            best, best_score = r, score
    return best


def hindi_dub_status(query: str, total: int | None = None) -> dict | None:
    """Anime ka Hindi dub status.

    Return (None = source error):
      {"found": True, "platforms": [...], "eps": int|None, "next": date|None,
       "finished": bool, "upcoming": date|None, "announced": bool}
      ya {"found": False} — record nahi mila (official dub unknown)
    """
    try:
        import aninidhi
    except ImportError:
        print("[aninidhi] package installed nahi hai (pip install aninidhi)")
        return None

    rec = None
    try:
        for variant in _title_variants(query):
            records = aninidhi.search(variant) or []
            rec = _best_match(records, query)
            if rec:
                break
    except Exception as e:
        print(f"[aninidhi] search fail: {e}")
    if not rec:
        # Fallback: poore dataset me khud match karo
        # (aninidhi ka search kabhi-kabhi lambe naam pe miss kar deta hai)
        try:
            rec = _best_match(list_all_cached(), query)
        except Exception:
            rec = None
    if not rec or not rec.get("hindi_available"):
        return {"found": False}

    today = date.today()
    out = {"found": True, "platforms": [], "eps": None, "next": None,
           "finished": False, "upcoming": None, "announced": False,
           "record": rec}
    for d in rec.get("hindi_dubs") or []:
        rd = _to_date(d.get("release_date"))
        st = (d.get("status") or "").strip()
        out["platforms"].append({
            "platform": d.get("platform"),
            "release_date": str(d.get("release_date") or ""),
            "status": st,
        })
        if st.lower() == "finished":
            out["finished"] = True
            continue
        if rd and rd > today:
            # Future release — upcoming dub
            if out["upcoming"] is None or rd < out["upcoming"]:
                out["upcoming"] = rd
            continue
        if st.lower() in ("tba", "announced", "upcoming"):
            out["announced"] = True
            continue
        if st.lower() in ("airing", "ongoing") and rd and rd <= today:
            days = (today - rd).days
            eps = days // 7 + 1
            if total:
                eps = min(eps, total)
            out["eps"] = max(out["eps"] or 0, eps)
            out["next"] = rd + timedelta(days=7 * (days // 7 + 1))
    return out


def _to_date(s):
    try:
        return date.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
