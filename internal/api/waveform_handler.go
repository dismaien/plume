package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"quill/pkg/binaries"
	"quill/pkg/logger"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

const (
	defaultWaveformSamples = 640
	minWaveformSamples     = 128
	maxWaveformSamples     = 2048
	waveformSampleRate     = 800
	maxWaveformPCMBytes    = 256 << 20
)

type waveformResponse struct {
	Peaks []float64 `json:"peaks"`
}

type cappedPCMBuffer struct {
	bytes.Buffer
	limit    int
	overflow bool
}

func (buffer *cappedPCMBuffer) Write(data []byte) (int, error) {
	originalLength := len(data)
	remaining := buffer.limit - buffer.Len()
	if remaining <= 0 {
		buffer.overflow = true
		return originalLength, nil
	}
	if len(data) > remaining {
		buffer.overflow = true
		data = data[:remaining]
	}
	_, _ = buffer.Buffer.Write(data)
	return originalLength, nil
}

// GetAudioWaveform returns a compact, cached amplitude envelope for the player.
// Decoding happens server-side so long recordings never need to be decoded into
// a full PCM buffer by the browser.
func (h *Handler) GetAudioWaveform(c *gin.Context) {
	jobID := c.Param("id")
	job, err := h.jobRepo.FindAudioSourceByID(c.Request.Context(), jobID)
	if err != nil {
		if err == gorm.ErrRecordNotFound {
			c.JSON(http.StatusNotFound, gin.H{"error": "Job not found"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to get job"})
		return
	}

	audioPath := job.AudioPath
	if job.IsMultiTrack && job.MergedAudioPath != nil && *job.MergedAudioPath != "" {
		if _, statErr := os.Stat(*job.MergedAudioPath); statErr == nil {
			audioPath = *job.MergedAudioPath
		}
	}
	if audioPath == "" {
		c.JSON(http.StatusNotFound, gin.H{"error": "Audio file path not found"})
		return
	}
	audioPath, err = localPlaybackPath(h.config.TempDir, audioPath)
	if err != nil {
		logger.Error("failed to prepare audio for waveform", "job_id", jobID, "error", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to prepare audio for waveform"})
		return
	}

	fileInfo, err := os.Stat(audioPath)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Audio file not found on disk"})
		return
	}

	sampleCount := defaultWaveformSamples
	if requested, parseErr := strconv.Atoi(c.Query("samples")); parseErr == nil && requested > 0 {
		sampleCount = max(minWaveformSamples, min(maxWaveformSamples, requested))
	}

	cachePath := waveformCachePath(audioPath, fileInfo, sampleCount)
	if cached, readErr := os.ReadFile(cachePath); readErr == nil {
		c.Header("Cache-Control", "private, max-age=86400")
		c.Data(http.StatusOK, "application/json; charset=utf-8", cached)
		return
	}

	peaks, err := extractWaveformPeaks(c.Request.Context(), audioPath, sampleCount)
	if err != nil {
		logger.Error("waveform extraction failed", "job_id", jobID, "error", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to generate waveform"})
		return
	}

	payload, err := json.Marshal(waveformResponse{Peaks: peaks})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to encode waveform"})
		return
	}
	if err := os.MkdirAll(filepath.Dir(cachePath), 0o755); err == nil {
		if writeErr := os.WriteFile(cachePath, payload, 0o644); writeErr != nil {
			logger.Warn("failed to cache waveform", "path", cachePath, "error", writeErr)
		}
	}

	c.Header("Cache-Control", "private, max-age=86400")
	c.Data(http.StatusOK, "application/json; charset=utf-8", payload)
}

func waveformCachePath(audioPath string, info os.FileInfo, sampleCount int) string {
	cacheRoot, err := os.UserCacheDir()
	if err != nil || cacheRoot == "" {
		cacheRoot = os.TempDir()
	}
	identity := fmt.Sprintf("%s\x00%d\x00%d\x00%d", audioPath, info.Size(), info.ModTime().UnixNano(), sampleCount)
	digest := sha256.Sum256([]byte(identity))
	return filepath.Join(cacheRoot, "Plume", "waveforms", hex.EncodeToString(digest[:])+".json")
}

func extractWaveformPeaks(ctx context.Context, audioPath string, sampleCount int) ([]float64, error) {
	cmd := exec.CommandContext(ctx, binaries.FFmpeg(),
		"-v", "error",
		"-i", audioPath,
		"-map", "0:a:0",
		"-ac", "1",
		"-ar", strconv.Itoa(waveformSampleRate),
		"-f", "s16le",
		"pipe:1",
	)
	var stderr bytes.Buffer
	pcm := cappedPCMBuffer{limit: maxWaveformPCMBytes}
	cmd.Stderr = &stderr
	cmd.Stdout = &pcm
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("ffmpeg: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	if pcm.overflow {
		return nil, fmt.Errorf("decoded waveform exceeds %d bytes", maxWaveformPCMBytes)
	}

	return waveformPeaksFromPCM(pcm.Bytes(), sampleCount), nil
}

func waveformPeaksFromPCM(pcm []byte, sampleCount int) []float64 {
	if sampleCount <= 0 {
		return nil
	}
	peaks := make([]float64, sampleCount)
	frameCount := len(pcm) / 2
	if frameCount == 0 {
		return peaks
	}

	for frame := 0; frame < frameCount; frame++ {
		value := int16(binary.LittleEndian.Uint16(pcm[frame*2 : frame*2+2]))
		amplitude := math.Abs(float64(value))
		bucket := frame * sampleCount / frameCount
		if bucket >= sampleCount {
			bucket = sampleCount - 1
		}
		if amplitude > peaks[bucket] {
			peaks[bucket] = amplitude
		}
	}

	// A percentile reference prevents one accidental spike from flattening the
	// rest of the recording. Square-root scaling keeps speech legible while true
	// silence remains at zero.
	sorted := append([]float64(nil), peaks...)
	sort.Float64s(sorted)
	referenceIndex := int(math.Floor(float64(len(sorted)-1) * 0.95))
	reference := sorted[referenceIndex]
	if reference <= 0 {
		return peaks
	}
	for i, peak := range peaks {
		normalized := math.Min(1, peak/reference)
		peaks[i] = math.Round(math.Sqrt(normalized)*1000) / 1000
	}
	return peaks
}
