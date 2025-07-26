#!/usr/bin/env python
"""
Integration test for the handshakes functionality using the actual graph database.
This test requires graph.db to be created first by running create_graph.py
"""
import os
import sqlite3

from flask_app import db_shortest_path, db_has_player

def test_handshakes_with_real_data():
    """Test the handshakes functionality with real graph data"""
    graph_db_path = "graph.db"
    
    if not os.path.exists(graph_db_path):
        print("ERROR: graph.db not found. Run 'python create_graph.py' first.")
        return False
    
    # Test 1: Check if we can query existing players
    conn = sqlite3.connect(graph_db_path)
    cur = conn.cursor()
    
    # Get a few random players from the database
    sample_players = cur.execute("SELECT player_id FROM nodes LIMIT 10").fetchall()
    if len(sample_players) < 2:
        print("ERROR: Not enough players in database")
        conn.close()
        return False
    
    player1, player2 = sample_players[0][0], sample_players[1][0]
    print(f"Testing with players {player1} and {player2}")
    
    # Test 2: Player existence check
    assert db_has_player(player1, graph_db_path), f"Player {player1} should exist"
    assert db_has_player(player2, graph_db_path), f"Player {player2} should exist"
    assert not db_has_player(999999999, graph_db_path), "Player 999999999 should not exist"
    print("✓ Player existence checks passed")
    
    # Test 3: Find path between two players
    path = db_shortest_path(player1, player2, graph_db_path)
    if path:
        print(f"✓ Found path of length {len(path)}: {path[:5]}{'...' if len(path) > 5 else ''}")
        assert path[0] == player1, "Path should start with first player"
        assert path[-1] == player2, "Path should end with second player"
    else:
        print(f"! No path found between {player1} and {player2} (they may be in different components)")
    
    # Test 4: Find a pair that should be connected (from same team)
    # Look for players who share an edge
    connected_players = cur.execute("""
        SELECT player1, player2 FROM edges LIMIT 1
    """).fetchone()
    
    if connected_players:
        p1, p2 = connected_players
        path = db_shortest_path(p1, p2, graph_db_path)
        assert len(path) == 2, f"Direct neighbors should have path length 2, got {len(path)}"
        assert path == [p1, p2], f"Direct path should be [{p1}, {p2}], got {path}"
        print(f"✓ Direct connection test passed: {p1} -> {p2}")
    
    # Test 5: Self-path
    path = db_shortest_path(player1, player1, graph_db_path)
    assert path == [player1], f"Self-path should be [{player1}], got {path}"
    print("✓ Self-path test passed")
    
    conn.close()
    
    print("All tests passed! The database-based handshakes implementation is working correctly.")
    return True

def performance_test():
    """Quick performance test comparing database vs memory usage"""
    import time
    import psutil
    import os
    
    graph_db_path = "graph.db"
    
    if not os.path.exists(graph_db_path):
        print("ERROR: graph.db not found. Run 'python create_graph.py' first.")
        return
    
    # Get current memory usage
    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss / 1024 / 1024  # MB
    
    # Test database approach
    conn = sqlite3.connect(graph_db_path)
    cur = conn.cursor()
    sample_players = cur.execute("SELECT player_id FROM nodes LIMIT 4").fetchall()
    conn.close()
    
    if len(sample_players) >= 2:
        p1, p2 = sample_players[0][0], sample_players[1][0]
        
        start_time = time.time()
        path = db_shortest_path(p1, p2, graph_db_path)
        db_time = time.time() - start_time
        
        current_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_used = current_memory - initial_memory
        
        print(f"Database approach:")
        print(f"  Time: {db_time:.4f} seconds")
        print(f"  Memory increase: {memory_used:.2f} MB")
        print(f"  Path length: {len(path) if path else 'No path'}")

if __name__ == '__main__':
    print("Running handshakes integration tests...")
    if test_handshakes_with_real_data():
        print("\nRunning performance test...")
        performance_test()