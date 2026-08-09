package graph_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/graph"
)

type handshakeCase struct {
	ID1           int    `json:"id1"`
	ID2           int    `json:"id2"`
	Outcome       string `json:"outcome"`
	Hops          *int   `json:"hops"`
	Intermediates []int  `json:"intermediates"`
}

func openGraph(t *testing.T) *graph.Graph {
	t.Helper()
	path := filepath.Join("..", "..", "graph.bin")
	if _, err := os.Stat(path); err != nil {
		t.Skip("graph.bin not built; run create_graph.py")
	}
	g, err := graph.Open(path)
	if err != nil {
		t.Fatalf("open graph: %v", err)
	}
	t.Cleanup(func() { g.Close() })
	return g
}

func loadCases(t *testing.T) []handshakeCase {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "handshakes.json"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	var cases []handshakeCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatalf("parse fixture: %v", err)
	}
	return cases
}

// The path itself is not unique — bidirectional search may pick a different chain of the
// same length — so we hold the port to the hop count and to every link being a real edge.
func TestPathMatchesFlaskOutcomes(t *testing.T) {
	g := openGraph(t)
	for _, c := range loadCases(t) {
		path := g.Path(c.ID1, c.ID2)
		switch c.Outcome {
		case "unknown_player":
			if g.Has(c.ID2) {
				t.Errorf("player %d should be absent from the graph", c.ID2)
			}
			if path != nil {
				t.Errorf("%d->%d: got path %v, want none", c.ID1, c.ID2, path)
			}
		case "no_path":
			if path != nil {
				t.Errorf("%d->%d: got path %v, want none", c.ID1, c.ID2, path)
			}
		default:
			if len(path) == 0 {
				t.Errorf("%d->%d: no path, want %d hops", c.ID1, c.ID2, *c.Hops)
				continue
			}
			if got := len(path) - 1; got != *c.Hops {
				t.Errorf("%d->%d: %d hops, want %d (path %v)", c.ID1, c.ID2, got, *c.Hops, path)
			}
			if path[0] != c.ID1 || path[len(path)-1] != c.ID2 {
				t.Errorf("%d->%d: path %v has wrong endpoints", c.ID1, c.ID2, path)
			}
			for i := 0; i+1 < len(path); i++ {
				if !g.AreTeammates(path[i], path[i+1]) {
					t.Errorf("%d->%d: %d and %d never played together", c.ID1, c.ID2, path[i], path[i+1])
				}
			}
		}
	}
}

func TestHasKnowsPlayers(t *testing.T) {
	g := openGraph(t)
	if !g.Has(30152) {
		t.Error("player 30152 should be in the graph")
	}
	if g.Has(99999999) {
		t.Error("player 99999999 should not be in the graph")
	}
}

func BenchmarkPath(b *testing.B) {
	g, err := graph.Open(filepath.Join("..", "..", "graph.bin"))
	if err != nil {
		b.Skip("graph.bin not built")
	}
	defer g.Close()
	for b.Loop() {
		g.Path(1, 30152)
	}
}
