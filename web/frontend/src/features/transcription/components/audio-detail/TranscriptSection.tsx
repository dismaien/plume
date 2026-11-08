import { getTranscriptSpeakerOrder } from "@/lib/speaker-utils";
import { useRef, useState, useEffect, useMemo, useCallback } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { TranscriptView } from "@/components/transcript/TranscriptView";
import { useNotes, useCreateNote, useUpdateNote, useDeleteNote } from "@/features/transcription/hooks/useTranscriptionNotes";
import { useSelectionMenu } from "@/features/transcription/hooks/useSelectionMenu";
import { TranscriptEditor, type TranscriptEditTarget } from "./TranscriptEditor";
import { DownloadDialog } from "./DownloadDialog";
import SpeakerRenameDialog from "./SpeakerRenameDialog";
import { NotesSidebar } from "./NotesSidebar";
import { TranscriptSelectionMenu } from "./TranscriptSelectionMenu";
import { NoteEditorDialog } from "./NoteEditorDialog";
import { useIsMobile } from "@/hooks/use-mobile";
import { useIsDesktop } from "@/hooks/useIsDesktop";
import { X, StickyNote } from "lucide-react";
import { computeWordOffsets } from "@/features/transcription/hooks/useKaraokeHighlight";
import { useAudioDetail, type Transcript } from "@/features/transcription/hooks/useAudioDetail";
import type { SpeakerMapping } from "@/features/transcription/hooks/useTranscriptionSpeakers";
import type { TranscriptMode } from "@/components/transcript/TranscriptView";
import { cn } from "@/lib/utils";
import { useTranscriptSearch } from "@/features/transcription/hooks/useTranscriptSearch";
import { TranscriptSearchBar } from "@/components/transcript/TranscriptSearchBar";

interface TranscriptSectionProps {
    audioId: string;
    currentWordIndex: number | null;
    currentTime: number;
    isPlaying: boolean;
    onSeek: (time: number) => void;
    // Lifted State Props
    transcript: Transcript | undefined;
    speakerMappings: Record<string, string>;
    transcriptMode: TranscriptMode;
    autoScrollEnabled: boolean;
    notesOpen: boolean;
    setNotesOpen: (open: boolean) => void;
    speakerRenameOpen: boolean;
    setSpeakerRenameOpen: (open: boolean) => void;
    downloadDialogOpen: boolean;
    setDownloadDialogOpen: (open: boolean) => void;
    downloadFormat: 'txt' | 'json';
    searchOpen: boolean;
    setSearchOpen: (open: boolean) => void;
    onSpeakerMappingsSaved?: (mappings: SpeakerMapping[]) => void;
}

function computeSegmentSelectionOffsets(transcript: Transcript | undefined, mode: TranscriptMode) {
    if (!transcript?.segments?.length) return [];

    const texts = transcript.segments.map(segment =>
        mode === 'compacted' ? segment.text.trim() : segment.text
    );
    const renderedText = mode === 'plain'
        ? (transcript.text || texts.join(' '))
        : texts.join(' ');
    let cursor = 0;

    return transcript.segments.map((segment, index) => {
        const text = texts[index];
        let startChar = renderedText.indexOf(text, cursor);
        if (startChar < 0 && text !== text.trim()) {
            startChar = renderedText.indexOf(text.trim(), cursor);
        }
        if (startChar < 0) startChar = cursor;
        const endChar = Math.min(renderedText.length, startChar + text.length);
        cursor = endChar + (index < transcript.segments!.length - 1 ? 1 : 0);

        return {
            startChar,
            endChar,
            startTime: segment.start,
            endTime: segment.end,
        };
    });
}

export function TranscriptSection({
    audioId,
    currentWordIndex,
    currentTime,
    isPlaying,
    onSeek,
    transcript,
    speakerMappings,
    transcriptMode,
    autoScrollEnabled,
    notesOpen,
    setNotesOpen,
    speakerRenameOpen,
    setSpeakerRenameOpen,
    downloadDialogOpen,
    setDownloadDialogOpen,
    downloadFormat,
    searchOpen,
    setSearchOpen,
    onSpeakerMappingsSaved,
    className
}: TranscriptSectionProps & { className?: string }) {
    const isMobile = useIsMobile();
    const isDesktop = useIsDesktop();
    const queryClient = useQueryClient();
    const { data: audio } = useAudioDetail(audioId);

    // Data hooks
    const { data: notes = [] } = useNotes(audioId);
    const { mutate: createNote } = useCreateNote(audioId);
    const { mutateAsync: updateNote } = useUpdateNote(audioId);
    const { mutateAsync: deleteNote } = useDeleteNote(audioId);

    // Refs
    const transcriptRef = useRef<HTMLDivElement>(null);
    const highlightedWordRef = useRef<HTMLSpanElement>(null);
    const [editTarget, setEditTarget] = useState<TranscriptEditTarget | null>(null);

    // Search
    const [searchQuery, setSearchQuery] = useState("");
    const { matchCount, activeMatchIndex, goToNext, goToPrevious, clearHighlights } =
        useTranscriptSearch(searchQuery, transcriptRef, transcriptMode);

    const handleCloseSearch = useCallback(() => {
        setSearchOpen(false);
        setSearchQuery("");
        clearHighlights();
    }, [setSearchOpen, clearHighlights]);

    // Cmd/Ctrl+F to open search
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'f') {
                e.preventDefault();
                setSearchOpen(true);
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [setSearchOpen]);

    // Compute offsets for selection logic
    const words = useMemo(() => transcript?.word_segments || [], [transcript?.word_segments]);
    const { offsets } = useMemo(() => computeWordOffsets(words), [words]);
    const selectionOffsets = useMemo(
        () => offsets.length > 0 ? offsets : computeSegmentSelectionOffsets(transcript, transcriptMode),
        [offsets, transcript, transcriptMode]
    );

    // Unified Selection Hook for both desktop and mobile
    const {
        menuState,
        showEditor,
        openEditor,
        closeEditor,
        dismissMenu
    } = useSelectionMenu(transcriptRef, selectionOffsets);

    // Auto-scroll logic
    useEffect(() => {
        if (currentWordIndex !== null && highlightedWordRef.current && autoScrollEnabled) {
            const highlightedElement = highlightedWordRef.current;
            const highlightedRect = highlightedElement.getBoundingClientRect();
            const viewportHeight = window.innerHeight;
            const buffer = viewportHeight * 0.2; // 20%
            const isAboveView = highlightedRect.top < buffer;
            const isBelowView = highlightedRect.bottom > (viewportHeight - buffer);

            if (isAboveView || isBelowView) {
                highlightedElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'center',
                });
            }
        }
    }, [currentWordIndex, autoScrollEnabled]);

    useEffect(() => {
        // Only enable click-to-seek on desktop devices with fine pointer
        if (!isDesktop) return;
        const el = transcriptRef.current;
        if (!el) return;
        const onClick = (e: MouseEvent) => {
            if (!(e.metaKey || e.ctrlKey)) return;
            const target = e.target as HTMLElement | null;
            if (!target) return;
            const wordEl = target.closest('span[data-word-index]') as HTMLElement | null;
            if (!wordEl) return;
            const startAttr = wordEl.getAttribute('data-start');
            const start = startAttr ? parseFloat(startAttr) : NaN;
            if (isNaN(start)) return;

            e.preventDefault();
            e.stopPropagation();
            onSeek(start);
        };
        el.addEventListener('click', onClick);
        return () => el.removeEventListener('click', onClick);
    }, [onSeek, isDesktop]);

    // Helpers
    const detectedSpeakers = useMemo(() => getTranscriptSpeakerOrder(transcript), [transcript]);

    const handleSaveNote = (content: string) => {
        if (menuState) {
            createNote({
                start_time: menuState.startTime,
                end_time: menuState.endTime,
                content: content,
                quote: menuState.selectedText,
                start_word_index: menuState.startIdx,
                end_word_index: menuState.endIdx
            });
            closeEditor();
            setNotesOpen(true);
        }
    };

    const handleListenFromHere = () => {
        if (menuState) {
            onSeek(menuState.startTime);
            closeEditor();
        }
    };

    const handleEditSelection = () => {
        if (!menuState) return;
        setEditTarget({
            requestId: Date.now(),
            startTime: menuState.startTime,
            selectedText: menuState.selectedText,
        });
        dismissMenu();
        window.getSelection()?.removeAllRanges();
    };

    const unclearPassages = useMemo(() => {
        const items = transcript?.word_segments?.length ? transcript.word_segments : transcript?.segments ?? [];
        const runs: { start: number; end: number }[] = [];
        let inRun = false;
        for (const item of items) {
            if (item.speaker || !Number.isFinite(item.start) || !Number.isFinite(item.end)) {
                inRun = false;
                continue;
            }
            const previous = runs[runs.length - 1];
            if (inRun && previous && item.start <= previous.end + 0.75) previous.end = Math.max(previous.end, item.end);
            else runs.push({ start: item.start, end: item.end });
            inRun = true;
        }
        const missingVoiceStart = Number(transcript?.metadata?.speaker_review_start ?? NaN);
        if (Number.isFinite(missingVoiceStart) && missingVoiceStart >= 0) runs.push({ start: missingVoiceStart, end: missingVoiceStart });
        return runs.sort((a, b) => a.start - b.start);
    }, [transcript]);
    const unlabelledWordCount = transcript?.word_segments?.filter(word => !word.speaker).length ?? 0;

    if (!transcript) return null;

    return (
        <div className="obsidian-pane p-4 md:p-5 min-h-[500px]">
            {audio?.candidate_transcript_revision_id && (
                <p role="status" className="mb-4 text-sm text-[var(--text-secondary)]">Rerun candidate ready. Your accepted transcript is shown below. Open Edit history to compare and accept the candidate.</p>
            )}
            {transcript.metadata?.text_status === 'needs_review' && (
                <p role="status" className="mb-4 text-sm text-[var(--text-secondary)]">Text needs review: no usable text was returned. This recording has not been verified as silence.</p>
            )}
            {transcript.metadata?.text_status !== 'needs_review' && ['uncertain', 'failed'].includes(transcript.metadata?.diarization_status || '') && (
                <div role="status" className="mb-4 text-sm text-[var(--text-secondary)]">
                    <p>Text ready · speakers need review. {unlabelledWordCount > 0
                        ? `${unlabelledWordCount} of ${transcript.word_segments!.length} words have no speaker label. `
                        : ''}Unattributed turns are marked “Speaker unclear”.</p>
                    {transcript.metadata?.speaker_review_reason === 'detected_voice_has_no_attributed_words' && (
                        <p>A detected voice has no attributed words. Review the overlapping or brief speech before trusting the speaker labels.</p>
                    )}
                    {unclearPassages.length > 0 && (
                        <button type="button" className="mt-2 underline underline-offset-4" onClick={() => {
                            const target = unclearPassages.find(passage => passage.start > currentTime + 0.25) ?? unclearPassages[0];
                            onSeek(Math.max(0, target.start));
                        }}>Review next unclear passage</button>
                    )}
                </div>
            )}
            {transcript.metadata?.alignment_status === 'failed' && (
                <p role="status" className="mb-4 text-sm text-[var(--text-secondary)]">Text recovered · alignment needs review. Word times come from the speech decoder.</p>
            )}
            <TranscriptEditor
                audioId={audioId}
                speakerMappings={speakerMappings}
                contextualTarget={editTarget}
            />

            {/* Transcript Content - Systematic Typography */}
            <div
                className={cn("relative font-sans", className)}
                style={{
                    // Allow text selection to work in children
                    WebkitUserSelect: 'text',
                    userSelect: 'text'
                }}
            >
                {searchOpen && (
                    <TranscriptSearchBar
                        query={searchQuery}
                        onQueryChange={setSearchQuery}
                        matchCount={matchCount}
                        activeMatchIndex={activeMatchIndex}
                        onNext={goToNext}
                        onPrevious={goToPrevious}
                        onClose={handleCloseSearch}
                    />
                )}
                <div className="w-full text-[var(--text-secondary)] leading-relaxed">
                    <div
                        ref={transcriptRef}
                        className="relative"
                        style={{
                            // Ensure this container doesn't interfere with touch events
                            touchAction: 'pan-y pinch-zoom'
                        }}
                    >
                        <TranscriptView
                            transcript={transcript}
                            mode={transcriptMode}
                            currentWordIndex={currentWordIndex}
                            currentTime={currentTime}
                            isPlaying={isPlaying}
                            notes={notes}
                            onSeek={onSeek}
                            highlightedWordRef={highlightedWordRef}
                            speakerMappings={speakerMappings}
                            autoScrollEnabled={autoScrollEnabled}
                        />
                    </div>
                </div>
            </div>

            {/* Download Dialog */}
            <DownloadDialog
                audioId={audioId}
                isOpen={downloadDialogOpen}
                onClose={setDownloadDialogOpen}
                initialFormat={downloadFormat}
            />

            {/* Speaker Rename Dialog */}
            <SpeakerRenameDialog
                open={speakerRenameOpen}
                onOpenChange={setSpeakerRenameOpen}
                transcriptionId={audioId}
                initialSpeakers={detectedSpeakers}
                onSpeakerMappingsUpdate={(mappings) => {
                    queryClient.invalidateQueries({ queryKey: ["speakerMappings", audioId] });
                    queryClient.invalidateQueries({ queryKey: ["transcript", audioId] });
                    queryClient.invalidateQueries({ queryKey: ["audio", audioId] });
                    queryClient.invalidateQueries({ queryKey: ["audioFiles"] });
                    queryClient.invalidateQueries({ queryKey: ["contacts", "appearances"] });
                    queryClient.invalidateQueries({ queryKey: ["contacts"] });
                    onSpeakerMappingsSaved?.(mappings);
                }}
            />
            {/* Portals */}
            {createPortal(
                <>
                    {/* Selection Menu Bubble (Glass) - Unified for desktop and mobile */}
                    {!showEditor && (
                        <TranscriptSelectionMenu
                            menuState={menuState}
                            onAddNote={openEditor}
                            onListenFromHere={handleListenFromHere}
                            onEditSelection={handleEditSelection}
                        />
                    )}

                    {/* Note Editor Dialog */}
                    <NoteEditorDialog
                        isOpen={showEditor}
                        quote={menuState?.selectedText || ""}
                        position={menuState ? { x: menuState.x, y: menuState.y } : { x: 0, y: 0 }}
                        onSave={handleSaveNote}
                        onCancel={closeEditor}
                    />

                    {/* Notes Sidebar - Premium Drawer */}
                    {notesOpen && (
                        <div className="fixed inset-y-0 right-0 w-[90vw] max-w-[400px] bg-[var(--bg-card)] border-l border-[var(--border-subtle)] shadow-[var(--shadow-float)] z-[9990] transition-transform duration-300 transform-gpu">
                            <div className="h-full flex flex-col">
                                <div className="px-6 py-5 border-b border-[var(--border-subtle)] flex items-center justify-between">
                                    <h3 className="font-bold text-[var(--text-primary)] flex items-center gap-2 text-lg">
                                        <StickyNote className="h-5 w-5 text-[var(--brand-solid)]" />
                                        Notes
                                        <span className="ml-1 text-xs font-bold rounded-full px-2 py-0.5 bg-[var(--bg-main)] text-[var(--text-secondary)] border border-[var(--border-subtle)]">
                                            {notes.length}
                                        </span>
                                    </h3>
                                    <button
                                        type="button"
                                        onClick={() => setNotesOpen(false)}
                                        className="h-8 w-8 inline-flex items-center justify-center rounded-[var(--radius-btn)] text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-main)] transition-colors"
                                        aria-label="Close notes"
                                    >
                                        <X className="h-5 w-5" />
                                    </button>
                                </div>
                                <div className="flex-1 overflow-y-auto px-6 py-4">
                                    <NotesSidebar
                                        notes={notes}
                                        onEdit={(id, content) => updateNote({ id, content })}
                                        onDelete={(id) => deleteNote(id)}
                                        onJumpTo={(t) => {
                                            onSeek(t);
                                            if (isMobile) setNotesOpen(false);
                                        }}
                                    />
                                </div>
                            </div>
                        </div>
                    )}
                </>,
                document.body
            )}
        </div>
    );
}
