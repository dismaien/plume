package transcription

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"quill/internal/transcription/adapters"
	"quill/internal/transcription/interfaces"
	"quill/pkg/binaries"
)

const wordRefinementPolicy = "anchored_native_word_gaps_v4"

const wordRefinementRevision = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"

// RefineWithNativeWordRuntime exercises the full application stage without
// opening a database, for reproducible local preflight and scoring.
func RefineWithNativeWordRuntime(ctx context.Context, input interfaces.AudioInput, result *interfaces.TranscriptResult, runtimeRoot, outputDirectory string) (*interfaces.TranscriptResult, error) {
	if filepath.Base(filepath.Clean(runtimeRoot)) != "nemotron3" {
		return result, fmt.Errorf("expected named nemotron3 runtime directory")
	}
	u := &UnifiedTranscriptionService{envPath: filepath.Dir(runtimeRoot)}
	return u.refineNativeWords(ctx, input, result, interfaces.ProcessingContext{OutputDirectory: outputDirectory})
}

type wordRefinementProfile struct {
	Enabled  bool   `json:"enabled"`
	Revision string `json:"model_revision"`
	Policy   string `json:"policy"`
}

// refineNativeWords only processes fresh machine output in the job pipeline.
// Existing accepted results still pass through the immutable rerun-candidate
// store. No environment installation/download or person-name transfer happens.
func (u *UnifiedTranscriptionService) refineNativeWords(ctx context.Context, input interfaces.AudioInput, result *interfaces.TranscriptResult, proc interfaces.ProcessingContext) (*interfaces.TranscriptResult, error) {
	root := filepath.Join(u.envPath, "nemotron3")
	data, err := os.ReadFile(filepath.Join(root, "ready.json"))
	if os.IsNotExist(err) {
		return result, nil
	}
	if err != nil {
		return result, err
	}
	var profile wordRefinementProfile
	if err = json.Unmarshal(data, &profile); err != nil {
		return result, err
	}
	if !profile.Enabled {
		return result, nil
	}
	// Existing profiles pin the same model. The embedded scripts and Go
	// validator always apply the current policy to fresh outputs.
	if profile.Revision != wordRefinementRevision || (profile.Policy != wordRefinementPolicy && profile.Policy != "primary_mixed_track_local_support_v3" && profile.Policy != "primary_speakers_review_unmatched_v2" && profile.Policy != "known_machine_labels_v1") {
		return result, fmt.Errorf("unverified native word-refinement profile")
	}
	if result == nil || result.Diarization == nil || len(result.WordSegments) == 0 {
		return result, nil
	}
	if !strings.Contains(result.Diarization.ModelUsed, "speaker-diarization-community-1") {
		return result, nil
	}
	if err = adapters.InstallWordRefinementScripts(filepath.Join(root, "scripts")); err != nil {
		return result, err
	}
	python := filepath.Join(root, ".venv", "bin", "python")
	if _, err = os.Stat(python); err != nil {
		return result, fmt.Errorf("configured native runtime unavailable: %w", err)
	}
	directory := filepath.Join(proc.OutputDirectory, "word-refinement", fmt.Sprint(time.Now().UnixNano()))
	if err = os.MkdirAll(directory, 0700); err != nil {
		return result, err
	}
	snapshot, err := json.Marshal(result)
	if err != nil {
		return result, err
	}
	inputJSON, outputJSON := filepath.Join(directory, "primary.json"), filepath.Join(directory, "proposals.json")
	if err = os.WriteFile(inputJSON, snapshot, 0600); err != nil {
		return result, err
	}
	// The diarization input normally is the complete converted waveform. Keep
	// its original clock; never use VAD-stripped audio for the supplementary model.
	audio := input.FilePath
	converted := filepath.Join(directory, "input.wav")
	defer os.Remove(converted)
	convertCtx, convertCancel := context.WithTimeout(ctx, 10*time.Minute)
	defer convertCancel()
	convert := exec.CommandContext(convertCtx, binaries.FFmpeg(), "-nostdin", "-v", "error", "-i", audio, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", converted)
	if output, conversionErr := convert.CombinedOutput(); conversionErr != nil {
		return result, fmt.Errorf("native waveform conversion: %w: %.500s", conversionErr, output)
	}
	runCtx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	command := exec.CommandContext(runCtx, python, filepath.Join(root, "scripts", "refine_words.py"), "--audio", converted, "--input", inputJSON, "--out", outputJSON, "--model", filepath.Join(root, "model"), "--device", "mps")
	command.Env = nativeWordEnvironment()
	log, err := os.OpenFile(filepath.Join(directory, "process.log"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return result, err
	}
	defer log.Close()
	command.Stdout, command.Stderr = log, log
	if err = command.Run(); err != nil {
		return result, fmt.Errorf("native word refinement failed; primary retained; see %s: %w", directory, err)
	}
	output, err := os.ReadFile(outputJSON)
	if err != nil {
		return result, err
	}
	var proposals struct {
		InputSHA256   string `json:"input_sha256"`
		ModelRevision string `json:"model_revision"`
		AcousticWordRefinement
	}
	if err = json.Unmarshal(output, &proposals); err != nil {
		return result, err
	}
	sum := sha256.Sum256(snapshot)
	if proposals.InputSHA256 != hex.EncodeToString(sum[:]) || proposals.ModelRevision != wordRefinementRevision {
		return result, fmt.Errorf("native proposal source/model mismatch")
	}
	refined, err := ApplyAcousticWordRefinement(result, proposals.AcousticWordRefinement)
	if err != nil {
		return result, err
	}
	if refined.Metadata == nil {
		refined.Metadata = map[string]string{}
	}
	if len(proposals.Proposals) == 0 && len(proposals.MixedActivity.Exclusive)+len(proposals.MixedActivity.Regular) == 0 {
		refined.Metadata["word_refinement_status"] = "completed_no_changes"
	}
	refined.Metadata["word_refinement_policy"] = wordRefinementPolicy
	refined.Metadata["word_refinement_engine"] = "nvidia/Nemotron-3-Diarization"
	refined.Metadata["word_refinement_model_revision"] = wordRefinementRevision
	refined.Metadata["word_refinement_artifact_dir"] = directory
	return refined, nil
}

func nativeWordEnvironment() []string {
	var env []string
	for _, value := range os.Environ() {
		key, _, _ := strings.Cut(value, "=")
		if key == "HF_TOKEN" || key == "HUGGING_FACE_HUB_TOKEN" || strings.HasSuffix(key, "API_KEY") || strings.HasPrefix(key, "PLUME_EVAL_") {
			continue
		}
		switch key {
		case "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "PYTORCH_ENABLE_MPS_FALLBACK", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONNOUSERSITE":
			continue
		}
		env = append(env, value)
	}
	return append(env, "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "HF_HUB_DISABLE_TELEMETRY=1", "PYTORCH_ENABLE_MPS_FALLBACK=0", "OMP_NUM_THREADS=1", "OPENBLAS_NUM_THREADS=1", "MKL_NUM_THREADS=1", "PYTHONNOUSERSITE=1")
}
