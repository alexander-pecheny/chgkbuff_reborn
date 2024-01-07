import itertools
import json
import os
import sqlite3

import dill
import networkx as nx

DIR = os.path.dirname(os.path.abspath(__file__))
DB_LOC = os.path.join(DIR, "buff.db")
GRAPH_PATH = os.path.join(DIR, "graph.pickle")


def create_graph(cur):
    pairs = set()
    for res in cur.execute("select team_members from tournament_results"):
        for pair in itertools.combinations(json.loads(res[0]), 2):
            pairs.add(tuple(sorted(pair)))
    G = nx.Graph()
    G.add_nodes_from(itertools.chain(*pairs))
    for t in pairs:
        G.add_edge(*t)
    return G


if __name__ == "__main__":
    conn = sqlite3.connect(DB_LOC)
    cur = conn.cursor()

    G = create_graph(cur)

    with open(GRAPH_PATH, "wb") as f:
        dill.dump(G, f)

    print("Number of nodes", len(G.nodes))
    print("Number of edges", len(G.edges))
    print("Average degree", sum(dict(G.degree).values()) / len(G.nodes))
