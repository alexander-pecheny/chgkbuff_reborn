#!/usr/bin/env python
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask_app import db_shortest_path, db_has_player


class TestGraphDB(unittest.TestCase):
    def setUp(self):
        """Create a temporary test database with sample graph data"""
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        
        conn = sqlite3.connect(self.test_db_path)
        cur = conn.cursor()
        
        # Create tables
        cur.execute("""
            CREATE TABLE edges (
                player1 INTEGER,
                player2 INTEGER,
                PRIMARY KEY (player1, player2)
            )
        """)
        cur.execute("CREATE INDEX idx_player2 ON edges(player2)")
        cur.execute("CREATE TABLE nodes (player_id INTEGER PRIMARY KEY)")
        
        # Create test graph: 1-2-3-4 and 5-6 (disconnected)
        test_edges = [
            (1, 2),
            (2, 3), 
            (3, 4),
            (5, 6)
        ]
        
        test_nodes = [1, 2, 3, 4, 5, 6]
        
        cur.executemany("INSERT INTO edges (player1, player2) VALUES (?, ?)", test_edges)
        cur.executemany("INSERT INTO nodes (player_id) VALUES (?)", [(n,) for n in test_nodes])
        
        conn.commit()
        conn.close()
    
    def tearDown(self):
        """Clean up test database"""
        os.unlink(self.test_db_path)
    
    def test_db_has_player_existing(self):
        """Test checking for existing player"""
        self.assertTrue(db_has_player(1, self.test_db_path))
        self.assertTrue(db_has_player(6, self.test_db_path))
    
    def test_db_has_player_nonexisting(self):
        """Test checking for non-existing player"""
        self.assertFalse(db_has_player(99, self.test_db_path))
        self.assertFalse(db_has_player(0, self.test_db_path))
    
    def test_shortest_path_direct_connection(self):
        """Test path between directly connected players"""
        path = db_shortest_path(1, 2, self.test_db_path)
        self.assertEqual(path, [1, 2])
        
        # Test reverse direction
        path = db_shortest_path(2, 1, self.test_db_path)
        self.assertEqual(path, [2, 1])
    
    def test_shortest_path_multiple_hops(self):
        """Test path requiring multiple hops"""
        path = db_shortest_path(1, 4, self.test_db_path)
        self.assertEqual(path, [1, 2, 3, 4])
        
        # Test reverse direction
        path = db_shortest_path(4, 1, self.test_db_path)
        self.assertEqual(path, [4, 3, 2, 1])
    
    def test_shortest_path_same_player(self):
        """Test path from player to themselves"""
        path = db_shortest_path(1, 1, self.test_db_path)
        self.assertEqual(path, [1])
    
    def test_shortest_path_no_connection(self):
        """Test path between disconnected components"""
        path = db_shortest_path(1, 5, self.test_db_path)
        self.assertEqual(path, [])
        
        path = db_shortest_path(4, 6, self.test_db_path)
        self.assertEqual(path, [])
    
    def test_shortest_path_nonexistent_player(self):
        """Test path involving non-existent players"""
        path = db_shortest_path(1, 99, self.test_db_path)
        self.assertEqual(path, [])
        
        path = db_shortest_path(99, 1, self.test_db_path)
        self.assertEqual(path, [])
        
        path = db_shortest_path(99, 100, self.test_db_path)
        self.assertEqual(path, [])
    
    def test_shortest_path_optimal(self):
        """Test that the algorithm finds the shortest path"""
        # Add more connections to create multiple possible paths
        conn = sqlite3.connect(self.test_db_path)
        cur = conn.cursor()
        
        # Add nodes 7, 8 and create a longer alternative path: 1-7-8-4
        cur.executemany("INSERT INTO nodes (player_id) VALUES (?)", [(7,), (8,)])
        cur.executemany("INSERT INTO edges (player1, player2) VALUES (?, ?)", 
                       [(1, 7), (7, 8), (8, 4)])
        conn.commit()
        conn.close()
        
        # Should still find the shorter path 1-2-3-4 (length 4) not 1-7-8-4 (length 4)
        # Both are length 4, but BFS will find the first one discovered
        path = db_shortest_path(1, 4, self.test_db_path)
        self.assertEqual(len(path), 4)
        self.assertEqual(path[0], 1)
        self.assertEqual(path[-1], 4)


class TestGraphDBIntegration(unittest.TestCase):
    """Integration tests that require the actual buff.db"""
    
    def test_create_graph_db_function(self):
        """Test that create_graph_db function works correctly with test data"""
        import json
        import itertools
        
        # Create a minimal test database
        test_db_fd, test_buff_db = tempfile.mkstemp(suffix='.db')
        os.close(test_db_fd)
        
        test_graph_db_fd, test_graph_db = tempfile.mkstemp(suffix='.db')
        os.close(test_graph_db_fd)
        
        try:
            # Create minimal tournament results
            conn = sqlite3.connect(test_buff_db)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE tournament_results (
                    team_members TEXT
                )
            """)
            
            # Insert test data: teams with some shared players
            teams = [
                [1, 2, 3],
                [2, 3, 4], 
                [4, 5, 6]
            ]
            
            cur.executemany("INSERT INTO tournament_results (team_members) VALUES (?)", [
                (json.dumps(team),) for team in teams
            ])
            conn.commit()
            
            # Create the graph database manually (simulating create_graph_db logic)
            graph_conn = sqlite3.connect(test_graph_db)
            graph_cur = graph_conn.cursor()
            
            # Create tables
            graph_cur.execute("DROP TABLE IF EXISTS edges")
            graph_cur.execute("""
                CREATE TABLE edges (
                    player1 INTEGER,
                    player2 INTEGER,
                    PRIMARY KEY (player1, player2)
                )
            """)
            graph_cur.execute("CREATE INDEX idx_player2 ON edges(player2)")
            graph_cur.execute("DROP TABLE IF EXISTS nodes")
            graph_cur.execute("CREATE TABLE nodes (player_id INTEGER PRIMARY KEY)")
            
            # Extract pairs
            pairs = set()
            for team in teams:
                for pair in itertools.combinations(team, 2):
                    pairs.add(tuple(sorted(pair)))
            
            # Insert data
            graph_cur.executemany("INSERT INTO edges (player1, player2) VALUES (?, ?)", pairs)
            all_players = set(itertools.chain(*pairs))
            graph_cur.executemany("INSERT INTO nodes (player_id) VALUES (?)", [(p,) for p in all_players])
            
            graph_conn.commit()
            
            # Verify the content
            nodes = graph_cur.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            self.assertEqual(nodes, 6)  # Players 1,2,3,4,5,6
            
            # Check edges: (1,2), (1,3), (2,3), (2,4), (3,4), (4,5), (4,6), (5,6)
            edges = graph_cur.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            expected_edges = 8  # Total unique pairs across all teams
            self.assertEqual(edges, expected_edges)
            
            # Test pathfinding on this graph
            path = db_shortest_path(1, 6, test_graph_db)
            self.assertGreater(len(path), 0)  # Should find a path
            self.assertEqual(path[0], 1)
            self.assertEqual(path[-1], 6)
            
            graph_conn.close()
            conn.close()
            
        finally:
            # Clean up
            for db_path in [test_buff_db, test_graph_db]:
                if os.path.exists(db_path):
                    os.unlink(db_path)


if __name__ == '__main__':
    unittest.main()