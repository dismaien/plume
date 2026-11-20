package adapters

import (
	"io/fs"
	"strings"
	"testing"
)

func TestInstalledModelScriptsExcludeGeneratedCachesAndTests(t *testing.T) {
	for _, scripts := range []fs.FS{nvidiaScripts, pyannoteScripts} {
		if err := fs.WalkDir(scripts, ".", func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if strings.Contains(path, "__pycache__") || strings.Contains(path, ".pytest_cache") || strings.Contains(path, "/tests/") || strings.HasSuffix(path, ".pyc") {
				t.Errorf("generated/test artifact embedded: %s", path)
			}
			return nil
		}); err != nil {
			t.Fatal(err)
		}
	}
}
