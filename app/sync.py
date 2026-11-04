import asyncio
import json
import sqlite3

from . import config, ranks
from .riot import RiotClient, RiotError

status = {"running": False, "message": "idle", "new_matches": 0, "errors": []}


def store_match(conn: sqlite3.Connection, m: dict) -> None:
    info = m["info"]
    conn.execute(
        "INSERT OR IGNORE INTO matches(id, queue_id, game_creation, duration, version) VALUES (?,?,?,?,?)",
        (m["metadata"]["matchId"], info["queueId"], info["gameCreation"],
         info["gameDuration"], info.get("gameVersion")),
    )
    for p in info["participants"]:
        name = p.get("riotIdGameName") or p.get("summonerName") or ""
        tag = p.get("riotIdTagline") or ""
        styles = (p.get("perks") or {}).get("styles") or [{}, {}]
        keystone = ((styles[0].get("selections") or [{}])[0]).get("perk")
        sub_style = styles[1].get("style") if len(styles) > 1 else None
        items = json.dumps([p.get(f"item{i}", 0) for i in range(7)])
        conn.execute(
            """INSERT OR REPLACE INTO participants(match_id, puuid, team_id, champion, position,
                 kills, deaths, assists, cs, damage, gold, vision, win, riot_name,
                 items, keystone, sub_style, spell1, spell2, level)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m["metadata"]["matchId"], p["puuid"], p["teamId"], p["championName"],
             p.get("teamPosition") or p.get("individualPosition"),
             p["kills"], p["deaths"], p["assists"],
             p["totalMinionsKilled"] + p.get("neutralMinionsKilled", 0),
             p["totalDamageDealtToChampions"], p["goldEarned"], p.get("visionScore", 0),
             1 if p["win"] else 0, f"{name}#{tag}" if tag else name,
             items, keystone, sub_style, p.get("summoner1Id"), p.get("summoner2Id"),
             p.get("champLevel")),
        )
    conn.commit()


async def sync_puuid(conn, client: RiotClient, puuid: str, queues: list[int]) -> int:
    """Pages backwards through the full match history of each queue; stops once a page is all known."""
    added = 0
    for queue in queues:
        start = 0
        while True:
            ids = await client.match_ids(puuid, queue, start=start, count=100)
            if not ids:
                break
            known = {r[0] for r in conn.execute(
                f"SELECT id FROM matches WHERE id IN ({','.join('?' * len(ids))})", ids)}
            for mid in ids:
                if mid in known:
                    continue
                try:
                    store_match(conn, await client.match(mid))
                    added += 1
                    status["new_matches"] += 1
                    status["message"] = f"Downloaded {status['new_matches']} new matches"
                except RiotError as e:
                    if e.status != 404:
                        status["errors"].append(str(e))
            if len(known) == len(ids) or len(ids) < 100:
                break
            start += 100
    return added


async def backfill(conn, client: RiotClient) -> None:
    """Re-download matches stored before items/runes were tracked."""
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT match_id FROM participants WHERE items IS NULL ORDER BY match_id DESC")]
    for i, mid in enumerate(ids, 1):
        status["message"] = f"Adding build details: {i}/{len(ids)}"
        try:
            store_match(conn, await client.match(mid))
        except RiotError as e:
            if e.status != 404:
                status["errors"].append(str(e))
            conn.execute("UPDATE participants SET items = '[]' WHERE match_id = ?", (mid,))
            conn.commit()


async def sync_all(conn, player_id: int | None = None) -> None:
    if status["running"]:
        return
    status.update(running=True, message="starting", new_matches=0, errors=[])
    client = RiotClient()
    try:
        await backfill(conn, client)
        sql = "SELECT puuid FROM accounts"
        args: tuple = ()
        if player_id is not None:
            sql += " WHERE player_id = ?"
            args = (player_id,)
        for (puuid,) in conn.execute(sql, args).fetchall():
            try:
                await sync_puuid(conn, client, puuid, config.SYNC_QUEUES)
            except RiotError as e:
                status["errors"].append(str(e))
                if e.status in (401, 403):
                    break
        status["message"] = f"Done: {status['new_matches']} new matches"
    except Exception as e:  # keep the status endpoint informative
        status["errors"].append(repr(e))
        status["message"] = "Failed"
    finally:
        await client.close()
        status["running"] = False
    start_rank_lookup(conn)


async def _rank_task(conn) -> None:
    client = RiotClient()
    try:
        await ranks.enrich(conn, client)
    finally:
        await client.close()


def start_rank_lookup(conn) -> bool:
    if ranks.status["running"]:
        return False
    asyncio.get_running_loop().create_task(_rank_task(conn))
    return True
