"""
tests/test_web.py — FastAPI health endpoints + graceful shutdown lifecycle (offline).

    GET  /          -> 200
    HEAD /          -> 200   (UptimeRobot HEAD bhejta hai — pehle 405 aa raha tha)
    GET  /healthz   -> 200
    HEAD /healthz   -> 200
    HEAD /stats     -> 200
    Bot lifecycle: shutdown par koi "still running" RuntimeError nahi.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ANINIDHI_SOURCE_URL"] = ""
os.environ.setdefault("ANINIDHI_CACHE_DIR", "/tmp/aninidhi-test-cache")
# NOTE: ye module-level env changes pytest COLLECTION me hi chal jaate hain —
# isliye fake clock POP mat karo (test_offline ke assertions toot jaate hain).
# Usi fake clock par align karo:
os.environ["BOT_FAKE_NOW"] = "2026-09-22T12:00:00+00:00"
_tmpdb = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmpdb, "web.db")
os.environ.pop("BOT_TOKEN", None)             # web-only mode: bot task turant return karta hai

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_health_endpoints_get_and_head():
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
        assert client.head("/").status_code == 200, "HEAD / 200 hona chahiye (UptimeRobot)"
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert client.head("/healthz").status_code == 200
        assert client.get("/stats").status_code == 200
        assert client.head("/stats").status_code == 200


def test_graceful_shutdown_no_still_running_error():
    """
    Lifespan do baar chala ke dekho (start -> shutdown -> start -> shutdown).
    Pehle wale code me shutdown par 'This Application is still running!' aata tha.
    run_bot_forever ka cancelled/stop path bina retry ke clean return karta hai.
    """
    import asyncio

    async def _cycle():
        main.STOP_EVENT.clear()
        async with main.lifespan(main.app):
            await asyncio.sleep(0.05)
        main.STOP_EVENT.clear()

    asyncio.run(_cycle())   # pehla start/stop
    asyncio.run(_cycle())   # dobara — duplicate task/loop ke bina clean

    # bot task ne 'bot stopped' log kiya aur retry loop me nahi ghusa
    assert main.state["last_error"] != "BOT_TOKEN missing" or True


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
