import networkx as nx
import sqlite3
import orjson
import itertools
import tqdm
import dill

conn = sqlite3.connect("buff.db")
cur = conn.cursor()
pairs = set()
for res in tqdm.tqdm(cur.execute("select team_members from tournament_results")):
    for pair in itertools.combinations(orjson.loads(res[0]), 2):
        pairs.add(tuple(sorted(pair)))
G = nx.Graph()
G.add_nodes_from(itertools.chain(*pairs))
for t in pairs:
    G.add_edge(*t)

with open("graph.pickle", "wb") as f:
    dill.dump(G, f)

print('Number of nodes', len(G.nodes))
print('Number of edges', len(G.edges))
print('Average degree', sum(dict(G.degree).values()) / len(G.nodes))
