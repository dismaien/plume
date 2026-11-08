package voicememos

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var ErrSyncInProgress = errors.New("voice memo sync is already running")

type commandRunner interface {
	Run(ctx context.Context, name string, args ...string) ([]byte, error)
}

type execRunner struct{}

func (execRunner) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	return exec.CommandContext(ctx, name, args...).CombinedOutput()
}

type Options struct {
	RepositoryRoot  string
	LiveRoot        string
	ArchiveRoot     string
	ReportsRoot     string
	DatabasePath    string
	PlaybackLinkDir string
	NodeBinary      string
}

type Service struct {
	options Options
	runner  commandRunner

	mu      sync.Mutex
	running bool
}

type Result struct {
	CopiedFiles     int      `json:"copied_files"`
	ReconciledFiles int      `json:"reconciled_files"`
	DuplicateFiles  int      `json:"duplicate_files"`
	QueuedJobs      int      `json:"queued_jobs"`
	JobIDs          []string `json:"job_ids"`
	ReportPath      string   `json:"report_path"`
}

type syncReport struct {
	CopiedAudio             []string `json:"copied_audio"`
	ReconciledAudio         []string `json:"reconciled_audio"`
	DuplicateContentSkipped []any    `json:"duplicate_content_skipped"`
}

type importState struct {
	Jobs []struct {
		ID string `json:"id"`
	} `json:"jobs"`
}

func NewService(databasePath string) *Service {
	home, _ := os.UserHomeDir()
	repositoryRoot := strings.TrimSpace(os.Getenv("PLUME_REPOSITORY_ROOT"))
	if repositoryRoot == "" {
		repositoryRoot = filepath.Join(home, "Documents", "Playground", "plume")
	}
	dataDir := filepath.Dir(databasePath)
	return newService(Options{
		RepositoryRoot:  repositoryRoot,
		LiveRoot:        filepath.Join(home, "Library", "Group Containers", "group.com.apple.VoiceMemos.shared", "Recordings"),
		ArchiveRoot:     filepath.Join(home, "Documents", "Voice Notes Archive", "10-raw-apple-voice-memos-sync-20260615"),
		ReportsRoot:     filepath.Join(home, "Documents", "Voice Notes Archive", "20-quill-processing", "archive-sync-reports"),
		DatabasePath:    databasePath,
		PlaybackLinkDir: filepath.Join(dataDir, "playback-links"),
	}, execRunner{})
}

func newService(options Options, runner commandRunner) *Service {
	return &Service{options: options, runner: runner}
}

func (s *Service) SyncAndStart(ctx context.Context, apiBase string) (*Result, error) {
	if !s.begin() {
		return nil, ErrSyncInProgress
	}
	defer s.end()

	node := strings.TrimSpace(s.options.NodeBinary)
	if node == "" {
		var err error
		node, err = exec.LookPath("node")
		if err != nil {
			return nil, fmt.Errorf("Node.js is required for the Voice Memos archive sync: %w", err)
		}
	}

	syncScript := filepath.Join(s.options.RepositoryRoot, "scripts", "sync-apple-voice-memos-archive.mjs")
	importScript := filepath.Join(s.options.RepositoryRoot, "scripts", "import-voice-memo-archive.mjs")
	for _, required := range []string{syncScript, importScript, s.options.LiveRoot, s.options.ArchiveRoot, s.options.ReportsRoot, s.options.DatabasePath} {
		if _, err := os.Stat(required); err != nil {
			return nil, fmt.Errorf("Voice Memos sync prerequisite is unavailable at %q: %w", required, err)
		}
	}

	stamp := time.Now().UTC().Format("2006-01-02T15-04-05.000Z")
	reportPath := filepath.Join(s.options.ReportsRoot, "archive-sync-"+stamp+".json")
	statePath := filepath.Join(s.options.ReportsRoot, "plume-import-archive-sync-"+stamp+".json")
	sinceDate := time.Now().AddDate(0, 0, -14).Format("20060102")

	output, err := s.runner.Run(ctx, node,
		syncScript,
		"--live-root", s.options.LiveRoot,
		"--archive-root", s.options.ArchiveRoot,
		"--reports-root", s.options.ReportsRoot,
		"--report", reportPath,
		"--since-date", sinceDate,
		"--minimum-free-gb", "12",
	)
	if err != nil {
		return nil, commandError("archive sync", err, output)
	}

	reportBytes, err := os.ReadFile(reportPath)
	if err != nil {
		return nil, fmt.Errorf("read Voice Memos sync report: %w", err)
	}
	var report syncReport
	if err := json.Unmarshal(reportBytes, &report); err != nil {
		return nil, fmt.Errorf("parse Voice Memos sync report: %w", err)
	}

	result := &Result{
		CopiedFiles:     len(report.CopiedAudio),
		ReconciledFiles: len(report.ReconciledAudio),
		DuplicateFiles:  len(report.DuplicateContentSkipped),
		ReportPath:      reportPath,
	}
	if result.CopiedFiles+result.ReconciledFiles == 0 {
		return result, nil
	}

	output, err = s.runner.Run(ctx, node,
		importScript,
		"--archive-root", s.options.ArchiveRoot,
		"--db", s.options.DatabasePath,
		"--playback-link-dir", s.options.PlaybackLinkDir,
		"--sync-report", reportPath,
		"--state", statePath,
		"--api-base", strings.TrimRight(apiBase, "/"),
		"--start",
	)
	if err != nil {
		return nil, commandError("Plume import", err, output)
	}

	stateBytes, err := os.ReadFile(statePath)
	if err != nil {
		return nil, fmt.Errorf("read Plume import state: %w", err)
	}
	var state importState
	if err := json.Unmarshal(stateBytes, &state); err != nil {
		return nil, fmt.Errorf("parse Plume import state: %w", err)
	}
	result.QueuedJobs = len(state.Jobs)
	result.JobIDs = make([]string, 0, len(state.Jobs))
	for _, job := range state.Jobs {
		if job.ID != "" {
			result.JobIDs = append(result.JobIDs, job.ID)
		}
	}
	return result, nil
}

func (s *Service) begin() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.running {
		return false
	}
	s.running = true
	return true
}

func (s *Service) end() {
	s.mu.Lock()
	s.running = false
	s.mu.Unlock()
}

func commandError(step string, err error, output []byte) error {
	message := strings.TrimSpace(string(output))
	if strings.Contains(message, "EPERM: operation not permitted") && strings.Contains(message, "group.com.apple.VoiceMemos.shared") {
		return errors.New("macOS denied Plume access to Apple Voice Memos. Give Plume Full Disk Access in System Settings > Privacy & Security > Full Disk Access, then click Sync Memos again")
	}
	if len(message) > 2000 {
		message = message[len(message)-2000:]
	}
	if message == "" {
		return fmt.Errorf("%s failed: %w", step, err)
	}
	return fmt.Errorf("%s failed: %w: %s", step, err, message)
}
