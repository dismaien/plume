package api

import (
	"encoding/binary"
	"testing"
)

func pcm16(samples ...int16) []byte {
	data := make([]byte, len(samples)*2)
	for index, sample := range samples {
		binary.LittleEndian.PutUint16(data[index*2:], uint16(sample))
	}
	return data
}

func TestWaveformPeaksFromPCMExposesSilenceAndRelativeVolume(t *testing.T) {
	peaks := waveformPeaksFromPCM(pcm16(
		0, 0,
		500, -500,
		2000, -2000,
		8000, -8000,
	), 4)

	if len(peaks) != 4 {
		t.Fatalf("expected 4 peaks, got %d", len(peaks))
	}
	if peaks[0] != 0 {
		t.Fatalf("expected silence to remain zero, got %f", peaks[0])
	}
	if !(peaks[1] > peaks[0] && peaks[2] > peaks[1] && peaks[3] >= peaks[2]) {
		t.Fatalf("expected increasing volume, got %#v", peaks)
	}
}

func TestWaveformPeaksFromPCMReturnsRequestedSilentBuckets(t *testing.T) {
	peaks := waveformPeaksFromPCM(nil, 6)
	if len(peaks) != 6 {
		t.Fatalf("expected 6 peaks, got %d", len(peaks))
	}
	for index, peak := range peaks {
		if peak != 0 {
			t.Fatalf("expected bucket %d to be silent, got %f", index, peak)
		}
	}
}
