#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import copy
import datetime
import json
import logging
import logging.handlers
import os
import sqlite3
import time

import requests

# from create_graph import create_graph

UTC_PLUS_3 = datetime.timezone(datetime.timedelta(seconds=10800))
DIR = os.path.dirname(os.path.abspath(__file__))

API = "https://api.rating.chgk.net"
RATING_API = "https://rating.chgk.gg/api/v1/b"

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
    maii_rating integer,
    questions_by_tour text
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
CREATE INDEX IF NOT EXISTS idx_tournament_results_id ON tournament_results(id);
CREATE TABLE IF NOT EXISTS players (
    id integer PRIMARY KEY,
    name text,
    patronymic text,
    surname text
);
CREATE TABLE IF NOT EXISTS db_updates (
    datetime text
);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id, team_id, team_members);
CREATE TABLE IF NOT EXISTS releases (
    id integer PRIMARY KEY,
    date text,
    updated_at text,
    q real
);
CREATE TABLE IF NOT EXISTS ratings (
    release_id integer,
    team_id integer,
    place real,
    rating integer,
    trb integer
);
CREATE INDEX IF NOT EXISTS idx_ratings ON ratings(release_id, team_id);
"""


DELETED_TOURNAMENTS = """
with dtrns as (
    select
        s.id as id,
        s.name as name,
        t.id as t_id
    from tournaments as s
    left join tournament_ids as t on s.id = t.id
)
select id, name from dtrns where t_id is null;
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


def sqlite_repr(s):
    if s is None:
        return "null"
    return repr(s)


def parse_date(s):
    return datetime.datetime.strptime(s.split("T")[0], "%Y-%m-%d").date()


def get_releases():
    releases = requests.get(f"{RATING_API}/releases.json").json()
    time.sleep(0.5)
    return releases["items"]


def download_release(release_id):
    result = []
    page = 1
    while True:
        print(f"downloading page {page} of release {release_id}...")
        ratings = requests.get(
            f"{RATING_API}/teams/{release_id}.json?page={page}"
        ).json()
        time.sleep(0.5)
        result.extend(ratings["items"])
        if ratings["current_page"] >= ratings["pages"]:
            break
        else:
            page += 1
    return result


class DbUpdater:
    def __init__(self, db_path, args):
        self.db_path = db_path
        self.args = args
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
        self.session = requests.Session()
        self.last_db_update = self.get_last_db_update()
        self.items_per_page = 100
        self.page = 1
        self.page_thresh = None
        self.updated_tournament_ids = set()

    def req_sleep(self, *args, **kwargs):
        """
        rating.chgk.info's API has rate limit of 2 requests per second
        """
        req = getattr(self.session, args[0])(*args[1:], **kwargs)
        time.sleep(0.5)
        return req

    def req_tournaments(self):
        if self.page_thresh is not None and self.page == self.page_thresh:
            self.page -= 1
            self.items_per_page = 100
            self.page //= 10
            self.page += 1
            self.page_thresh = None
        self.logger.debug(
            f"processing tournaments page {self.page} (ipp={self.items_per_page})..."
        )
        req = self.req_sleep(
            "get",
            f"{API}/tournaments.json",
            params={"page": self.page, "itemsPerPage": self.items_per_page},
        )
        if req.status_code == 500 and self.items_per_page == 100:
            self.page -= 1
            self.items_per_page = 10
            self.page *= 10
            self.page += 1
            req = self.req_sleep(
                "get",
                f"{API}/tournaments.json",
                params={"page": self.page, "itemsPerPage": self.items_per_page},
            )
            self.page_thresh = self.page + 10
        if req.status_code == 500:
            raise Exception(f"status 500 while try to get {req.url}")
        self.page += 1
        try:
            return req.json()
        except Exception as e:
            self.logger.error(
                f"error {type(e)} {e} while trying to parse "
                f"{req.content.decode('utf8', errors='replace')}"
            )

    def req_tournament(self, tournament_id):
        self.logger.debug(f"processing tournament {tournament_id}...")
        req = self.req_sleep("get", f"{API}/tournaments/{tournament_id}.json")
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
                    updates.append(f"{key} = {sqlite_repr(player_dict[key])}")
            if updates:
                cur.execute(
                    f"update players set {','.join(updates)} where id = {dct['id']};"
                )
                self.conn.commit()
        else:
            self.insert_wrapper(player_dict, "players")

    @classmethod
    def get_questions_by_tour(cls, tourn_info):
        qty = tourn_info.get("questionQty")
        if not qty:
            return
        return ",".join([str(v) for v in qty.values()])

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
                "maii_rating": int(tourn_info["maiiRating"]),
                "questions_by_tour": self.get_questions_by_tour(tourn_info),
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
        req = self.req_sleep(
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
        cur.execute(f"delete from search where id = {tournament_id};")
        self.conn.commit()
        for res in results:
            tm = copy.deepcopy(res["teamMembers"])
            for t in tm:
                t.pop("player")
            team_members_short = json.dumps(
                [x["player"]["id"] for x in res["teamMembers"]]
            )
            result_dict = {
                "id": tournament_id,
                "team_id": res["team"]["id"],
                "team_current_name": res["current"]["name"],
                "team_current_town": self.wrap_town(res["current"]["town"]),
                "team_members": team_members_short,
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
            self.insert_wrapper(
                {"id": tournament_id, "team_members": team_members_short}, "search"
            )
            for player in res["teamMembers"]:
                self.update_player(player["player"])
        return "ok"

    def process_tournaments_batch(self, res):
        for tournament in res:
            self.insert_wrapper({"id": tournament["id"]}, "tournament_ids")
            if parse_datetime(tournament["lastEditDate"]) > self.last_db_update:
                self.update_tournament_full(tournament["id"])

    def get_last_db_update(self):
        cur = self.conn.cursor()
        last_date = cur.execute("select max(datetime) from db_updates;").fetchone()
        if last_date[0]:
            return parse_datetime(last_date[0])
        else:
            return parse_datetime("1970-01-01T00:00:00+03:00")

    def init_tourn_id_table(self):
        cur = self.conn.cursor()
        try:
            cur.execute("drop table tournament_ids;")
        except sqlite3.OperationalError:
            print("couldn't delete tournament_ids table as it doesn't exist")
        cur.execute("create table tournament_ids(id integer primary key);")
        self.conn.commit()

    def handle_deleted_tournaments(self):
        cur = self.conn.cursor()
        deleted_tournaments = cur.execute(DELETED_TOURNAMENTS).fetchall()
        for tup in deleted_tournaments:
            cur.execute(f"delete from tournaments where id = {tup[0]};")
            cur.execute(f"delete from tournament_results where id = {tup[0]};")
            cur.execute(f"delete from search where id = {tup[0]};")
            print(f"Удалён турнир {tup[0]} {tup[1]}")
        cur.execute("drop table tournament_ids;")
        self.conn.commit()

    def get_releases_in_db(self):
        cur = self.conn.cursor()
        releases = cur.execute("select id, updated_at from releases;").fetchall()
        return {r[0]: r[1] for r in releases}

    def update_release(self, release_dict):
        release_id = release_dict["id"]
        print(f"updating release {release_id}...")
        ratings = download_release(release_id)
        cur = self.conn.cursor()
        cur.execute(f"delete from releases where id = {release_id};")
        cur.execute(f"delete from ratings where release_id = {release_id};")
        self.conn.commit()
        self.insert_wrapper(
            {
                "id": release_id,
                "date": release_dict["date"],
                "updated_at": release_dict["updated_at"],
                "q": release_dict["q"],
            },
            "releases",
        )
        for item in ratings:
            self.insert_wrapper(
                {
                    "release_id": release_id,
                    "team_id": item["team_id"],
                    "place": item["place"],
                    "rating": item["rating"],
                    "trb": item["trb"],
                },
                "ratings",
            )

    def update_ratings(self):
        print("updating ratings...")
        releases = get_releases()
        for release in releases:
            releases_in_db = self.get_releases_in_db()
            if (
                release["id"] not in releases_in_db
                or release["updated_at"] > releases_in_db[release["id"]]
            ):
                self.update_release(release)

    def update_tournament_full(self, tournament_id):
        try:
            self.update_tournament_data(tournament_id)
        except Exception as e:
            self.logger.error(
                f"exception {type(e)} {e} while trying to update tournament data for {tournament_id}"
            )
        try:
            self.update_results(tournament_id)
        except Exception as e:
            self.logger.error(
                f"exception {type(e)} {e} while trying to update tournament results for {tournament_id}"
            )
        self.updated_tournament_ids.add(tournament_id)

    def update_tournaments_last_month(self):
        cur = self.conn.cursor()
        now = datetime.datetime.now(UTC_PLUS_3).date()
        if now.weekday() == 0:
            delta = 90  # on mondays we update for the last 3 months
        else:
            delta = 30
        threshold = now - datetime.timedelta(days=delta)
        tournaments = cur.execute(
            "select id from tournaments where date_start >= ? and date_start < ?;",
            (threshold, now),
        ).fetchall()
        for tournament in tournaments:
            if tournament[0] not in self.updated_tournament_ids:
                self.update_tournament_full(tournament[0])

    def update(self):
        start = datetime.datetime.now(UTC_PLUS_3)
        if self.args.tourn_ids:
            for t_id_ in self.args.tourn_ids.split(","):
                t_id = int(t_id_.strip())
                self.logger.info(f"updating data for {t_id}...")
                self.update_tournament_full(t_id)
        else:
            self.init_tourn_id_table()
            res = self.req_tournaments()
            self.process_tournaments_batch(res)
            while len(res) == self.items_per_page:
                res = self.req_tournaments()
                self.process_tournaments_batch(res)
            self.handle_deleted_tournaments()
            self.update_tournaments_last_month()
            self.update_ratings()
            self.insert_wrapper(
                {"datetime": start.strftime(DT_FORMAT_STRING)}, "db_updates"
            )
            self.logger.info("update finished successfully!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="buff.db")
    parser.add_argument("--tourn_ids")
    args = parser.parse_args()

    if os.path.isabs(args.db):
        db_path = args.db
    else:
        db_path = os.path.abspath(os.path.join(DIR, args.db))
    db_init(db_path)
    DbUpdater(db_path, args).update()


if __name__ == "__main__":
    main()
