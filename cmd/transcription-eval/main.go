// transcription-eval assembles frozen ASR words with a candidate diarization
// using the exact production attribution policy. It never opens a database.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"quill/internal/transcription"
	"quill/internal/transcription/interfaces"
	"quill/internal/transcription/pipeline"
)

func main() {
	asr := flag.String("transcript", "", "Frozen Plume TranscriptResult JSON")
	diar := flag.String("diarization", "", "Candidate DiarizationResult JSON")
	output := flag.String("out", "", "New assembled output JSON")
	overlapReview := flag.Bool("overlap-review", false, "Experimental conservative overlap policy; not the production default")
	proposals := flag.String("word-proposals", "", "Optional frozen independent-model word proposals; no inference")
	nativeRuntime := flag.String("native-runtime", "", "Optional configured local runtime for the real application refinement stage")
	audio := flag.String("audio", "", "Full original-clock WAV for native-runtime preflight")
	vadEvidence := flag.String("vad-evidence", "", "Retained VAD manifest for original-clock words; no timestamp edits")
	flag.Parse()
	if *asr == "" || *diar == "" || *output == "" {
		flag.Usage()
		os.Exit(2)
	}
	var t interfaces.TranscriptResult
	var d interfaces.DiarizationResult
	read := func(path string, into interface{}) {
		data, err := os.ReadFile(path)
		if err != nil {
			panic(err)
		}
		if err = json.Unmarshal(data, into); err != nil {
			panic(err)
		}
	}
	read(*asr, &t)
	if *vadEvidence != "" {
		var manifest pipeline.VadManifest
		read(*vadEvidence, &manifest)
		t = *pipeline.AttachVadSpeechEvidence(&t, manifest)
	}
	read(*diar, &d)
	if d.ModelUsed == "" {
		var p struct {
			Model string `json:"model"`
		}
		read(*diar, &p)
		d.ModelUsed = p.Model
	}
	result := transcription.AssembleDiarizedTranscript(&t, &d)
	if *overlapReview {
		result = transcription.AssembleWithOverlapReview(&t, &d)
	}
	if *proposals != "" {
		var payload struct {
			Proposals []transcription.SpeakerWordProposal `json:"proposals"`
		}
		read(*proposals, &payload)
		var err error
		result, err = transcription.ApplySpeakerWordProposals(result, payload.Proposals)
		if err != nil {
			panic(err)
		}
	}
	if *nativeRuntime != "" {
		if *audio == "" {
			panic("-audio is required for native runtime inference")
		}
		var err error
		result, err = transcription.RefineWithNativeWordRuntime(context.Background(), interfaces.AudioInput{FilePath: *audio}, result, *nativeRuntime, filepath.Dir(*output))
		if err != nil {
			panic(err)
		}
	}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		panic(err)
	}
	file, err := os.OpenFile(*output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		panic(err)
	}
	if _, err = file.Write(data); err != nil {
		file.Close()
		panic(err)
	}
	if err = file.Close(); err != nil {
		panic(err)
	}
	fmt.Printf("Assembled %d frozen words with %s; attribution coverage=%s (not accuracy)\n", len(t.WordSegments), d.ModelUsed, result.Metadata["speaker_labelled_coverage"])
}
