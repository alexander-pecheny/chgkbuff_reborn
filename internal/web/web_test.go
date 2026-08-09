package web_test

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/graph"
	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/store"
	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/web"
)

func newServer(t *testing.T) http.Handler {
	t.Helper()
	root := filepath.Join("..", "..")
	for _, needed := range []string{"buff.db", "graph.bin"} {
		if _, err := os.Stat(filepath.Join(root, needed)); err != nil {
			t.Skipf("%s not present", needed)
		}
	}
	db, err := store.Open(filepath.Join(root, "buff.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	players, err := graph.Open(filepath.Join(root, "graph.bin"))
	if err != nil {
		t.Fatalf("open graph: %v", err)
	}
	t.Cleanup(func() { players.Close() })
	server, err := web.New(db, players)
	if err != nil {
		t.Fatalf("build server: %v", err)
	}
	return server.Handler()
}

func get(t *testing.T, handler http.Handler, target string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, target, nil))
	return w
}

func TestPagesRender(t *testing.T) {
	handler := newServer(t)
	pages := []struct {
		target string
		want   string
	}{
		{"/", "С кем вы играли чаще всего?"},
		{"/stats?player_id=30152&date_from=1990-01-01&date_to=2026-01-01&tournament_types=all_tournaments", "Статистика игрока"},
		{"/together?id1=30152&id2=13782&date_from=1990-01-01&date_to=2026-01-01", "Совместные игры игроков"},
		{"/tournament/12107", "TrueDL"},
		{"/tournaments?page=1", "Турниры"},
		{"/tournaments?page=0", "раньше"},
		{"/handshakes", "N рукопожатий"},
		{"/static/buff.css", "font-variant-numeric"},
	}
	for _, page := range pages {
		got := get(t, handler, page.target)
		if got.Code != http.StatusOK {
			t.Errorf("GET %s: status %d", page.target, got.Code)
			continue
		}
		if !strings.Contains(got.Body.String(), page.want) {
			t.Errorf("GET %s: body missing %q", page.target, page.want)
		}
	}
}

func TestBadInputStaysOnTheFormWithAMessage(t *testing.T) {
	handler := newServer(t)
	cases := []struct {
		target string
		want   string
	}{
		{"/stats?player_id=nonsense&date_from=1990-01-01&date_to=2026-01-01", "Нужно ввести валидный id игрока"},
		{"/stats?player_id=30152&date_from=вчера&date_to=2026-01-01", "стартовая дата невалидна"},
		{"/tournament/99999999", "не найден"},
		{"/tournaments?page=-1", "Неверный номер страницы"},
	}
	for _, c := range cases {
		got := get(t, handler, c.target)
		if got.Code != http.StatusOK {
			t.Errorf("GET %s: status %d, want 200 with a message", c.target, got.Code)
			continue
		}
		if !strings.Contains(got.Body.String(), c.want) {
			t.Errorf("GET %s: body missing %q", c.target, c.want)
		}
	}
}

// The listing filter matches a row's data-kind against the checkbox values, so a row whose
// bucket is not one of them silently disappears the moment anyone filters.
func TestEveryListedTournamentIsReachableByAFilter(t *testing.T) {
	handler := newServer(t)
	body := get(t, handler, "/tournaments?page=1").Body.String()
	buckets := map[string]bool{}
	for _, match := range regexp.MustCompile(`value="([a-z]+)" checked`).FindAllStringSubmatch(body, -1) {
		buckets[match[1]] = true
	}
	if len(buckets) != 3 {
		t.Fatalf("found %d filter checkboxes, want 3", len(buckets))
	}
	rows := regexp.MustCompile(`data-kind="([^"]*)"`).FindAllStringSubmatch(body, -1)
	if len(rows) == 0 {
		t.Fatal("no tournament rows on the page")
	}
	unreachable := map[string]int{}
	for _, row := range rows {
		if !buckets[row[1]] {
			unreachable[row[1]]++
		}
	}
	if len(unreachable) > 0 {
		t.Errorf("%d rows carry a kind no checkbox matches: %v", len(rows), unreachable)
	}
}

func TestUnparseablePageFallsBackToTheFirst(t *testing.T) {
	handler := newServer(t)
	got := get(t, handler, "/tournaments?page=abc")
	if got.Code != http.StatusOK || !strings.Contains(got.Body.String(), "data-kind") {
		t.Errorf("status %d without a listing, want page 1", got.Code)
	}
}

func TestSearchRedirectsToStats(t *testing.T) {
	handler := newServer(t)
	form := url.Values{"player_id": {"30152"}, "date_from": {""}, "date_to": {"2026-01-01"},
		"tournament_types": {"lan_sync"}}
	request := httptest.NewRequest(http.MethodPost, "/", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	if w.Code != http.StatusFound {
		t.Fatalf("status %d, want %d", w.Code, http.StatusFound)
	}
	location := w.Header().Get("Location")
	for _, want := range []string{"player_id=30152", "date_from=1990-01-01", "tournament_types=lan_sync"} {
		if !strings.Contains(location, want) {
			t.Errorf("redirect %q missing %q", location, want)
		}
	}
}

func TestHandshakesReportsThePath(t *testing.T) {
	handler := newServer(t)
	form := url.Values{"player_id1": {"1"}, "player_id2": {"30152"}}
	request := httptest.NewRequest(http.MethodPost, "/handshakes", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d", w.Code)
	}
	body := w.Body.String()
	if !strings.Contains(body, "Кратчайший путь между игроками") {
		t.Error("no path heading")
	}
	if got := strings.Count(body, "<li>"); got != 3 {
		t.Errorf("%d intermediate players, want 3", got)
	}
}

func TestHandshakesWithOneselfDoesNotClaimNoPath(t *testing.T) {
	handler := newServer(t)
	form := url.Values{"player_id1": {"30152"}, "player_id2": {"30152"}}
	request := httptest.NewRequest(http.MethodPost, "/handshakes", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	if strings.Contains(w.Body.String(), "Путь не найден") {
		t.Error("a player is told there is no path to himself")
	}
}
