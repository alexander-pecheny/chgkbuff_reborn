# Go web server, Python updater

The Flask server held around 50 MB resident for an app that only reads SQLite and prints HTML, of
which 21 MB is the interpreter and 18 MB is Flask itself. We rewrote the web server in Go, where the
same job costs about 10 MB, and deliberately left `update_db.py` and `create_graph.py` in Python:
they run once a night from cron, so their memory is irrelevant, and they encode years of quirks in
the rating site's API against a database that takes five hours to rebuild if damaged.

## Consequences

The two halves are coupled by the SQLite schema and the `graph.bin` layout rather than by code, so a
schema change has to be made in both languages.

`modernc.org/sqlite` is the driver, so the server builds with `CGO_ENABLED=0` and ships as one
static binary. It is slower per query than the C library; if that ever shows up in a page load,
`mattn/go-sqlite3` with `-tags sqlite_fts5` is a one-import swap behind `database/sql`.
