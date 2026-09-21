# 🎌 Anime Dub Bot — Hindi Dub Tracker

Telegram bot jo kisi bhi anime ki **Hindi / English / Japanese dub status**, available platforms, episode counts aur **next episode ki date** batata hai — aur follow karne par **naye episode ki notification** bhejta hai.

## 📋 Kya karta hai

- 🎬 Anime search — full info card (status, platforms, dub episodes, next episode date)
- 📺 Platforms: Crunchyroll, Netflix, Amazon Prime Video, Anime Times, JioHotstar, MX Player, ZEE5, Muse India, Ani-One, Crunchyroll Channel...
- ✅ **Follow** button → choose karo Japanese / English / Hindi audio
- 🔔 Naya episode aate hi notification: *"Black Torch — Episode 4 (Hindi dub) aa chuka hai! Platform: Crunchyroll (India)"*
- ❌ **Unfollow** → notification band

## 🤖 Bot commands

| Command | Kaam |
|---|---|
| `/start` | Bot start |
| `/search <naam>` | Anime dhundo |
| (plain text) | Koi bhi naam bhejo — search ho jayega |
| `/check <naam>` | Fresh data ke saath check |
| `/myfollows` | Follow list + unfollow buttons |
| `/setep <jp\|en\|hi> <count> <naam>` | Galat data khud theek karo |

## 🏗️ Data sources (hybrid)

1. **AniList API** (no key) — metadata, status, next episode, streaming links
2. **AniNidhi** (`pip install aninidhi`, no key) — **official Hindi dub tracker**: Crunchyroll/Netflix/Prime/Muse India — kab start hua + Airing/Finished status. Weekly math se episode count + next episode date nikalti hai (e.g. Black Torch: 29 Aug start → 19 Sep tak 4 episodes → next 26 Sep)
3. **YouTube RSS** (no key) — Muse India / Ani-One ke Hindi dub uploads live track
4. **anime-dub-info** (self-hosted, optional) — per-platform dub episode counts
5. **AnimeSchedule API** (free token, optional) — English dub air times + platforms
6. **`/setep` fixes + overrides.json** — manual corrections (sabse zyada priority)

> **Note:** Crunchyroll/Netflix/JioHotstar ka koi official episode API **nahi hota**. Isliye layered system hai: AniNidhi (daily auto-update) + YouTube live + `/setep`. Bot episode aate hi release history bhi yaad rakhta hai.

## 🚀 Setup (step-by-step)

### Step 1 — Telegram bot banao
1. Telegram pe **@BotFather** kholo
2. `/newbot` bhejo → naam rakho (e.g. `Anime Dub Tracker`)
3. Jo **token** mile use save karo — `BOT_TOKEN` ke liye chahiye

### Step 2 — GitHub par repo daalo
```bash
git init
git add .
git commit -m "Anime Dub Bot"
git branch -M main
git remote add origin https://github.com/<tumhara-username>/anime-dub-bot.git
git push -u origin main
```

### Step 3 — Render pe deploy karo
1. [render.com](https://render.com) par account banao (GitHub se login)
2. **New + → Web Service** → apna GitHub repo select karo
3. Settings:
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Instance Type:** Free
4. **Environment Variables** mein daalo:
   - `BOT_TOKEN` = BotFather wala token (**required**)
   - `ANIMESCHEDULE_TOKEN` = (optional) animeschedule.net → account → Settings → API → app banao → Bearer token
   - `DUB_INFO_API` = (optional) neeche dekho
   - `POLL_MINUTES` = `20` (default)
5. **Create Web Service** dabao — done! 🎉

> Repo mein `render.yaml` already hai, to Render blueprint se bhi seedha deploy ho jayega.

### Step 4 — Free tier ko awake rakho (zaroori!)
Render free tier 15 minute baad service **sleep** kar deta hai. UptimeRobot se fix karo:
1. [uptimerobot.com](https://uptimerobot.com) par free account banao
2. **New Monitor → HTTP(s)** → URL: `https://anime-dub-bot.onrender.com/healthz`
3. Interval: 5 minutes → Save

Ab bot 24×7 chalta rahega aur notifications time pe aayenge.

### Step 5 (optional but recommended) — Hindi dub data behtar karo
Hindi dub ke exact episode counts ke liye **anime-dub-info** khud deploy karo (2 minute ka kaam):
1. [github.com/sama511/anime-dub-info](https://github.com/sama511/anime-dub-info) repo kholo
2. "Deploy to Render" / fork karke Render par Web Service banao (Node.js)
3. Uska URL lo aur bot ki env var mein daalo:
   - `DUB_INFO_API` = `https://anime-dub-info-tumhara.onrender.com`

Iske baad Hindi dub episode counts seedha platform se aane lagenge.

## 💬 Output example

```
🎬 Black Torch
📌 Status: Completed ✅ (sab episodes release ho chuke)

📺 Available platforms:
• Crunchyroll — India
• Netflix
Audio: Japanese, English, Hindi
Subtitles: English, Hindi
Season 1: 12 episodes total

🎞 Season details:
• Season 1
Released: 12/12 episodes
Hindi dub: 4 episodes
English dub: 12 episodes
Japanese audio: 12 episodes

📅 Next episode:
• Japanese audio: All episodes released
• English dub: All episodes released
• Hindi dub: 26 Sep 2026 (estimated)

⏱ Last checked:
21 Sep 2026, 11:39 AM IST
```

Aur niche do button: **✅ Follow** / **❌ Unfollow** (+ 🔄 Refresh)

## 🔔 Notification example

```
🔔 Black Torch — Naya Episode!

📌 Episode 4 (Hindi dub) aa chuka hai 🎉
📺 Platform: Crunchyroll (India)
📈 Hindi dub: 4/12 episodes
⏱ 16 Sep 2026, 1:10 PM IST
```

## ⚙️ Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # BOT_TOKEN bharo
python main.py
```

## 📁 Project structure

```
anime-dub-bot/
├── main.py              # entrypoint: web service + bot + notifier
├── config.py            # env config
├── database.py          # SQLite (users, follows, cache)
├── handlers.py          # Telegram commands + buttons
├── notifier.py          # episode check loop + notifications
├── formatter.py         # info card + notification message
├── texts.py             # Hinglish strings
├── sources/
│   ├── aggregator.py    # sab sources merge karta hai
│   ├── anilist.py       # AniList GraphQL
│   ├── aninidhi_src.py  # AniNidhi — official Hindi dub tracker (primary HI source)
│   ├── anischedule.py   # AnimeSchedule API (token)
│   ├── dubinfo.py       # anime-dub-info (self-hosted)
│   ├── youtube.py       # YouTube RSS — Hindi dub live tracking
│   └── platforms.py      # platform naam normalization
├── render.yaml          # Render deployment
├── requirements.txt
└── .env.example
```

## ⚠️ Notes

- **Data accuracy:** Hindi dub ka primary source **AniNidhi** hai (daily auto-update, 488+ records — Crunchyroll/Netflix/Prime/Anime Times/Muse India/Ani-One sab covered). Season-aware title matching built-in hai ("Jujutsu Kaisen 2nd Season" -> "Jujutsu Kaisen (Season 2)" record se match hota hai). Popular test par 21/24 anime theek mile — jo miss hue unka official streaming Hindi dub hai hi nahi (jaise purane Hungama/TV dubs), bot unke liye "No official Hindi dub found" dikhata hai. Uske upar YouTube tracking + `/setep` (e.g. `/setep hi 4 Black Torch`) hai hi.
- **Not-found vs Unknown:** "No official Hindi dub found" = AniNidhi me record nahi (sach me dub available nahi hai streaming par). "Unknown" = sources se data hi nahi aaya (network issue etc.).
- **Next episode dates:** Japanese ka date AniList se exact aata hai. Hindi/English dub ka date last release + 7 din ka weekly estimate hai ("(estimated)" tag ke saath). Bot episode aate hi history yaad rakhta hai, isliye estimate time ke saath accurate hoti jati hai.
- **SQLite + Render free tier:** redeploy par `data/bot.db` reset ho sakta hai (follows delete ho jayenge). Permanent ke liye Render ka paid **Persistent Disk** lagao ya `DB_PATH` env var se disk path do.
- Follow karne ke waqt ke episode counts baseline ban jaate hain — purane episodes ki notification nahi aayegi, sirf naye wale ki.
- AniList API limit ~90 req/min hai; cache isko handle karta hai.
- Bot Hinglish mein baat karta hai — `texts.py` se badal sakte ho.

Made with ❤️ for Indian anime fans.
