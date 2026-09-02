#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fill team_seasons for every team that played recently, one team per API call.
Resumable: teams already mirrored for the season are skipped unless --force.
"""
import argparse
import datetime
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, DIR)
from update_db import UTC_PLUS_3, DbUpdater, db_init, parse_date, previous_season_id

RECENT_TEAMS = """
select distinct r.team_id
from tournament_results r
join tournaments t on t.id = r.id
where t.date_start >= ?
order by r.team_id;
"""


def previous_season_start(seasons, current_id):
    previous = previous_season_id(seasons, current_id)
    for season_id, date_start, _ in seasons:
        if season_id == (previous if previous is not None else current_id):
            return date_start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="buff.db")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    args.tourn_ids = None

    db_path = args.db if os.path.isabs(args.db) else os.path.join(DIR, args.db)
    db_init(db_path)
    updater = DbUpdater(db_path, args)

    if not updater.seasons():
        updater.update_seasons()
    seasons = updater.seasons()
    today = datetime.datetime.now(UTC_PLUS_3).date()
    season_ids = updater.mirror_season_ids(today)
    if not season_ids:
        print("no season covers today")
        return

    since = previous_season_start(seasons, season_ids[0])
    conn = sqlite3.connect(db_path, timeout=60)
    recent = [row[0] for row in conn.execute(RECENT_TEAMS, (since,))]

    for season_id in season_ids:
        team_ids = recent
        if not args.force:
            done = {
                row[0]
                for row in conn.execute(
                    "select distinct team_id from team_seasons where season_id = ?;",
                    (season_id,),
                )
            }
            team_ids = [t for t in team_ids if t not in done]
        if args.limit:
            team_ids = team_ids[: args.limit]
        print(f"season {season_id}, {len(team_ids)} teams to fetch")
        for i, team_id in enumerate(team_ids, 1):
            updater.replace_team_season(team_id, season_id)
            if i % 100 == 0:
                print(f"[{i}/{len(team_ids)}] teams done")
    conn.close()
    print("done")


if __name__ == "__main__":
    main()
