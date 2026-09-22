"""
formatter.py — card + notification ka EXACT format.

Yahan koi data fetch nahi hota — sirf CardData ko text me badalna.
Isliye tests me isko directly assert kiya ja sakta hai.
"""
from __future__ import annotations

import html
from datetime import date, datetime

import config
import texts
from sources import platforms
from sources.aggregator import CardData, ExtraInfo, SeasonInfo

MAX_PLATFORM_LINES = 8

UNKNOWN = "Unknown"


# ---------------------------------------------------------------------------
# chhote helpers
# ---------------------------------------------------------------------------
def _count(count: int | None) -> str:
    return UNKNOWN if count is None else str(count)


def _count_word(count: int | None) -> str:
    """'12 episodes' ya 'Unknown' — spec ke hisaab se count ke saath word."""
    return UNKNOWN if count is None else f"{count} episodes"


def _hindi_line(count: int | None, complete_month: str | None, platforms_list: list[str], status: str | None) -> str:
    """Hindi dub count line — kabhi guess nahi, sirf real data."""
    if count is None and not platforms_list:
        return "No official Hindi dub found"
    if (status or "").lower() == "tba":
        where = f" ({', '.join(platforms_list)})" if platforms_list else ""
        return f"Announced (TBA){where}"
    if count is None:
        if (status or "").lower() == "finished":
            return "Available ✅ (complete)"      # dub poora hai, count ka record nahi
        return UNKNOWN
    if complete_month:
        return f"{count} episodes ({complete_month} me complete)"
    return f"{count} episodes"


def _platform_lines(names: list[str]) -> list[str]:
    lines = []
    for n in names[:MAX_PLATFORM_LINES]:
        lines.append(f"• {n} — India" if platforms.is_india_available(n) else f"• {n} (India me available nahi)")
    if len(names) > MAX_PLATFORM_LINES:
        lines.append(f"• ... +{len(names) - MAX_PLATFORM_LINES} more")
    return lines


def _season_head(s: SeasonInfo) -> str:
    if s.ongoing:
        tag = "(ongoing)"
    elif s.not_yet_released:
        tag = "(announced)"
    elif s.year:
        tag = f"({s.year})"
    else:
        tag = ""
    return f"• {s.label} {tag}".rstrip()


def _season_body(s: SeasonInfo) -> list[str]:
    """Ek season ki detail lines — Released / Hindi dub / English dub / Japanese audio."""
    lines: list[str] = []
    if s.planned is None and s.released is None:
        lines.append("Released: Unknown")
    elif s.planned is None:
        lines.append(f"Released: {s.released} episodes")
    elif s.released is None:
        lines.append(f"Released: Unknown/{s.planned} episodes")
    else:
        lines.append(f"Released: {s.released}/{s.planned} episodes")
    lines.append(f"Hindi dub: {_hindi_line(s.hi_count, s.hi_complete_month, s.hi_platforms, s.hi_status)}")
    lines.append(f"English dub: {_count_word(s.en_count)}")
    lines.append(f"Japanese audio: {_count_word(s.jp_count)}")
    return lines


def _top_season_line(data: CardData) -> str:
    """'Season 3: 12 episodes planned' — hamesha CURRENT season ka."""
    current = next((s for s in data.seasons if s.number == data.current_number), None)
    if current is None:
        return ""
    label = current.label
    if current.planned is None:
        return f"{label}: episodes TBD"
    if current.ongoing or current.not_yet_released:
        return f"{label}: {current.planned} episodes planned"
    return f"{label}: {current.planned} episodes total"


def _extra_line(e: ExtraInfo) -> list[str]:
    year = f" ({e.year})" if e.year else ""
    kind = {"MOVIE": "Movie", "SPECIAL": "Special", "OVA": "OVA", "MUSIC": "Music"}.get(e.format or "", "Extra")
    head = f"• {e.title}{year} — {kind}"
    if e.format == "MOVIE" and e.minutes:
        head += f", {e.minutes} min"
    sub = f"  Hindi dub: {e.hindi}"
    if e.hi_platforms:
        sub += f" ({', '.join(e.hi_platforms)})"
    return [head, sub]


def _movie_line(data: CardData) -> str:
    """'Movie — 121 min, released 11 Nov 2022'"""
    bits = []
    if data.movie_minutes:
        bits.append(f"{data.movie_minutes} min")
    if data.movie_date:
        bits.append(f"released {data.movie_date.strftime('%d %b %Y')}")
    kind = {"movie": "Movie", "special": "Special", "ova": "OVA"}.get(data.kind, "Movie")
    if not bits:
        return f"{kind} — details unknown"
    return f"{kind} — {', '.join(bits)}"


# ---------------------------------------------------------------------------
# CARD
# ---------------------------------------------------------------------------
def format_card(data: CardData) -> str:
    """Poora card text (plain text — Telegram me bina parse mode ke bhejte hain)."""
    lines: list[str] = [f"🎬 {data.title}"]
    if data.romaji and data.romaji.strip().lower() != data.title.strip().lower():
        lines.append(f"   ({data.romaji})")
    lines.append(f"📌 Status: {data.status_text}")
    lines.append("")

    if data.kind != "series":
        # ---------------- MOVIE / SPECIAL / OVA ----------------
        lines.append(f"🎬 {_movie_line(data)}")
        lines.append("")
        if data.streaming_platforms:
            lines.append("📺 Available platforms:")
            lines.extend(_platform_lines(data.streaming_platforms))
        else:
            lines.append("📺 Available platforms: Unknown")
        lines.append(f"Audio: {', '.join(data.audio_langs) if data.audio_langs else UNKNOWN}")
        lines.append(f"Subtitles: {', '.join(data.sub_langs) if data.sub_langs else UNKNOWN}")
        lines.append("")
        lines.append("🎞 Movie details:")
        hi = data.movie_hindi
        if data.hi_platforms:
            hi += f" ({', '.join(data.hi_platforms)})"
        lines.append(f"Hindi dub: {hi}")
        lines.append("Japanese audio: Available ✅")
        lines.append(f"English dub: {UNKNOWN}")
        if data.extras:
            lines.append("")
            lines.append("🎥 Related:")
            for e in data.extras[: config.MAX_EXTRA_LIST]:
                lines.extend(_extra_line(e))
    else:
        # ---------------- SERIES ----------------
        if data.streaming_platforms or data.hi_platforms:
            lines.append("📺 Available platforms:")
            combined = platforms.dedupe_preserve(list(data.hi_platforms) + list(data.streaming_platforms))
            lines.extend(_platform_lines(combined))
        else:
            lines.append("📺 Available platforms: Unknown")
        lines.append(f"Audio: {', '.join(data.audio_langs) if data.audio_langs else UNKNOWN}")
        lines.append(f"Subtitles: {', '.join(data.sub_langs) if data.sub_langs else UNKNOWN}")
        top = _top_season_line(data)
        if top:
            lines.append(top)
        lines.append("")

        if len(data.seasons) > 1:
            lines.append("🎞 Season details:")
            for s in data.seasons:
                lines.append(_season_head(s))
                lines.extend(_season_body(s))
        else:
            # single-season: 'Season details' header nahi, seedha lines
            s = data.seasons[0] if data.seasons else None
            if s is not None:
                lines.extend(_season_body(s))
            else:
                lines.append("Season details: Unknown")

        if data.extras:
            lines.append("")
            lines.append("🎥 Movies / Specials:")
            for e in data.extras[: config.MAX_EXTRA_LIST]:
                lines.extend(_extra_line(e))
            if len(data.extras) > config.MAX_EXTRA_LIST:
                lines.append(f"  ... +{len(data.extras) - config.MAX_EXTRA_LIST} more")

        # ---------------- Next episode ----------------
        lines.append("")
        lines.append("📅 Next episode:")
        lines.append(f"• Japanese audio: {data.next_jp_text}")
        lines.append(f"• English dub: {data.next_en_text}")
        lines.append(f"• Hindi dub: {data.next_hi_text}")

    # ---------------- footer ----------------
    lines.append("")
    lines.append("⏱ Last checked:")
    lines.append(config.ts_ist(data.last_checked))
    lines.append(texts.footer())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# NOTIFICATION
# ---------------------------------------------------------------------------
def format_notification(
    title: str,
    episode: int,
    lang_label: str,
    platform: str | None,
    count: int,
    total: int | None,
    when: datetime | None = None,
) -> str:
    """
    Exact notification format (HTML):

    🔔 <b>{title}</b> — Naya Episode!

    📌 Episode <b>{N}</b> (Hindi dub) aa chuka hai 🎉
    📺 Platform: {sirf relevant platform}
    📈 Hindi dub: {N}/{total} episodes
    ⏱ {IST timestamp}
    """
    progress = f"{count}/{total}" if total else str(count)
    lines = [
        f"🔔 <b>{html.escape(title)}</b> — Naya Episode!",
        "",
        f"📌 Episode <b>{episode}</b> ({lang_label}) aa chuka hai 🎉",
        f"📺 Platform: {html.escape(str(platform)) if platform else UNKNOWN}",
        f"📈 {lang_label}: {progress} episodes",
        f"⏱ {config.ts_ist(when or config.now_ist())}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pick list / myfollows
# ---------------------------------------------------------------------------
def format_pick_list(candidates: list) -> str:
    lines = [texts.PICK_LIST_HEADER, ""]
    for i, e in enumerate(candidates, start=1):
        year = f" ({e.year})" if e.year else ""
        fmt = e.format or "?"
        sub = e.romaji if e.romaji and e.romaji != e.best_title else ""
        extra = f" — {sub}" if sub else ""
        lines.append(f"{i}. {e.best_title}{year} — {fmt}{extra}")
    lines.append("")
    lines.append(texts.PICK_LIST_FOOTER)
    return "\n".join(lines)


def format_myfollows(follows: list[dict]) -> str:
    if not follows:
        return texts.MYFOLLOWS_EMPTY
    lines = [texts.MYFOLLOWS_HEADER.format(count=len(follows))]
    for f in follows:
        counts = []
        if f.get("hi_count") is not None:
            total = f.get("total_eps")
            counts.append(f"hi {f['hi_count']}/{total}" if total else f"hi {f['hi_count']}")
        if f.get("jp_count") is not None:
            counts.append(f"jp {f['jp_count']}")
        if f.get("en_count") is not None:
            counts.append(f"en {f['en_count']}")
        suffix = f" — {', '.join(counts)}" if counts else ""
        lines.append(
            texts.MYFOLLOWS_LINE.format(
                title=html.escape(f["title"]),
                langs=texts.lang_names(f["langs"]),
                counts=suffix,
            )
        )
    return "\n".join(lines)


def format_date(d: date | None) -> str:
    return config.date_ist_short(d) if d else UNKNOWN
