import itertools
import json
import os
import sqlite3

import dill
import networkx as nx

DIR = os.path.dirname(os.path.abspath(__file__))
DB_LOC = os.path.join(DIR, "buff.db")
GRAPH_DB_LOC = os.path.join(DIR, "graph.db")
GRAPH_PATH = os.path.join(DIR, "graph.pickle")


def create_graph_db(cur):
    """Create graph database with edges table for memory-efficient pathfinding"""
    graph_conn = sqlite3.connect(GRAPH_DB_LOC)
    graph_cur = graph_conn.cursor()
    
    # Create edges table with proper indexing
    graph_cur.execute("DROP TABLE IF EXISTS edges")
    graph_cur.execute("""
        CREATE TABLE edges (
            player1 INTEGER,
            player2 INTEGER,
            PRIMARY KEY (player1, player2)
        )
    """)
    
    # Create index for reverse lookups
    graph_cur.execute("CREATE INDEX idx_player2 ON edges(player2)")
    
    # Extract all player pairs from tournament results
    pairs = set()
    for res in cur.execute("select team_members from tournament_results"):
        for pair in itertools.combinations(json.loads(res[0]), 2):
            pairs.add(tuple(sorted(pair)))
    
    # Insert edges into database
    graph_cur.executemany("INSERT INTO edges (player1, player2) VALUES (?, ?)", pairs)
    
    # Create nodes table for quick existence checks
    graph_cur.execute("DROP TABLE IF EXISTS nodes")
    graph_cur.execute("CREATE TABLE nodes (player_id INTEGER PRIMARY KEY)")
    
    all_players = set(itertools.chain(*pairs))
    graph_cur.executemany("INSERT INTO nodes (player_id) VALUES (?)", [(p,) for p in all_players])
    
    graph_conn.commit()
    graph_conn.close()
    
    return len(all_players), len(pairs)


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

    # Create both NetworkX graph (for backward compatibility) and database graph
    G = create_graph(cur)
    
    with open(GRAPH_PATH, "wb") as f:
        dill.dump(G, f)

    num_nodes, num_edges = create_graph_db(cur)

    print("NetworkX Graph:")
    print("Number of nodes", len(G.nodes))
    print("Number of edges", len(G.edges))
    print("Average degree", sum(dict(G.degree).values()) / len(G.nodes))
    
    print("\nDatabase Graph:")
    print("Number of nodes", num_nodes)
    print("Number of edges", num_edges)
    print("Average degree", (num_edges * 2) / num_nodes if num_nodes > 0 else 0)
