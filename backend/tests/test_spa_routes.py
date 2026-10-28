"""Real static SPA routing, no database/model lifespan side effects."""
import asyncio
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app


def test_admin_deep_link_and_login_refresh_use_spa_but_missing_api_stays_404():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/admin", "/login"):
                response = await client.get(path)
                assert response.status_code == 200 and '<div id="app">' in response.text
            assert (await client.get("/api/does-not-exist")).status_code == 404
    asyncio.run(run())
