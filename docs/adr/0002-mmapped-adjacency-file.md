# Handshake paths come from an mmapped adjacency file

Handshake search used to run breadth-first search with one SQLite query per visited player against
`graph.db`, which took about 4.7 seconds. `create_graph.py` now writes `graph.bin`: sorted player
ids, then start offsets, then neighbour lists, all as packed little-endian int32. The server mmaps
it and runs bidirectional breadth-first search over the mapping, which answers in under a
millisecond.

The point of mmap rather than reading the file is that the 17 MB of adjacency stays file-backed and
evictable instead of sitting on the Go heap, which is the whole reason this rewrite happened.

## Consequences

A mapped file does not follow a rewrite, so the nightly cron restarts the server after rebuilding
`graph.bin`. Without that restart the server serves yesterday's graph indefinitely.
