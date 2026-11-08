package adapters

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
)

// InstallWordRefinementScripts uses the same inference/proposal code exercised
// by offline evaluation. Dependencies/models live in a separate local runtime.
func InstallWordRefinementScripts(directory string) error {
	if err := os.MkdirAll(directory, 0700); err != nil {
		return err
	}
	for _, name := range []string{"activity_timeline.py", "nemotron3_core.py", "word_rescue.py", "refine_words.py"} {
		data, err := nvidiaScripts.ReadFile("py/nvidia/" + name)
		if err != nil {
			return fmt.Errorf("read native refinement script: %w", err)
		}
		path := filepath.Join(directory, name)
		previous, _ := os.ReadFile(path)
		if bytes.Equal(previous, data) {
			continue
		}
		if err = os.WriteFile(path, data, 0600); err != nil {
			return err
		}
		// Source changes must not accidentally reuse same-second bytecode.
		if err = os.RemoveAll(filepath.Join(directory, "__pycache__")); err != nil {
			return err
		}
	}
	return nil
}
