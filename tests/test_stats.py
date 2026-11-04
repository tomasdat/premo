import sqlite3

from app import db, stats, sync


def match(mid, queue, team_players, win_team, ts):
    """team_players: {team_id: [puuid,...]}"""
    parts = []
    for team, puuids in team_players.items():
        for p in puuids:
            parts.append({"puuid": p, "teamId": team, "championName": "Ahri", "teamPosition": "MIDDLE",
                          "kills": 5, "deaths": 2, "assists": 7, "totalMinionsKilled": 150,
                          "totalDamageDealtToChampions": 10000, "goldEarned": 9000, "visionScore": 20,
                          "win": team == win_team, "riotIdGameName": p, "riotIdTagline": "EUW"})
    return {"metadata": {"matchId": mid},
            "info": {"queueId": queue, "gameCreation": ts, "gameDuration": 1800,
                     "gameVersion": "14.1", "participants": parts}}


def setup():
    conn = db.connect(":memory:")
    for pid, name in [(1, "A"), (2, "B"), (3, "C")]:
        conn.execute("INSERT INTO players VALUES (?,?)", (pid, name))
    # B has two accounts
    for puuid, pid in [("a", 1), ("b1", 2), ("b2", 2), ("c", 3)]:
        conn.execute("INSERT INTO accounts VALUES (?,?,?,?)", (puuid, puuid, "EUW", pid))
    return conn


def test_squad_and_multi_account():
    conn = setup()
    others = ["x1", "x2", "x3"]
    sync.store_match(conn, match("M1", 440, {100: ["a", "b1", "c", "x1", "x2"], 200: ["x3"]}, 100, 1))
    sync.store_match(conn, match("M2", 440, {100: ["a", "b2", "x1", "x2", "x3"], 200: others[:1]}, 200, 2))
    sync.store_match(conn, match("M3", 420, {100: ["a", "b1", "x1", "x2", "x3"], 200: ["c"]}, 100, 3))

    s = stats.squad_stats(conn, {1, 2}, 440)
    assert (s["games"], s["wins"]) == (2, 1)
    assert stats.squad_stats(conn, {1, 2}, None)["games"] == 3
    assert stats.squad_stats(conn, {1, 2}, 440, exact=True)["games"] == 1

    ps = stats.player_stats(conn, 2, None)
    assert ps["overall"]["games"] == 3
    assert {a["account"] for a in ps["accounts"]} == {"b1#EUW", "b2#EUW"}

    auto = stats.auto_squads(conn, 440, min_games=1)
    assert {tuple(x["names"]) for x in auto} == {("A", "B", "C"), ("A", "B")}


def test_rerun_idempotent():
    conn = setup()
    m = match("M1", 440, {100: ["a"], 200: ["x"]}, 100, 1)
    sync.store_match(conn, m)
    sync.store_match(conn, m)
    assert conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 2


def test_suggestions():
    conn = setup()
    for i in range(3):
        # "x1" is always with A; "x2" is an enemy; "c" is tracked so must not be suggested
        sync.store_match(conn, match(f"S{i}", 440, {100: ["a", "x1", "c"], 200: ["x2"]}, 100, i))
    sync.store_match(conn, match("S9", 440, {100: ["a", "x3"], 200: ["x1"]}, 100, 9))
    s = stats.suggestions(conn, min_games=3)
    assert [x["puuid"] for x in s] == ["x1"]
    assert s[0]["games"] == 3 and s[0]["winrate"] == 100 and s[0]["with"] == ["A", "C"]
    conn.execute("INSERT INTO ignored_suggestions VALUES ('x1')")
    assert stats.suggestions(conn, min_games=3) == []


def test_suggest_squads_rotating_core():
    conn = setup()
    # A+B always together, C rotates in for half the games
    for i in range(6):
        team = ["a", "b1", "x1", "x2"] + (["c"] if i % 2 else [])
        sync.store_match(conn, match(f"R{i}", 440, {100: team, 200: ["y1"]}, 100, i))
    s = stats.suggest_squads(conn, 440, min_games=3)
    assert {tuple(x["names"]) for x in s} == {("A", "B"), ("A", "B", "C")}
    assert s[0]["names"] == ["A", "B"] and s[0]["games"] == 6
    conn.execute("INSERT INTO squads(id, name) VALUES (1, 'x')")
    conn.execute("INSERT INTO squad_players VALUES (1, 1), (1, 2)")
    assert all(x["names"] != ["A", "B"] for x in stats.suggest_squads(conn, 440, min_games=3))


def test_explore():
    conn = setup()
    sync.store_match(conn, match("M1", 440, {100: ["a", "b1", "c", "x1", "x2"], 200: ["x3"]}, 100, 1))
    sync.store_match(conn, match("M2", 440, {100: ["a", "b2", "x1", "x2", "x3"], 200: ["x4"]}, 200, 2))
    e = stats.explore(conn, {1}, 440)
    assert (e["games"], e["wins"]) == (2, 1)
    adds = {a["player_id"]: a for a in e["additions"]}
    assert adds[2]["games"] == 2 and adds[3]["games"] == 1 and adds[3]["wins"] == 1
    assert stats.explore(conn, {1, 2, 3}, 440)["games"] == 1
    assert len(stats.explore(conn, set(), 440)["additions"]) == 3
    only = stats.explore(conn, {1, 2}, 440, exact=True)
    assert only["games"] == 1 and only["wins"] == 0
    assert stats.explore(conn, {1}, 440, exact=True)["games"] == 0


def test_rank_difficulty():
    from app import ranks
    assert ranks.label(ranks.score('GOLD', 'II', 0)) == 'Gold II'
    r = {'a': (100, 1200), 'b': (100, 1200), 'c': (100, 1200), 'd': (200, 1600), 'e': (200, 1600), 'f': (200, 1600)}
    d = ranks.difficulty(r, 100)
    assert d['diff'] == 4.0 and d['opp_label'] == 'Platinum IV'
    assert ranks.difficulty({'a': (100, None)}, 100) is None
