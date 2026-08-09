#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Capture what the Flask app answers today, so the Go port can be held to it.

Output goes to testdata/*.json and is read by the Go tests. Regenerate only when
the Python app is still the reference implementation.
"""
import datetime
import json
import os
import re
import sys

import flask_app

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, "testdata")

TEAMMATE_RE = re.compile(
    r'<li><a href="https://rating\.chgk\.info/player/(\d+)">\d+</a>, '
    r'<a href="/stats\?[^"]*">([^<]*)</a> — '
    r'<a href="/together\?[^"]*">(\d+) игр'
)
TOGETHER_RE = re.compile(
    r'<li><a href="https://rating\.chgk\.info/tournament/(\d+)">\d+</a> '
    r'<a href="/tournament/\d+">([^<]*)</a> <small>\(([^,]*), ([^)]*)\)</small></li>'
)
PATH_RE = re.compile(r'<li><a href="https://rating\.chgk\.info/player/(\d+)">')
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TRUEDL_RE = re.compile(r"<p>TrueDL: ([\d.]+), по турам: ([^<]*)</p>")
CATEGORIES_RE = re.compile(r"⚰️</abbr> — (\d+), .*?👹</abbr> — (\d+),.*?😥</abbr> — (\d+),"
                           r".*?🙂</abbr> — (\d+),.*?👶</abbr> — (\d+),.*?🐣</abbr> — (\d+)\.", re.S)
TEAM_RE = re.compile(r'<a href="https://rating\.chgk\.info/teams/(\d+)">')

TEAMMATE_CASES = [
    {"player_id": 30152, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "all_tournaments"},
    {"player_id": 30152, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "lan_sync"},
    {"player_id": 30152, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "online_async"},
    {"player_id": 30152, "date_from": "2018-01-01", "date_to": "2019-01-01",
     "tournament_types": "all_tournaments"},
    {"player_id": 1, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "all_tournaments"},
    {"player_id": 99999999, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "all_tournaments"},
]

TOGETHER_CASES = [
    {"id1": 30152, "id2": 13782, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "all_tournaments"},
    {"id1": 30152, "id2": 13782, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "lan_sync"},
    {"id1": 30152, "id2": 99999999, "date_from": "1990-01-01", "date_to": "2026-01-01",
     "tournament_types": "all_tournaments"},
]

# 1355 and 341457 sit on the rim of the graph with a single teammate each, so the pair
# needs a long chain rather than a hub hop.
HANDSHAKE_CASES = [(30152, 13782), (1, 30152), (1355, 341457), (2084, 341436),
                   (30152, 30152), (30152, 99999999)]

# 12107 and 12639 are rated and post-date the first release; 6342 predates every release,
# which exercises the fallback to the earliest one.
TOURNAMENT_CASES = [12107, 12639, 6342]


def player_name(html):
    m = re.search(r'<a href="/stats\?player_id=\d+[^"]*">([^<]*)</a> с ', html)
    return m.group(1) if m else None


def capture_teammates(client):
    out = []
    for case in TEAMMATE_CASES:
        resp = client.get("/stats", query_string=case)
        html = resp.get_data(as_text=True)
        teammates = [
            {"player_id": int(pid), "name": name, "games": int(games)}
            for pid, name, games in TEAMMATE_RE.findall(html)
        ]
        out.append({**case, "player_name": player_name(html), "teammates": teammates})
        print(f"  stats {case['player_id']} {case['tournament_types']}: {len(teammates)} teammates")
    return out


def capture_together(client):
    out = []
    for case in TOGETHER_CASES:
        resp = client.get("/together", query_string={
            "id1": case["id1"], "id2": case["id2"], "date_from": case["date_from"],
            "date_to": case["date_to"], "tournament_types": case["tournament_types"]})
        html = resp.get_data(as_text=True)
        tournaments = [
            {"id": int(tid), "name": name, "dates": dates, "type": ttype}
            for tid, name, dates, ttype in TOGETHER_RE.findall(html)
        ]
        out.append({**case, "tournaments": tournaments})
        print(f"  together {case['id1']}+{case['id2']} {case['tournament_types']}: "
              f"{len(tournaments)} tournaments")
    return out


def capture_handshakes(client):
    out = []
    for id1, id2 in HANDSHAKE_CASES:
        resp = client.post("/handshakes", data={"player_id1": id1, "player_id2": id2})
        html = resp.get_data(as_text=True)
        intermediates = []
        if "не найдены" in html:
            outcome, hops = "unknown_player", None
        elif id1 == id2:
            outcome, hops = "self", 0
        elif "соединены напрямую" in html:
            outcome, hops = "direct", 1
        elif "Путь не найден" in html:
            outcome, hops = "no_path", None
        else:
            outcome = "path"
            intermediates = [int(p) for p in PATH_RE.findall(html)]
            hops = len(intermediates) + 1
        out.append({"id1": id1, "id2": id2, "outcome": outcome, "hops": hops,
                    "intermediates": intermediates})
        print(f"  handshakes {id1}->{id2}: {outcome}, {hops} hops")
    return out


def capture_tournaments(client):
    out = []
    for tid in TOURNAMENT_CASES:
        html = client.get(f"/tournament/{tid}").get_data(as_text=True)
        cats = CATEGORIES_RE.search(html)
        truedl = TRUEDL_RE.search(html)
        rows = []
        for row in ROW_RE.findall(html):
            cells = CELL_RE.findall(row)
            if not cells:
                continue
            team = TEAM_RE.search(cells[0])
            if not team:
                continue
            rows.append({
                "team_id": int(team.group(1)),
                "rating": int(cells[1]),
                "taken": int(cells[2]),
                "avg_rating": float(cells[3]),
                "by_category": [int(c) for c in cells[4:9]],
            })
        out.append({
            "tournament_id": tid,
            "categories": {k: int(v) for k, v in zip(
                ["coffin", "a", "b", "c", "d", "e"], cats.groups())} if cats else None,
            "truedl": float(truedl.group(1)) if truedl else None,
            "truedl_by_tour": truedl.group(2) if truedl else None,
            "teams": rows,
        })
        print(f"  tournament {tid}: {len(rows)} teams, truedl {out[-1]['truedl']}")
    return out


def capture_tournaments_page(client):
    html = client.get("/tournaments", query_string={"page": 1}).get_data(as_text=True)
    cutoff = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    total_pages = int(re.search(r"1 из (\d+)", html).group(1))
    rows = []
    for row in ROW_RE.findall(html)[:40]:
        cells = CELL_RE.findall(row)
        if len(cells) != 6:
            continue
        tid = re.search(r"/tournament/(\d+)\"", cells[0])
        editors = [int(e) for e in re.findall(r"/player/(\d+)", cells[5])]
        rows.append({
            "id": int(tid.group(1)),
            "name": re.sub(r"<[^>]+>", "", cells[1]),
            "has_results": "/tournament/" in cells[1],
            "date_start": cells[2].strip(),
            "date_end": cells[3].strip(),
            "type": cells[4].strip(),
            "editors": editors,
        })
    print(f"  tournaments page 1: {len(rows)} rows captured, {total_pages} pages")
    return {"page": 1, "cutoff": cutoff, "total_pages": total_pages, "rows": rows}


def build_graph_db():
    """Rebuild the graph flask_app.py reads.

    create_graph.py writes graph.bin for the Go server and nothing else, so the outgoing
    implementation needs its own copy built here — from the same buff.db, or the handshake
    fixtures would describe a different graph than the one under test.
    """
    import itertools
    import sqlite3

    path = os.path.join(DIR, "graph.db")
    pairs = set()
    source = sqlite3.connect(os.path.join(DIR, "buff.db"))
    for (members,) in source.execute("select team_members from tournament_results"):
        pairs.update(itertools.combinations(sorted(set(json.loads(members))), 2))
    if os.path.exists(path):
        os.remove(path)
    graph = sqlite3.connect(path)
    graph.execute("CREATE TABLE edges (player1 INTEGER, player2 INTEGER, PRIMARY KEY (player1, player2))")
    graph.execute("CREATE INDEX idx_player2 ON edges(player2)")
    graph.executemany("INSERT INTO edges VALUES (?,?)", pairs)
    graph.execute("CREATE TABLE nodes (player_id INTEGER PRIMARY KEY)")
    graph.executemany("INSERT INTO nodes VALUES (?)", [(p,) for p in set(itertools.chain(*pairs))])
    graph.commit()
    graph.close()
    print(f"graph.db: {len(pairs)} edges")


def main():
    os.makedirs(OUT, exist_ok=True)
    build_graph_db()
    client = flask_app.app.test_client()
    captures = {
        "teammates": capture_teammates,
        "together": capture_together,
        "handshakes": capture_handshakes,
        "tournaments": capture_tournaments,
        "tournaments_page": capture_tournaments_page,
    }
    for name, fn in captures.items():
        print(f"{name}:")
        data = fn(client)
        path = os.path.join(OUT, f"{name}.json")
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"  -> {path} ({os.path.getsize(path) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
