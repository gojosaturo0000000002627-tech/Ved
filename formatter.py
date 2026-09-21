"""Card formatter — bilkul user ke format mein output banata hai."""
from datetime import datetime
from typing import Optional

from sources.platforms import INDIA_PLATFORMS

LANG_LABEL = {"jp": "Japanese audio", "en": "English dub", "hi": "Hindi dub"}


def _pretty_date(s) -> Optional[str]:
    """'2026-08-29' -> '29 Aug 2026'; '2026-08' -> 'Aug 2026'."""
    if not s:
        return None
    s = str(s)
    for fmt, out in (("%Y-%m-%d", "%d %b %Y"), ("%Y-%m", "%b %Y")):
        try:
            return datetime.strptime(s, fmt).strftime(out)
        except ValueError:
            continue
    return s


def _eps(x: Optional[int]) -> str:
    return str(x) if x is not None else "?"


def _next_line(value: Optional[str]) -> str:
    return value or "To be announced"


def _lang_eps(v) -> str:
    return f"{v} episodes" if v is not None else "Unknown"


def _hi_source_tag(info: dict) -> str:
    """Hindi dub count kis source se aaya — trust ke liye."""
    src = info.get("hi_source")
    if src == "youtube":
        return " (YouTube se track kiya)"
    if src == "manual":
        return " (/setep se set kiya)"
    return ""


def _hi_line(info: dict) -> str:
    """Hindi dub line — count, ya note, ya Unknown."""
    if info.get("hi_aired") is not None:
        return f"{info['hi_aired']} episodes{_hi_source_tag(info)}"
    if info.get("hi_note"):
        return info["hi_note"]
    return "Unknown"


def format_card(info: dict) -> str:
    """Aggregated info -> user-style card text."""
    L = []
    L.append(f"🎬 {info.get('title', '?')}")

    # Native title (agar Japanese naam alag hai to chhota sa extra)
    romaji = info.get("romaji")
    if romaji and romaji.lower() != (info.get("title") or "").lower():
        L.append(f"   ({romaji})")

    L.append(f"📌 Status: {info.get('status_display', '?')}")

    # Platforms
    platforms = info.get("platforms") or []
    if platforms:
        L.append("")
        L.append("📺 Available platforms:")
        for p in platforms:
            reg = " — India" if p.get("region") == "India" else ""
            L.append(f"• {p['name']}{reg}")
        # Audio / Subtitles
        audio = ["Japanese"]
        if (info.get("en_aired") or 0) > 0 or any("en" in p.get("langs", []) for p in platforms):
            audio.append("English")
        if (info.get("hi_aired") or 0) > 0 or any("hi" in p.get("langs", []) for p in platforms):
            audio.append("Hindi")
        L.append(f"Audio: {', '.join(audio)}")
        subs = ["English"]
        if (info.get("hi_aired") or 0) > 0:
            subs.append("Hindi")
        L.append(f"Subtitles: {', '.join(subs)}")

    total = info.get("total_episodes")
    nb = info.get("next_by_lang") or {}

    if info.get("is_movie"):
        # ================= MOVIE CARD =================
        bits = []
        if info.get("duration"):
            bits.append(f"{info['duration']} min")
        rel_pretty = _pretty_date(info.get("release_date"))
        if rel_pretty:
            bits.append(f"released {rel_pretty}")
        if bits:
            L.append("Movie — " + ", ".join(bits))

        L.append("")
        L.append("🎞 Movie details:")
        year = info.get("year") or str(info.get("release_date") or "")[:4]
        L.append(f"• Movie ({year})" if year else "• Movie")
        released = (info.get("status") == "FINISHED") or (rel_pretty is not None
                    and "Upcoming" not in (info.get("status_display") or ""))
        if rel_pretty:
            L.append(f"Released: {rel_pretty}")
        elif released:
            L.append("Released: ✅")
        else:
            L.append("Released: — (abhi release nahi hui)")
        # Hindi dub — movie ke liye available/not
        if info.get("hi_aired"):
            L.append("Hindi dub: Available ✅")
        elif nb.get("hi"):
            L.append(f"Hindi dub: {nb['hi']}")
        elif info.get("hi_note"):
            L.append(f"Hindi dub: {info['hi_note']}")
        else:
            L.append("Hindi dub: No official Hindi dub found")
        L.append("Japanese audio: Available ✅" if released else "Japanese audio: —")

        # Movie: dub upcoming ho to hi dikhao, warna section skip
        if nb.get("hi") and not info.get("hi_aired"):
            L.append("")
            L.append("📅 Hindi dub:")
            L.append(f"• {nb['hi']}")
    else:
        # ================= SERIES CARD =================
        # Top line — multi-season me current group ka total (cours merged)
        seasons = info.get("seasons") or []
        top_total = total
        top_num = info.get("current_season_num") or 1
        if len(seasons) > 1:
            cur = next((s for s in seasons if s.get("is_current")), None)
            if cur and cur.get("total"):
                top_total = cur["total"]
        if top_total:
            status = info.get("status") or ""
            word = "planned" if status != "FINISHED" else "total"
            L.append(f"Season {top_num}: {top_total} episodes {word}")

        seasons = info.get("seasons") or []
        if len(seasons) > 1:
            # Multi-season — user format:
            # • Season 1 (2018) / Released / Hindi dub / Japanese audio
            L.append("")
            L.append("🎞 Season details:")
            for s in seasons:
                year = "ongoing" if s.get("ongoing") else str(s.get("year") or "")
                label = f"• Season {s['num']}"
                if year:
                    label += f" ({year})"
                L.append(label)
                tot = s.get("total")
                jp = s.get("jp_aired")
                if tot:
                    L.append(f"Released: {jp if jp is not None else 0}/{tot} episodes")
                elif jp:
                    L.append(f"Released: {jp} episodes")
                # Hindi dub — current season ke liye main data (zyada sources)
                hi = s.get("hi_aired")
                note = s.get("hi_note")
                if s.get("is_current") and info.get("hi_aired") is not None:
                    hi = info["hi_aired"]
                if hi is not None:
                    line = f"Hindi dub: {hi} episodes"
                    if note:
                        line += f" {note}"
                    elif info.get("hi_source") == "manual" and s.get("is_current"):
                        line += " (/setep se set kiya)"
                    L.append(line)
                elif note:
                    L.append(f"Hindi dub: {note}")
                else:
                    L.append("Hindi dub: No official Hindi dub found")
                L.append(f"Japanese audio: {_lang_eps(jp)}")
        else:
            L.append("")
            L.append("🎞 Season details:")
            L.append("• Season 1")
            if total:
                rel = info.get("jp_aired") or info.get("en_aired") or 0
                L.append(f"Released: {rel}/{total} episodes")
            else:
                rel = info.get("jp_aired")
                if rel:
                    L.append(f"Released: {rel} episodes")
            L.append(f"Hindi dub: {_hi_line(info)}")
            L.append(f"English dub: {_lang_eps(info.get('en_aired'))}")
            L.append(f"Japanese audio: {_lang_eps(info.get('jp_aired'))}")

        # Movies / Specials — franchise ki movies bhi isi card me
        movies = info.get("movies") or []
        if movies:
            L.append("")
            L.append("🎥 Movies / Specials:")
            for mv in movies:
                y = f" ({mv['year']})" if mv.get("year") else ""
                L.append(f"• {mv['title'] or '?'}{y}")
                if mv.get("hi"):
                    L.append("  Hindi dub: Available ✅")
                elif mv.get("hi_note"):
                    L.append(f"  Hindi dub: {mv['hi_note']}")
                else:
                    L.append("  Hindi dub: No official Hindi dub found")

        # Next episode (series ke liye)
        L.append("")
        L.append("📅 Next episode:")
        finished = info.get("status") == "FINISHED"

        def _next_or(v):
            if v:
                return v
            if finished:
                return "All episodes released"
            return "To be announced"

        L.append(f"• Japanese audio: {_next_or(nb.get('jp'))}")
        L.append(f"• English dub: {_next_or(nb.get('en'))}")
        L.append(f"• Hindi dub: {_next_or(nb.get('hi'))}")

    L.append("")
    L.append("⏱ Last checked:")
    L.append(info.get("checked_at", "?"))

    return "\n".join(L)


def relevant_platforms(info: dict, lang: str) -> list:
    """Notification ke liye us language wale platforms hi dikhao.

    Hindi notification -> jahan Hindi dub hai (jaise Crunchyroll),
    poora platform list nahi.
    """
    platforms = info.get("platforms") or []
    if lang in ("hi", "en"):
        rel = [p for p in platforms if lang in (p.get("langs") or [])]
        if rel:
            return rel
        # langs unknown ho to India wale platforms pehle
        return [p for p in platforms if p.get("region") == "India"] or platforms
    return platforms  # jp — koi bhi main platform


def format_notification(info: dict, lang: str, ep: int, total: Optional[int]) -> str:
    """Naya episode notification message (HTML) — user ke format jaisa:

    🔔 Black Torch — Naya Episode!

    📌 Episode 4 (Hindi dub) aa chuka hai 🎉
    📺 Platform: Crunchyroll (India)
    📈 Hindi dub: 4/12 episodes
    ⏱ 16 Sep 2026, 1:10 PM IST
    """
    import html as _html
    title = _html.escape(str(info.get("title", "?")))
    plats = relevant_platforms(info, lang)
    names = [p["name"] + (" (India)" if p.get("region") == "India" else "")
             for p in plats[:3]]
    plat_str = ", ".join(names) if names else "— (platform par check karo)"
    total_str = f"/{total}" if total else ""

    if info.get("is_movie"):
        # Movie notification — dub aa gayi
        return (
            f"🔔 <b>{title}</b> — Hindi Dub Aa Gayi! 🎉\n\n"
            f"📺 Platform: {_html.escape(plat_str)}\n"
            f"⏱ {info.get('checked_at', '?')}"
        )

    return (
        f"🔔 <b>{title}</b> — Naya Episode!\n\n"
        f"📌 Episode <b>{ep}</b> ({LANG_LABEL[lang]}) aa chuka hai 🎉\n"
        f"📺 Platform: {_html.escape(plat_str)}\n"
        f"📈 {LANG_LABEL[lang]}: {ep}{total_str} episodes\n"
        f"⏱ {info.get('checked_at', '?')}"
    )


def notification_watch_url(info: dict, lang: str) -> Optional[str]:
    """Us language ke pehle platform ka watch URL (button ke liye)."""
    for p in relevant_platforms(info, lang):
        if p.get("url"):
            return p["url"]
    return None
