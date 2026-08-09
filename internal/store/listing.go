package store

import (
	"database/sql"
	"encoding/json"
	"math"
	"sort"
)

type Editor struct {
	PlayerID int
	Name     string
}

type Listing struct {
	Tournament
	HasResults bool
	Editors    []Editor
}

type TournamentPage struct {
	Rows       []Listing
	TotalPages int
}

// Page 0 is the exception: it looks forward, at what is coming up before the horizon.
func (s *Store) Tournaments(page int, cutoff, horizon string, perPage int) (TournamentPage, error) {
	var result TournamentPage
	var rows *sql.Rows
	var err error
	// The has-results flag comes from a subquery rather than a second pass: with the partial
	// index on masked results it is one probe per listed row.
	const columns = `select id, name, date_start, date_end, tournament_type, editors,
		exists(select 1 from tournament_results r where r.id = t.id and r.mask is not null)
		from tournaments t`
	if page == 0 {
		rows, err = s.db.Query(columns+
			" where date_end > ? and date_end <= ? order by date_start asc, id asc limit ?", cutoff, horizon, perPage)
	} else {
		var total int
		if err := s.db.QueryRow(
			"select count(*) from tournaments where date_end <= ?", cutoff).Scan(&total); err != nil {
			return result, err
		}
		result.TotalPages = int(math.Ceil(float64(total) / float64(perPage)))
		rows, err = s.db.Query(columns+
			" where date_end <= ? order by date_start desc, id asc limit ? offset ?", cutoff, perPage, (page-1)*perPage)
	}
	if err != nil {
		return result, err
	}
	defer rows.Close()

	for rows.Next() {
		var listing Listing
		var start, end, kind, editors sql.NullString
		if err := rows.Scan(&listing.ID, &listing.Name, &start, &end, &kind, &editors, &listing.HasResults); err != nil {
			return result, err
		}
		listing.DateStart, listing.DateEnd, listing.Type = start.String, end.String, kind.String
		var ids []int
		if editors.Valid {
			_ = json.Unmarshal([]byte(editors.String), &ids)
		}
		for _, id := range ids {
			listing.Editors = append(listing.Editors, Editor{PlayerID: id})
		}
		result.Rows = append(result.Rows, listing)
	}
	if err := rows.Err(); err != nil {
		return result, err
	}
	return result, s.fillEditorNames(result.Rows)
}

// Editors the site credits but has no player record for would render as a nameless link.
func (s *Store) fillEditorNames(listings []Listing) error {
	var editorIDs []int
	for _, listing := range listings {
		for _, editor := range listing.Editors {
			editorIDs = append(editorIDs, editor.PlayerID)
		}
	}
	if len(editorIDs) == 0 {
		return nil
	}
	names, err := s.PlayerNames(editorIDs)
	if err != nil {
		return err
	}
	for i := range listings {
		known := listings[i].Editors[:0]
		for _, editor := range listings[i].Editors {
			if name, ok := names[editor.PlayerID]; ok {
				known = append(known, Editor{PlayerID: editor.PlayerID, Name: name})
			}
		}
		sort.Slice(known, func(a, b int) bool { return known[a].PlayerID < known[b].PlayerID })
		listings[i].Editors = known
	}
	return nil
}
