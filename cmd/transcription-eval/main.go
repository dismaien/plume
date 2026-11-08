// transcription-eval assembles frozen ASR words with a candidate diarization
// using the exact production attribution policy. It never opens a database.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"quill/internal/transcription"
	"quill/internal/transcription/interfaces"
)

func main() {
	asr := flag.String("transcript", "", "Frozen Plume TranscriptResult JSON")
	diar := flag.String("diarization", "", "Candidate DiarizationResult JSON")
	output := flag.String("out", "", "New assembled output JSON")
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
	read(*diar, &d)
	if d.ModelUsed == "" {
		var p struct {
			Model string `json:"model"`
		}
		read(*diar, &p)
		d.ModelUsed = p.Model
	}
	result := transcription.AssembleDiarizedTranscript(&t, &d)
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
