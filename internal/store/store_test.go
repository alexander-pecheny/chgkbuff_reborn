package store_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/store"
)

// Every expectation here was captured from the Flask app this package replaced, running
// against this same buff.db, so a mismatch means behaviour changed. The app and the
// gen_fixtures.py that captured them were removed in the commit after 67bc0b1.

func openStore(t testing.TB) *store.Store {
	t.Helper()
	path := filepath.Join("..", "..", "buff.db")
	if _, err := os.Stat(path); err != nil {
		t.Skip("buff.db not present; run update_db.py")
	}
	s, err := store.Open(path)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func loadFixture(t *testing.T, name string, into any) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("parse fixture %s: %v", name, err)
	}
}

type teammatesCase struct {
	PlayerID   int    `json:"player_id"`
	DateFrom   string `json:"date_from"`
	DateTo     string `json:"date_to"`
	Types      string `json:"tournament_types"`
	PlayerName string `json:"player_name"`
	Teammates  []struct {
		PlayerID int    `json:"player_id"`
		Name     string `json:"name"`
		Games    int    `json:"games"`
	} `json:"teammates"`
}

func TestTeammatesMatchFlask(t *testing.T) {
	s := openStore(t)
	var cases []teammatesCase
	loadFixture(t, "teammates.json", &cases)
	for _, c := range cases {
		got, err := s.Teammates(c.PlayerID, c.DateFrom, c.DateTo, store.Filter(c.Types))
		if err != nil {
			t.Fatalf("teammates %d: %v", c.PlayerID, err)
		}
		if len(got) != len(c.Teammates) {
			t.Errorf("player %d (%s): %d teammates, want %d", c.PlayerID, c.Types, len(got), len(c.Teammates))
			continue
		}
		names, err := s.PlayerNames(append(idsOf(got), c.PlayerID))
		if err != nil {
			t.Fatalf("player names: %v", err)
		}
		for i, want := range c.Teammates {
			if got[i].PlayerID != want.PlayerID || got[i].Games != want.Games {
				t.Errorf("player %d (%s) rank %d: %d with %d games, want %d with %d",
					c.PlayerID, c.Types, i, got[i].PlayerID, got[i].Games, want.PlayerID, want.Games)
			}
			if names[want.PlayerID] != want.Name {
				t.Errorf("player %d name %q, want %q", want.PlayerID, names[want.PlayerID], want.Name)
			}
		}
		if c.PlayerName != "Игрок не найден" && names[c.PlayerID] != c.PlayerName {
			t.Errorf("player %d name %q, want %q", c.PlayerID, names[c.PlayerID], c.PlayerName)
		}
	}
}

type togetherCase struct {
	ID1         int    `json:"id1"`
	ID2         int    `json:"id2"`
	DateFrom    string `json:"date_from"`
	DateTo      string `json:"date_to"`
	Types       string `json:"tournament_types"`
	Tournaments []struct {
		ID    int    `json:"id"`
		Name  string `json:"name"`
		Dates string `json:"dates"`
		Type  string `json:"type"`
	} `json:"tournaments"`
}

func TestTogetherMatchesFlask(t *testing.T) {
	s := openStore(t)
	var cases []togetherCase
	loadFixture(t, "together.json", &cases)
	for _, c := range cases {
		got, err := s.Together(c.ID1, c.ID2, c.DateFrom, c.DateTo, store.Filter(c.Types))
		if err != nil {
			t.Fatalf("together %d+%d: %v", c.ID1, c.ID2, err)
		}
		if len(got) != len(c.Tournaments) {
			t.Errorf("%d+%d (%s): %d tournaments, want %d", c.ID1, c.ID2, c.Types, len(got), len(c.Tournaments))
			continue
		}
		for i, want := range c.Tournaments {
			if got[i].ID != want.ID || got[i].Name != want.Name || got[i].Type != want.Type {
				t.Errorf("%d+%d position %d: %d %q (%s), want %d %q (%s)", c.ID1, c.ID2, i,
					got[i].ID, got[i].Name, got[i].Type, want.ID, want.Name, want.Type)
			}
			if dates := datesOf(got[i]); dates != want.Dates {
				t.Errorf("tournament %d dates %q, want %q", got[i].ID, dates, want.Dates)
			}
		}
	}
}

// The fixture holds dates as the page shows them: one date when a tournament starts and
// ends the same day, otherwise a range.
func datesOf(t store.Tournament) string {
	start, _, _ := strings.Cut(t.DateStart, "T")
	end, _, _ := strings.Cut(t.DateEnd, "T")
	if start == end {
		return start
	}
	return start + "–" + end
}

func idsOf(teammates []store.Teammate) []int {
	ids := make([]int, len(teammates))
	for i, t := range teammates {
		ids[i] = t.PlayerID
	}
	return ids
}
