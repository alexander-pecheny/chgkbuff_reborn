// Package scoring turns a tournament's per-question masks into the numbers the tournament
// page shows: how hard each question turned out to be, how each team did against that, and
// the tournament's TrueDL.
package scoring

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

type TeamResult struct {
	TeamID   int
	TeamName string
	Mask     string
	InRating bool
}

// Coffin is a question no team took; the rest split into even fifths, and ByCategory drops the coffin.
const (
	Coffin = iota
	bandA
	bandB
	bandC
	bandD
	bandE
	bands
)

type TeamStanding struct {
	TeamID     int
	TeamName   string
	Rating     int
	Taken      int
	AvgRating  float64
	ByCategory [5]int
}

type Summary struct {
	Teams     []TeamStanding
	Questions [bands]int
	Discarded int
}

func band(share float64) int {
	switch {
	case share == 0:
		return Coffin
	case share <= 0.2:
		return bandA
	case share <= 0.4:
		return bandB
	case share <= 0.6:
		return bandC
	case share <= 0.8:
		return bandD
	default:
		return bandE
	}
}

func Summarize(results []TeamResult) Summary {
	var summary Summary
	if len(results) == 0 {
		return summary
	}
	summary.Discarded = strings.Count(results[0].Mask, "X")
	standings := make([]TeamStanding, len(results))
	for i, r := range results {
		standings[i] = TeamStanding{TeamID: r.TeamID, TeamName: r.TeamName, Taken: strings.Count(r.Mask, "1")}
	}
	for q := range len(results[0].Mask) {
		var takers []int
		for i, r := range results {
			if q < len(r.Mask) && r.Mask[q] == '1' {
				takers = append(takers, i)
			}
		}
		band := band(float64(len(takers)) / float64(len(results)))
		summary.Questions[band]++
		worth := len(results) - len(takers) + 1
		for _, i := range takers {
			standings[i].Rating += worth
			if band != Coffin {
				standings[i].ByCategory[band-1]++
			}
		}
	}
	for i := range standings {
		standings[i].AvgRating = round(safeDiv(float64(standings[i].Rating), float64(standings[i].Taken)), 2)
	}
	sort.SliceStable(standings, func(i, j int) bool {
		if standings[i].Taken != standings[j].Taken {
			return standings[i].Taken > standings[j].Taken
		}
		return standings[i].Rating > standings[j].Rating
	})
	summary.Teams = standings
	return summary
}

// The better a team stands, the more it was expected to take, so its result is discounted more.
var truedlCoefficients = []struct {
	upToPlace int
	coeff     float64
}{
	{10, 1.61}, {25, 1.52}, {50, 1.43}, {100, 1.32}, {250, 1.16},
	{500, 1.0}, {1000, 0.81}, {2000, 0.6}, {3000, 0.43}, {5000, 0.31},
}

func coefficient(place float64) (float64, error) {
	if place >= 5000 {
		return 0, fmt.Errorf("place %v is outside the TrueDL table", place)
	}
	for _, c := range truedlCoefficients {
		if float64(c.upToPlace) >= place {
			return c.coeff, nil
		}
	}
	return 0, fmt.Errorf("place %v is outside the TrueDL table", place)
}

func maskTrueDL(mask string, place float64) (float64, error) {
	coeff, err := coefficient(place)
	if err != nil {
		return 0, err
	}
	taken := float64(strings.Count(mask, "1"))
	total := float64(len(mask) - strings.Count(mask, "X"))
	if total == 0 {
		return 0, fmt.Errorf("every question withdrawn")
	}
	return round((1-math.Min(taken/coeff, total)/total)*10, 1), nil
}

type Score struct {
	Overall float64
	ByTour  []float64
	Rated   int
}

func TrueDL(results []TeamResult, places map[int]float64, questionsByTour string) (Score, error) {
	tours, err := parseTours(questionsByTour)
	if err != nil {
		return Score{}, err
	}
	var overall []float64
	byTour := make([][]float64, len(tours))
	for _, r := range results {
		place, rated := places[r.TeamID]
		if !r.InRating || !rated {
			continue
		}
		teamTrueDL, err := maskTrueDL(r.Mask, place)
		if err != nil {
			return Score{}, err
		}
		overall = append(overall, teamTrueDL)
		start := 0
		for i, length := range tours {
			from, to := min(start, len(r.Mask)), min(start+length, len(r.Mask))
			tourTrueDL, err := maskTrueDL(r.Mask[from:to], place)
			if err != nil {
				return Score{}, err
			}
			byTour[i] = append(byTour[i], tourTrueDL)
			start += length
		}
	}
	averages := make([]float64, 0, len(byTour))
	for _, tour := range byTour {
		if len(tour) == 0 {
			continue
		}
		averages = append(averages, round(mean(tour), 1))
	}
	return Score{Overall: round(mean(overall), 1), ByTour: averages, Rated: len(overall)}, nil
}

func parseTours(questionsByTour string) ([]int, error) {
	var tours []int
	for _, field := range strings.Split(questionsByTour, ",") {
		length, err := strconv.Atoi(strings.TrimSpace(field))
		if err != nil {
			return nil, fmt.Errorf("questions by tour %q: %w", questionsByTour, err)
		}
		tours = append(tours, length)
	}
	return tours, nil
}

// Neumaier compensation, matching CPython's sum(): plain accumulation drifts a bit and
// flips averages that land on a .x5 tie.
func mean(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	var sum, compensation float64
	for _, v := range values {
		t := sum + v
		if math.Abs(sum) >= math.Abs(v) {
			compensation += (sum - t) + v
		} else {
			compensation += (v - t) + sum
		}
		sum = t
	}
	return (sum + compensation) / float64(len(values))
}

func safeDiv(a, b float64) float64 {
	if b == 0 {
		return 0
	}
	return a / b
}

// Shortest form but never bare, so four reads "4.0".
func Format(value float64) string {
	text := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

// Python's round(): rounds what the double actually holds, ties to even. Scaling by ten
// instead misrounds whenever the scaling itself lands on a tie.
func round(value float64, places int) float64 {
	rounded, err := strconv.ParseFloat(strconv.FormatFloat(value, 'f', places, 64), 64)
	if err != nil {
		return value
	}
	return rounded
}
