// Package web serves the Buff pages.
package web

import (
	"embed"
	"errors"
	"fmt"
	"html/template"
	"io/fs"
	"log"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/graph"
	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/scoring"
	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/store"
)

//go:embed templates/*.html static/*.css
var assets embed.FS

const (
	earliestDate       = "1990-01-01"
	tournamentsPerPage = 750
	notFoundName       = "Игрок не найден"
)

type Server struct {
	store     *store.Store
	graph     *graph.Graph
	templates map[string]*template.Template
	now       func() time.Time
}

func New(s *store.Store, g *graph.Graph) (*Server, error) {
	templates := map[string]*template.Template{}
	pages, err := fs.Glob(assets, "templates/*.html")
	if err != nil {
		return nil, err
	}
	for _, page := range pages {
		name := strings.TrimSuffix(strings.TrimPrefix(page, "templates/"), ".html")
		if name == "layout" || name == "form" {
			continue
		}
		t, err := template.ParseFS(assets, "templates/layout.html", "templates/form.html", page)
		if err != nil {
			return nil, fmt.Errorf("parse %s: %w", page, err)
		}
		templates[name] = t
	}
	return &Server{store: s, graph: g, templates: templates, now: time.Now}, nil
}

func (s *Server) Handler() http.Handler {
	static, err := fs.Sub(assets, "static")
	if err != nil {
		panic(err)
	}
	mux := http.NewServeMux()
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServerFS(static)))
	mux.HandleFunc("GET /{$}", s.index)
	mux.HandleFunc("POST /{$}", s.search)
	mux.HandleFunc("GET /stats", s.stats)
	mux.HandleFunc("GET /together", s.together)
	mux.HandleFunc("GET /tournament/{id}", s.tournament)
	mux.HandleFunc("GET /tournaments", s.tournaments)
	mux.HandleFunc("GET /handshakes", s.handshakesForm)
	mux.HandleFunc("POST /handshakes", s.handshakes)
	return mux
}

type page struct {
	Errors     []string
	LastUpdate string
}

type formValues struct {
	PlayerID string
	DateFrom string
	DateTo   string
	Types    store.Filter
}

type playerRef struct {
	PlayerID  int
	Name      string
	StatsLink string
}

type statsRow struct {
	playerRef
	Games        string
	TogetherLink string
}

type statsView struct {
	PlayerID   int
	PlayerName string
	SelfLink   string
	DateFrom   string
	DateTo     string
	Types      string
	Rows       []statsRow
}

type indexPage struct {
	page
	Form  formValues
	Stats *statsView
}

func (s *Server) render(w http.ResponseWriter, name string, data any) {
	t, ok := s.templates[name]
	if !ok {
		http.Error(w, "unknown page", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := t.ExecuteTemplate(w, "layout", data); err != nil {
		log.Printf("render %s: %v", name, err)
	}
}

func (s *Server) fail(w http.ResponseWriter, form formValues, messages ...string) {
	s.render(w, "index", indexPage{page: s.chrome(messages...), Form: form})
}

func (s *Server) chrome(messages ...string) page {
	lastUpdate, err := s.store.LastUpdate()
	if err != nil {
		log.Printf("last update: %v", err)
	}
	return page{Errors: messages, LastUpdate: lastUpdate}
}

func (s *Server) today() string {
	return s.now().Format(time.DateOnly)
}

func (s *Server) index(w http.ResponseWriter, r *http.Request) {
	s.render(w, "index", indexPage{page: s.chrome(), Form: formValues{Types: store.AllTournaments}})
}

func (s *Server) search(w http.ResponseWriter, r *http.Request) {
	form := formValues{
		PlayerID: r.FormValue("player_id"),
		DateFrom: r.FormValue("date_from"),
		DateTo:   r.FormValue("date_to"),
		Types:    store.ParseFilter(r.FormValue("tournament_types")),
	}
	playerID, problems := validate(form.PlayerID, form.DateFrom, form.DateTo, false)
	if len(problems) > 0 {
		s.fail(w, form, problems...)
		return
	}
	query := url.Values{
		"player_id":        {strconv.Itoa(playerID)},
		"date_from":        {or(form.DateFrom, earliestDate)},
		"date_to":          {or(form.DateTo, s.today())},
		"tournament_types": {string(form.Types)},
	}
	http.Redirect(w, r, "/stats?"+query.Encode(), http.StatusFound)
}

func (s *Server) stats(w http.ResponseWriter, r *http.Request) {
	form := formValues{
		PlayerID: r.URL.Query().Get("player_id"),
		DateFrom: r.URL.Query().Get("date_from"),
		DateTo:   r.URL.Query().Get("date_to"),
		Types:    store.ParseFilter(r.URL.Query().Get("tournament_types")),
	}
	playerID, problems := validate(form.PlayerID, form.DateFrom, form.DateTo, true)
	if len(problems) > 0 {
		s.fail(w, form, problems...)
		return
	}
	teammates, err := s.store.Teammates(playerID, form.DateFrom, form.DateTo, form.Types)
	if err != nil {
		s.serverError(w, "teammates", err)
		return
	}
	ids := make([]int, 0, len(teammates)+1)
	ids = append(ids, playerID)
	for _, t := range teammates {
		ids = append(ids, t.PlayerID)
	}
	names, err := s.store.PlayerNames(ids)
	if err != nil {
		s.serverError(w, "player names", err)
		return
	}
	view := &statsView{
		PlayerID:   playerID,
		PlayerName: name(names, playerID),
		SelfLink:   statsLink(playerID, form),
		DateFrom:   form.DateFrom,
		DateTo:     form.DateTo,
		Types:      form.Types.String(),
	}
	for _, t := range teammates {
		view.Rows = append(view.Rows, statsRow{
			playerRef:    playerRef{PlayerID: t.PlayerID, Name: name(names, t.PlayerID), StatsLink: statsLink(t.PlayerID, form)},
			Games:        games(t.Games),
			TogetherLink: togetherLink(playerID, t.PlayerID, form),
		})
	}
	s.render(w, "index", indexPage{page: s.chrome(), Form: form, Stats: view})
}

type togetherTournament struct {
	ID    int
	Name  string
	Dates string
	Type  string
}

type togetherPage struct {
	page
	Form        formValues
	First       playerRef
	Second      playerRef
	DateFrom    string
	DateTo      string
	Types       string
	Tournaments []togetherTournament
}

func (s *Server) together(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	form := formValues{
		PlayerID: query.Get("id1"),
		DateFrom: query.Get("date_from"),
		DateTo:   query.Get("date_to"),
		Types:    store.ParseFilter(query.Get("tournament_types")),
	}
	playerID1, problems := validate(query.Get("id1"), form.DateFrom, form.DateTo, false)
	playerID2, moreProblems := validate(query.Get("id2"), form.DateFrom, form.DateTo, false)
	if problems := merge(problems, moreProblems); len(problems) > 0 {
		s.fail(w, form, problems...)
		return
	}
	form.DateFrom = or(form.DateFrom, earliestDate)
	form.DateTo = or(form.DateTo, s.today())
	tournaments, err := s.store.Together(playerID1, playerID2, form.DateFrom, form.DateTo, form.Types)
	if err != nil {
		s.serverError(w, "together", err)
		return
	}
	names, err := s.store.PlayerNames([]int{playerID1, playerID2})
	if err != nil {
		s.serverError(w, "player names", err)
		return
	}
	view := togetherPage{
		page:     s.chrome(),
		Form:     form,
		First:    playerRef{PlayerID: playerID1, Name: name(names, playerID1), StatsLink: statsLink(playerID1, form)},
		Second:   playerRef{PlayerID: playerID2, Name: name(names, playerID2), StatsLink: statsLink(playerID2, form)},
		DateFrom: form.DateFrom,
		DateTo:   form.DateTo,
		Types:    form.Types.String(),
	}
	for _, t := range tournaments {
		view.Tournaments = append(view.Tournaments, togetherTournament{
			ID: t.ID, Name: t.Name, Dates: dateRange(t.DateStart, t.DateEnd), Type: t.Type,
		})
	}
	s.render(w, "together", view)
}

type trueDL struct {
	Overall string
	ByTour  string
}

type teamRow struct {
	TeamID     int
	TeamName   string
	Rating     int
	Taken      int
	AvgRating  string
	ByCategory [5]int
}

type tournamentPage struct {
	page
	ID        int
	Name      string
	Date      string
	TrueDL    *trueDL
	Questions [6]int
	Discarded int
	Teams     []teamRow
}

func (s *Server) tournament(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil {
		s.fail(w, formValues{Types: store.AllTournaments}, "Турнир не найден")
		return
	}
	tournament, err := s.store.Tournament(id)
	if errors.Is(err, store.ErrNotFound) {
		s.fail(w, formValues{Types: store.AllTournaments}, fmt.Sprintf("Турнир %d не найден", id))
		return
	}
	if err != nil {
		s.serverError(w, "tournament", err)
		return
	}
	results, err := s.store.TournamentResults(id)
	if err != nil {
		s.serverError(w, "tournament results", err)
		return
	}
	if len(results) == 0 {
		s.fail(w, formValues{Types: store.AllTournaments},
			fmt.Sprintf("Повопросные результаты для турнира %d недоступны", id))
		return
	}
	summary := scoring.Summarize(results)
	view := tournamentPage{
		page:      s.chrome(),
		ID:        tournament.ID,
		Name:      tournament.Name,
		Date:      day(tournament.DateStart),
		Discarded: summary.Discarded,
		Questions: summary.Questions,
	}
	for _, team := range summary.Teams {
		row := teamRow{
			TeamID: team.TeamID, TeamName: team.TeamName, Rating: team.Rating, Taken: team.Taken,
			AvgRating: scoring.Format(team.AvgRating), ByCategory: team.ByCategory,
		}
		if team.Taken == 0 {
			row.AvgRating = "0" // no questions taken, so there is no average to write
		}
		view.Teams = append(view.Teams, row)
	}
	view.TrueDL = s.trueDL(tournament, results)
	s.render(w, "tournament", view)
}

// Best effort: a tournament whose teams are missing from the rating shows no score.
func (s *Server) trueDL(tournament store.TournamentDetail, results []scoring.TeamResult) *trueDL {
	teamIDs := make([]int, len(results))
	for i, r := range results {
		teamIDs[i] = r.TeamID
	}
	places, err := s.store.RatingPlaces(tournament.DateStart, teamIDs)
	if err != nil {
		log.Printf("rating places for tournament %d: %v", tournament.ID, err)
		return nil
	}
	score, err := scoring.TrueDL(results, places, tournament.QuestionsByTour)
	if err != nil {
		log.Printf("truedl for tournament %d: %v", tournament.ID, err)
		return nil
	}
	if score.Rated == 0 {
		return &trueDL{Overall: "0"} // nothing was rated, so there is nothing to average
	}
	parts := make([]string, len(score.ByTour))
	for i, v := range score.ByTour {
		parts[i] = fmt.Sprintf("%d — %s", i+1, scoring.Format(v))
	}
	return &trueDL{Overall: scoring.Format(score.Overall), ByTour: strings.Join(parts, ", ")}
}

type listingRow struct {
	ID         int
	Name       string
	DateStart  string
	DateEnd    string
	Type       string
	Bucket     string
	HasResults bool
	Editors    []store.Editor
}

type tournamentsPage struct {
	page
	Page         int
	PreviousPage int
	NextPage     int
	TotalPages   int
	Rows         []listingRow
}

func (s *Server) tournaments(w http.ResponseWriter, r *http.Request) {
	pageNumber := 1
	if parsed, err := strconv.Atoi(r.URL.Query().Get("page")); err == nil {
		pageNumber = parsed
	}
	if pageNumber < 0 {
		s.fail(w, formValues{Types: store.AllTournaments}, "Неверный номер страницы")
		return
	}
	cutoff := s.now().AddDate(0, 0, 7).Format(time.DateOnly)
	horizon := s.now().AddDate(1, 0, 0).Format(time.DateOnly)
	listing, err := s.store.Tournaments(pageNumber, cutoff, horizon, tournamentsPerPage)
	if err != nil {
		s.serverError(w, "tournaments", err)
		return
	}
	view := tournamentsPage{
		page:         s.chrome(),
		Page:         pageNumber,
		PreviousPage: pageNumber - 1,
		NextPage:     pageNumber + 1,
		TotalPages:   listing.TotalPages,
	}
	for _, row := range listing.Rows {
		view.Rows = append(view.Rows, listingRow{
			ID: row.ID, Name: row.Name, DateStart: day(row.DateStart), DateEnd: day(row.DateEnd),
			Type: kind(row.Type), Bucket: bucket(row.Type), HasResults: row.HasResults, Editors: row.Editors,
		})
	}
	s.render(w, "tournaments", view)
}

type handshakeResult struct {
	First         playerRef
	Second        playerRef
	Direct        bool
	Same          bool
	Intermediates []playerRef
}

type handshakesPage struct {
	page
	PlayerID1 string
	PlayerID2 string
	Result    *handshakeResult
}

func (s *Server) handshakesForm(w http.ResponseWriter, r *http.Request) {
	s.render(w, "handshakes", handshakesPage{page: s.chrome()})
}

func (s *Server) handshakes(w http.ResponseWriter, r *http.Request) {
	view := handshakesPage{
		PlayerID1: r.FormValue("player_id1"),
		PlayerID2: r.FormValue("player_id2"),
	}
	playerID1, ok1 := parseID(view.PlayerID1)
	playerID2, ok2 := parseID(view.PlayerID2)
	if !ok1 || !ok2 {
		view.page = s.chrome("Оба ID должны быть валидны")
		s.render(w, "handshakes", view)
		return
	}
	var missing []string
	for _, id := range []int{playerID1, playerID2} {
		if !s.graph.Has(id) {
			missing = append(missing, strconv.Itoa(id))
		}
	}
	if len(missing) > 0 {
		view.page = s.chrome(fmt.Sprintf("Игрок(и) %s не найдены", strings.Join(missing, ",")))
		s.render(w, "handshakes", view)
		return
	}
	path := s.graph.Path(playerID1, playerID2)
	names, err := s.store.PlayerNames(append(path, playerID1, playerID2))
	if err != nil {
		s.serverError(w, "player names", err)
		return
	}
	empty := formValues{DateFrom: earliestDate, DateTo: s.today(), Types: store.AllTournaments}
	result := &handshakeResult{
		First:  playerRef{PlayerID: playerID1, Name: name(names, playerID1), StatsLink: statsLink(playerID1, empty)},
		Second: playerRef{PlayerID: playerID2, Name: name(names, playerID2), StatsLink: statsLink(playerID2, empty)},
		Direct: len(path) == 2,
		Same:   len(path) == 1,
	}
	if len(path) > 2 {
		for _, id := range path[1 : len(path)-1] {
			result.Intermediates = append(result.Intermediates, playerRef{
				PlayerID: id, Name: name(names, id), StatsLink: statsLink(id, empty),
			})
		}
	}
	view.page = s.chrome()
	view.Result = result
	s.render(w, "handshakes", view)
}

func (s *Server) serverError(w http.ResponseWriter, what string, err error) {
	log.Printf("%s: %v", what, err)
	http.Error(w, "Что-то пошло не так", http.StatusInternalServerError)
}

func validate(playerID, dateFrom, dateTo string, datesRequired bool) (int, []string) {
	var problems []string
	id, ok := parseID(playerID)
	if !ok {
		problems = append(problems, "Нужно ввести валидный id игрока")
	}
	if !validDate(dateFrom, datesRequired) {
		problems = append(problems, "Введённая стартовая дата невалидна, правильный формат — 1990-01-01")
	}
	if !validDate(dateTo, datesRequired) {
		problems = append(problems, "Введённая конечная дата невалидна, правильный формат — 2024-01-01")
	}
	return id, problems
}

func parseID(raw string) (int, bool) {
	id, err := strconv.Atoi(strings.TrimSpace(raw))
	return id, err == nil && id != 0
}

func validDate(raw string, required bool) bool {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return !required
	}
	_, err := time.Parse(time.DateOnly, raw)
	return err == nil
}

func merge(first, second []string) []string {
	for _, problem := range second {
		if !slices.Contains(first, problem) {
			first = append(first, problem)
		}
	}
	return first
}

func statsLink(playerID int, form formValues) string {
	query := url.Values{
		"player_id":        {strconv.Itoa(playerID)},
		"date_from":        {form.DateFrom},
		"date_to":          {form.DateTo},
		"tournament_types": {string(form.Types)},
	}
	return "/stats?" + query.Encode()
}

func togetherLink(playerID1, playerID2 int, form formValues) string {
	query := url.Values{
		"id1":              {strconv.Itoa(playerID1)},
		"id2":              {strconv.Itoa(playerID2)},
		"date_from":        {form.DateFrom},
		"date_to":          {form.DateTo},
		"tournament_types": {string(form.Types)},
	}
	return "/together?" + query.Encode()
}

func name(names map[int]string, playerID int) string {
	if found, ok := names[playerID]; ok {
		return found
	}
	return notFoundName
}

func games(count int) string {
	suffix := ""
	text := strconv.Itoa(count)
	switch {
	case strings.HasSuffix(text, "11"), strings.HasSuffix(text, "12"),
		strings.HasSuffix(text, "13"), strings.HasSuffix(text, "14"):
	case strings.HasSuffix(text, "2"), strings.HasSuffix(text, "3"), strings.HasSuffix(text, "4"):
		suffix = "ы"
	case strings.HasSuffix(text, "1"):
		suffix = "а"
	}
	return fmt.Sprintf("%d игр%s", count, suffix)
}

func dateRange(start, end string) string {
	if day(start) == day(end) {
		return day(start)
	}
	return day(start) + "–" + day(end)
}

func day(stamp string) string {
	if stamp == "" {
		return "-"
	}
	date, _, _ := strings.Cut(stamp, "T")
	return date
}

func kind(tournamentType string) string {
	if tournamentType == "" {
		return "обычный"
	}
	return tournamentType
}

// The site has eight types; everything not synchronous or asynchronous belongs in the
// ordinary box rather than in none of them.
func bucket(tournamentType string) string {
	switch tournamentType {
	case "Синхрон":
		return "synchron"
	case "Асинхрон":
		return "asynchron"
	default:
		return "obychny"
	}
}

func or(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}
