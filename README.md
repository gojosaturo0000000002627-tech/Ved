# 🎬 Anime Info Telegram Bot

Ved jaisa anime-info agent, Telegram pe. Kya karta hai:

- **/anime <name>** ya bas naam likho → poora card: status, platforms, **har season
  ka breakdown** (Japanese / Hindi / Tamil / Telugu dub counts), next episode ka time IST me
- **Follow button sirf ongoing anime pe dikhta hai** — finished anime pe nahi
- Follow karne par **language buttons** (multi-select: Japanese sub, Hindi dub, Tamil, Telugu)
- Followed anime ka naya episode aate hi **turant DM notification** (har 15 min check)
- Season complete hone par ek "🏁 season complete" message, uske baad koi spam nahi
- Watchlist **GitHub repo me save** hota hai (Render restart hone pe bhi safe)

Data sources: rareanimes.mov (Hindi/Tamil/Telugu dub catalog — episode counting ke liye)
+ Jikan/MyAnimeList (Japanese episodes, status, broadcast timing). Dono free, koi API key nahi chahiye.

## Files

| File | Kaam |
|---|---|
| `bot.py` | Telegram bot — commands, follow buttons, language picker, notification poller |
| `scraper.py` | Catalog + MAL scraping, multi-season card builder |
| `storage.py` | Watchlist — GitHub repo (primary) + local file (fallback) |
| `requirements.txt` | Python dependencies |
| `render.yaml` | Render deploy config |

---

## Setup — 5 steps

### Step 1: Telegram bot banao (2 min)

1. Telegram me **@BotFather** kholo → `/newbot`
2. Naam do (jaise `Anime Info Bot`) aur username (jaise `animeinfo_shinchan_bot`)
3. BotFather ek **token** dega — copy kar lo (format: `123456:ABC-xyz...`)

### Step 2: GitHub pe code daalo (3 min)

1. GitHub pe naya repo banao (public ya private, dono chalega) — jaise `anime-info-bot`
2. Ye saari files us repo me upload kar do (upload par `watchlist_local.json` mat daalna)

### Step 3: Data repo + token (3 min) — optional par recommended

Watchlist ke liye alag repo (ya same repo ki branch) chahiye:

1. Naya repo banao — jaise `anime-bot-data` (me `watchlist.json` khud ban jayegi)
2. GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → Generate
3. Token ko sirf `anime-bot-data` repo ka **Contents: Read and write** permission do
4. Token copy kar lo

### Step 4: Render pe deploy (5 min)

1. [render.com](https://render.com) pe account banao (GitHub se login)
2. **New + → Web Service** → apna `anime-info-bot` repo connect karo
3. Render `render.yaml` khud padh lega. Environment variables me daalo:

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | Step 1 ka token |
   | `GITHUB_TOKEN` | Step 3 ka token (optional) |
   | `GITHUB_REPO` | `tumhara-username/anime-bot-data` (optional) |
   | `CHECK_INTERVAL` | `900` (15 min; chaho to `600` = 10 min) |

4. **Create Web Service** — 2-3 min me deploy ho jayega

### Step 5: Bot ko jagaye rakho (2 min) — free tier ke liye

Render ka free tier 15 min idle hone par service sula deta hai. Solve:

1. [UptimeRobot](https://uptimerobot.com) pe free account banao
2. **New Monitor → HTTP(s)** → URL: `https://anime-info-bot.onrender.com/` (apna Render URL)
3. Interval: **5 minutes**

Ab bot 24×7 chalta rahega. Done! 🎉

---

## Bot ke commands

| Command | Kaam |
|---|---|
| anime ka naam likho | Full card (ongoing pe Follow button ke saath) |
| `/anime <name>` | Same, command form me |
| `/follow <name>` | Ongoing anime follow + language picker |
| `/list` | Followed anime + on ki hui languages |
| `/settings <name>` | Languages dobara chuno |
| `/unfollow <name>` | Follow band |

## Notification example

```
🔔 NEW EPISODE OUT!
🎬 Grand Blue Dreaming Season 3
Episode 4
Language: 🇮🇳 Hindi dub
Platform: Anime Times
Released: 21 Sep 2026, 07:41 PM IST
```

## Notes

- Notification episode drop ke **15 min ke andar** aata hai (CHECK_INTERVAL). Exact second
  pe nahi — bot har 15 min me catalog check karta hai.
- Follow karne se **pehle** nikle episodes ka notification nahi aata — sirf naye wale.
- `GITHUB_TOKEN` set nahi karoge to watchlist sirf local file me rahegi — Render
  redeploy/restart pe follow list hat sakti hai (bot data repo use karo to safe hai).
- rareanimes catalog sirf Hindi/Tamil/Telugu dubs ke liye hai. Japanese info MAL se
  aati hai. English dub ka koi reliable free source nahi — isliye wo option har anime
  pe available nahi hoga.

## Local test (optional)

```bash
pip install -r requirements.txt
export BOT_TOKEN="tumhara-token"
python bot.py
```
