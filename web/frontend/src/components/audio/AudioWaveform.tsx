import { useEffect, useId, useMemo, useState } from "react";

interface AudioWaveformProps {
    audioId?: string;
    progressPercent: number;
}

interface WaveformPayload {
    peaks?: unknown;
}

const VIEWBOX_WIDTH = 720;
const VIEWBOX_HEIGHT = 56;
const WAVEFORM_SAMPLES = 420;

function waveformPath(peaks: number[]) {
    if (peaks.length === 0) {
        return `M 0 ${VIEWBOX_HEIGHT / 2} H ${VIEWBOX_WIDTH}`;
    }

    const center = VIEWBOX_HEIGHT / 2;
    const step = VIEWBOX_WIDTH / peaks.length;
    return peaks.map((peak, index) => {
        const x = (index + 0.5) * step;
        const height = Math.max(1.5, Math.min(1, peak) * (VIEWBOX_HEIGHT - 6));
        return `M ${x.toFixed(2)} ${(center - height / 2).toFixed(2)} V ${(center + height / 2).toFixed(2)}`;
    }).join(" ");
}

function isPeakArray(value: unknown): value is number[] {
    return Array.isArray(value) && value.every((peak) => (
        typeof peak === "number" && Number.isFinite(peak) && peak >= 0
    ));
}

export function AudioWaveform({ audioId, progressPercent }: AudioWaveformProps) {
    const [peaks, setPeaks] = useState<number[]>([]);
    const [isLoading, setIsLoading] = useState(Boolean(audioId));
    const clipId = useId().replace(/:/g, "");

    useEffect(() => {
        if (!audioId) {
            setPeaks([]);
            setIsLoading(false);
            return;
        }

        const controller = new AbortController();
        setIsLoading(true);

        fetch(`/api/v1/transcription/${audioId}/waveform?samples=${WAVEFORM_SAMPLES}`, {
            credentials: "include",
            signal: controller.signal,
        })
            .then(async (response) => {
                if (!response.ok) throw new Error(`Waveform request failed: ${response.status}`);
                return response.json() as Promise<WaveformPayload>;
            })
            .then((payload) => {
                setPeaks(isPeakArray(payload.peaks) ? payload.peaks : []);
            })
            .catch((error: unknown) => {
                if (error instanceof DOMException && error.name === "AbortError") return;
                console.error("Unable to load waveform:", error);
                setPeaks([]);
            })
            .finally(() => {
                if (!controller.signal.aborted) setIsLoading(false);
            });

        return () => controller.abort();
    }, [audioId]);

    const displayPeaks = useMemo(() => {
        if (peaks.length > 0) return peaks;
        return Array.from({ length: 96 }, (_, index) => isLoading ? 0.025 + (index % 5) * 0.008 : 0);
    }, [isLoading, peaks]);
    const path = useMemo(() => waveformPath(displayPeaks), [displayPeaks]);
    const safeProgress = Math.max(0, Math.min(100, progressPercent));

    return (
        <svg
            data-testid="audio-waveform"
            className="block h-full w-full overflow-visible"
            viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
            preserveAspectRatio="none"
            aria-hidden="true"
        >
            <defs>
                <clipPath id={clipId}>
                    <rect width={(safeProgress / 100) * VIEWBOX_WIDTH} height={VIEWBOX_HEIGHT} />
                </clipPath>
            </defs>
            <path
                d={path}
                fill="none"
                stroke="var(--text-tertiary)"
                strokeWidth="0.8"
                strokeLinecap="round"
                opacity={isLoading ? 0.28 : 0.55}
            />
            <path
                d={path}
                fill="none"
                stroke="var(--brand-solid)"
                strokeWidth="0.8"
                strokeLinecap="round"
                clipPath={`url(#${clipId})`}
            />
        </svg>
    );
}
