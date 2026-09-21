"""Hinglish messages."""
START = (
    "👋 Namaste! Main *Anime Dub Bot* hoon 🎌\n\n"
    "Main tumhe bata sakta hoon:\n"
    "• Koi bhi anime ki *Hindi / English / Japanese* dub status\n"
    "• Kaunsi platform pe available hai (Crunchyroll, Netflix, JioHotstar, Prime Video, ZEE5, MX Player, Muse India, Ani-One...)\n"
    "• Kitne episodes aa chuke hain aur *next episode kab aayega*\n\n"
    "*Commands:*\n"
    "/search <anime ka naam> — anime ki full info\n"
    "/myfollows — tumhare followed anime\n"
    "/help — madad\n\n"
    "👉 Bas kisi bhi anime ka naam type kar do, main dhoond dunga!"
)

HELP = (
    "*📖 Kaise use kare:*\n\n"
    "1️⃣ Anime ka naam bhejo — main full card bhejunga:\n"
    "   status, platforms, dub info, episode count, next episode date\n\n"
    "2️⃣ Card ke niche *Follow* button dabao — phir choose karo\n"
    "   kis audio ke notification chahiye (Japanese / English / Hindi)\n\n"
    "3️⃣ Jab bhi us anime ka naya episode aayega (jo tumne follow kiya),\n"
    "   main tumhe turant notification bhejunga 📩\n\n"
    "4️⃣ *Unfollow* dabao — notification band.\n\n"
    "*Commands:*\n"
    "/search <naam> — anime dhundo\n"
    "/myfollows — follow list + unfollow buttons\n"
    "/check <naam> — fresh data ke saath check\n"
    "/help — ye message"
)

NOT_FOLLOWING = "Tumne abhi koi anime follow nahi kiya hai. 🙃 Kisi anime ka naam bhejo aur Follow dabao!"

FOLLOW_ALREADY = "Tum ye anime pehle se follow kar chuke ho ✅"

FOLLOW_DONE = (
    "✅ *{title}* follow ho gaya!\n"
    "Notifications ON: {langs}\n\n"
    "Jab bhi naya episode aayega, main tumhe sabse pehle bata dunga 📩"
)

UNFOLLOW_DONE = (
    "❌ *{title}* unfollow ho gaya.\n"
    "Ab is anime ki notifications band ho gayi hain."
)

NOT_FOLLOWING_THIS = "Ye tumhare follow list mein nahi hai 🤔"

LANG_LABELS = {
    "jp": "🇯🇵 Japanese audio",
    "en": "🇺🇸 English dub",
    "hi": "🇮🇳 Hindi dub",
}

SEARCHING = "🔍 Dhund raha hoon..."
NOT_FOUND = "😔 Kuch nahi mila. Naam check karke dobara try karo (English naam better kaam karta hai)."

# Notification message
NOTIFY_TEMPLATE = (
    "🔔 *{title}* — Naya Episode!\n\n"
    "📌 Episode {ep} ({lang}) aa chuka hai 🎉\n"
    "📺 Platform: {platforms}\n"
    "📈 {lang} progress: {done}/{total} episodes\n"
    "⏱ {checked}\n\n"
    "_Source se confirm kar lena — kabhi kabhi delay ho sakta hai._"
)

ERROR_MSG = "😵 Thodi si technical problem aayi. Thodi der baad dobara try karo."

# /setep — manual data correction
SETEP_USAGE = (
    "Data galat aa raha hai? Khud theek karo 👇\n\n"
    "/setep <jp|en|hi> <episode count> <anime ka naam>\n\n"
    "Example: /setep hi 4 Black Torch\n"
    "(hi = Hindi dub, en = English dub, jp = Japanese audio)"
)
SETEP_CONFIRM = (
    "✅ Ho gaya! *{title}* ka {lang_label} ab *{count} episodes* dikhega.\n"
    "(source data se upar ye fix kiya gaya hai — /check {title} se dekh lo)"
)
SETEP_NOT_FOUND = "😔 Wo anime nahi mila. Naam check karke dobara try karo."
