package api

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// localPlaybackPath gives the desktop backend a stable path inside its own data
// directory. macOS can allow metadata access to cloud-originated files in
// Documents while indefinitely blocking open(2) and link(2) for a helper
// process. The terminal-side archive importer prepares zero-copy hard links;
// this lookup fails promptly when one is missing instead of hanging playback.
func localPlaybackPath(tempDir, sourcePath string) (string, error) {
	sourcePath = strings.TrimSpace(sourcePath)
	if sourcePath == "" {
		return "", errors.New("audio source path is empty")
	}

	sourceInfo, err := os.Stat(sourcePath)
	if err != nil {
		return "", err
	}
	if !sourceInfo.Mode().IsRegular() {
		return "", fmt.Errorf("audio source is not a regular file: %s", sourcePath)
	}

	dataDir := filepath.Clean(filepath.Dir(tempDir))
	if pathIsWithin(sourcePath, dataDir) {
		return sourcePath, nil
	}

	cachePath := playbackLinkPath(tempDir, sourcePath)
	cacheInfo, err := os.Stat(cachePath)
	if err != nil {
		return "", fmt.Errorf("playback link is unavailable: %w", err)
	}
	if !os.SameFile(sourceInfo, cacheInfo) {
		return "", fmt.Errorf("playback link is stale: %s", cachePath)
	}
	return cachePath, nil
}

func playbackLinkPath(tempDir, sourcePath string) string {
	dataDir := filepath.Clean(filepath.Dir(tempDir))
	digest := sha256.Sum256([]byte(sourcePath))
	return filepath.Join(dataDir, "playback-links", hex.EncodeToString(digest[:])+strings.ToLower(filepath.Ext(sourcePath)))
}

func pathIsWithin(path, root string) bool {
	absPath, pathErr := filepath.Abs(path)
	absRoot, rootErr := filepath.Abs(root)
	if pathErr != nil || rootErr != nil {
		return false
	}
	rel, err := filepath.Rel(absRoot, absPath)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}
