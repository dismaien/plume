package voicememos

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

type fakeRunner struct {
	mu    sync.Mutex
	calls [][]string
	block chan struct{}
}

func (r *fakeRunner) Run(_ context.Context, name string, args ...string) ([]byte, error) {
	r.mu.Lock()
	r.calls = append(r.calls, append([]string{name}, args...))
	call := len(r.calls)
	r.mu.Unlock()

	if r.block != nil {
		<-r.block
	}
	if call == 1 {
		reportPath := argumentValue(args, "--report")
		data, _ := json.Marshal(syncReport{CopiedAudio: []string{"20261011 204855.m4a"}})
		return nil, os.WriteFile(reportPath, data, 0600)
	}
	statePath := argumentValue(args, "--state")
	return nil, os.WriteFile(statePath, []byte(`{"jobs":[{"id":"archive-test"}]}`), 0600)
}

func TestSyncAndStartRunsArchiveThenImporter(t *testing.T) {
	root := t.TempDir()
	runner := &fakeRunner{}
	service := newService(testOptions(t, root), runner)

	result, err := service.SyncAndStart(context.Background(), "http://127.0.0.1:1234/api/v1")
	if err != nil {
		t.Fatalf("SyncAndStart: %v", err)
	}
	if result.CopiedFiles != 1 || result.QueuedJobs != 1 || len(result.JobIDs) != 1 || result.JobIDs[0] != "archive-test" {
		t.Fatalf("unexpected result: %#v", result)
	}
	if len(runner.calls) != 2 {
		t.Fatalf("expected two script calls, got %d", len(runner.calls))
	}
	if got := filepath.Base(runner.calls[0][1]); got != "sync-apple-voice-memos-archive.mjs" {
		t.Fatalf("first script = %q", got)
	}
	if got := filepath.Base(runner.calls[1][1]); got != "import-voice-memo-archive.mjs" {
		t.Fatalf("second script = %q", got)
	}
}

func TestSyncAndStartRejectsOverlappingRun(t *testing.T) {
	root := t.TempDir()
	runner := &fakeRunner{block: make(chan struct{})}
	service := newService(testOptions(t, root), runner)
	done := make(chan error, 1)
	go func() {
		_, err := service.SyncAndStart(context.Background(), "http://127.0.0.1:1234/api/v1")
		done <- err
	}()

	deadline := time.Now().Add(time.Second)
	for {
		service.mu.Lock()
		running := service.running
		service.mu.Unlock()
		if running {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("first sync did not start")
		}
		time.Sleep(time.Millisecond)
	}

	if _, err := service.SyncAndStart(context.Background(), "http://127.0.0.1:1234/api/v1"); err != ErrSyncInProgress {
		t.Fatalf("expected ErrSyncInProgress, got %v", err)
	}
	close(runner.block)
	if err := <-done; err != nil {
		t.Fatalf("first sync failed: %v", err)
	}
}

func TestCommandErrorExplainsVoiceMemosPermission(t *testing.T) {
	err := commandError("archive sync", context.Canceled, []byte("EPERM: operation not permitted, scandir '/Users/test/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings'"))
	if got := err.Error(); got != "macOS denied Plume access to Apple Voice Memos. Give Plume Full Disk Access in System Settings > Privacy & Security > Full Disk Access, then click Sync Memos again" {
		t.Fatalf("unexpected permission error: %q", got)
	}
}

func testOptions(t *testing.T, root string) Options {
	t.Helper()
	repositoryRoot := filepath.Join(root, "repo")
	liveRoot := filepath.Join(root, "live")
	archiveRoot := filepath.Join(root, "archive")
	reportsRoot := filepath.Join(root, "reports")
	for _, dir := range []string{filepath.Join(repositoryRoot, "scripts"), liveRoot, archiveRoot, reportsRoot} {
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
	}
	for _, script := range []string{"sync-apple-voice-memos-archive.mjs", "import-voice-memo-archive.mjs"} {
		if err := os.WriteFile(filepath.Join(repositoryRoot, "scripts", script), []byte("// test"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	databasePath := filepath.Join(root, "quill.db")
	if err := os.WriteFile(databasePath, nil, 0600); err != nil {
		t.Fatal(err)
	}
	return Options{
		RepositoryRoot:  repositoryRoot,
		LiveRoot:        liveRoot,
		ArchiveRoot:     archiveRoot,
		ReportsRoot:     reportsRoot,
		DatabasePath:    databasePath,
		PlaybackLinkDir: filepath.Join(root, "links"),
		NodeBinary:      "/test/node",
	}
}

func argumentValue(args []string, name string) string {
	for index := 0; index+1 < len(args); index++ {
		if args[index] == name {
			return args[index+1]
		}
	}
	return ""
}
