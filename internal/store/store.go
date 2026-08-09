// Package store reads the mirror of the rating site that update_db.py maintains. It only
// ever queries: the nightly updater owns every write.
package store

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/url"
	"slices"
	"sort"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

type Filter string

const (
	AllTournaments Filter = "all_tournaments"
	LanSync        Filter = "lan_sync"
	OnlineAsync    Filter = "online_async"
)

func ParseFilter(s string) Filter {
	switch Filter(s) {
	case LanSync:
		return LanSync
	case OnlineAsync:
		return OnlineAsync
	default:
		return AllTournaments
	}
}

func (f Filter) String() string {
	switch f {
	case LanSync:
		return "очники и синхроны"
	case OnlineAsync:
		return "онлайны и асинхроны"
	default:
		return "все турниры"
	}
}

type Tournament struct {
	ID        int
	Name      string
	DateStart string
	DateEnd   string
	Type      string
}

// The site types asynchronous events but not onlines, so onlines are recognised by name.
func (t Tournament) Remote() bool {
	return t.Type == "Асинхрон" || strings.Contains(strings.ToLower(t.Name), "онлайн")
}

func (f Filter) matches(t Tournament) bool {
	switch f {
	case LanSync:
		return !t.Remote()
	case OnlineAsync:
		return t.Remote()
	default:
		return true
	}
}

type Teammate struct {
	PlayerID int
	Games    int
}

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	// rw, not ro: the mirror is journal_mode=delete, and only a writable connection can roll
	// back the journal a killed updater leaves behind.
	dsn := fmt.Sprintf("file:%s?mode=rw&_pragma=busy_timeout(5000)&_pragma=cache_size(-2000)",
		url.PathEscape(path))
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(2)
	db.SetConnMaxIdleTime(time.Minute)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

const sharedTournamentsQuery = `
with results as (
    select id as tournament_id, team_members from search where team_members match ?
), right_tournaments as (
    select id as tournament_id, name, date_start, date_end, tournament_type
    from tournaments
    where date_start > ? and date_start < ? and tournament_type != 'Общий зачёт'
)
select r.tournament_id, r.team_members, r1.name, r1.date_start, r1.date_end, r1.tournament_type
from results as r
inner join right_tournaments as r1 on (r.tournament_id = r1.tournament_id)`

// Ties keep the order the tournaments came back in.
func (s *Store) Teammates(playerID int, dateFrom, dateTo string, filter Filter) ([]Teammate, error) {
	rows, err := s.db.Query(sharedTournamentsQuery, playerID, dateFrom, dateTo)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	games := map[int]int{}
	var order []int
	counted := map[int]bool{}
	for rows.Next() {
		tournament, members, err := scanShared(rows)
		if err != nil {
			return nil, err
		}
		if counted[tournament.ID] || !filter.matches(tournament) || !slices.Contains(members, playerID) {
			continue
		}
		counted[tournament.ID] = true
		for _, id := range members {
			if id == playerID {
				continue
			}
			if _, seen := games[id]; !seen {
				order = append(order, id)
			}
			games[id]++
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	teammates := make([]Teammate, len(order))
	for i, id := range order {
		teammates[i] = Teammate{PlayerID: id, Games: games[id]}
	}
	sort.SliceStable(teammates, func(i, j int) bool { return teammates[i].Games > teammates[j].Games })
	return teammates, nil
}

func (s *Store) Together(playerID1, playerID2 int, dateFrom, dateTo string, filter Filter) ([]Tournament, error) {
	match := fmt.Sprintf("%d AND %d", playerID1, playerID2)
	rows, err := s.db.Query(sharedTournamentsQuery, match, dateFrom, dateTo)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var tournaments []Tournament
	seen := map[int]bool{}
	for rows.Next() {
		tournament, members, err := scanShared(rows)
		if err != nil {
			return nil, err
		}
		if seen[tournament.ID] || !slices.Contains(members, playerID1) || !slices.Contains(members, playerID2) {
			continue
		}
		seen[tournament.ID] = true
		if !filter.matches(tournament) {
			continue
		}
		tournaments = append(tournaments, tournament)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sort.Slice(tournaments, func(i, j int) bool {
		if tournaments[i].DateStart != tournaments[j].DateStart {
			return tournaments[i].DateStart < tournaments[j].DateStart
		}
		return tournaments[i].Name < tournaments[j].Name
	})
	return tournaments, nil
}

func scanShared(rows *sql.Rows) (Tournament, []int, error) {
	var t Tournament
	var raw string
	if err := rows.Scan(&t.ID, &raw, &t.Name, &t.DateStart, &t.DateEnd, &t.Type); err != nil {
		return t, nil, err
	}
	var members []int
	if err := json.Unmarshal([]byte(raw), &members); err != nil {
		return t, nil, fmt.Errorf("tournament %d team members: %w", t.ID, err)
	}
	return t, members, nil
}

func (s *Store) PlayerNames(ids []int) (map[int]string, error) {
	names := map[int]string{}
	if len(ids) == 0 {
		return names, nil
	}
	query := "select id, name, surname from players where id in (?" + strings.Repeat(",?", len(ids)-1) + ")"
	args := make([]any, len(ids))
	for i, id := range ids {
		args[i] = id
	}
	rows, err := s.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int
		var name, surname sql.NullString
		if err := rows.Scan(&id, &name, &surname); err != nil {
			return nil, err
		}
		names[id] = fmt.Sprintf("%s %c.", name.String, initial(surname.String))
	}
	return names, rows.Err()
}

func initial(surname string) rune {
	for _, r := range surname {
		return r
	}
	return '-'
}

func (s *Store) LastUpdate() (string, error) {
	var stamp sql.NullString
	if err := s.db.QueryRow("select max(datetime) from db_updates").Scan(&stamp); err != nil {
		return "", err
	}
	cleaned, _, _ := strings.Cut(stamp.String, "+")
	return strings.Replace(cleaned, "T", " ", 1), nil
}
