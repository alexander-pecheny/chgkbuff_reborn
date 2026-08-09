// Package graph answers handshake questions over the player adjacency built by
// create_graph.py. The file is mmapped rather than read, so the adjacency stays
// file-backed instead of sitting on the heap.
package graph

import (
	"encoding/binary"
	"fmt"
	"os"
	"sort"
	"syscall"
)

const (
	magic      = "BUFFGRPH"
	version    = 1
	headerSize = len(magic) + 3*4
)

type Graph struct {
	data       []byte
	nodes      int
	arcs       int
	ids        int // byte offsets into data
	offsets    int
	neighbours int
}

func Open(path string) (*Graph, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if info.Size() < int64(headerSize) {
		return nil, fmt.Errorf("%s: too short to be a graph", path)
	}
	data, err := syscall.Mmap(int(f.Fd()), 0, int(info.Size()), syscall.PROT_READ, syscall.MAP_SHARED)
	if err != nil {
		return nil, fmt.Errorf("mmap %s: %w", path, err)
	}
	if string(data[:len(magic)]) != magic {
		syscall.Munmap(data)
		return nil, fmt.Errorf("%s: not a graph file", path)
	}
	if v := binary.LittleEndian.Uint32(data[8:]); v != version {
		syscall.Munmap(data)
		return nil, fmt.Errorf("%s: graph version %d, want %d", path, v, version)
	}
	g := &Graph{
		data:  data,
		nodes: int(binary.LittleEndian.Uint32(data[12:])),
		arcs:  int(binary.LittleEndian.Uint32(data[16:])),
	}
	g.ids = headerSize
	g.offsets = g.ids + 4*g.nodes
	g.neighbours = g.offsets + 4*(g.nodes+1)
	if want := int64(g.neighbours + 4*g.arcs); want != info.Size() {
		syscall.Munmap(data)
		return nil, fmt.Errorf("%s: size %d, want %d for %d players", path, info.Size(), want, g.nodes)
	}
	return g, nil
}

func (g *Graph) Close() error {
	if g.data == nil {
		return nil
	}
	data := g.data
	g.data = nil
	return syscall.Munmap(data)
}

func (g *Graph) word(base, i int) int {
	return int(int32(binary.LittleEndian.Uint32(g.data[base+4*i:])))
}

func (g *Graph) index(playerID int) int {
	i := sort.Search(g.nodes, func(i int) bool { return g.word(g.ids, i) >= playerID })
	if i < g.nodes && g.word(g.ids, i) == playerID {
		return i
	}
	return -1
}

func (g *Graph) Has(playerID int) bool {
	return g.index(playerID) >= 0
}

func (g *Graph) AreTeammates(a, b int) bool {
	ia, ib := g.index(a), g.index(b)
	if ia < 0 || ib < 0 {
		return false
	}
	for k := g.word(g.offsets, ia); k < g.word(g.offsets, ia+1); k++ {
		if g.word(g.neighbours, k) == ib {
			return true
		}
	}
	return false
}

// Both ends included, nil when unknown or unreachable. Many chains of the same length
// usually exist; which one comes back is not defined.
func (g *Graph) Path(from, to int) []int {
	start, end := g.index(from), g.index(to)
	if start < 0 || end < 0 {
		return nil
	}
	if start == end {
		return []int{from}
	}
	seenFrom := map[int]int{start: -1}
	seenTo := map[int]int{end: -1}
	frontFrom, frontTo := []int{start}, []int{end}
	for len(frontFrom) > 0 && len(frontTo) > 0 {
		var meeting int
		if len(frontFrom) <= len(frontTo) {
			frontFrom, meeting = g.expand(frontFrom, seenFrom, seenTo)
		} else {
			frontTo, meeting = g.expand(frontTo, seenTo, seenFrom)
		}
		if meeting >= 0 {
			return g.join(meeting, seenFrom, seenTo)
		}
	}
	return nil
}

func (g *Graph) expand(frontier []int, seen, other map[int]int) ([]int, int) {
	next := make([]int, 0, len(frontier))
	for _, node := range frontier {
		for k := g.word(g.offsets, node); k < g.word(g.offsets, node+1); k++ {
			neighbour := g.word(g.neighbours, k)
			if _, ok := seen[neighbour]; ok {
				continue
			}
			seen[neighbour] = node
			if _, ok := other[neighbour]; ok {
				return next, neighbour
			}
			next = append(next, neighbour)
		}
	}
	return next, -1
}

func (g *Graph) join(meeting int, seenFrom, seenTo map[int]int) []int {
	var head []int
	for node := meeting; node >= 0; node = seenFrom[node] {
		head = append(head, node)
	}
	for i, j := 0, len(head)-1; i < j; i, j = i+1, j-1 {
		head[i], head[j] = head[j], head[i]
	}
	for node := seenTo[meeting]; node >= 0; node = seenTo[node] {
		head = append(head, node)
	}
	path := make([]int, len(head))
	for i, node := range head {
		path[i] = g.word(g.ids, node)
	}
	return path
}
