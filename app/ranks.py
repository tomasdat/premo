import asyncio
import time
from collections import defaultdict

from .riot import RiotClient, RiotError

status = {"running": False, "message": "idle", "done": 0, "total": 0}

TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIVS = {"IV": 0, "III": 1, "II": 2, "I": 3}
APEX = 2800
RANKED_QUEUES = (420, 440)
QUEUE_TYPE = {420: "solo", 440: "flex"}


def score(tier: str | None, div: str | None, lp: int | None) -> int | None:
    """Single comparable number: 100 per division, 400 per tier; Master+ is 2800 + LP."""
    if tier not in TIERS:
        return None
    i = TIERS.index(tier)
    if i >= TIERS.index("MASTER"):
        return APEX + (lp or 0)
    return i * 400 + DIVS.get(div or "IV", 0) * 100 + (lp or 0)


def label(s: float | None) -> str | None:
    if s is None:
        return None
    s = int(round(s))
    if s >= APEX:
        return f"Master+ {s - APEX} LP"
    tier = TIERS[s // 400].capitalize()
    div = ["IV", "III", "II", "I"][min((s % 400) // 100, 3)]
    return f"{tier} {div}"


def init(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS ranks (
        puuid TEXT PRIMARY KEY, solo INTEGER, flex INTEGER, fetched_at INTEGER NOT NULL)""")
    conn.commit()


def _pending(conn) -> list[str]:
    """Players in ranked games, newest matches first, whose rank we haven't looked up yet."""
    rows = conn.execute(
        """SELECT p.puuid FROM participants p JOIN matches m ON m.id = p.match_id
           LEFT JOIN ranks r ON r.puuid = p.puuid
           WHERE r.puuid IS NULL AND m.queue_id IN (420, 440)
           GROUP BY p.puuid ORDER BY MAX(m.game_creation) DESC""").fetchall()
    return [r[0] for r in rows]


async def _fetch_one(conn, client: RiotClient, puuid: str) -> None:
    try:
        entries = await client.league_entries(puuid)
    except RiotError as e:
        if e.status in (401, 403):
            raise
        if e.status != 404:
            return
        entries = []
    solo = flex = None
    for e in entries:
        s = score(e.get("tier"), e.get("rank"), e.get("leaguePoints"))
        if e.get("queueType") == "RANKED_SOLO_5x5":
            solo = s
        elif e.get("queueType") == "RANKED_FLEX_SR":
            flex = s
    conn.execute("INSERT OR REPLACE INTO ranks(puuid, solo, flex, fetched_at) VALUES (?,?,?,?)",
                 (puuid, solo, flex, int(time.time())))
    conn.commit()


async def enrich(conn, client: RiotClient, refresh_tracked: bool = True) -> None:
    """Looks up ranks (one request each) for everyone seen in ranked matches, newest first."""
    if status["running"]:
        return
    init(conn)
    status.update(running=True, done=0, message="Looking up ranks")
    try:
        if refresh_tracked:
            conn.execute("DELETE FROM ranks WHERE puuid IN (SELECT puuid FROM accounts)")
            conn.commit()
        todo = _pending(conn)
        status["total"] = len(todo)
        for i in range(0, len(todo), 10):
            await asyncio.gather(*(_fetch_one(conn, client, p) for p in todo[i:i + 10]))
            status["done"] = min(i + 10, len(todo))
            status["message"] = f"Looking up ranks: {status['done']}/{len(todo)}"
        status["message"] = f"Ranks up to date ({len(todo)} looked up)"
    except RiotError as e:
        status["message"] = f"Rank lookup stopped: {e}"
    except Exception as e:
        status["message"] = f"Rank lookup failed: {e!r}"
    finally:
        status["running"] = False


def pick(row, queue_id: int) -> int | None:
    """Rank score for a match: the higher of Solo/Duo and Flex."""
    if row is None:
        return None
    known = [s for s in (row["solo"], row["flex"]) if s is not None]
    return max(known) if known else None


def match_ranks(conn, match_ids: list[str]) -> dict:
    """match_id -> {puuid: score or None}, restricted to ranked queues."""
    out: dict = defaultdict(dict)
    for i in range(0, len(match_ids), 400):
        chunk = match_ids[i:i + 400]
        for r in conn.execute(
                f"""SELECT p.match_id, p.puuid, p.team_id, m.queue_id, r.solo, r.flex
                    FROM participants p JOIN matches m ON m.id = p.match_id
                    LEFT JOIN ranks r ON r.puuid = p.puuid
                    WHERE p.match_id IN ({','.join('?' * len(chunk))}) AND m.queue_id IN (420, 440)""", chunk):
            out[r["match_id"]][r["puuid"]] = (r["team_id"], pick(r, r["queue_id"]) if r["solo"] is not None
                                              or r["flex"] is not None else None)
    return out


def _avg(vals: list[int | None], need: int = 3) -> float | None:
    known = [v for v in vals if v is not None]
    return sum(known) / len(known) if len(known) >= need else None


def difficulty(ranks: dict, team_id: int) -> dict | None:
    """Own team vs opponents average rank for one match; None when too few ranks are known."""
    if not ranks:
        return None
    own = _avg([s for t, s in ranks.values() if t == team_id])
    opp = _avg([s for t, s in ranks.values() if t != team_id])
    if own is None or opp is None:
        return None
    own_top = max((s for t, s in ranks.values() if t == team_id and s is not None))
    opp_top = max((s for t, s in ranks.values() if t != team_id and s is not None))
    return {"own": round(own), "opp": round(opp), "own_label": label(own), "opp_label": label(opp),
            "own_top": label(own_top), "opp_top": label(opp_top),
            "diff": round((opp - own) / 100, 1)}
