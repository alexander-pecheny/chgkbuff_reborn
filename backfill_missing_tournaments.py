#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script to backfill tournament metadata for tournaments that have results
but are missing from the tournaments table.
"""
import argparse
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))

# Import the updater from update_db
sys.path.insert(0, DIR)
from update_db import DbUpdater, db_init


def get_missing_tournament_ids(db_path):
    """Find tournament IDs that have results but no metadata."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    missing = cur.execute("""
        SELECT DISTINCT tr.id
        FROM tournament_results tr
        LEFT JOIN tournaments t ON tr.id = t.id
        WHERE t.id IS NULL
        ORDER BY tr.id
    """).fetchall()
    conn.close()
    return [row[0] for row in missing]


def main():
    parser = argparse.ArgumentParser(
        description="Backfill tournament metadata for tournaments with results but no metadata"
    )
    parser.add_argument("--db", default="buff.db", help="Path to database file")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be updated")
    parser.add_argument("--limit", type=int, help="Limit number of tournaments to update")
    args = parser.parse_args()

    if os.path.isabs(args.db):
        db_path = args.db
    else:
        db_path = os.path.abspath(os.path.join(DIR, args.db))

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return

    missing_ids = get_missing_tournament_ids(db_path)
    print(f"Found {len(missing_ids)} tournaments with results but no metadata.")

    if args.limit:
        missing_ids = missing_ids[:args.limit]
        print(f"Limited to {len(missing_ids)} tournaments.")

    if not missing_ids:
        print("Nothing to do.")
        return

    if args.dry_run:
        print("Dry run - would update these tournament IDs:")
        for tid in missing_ids:
            print(f"  {tid}")
        return

    # Create a minimal args object for DbUpdater
    class UpdaterArgs:
        tourn_ids = None

    db_init(db_path)
    updater = DbUpdater(db_path, UpdaterArgs())

    success = 0
    failed = 0
    for i, tid in enumerate(missing_ids, 1):
        print(f"[{i}/{len(missing_ids)}] Updating tournament {tid}...")
        try:
            result = updater.update_tournament_data(tid)
            if result == "ok":
                success += 1
            else:
                failed += 1
                print(f"  Failed to update metadata for {tid}")
        except Exception as e:
            failed += 1
            print(f"  Exception updating {tid}: {type(e).__name__} {e}")

    print(f"\nDone. Success: {success}, Failed: {failed}")


if __name__ == "__main__":
    main()
