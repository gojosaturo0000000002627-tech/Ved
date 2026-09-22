# 🎬 Anime Dub Bot

Telegram bot jo batata hai — kaun se anime ka **official Hindi dub** kahan hai,
kitne episode aa chuke hain, aur naya episode kab aayega. Follow karke naye
episodes ke notifications bhi milte hain.

> **Sabse bada promise: data REAL hai.** Agar koi cheez confirm nahi hai to bot
> saaf kehta hai — `Unknown` ya `No official Hindi dub found`. Kabhi guess nahi karta.

---

## Features

- **Smart search** — direct card, franchise pick (`Grand Blue Dreaming` = poora
  franchise), exact/best match, typo tolerance (`mushoko tensai` → Mushoku Tensei),
  aur garbage-guard (1-word fallback strictly forbidden)
- **Hindi dub status** — [AniNidhi](https://pypi.org/project/aninidhi/) (488+ real
  records) + YouTube official channels (Muse India, Ani-One) se cross-check
- **Seasons** — PREQUEL/SEQUEL chain (batched BFS), cours merging (Mushoku Tensei
  ke 5 AniList entries → 3 seasons), **current-season rule** (S1 search karo, S3 ka
  data dikhe)
- **Follow + notifications** — JP/EN/HI multi-select, har 20 min poll, naya episode
  = exact-format notification + ▶️ Watch button
- **Speed** — card 1–3s me: AniList entry cache 15 min, card cache 30 min (SQLite),
  AniNidhi 6h cache, YouTube handle permanent cache + failures 1h negative cache
- **Overrides** — `/setep jp|en|hi <count> <name>` + `overrides.json` (highest priority)
- **Health endpoints** — `/`, `/healthz`, `/stats` (Render free web service)

## Project structure

```
main.py            — FastAPI app + PTB bot (auto-retry loop) + notifier
config.py          — env vars + VERSION (card footer + /version)
database.py        — SQLite: users/follows/cache/lang_state/manual_fix
texts.py           — saare Hinglish messages
formatter.py       — card + notification formats (exact)
handlers.py        — commands + callbacks
notifier.py        — notification engine
sources/
  aggregator.py    — merge + search logic + season logic (bot ka dimaag)
  anilist.py       — GraphQL (search/get/many + 15-min cache)
  aninidhi_src.py  — Hindi dub (variants, weekly math, list_all cache)
  youtube.py       — RSS scanner + handle resolve + negative cache
  anischedule.py   — optional (token chahiye)
  dubinfo.py       — optional self-hosted API
  platforms.py     — platform name canon + India region map
tests/
  test_offline.py          — 33 tests: AniList/dubinfo/YouTube mocked, AniNidhi REAL
  test_handlers_offline.py — 14 tests: asli PTB objects, sirf network fake
  anilist_fixtures.json    — real captured AniList data
overrides.json     — manual corrections (highest priority)
render.yaml        — Render blueprint
```

## Local run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export BOT_TOKEN="123456:ABC..."        # @BotFather se
python main.py                          # web service + bot dono ek process me
```

Sirf tests:

```bash
python -m pytest tests/ -v              # 47 tests, fully offline (network ke bina)
```

## Deploy on Render (free web service)

### 1. GitHub push

```bash
git init && git add . && git commit -m "Anime Dub Bot v1"
git remote add origin https://github.com/<you>/anime-dub-bot.git
git push -u origin main
```

### 2. Render par service banao

- Dashboard → **New +** → **Blueprint** → repo select karo (`render.yaml` auto-import ho jaata hai)
- Ya manually: **New + → Web Service** with:
  - **Runtime:** Python 3
  - **Build Command:** `pip install -r requirements.txt`
  - **Start Command:** `python main.py`
  - **Health Check Path:** `/healthz`
- **Env vars (zaroori):**
  - `BOT_TOKEN` — Telegram token (secret)
  - `PYTHON_VERSION` = **`3.12.4`** ← ye **zaroori** hai. Naya Python
    (3.13+) python-telegram-bot v21 ke saath toot-ta hai.
  - `POLL_MINUTES` = `20` (notification interval)

### 3. Service ko awake rakho (warna notifications ruk jaayenge)

Render free tier 15 min idle ke baad service sleep kar deta hai. Isliye
[UptimeRobot](https://uptimerobot.com) ya cron-job.org se:

- Monitor type: HTTP(s)
- URL: `https://<your-service>.onrender.com/healthz`
- Interval: **har 5 minute**

### 4. Verify karo

1. Bot ko Telegram par `/start` bhejo
2. `/version` — live version confirm (`v1`)
3. `/search grand blue` — card ke footer me bhi `🤖 v1` dikhega
4. Card par **✅ Follow** → language chuno → **✅ Confirm**
5. `/stats` (admin) ya `https://<service>.onrender.com/stats` — follows/users/poll

> **Telegram "Conflict" error** = do instance ek hi token par chal rahe hain
> (jaise Render + local). Purana band karo — bot khud retry karta rahega.

## Commands

| Command | Kaam |
|---|---|
| `/search <name>` | Anime ka Hindi dub card (plain naam likhne par bhi search hota hai) |
| `/myfollows` | Followed anime + language settings |
| `/setep jp\|en\|hi <count> <name>` | Manual override (highest priority), `clear` se hatao |
| `/version` | Live version confirm |
| `/stats` | Bot stats (sirf admins) |

## Data sources

| Source | Kya milta hai | Key? |
|---|---|---|
| [AniList GraphQL](https://docs.anilist.co) | metadata, episodes, airing time, seasons, streaming links | nahi (90 req/min) |
| [AniNidhi](https://pypi.org/project/aninidhi/) | **Hindi dub** platform + start date + status | nahi |
| YouTube RSS | official channels par hue Hindi dub episodes | nahi |
| anime-dub-info (self-host) | optional extra dub data | nahi |
| AnimeSchedule API | optional airing corroboration | token |

AniList ko **User-Agent header chahiye** (bina uske 403) — code me handled hai.

## Edge cases (day-1 se handled)

- Cours/Parts alag AniList entries → merged (Mushoku = 3 seasons)
- Purani season entry search karne par bhi **current season** ka data/status/counts
- JP complete par Hindi dub airing → dub ke dates dikhna jaari rahte hain
- Estimate ki date nikal chuki → "To be announced"
- Bleach / DBZ / Shin-chan → "No official Hindi dub found" (yahi SAHI jawab hai)
- `CallbackQuery` me `effective_user` nahi hota → `query.from_user` use hota hai
- Ek slow source pura card block na kare → har optional source par timeout

## License

MIT
