#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import sqlite3
from pathlib import Path


def main():
    conn = sqlite3.connect("buff.db")
    cur = conn.cursor()
    conn2 = sqlite3.connect("rating.db")
    conn2.row_factory = sqlite3.Row
    releases_data = json.loads(Path("releases_data.json").read_text())
    release_to_date = {x: releases_data[x]["date"].split("T")[0] for x in releases_data}
    cur2 = conn2.cursor()
    for row in cur2.execute("select * from chgk_release_data_balanced"):
        release_date = release_to_date[str(row["idrelease"])]
        if release_date > "2020-04-01":
            continue
        if row["rating_position_from"]:
            position = (row["rating_position_from"] + row["rating_position_to"]) / 2.0
        else:
            position = row["rating_position"]
        already_exists = cur.execute(
            "select id from releases where id = ?", (row["idrelease"] - 5000,)
        ).fetchone()
        if not already_exists:
            cur.execute(
                "insert into releases (id, date, updated_at, q) values (?, ?, ?, ?)",
                (
                    row["idrelease"] - 5000,
                    release_date,
                    "2025-06-09",
                    0,
                ),
            )
        cur.execute(
            "insert into ratings (release_id, team_id, place, rating, trb) values (?, ?, ?, ?, ?)",
            (
                row["idrelease"] - 5000,
                row["idteam"],
                position,
                row["rating"],
                row["tech_rating"],
            ),
        )


if __name__ == "__main__":
    main()
