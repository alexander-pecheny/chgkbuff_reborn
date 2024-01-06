#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sqlite3
import datetime
from collections import Counter
import json

from flask import Flask, flash, render_template_string, request, redirect, url_for
from config import Config

app = Flask("Buff", static_folder="static")
app.config.from_object(Config)
DIR = os.path.dirname(os.path.abspath(__file__))
DB_LOC = os.path.join(DIR, "buff.db")

HTML_STUB = """\
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
<h1>С кем вы играли чаще всего?</h1>
<form action="{{ url_for('.index') }}" method="post">
<label for="player_id">Ваш ID</label><input name="player_id" placeholder="12345"></input>
<label for="date_from">С даты</label><input name="date_from" placeholder="1990-02-28"></input>
<label for="date_from">По дату</label><input name="date_from" placeholder="2024-02-28"></input>
<input type="submit" value="Рассчитать"></input>
</form>
{{ rendered_content|safe }}
</body>
"""


def tryint(x):
    try:
        return int(x.strip())
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
    where date_start > '{date_from}' and date_start < '{date_to}'
)
select
    r.*
from results as r
inner join right_tournaments as r1 on (r.tournament_id = r1.tournament_id);
"""

PLAYER_QUERY_STUB = """\
select id, name, surname from players where id in ({player_ids});
"""

RENDERED_CONTENT_STUB = """\
<h2>Статистика игрока <a href="https://rating.chgk.info/player/{player_id}">{player_id} {player_name}</a> с {date_from} по {date_to}</h2>
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
    conn = sqlite3.connect(DB_LOC)
    cur = conn.cursor()
    date_from = date_from or "1990-01-01"
    date_to = date_to or datetime.date.today().strftime("%Y-%m-%d")
    query = QUERY_STUB.format(
        player_id=player_id,
        date_from=date_from,
        date_to=date_to,
    )
    cntr = Counter()
    for res in cur.execute(query):
        members = json.loads(res[1])
        if player_id not in members:
            continue
        for p_id in members:
            if p_id == player_id:
                continue
            cntr[p_id] += 1
    mc = []
    prev_tup = None
    for tup in cntr.most_common():
        if prev_tup and len(mc) >= 50 and tup[1] != prev_tup[1]:
            break
        mc.append(tup)
        prev_tup = tup
    player_ids = [player_id] + [x[0] for x in mc]
    player_query = PLAYER_QUERY_STUB.format(
        player_ids=",".join([str(p) for p in player_ids])  
    )
    player_dict = {}
    for res in cur.execute(player_query):
        player_dict[res[0]] = f"{res[1]} {(res[2] or '-')[0]}."
    lis = []
    for tup in mc:
        lis.append(f"""<li><a href="https://rating.chgk.info/player/{tup[0]}">{tup[0]} {player_dict[tup[0]]}</a> — {tup[1]} игр{get_suffix(tup[1])}</li>""")
    lis = "\n".join(lis)
    return RENDERED_CONTENT_STUB.format(
        player_id=player_id,
        player_name=player_dict.get(player_id) or "Игрок не найден",
        date_from=date_from,
        date_to=date_to,
        lis=lis,
    )




@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(HTML_STUB, rendered_content="")
    form_content = request.form.to_dict()
    print(form_content)
    player_id = tryint(form_content.get("player_id"))
    ok = True
    if not player_id:
        flash("Нужно ввести валидный id игрока", "red")
        ok = False 
    date_from = form_content.get("date_from")
    if date_from and not try_parse_date(date_from):
        flash("Введённая стартовая дата невалидна, правильный формат — 1990-01-01")
        ok = False
    date_to = form_content.get("date_to")
    if date_to and not try_parse_date(date_to):
        flash("Введённая конечная дата невалидна, правильный формат — 2023-01-01")
        ok = False
    if not ok:
        return redirect(url_for(".index"))
    rendered_content = make_query(player_id, date_from, date_to)
    return render_template_string(HTML_STUB, rendered_content=rendered_content)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5678, debug=True)
