#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import argparse
import copy
import sqlite3
import time
import datetime
import logging
import logging.handlers
import json

import requests
import dill
from create_graph import create_graph

UTC_PLUS_3 = datetime.timezone(datetime.timedelta(seconds=10800))
DIR = os.path.dirname(os.path.abspath(__file__))

API = "https://api.rating.chgk.net"

DB_INIT = """\
CREATE TABLE IF NOT EXISTS tournaments (
    id integer PRIMARY KEY,
    name text,
    long_name text,
    last_edit_date text,
    date_start text,
    date_end text,
    tournament_type,
    orgcommittee text,
    editors text,
    game_jury text,
    appeal_jury text,
    town_id integer,
    in_rating integer,
    maii_aegis integer,
    maii_rating integer
);
CREATE TABLE IF NOT EXISTS tournament_results (
    id integer,
    team_id integer,
    team_current_name text,
    team_current_town integer,
    team_members text,
    team_members_full text,
    position real,
    questions_total text,
    mask text,
    controversials text,
    flags text,
    rating text
);
CREATE TABLE IF NOT EXISTS players (
    id integer PRIMARY KEY,
    name text,
    patronymic text,
    surname text
);
CREATE TABLE IF NOT EXISTS db_updates (
    datetime text
);
"""


def db_init(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    for st in DB_INIT.split(";"):
        if st.strip():
            cur.execute(st.strip() + ";")
    conn.commit()


DT_FORMAT_STRING = "%Y-%m-%dT%H:%M:%S%z"


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


def parse_datetime(s):
    return datetime.datetime.strptime(s, DT_FORMAT_STRING)


def req_sleep(*args, **kwargs):
    """
    rating.chgk.info's API has rate limit of 2 requests per second
    """
    req = getattr(requests, args[0])(*args[1:], **kwargs)
    time.sleep(0.5)
    return req


class DbUpdater:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        dir_name = os.path.dirname(db_path)
        log_path = os.path.join(dir_name, "db_updater.log")
        formatter = Formatter("%(asctime)s %(message)s")
        fileHandler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=1024 * 1024 * 16,
            backupCount=1,
        )
        fileHandler.setFormatter(formatter)
        consoleHandler = logging.StreamHandler()
        consoleHandler.setFormatter(formatter)
        logger = logging.getLogger("buff_db_updater")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(consoleHandler)
        logger.addHandler(fileHandler)
        self.logger = logger
        self.last_db_update = self.get_last_db_update()

    def req_tournaments(self, page=1):
        self.logger.debug(f"processing tournaments page {page}...")
        req = req_sleep(
            "get", f"{API}/tournaments.json", params={"page": page, "itemsPerPage": 100}
        )
        try:
            return req.json()
        except Exception as e:
            self.logger.error(
                f"error {type(e)} {e} while trying to parse "
                f"{req.content.decode('utf8', errors='replace')}"
            )

    def req_tournament(self, tournament_id):
        self.logger.debug(f"processing tournament {tournament_id}...")
        req = req_sleep("get", f"{API}/tournaments/{tournament_id}.json")
        try:
            return req.json()
        except Exception as e:
            self.logger.error(
                f"error {type(e)} {e} while trying to parse "
                f"{req.content.decode('utf8', errors='replace')}"
            )

    def wrap_sql_value(self, val):
        if val is None:
            return "NULL"
        return repr(val)

    def insert_wrapper(self, dct, table_name):
        cur = self.conn.cursor()
        ks = sorted(dct.keys())
        values = tuple(dct[k] for k in ks)
        query = (
            f"insert into {table_name}({','.join(ks)}) "
            f"values ({','.join(['?'] * len(values))});"
        )
        cur.execute(query, values)
        self.conn.commit()

    def update_player(self, dct):
        player_dict = {
            "id": dct["id"],
            "name": dct["name"],
            "surname": dct["surname"],
            "patronymic": dct["patronymic"],
        }
        cur = self.conn.cursor()
        current_rec = cur.execute(
            f"select id,name,surname,patronymic from players where id = {dct['id']};"
        ).fetchone()
        if current_rec:
            updates = []
            current_rec_parsed = dict(
                zip(["id", "name", "surname", "patronymic"], current_rec)
            )
            for key in ["name", "surname", "patronymic"]:
                if current_rec_parsed[key] != player_dict[key]:
                    updates.append(f"{key} = {repr(player_dict[key])}")
            if updates:
                cur.execute(
                    f"update players set {','.join(updates)} where id = {dct['id']};"
                )
                self.conn.commit()
        else:
            self.insert_wrapper(player_dict, "players")

    def update_tournament_data(self, tournament_id):
        tourn_info = self.req_tournament(tournament_id)
        if not tourn_info:
            return
        cur = self.conn.cursor()
        cur.execute(f"delete from tournaments where id = {tournament_id};")
        self.conn.commit()
        try:
            tourn_dict = {
                "id": tournament_id,
                "name": tourn_info["name"],
                "long_name": tourn_info["longName"],
                "last_edit_date": tourn_info["lastEditDate"],
                "date_start": tourn_info["dateStart"],
                "date_end": tourn_info["dateEnd"],
                "tournament_type": tourn_info["type"]["name"],
                "orgcommittee": json.dumps(
                    [x["id"] for x in tourn_info["orgcommittee"]]
                ),
                "editors": json.dumps([x["id"] for x in tourn_info["editors"]]),
                "game_jury": json.dumps([x["id"] for x in tourn_info["gameJury"]]),
                "appeal_jury": json.dumps([x["id"] for x in tourn_info["appealJury"]]),
                "town_id": tourn_info.get("idtown"),
                "in_rating": int(tourn_info["tournamentInRatingBalanced"]),
                "maii_aegis": int(tourn_info["maiiAegis"]),
                "maii_rating": int(tourn_info["maiiRating"]),
            }
            self.insert_wrapper(tourn_dict, "tournaments")
            return "ok"
        except Exception as e:
            self.logger.error(
                f"couldn't update tournament data for {tournament_id}: {type(e)} {e}"
            )
        for key in ("orgcommittee", "editors", "gameJury", "appealJury"):
            for person in tourn_info[key]:
                self.update_player(person)

    def req_results(self, tournament_id):
        self.logger.debug(f"processing results of {tournament_id}...")
        req = req_sleep(
            "get",
            f"{API}/tournaments/{tournament_id}/results.json",
            params={
                "includeTeamMembers": 1,
                "includeMasksAndControversials": 1,
                "includeTeamFlags": 1,
                "includeRatingB": 1,
            },
        )
        try:
            return req.json()
        except Exception as e:
            self.logger.error(
                f"error {type(e)} {e} while trying to parse "
                f"{req.content.decode('utf8', errors='replace')}"
            )

    def wrap_town(self, town):
        if town:
            return town["name"]

    def update_results(self, tournament_id):
        results = self.req_results(tournament_id)
        if not results:
            return
        cur = self.conn.cursor()
        cur.execute(f"delete from tournament_results where id = {tournament_id};")
        self.conn.commit()
        for res in results:
            tm = copy.deepcopy(res["teamMembers"])
            for t in tm:
                t.pop("player")
            result_dict = {
                "id": tournament_id,
                "team_id": res["team"]["id"],
                "team_current_name": res["current"]["name"],
                "team_current_town": self.wrap_town(res["current"]["town"]),
                "team_members": json.dumps(
                    [x["player"]["id"] for x in res["teamMembers"]]
                ),
                "team_members_full": json.dumps(tm),
                "position": res.get("position"),
                "questions_total": res.get("questionsTotal"),
                "mask": res.get("mask"),
                "controversials": json.dumps(
                    res.get("controversials"), ensure_ascii=False
                ),
                "flags": json.dumps(res.get("flags"), ensure_ascii=False),
                "rating": json.dumps(res.get("rating"), ensure_ascii=False),
            }
            self.insert_wrapper(result_dict, "tournament_results")
            for player in res["teamMembers"]:
                self.update_player(player["player"])
        return "ok"

    def process_tournaments_batch(self, res):
        for tournament in res:
            if parse_datetime(tournament["lastEditDate"]) > self.last_db_update:
                try:
                    self.update_tournament_data(tournament["id"])
                except Exception as e:
                    self.logger.error(
                        f"exception {type(e)} {e} while trying to update tournament data for {tournament['id']}"
                    )
                try:
                    self.update_results(tournament["id"])
                except Exception as e:
                    self.logger.error(
                        f"exception {type(e)} {e} while trying to update tournament results for {tournament['id']}"
                    )

    def get_last_db_update(self):
        cur = self.conn.cursor()
        last_date = cur.execute("select max(datetime) from db_updates;").fetchone()
        if last_date[0]:
            return parse_datetime(last_date[0])
        else:
            return parse_datetime("1970-01-01T00:00:00+03:00")

    def update(self):
        start = datetime.datetime.now(UTC_PLUS_3)
        page = 1
        res = self.req_tournaments(page=1)
        self.process_tournaments_batch(res)
        while len(res) == 100:
            page += 1
            res = self.req_tournaments(page=page)
            self.process_tournaments_batch(res)
        self.insert_wrapper(
            {"datetime": start.strftime(DT_FORMAT_STRING)}, "db_updates"
        )
        self.logger.info("update finished successfully!")
        self.logger.info("now creating graph...")
        cur = self.conn.cursor()
        graph = create_graph(cur)
        with open(os.path.join(DIR, "graph.pickle"), "wb") as f:
            dill.dump(graph, f)
        self.logger.info("graph dumped!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="buff.db")
    args = parser.parse_args()

    if os.path.isabs(args.db):
        db_path = args.db
    else:
        db_path = os.path.abspath(os.path.join(DIR, args.db))
    db_init(db_path)
    DbUpdater(db_path).update()


if __name__ == "__main__":
    main()
