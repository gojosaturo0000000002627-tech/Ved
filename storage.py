"""Watchlist storage — GitHub repo as the database, local file as fallback."""

import base64
import json
import os

import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()   # e.g. "shinchan/anime-bot-data"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
DATA_PATH = os.getenv("DATA_PATH", "watchlist.json")

LOCAL_FILE = "watchlist_local.json"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_PATH}"
RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{DATA_PATH}"

EMPTY = {"users": {}}


def _http_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def load():
    """Load watchlist: try GitHub raw first, then local file, else empty."""
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            r = requests.get(RAW_URL, params={"t": os.urandom(4).hex()},
                             timeout=20)
            if r.status_code == 200:
                return json.loads(r.text)
        except Exception:
            pass
    if os.path.exists(LOCAL_FILE):
        try:
            with open(LOCAL_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return json.loads(json.dumps(EMPTY))


def save(data):
    """Save to local file always; commit to GitHub if configured."""
    with open(LOCAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if not (GITHUB_TOKEN and GITHUB_REPO):
        return False
    try:
        content = base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii")

        # current file sha (needed to overwrite); 404 = new file
        sha = None
        r = requests.get(API_URL, params={"ref": GITHUB_BRANCH},
                         headers=_http_headers(), timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")

        payload = {
            "message": "watchlist update",
            "content": content,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(API_URL, headers=_http_headers(), json=payload, timeout=25)
        return r.status_code in (200, 201)
    except Exception:
        return False
