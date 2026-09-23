"""
texts.py — bot ke saare user-facing messages (Hinglish).

Rule: har message Hinglish me — Indian audience ke liye.
Code comments bhi Hinglish me.
"""
from __future__ import annotations

import config

# ---------------------------------------------------------------- greetings
START = """Namaste {name}! 🙏 Main <b>Anime Dub Bot</b> hoon.

Main batata hoon ki kaun sa anime <b>Hindi dub</b> me available hai, kis platform par, aur naya episode kab aa raha hai.

<b>Shuru karne ke liye:</b>
/search &lt;anime ka naam&gt;   — jaise <code>/search grand blue</code>
/myfollows                 — aapke followed anime
/version                   — bot version check

Tip: card par ✅ Follow daba do, naya episode aate hi main message kar dunga 🔔"""

HELP = """<b>Kaise use karein:</b>

/search &lt;naam&gt; — anime dhundho aur uska Hindi dub status dekho
/anime &lt;naam&gt; — /search ka hi doosra naam
/myfollows — follow kiye hue anime + unki language settings
/setep jp|en|hi &lt;count&gt; &lt;naam&gt; — galat episode count manually theek karo
/version — live version confirm karo
/help — ye message

<b>Data kahan se aata hai:</b>
• AniList — episodes, airing date, seasons (real-time)
• AniNidhi — Hindi dub ka asli database (platform + date)
• YouTube — official channels par upload hue episodes

Agar data confirm nahi hai to main saaf keh deta hoon: <i>Unknown</i> ya
<i>No official Hindi dub found</i>. Guess kabhi nahi karta. 🤝"""

# ------------------------------------------------------------------ search
SEARCHING = "Dhund raha hoon... 🔍"
SEARCHING_DETAIL = "Dhund raha hoon... 🔍 (AniList + Hindi dub database)"
REFRESHING = "Refresh kar raha hoon... 🔄"
NO_QUERY = "Naam bhi to batao 🙂 — jaise <code>/search demon slayer</code>"

PICK_LIST_HEADER = "Ye mile — ek chun lo:"
PICK_LIST_FOOTER = "Aur specific naam likhoge to seedha card mil jaayega 👍"

NOT_FOUND = """Hmm, <b>{query}</b> nahi mila 😕

Naam thoda check kar lo — English ya Romaji dono chalta hai.
Jaise: <code>/search mushoku tensei</code>, <code>/search dan da dan</code>

<small>AniList par is naam se koi entry nahi hai.</small>"""

NOT_FOUND_WITH_SUGGESTIONS = """Hmm, <b>{query}</b> nahi mila 😕

Shayad aap ye dhundh rahe ho:
{suggestions}

Seedha naam likh kar dobara try karo 👍"""

TOO_SLOW = "Data sources thoda slow chal rahe hain 🐢 — 10-15 second baad dobara try karo."
BUILD_ERROR = "Card banate waqt kuch gadbad ho gayi 😓 Thodi der baad dobara try karo."
ANILIST_DOWN = "AniList abhi jawab nahi de raha 📡 — thodi der baad try karo."
ANILIST_RATELIMIT = (
    "AniList ne abhi requests limit kar di hain ⏳ (Render par IP shared hota hai, "
    "isliye kabhi-kabhi hota hai). Card cache se baaki data aa jaata hai — "
    "~1 minute baad /search ya 🔄 Refresh dobara try karo."
)

# ------------------------------------------------------------------ follow
FOLLOW_PICK_LANG = """<b>{title}</b> ke liye kaunsi language track karni hai? 🎧

Ek se zyada chun sakte ho — button daba kar toggle karo, phir ✅ Confirm karo."""

FOLLOW_DONE = """✅ Follow ho gaya — <b>{title}</b>

Track ho raha hai: {langs}
Naya episode aate hi yahan message aa jaayega 🔔"""

UNFOLLOW_DONE = "❌ Unfollow kar diya — <b>{title}</b>. Ab notification nahi aayega."
NOT_FOLLOWED = "Aapne <b>{title}</b> ko follow nahi kiya hua 🙂"
ALREADY_FOLLOWED = "Aap pehle se <b>{title}</b> follow kar rahe ho ✅"
NO_LANG_SELECTED = "Kam se kam ek language chun lo 🎧 (Japanese / English / Hindi)"
LANG_SAVED = "Language settings save ho gayi ✅ — <b>{title}</b>: {langs}"

MYFOLLOWS_EMPTY = """Abhi koi anime follow nahi kiya hua 🙂

/search &lt;naam&gt; se card kholo aur ✅ Follow daba do."""

MYFOLLOWS_HEADER = "<b>Aapke followed anime</b> ({count}):\n"
MYFOLLOWS_LINE = "• <b>{title}</b> — {langs}{counts}"

# ------------------------------------------------------------------ setep
SETEP_USAGE = """<b>/setep</b> se episode count manually theek kar sakte ho:

<code>/setep hi 5 black torch</code>
<code>/setep jp 13 mushoku tensei</code>
<code>/setep en 12 dan da dan</code>

jp = Japanese audio, en = English dub, hi = Hindi dub
Ye override sabse high priority rakhta hai."""

SETEP_BAD_LANG = "Language samajh nahi aaya — <code>jp</code>, <code>en</code> ya <code>hi</code> likho."
SETEP_BAD_COUNT = "Count ek number hona chahiye (0 ya usse zyada)."
SETEP_OK = "✅ Override save: <b>{title}</b> — {lang_label}: {count} episodes"
SETEP_CLEARED = "🧹 Override hata diya: <b>{title}</b> — {lang_label} (ab real data chalega)"

# ---------------------------------------------------------------- version
VERSION_MSG = """🤖 <b>Anime Dub Bot</b> — version <code>{version}</code> ({version_long})

Polling: har {poll_minutes} minute
Abhi time: {ist_now}
Card ke footer me bhi yahi version tag dikhta hai — dono match karein to latest code live hai ✅"""

# ---------------------------------------------------------------- errors
ERROR_GENERIC = "Kuch gadbad ho gayi 😓 Log me record kar liya hai — thodi der baad try karo."
ADMIN_ONLY = "Ye command sirf admins ke liye hai 🔒"

# ---------------------------------------------------------------- notifier
NOTIFY_FAIL = "Notification bhejne me dikkat aayi (user {user_id}): {error}"

# ---------------------------------------------------------------- buttons
BTN_FOLLOW = "✅ Follow"
BTN_UNFOLLOW = "❌ Unfollow"
BTN_REFRESH = "🔄 Refresh"
BTN_WATCH = "▶️ Watch on {platform}"
BTN_ANILIST = "🔗 AniList"
BTN_CONFIRM = "✅ Confirm"
BTN_CANCEL = "✖️ Cancel"
BTN_BACK = "⬅️ Back"
BTN_LANG = {"jp": "🇯🇵 Japanese audio", "en": "🇺🇸 English dub", "hi": "🇮🇳 Hindi dub"}


def lang_names(langs: list[str]) -> str:
    """['jp','hi'] -> 'Japanese audio, Hindi dub'"""
    from database import LANG_LABEL

    return ", ".join(LANG_LABEL.get(l, l) for l in langs) or "—"


def footer() -> str:
    return f"🤖 {config.VERSION}"
