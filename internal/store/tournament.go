package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"strings"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/scoring"
)

var ErrNotFound = errors.New("not found")

type TournamentDetail struct {
	Tournament
	QuestionsByTour string
}

func (s *Store) Tournament(id int) (TournamentDetail, error) {
	var t TournamentDetail
	var questionsByTour sql.NullString
	err := s.db.QueryRow(
		"select id, name, date_start, questions_by_tour from tournaments where id = ?", id,
	).Scan(&t.ID, &t.Name, &t.DateStart, &questionsByTour)
	if errors.Is(err, sql.ErrNoRows) {
		return t, ErrNotFound
	}
	t.QuestionsByTour = questionsByTour.String
	return t, err
}

func (s *Store) TournamentResults(id int) ([]scoring.TeamResult, error) {
	rows, err := s.db.Query(
		"select team_id, team_current_name, mask, rating from tournament_results where id = ? and mask is not null", id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var results []scoring.TeamResult
	for rows.Next() {
		var r scoring.TeamResult
		var name, rating sql.NullString
		if err := rows.Scan(&r.TeamID, &name, &r.Mask, &rating); err != nil {
			return nil, err
		}
		r.TeamName = name.String
		var parsed struct {
			InRating bool `json:"inRating"`
		}
		if err := json.Unmarshal([]byte(rating.String), &parsed); err == nil {
			r.InRating = parsed.InRating
		}
		results = append(results, r)
	}
	return results, rows.Err()
}

// The release used is the last one before the tournament, or the earliest if it predates them all.
func (s *Store) RatingPlaces(dateStart string, teamIDs []int) (map[int]float64, error) {
	places := map[int]float64{}
	if len(teamIDs) == 0 {
		return places, nil
	}
	var releaseID int
	err := s.db.QueryRow(
		"select id from releases where date < ? order by date desc limit 1", dateStart).Scan(&releaseID)
	if errors.Is(err, sql.ErrNoRows) {
		err = s.db.QueryRow("select min(id) from releases").Scan(&releaseID)
	}
	if err != nil {
		return nil, err
	}
	args := make([]any, 0, len(teamIDs)+1)
	args = append(args, releaseID)
	for _, id := range teamIDs {
		args = append(args, id)
	}
	query := "select team_id, place from ratings where release_id = ? and team_id in (?" +
		strings.Repeat(",?", len(teamIDs)-1) + ")"
	rows, err := s.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var teamID int
		var place float64
		if err := rows.Scan(&teamID, &place); err != nil {
			return nil, err
		}
		places[teamID] = place
	}
	return places, rows.Err()
}
