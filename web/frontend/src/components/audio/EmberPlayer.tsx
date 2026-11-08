import { useRef, useState, useEffect, forwardRef, useImperativeHandle, useCallback } from "react";
import { Play, Pause, AlertCircle, RotateCcw } from "lucide-react";
import { AudioWaveform } from "./AudioWaveform";
import { cn } from "@/lib/utils";

export interface EmberPlayerRef {
    seekTo: (time: number) => void;
    playPause: () => void;
    isPlaying: () => boolean;
}

export interface EmberPlayerProps {
    src?: string;
    audioId?: string;
    unavailableReason?: string;
    className?: string;
    onTimeUpdate?: (time: number) => void;
    onPlayStateChange?: (isPlaying: boolean) => void;
}

function shouldIgnorePlaybackShortcut(event: KeyboardEvent) {
    if (event.defaultPrevented || event.repeat || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) {
        return true;
    }

    if (document.querySelector('[role="dialog"], [aria-modal="true"]')) {
        return true;
    }

    const target = event.target;
    if (!(target instanceof HTMLElement)) return false;

    return target.isContentEditable || Boolean(target.closest(
        'input, textarea, select, button, a, [role="button"], [role="textbox"], [contenteditable="true"]',
    ));
}

export const EmberPlayer = forwardRef<EmberPlayerRef, EmberPlayerProps>(
    ({ src, audioId, unavailableReason, className, onTimeUpdate, onPlayStateChange }, ref) => {
        const audioRef = useRef<HTMLAudioElement>(null);
        const progressRef = useRef<HTMLDivElement>(null);

        const [isPlaying, setIsPlaying] = useState(false);
        const [currentTime, setCurrentTime] = useState(0);
        const [duration, setDuration] = useState(0);
        const [error, setError] = useState<string | null>(null);

        // Visualizer Interaction State
        const [hoverTime, setHoverTime] = useState(0);
        const [isHovering, setIsHovering] = useState(false);
        const [isDragging, setIsDragging] = useState(false);

        const togglePlay = useCallback(() => {
            const audio = audioRef.current;
            if (!audio) return;

            if (audio.paused) {
                audio.play().catch(e => {
                    console.error("Playback failed:", e);
                    setError("Playback failed.");
                });
            } else {
                audio.pause();
            }
        }, []);

        const streamUrl = src || (audioId ? `/api/v1/transcription/${audioId}/audio` : undefined);

        useEffect(() => {
            audioRef.current?.pause();
            setError(null);
            setIsPlaying(false);
            setCurrentTime(0);
            setDuration(0);
            setHoverTime(0);
            setIsHovering(false);
            setIsDragging(false);
        }, [streamUrl, unavailableReason]);

        // --- 1. Parent Control (ForwardRef) ---
        useImperativeHandle(ref, () => ({
            seekTo: (time: number) => {
                if (audioRef.current) {
                    audioRef.current.currentTime = time;
                    setCurrentTime(time);
                }
            },
            playPause: () => togglePlay(),
            isPlaying: () => isPlaying
        }), [isPlaying, togglePlay]);

        // --- 3. Audio Handlers ---
        useEffect(() => {
            const handlePlaybackShortcut = (event: KeyboardEvent) => {
                if ((event.code !== "Space" && event.key !== " ") || shouldIgnorePlaybackShortcut(event)) {
                    return;
                }

                event.preventDefault();
                togglePlay();
            };

            window.addEventListener("keydown", handlePlaybackShortcut);
            return () => window.removeEventListener("keydown", handlePlaybackShortcut);
        }, [togglePlay]);

        const handleTimeUpdate = () => {
            if (audioRef.current && !isDragging) {
                const time = audioRef.current.currentTime;
                setCurrentTime(time);
                onTimeUpdate?.(time);
            }
        };

        const handleLoadedMetadata = () => {
            if (audioRef.current) {
                setDuration(audioRef.current.duration);
                setError(null);
            }
        };

        // --- 4. Advanced Scrubber Logic ---
        const calculateTimeFromEvent = useCallback((e: React.MouseEvent | MouseEvent) => {
            if (!progressRef.current || !duration) return 0;
            const rect = progressRef.current.getBoundingClientRect();
            let x = e.clientX - rect.left;
            x = Math.max(0, Math.min(x, rect.width));
            return (x / rect.width) * duration;
        }, [duration]);

        const seekTo = useCallback((time: number) => {
            if (!audioRef.current) return;
            const boundedTime = Math.max(0, Math.min(duration, time));
            audioRef.current.currentTime = boundedTime;
            setCurrentTime(boundedTime);
            onTimeUpdate?.(boundedTime);
        }, [duration, onTimeUpdate]);

        const handleScrubberMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
            setIsDragging(true);
            seekTo(calculateTimeFromEvent(e));
        };

        // Global mouse listeners for dragging outside the component
        useEffect(() => {
            const handleGlobalMouseMove = (e: MouseEvent) => {
                if (isDragging && audioRef.current && progressRef.current) {
                    seekTo(calculateTimeFromEvent(e));
                }
            };

            const handleGlobalMouseUp = () => {
                setIsDragging(false);
            };

            if (isDragging) {
                window.addEventListener("mousemove", handleGlobalMouseMove);
                window.addEventListener("mouseup", handleGlobalMouseUp);
            }

            return () => {
                window.removeEventListener("mousemove", handleGlobalMouseMove);
                window.removeEventListener("mouseup", handleGlobalMouseUp);
            };
        }, [isDragging, calculateTimeFromEvent, seekTo]);

        const handleHoverMove = (e: React.MouseEvent<HTMLDivElement>) => {
            if (!progressRef.current || !duration) return;
            const rect = progressRef.current.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const percent = Math.min(Math.max(0, x / rect.width), 1);
            setHoverTime(percent * duration);
        };

        // Sync State
        useEffect(() => {
            onPlayStateChange?.(isPlaying);
        }, [isPlaying, onPlayStateChange]);


        const formatTime = (time: number) => {
            if (isNaN(time)) return "00:00";
            const min = Math.floor(time / 60);
            const sec = Math.floor(time % 60);
            return `${min.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
        };

        const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0;
        if (unavailableReason) {
            return (
                <div className="flex h-32 w-full items-center justify-center rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--bg-card)] text-[var(--text-secondary)]">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    {unavailableReason}
                </div>
            );
        }

        const retryPlayback = () => {
            const audio = audioRef.current;
            if (!audio) return;
            setError(null);
            audio.load();
            audio.play().catch((playError) => {
                console.error("Playback retry failed:", playError);
                setError("Playback failed.");
            });
        };

        return (
            <div
                className={cn(
                    // Base Container Styles - designed to fill the parent card
                    "relative w-full overflow-hidden rounded-[var(--radius-card)]",
                    // Use standard background matching the design system
                    "bg-transparent",
                    className
                )}
            >
                {/* Controls Layer */}
                {error ? (
                    <div className="relative z-10 flex h-32 items-center justify-center gap-3 rounded-[var(--radius-card)] border border-[var(--error)]/20 bg-[var(--error)]/10 text-[var(--error)]">
                        <AlertCircle className="h-5 w-5" />
                        <span>{error}</span>
                        <button
                            type="button"
                            onClick={retryPlayback}
                            className="inline-flex items-center gap-1.5 text-sm font-medium underline underline-offset-4"
                        >
                            <RotateCcw className="h-4 w-4" />
                            Retry
                        </button>
                    </div>
                ) : (
                <div className="relative z-10 flex flex-col px-1 py-1 gap-2">
                    {/* Top Row: Button & Time */}
                    <div className="flex items-center justify-between">
                        <button
                            type="button"
                            aria-label={isPlaying ? "Pause recording" : "Play recording"}
                            onClick={togglePlay}
                            className="flex h-12 w-12 items-center justify-center rounded-full bg-[image:var(--brand-gradient)] text-white shadow-lg shadow-black/20 hover:scale-105 active:scale-95 transition-all focus:outline-none cursor-pointer"
                        >
                            {isPlaying ? (
                                <Pause size={20} fill="currentColor" />
                            ) : (
                                <Play size={20} fill="currentColor" className="ml-0.5" />
                            )}
                        </button>

                        <div className="flex flex-col items-end">
                            <span className="font-mono text-xs font-medium text-[var(--text-secondary)] tabular-nums tracking-wide">
                                {formatTime(currentTime)}{" "}
                                <span className="text-[var(--text-tertiary)] mx-0.5">/</span>{" "}
                                <span className="text-[var(--text-tertiary)]">
                                    {formatTime(duration)}
                                </span>
                            </span>
                            <span className="text-[10px] text-[var(--text-tertiary)] font-bold uppercase tracking-widest mt-0.5 opacity-80">
                                {isPlaying ? "Playing" : "Ready"}
                            </span>
                        </div>
                    </div>

                    {/* Bottom Row: Interactive Scrubber */}
                    <div
                        ref={progressRef}
                        className="relative mx-2 h-16 w-[calc(100%-1rem)] group cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--border-focus)]"
                        role="slider"
                        tabIndex={0}
                        aria-label="Recording position"
                        aria-valuemin={0}
                        aria-valuemax={Math.max(0, Math.floor(duration))}
                        aria-valuenow={Math.floor(currentTime)}
                        aria-valuetext={`${formatTime(currentTime)} of ${formatTime(duration)}`}
                        onMouseMove={handleHoverMove}
                        onMouseEnter={() => setIsHovering(true)}
                        onMouseLeave={() => setIsHovering(false)}
                        onMouseDown={handleScrubberMouseDown}
                        onKeyDown={(event) => {
                            if (event.key === "ArrowLeft") {
                                event.preventDefault();
                                seekTo(currentTime - 5);
                            } else if (event.key === "ArrowRight") {
                                event.preventDefault();
                                seekTo(currentTime + 5);
                            } else if (event.key === "Home") {
                                event.preventDefault();
                                seekTo(0);
                            } else if (event.key === "End") {
                                event.preventDefault();
                                seekTo(duration);
                            }
                        }}
                    >
                        {/* Tooltip */}
                        <div
                            className={cn(
                                "absolute bottom-full mb-3 px-2 py-1 rounded bg-[var(--text-primary)] text-[10px] font-mono text-[var(--bg-main)] shadow-sm pointer-events-none transition-opacity duration-200 z-30",
                                isHovering ? "opacity-100" : "opacity-0"
                            )}
                            style={{
                                left: `${duration > 0 ? (hoverTime / duration) * 100 : 0}%`,
                                transform: "translateX(-50%)",
                            }}
                        >
                            {formatTime(hoverTime)}
                        </div>

                        <div className="pointer-events-none absolute inset-y-1 inset-x-0">
                            <AudioWaveform audioId={audioId} progressPercent={progressPercent} />
                        </div>

                        {/* Persistent playhead: the current position must remain visible while playing. */}
                        <div
                            className="pointer-events-none absolute inset-y-1 w-0.5 -translate-x-1/2 rounded-full bg-[var(--brand-solid)] shadow-[0_0_0_1px_var(--bg-card)] transition-[left] duration-100 ease-linear"
                            style={{ left: `clamp(6px, ${progressPercent}%, calc(100% - 6px))` }}
                        />
                        <div
                            className={cn(
                                "pointer-events-none absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[var(--bg-card)] bg-[var(--brand-solid)] shadow-[0_0_0_1px_var(--border-focus)] transition-[left,transform] duration-100 ease-linear",
                                (isHovering || isDragging) && "scale-125"
                            )}
                            style={{ left: `clamp(6px, ${progressPercent}%, calc(100% - 6px))` }}
                        />
                    </div>
                </div>
                )}

                {/* Keep audio mounted after errors so retry and source changes can recover. */}
                <audio
                    ref={audioRef}
                    src={streamUrl}
                    preload="metadata"
                    crossOrigin="use-credentials" // Sends cookies AND allows Web Audio API access (with backend support)
                    onPlay={() => setIsPlaying(true)}
                    onPause={() => setIsPlaying(false)}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={handleLoadedMetadata}
                    onEnded={() => setIsPlaying(false)}
                    onError={(e) => {
                        console.error("Audio Load Error", e);
                        setError("Unable to load audio stream.");
                    }}
                />
            </div>
        );
    }
);

EmberPlayer.displayName = "EmberPlayer";
