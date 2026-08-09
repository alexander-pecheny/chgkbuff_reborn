// Command buff serves the Buff web pages from the mirror update_db.py maintains.
package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/graph"
	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/store"
	"code.pecheny.me/pecheny/chgkbuff_reborn/internal/web"
)

func main() {
	dir := flag.String("dir", ".", "directory holding buff.db and graph.bin")
	addr := flag.String("addr", "127.0.0.1:8080", "address to listen on")
	flag.Parse()

	log.SetFlags(log.LstdFlags | log.LUTC)

	db, err := store.Open(filepath.Join(*dir, "buff.db"))
	if err != nil {
		log.Fatalf("open database: %v", err)
	}
	defer db.Close()

	// The mapping does not follow a rebuild, so the nightly cron restarts this process.
	players, err := graph.Open(filepath.Join(*dir, "graph.bin"))
	if err != nil {
		log.Fatalf("open graph: %v", err)
	}
	defer players.Close()

	handler, err := web.New(db, players)
	if err != nil {
		log.Fatalf("build server: %v", err)
	}
	server := &http.Server{
		Addr:              *addr,
		Handler:           handler.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	drained := make(chan struct{})
	go func() {
		defer close(drained)
		<-stop
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			log.Printf("shutdown: %v", err)
		}
	}()

	log.Printf("listening on %s", *addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("serve: %v", err)
	}
	// In-flight requests still read the mapped graph, so unmap nothing until Shutdown ends.
	<-drained
}
