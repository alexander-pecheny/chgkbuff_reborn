package store_test

import (
	"fmt"
	"strings"
	"testing"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/scoring"
)

type tournamentCase struct {
	TournamentID int            `json:"tournament_id"`
	Categories   map[string]int `json:"categories"`
	TrueDL       *float64       `json:"truedl"`
	TrueDLByTour string         `json:"truedl_by_tour"`
	Teams        []struct {
		TeamID     int     `json:"team_id"`
		Rating     int     `json:"rating"`
		Taken      int     `json:"taken"`
		AvgRating  float64 `json:"avg_rating"`
		ByCategory []int   `json:"by_category"`
	} `json:"teams"`
}

func TestTournamentScoringMatchesFlask(t *testing.T) {
	s := openStore(t)
	var cases []tournamentCase
	loadFixture(t, "tournaments.json", &cases)
	for _, c := range cases {
		tournament, err := s.Tournament(c.TournamentID)
		if err != nil {
			t.Fatalf("tournament %d: %v", c.TournamentID, err)
		}
		results, err := s.TournamentResults(c.TournamentID)
		if err != nil {
			t.Fatalf("results %d: %v", c.TournamentID, err)
		}
		summary := scoring.Summarize(results)

		if len(summary.Teams) != len(c.Teams) {
			t.Errorf("tournament %d: %d teams, want %d", c.TournamentID, len(summary.Teams), len(c.Teams))
			continue
		}
		bands := map[string]int{"coffin": 0, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
		for name, want := range c.Categories {
			if got := summary.Questions[bands[name]]; got != want {
				t.Errorf("tournament %d: %d questions in category %s, want %d", c.TournamentID, got, name, want)
			}
		}
		for i, want := range c.Teams {
			got := summary.Teams[i]
			if got.TeamID != want.TeamID || got.Rating != want.Rating || got.Taken != want.Taken {
				t.Errorf("tournament %d place %d: team %d rating %d taken %d, want team %d rating %d taken %d",
					c.TournamentID, i, got.TeamID, got.Rating, got.Taken, want.TeamID, want.Rating, want.Taken)
			}
			if got.AvgRating != want.AvgRating {
				t.Errorf("tournament %d team %d: average %v, want %v", c.TournamentID, got.TeamID, got.AvgRating, want.AvgRating)
			}
			for j, wantCount := range want.ByCategory {
				if got.ByCategory[j] != wantCount {
					t.Errorf("tournament %d team %d category %d: %d, want %d",
						c.TournamentID, got.TeamID, j, got.ByCategory[j], wantCount)
				}
			}
		}

		places, err := s.RatingPlaces(tournament.DateStart, teamIDs(results))
		if err != nil {
			t.Fatalf("rating places %d: %v", c.TournamentID, err)
		}
		score, err := scoring.TrueDL(results, places, tournament.QuestionsByTour)
		if err != nil {
			t.Fatalf("truedl %d: %v", c.TournamentID, err)
		}
		if score.Overall != *c.TrueDL {
			t.Errorf("tournament %d: TrueDL %v, want %v", c.TournamentID, score.Overall, *c.TrueDL)
		}
		if got := formatByTour(score.ByTour); got != c.TrueDLByTour {
			t.Errorf("tournament %d: TrueDL by tour %q, want %q", c.TournamentID, got, c.TrueDLByTour)
		}
	}
}

func teamIDs(results []scoring.TeamResult) []int {
	ids := make([]int, len(results))
	for i, r := range results {
		ids[i] = r.TeamID
	}
	return ids
}

func formatByTour(byTour []float64) string {
	parts := make([]string, len(byTour))
	for i, v := range byTour {
		parts[i] = fmt.Sprintf("%d — %s", i+1, scoring.Format(v))
	}
	return strings.Join(parts, ", ")
}
