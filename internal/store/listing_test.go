package store_test

import (
	"testing"
)

type tournamentsPageFixture struct {
	Page       int    `json:"page"`
	Cutoff     string `json:"cutoff"`
	TotalPages int    `json:"total_pages"`
	Rows       []struct {
		ID         int    `json:"id"`
		Name       string `json:"name"`
		HasResults bool   `json:"has_results"`
		DateStart  string `json:"date_start"`
		DateEnd    string `json:"date_end"`
		Type       string `json:"type"`
		Editors    []int  `json:"editors"`
	} `json:"rows"`
}

func TestTournamentsPageMatchesFlask(t *testing.T) {
	s := openStore(t)
	var fixture tournamentsPageFixture
	loadFixture(t, "tournaments_page.json", &fixture)

	page, err := s.Tournaments(fixture.Page, fixture.Cutoff, "", 750)
	if err != nil {
		t.Fatalf("tournaments page: %v", err)
	}
	if page.TotalPages != fixture.TotalPages {
		t.Errorf("%d pages, want %d", page.TotalPages, fixture.TotalPages)
	}
	if len(page.Rows) < len(fixture.Rows) {
		t.Fatalf("%d rows, want at least %d", len(page.Rows), len(fixture.Rows))
	}
	for i, want := range fixture.Rows {
		got := page.Rows[i]
		if got.ID != want.ID || got.Name != want.Name {
			t.Errorf("row %d: %d %q, want %d %q", i, got.ID, got.Name, want.ID, want.Name)
		}
		if got.HasResults != want.HasResults {
			t.Errorf("tournament %d has results %v, want %v", got.ID, got.HasResults, want.HasResults)
		}
		if date(got.DateStart) != want.DateStart || date(got.DateEnd) != want.DateEnd {
			t.Errorf("tournament %d runs %s..%s, want %s..%s", got.ID,
				date(got.DateStart), date(got.DateEnd), want.DateStart, want.DateEnd)
		}
		if kind(got.Type) != want.Type {
			t.Errorf("tournament %d is %q, want %q", got.ID, kind(got.Type), want.Type)
		}
		if len(got.Editors) != len(want.Editors) {
			t.Errorf("tournament %d has %d editors, want %d", got.ID, len(got.Editors), len(want.Editors))
			continue
		}
		for j, wantEditor := range want.Editors {
			if got.Editors[j].PlayerID != wantEditor {
				t.Errorf("tournament %d editor %d is %d, want %d", got.ID, j, got.Editors[j].PlayerID, wantEditor)
			}
		}
	}
}

func date(stamp string) string {
	if stamp == "" {
		return "-"
	}
	if len(stamp) >= 10 {
		return stamp[:10]
	}
	return stamp
}

func kind(tournamentType string) string {
	if tournamentType == "" {
		return "обычный"
	}
	return tournamentType
}
