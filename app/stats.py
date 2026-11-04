import json
import sqlite3
import time
from collections import Counter, defaultdict

from . import ranks as rk

QUEUE_NAMES = {420: "Ranked Solo", 440: "Ranked Flex", 450: "ARAM"}


def _queue_clause(queue: int | None, col="m.queue_id"):
    return (f" AND {col} = ?", [queue]) if queue else ("", [])


def _agg(rows) -> dict:
    n = len(rows)
    wins = sum(r["win"] for r in rows)
    k = sum(r["kills"] for r in rows)
    d = sum(r["deaths"] for r in rows)
    a = sum(r["assists"] for r in rows)
    return {
        "games": n, "wins": wins, "losses": n - wins,
        "winrate": round(100 * wins / n, 1) if n else 0,
        "kda": round((k + a) / max(d, 1), 2),
        "avg_kills": round(k / n, 1) if n else 0,
        "avg_deaths": round(d / n, 1) if n else 0,
        "avg_assists": round(a / n, 1) if n else 0,
    }


def _season(version) -> int:
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return 0


def _period_filter(conn, rows, period: str):
    """period: all | season (current) | prev (last season) | <N> days. Season = major patch number."""
    if not period or period == "all":
        return rows
    if period in ("season", "prev"):
        cur = max((_season(r[0]) for r in conn.execute("SELECT DISTINCT version FROM matches")), default=0)
        want = cur if period == "season" else cur - 1
        return [r for r in rows if _season(r["version"]) == want]
    if period.isdigit():
        cutoff = (time.time() - int(period) * 86400) * 1000
        return [r for r in rows if r["game_creation"] >= cutoff]
    return rows


def lane_matchups(conn, rows, min_games: int = 2, limit: int = 5, as_champ: str = "") -> dict:
    """Win rate against each enemy champion played in the same role (lane opponent)."""
    mine = [r for r in rows if r["position"]]
    played = sorted(Counter(r["champion"] for r in mine).items(), key=lambda x: (-x[1], x[0]))
    if as_champ:
        mine = [r for r in mine if r["champion"] == as_champ]
    opp: dict = {}
    for i in range(0, len(mine), 400):
        chunk = mine[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for o in conn.execute(
                f"SELECT match_id, team_id, position, champion FROM participants "
                f"WHERE match_id IN ({marks}) AND position != ''", [r["match_id"] for r in chunk]):
            opp[(o["match_id"], o["team_id"], o["position"])] = o["champion"]
    per: dict = defaultdict(list)
    for r in mine:
        enemy = opp.get((r["match_id"], 300 - r["team_id"], r["position"]))
        if enemy:
            per[enemy].append(r)
    out = [{"champion": c, **_agg(v), "score": (sum(x["win"] for x in v) + 1) / (len(v) + 2)}
           for c, v in per.items() if len(v) >= min_games]
    worst = sorted((m for m in out if m["winrate"] < 50), key=lambda m: (-m["games"], m["score"]))[:limit]
    best = sorted((m for m in out if m["winrate"] > 50), key=lambda m: (-m["games"], -m["score"]))[:limit]
    every = sorted(({"champion": c, **_agg(v)} for c, v in per.items()), key=lambda m: (-m["games"], m["champion"]))
    return {"worst": worst, "best": best, "min_games": min_games, "all": every, "as": as_champ, "played": [{"champion": c, "games": n} for c, n in played]}


def player_stats(conn: sqlite3.Connection, player_id: int, queue: int | None, recent: int = 20,
                 offset: int = 0, search: str = "", period: str = "all", role: str = "", mu_as: str = "") -> dict:
    qc, qa = _queue_clause(queue)
    rows = conn.execute(
        f"""SELECT p.*, m.queue_id, m.game_creation, m.duration, m.version, a.game_name, a.tag_line
            FROM participants p JOIN matches m ON m.id = p.match_id
            JOIN accounts a ON a.puuid = p.puuid
            WHERE a.player_id = ? {qc} ORDER BY m.game_creation DESC""",
        [player_id, *qa],
    ).fetchall()
    rows = _period_filter(conn, rows, period)
    if role:
        rows = [r for r in rows if r["position"] == role]
    by_champ, by_pos, by_acc = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows:
        by_champ[r["champion"]].append(r)
        if r["position"]:
            by_pos[r["position"]].append(r)
        by_acc[f"{r['game_name']}#{r['tag_line']}"].append(r)
    mins = sum(r["duration"] for r in rows) / 60 or 1
    overall = _agg(rows)
    overall["cs_per_min"] = round(sum(r["cs"] for r in rows) / mins, 1)
    overall["vision_per_game"] = round(sum(r["vision"] for r in rows) / len(rows), 1) if rows else 0

    listed = rows
    if search.strip():
        like = f"%{search.strip()}%"
        ids = {r[0] for r in conn.execute(
            """SELECT DISTINCT p.match_id FROM participants p WHERE p.champion LIKE ? OR p.riot_name LIKE ?""",
            (like, like))}
        listed = [r for r in rows if r["match_id"] in ids]
    page = listed[offset:offset + recent]
    rank_map = rk.match_ranks(conn, [r["match_id"] for r in rows])
    diffs = {r["match_id"]: rk.difficulty(rank_map.get(r["match_id"]), r["team_id"]) for r in rows}
    overall["difficulty"] = _avg_difficulty([diffs[r["match_id"]] for r in rows])
    lineups = _lineups(conn, [r["match_id"] for r in page])
    last20 = listed
    last20_champs = defaultdict(list)
    for r in last20:
        last20_champs[r["champion"]].append(r)

    return {
        "overall": overall,
        "summary": {**_agg(last20), "top_champions": sorted(
            ({"champion": c, **_agg(v)} for c, v in last20_champs.items()),
            key=lambda x: -x["games"])[:3]},
        "champions": sorted(({"champion": c, "cs_per_min": round(sum(x["cs"] for x in v) / (sum(
            x["duration"] for x in v) / 60 or 1), 1), **_agg(v)} for c, v in by_champ.items()),
            key=lambda x: -x["games"]),
        "positions": sorted(({"position": c, **_agg(v)} for c, v in by_pos.items()),
                            key=lambda x: -x["games"]),
        "accounts": [{"account": c, **_agg(v)} for c, v in by_acc.items()],
        "teammates": _teammates(conn, rows),
        "matchups": lane_matchups(conn, rows, as_champ=mu_as),
        "recent": [
            {"match_id": r["match_id"], "champion": r["champion"], "win": bool(r["win"]),
             "kills": r["kills"], "deaths": r["deaths"], "assists": r["assists"],
             "queue": QUEUE_NAMES.get(r["queue_id"], str(r["queue_id"])),
             "game_creation": r["game_creation"], "duration": r["duration"],
             "account": f"{r['game_name']}#{r['tag_line']}",
             "cs": r["cs"], "level": r["level"], "position": r["position"], "vision": r["vision"],
             "items": json.loads(r["items"] or "[]"), "keystone": r["keystone"],
             "sub_style": r["sub_style"], "spell1": r["spell1"], "spell2": r["spell2"],
             "difficulty": diffs.get(r["match_id"]),
             "self_puuid": r["puuid"], "players": lineups.get(r["match_id"], [])}
            for r in page
        ],
        "total_matches": len(listed),
    }


def _avg_difficulty(ds: list) -> dict | None:
    """Average opponent vs own-team rank over the games where ranks are known."""
    known = [d for d in ds if d]
    if not known:
        return None
    own = sum(d["own"] for d in known) / len(known)
    opp = sum(d["opp"] for d in known) / len(known)
    return {"own_label": rk.label(own), "opp_label": rk.label(opp), "diff": round((opp - own) / 100, 1),
            "known": len(known), "games": len(ds)}


def _lineups(conn, match_ids: list[str]) -> dict:
    """Compact 10-player list per match, for the match row preview."""
    if not match_ids:
        return {}
    tracked = {r["puuid"]: r["name"] for r in conn.execute(
        "SELECT a.puuid, p.name FROM accounts a JOIN players p ON p.id = a.player_id")}
    out: dict = defaultdict(list)
    for r in conn.execute(
            f"""SELECT match_id, puuid, team_id, champion, riot_name FROM participants
                WHERE match_id IN ({','.join('?' * len(match_ids))}) ORDER BY team_id, rowid""", match_ids):
        out[r["match_id"]].append({"puuid": r["puuid"], "team_id": r["team_id"], "champion": r["champion"],
                                   "name": (r["riot_name"] or "").split("#")[0],
                                   "tracked": tracked.get(r["puuid"])})
    return out


def _teammates(conn, rows, limit: int = 12) -> list[dict]:
    """Who this player plays with most (same team), like U.gg's 'Played With'."""
    if not rows:
        return []
    own = {(r["match_id"]): (r["team_id"], r["puuid"]) for r in rows}
    tracked = {r["puuid"]: r["name"] for r in conn.execute(
        "SELECT a.puuid, p.name FROM accounts a JOIN players p ON p.id = a.player_id")}
    ids = list(own)
    agg: dict = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                f"""SELECT match_id, puuid, team_id, champion, riot_name, win FROM participants
                    WHERE match_id IN ({','.join('?' * len(chunk))})""", chunk):
            team, me = own[r["match_id"]]
            if r["team_id"] != team or r["puuid"] == me:
                continue
            a = agg.setdefault(r["puuid"], {"name": r["riot_name"] or "?", "games": 0, "wins": 0,
                                           "champions": defaultdict(int)})
            a["games"] += 1
            a["wins"] += r["win"]
            a["champions"][r["champion"]] += 1
    out = []
    for puuid, a in agg.items():
        if a["games"] < 2:
            continue
        out.append({"name": a["name"], "tracked": tracked.get(puuid), "games": a["games"],
                    "wins": a["wins"], "losses": a["games"] - a["wins"],
                    "winrate": round(100 * a["wins"] / a["games"], 1),
                    "champion": max(a["champions"], key=a["champions"].get)})
    return sorted(out, key=lambda x: -x["games"])[:limit]


def score_parts(parts) -> dict:
    """Score 0-10 per participant of one match, relative to the other nine.
    Blend of KDA, damage and gold share, kill participation and vision; winners get a +1 bonus.
    The best winner is MVP, the best loser ACE."""
    teams: dict = {}
    for r in parts:
        t = teams.setdefault(r["team_id"], {"k": 0, "dmg": 0, "gold": 0})
        t["k"] += r["kills"] or 0
        t["dmg"] += r["damage"] or 0
        t["gold"] += r["gold"] or 0
    feats = {}
    for r in parts:
        t = teams[r["team_id"]]
        k, d, a = r["kills"] or 0, r["deaths"] or 0, r["assists"] or 0
        feats[r["puuid"]] = (min((k + a) / max(d, 1), 10), (r["damage"] or 0) / max(t["dmg"], 1),
                             (k + a) / max(t["k"], 1), (r["gold"] or 0) / max(t["gold"], 1), r["vision"] or 0)
    n = len(feats) or 1
    means = [(sum(f[i] for f in feats.values()) / n) or 1 for i in range(5)]
    weights = (0.35, 0.25, 0.2, 0.1, 0.1)
    out = {}
    for r in parts:
        raw = sum(w * f / m for w, f, m in zip(weights, feats[r["puuid"]], means))
        out[r["puuid"]] = {"score": round(max(0, min(10, 4.5 * raw + (1 if r["win"] else 0))), 1), "win": bool(r["win"])}
    for win, tag in ((True, "MVP"), (False, "ACE")):
        side = [p for p, v in out.items() if v["win"] == win]
        if side:
            out[max(side, key=lambda p: out[p]["score"])]["tag"] = tag
    for v in out.values():
        v["titles"] = []
    _award_titles(parts, out, teams)
    return out


TITLE_INFO = {
    "untouchable": ("Untouchable", "Fewest deaths in the match (with 5+ kills and assists)"),
    "eagle": ("Eagle Eye", "Highest vision score in the match (20+)"),
    "feeder": ("Feeder", "Most deaths in the match (7+)"),
    "clutch": ("Clutch", "Best performer on a team that won despite having fewer kills than the enemy"),
}


def _award_titles(parts, out, teams) -> None:
    by = {r["puuid"]: r for r in parts}

    def give(key, puuid):
        out[puuid]["titles"].append({"key": key, "label": TITLE_INFO[key][0], "tip": TITLE_INFO[key][1]})

    def sole_best(pick, floor, ok=lambda r: True):
        """Winner of a stat, only when nobody ties for it."""
        cand = [r for r in parts if ok(r)]
        if not cand:
            return None
        best = max(pick(r) for r in cand)
        top = [r for r in cand if pick(r) == best]
        return top[0]["puuid"] if len(top) == 1 and best >= floor else None

    p = sole_best(lambda r: -(r["deaths"] or 0), -1_000_000, lambda r: (r["kills"] or 0) + (r["assists"] or 0) >= 5)
    if p:
        give("untouchable", p)
    p = sole_best(lambda r: r["vision"] or 0, 20)
    if p:
        give("eagle", p)
    p = sole_best(lambda r: r["deaths"] or 0, 7)
    if p:
        give("feeder", p)
    kills = {t: v["k"] for t, v in teams.items()}
    for r in parts:
        if r["win"] and any(kills[r["team_id"]] < k for t, k in kills.items() if t != r["team_id"]):
            side = [q for q in parts if q["team_id"] == r["team_id"]]
            best = max(side, key=lambda q: out[q["puuid"]]["score"])
            if best["puuid"] == r["puuid"]:
                give("clutch", r["puuid"])


def match_scores(conn, match_ids: list[str]) -> dict:
    """{match_id: {puuid: {score, tag?}}} for the given matches."""
    res: dict = {}
    ids = list(dict.fromkeys(match_ids))
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = conn.execute(
            f"SELECT match_id, puuid, team_id, kills, deaths, assists, damage, gold, vision, win "
            f"FROM participants WHERE match_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        by_match: dict = {}
        for r in rows:
            by_match.setdefault(r["match_id"], []).append(r)
        for mid, ps in by_match.items():
            res[mid] = score_parts(ps)
    return res


def match_detail(conn: sqlite3.Connection, match_id: str) -> dict | None:
    m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not m:
        return None
    tracked = {r["puuid"]: r["name"] for r in conn.execute(
        "SELECT a.puuid, p.name FROM accounts a JOIN players p ON p.id = a.player_id")}
    parts = conn.execute(
        "SELECT * FROM participants WHERE match_id = ? ORDER BY team_id, "
        "CASE position WHEN 'TOP' THEN 1 WHEN 'JUNGLE' THEN 2 WHEN 'MIDDLE' THEN 3 "
        "WHEN 'BOTTOM' THEN 4 WHEN 'UTILITY' THEN 5 ELSE 6 END", (match_id,)).fetchall()
    rank_map = rk.match_ranks(conn, [match_id]).get(match_id, {})
    team_diff = {t: rk.difficulty(rank_map, t) for t in {r["team_id"] for r in parts}}
    sc = score_parts(parts)
    return {
        "difficulty": team_diff,
        "id": m["id"], "queue": QUEUE_NAMES.get(m["queue_id"], str(m["queue_id"])),
        "game_creation": m["game_creation"], "duration": m["duration"],
        "participants": [
            {"name": r["riot_name"], "tracked_player": tracked.get(r["puuid"]),
             "team_id": r["team_id"], "win": bool(r["win"]), "champion": r["champion"],
             "position": r["position"], "kills": r["kills"], "deaths": r["deaths"],
             "assists": r["assists"], "cs": r["cs"], "damage": r["damage"], "gold": r["gold"],
             "vision": r["vision"], "level": r["level"],
             "score": sc[r["puuid"]]["score"], "tag": sc[r["puuid"]].get("tag"), "titles": sc[r["puuid"]]["titles"],
             "rank": rk.label(rank_map.get(r["puuid"], (0, None))[1]),
             "items": json.loads(r["items"] or "[]"), "keystone": r["keystone"],
             "sub_style": r["sub_style"], "spell1": r["spell1"], "spell2": r["spell2"]}
            for r in parts
        ],
    }


def team_groups(conn: sqlite3.Connection, queue: int | None) -> list[dict]:
    """One entry per (match, team) with the set of tracked players on that team.
    A player with two accounts counted once."""
    qc, qa = _queue_clause(queue)
    rows = conn.execute(
        f"""SELECT p.match_id, p.team_id, p.win, p.kills, p.deaths, p.assists, p.champion,
                   p.puuid, a.player_id, m.game_creation, m.queue_id, p.riot_name
            FROM participants p JOIN matches m ON m.id = p.match_id
            JOIN accounts a ON a.puuid = p.puuid
            WHERE 1=1 {qc}""", qa,
    ).fetchall()
    groups: dict = {}
    for r in rows:
        g = groups.setdefault((r["match_id"], r["team_id"]), {
            "match_id": r["match_id"], "win": bool(r["win"]), "game_creation": r["game_creation"],
            "queue_id": r["queue_id"], "team_id": r["team_id"], "members": {}})
        g["members"][r["player_id"]] = r
    for g in groups.values():
        g["players"] = frozenset(g["members"])
    return list(groups.values())


def _group_difficulty(conn, games: list, per_game: bool = False):
    """Average difficulty for a set of team_groups (and optionally each game's own value)."""
    ids = [g["match_id"] for g in games if g["queue_id"] in rk.RANKED_QUEUES]
    rank_map = rk.match_ranks(conn, ids)
    ds = {g["match_id"]: rk.difficulty(rank_map.get(g["match_id"]), g["team_id"]) for g in games
          if g["match_id"] in rank_map}
    avg = _avg_difficulty([ds.get(g["match_id"]) for g in games])
    return (avg, ds) if per_game else avg


def squad_stats(conn, player_ids: set[int], queue: int | None, exact: bool = False,
                limit: int = 20, offset: int = 0) -> dict:
    """Games where all given players were on the same team (exact: and no other tracked player)."""
    ids = frozenset(player_ids)
    games = [g for g in team_groups(conn, queue)
             if (g["players"] == ids if exact else ids <= g["players"])]
    games.sort(key=lambda g: -g["game_creation"])
    wins = sum(g["win"] for g in games)
    difficulty, per_game = _group_difficulty(conn, games, per_game=True)
    scores = match_scores(conn, [g["match_id"] for g in games])

    def sc(g, pid):
        return scores.get(g["match_id"], {}).get(g["members"][pid]["puuid"], {})

    members = []
    for pid in ids:
        rs = [g["members"][pid] for g in games]
        ss = [sc(g, pid) for g in games]
        vals = [s["score"] for s in ss if "score" in s]
        members.append({"player_id": pid, **_agg(rs),
                        "avg_score": round(sum(vals) / len(vals), 1) if vals else None,
                        "mvps": sum(s.get("tag") == "MVP" for s in ss),
                        "aces": sum(s.get("tag") == "ACE" for s in ss)})
    return {
        "games": len(games), "wins": wins, "losses": len(games) - wins,
        "winrate": round(100 * wins / len(games), 1) if games else 0,
        "difficulty": difficulty,
        "members": members,
        "recent": [{"match_id": g["match_id"], "win": g["win"], "game_creation": g["game_creation"],
                    "queue": QUEUE_NAMES.get(g["queue_id"], str(g["queue_id"])),
                    "difficulty": per_game.get(g["match_id"]),
                    "lines": [{"player_id": pid, "account": r["riot_name"], "champion": r["champion"], "kills": r["kills"],
                               "deaths": r["deaths"], "assists": r["assists"],
                                                              "score": sc(g, pid).get("score"), "tag": sc(g, pid).get("tag")}
                                                             for pid, r in g["members"].items() if pid in ids]}
                   for g in games[:offset + limit][offset:]],
        "total_matches": len(games),
    }


def _wl(games) -> dict:
    wins = sum(g["win"] for g in games)
    n = len(games)
    return {"games": n, "wins": wins, "losses": n - wins, "winrate": round(100 * wins / n, 1) if n else 0}


def squad_elo(conn, player_ids, queue: int | None) -> dict | None:
    """Average current rank of the squad: each player's best account on the queue's ladder."""
    if not player_ids:
        return None
    ids = list(player_ids)
    rows = conn.execute(
        f"""SELECT a.player_id, r.solo, r.flex FROM accounts a JOIN ranks r ON r.puuid = a.puuid
            WHERE a.player_id IN ({','.join('?' * len(ids))})""", ids).fetchall()
    best: dict = {}
    for r in rows:
        s = rk.pick(r, queue or 420)
        if s is not None:
            best[r["player_id"]] = max(best.get(r["player_id"], s), s)
    if not best:
        return None
    avg = sum(best.values()) / len(best)
    return {"avg": rk.label(avg), "known": len(best), "size": len(ids),
            "players": {pid: rk.label(s) for pid, s in best.items()}}


def explore(conn, player_ids: set[int], queue: int | None, exact: bool = False) -> dict:
    """Stats for a group plus every tracked player who could be added to it.
    exact=True only counts games where no other tracked player was on the team."""
    ids = frozenset(player_ids)
    groups = team_groups(conn, queue)
    superset = [g for g in groups if ids <= g["players"]]
    base = [g for g in superset if g["players"] == ids] if exact else superset
    base_stats = _wl(base)
    # key = extra players added to the group; exact mode keys by the whole lineup so every option has games
    by_extra: dict[frozenset, list] = {}
    for g in superset:
        extra = g["players"] - ids
        if not extra:
            continue
        if exact and ids:
            by_extra.setdefault(extra, []).append(g)
        else:
            for pid in extra:
                by_extra.setdefault(frozenset([pid]), []).append(g)
    if exact and ids:
        # Only the smallest steps: hide a lineup while a smaller one inside it has games of its own
        by_extra = {e: gs for e, gs in by_extra.items() if not any(o < e for o in by_extra)}
    adds = []
    for extra, gs in by_extra.items():
        s = _wl(gs)
        adds.append({"player_id": min(extra), "player_ids": sorted(extra), **s,
                     "delta": round(s["winrate"] - base_stats["winrate"], 1),
                     "share": round(100 * s["games"] / len(superset), 1) if superset else 0})
    adds.sort(key=lambda a: (-a["games"], len(a["player_ids"])))
    if exact:
        adds = adds[:14]
    return {"player_ids": sorted(ids), **base_stats, "difficulty": _group_difficulty(conn, base),
            "elo": squad_elo(conn, ids, queue),
            "additions": adds}

def suggest_squads(conn, queue: int | None, min_games: int = 5, limit: int = 12) -> list[dict]:
    """Groups of 2-5 tracked people who often share a team, counting rotating fifth members.
    A group is dropped when a bigger group covers most of its games, and existing squads are skipped."""
    from itertools import combinations

    agg: dict = defaultdict(lambda: [0, 0])
    for g in team_groups(conn, queue):
        members = sorted(g["players"])
        for size in range(2, len(members) + 1):
            for combo in combinations(members, size):
                agg[frozenset(combo)][0] += 1
                agg[frozenset(combo)][1] += g["win"]
    existing = {frozenset(r[0] for r in conn.execute(
        "SELECT player_id FROM squad_players WHERE squad_id = ?", (s[0],)))
        for s in conn.execute("SELECT id FROM squads")}
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM players")}
    out = []
    for group, (n, w) in agg.items():
        if n < min_games or group in existing:
            continue
        if any(group < other and agg[other][0] >= 0.8 * n for other in agg if len(other) > len(group)):
            continue
        out.append({"player_ids": sorted(group), "names": sorted(names[i] for i in group),
                    "games": n, "wins": w, "losses": n - w, "winrate": round(100 * w / n, 1)})
    return sorted(out, key=lambda x: (-x["games"], -len(x["player_ids"])))[:limit]


def suggestions(conn, min_games: int = 3, limit: int = 30) -> list[dict]:
    """Untracked players who were on the same team as a tracked account, most frequent first."""
    rows = conn.execute(
        """SELECT p.puuid, p.riot_name, p.win, p.match_id, p.champion, p.team_id, m.game_creation,
                  (SELECT GROUP_CONCAT(DISTINCT pl.name) FROM participants t
                     JOIN accounts a ON a.puuid = t.puuid JOIN players pl ON pl.id = a.player_id
                    WHERE t.match_id = p.match_id AND t.team_id = p.team_id) AS with_players
           FROM participants p JOIN matches m ON m.id = p.match_id
           WHERE p.puuid NOT IN (SELECT puuid FROM accounts)
             AND p.puuid NOT IN (SELECT puuid FROM ignored_suggestions)
             AND EXISTS (SELECT 1 FROM participants t JOIN accounts a ON a.puuid = t.puuid
                          WHERE t.match_id = p.match_id AND t.team_id = p.team_id)""").fetchall()
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r["puuid"], {"games": 0, "wins": 0, "last": 0, "name": "", "champs": defaultdict(int),
                                        "with": defaultdict(int)})
        a["games"] += 1
        a["wins"] += r["win"]
        a["champs"][r["champion"]] += 1
        for n in (r["with_players"] or "").split(","):
            if n:
                a["with"][n] += 1
        if r["game_creation"] >= a["last"]:
            a["last"] = r["game_creation"]
            a["name"] = r["riot_name"] or a["name"]
    out = []
    for puuid, a in agg.items():
        if a["games"] < min_games:
            continue
        game_name, _, tag = a["name"].partition("#")
        out.append({
            "puuid": puuid, "riot_id": a["name"], "game_name": game_name, "tag_line": tag,
            "games": a["games"], "wins": a["wins"], "losses": a["games"] - a["wins"],
            "winrate": round(100 * a["wins"] / a["games"], 1), "last_played": a["last"],
            "champion": max(a["champs"], key=a["champs"].get),
            "with": sorted(a["with"], key=lambda k: -a["with"][k]),
        })
    return sorted(out, key=lambda x: (-x["games"], -x["last_played"]))[:limit]


def auto_squads(conn, queue: int | None, min_games: int = 3, min_size: int = 2) -> list[dict]:
    """Every exact lineup of tracked players that actually played together, with its record."""
    agg: dict = defaultdict(lambda: [0, 0])
    for g in team_groups(conn, queue):
        if len(g["players"]) >= min_size:
            agg[g["players"]][0] += 1
            agg[g["players"]][1] += g["win"]
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM players")}
    out = []
    for lineup, (n, w) in agg.items():
        if n >= min_games:
            out.append({"player_ids": sorted(lineup), "names": sorted(names[i] for i in lineup),
                        "games": n, "wins": w, "losses": n - w, "winrate": round(100 * w / n, 1)})
    return sorted(out, key=lambda x: (-x["games"], -x["winrate"]))
