#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import collections
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
    questions_by_tour text,
    hide_questions_to text
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
CREATE INDEX IF NOT EXISTS idx_tournaments_date_start ON tournaments(date_start DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_tournaments_date_end ON tournaments(date_end);
CREATE INDEX IF NOT EXISTS idx_results_with_mask ON tournament_results(id) WHERE mask IS NOT NULL;
CREATE TABLE IF NOT EXISTS seasons (
    id integer PRIMARY KEY,
    date_start text,
    date_end text
);
CREATE TABLE IF NOT EXISTS team_seasons (
    team_id integer,
    season_id integer,
    player_id integer,
    date_added text,
    date_removed text,
    player_number integer,
    PRIMARY KEY (team_id, season_id, player_id)
);
CREATE TABLE IF NOT EXISTS player_games (
    player_id integer PRIMARY KEY,
    games integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tournament_requests (
    tournament_id integer PRIMARY KEY,
    teams integer NOT NULL DEFAULT 0,
    venues integer NOT NULL DEFAULT 0,
    updated_at text
);
CREATE INDEX IF NOT EXISTS idx_players_surname ON players(surname);
CREATE INDEX IF NOT EXISTS idx_tournament_results_team ON tournament_results(team_id);
-- COLLATE NOCASE because SQLite's LIKE folds ASCII case, so a BINARY index is
-- no use to `name LIKE 'prefix%'` and the team suggest scanned all 941k rows.
CREATE INDEX IF NOT EXISTS idx_results_team_name ON tournament_results(team_current_name COLLATE NOCASE);
-- One row per team, carrying the name and town it played its latest tournament
-- under. tournament_results names a team per result, and a team that has played
-- under eight names over the years cannot be searched or displayed from that:
-- an infix search over 941k results takes two seconds, over these 74k it takes
-- ten milliseconds, and «the team's name» stops depending on which row a query
-- plan happened to reach first.
CREATE TABLE IF NOT EXISTS teams (
    id integer PRIMARY KEY,
    name text NOT NULL,
    name_fold text NOT NULL DEFAULT '',
    town text NOT NULL DEFAULT '',
    last_tournament_id integer
);
-- name_fold is the name lowercased in Python, which folds Cyrillic. SQLite's
-- own lower() and LIKE fold ASCII only, so «мангаз» would not find «Мангазея»
-- without it. A searcher lowercases their query the same way and matches this.
-- (Keep semicolons out of these comments: db_init splits the schema on them.)
CREATE INDEX IF NOT EXISTS idx_teams_name_fold ON teams(name_fold);
"""

NEW_COLUMNS = [
    ("tournaments", "hide_questions_to", "text"),
    ("tournaments", "difficulty_forecast", "real"),
]

MISSING_MASKS = """
select id from tournaments
where date_end >= ? and date_end < ?
  and (hide_questions_to is null or hide_questions_to < ?)
  and exists (select 1 from tournament_results r where r.id = tournaments.id)
  and not exists (
      select 1 from tournament_results r where r.id = tournaments.id and r.mask is not null
  );
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
    conn = sqlite3.connect(path, timeout=60)
    cur = conn.cursor()
    for st in DB_INIT.split(";"):
        if st.strip():
            cur.execute(st.strip() + ";")
    for table, column, decl in NEW_COLUMNS:
        try:
            cur.execute(f"alter table {table} add column {column} {decl};")
        except sqlite3.OperationalError:
            pass
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


def season_containing(seasons, day):
    """seasons: rows of (id, date_start, date_end); day: a date."""
    for season_id, date_start, date_end in seasons:
        if parse_date(date_start) <= day <= parse_date(date_end):
            return season_id


def previous_season_id(seasons, current_id):
    """The season that started before current_id, or None when it is the first."""
    current = next((s for s in seasons if s[0] == current_id), None)
    if current is None:
        return None
    earlier = [s for s in seasons if parse_date(s[1]) < parse_date(current[1])]
    if not earlier:
        return None
    return max(earlier, key=lambda s: parse_date(s[1]))[0]


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
        self.conn = sqlite3.connect(db_path, timeout=60)
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
        self.updated_team_ids = set()

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

    @staticmethod
    def maybe_int(val):
        if val is None:
            return None
        return int(val)

    def update_tournament_data(self, tournament_id):
        tourn_info = self.req_tournament(tournament_id)
        if not tourn_info:
            return
        cur = self.conn.cursor()
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
                # the API used to expose this as "tournamentInRatingBalanced";
                # it was renamed to "rating" (a boolean). Use .get so a future
                # rename degrades to NULL instead of wiping the whole record.
                "in_rating": self.maybe_int(tourn_info.get("rating")),
                "maii_rating": self.maybe_int(tourn_info.get("maiiRating")),
                "questions_by_tour": self.get_questions_by_tour(tourn_info),
                "hide_questions_to": tourn_info.get("hideQuestionsTo"),
                "difficulty_forecast": tourn_info.get("difficultyForecast"),
            }
        except Exception as e:
            self.logger.error(
                f"couldn't update tournament data for {tournament_id}: {type(e)} {e}"
            )
            return
        # delete only after we successfully built the new record, so a parsing
        # failure can never leave a tournament without metadata
        cur.execute(f"delete from tournaments where id = {tournament_id};")
        self.conn.commit()
        self.insert_wrapper(tourn_dict, "tournaments")
        return "ok"

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
            self.updated_team_ids.add(res["team"]["id"])
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

    def rebuild_player_games(self):
        """
        How many tournaments each player has, counted from the results rows.
        Rebuilt whole in one transaction, so a reader never sees it half-built.
        """
        cur = self.conn.cursor()
        counts = collections.Counter()
        for (members,) in cur.execute(
            "select team_members from tournament_results where team_members is not null"
        ):
            try:
                ids = json.loads(members)
            except (TypeError, ValueError):
                continue
            if not isinstance(ids, list):
                continue
            for player_id in ids:
                if isinstance(player_id, int):
                    counts[player_id] += 1
        cur.execute("delete from player_games;")
        cur.executemany(
            "insert into player_games(player_id, games) values (?, ?);", counts.items()
        )
        self.conn.commit()
        self.logger.info(f"player_games rebuilt for {len(counts)} players")

    def rebuild_teams(self):
        """
        Each team as it is currently called: the name and town from its latest
        tournament. Rebuilt whole in one transaction, so a reader never sees it
        half-built.
        """
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            select r.team_id, r.team_current_name, coalesce(r.team_current_town, ''), r.id
            from tournament_results r
            join tournaments t on t.id = r.id
            where r.team_id is not null and r.team_current_name is not null
            order by r.team_id, t.date_start, r.id
            """
        )
        # Ordered by date within each team, so the last row seen for a team is
        # the current one and the dict keeps it.
        latest = {}
        for team_id, name, town, tournament_id in rows:
            latest[team_id] = (team_id, name, name.lower(), town, tournament_id)
        cur.execute("delete from teams;")
        cur.executemany(
            "insert into teams(id, name, name_fold, town, last_tournament_id) "
            "values (?, ?, ?, ?, ?);",
            latest.values(),
        )
        self.conn.commit()
        self.logger.info(f"teams rebuilt for {len(latest)} teams")

    def update_seasons(self):
        req = self.req_sleep("get", f"{API}/seasons")
        cur = self.conn.cursor()
        for season in req.json():
            cur.execute("delete from seasons where id = ?;", (season["id"],))
            cur.execute(
                "insert into seasons(id, date_start, date_end) values (?, ?, ?);",
                (season["id"], season["dateStart"], season["dateEnd"]),
            )
        self.conn.commit()

    def seasons(self):
        cur = self.conn.cursor()
        return cur.execute("select id, date_start, date_end from seasons;").fetchall()

    def current_season_id(self, day=None):
        day = day or datetime.datetime.now(UTC_PLUS_3).date()
        return season_containing(self.seasons(), day)

    def mirror_season_ids(self, day=None):
        """The seasons dope reads, newest first: the current one and the one
        before it. Most teams declare a base roster months into a season, and
        until they do the previous one is the answer."""
        seasons = self.seasons()
        current = self.current_season_id(day)
        if current is None:
            return []
        previous = previous_season_id(seasons, current)
        return [current] if previous is None else [current, previous]

    def has_team_season(self, team_id, season_id):
        cur = self.conn.cursor()
        return bool(
            cur.execute(
                "select 1 from team_seasons where team_id = ? and season_id = ? limit 1;",
                (team_id, season_id),
            ).fetchone()
        )

    def replace_team_season(self, team_id, season_id):
        req = self.req_sleep(
            "get", f"{API}/teams/{team_id}/seasons", params={"idseason": season_id}
        )
        try:
            rows = req.json()
        except Exception as e:
            self.logger.error(
                f"couldn't parse base roster of team {team_id}: {type(e)} {e}"
            )
            return
        if not isinstance(rows, list):
            return
        cur = self.conn.cursor()
        cur.execute(
            "delete from team_seasons where team_id = ? and season_id = ?;",
            (team_id, season_id),
        )
        if not rows:
            # A fetched-but-empty roster is remembered as player 0, so the
            # backfill can skip the team and dope reads "no base players".
            cur.execute(
                "insert into team_seasons(team_id, season_id, player_id) values (?, ?, 0);",
                (team_id, season_id),
            )
        for row in rows:
            cur.execute(
                "insert or replace into team_seasons("
                "team_id, season_id, player_id, date_added, date_removed, player_number"
                ") values (?, ?, ?, ?, ?, ?);",
                (
                    team_id,
                    season_id,
                    row["idplayer"],
                    row.get("dateAdded"),
                    row.get("dateRemoved"),
                    row.get("playerNumber"),
                ),
            )
        self.conn.commit()
        return "ok"

    def update_team_seasons(self):
        season_ids = self.mirror_season_ids()
        if not season_ids:
            self.logger.error("no season covers today; skipping base rosters")
            return
        current, earlier = season_ids[0], season_ids[1:]
        for team_id in sorted(self.updated_team_ids):
            self.replace_team_season(team_id, current)
            # A finished season's roster no longer moves: fetch it once.
            for season_id in earlier:
                if not self.has_team_season(team_id, season_id):
                    self.replace_team_season(team_id, season_id)

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

    def req_requests(self, tournament_id):
        req = self.req_sleep("get", f"{API}/tournaments/{tournament_id}/requests")
        if req.status_code != 200:
            return None
        try:
            return req.json()
        except Exception as e:
            self.logger.error(f"couldn't parse requests for {tournament_id}: {type(e)} {e}")
            return None

    def update_tournament_requests(self):
        """
        How many teams a tournament expects, as its venues declared when they
        asked to play it. Only a tournament still to be played is asked about:
        the number is a forecast, it moves until the last venue has signed up,
        and once the window has closed the last count stays as it was.
        """
        cur = self.conn.cursor()
        today = datetime.datetime.now(UTC_PLUS_3).date().isoformat()
        tournaments = cur.execute(
            "select id from tournaments where date_end >= ? order by date_start, id;",
            (today,),
        ).fetchall()
        self.logger.info(f"updating requests for {len(tournaments)} tournaments...")
        now = datetime.datetime.now(UTC_PLUS_3).strftime(DT_FORMAT_STRING)
        for (tournament_id,) in tournaments:
            requests = self.req_requests(tournament_id)
            if requests is None:
                continue
            # A rejected request is not a team anyone expects to see; one still
            # waiting on the organiser is.
            live = [r for r in requests if r.get("status") != "R"]
            cur.execute(
                "insert into tournament_requests(tournament_id, teams, venues, updated_at) "
                "values(?, ?, ?, ?) on conflict(tournament_id) do update set "
                "teams = excluded.teams, venues = excluded.venues, updated_at = excluded.updated_at;",
                (
                    tournament_id,
                    sum(r.get("approximateTeamsCount") or 0 for r in live),
                    len(live),
                    now,
                ),
            )
            self.conn.commit()

    def update_tournaments_missing_masks(self):
        """
        The API does not bump lastEditDate when an organiser finally uploads
        per-question results, so a tournament can go stale for good. Once
        hideQuestionsTo has passed, keep asking until the masks turn up.
        """
        cur = self.conn.cursor()
        now = datetime.datetime.now(datetime.timezone.utc)
        tournaments = cur.execute(
            MISSING_MASKS,
            (
                (now - datetime.timedelta(days=182)).date().isoformat(),
                now.date().isoformat(),
                now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            ),
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
            self.update_tournaments_missing_masks()
            self.update_tournament_requests()
            self.update_seasons()
            self.update_team_seasons()
            self.rebuild_player_games()
            self.rebuild_teams()
            self.update_ratings()
            self.insert_wrapper(
                {"datetime": start.strftime(DT_FORMAT_STRING)}, "db_updates"
            )
            self.logger.info("update finished successfully!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="buff.db")
    parser.add_argument("--tourn_ids")
    parser.add_argument(
        "--rebuild-player-games",
        action="store_true",
        help="only recount player_games from the results already mirrored",
    )
    parser.add_argument(
        "--rebuild-teams",
        action="store_true",
        help="only rebuild teams from the results already mirrored",
    )
    args = parser.parse_args()

    if os.path.isabs(args.db):
        db_path = args.db
    else:
        db_path = os.path.abspath(os.path.join(DIR, args.db))
    db_init(db_path)
    updater = DbUpdater(db_path, args)
    if args.rebuild_player_games:
        updater.rebuild_player_games()
        return
    if args.rebuild_teams:
        updater.rebuild_teams()
        return
    updater.update()


if __name__ == "__main__":
    main()
