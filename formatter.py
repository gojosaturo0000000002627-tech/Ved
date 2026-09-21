"""Card formatter — bilkul user ke format mein output banata hai."""
from typing import Optional

from sources.platforms import INDIA_PLATFORMS

LANG_LABEL = {"jp": "Japanese audio", "en": "English dub", "hi": "Hindi dub"}


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
    if total:
        status = info.get("status") or ""
        word = "planned" if status != "FINISHED" else "total"
        L.append(f"Season 1: {total} episodes {word}")

    # Season details
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

    # Next episode
    L.append("")
    L.append("📅 Next episode:")
    nb = info.get("next_by_lang") or {}
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
