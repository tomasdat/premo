import asyncio

import re

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config, db, ranks, stats, sync
from .riot import RiotClient, RiotError

app = FastAPI(title="Premo")
conn = db.connect()
auth.init(conn)
app.include_router(auth.router)

# Changing the tracked data or triggering Riot calls is admin-only; reading and personal squads are open to any account
ADMIN_WRITES = re.compile(r"^/api/(players|accounts|suggestions|sync|ranks)(/|$)")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        user = auth.user_from_request(request)
        if not user:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        if request.method not in ("GET", "HEAD") and ADMIN_WRITES.match(path) and user["role"] != "admin":
            return JSONResponse({"detail": "Admins only"}, status_code=403)
        request.state.user = user
    return await call_next(request)


class PlayerIn(BaseModel):
    name: str


class AccountIn(BaseModel):
    riot_id: str  # "GameName#TAG"


class SquadIn(BaseModel):
    name: str
    player_ids: list[int]


@app.get("/api/players")
def list_players(queue: int | None = None):
    players = [dict(r) for r in conn.execute("SELECT * FROM players ORDER BY name")]
    qc, qa = stats._queue_clause(queue)
    counts = {r[0]: r[1] for r in conn.execute(
        f"""SELECT a.player_id, COUNT(*) FROM participants p JOIN matches m ON m.id = p.match_id
            JOIN accounts a ON a.puuid = p.puuid WHERE 1=1 {qc} GROUP BY a.player_id""", qa)}
    for p in players:
        p["games"] = counts.get(p["id"], 0)
    for p in players:
        p["accounts"] = [dict(r) for r in conn.execute(
            "SELECT puuid, game_name, tag_line FROM accounts WHERE player_id = ?", (p["id"],))]
    return players


@app.post("/api/players")
def add_player(body: PlayerIn):
    try:
        cur = conn.execute("INSERT INTO players(name) VALUES (?)", (body.name.strip(),))
    except Exception:
        raise HTTPException(409, "Player already exists")
    conn.commit()
    return {"id": cur.lastrowid, "name": body.name.strip()}


@app.delete("/api/players/{player_id}")
def delete_player(player_id: int):
    conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
    conn.commit()
    return {"ok": True}


@app.post("/api/players/{player_id}/accounts")
async def add_account(player_id: int, body: AccountIn):
    if "#" not in body.riot_id:
        raise HTTPException(400, "Use the format Name#TAG")
    name, tag = body.riot_id.rsplit("#", 1)
    client = RiotClient()
    try:
        acc = await client.account_by_riot_id(name.strip(), tag.strip())
    except RiotError as e:
        raise HTTPException(404 if e.status == 404 else 502, str(e))
    finally:
        await client.close()
    conn.execute(
        "INSERT OR REPLACE INTO accounts(puuid, game_name, tag_line, player_id) VALUES (?,?,?,?)",
        (acc["puuid"], acc.get("gameName", name), acc.get("tagLine", tag), player_id))
    conn.commit()
    return {"puuid": acc["puuid"]}


@app.delete("/api/accounts/{puuid}")
def delete_account(puuid: str):
    conn.execute("DELETE FROM accounts WHERE puuid = ?", (puuid,))
    conn.commit()
    return {"ok": True}


@app.post("/api/sync")
def start_sync(background: BackgroundTasks, player_id: int | None = None):
    if sync.status["running"]:
        raise HTTPException(409, "Sync already running")
    background.add_task(sync.sync_all, conn, player_id)
    return {"started": True}


@app.get("/api/sync/status")
def sync_status():
    return {**sync.status, "ranks": ranks.status}


@app.post("/api/ranks/sync")
async def start_rank_sync():
    return {"started": sync.start_rank_lookup(conn)}


@app.get("/api/players/{player_id}/stats")
def player_stats(player_id: int, queue: int | None = None, offset: int = 0, limit: int = 20,
                 search: str = "", period: str = "all", role: str = "", mu_as: str = ""):
    return stats.player_stats(conn, player_id, queue, limit, offset, search, period, role, mu_as)


@app.get("/api/matches/{match_id}")
def match_detail(match_id: str):
    d = stats.match_detail(conn, match_id)
    if not d:
        raise HTTPException(404, "Match not found")
    return d


@app.get("/api/squads")
def list_squads(request: Request, queue: int | None = None, exact: bool = False):
    out = []
    for s in conn.execute("SELECT * FROM squads WHERE owner_id = ? ORDER BY name", (request.state.user["id"],)).fetchall():
        ids = [r[0] for r in conn.execute(
            "SELECT player_id FROM squad_players WHERE squad_id = ?", (s["id"],))]
        st = stats.squad_stats(conn, set(ids), queue, exact)
        out.append({"id": s["id"], "name": s["name"], "player_ids": ids, **st})
    return out


@app.post("/api/squads")
def add_squad(body: SquadIn, request: Request):
    if len(body.player_ids) < 2:
        raise HTTPException(400, "A squad needs at least 2 players")
    try:
        cur = conn.execute("INSERT INTO squads(name, owner_id) VALUES (?,?)", (body.name.strip(), request.state.user["id"]))
    except Exception:
        raise HTTPException(409, "Squad name already exists")
    conn.executemany("INSERT INTO squad_players VALUES (?,?)",
                     [(cur.lastrowid, p) for p in set(body.player_ids)])
    conn.commit()
    return {"id": cur.lastrowid}


@app.patch("/api/squads/{squad_id}")
def rename_squad(squad_id: int, body: dict, request: Request):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Name required")
    try:
        cur = conn.execute("UPDATE squads SET name = ? WHERE id = ? AND owner_id = ?", (name, squad_id, request.state.user["id"]))
    except Exception:
        raise HTTPException(409, "Squad name already exists")
    if not cur.rowcount:
        raise HTTPException(404, "Squad not found")
    conn.commit()
    return {"ok": True}


@app.delete("/api/squads/{squad_id}")
def delete_squad(squad_id: int, request: Request):
    conn.execute("DELETE FROM squads WHERE id = ? AND owner_id = ?", (squad_id, request.state.user["id"]))
    conn.commit()
    return {"ok": True}


@app.get("/api/squads/suggestions")
def squad_suggestions(queue: int | None = None, min_games: int = 5):
    return stats.suggest_squads(conn, queue, min_games)


@app.get("/api/squads/auto")
def auto_squads(queue: int | None = None, min_games: int = 3, min_size: int = 2):
    return stats.auto_squads(conn, queue, min_games, min_size)


@app.get("/api/squads/explore")
def explore_squad(players: str, queue: int | None = None, exact: bool = False):
    ids = {int(x) for x in players.split(",") if x}
    return stats.explore(conn, ids, queue, exact)


@app.get("/api/squads/custom")
def custom_squad(players: str, queue: int | None = None, exact: bool = False,
                 offset: int = 0, limit: int = 20):
    ids = {int(x) for x in players.split(",") if x}
    return stats.squad_stats(conn, ids, queue, exact, limit, offset)


class SuggestionIn(BaseModel):
    puuid: str
    player_id: int | None = None  # attach to an existing person (e.g. a second account)
    name: str | None = None       # otherwise create a new person with this name


@app.get("/api/suggestions")
def get_suggestions(min_games: int = 3):
    return stats.suggestions(conn, min_games)


@app.post("/api/suggestions/add")
def add_suggestion(body: SuggestionIn, background: BackgroundTasks):
    row = conn.execute(
        "SELECT riot_name FROM participants WHERE puuid = ? AND riot_name IS NOT NULL "
        "ORDER BY rowid DESC LIMIT 1", (body.puuid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown player")
    game_name, _, tag = (row["riot_name"] or "").partition("#")
    player_id = body.player_id
    if player_id is None:
        name = (body.name or game_name or "Player").strip()
        base, n = name, 2
        while conn.execute("SELECT 1 FROM players WHERE name = ?", (name,)).fetchone():
            name, n = f"{base} {n}", n + 1
        player_id = conn.execute("INSERT INTO players(name) VALUES (?)", (name,)).lastrowid
    elif not conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone():
        raise HTTPException(404, "Person not found")
    conn.execute("INSERT OR REPLACE INTO accounts(puuid, game_name, tag_line, player_id) VALUES (?,?,?,?)",
                 (body.puuid, game_name, tag, player_id))
    conn.commit()
    started = not sync.status["running"]
    if started:
        background.add_task(sync.sync_all, conn, player_id)
    return {"player_id": player_id, "sync_started": started}


@app.post("/api/suggestions/dismiss")
def dismiss_suggestion(body: SuggestionIn):
    conn.execute("INSERT OR IGNORE INTO ignored_suggestions(puuid) VALUES (?)", (body.puuid,))
    conn.commit()
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return {"platform": config.PLATFORM, "queues": stats.QUEUE_NAMES,
            "has_key": bool(config.RIOT_API_KEY)}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
