import asyncio
import time
from collections import deque
from urllib.parse import quote

import httpx

from . import config


class RiotError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Riot API {status}: {message}")
        self.status = status


class RateLimiter:
    """Dev-key limits: 20 req/1s and 100 req/2min (slightly under for safety)."""

    def __init__(self, limits=((18, 1.0), (95, 120.0))):
        self.limits = limits
        self.hits = {w: deque() for _, w in limits}
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                delay = 0.0
                for max_n, window in self.limits:
                    q = self.hits[window]
                    while q and now - q[0] >= window:
                        q.popleft()
                    if len(q) >= max_n:
                        delay = max(delay, window - (now - q[0]))
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
            now = time.monotonic()
            for _, window in self.limits:
                self.hits[window].append(now)


LIMITER = RateLimiter()


class RiotClient:
    def __init__(self, api_key: str | None = None, region: str | None = None):
        self.region = region or config.REGION
        self.http = httpx.AsyncClient(
            headers={"X-Riot-Token": api_key or config.RIOT_API_KEY}, timeout=30
        )
        self.limiter = LIMITER

    async def close(self):
        await self.http.aclose()

    async def _get(self, path: str, params: dict | None = None, host: str | None = None):
        url = f"https://{host or self.region}.api.riotgames.com{path}"
        for _ in range(5):
            await self.limiter.wait()
            r = await self.http.get(url, params=params)
            if r.status_code == 429:
                await asyncio.sleep(float(r.headers.get("Retry-After", "5")) + 0.5)
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2)
                continue
            if r.status_code != 200:
                raise RiotError(r.status_code, r.text[:200])
            return r.json()
        raise RiotError(429, "rate limited repeatedly")

    async def account_by_riot_id(self, game_name: str, tag_line: str) -> dict:
        return await self._get(
            f"/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        )

    async def match_ids(self, puuid: str, queue: int, start: int = 0, count: int = 100) -> list[str]:
        return await self._get(
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            {"queue": queue, "start": start, "count": count},
        )

    async def match(self, match_id: str) -> dict:
        return await self._get(f"/lol/match/v5/matches/{match_id}")

    async def league_entries(self, puuid: str) -> list[dict]:
        return await self._get(f"/lol/league/v4/entries/by-puuid/{puuid}", host=config.PLATFORM.lower())
