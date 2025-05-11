#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import json
import logging
import logging.handlers
import os
import sqlite3
import textwrap
from collections import Counter, defaultdict
from functools import wraps
from typing import NamedTuple

import dill
import networkx as nx
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template_string,
    request,
    url_for,
)

from config import Config

UTC_PLUS_3 = datetime.timezone(datetime.timedelta(seconds=10800))
DIR = os.path.dirname(os.path.abspath(__file__))
DB_LOC = os.path.join(DIR, "buff.db")
GRAPH_PATH = os.path.join(DIR, "graph.pickle")


class Tournament(NamedTuple):
    id: int
    name: str
    date_start: str
    date_end: str
    type: str


def debug_only(f):
    @wraps(f)
    def wrapped(**kwargs):
        if not current_app.debug:
            abort(404)

        return f(**kwargs)

    return wrapped


class GraphContainer:
    def __init__(self):
        self.g = None

    def load_graph(self, path):
        with open(path, "rb") as f:
            self.g = dill.load(f)


gc = GraphContainer()


class Formatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.astimezone(UTC_PLUS_3)

    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            return dt.strftime("%Y-%m-%d %H:%M:%S%z")
        return s


app = Flask("Buff", static_folder="static")
app.config.from_object(Config)
log_path = os.path.join(DIR, "buff.log")
formatter = Formatter("%(asctime)s %(message)s")
fileHandler = logging.handlers.RotatingFileHandler(
    log_path,
    maxBytes=20 * 1024 * 16,
    backupCount=1,
)
fileHandler.setFormatter(formatter)
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(formatter)
logger = logging.getLogger("buff_db_updater")
logger.setLevel(logging.DEBUG)
logger.addHandler(consoleHandler)
logger.addHandler(fileHandler)

HTML_HEADER = """\
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="{{ url_for('static', filename='water.css') }}">
    <style>
        .message {
            border-radius: 6px;
            padding: 10px;
        }
        .message.green {
            background-color: green;
            color: white;
        }
        .message.red {
            background-color: rgb(236, 155, 155);
            color: rgb(144, 2, 2);
            border-color: rgb(144, 2, 2);
        }
        body {
            font-variant-numeric: tabular-nums;
        }
    </style>
</head>
<body>
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    {% for category, message in messages %}
      <p class="message {{ category }}">{{ message }}</p>
    {% endfor %}
  {% endif %}
{% endwith %}
"""

HTML_STUB = (
    HTML_HEADER
    + """\
<h1>С кем вы играли чаще всего?</h1>
<form action="{{ url_for('.index') }}" method="post">
<label for="player_id">Ваш ID</label><input name="player_id" placeholder="12345"[PLAYER_ID]></input>
<label for="date_from">С даты</label><input name="date_from" placeholder="1990-02-28"[DATE_FROM]></input>
<label for="date_to">По дату</label><input name="date_to" placeholder="2024-02-28"[DATE_TO]></input>
<div><input id="all_tournaments" type="radio" name="tournament_types" value="all_tournaments" [ALL_TOURNAMENTS_CHECKED]><label for="all_tournaments">Все турниры</label></div>
<div><input id="lan_sync" type="radio" name="tournament_types" value="lan_sync" [LAN_SYNC_CHECKED]><label for="lan_sync">Очники и синхроны</label></div>
<div><input id="online_async" type="radio" name="tournament_types" value="online_async" [ONLINE_ASYNC_CHECKED]><label for="online_async">Онлайны и асинхроны</label></div>
<input type="submit" value="Рассчитать"></input>
</form>
{{ rendered_content|safe }}
<p><small>Последнее обновление базы: {{last_db_update|safe}}</small></p>
</body>
"""
)

TOURNAMENT_STUB = (
    HTML_HEADER
    + """{{ rendered_content|safe }}
<p><small>Последнее обновление базы: {{last_db_update|safe}}</small></p>
</body>
"""
)

HANDSHAKES_STUB = (
    HTML_HEADER
    + """\
<h1>N рукопожатий</h1>
<form action="{{ url_for('.handshakes') }}" method="post">
<label for="player_id1">ID игрока 1</label><input name="player_id1" placeholder="12345" value="{{ player_id1|safe }}"></input>
<label for="player_id2">ID игрока 2</label><input name="player_id2" placeholder="67890" value="{{ player_id2|safe }}"></input>
<input type="submit" value="Рассчитать"></input>
</form>
{{ rendered_content|safe }}
</body>
"""
)


def get_html_stub(
    player_id="",
    date_from="",
    date_to="",
    tournament_types="",
):
    result = HTML_STUB
    if player_id:
        result = result.replace("[PLAYER_ID]", f'value="{player_id}"')
    else:
        result = result.replace("[PLAYER_ID]", "")
    if date_from:
        result = result.replace("[DATE_FROM]", f'value="{date_from}"')
    else:
        result = result.replace("[DATE_FROM]", "")
    if date_to:
        result = result.replace("[DATE_TO]", f'value="{date_to}"')
    else:
        result = result.replace("[DATE_TO]", "")
    if tournament_types == "lan_sync":
        result = (
            result.replace("[LAN_SYNC_CHECKED]", 'checked=""')
            .replace("[ONLINE_ASYNC_CHECKED]", "")
            .replace("[ALL_TOURNAMENTS_CHECKED]", "")
        )
    elif tournament_types == "online_async":
        result = (
            result.replace("[LAN_SYNC_CHECKED]", "")
            .replace("[ONLINE_ASYNC_CHECKED]", 'checked=""')
            .replace("[ALL_TOURNAMENTS_CHECKED]", "")
        )
    else:
        result = (
            result.replace("[LAN_SYNC_CHECKED]", "")
            .replace("[ONLINE_ASYNC_CHECKED]", "")
            .replace("[ALL_TOURNAMENTS_CHECKED]", 'checked=""')
        )
    return result


def tryint(x):
    try:
        if isinstance(x, str):
            x = x.strip()
        return int(x)
    except:
        return


def try_parse_date(date):
    try:
        return datetime.datetime.strptime(date.strip(), "%Y-%m-%d").date()
    except:
        return


QUERY_STUB = """\
with results as (
    select
        id as tournament_id,
        team_members
    from search
    where team_members match '{player_id}'
), right_tournaments as (
    select
        id as tournament_id,
        name,
        date_start,
        date_end,
        tournament_type
    from tournaments
    where date_start > '{date_from}' and date_start < '{date_to}' and tournament_type != 'Общий зачёт'
)
select
    r.tournament_id as tournament_id,
    r.team_members as team_members,        
    r1.name as name,
    r1.date_start as date_start,
    r1.date_end as date_end,
    r1.tournament_type as tournament_type
from results as r
inner join right_tournaments as r1 on (r.tournament_id = r1.tournament_id);
"""

TOGETHER_QUERY_STUB = """\
with results as (
    select
        id as tournament_id, team_members
    from search
    where team_members match '{player_id1}' and team_members match '{player_id2}'
), right_tournaments as (
    select
        id as tournament_id,
        name as tournament_name,
        date_start,
        date_end,
        tournament_type
    from tournaments
    where date_start > '{date_from}' and date_start < '{date_to}' and tournament_type != 'Общий зачёт'
)
select
    r.tournament_id as tournament_id,
    r.team_members as team_members,
    r1.tournament_name as tournament_name,
    r1.date_start as date_start,
    r1.date_end as date_end,
    r1.tournament_type as tournament_type
from results as r
inner join right_tournaments as r1 on (r.tournament_id = r1.tournament_id);
"""

PLAYER_QUERY_STUB = """\
select id, name, surname from players where id in ({player_ids});
"""

RENDERED_CONTENT_STUB = """\
<h2>Статистика игрока <a href="https://rating.chgk.info/player/{player_id}">{player_id}</a>, <a href="/stats?player_id={player_id}&date_from={date_from}&date_to={date_to}">{player_name}</a> с {date_from} по {date_to} ({tournament_types})</h2>
<p>По клику на ID игрока открывается его страничка на турнирном сайте, на имя-фамилию — его статистика на buff, по клику на количество игр — страничка с вашими совместными играми.</p>
<ol>
{lis}
</ol>
"""


def get_suffix(num):
    if str(num).endswith(("11", "12", "13", "14")):
        return ""
    if str(num).endswith(("2", "3", "4")):
        return "ы"
    if str(num).endswith("1"):
        return "а"
    return ""


def is_online_async(tourn: Tournament):
    return tourn.type == "Асинхрон" or "онлайн" in tourn.name.lower()


def tourn_match(tourn: Tournament, tournament_types: str):
    if tournament_types == "lan_sync" and is_online_async(tourn):
        return False
    elif tournament_types == "online_async" and not is_online_async(tourn):
        return False
    return True


def hr_tournament_types(tournament_types):
    if tournament_types == "lan_sync":
        return "очники и синхроны"
    elif tournament_types == "online_async":
        return "онлайны и асинхроны"
    else:
        return "все турниры"


def make_query(player_id, date_from, date_to, tournament_types):
    player_id = tryint(player_id)
    conn = sqlite3.connect(DB_LOC)
    cur = conn.cursor()
    date_from = date_from or "1990-01-01"
    date_to = date_to or datetime.date.today().strftime("%Y-%m-%d")
    query = QUERY_STUB.format(
        player_id=player_id,
        date_from=date_from,
        date_to=date_to,
    )
    logger.debug(query)
    cntr = Counter()
    already_encountered = set()
    for res in cur.execute(query):
        if res[0] in already_encountered:
            continue
        members = json.loads(res[1])
        tourn = Tournament(
            id=res[0], name=res[2], date_start=res[3], date_end=res[4], type=res[5]
        )
        if not tourn_match(tourn, tournament_types):
            continue
        if player_id not in members:
            continue
        already_encountered.add(res[0])
        for p_id in members:
            if p_id == player_id:
                continue
            cntr[p_id] += 1
    mc = cntr.most_common()
    player_ids = [player_id] + [x[0] for x in mc]
    player_query = PLAYER_QUERY_STUB.format(
        player_ids=",".join([str(p) for p in player_ids])
    )
    player_dict = {}
    for res in cur.execute(player_query):
        player_dict[res[0]] = f"{res[1]} {(res[2] or '-')[0]}."
    lis = []
    for tup in mc:
        lis.append(
            f"""<li><a href="https://rating.chgk.info/player/{tup[0]}">{tup[0]}</a>, """
            f"""<a href="/stats?player_id={tup[0]}&date_from={date_from}&date_to={date_to}&tournament_types={tournament_types}">{player_dict[tup[0]]}</a> — """
            f"""<a href="/together?id1={player_id}&id2={tup[0]}&date_from={date_from}&date_to={date_to}&tournament_types={tournament_types}">{tup[1]} игр{get_suffix(tup[1])}</a></li>"""
        )
    lis = "\n".join(lis)
    return RENDERED_CONTENT_STUB.format(
        player_id=player_id,
        player_name=player_dict.get(player_id) or "Игрок не найден",
        date_from=date_from,
        date_to=date_to,
        lis=lis,
        tournament_types=hr_tournament_types(tournament_types),
    )


def get_last_db_update():
    conn = sqlite3.connect(DB_LOC)
    cur = conn.cursor()
    last_update = cur.execute("select max(datetime) from db_updates;").fetchone()
    return last_update[0].split("+")[0].replace("T", " ")


def get_cat(share):
    if share == 0:
        return "coffin"
    if 0 < share <= 0.1:
        return "a"
    if 0.1 < share <= 0.33:
        return "b"
    if 0.33 < share <= 0.66:
        return "c"
    if 0.66 < share <= 0.9:
        return "d"
    if share > 0.9:
        return "e"


def safe_div(a, b):
    if b == 0:
        return 0
    return a / b


def calculate_questions_categs(results):
    t_questions = {}
    total_questions = len(results[0]["mask"])
    teams_taken = defaultdict(list)
    t_rating = Counter()
    t_by_cat = defaultdict(Counter)
    n_teams = len(results)
    questions_by_cat = Counter()
    discarded = None
    for res in results:
        team = (res["team_id"], res["team_current_name"])
        t_questions[team] = sum([1 for v in list(res["mask"]) if v == "1"])
        if discarded is None:
            discarded = sum([1 for v in list(res["mask"]) if v == "X"])
        for i, v in enumerate(list(res["mask"])):
            q_num = i + 1
            if v == "1":
                teams_taken[q_num].append(team)
    for q_ in range(total_questions):
        q = q_ + 1
        teams = teams_taken[q]
        rating = n_teams - len(teams) + 1
        cat = get_cat(len(teams) / n_teams)
        questions_by_cat[cat] += 1
        for t in teams:
            t_rating[t] += rating
            t_by_cat[t][cat] += 1
    pre = (
        f"""Всего вопросов: <abbr title="0 взятых">⚰️</abbr> — {questions_by_cat['coffin']},"""
        f""" <abbr title="<20% взятых">👹</abbr> — {questions_by_cat['a']},"""
        f""" <abbr title="21–40% взятых">😥</abbr> — {questions_by_cat['b']},"""
        f""" <abbr title="41–60% взятых">🙂</abbr> — {questions_by_cat['c']},"""
        f""" <abbr title="61–80% взятых">👶</abbr> — {questions_by_cat['d']},"""
        f""" <abbr title="81–100% взятых">🐣</abbr> — {questions_by_cat['e']}."""
    )
    if discarded:
        pre += f""" Снято: {discarded}."""
    header = ["Команда", "Рейтинг", "Взято", "Ср. рейт", "👹", "😥", "🙂", "👶", "🐣"]
    rows = [header]
    srt = sorted(t_questions, key=lambda x: (t_questions[x], t_rating[x]), reverse=True)
    for t in srt:
        q = t_questions[t]
        r = t_rating[t]
        avg_rating = round(safe_div(r, q), 2)
        row = [
            f'<a href="https://rating.chgk.info/teams/{t[0]}">{t[1]}</a>',
            r,
            q,
            avg_rating,
        ] + [t_by_cat[t][c] for c in ["a", "b", "c", "d", "e"]]
        rows.append(row)
    return rows, pre


def render_table(rows):
    column_widths = {
        0: "39%",  # Team name
        1: "12%",  # Rating
        2: "12%",  # Taken
        3: "12%",  # Avg rating
        4: "5%",  # A
        5: "5%",  # B
        6: "5%",  # C
        7: "5%",  # D
        8: "5%",  # E
    }
    table_style = "width: 100%; table-layout: fixed; border-collapse: collapse;"
    table = f'<table style="{table_style}">'
    for row in rows:
        table += "<tr>"
        for i, cell in enumerate(row):
            width = column_widths.get(i, "auto")
            cell_style = f"width: {width}; padding: clamp(2px, 1vw, 8px);"
            if i == 0:  # Team name column
                cell_style += " font-size: clamp(0.8rem, 2vw, 1rem);"
            else:  # Other columns
                cell_style += " font-size: clamp(0.7rem, 1.5vw, 1rem); white-space: nowrap; text-align: right;"
            table += f'<td style="{cell_style}">{cell}</td>'
        table += "</tr>"

    table += "</table>"
    return table


@app.route("/tournament/<int:tournament_id>")
def tournament(tournament_id):
    conn = sqlite3.connect(DB_LOC)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    tournament = cur.execute(
        "select id, name, date_start from tournaments where id = ?;", (tournament_id,)
    ).fetchone()
    if not tournament:
        flash(f"Турнир {tournament_id} не найден", "red")
        return redirect(url_for(".index"))
    results = cur.execute(
        "select team_id, team_current_name, mask from tournament_results where id = ? and mask is not null;",
        (tournament_id,),
    ).fetchall()
    if not results:
        flash(f"Повопросные результаты для турнира {tournament_id} недоступны", "red")
        return redirect(url_for(".index"))
    rows, pre = calculate_questions_categs(results)
    table = render_table(rows)
    rendered_content = textwrap.dedent(f"""\
    <h1><a href="https://rating.chgk.info/tournament/{tournament["id"]}">{tournament["id"]}</a> {tournament["name"]}</a></h1>
    <p>{tournament["date_start"].split("T")[0]}</p>
    <p>{pre}</p>
    {table}
    """)
    return render_template_string(
        TOURNAMENT_STUB,
        rendered_content=rendered_content,
        last_db_update=get_last_db_update(),
    )


@app.route("/stats", methods=["GET", "POST"])
def stats():
    player_id = request.args.get("player_id")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    tournament_types = request.args.get("tournament_types")
    ok, flashes = validate_stats_args(player_id, date_from, date_to, strict=True)
    if not ok:
        for _flash in flashes:
            flash(*_flash)
        return redirect(url_for(".index"))
    logger.debug(
        f"player_id={type(player_id)} {player_id}, date_from={type(date_from)} {date_from}, date_to={type(date_to)} {date_to}"
    )
    rendered_content = make_query(player_id, date_from, date_to, tournament_types)
    logger.debug(f"rendered_content={type(rendered_content)} {rendered_content}")
    return render_template_string(
        get_html_stub(player_id, date_from, date_to, tournament_types),
        rendered_content=rendered_content,
        last_db_update=get_last_db_update(),
    )


def r_link(player_id):
    return f"https://rating.chgk.info/player/{player_id}"


def a_link(player_id, date_from=None, date_to=None):
    date_from = date_from or "1990-01-01"
    date_to = date_to or datetime.date.today().strftime("%Y-%m-%d")
    return f"/stats?player_id={player_id}&date_from={date_from}&date_to={date_to}"


def format_dates(start, end):
    start = start.split("T")[0]
    end = end.split("T")[0]
    if start == end:
        return start
    return f"{start}–{end}"


def make_together_query(player_id1, player_id2, date_from, date_to, tournament_types):
    player_id1 = tryint(player_id1)
    player_id2 = tryint(player_id2)
    conn = sqlite3.connect(DB_LOC)
    cur = conn.cursor()
    date_from = date_from or "1990-01-01"
    date_to = date_to or datetime.date.today().strftime("%Y-%m-%d")
    query = TOGETHER_QUERY_STUB.format(
        player_id1=player_id1,
        player_id2=player_id2,
        date_from=date_from,
        date_to=date_to,
    )
    logger.debug(query)
    tourns = []
    already_encountered = set()
    for res in cur.execute(query):
        if res[0] in already_encountered:
            continue
        members = json.loads(res[1])
        if player_id1 not in members or player_id2 not in members:
            continue
        already_encountered.add(res[0])
        tourn = Tournament(
            id=res[0], name=res[2], date_start=res[3], date_end=res[4], type=res[5]
        )
        if not tourn_match(tourn, tournament_types):
            continue
        tourns.append(tourn)
    player_query = PLAYER_QUERY_STUB.format(
        player_ids=",".join([str(player_id1), str(player_id2)])
    )
    player_dict = {}
    for res in cur.execute(player_query):
        player_dict[res[0]] = f"{res[1]} {(res[2] or '-')[0]}."

    def name(player_id):
        return player_dict.get(player_id) or "Игрок не найден"

    def a_link(player_id):
        return f"/stats?player_id={player_id}&date_from={date_from}&date_to={date_to}&tournament_types={tournament_types}"

    result = [
        f"""<h2>Совместные игры игроков <a href="{r_link(player_id1)}">{player_id1}</a>, <a href="{a_link(player_id1)}">{name(player_id1)}</a> и <a href="{r_link(player_id2)}">{player_id2}</a>, <a href="{a_link(player_id2)}">{name(player_id2)}</a> с {date_from} по {date_to} ({hr_tournament_types(tournament_types)})</h2>""",
        "<ol>",
    ]
    for tourn in sorted(tourns, key=lambda t: (t.date_start, t.name)):
        result.append(
            f"""<li><a href="https://rating.chgk.info/tournament/{tourn.id}">{tourn.id}</a> <a href="/tournament/{tourn.id}">{tourn.name}</a> <small>({format_dates(tourn.date_start, tourn.date_end)}, {tourn.type})</small></li>"""
        )
    result.append("</ol>")
    return "\n".join(result)


@app.route("/together", methods=["GET"])
def together():
    player_id1 = request.args.get("id1")
    player_id2 = request.args.get("id2")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    tournament_types = request.args.get("tournament_types")
    if tournament_types not in ("lan_sync", "online_async", "all_tournaments"):
        tournament_types = "all_tournaments"
    ok1, flashes1 = validate_stats_args(player_id1, date_from, date_to, strict=False)
    ok2, flashes2 = validate_stats_args(player_id2, date_from, date_to, strict=False)
    flashes = sorted(set(flashes1) | set(flashes2))
    if not ok1 or not ok2:
        for _flash in flashes:
            flash(*_flash)
        return redirect(url_for(".index"))
    rendered_content = make_together_query(
        player_id1, player_id2, date_from, date_to, tournament_types
    )
    return render_template_string(
        get_html_stub(player_id1, date_from, date_to, tournament_types),
        rendered_content=rendered_content,
        last_db_update=get_last_db_update(),
    )


def validate_stats_args(player_id, date_from, date_to, strict=False):
    ok = True
    flashes = []
    logger.debug(
        f"trying tryint with arg {type(player_id)} {player_id} -> {tryint(player_id)}"
    )
    player_id = tryint(player_id)
    if not player_id:
        flashes.append(("Нужно ввести валидный id игрока", "red"))
        ok = False
    if not (try_parse_date(date_from) or (not strict and not date_from)):
        flashes.append(
            (
                "Введённая стартовая дата невалидна, правильный формат — 1990-01-01",
                "red",
            )
        )
        ok = False
    if not (try_parse_date(date_to) or (not strict and not date_to)):
        flashes.append(
            ("Введённая конечная дата невалидна, правильный формат — 2024-01-01", "red")
        )
        ok = False
    return (ok, flashes)


@app.route("/handshakes", methods=["GET", "POST"])
@debug_only
def handshakes():
    if request.method == "GET":
        return render_template_string(HANDSHAKES_STUB, rendered_content="")
    form_content = request.form.to_dict()
    player_id1 = tryint(form_content.get("player_id1"))
    player_id2 = tryint(form_content.get("player_id2"))
    if not player_id1 or not player_id2:
        flash("Оба ID должны быть валидны", "red")
        return render_template_string(
            HANDSHAKES_STUB,
            rendered_content="",
            player_id1=player_id1,
            player_id2=player_id2,
        )
    if not gc.g:
        gc.load_graph(GRAPH_PATH)
    if not gc.g.has_node(player_id1) or not gc.g.has_node(player_id2):
        flash("Игрок не найден", "red")
        return render_template_string(
            HANDSHAKES_STUB,
            rendered_content="",
            player_id1=player_id1,
            player_id2=player_id2,
        )
    try:
        shortest_path = nx.shortest_path(gc.g, tryint(player_id1), tryint(player_id2))
    except nx.NetworkXNoPath:
        shortest_path = []
    player_dict = {}
    conn = sqlite3.connect(DB_LOC)
    cur = conn.cursor()
    player_query = PLAYER_QUERY_STUB.format(
        player_ids=",".join(
            sorted(map(str, set(shortest_path) | set([player_id1, player_id2])))
        )
    )
    for res in cur.execute(player_query):
        player_dict[res[0]] = f"{res[1]} {(res[2] or '-')[0]}."

    def name(p_id):
        return player_dict.get(p_id) or "Игрок не найден"

    result = [
        f"""<h2>Кратчайший путь между игроками <a href="{r_link(player_id1)}">{player_id1}</a>, <a href="{a_link(player_id1)}">{name(player_id1)}</a> и <a href="{r_link(player_id2)}">{player_id2}</a>, <a href="{a_link(player_id2)}">{name(player_id2)}</a></h2>"""
    ]
    if len(shortest_path) == 2:
        result.append("Игроки соединены напрямую")
    elif shortest_path:
        without_players = shortest_path[1:-1]
        result.append("<ol>")
        for pl in without_players:
            result.append(
                f"""<li><a href="{r_link(pl)}">{pl}</a>, <a href="{a_link(pl)}">{name(pl)}</a></li>"""
            )
        result.append("</ol>")
    else:
        result.append("Путь не найден")
    return render_template_string(
        HANDSHAKES_STUB,
        rendered_content="\n".join(result),
        player_id1=player_id1,
        player_id2=player_id2,
    )


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(
            get_html_stub(), rendered_content="", last_db_update=get_last_db_update()
        )
    form_content = request.form.to_dict()
    print(form_content)
    player_id = tryint(form_content.get("player_id"))
    date_from = form_content.get("date_from")
    date_to = form_content.get("date_to")
    ok, flashes = validate_stats_args(player_id, date_from, date_to, strict=False)
    if not ok:
        for _flash in flashes:
            flash(*_flash)
        return redirect(url_for(".index"))
    return redirect(
        url_for(
            ".stats",
            player_id=player_id,
            date_from=date_from or "1990-01-01",
            date_to=date_to or datetime.date.today().strftime("%Y-%m-%d"),
            tournament_types=form_content.get("tournament_types"),
        )
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5678, debug=True)
