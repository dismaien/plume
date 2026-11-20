// stage-speaker-candidate builds a speaker-only alternative without replacing an
// accepted transcript. --stage places it in Plume's existing revision review UI.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"quill/internal/models"
	"quill/internal/repository"
	"quill/internal/transcription"
	"quill/internal/transcription/interfaces"
)

type diarizationArtifact struct {
	AudioFile       string                          `json:"audio_file"`
	Model           string                          `json:"model"`
	Segments        []interfaces.DiarizationSegment `json:"segments"`
	RegularSegments []interfaces.DiarizationSegment `json:"regular_segments"`
	Speakers        []string                        `json:"speakers"`
	SpeakerCount    int                             `json:"speaker_count"`
	Metadata        map[string]string               `json:"metadata"`
}

func candidateTranscript(accepted string, artifact []byte) (string, int, error) {
	var snapshot struct {
		interfaces.TranscriptResult
		ManuallyEdited bool `json:"manually_edited"`
	}
	if err := json.Unmarshal([]byte(accepted), &snapshot); err != nil {
		return "", 0, fmt.Errorf("accepted transcript: %w", err)
	}
	if snapshot.ManuallyEdited {
		return "", 0, errors.New("accepted transcript has manual corrections; its edited passages cannot be rebuilt from machine timings")
	}
	base := snapshot.TranscriptResult
	if len(base.WordSegments) == 0 || strings.TrimSpace(base.Text) == "" {
		return "", 0, errors.New("accepted transcript has no timed words or text")
	}
	var source diarizationArtifact
	if err := json.Unmarshal(artifact, &source); err != nil {
		return "", 0, fmt.Errorf("diarization artifact: %w", err)
	}
	if source.Model != "pyannote/speaker-diarization-community-1" || source.SpeakerCount != 2 || len(source.Speakers) != 2 || len(source.Segments) == 0 || len(source.RegularSegments) == 0 {
		return "", 0, errors.New("expected a two-speaker Community-1 result with both timelines")
	}
	var constraints map[string]int
	if json.Unmarshal([]byte(source.Metadata["speaker_constraints"]), &constraints) != nil || constraints["min_speakers"] != 2 || constraints["max_speakers"] != 2 {
		return "", 0, errors.New("diarization result lacks exact-two constraint provenance")
	}
	if source.Metadata["diarization_device"] != "mps" || source.Metadata["mps_fallback_enabled"] != "0" {
		return "", 0, errors.New("diarization did not run on MPS without fallback")
	}
	if base.Metadata == nil {
		base.Metadata = map[string]string{}
	}
	for key := range base.Metadata {
		if strings.HasPrefix(key, "word_refinement_") || key == "speaker_review_start" || key == "speaker_review_reason" {
			delete(base.Metadata, key)
		}
	}
	source.Metadata["speaker_identity_status"] = "uncertain"
	source.Metadata["speaker_identity_reason"] = "exact_two_speakers_requires_audio_review"
	diar := interfaces.DiarizationResult{
		Segments: source.Segments, RegularSegments: source.RegularSegments,
		SpeakerCount: source.SpeakerCount, Speakers: source.Speakers,
		ModelUsed: source.Model, Metadata: source.Metadata,
	}
	candidate := transcription.AssembleDiarizedTranscript(&base, &diar)
	candidate.Metadata["speaker_review_reason"] = "exact_two_speakers_requires_audio_review"
	if candidate.Text != base.Text || len(candidate.WordSegments) != len(base.WordSegments) {
		return "", 0, errors.New("candidate changed accepted text or word count")
	}
	unattributed := 0
	allowed := map[string]bool{source.Speakers[0]: true, source.Speakers[1]: true}
	for i, word := range candidate.WordSegments {
		original := base.WordSegments[i]
		if word.Start != original.Start || word.End != original.End || word.Word != original.Word || word.Score != original.Score {
			return "", 0, fmt.Errorf("candidate changed word %d text or timing", i)
		}
		if word.Speaker == nil {
			unattributed++
		} else if !allowed[*word.Speaker] {
			return "", 0, fmt.Errorf("candidate introduced unknown identity %q", *word.Speaker)
		}
	}
	raw, err := json.Marshal(candidate)
	if err != nil {
		return "", 0, err
	}
	return string(raw), unattributed, nil
}

func main() {
	jobID := flag.String("job", "", "completed transcription job ID")
	dbPath := flag.String("db", "", "Plume SQLite database")
	artifactPath := flag.String("diarization", "", "offline Community-1 result.json")
	outputPath := flag.String("output", "", "immutable candidate JSON output")
	expectedSHA := flag.String("expect-transcript-sha256", "", "accepted transcript fingerprint, required for --stage")
	stage := flag.Bool("stage", false, "stage candidate in review history without replacing accepted transcript")
	flag.Parse()
	if *jobID == "" || *dbPath == "" || *artifactPath == "" || *outputPath == "" || (*stage && *expectedSHA == "") {
		flag.Usage()
		os.Exit(2)
	}
	if err := run(*jobID, *dbPath, *artifactPath, *outputPath, *expectedSHA, *stage); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(jobID, dbPath, artifactPath, outputPath, expectedSHA string, stage bool) error {
	db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
	if err != nil {
		return err
	}
	sqlDB, err := db.DB()
	if err != nil {
		return err
	}
	defer sqlDB.Close()
	var job models.TranscriptionJob
	if err := db.First(&job, "id = ?", jobID).Error; err != nil {
		return err
	}
	if job.Status != models.StatusCompleted || job.Transcript == nil || job.CandidateTranscriptRevisionID != nil {
		return errors.New("job must be completed with accepted text and no existing candidate")
	}
	acceptedSHA := fmt.Sprintf("%x", sha256.Sum256([]byte(*job.Transcript)))
	if expectedSHA != "" && expectedSHA != acceptedSHA {
		return fmt.Errorf("accepted transcript changed: got %s", acceptedSHA)
	}
	artifact, err := os.ReadFile(artifactPath)
	if err != nil {
		return err
	}
	candidate, unknown, err := candidateTranscript(*job.Transcript, artifact)
	if err != nil {
		return err
	}
	if existing, err := os.ReadFile(outputPath); err == nil {
		if string(existing) != candidate {
			return errors.New("immutable candidate output already differs")
		}
	} else if os.IsNotExist(err) {
		file, createErr := os.OpenFile(outputPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if createErr != nil {
			return createErr
		}
		if _, createErr = file.WriteString(candidate); createErr != nil {
			file.Close()
			return createErr
		}
		if createErr = file.Close(); createErr != nil {
			return createErr
		}
	} else {
		return err
	}
	if stage {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
			var active int64
			if err := tx.Model(&models.TranscriptionJob{}).Where("status IN ?", []models.JobStatus{models.StatusPending, models.StatusProcessing}).Count(&active).Error; err != nil {
				return err
			}
			if active != 0 {
				return fmt.Errorf("%d Plume jobs are active", active)
			}
			var current models.TranscriptionJob
			if err := tx.First(&current, "id = ?", jobID).Error; err != nil {
				return err
			}
			if current.Transcript == nil || current.CandidateTranscriptRevisionID != nil || fmt.Sprintf("%x", sha256.Sum256([]byte(*current.Transcript))) != acceptedSHA {
				return errors.New("accepted transcript or candidate state changed before staging")
			}
			return repository.NewJobRepository(tx).UpdateTranscript(ctx, jobID, candidate, 0)
		}); err != nil {
			return err
		}
	}
	fmt.Printf("accepted_sha256=%s candidate_sha256=%x unattributed_words=%d staged=%t\n", acceptedSHA, sha256.Sum256([]byte(candidate)), unknown, stage)
	return nil
}
