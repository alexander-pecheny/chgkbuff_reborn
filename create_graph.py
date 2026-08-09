#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build graph.bin: the player adjacency the Go server mmaps to answer handshake paths.

Layout, little-endian int32 throughout:

    magic "BUFFGRPH", version, node count, arc count   (20 bytes)
    node count      player ids, ascending
    node count + 1  start offsets into the neighbour list
    arc count       neighbour indices (each edge appears in both directions)

Players are addressed by their index in the id table, so the server binary-searches an id
once and then works in indices.
"""
import array
import itertools
import json
import os
import sqlite3
import struct
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
DB_LOC = os.path.join(DIR, "buff.db")
GRAPH_BIN_LOC = os.path.join(DIR, "graph.bin")
MAGIC = b"BUFFGRPH"
VERSION = 1


def read_edges(cur):
    """Deduplicated player pairs, packed into one int each to keep the set small."""
    edges = set()
    for (members,) in cur.execute("select team_members from tournament_results"):
        for a, b in itertools.combinations(sorted(set(json.loads(members))), 2):
            edges.add((a << 32) | b)
    return edges


def build_csr(edges):
    ids = sorted({e >> 32 for e in edges} | {e & 0xFFFFFFFF for e in edges})
    index = {p: i for i, p in enumerate(ids)}
    offsets = array.array("i", bytes(4 * (len(ids) + 1)))
    for e in edges:
        offsets[index[e >> 32] + 1] += 1
        offsets[index[e & 0xFFFFFFFF] + 1] += 1
    for i in range(len(ids)):
        offsets[i + 1] += offsets[i]
    cursor = array.array("i", offsets[: len(ids)])
    neighbours = array.array("i", bytes(4 * offsets[len(ids)]))
    for e in edges:
        ia, ib = index[e >> 32], index[e & 0xFFFFFFFF]
        neighbours[cursor[ia]] = ib
        cursor[ia] += 1
        neighbours[cursor[ib]] = ia
        cursor[ib] += 1
    return array.array("i", ids), offsets, neighbours


def write_graph_bin(path, ids, offsets, neighbours):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<iii", VERSION, len(ids), len(neighbours)))
        for part in (ids, offsets, neighbours):
            if sys.byteorder != "little":
                part = array.array("i", part)
                part.byteswap()
            part.tofile(f)
    os.replace(tmp, path)


def main():
    conn = sqlite3.connect(DB_LOC)
    edges = read_edges(conn.cursor())
    ids, offsets, neighbours = build_csr(edges)
    del edges
    write_graph_bin(GRAPH_BIN_LOC, ids, offsets, neighbours)
    print(f"players {len(ids)}, edges {len(neighbours) // 2}, "
          f"{os.path.getsize(GRAPH_BIN_LOC) / 1e6:.1f} MB -> {GRAPH_BIN_LOC}")


if __name__ == "__main__":
    main()
