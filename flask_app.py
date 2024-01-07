#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import json
import logging
import logging.handlers
import os
import sqlite3
from collections import Counter

import networkx as nx
import dill

from config import Config
from flask import Flask, flash, redirect, render_template_string, request, url_for

UTC_PLUS_3 = datetime.timezone(datetime.timedelta(seconds=10800))
DIR = os.path.dirname(os.path.abspath(__file__))
DB_LOC = os.path.join(DIR, "buff.db")
GRAPH_PATH = os.path.join(DIR, "graph.pickle")


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
<label for="player_id">Ваш ID</label><input name="player_id" placeholder="12345"></input>
<label for="date_from">С даты</label><input name="date_from" placeholder="1990-02-28"></input>
<label for="date_to">По дату</label><input name="date_to" placeholder="2024-02-28"></input>
<input type="submit" value="Рассчитать"></input>
</form>
{{ rendered_content|safe }}
<p>См. также: <a href="{{ url_for('.handshakes') }}">N рукопожатий</a>.</p>
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
    from tournament_results
    where team_members like '%{player_id}%'
), right_tournaments as (
    select
        id as tournament_id
    from tournaments
    where date_start > '{date_from}' and date_start < '{date_to}' and tournament_type != 'Общий зачёт'
)
select
    r.*
from results as r
inner join right_tournaments as r1 on (r.tournament_id = r1.tournament_id);
"""

TOGETHER_QUERY_STUB = """\
with results as (
    select
        id as tournament_id,
        team_members
    from tournament_results
    where team_members like '%{player_id1}%' and team_members like '%{player_id2}%'
), right_tournaments as (
    select
        id as tournament_id,
        name as tournament_name
    from tournaments
    where date_start > '{date_from}' and date_start < '{date_to}' and tournament_type != 'Общий зачёт'
)
select
    r.*, tournament_name
from results as r
inner join right_tournaments as r1 on (r.tournament_id = r1.tournament_id);
"""

PLAYER_QUERY_STUB = """\
select id, name, surname from players where id in ({player_ids});
"""

RENDERED_CONTENT_STUB = """\
<h2>Статистика игрока <a href="https://rating.chgk.info/player/{player_id}">{player_id}</a>, <a href="/stats?player_id={player_id}&date_from={date_from}&date_to={date_to}">{player_name}</a> с {date_from} по {date_to}</h2>
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


def make_query(player_id, date_from, date_to):
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
    for res in cur.execute(query):
        members = json.loads(res[1])
        if player_id not in members:
            continue
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
            f"""<li><a href="https://rating.chgk.info/player/{tup[0]}">{tup[0]}</a>, <a href="/stats?player_id={tup[0]}&date_from={date_from}&date_to={date_to}">{player_dict[tup[0]]}</a> — <a href="/together?id1={player_id}&id2={tup[0]}&date_from={date_from}&date_to={date_to}">{tup[1]} игр{get_suffix(tup[1])}</a></li>"""
        )
    lis = "\n".join(lis)
    return RENDERED_CONTENT_STUB.format(
        player_id=player_id,
        player_name=player_dict.get(player_id) or "Игрок не найден",
        date_from=date_from,
        date_to=date_to,
        lis=lis,
    )


@app.route("/stats", methods=["GET", "POST"])
def stats():
    player_id = request.args.get("player_id")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    ok, flashes = validate_stats_args(player_id, date_from, date_to, strict=True)
    if not ok:
        for _flash in flashes:
            flash(*_flash)
        return redirect(url_for(".index"))
    logger.debug(
        f"player_id={type(player_id)} {player_id}, date_from={type(date_from)} {date_from}, date_to={type(date_to)} {date_to}"
    )
    rendered_content = make_query(player_id, date_from, date_to)
    logger.debug(f"rendered_content={type(rendered_content)} {rendered_content}")
    return render_template_string(HTML_STUB, rendered_content=rendered_content)


def r_link(player_id):
    return f"https://rating.chgk.info/player/{player_id}"


def a_link(player_id, date_from=None, date_to=None):
    date_from = date_from or "1990-01-01"
    date_to = date_to or datetime.date.today().strftime("%Y-%m-%d")
    return f"/stats?player_id={player_id}&date_from={date_from}&date_to={date_to}"


def make_together_query(player_id1, player_id2, date_from, date_to):
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
    for res in cur.execute(query):
        members = json.loads(res[1])
        if player_id1 not in members or player_id2 not in members:
            continue
        tourns.append((res[0], res[2]))
    player_query = PLAYER_QUERY_STUB.format(
        player_ids=",".join([str(player_id1), str(player_id2)])
    )
    player_dict = {}
    for res in cur.execute(player_query):
        player_dict[res[0]] = f"{res[1]} {(res[2] or '-')[0]}."

    def name(player_id):
        return player_dict.get(player_id) or "Игрок не найден"

    def a_link(player_id):
        return f"/stats?player_id={player_id}&date_from={date_from}&date_to={date_to}"

    result = [
        f"""<h2>Совместные игры игроков <a href="{r_link(player_id1)}">{player_id1}</a>, <a href="{a_link(player_id1)}">{name(player_id1)}</a> и <a href="{r_link(player_id2)}">{player_id2}</a>, <a href="{a_link(player_id2)}">{name(player_id2)}</a> с {date_from} по {date_to}</h2>""",
        "<ol>",
    ]
    for tourn in tourns:
        result.append(
            f"""<li><a href="https://rating.chgk.info/tournament/{tourn[0]}">{tourn[0]} {tourn[1]}</a></li>"""
        )
    result.append("</ol>")
    return "\n".join(result)


@app.route("/together", methods=["GET"])
def together():
    player_id1 = request.args.get("id1")
    player_id2 = request.args.get("id2")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    ok1, flashes1 = validate_stats_args(player_id1, date_from, date_to, strict=False)
    ok2, flashes2 = validate_stats_args(player_id2, date_from, date_to, strict=False)
    flashes = sorted(set(flashes1) | set(flashes2))
    if not ok1 or not ok2:
        for _flash in flashes:
            flash(*_flash)
        return redirect(url_for(".index"))
    rendered_content = make_together_query(player_id1, player_id2, date_from, date_to)
    return render_template_string(HTML_STUB, rendered_content=rendered_content)


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
        return render_template_string(HTML_STUB, rendered_content="")
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
        )
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5678, debug=True)
