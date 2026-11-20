import { Fragment, forwardRef, useRef, useState, useCallback, useEffect, useMemo } from 'react';
import { useKaraokeHighlight, computeWordOffsets, findActiveWordIndex } from '@/features/transcription/hooks/useKaraokeHighlight';
import { compactSpeakerPassages } from './compact-speaker-passages';
import { cn } from '@/lib/utils';
import { useIsDesktop } from '@/hooks/useIsDesktop';
import { formatSpeakerLabel } from '@/lib/speaker-utils';
import type { Note } from '@/types/note';

function formatSegmentTime(value: unknown): string {
    const numericValue = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(numericValue) || numericValue < 0) {
        return "--:--:--";
    }

    const totalSeconds = Math.floor(numericValue);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

// Helper for cross-browser caret position
function getCaretOffsetFromPoint(x: number, y: number) {
    if (document.caretRangeFromPoint) {
        const range = document.caretRangeFromPoint(x, y);
        return range ? range.startOffset : null;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if ((document as any).caretPositionFromPoint) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const pos = (document as any).caretPositionFromPoint(x, y);
        return pos ? pos.offset : null;
    }
    return null;
}

interface WordSegment {
    speaker_review?: boolean;
    start: number;
    end: number;
    word: string;
    score: number;
    speaker?: string;
    speaker_name?: string;
}

interface Transcript {
    metadata?: Record<string, string>;
    text: string;
    segments?: Array<{
        start: number;
        end: number;
        text: string;
        speaker?: string;
        speaker_name?: string;
    }>;
    word_segments?: WordSegment[];
}

type TranscriptMode = 'plain' | 'timeline' | 'compacted';

interface TranscriptViewProps {
    transcript: Transcript | null;
    mode: TranscriptMode;
    currentWordIndex: number | null;
    currentTime: number;
    isPlaying: boolean;
    notes: Note[];
    highlightedWordRef: React.RefObject<HTMLSpanElement | null>;
    speakerMappings: Record<string, string>;
    autoScrollEnabled: boolean;
    onSeek: (time: number) => void;
    reviewTarget?: { time: number; requestId: number } | null;
    className?: string;
}

export const TranscriptView = forwardRef<HTMLDivElement, TranscriptViewProps>(({
    transcript,
    mode,
    // currentWordIndex,
    currentTime,
    isPlaying,
    // notes,
    // highlightedWordRef,
    speakerMappings,
    autoScrollEnabled,
    onSeek,
    reviewTarget,
    className
}, ref) => {
    const resolveSegmentSpeaker = (originalSpeaker?: string): string => originalSpeaker?.trim() || '';

    const getDisplaySpeakerName = (originalSpeaker: string, persistedName?: string): string => {
        const resolvedSpeaker = resolveSegmentSpeaker(originalSpeaker);
        if (!resolvedSpeaker && persistedName?.trim()) return persistedName.trim();
        if (!resolvedSpeaker && transcript?.metadata?.diarization_status && transcript.metadata.diarization_status !== 'not_requested') return 'Speaker unclear';
        return speakerMappings[resolvedSpeaker] || persistedName || formatSpeakerLabel(resolvedSpeaker);
    };

    const containerRef = useRef<HTMLDivElement>(null);
    const [isModifierPressed, setIsModifierPressed] = useState(false);
    const isDesktop = useIsDesktop();

    // Use CSS Highlight API for Compact Mode
    // Note: We only use this hook when in compact mode to save resources
    const words = transcript?.word_segments || [];
    const { fullText, offsets } = useKaraokeHighlight(
        containerRef,
        words,
        currentTime,
        isPlaying
    );

    // Click-to-Seek Handler
    const handleWordClick = useCallback((e: React.MouseEvent) => {
        // Only trigger if Cmd (Mac) or Ctrl (Windows) is held
        if (!e.metaKey && !e.ctrlKey) return;

        const clickOffset = getCaretOffsetFromPoint(e.clientX, e.clientY);
        if (clickOffset === null) return;

        const clickedWord = offsets.find(w =>
            clickOffset >= w.startChar && clickOffset <= w.endChar
        );

        if (clickedWord) {
            onSeek(clickedWord.startTime);
            e.preventDefault();
        }
    }, [offsets, onSeek]);

    // Keyboard listener for modifier key visual cue
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Meta' || e.key === 'Control') setIsModifierPressed(true);
        };
        const handleKeyUp = (e: KeyboardEvent) => {
            if (e.key === 'Meta' || e.key === 'Control') setIsModifierPressed(false);
        };

        window.addEventListener('keydown', handleKeyDown);
        window.addEventListener('keyup', handleKeyUp);
        return () => {
            window.removeEventListener('keydown', handleKeyDown);
            window.removeEventListener('keyup', handleKeyUp);
        };
    }, []);

    // Expanded View Logic
    const segmentRefs = useRef<(HTMLDivElement | null)[]>([]);

    // 1. Precompute per-segment text and offsets
    const expandedData = useMemo(() => {
        if (!transcript?.segments) return [];

        return transcript.segments.map((segment) => {
            // Filter words belonging to this segment
            const segmentWords = (transcript.word_segments ?? []).filter(
                word => word.start >= segment.start - 0.1 && word.end <= segment.end + 0.1
            );

            // Compute local offsets for this segment's text
            const { fullText, offsets } = computeWordOffsets(segmentWords);

            return {
                ...segment,
                fullText, // The text to render
                offsets   // Offsets relative to this segment's text node
            };
        });
    }, [transcript]);

    // Compute which segment is currently active based on playback time
    const activeSegmentIndex = useMemo(() => {
        if (!expandedData.length) return -1;
        // Find the latest segment that has started (search backwards)
        for (let i = expandedData.length - 1; i >= 0; i--) {
            if (expandedData[i].start <= currentTime) return i;
        }
        return 0;
    }, [expandedData, currentTime]);

    const isReviewTarget = (start: number, end: number) =>
        reviewTarget !== null && reviewTarget !== undefined && reviewTarget.time >= start && reviewTarget.time < end;

    // Track previous active segment to only scroll on segment change
    const prevActiveSegmentRef = useRef<number>(-1);

    // Auto-scroll to active segment during playback
    useEffect(() => {
        if (mode === 'plain' || !autoScrollEnabled || !isPlaying) return;
        if (activeSegmentIndex < 0) return;
        // Only scroll when the segment actually changes
        if (activeSegmentIndex === prevActiveSegmentRef.current) return;
        prevActiveSegmentRef.current = activeSegmentIndex;

        const el = segmentRefs.current[activeSegmentIndex];
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }, [activeSegmentIndex, autoScrollEnabled, isPlaying, mode]);

    // 2. Highlight Effect for Expanded View
    useEffect(() => {
        if (mode === 'plain' || !expandedData.length || !isPlaying) return;
        if (typeof CSS === 'undefined' || !CSS.highlights) return;

        // Find the active segment and word
        // Optimization: We could binary search segments, but N is usually small (<1000). Linear is okay or optimize later.
        // Actually for real-time validation, let's just find the active word in the relevant segment.

        let found = false;

        // Search backwards to find the LATEST segment that has started
        // This prevents getting stuck on the first segment (which is always "started" relative to future time)
        for (let i = expandedData.length - 1; i >= 0; i--) {
            const seg = expandedData[i];

            // Optimization: If segment hasn't started yet, skip it
            // (heuristic using segment start time)
            if (seg.start > currentTime) continue;

            const activeIndex = findActiveWordIndex(seg.offsets, currentTime);
            if (activeIndex !== -1) {
                const w = seg.offsets[activeIndex];
                const el = segmentRefs.current[i];

                if (el && el.firstChild) {
                    try {
                        const range = new Range();
                        if (w.endChar <= (el.firstChild as Text).length) {
                            range.setStart(el.firstChild, w.startChar);
                            range.setEnd(el.firstChild, w.endChar);
                            const highlight = new Highlight(range);
                            CSS.highlights.set('karaoke-word', highlight);
                            found = true;
                        }
                    } catch {
                        // Ignore range errors
                    }
                }
                if (found) break;
            }
        }

        if (!found) {
            if (CSS.highlights.has('karaoke-word')) CSS.highlights.delete('karaoke-word');
        }

    }, [currentTime, isPlaying, mode, expandedData]);

    // 3. Click Handler for Expanded View
    const handleExpandedClick = useCallback((e: React.MouseEvent, segmentIndex: number) => {
        if (!e.metaKey && !e.ctrlKey) return;

        const clickOffset = getCaretOffsetFromPoint(e.clientX, e.clientY);
        if (clickOffset === null) return;

        const segData = expandedData[segmentIndex];
        if (!segData) return;

        const clickedWord = segData.offsets.find(w =>
            clickOffset >= w.startChar && clickOffset <= w.endChar
        );

        if (clickedWord) {
            onSeek(clickedWord.startTime);
            e.preventDefault();
        }
    }, [expandedData, onSeek]);

    // Use the saved characters for reading and selection. Mark an uncertain
    // word inside a known passage only when its timing and text both match.
    const compactTexts = useMemo(() => {
        const timed = transcript?.word_segments ?? [];
        let index = 0;
        return (transcript?.segments ?? []).map(segment => {
            const text = segment.text.trim();
            const marks: Array<{ start: number; end: number }> = [];
            let cursor = 0;
            let aligned = true;
            while (index < timed.length) {
                const word = timed[index];
                const midpoint = (word.start + word.end) / 2;
                if (midpoint > segment.end) break;
                index++;
                if (!aligned || !segment.speaker || midpoint < segment.start) continue;
                const token = word.word.trim();
                if (!token) continue;
                const start = text.indexOf(token, cursor);
                if (start < 0) {
                    aligned = false;
                    continue;
                }
                cursor = start + token.length;
                if (!word.speaker) marks.push({ start, end: cursor });
            }
            if (!aligned || !marks.length) return text;
            return <>{marks.map((mark, i) => (
                <Fragment key={mark.start}>
                    {text.slice(i ? marks[i - 1].end : 0, mark.start)}
                    <abbr title="Speaker unclear" className="decoration-dotted underline underline-offset-4">{text.slice(mark.start, mark.end)}</abbr>
                </Fragment>
            ))}{text.slice(marks[marks.length - 1].end)}</>;
        });
    }, [transcript]);

    if (!transcript) {
        return (
            <div className="flex flex-col items-center justify-center h-64 text-carbon-400">
                <p>No transcript available.</p>
            </div>
        );
    }

    // Render plain text with word-level highlighting (no speaker info)
    const renderPlainView = () => {
        if (!transcript.word_segments || transcript.word_segments.length === 0) {
            return <p data-transcript-text className="text-lg leading-relaxed text-carbon-700 dark:text-carbon-300 whitespace-pre-wrap break-words">{transcript.text}</p>;
        }

        return (
            <div
                ref={containerRef}
                onClick={isDesktop ? handleWordClick : undefined}
                className={cn(
                    "text-lg leading-relaxed text-carbon-700 dark:text-carbon-300 whitespace-pre-wrap font-reading selection:bg-[var(--brand-light)] transition-colors duration-200 select-text",
                    isDesktop && isModifierPressed ? 'cursor-pointer hover:text-carbon-900 dark:hover:text-carbon-100' : 'cursor-text'
                )}
                style={{
                    // CRITICAL: Enable native text selection on iOS/Android
                    WebkitUserSelect: 'text',
                    userSelect: 'text',
                    // CRITICAL: Remove grey tap highlight on iOS
                    WebkitTapHighlightColor: 'transparent',
                    // CRITICAL: Allow text selection gestures while supporting scroll
                    // 'manipulation' allows pan and pinch-zoom but not double-tap zoom
                    touchAction: 'pan-y pinch-zoom',
                    // Ensure text is the selection target, not the container
                    WebkitTouchCallout: 'default'
                }}
                data-transcript-text
                data-review-target={reviewTarget ? "true" : undefined}
                tabIndex={reviewTarget ? -1 : undefined}
            >
                {/* The hook returns the built text string, so we just render it directly */}
                {fullText}
            </div>

        );
    };

    const renderTimelineView = () => {
        if (!transcript?.segments) {
            return renderPlainView();
        }

        return (
            <div className="space-y-4"> {/* Reduced spacing from space-y-6 */}
                {expandedData.map((segment, i) => (
                    <div
                        key={i}
                        data-transcript-start={segment.start}
                        data-transcript-end={segment.end}
                        data-review-target={isReviewTarget(segment.start, segment.end) ? "true" : undefined}
                        tabIndex={isReviewTarget(segment.start, segment.end) ? -1 : undefined}
                        className={cn(
                            "group flex flex-col sm:flex-row items-start gap-4 p-3 rounded-lg transition-colors border",
                            i === activeSegmentIndex && isPlaying
                                ? "bg-carbon-50 dark:bg-carbon-800/40 border-carbon-200 dark:border-carbon-700"
                                : "hover:bg-carbon-50 dark:hover:bg-carbon-800/50 border-transparent hover:border-carbon-100 dark:hover:border-carbon-800",
                            isReviewTarget(segment.start, segment.end) && "bg-[var(--brand-light)]/20 border-[var(--brand-solid)]/50"
                        )}
                    >
                        {/* Timestamp & Speaker */}
                        <div className="flex-shrink-0 w-24 sm:w-28 flex flex-col items-start sm:items-end gap-1 text-xs text-carbon-500 dark:text-carbon-400 select-none mt-1">
                            <span className="font-mono bg-carbon-100 dark:bg-carbon-800/80 px-1.5 py-0.5 rounded text-[10px] sm:text-xs">
                                {formatSegmentTime(segment.start)}
                            </span>
                            {(resolveSegmentSpeaker(segment.speaker) || (transcript.metadata?.diarization_status && transcript.metadata.diarization_status !== 'not_requested')) && (
                                <span
                                    className="font-medium text-carbon-700 dark:text-carbon-300 truncate max-w-full"
                                    title={getDisplaySpeakerName(resolveSegmentSpeaker(segment.speaker), segment.speaker_name)}
                                >
                                    {getDisplaySpeakerName(resolveSegmentSpeaker(segment.speaker), segment.speaker_name)}
                                </span>
                            )}
                        </div>

                        {/* Text */}
                        <div
                            ref={(el) => { segmentRefs.current[i] = el; }}
                            onClick={isDesktop ? (e) => handleExpandedClick(e, i) : undefined}
                            className={cn(
                                "flex-grow min-w-0 text-base text-primary leading-relaxed whitespace-pre-wrap break-words font-reading transition-colors duration-200 select-text",
                                isDesktop && isModifierPressed ? 'cursor-pointer hover:text-carbon-900 dark:hover:text-carbon-100' : 'cursor-text'
                            )}
                            style={{
                                // CRITICAL: Enable native text selection on iOS/Android
                                WebkitUserSelect: 'text',
                                userSelect: 'text',
                                // CRITICAL: Remove grey tap highlight on iOS
                                WebkitTapHighlightColor: 'transparent',
                                // CRITICAL: Allow text selection gestures while supporting scroll
                                touchAction: 'pan-y pinch-zoom',
                                WebkitTouchCallout: 'default'
                            }}
                            data-transcript-text
                        >
                            {segment.fullText || segment.text}
                        </div>
                    </div>
                ))}
            </div>
        );
    };

    // Compacted View: groups consecutive same-speaker segments into single paragraphs
    const compactedGroups = compactSpeakerPassages(transcript.segments ?? [], getDisplaySpeakerName,
        new Set(words.filter(word => word.speaker_review).map(word => word.start)));

    const renderCompactedView = () => {
        if (!transcript?.segments || compactedGroups.length === 0) {
            return renderPlainView();
        }

        return (
            <div className="space-y-6">
                {compactedGroups.map((group, gi) => (
                    <div
                        key={gi}
                        data-transcript-start={group.startTime}
                        data-transcript-end={group.endTime}
                        data-review-target={isReviewTarget(group.startTime, group.endTime) ? "true" : undefined}
                        tabIndex={isReviewTarget(group.startTime, group.endTime) ? -1 : undefined}
                        className={cn(
                            "space-y-1 rounded-lg border border-transparent p-2 transition-colors",
                            isReviewTarget(group.startTime, group.endTime) && "bg-[var(--brand-light)]/20 border-[var(--brand-solid)]/50"
                        )}
                    >
                        {group.displaySpeaker && (
                            <div className="flex items-center gap-2 text-sm font-medium text-carbon-600 dark:text-carbon-400 select-none">
                                <span>{group.displaySpeaker}</span>
                                <span className="font-mono text-[10px] text-carbon-400 dark:text-carbon-500">
                                    {formatSegmentTime(group.startTime)}
                                </span>
                            </div>
                        )}
                        <p
                            className={cn(
                                "text-base text-primary leading-relaxed whitespace-pre-wrap font-reading select-text",
                                isDesktop && isModifierPressed ? 'cursor-pointer' : 'cursor-text'
                            )}
                            style={{
                                WebkitUserSelect: 'text',
                                userSelect: 'text',
                                WebkitTapHighlightColor: 'transparent',
                                touchAction: 'pan-y pinch-zoom',
                                WebkitTouchCallout: 'default'
                            }}
                            data-transcript-text
                        >
                            {group.segmentIndices.map((index, part) => (
                                <Fragment key={index}>{part > 0 ? ' ' : ''}{group.segmentIndices.length > 1 && !transcript.segments?.[index].speaker && !transcript.segments?.[index].speaker_name
                                        ? <span><span role="img" aria-label="Speaker unclear" data-label="[Speaker unclear] " className="text-xs font-medium text-carbon-500 before:content-[attr(data-label)]" /><span title="Speaker unclear" className="underline decoration-dotted underline-offset-4">{compactTexts[index]}</span></span>
                                        : compactTexts[index]}</Fragment>
                            ))}
                        </p>
                    </div>
                ))}
            </div>
        );
    };

    const renderView = () => {
        switch (mode) {
            case 'plain':
                return renderPlainView();
            case 'compacted':
                return renderCompactedView();
            case 'timeline':
                return renderTimelineView();
            default:
                return renderPlainView();
        }
    };

    return (
        <div
            ref={ref}
            className={cn("w-full max-w-none font-literata mt-4", className)}
        >
            {renderView()}

            {/* CSS for the Highlight API - Global for both views */}
            <style>{`
                ::highlight(karaoke-word) {
                    background-color: transparent;
                    color: var(--brand-solid) !important;
                    font-weight: 600;
                    text-decoration: underline decoration-dotted var(--brand-solid);
                    text-underline-offset: 4px;
                }
            `}</style>
        </div>
    );
});

TranscriptView.displayName = 'TranscriptView';

export type { TranscriptMode };
