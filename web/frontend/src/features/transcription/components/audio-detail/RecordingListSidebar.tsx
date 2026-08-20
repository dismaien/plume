import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { AlertCircle, CheckCircle2, Clock3, Loader2, PanelLeftClose, Search } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { formatRecordingSidebarDateTime } from "@/lib/recording-date";
import { useAudioListInfinite, type AudioFile } from "@/features/transcription/hooks/useAudioFiles";

interface RecordingListSidebarProps {
    currentAudioId: string;
    dashboardReturnPath: string;
    onClose: () => void;
}

interface DashboardListParams {
    search: string;
    sortBy: string;
    sortOrder: "asc" | "desc";
    folder: string | null;
    status: string;
    speaker: string;
    speakerStatus: string;
}

function parseDashboardListParams(returnPath: string): DashboardListParams {
    const url = new URL(returnPath || "/", window.location.origin);
    const params = url.searchParams;

    return {
        search: params.get("q") ?? "",
        sortBy: params.get("sort_by") ?? "recorded_at",
        sortOrder: params.get("sort_order") === "asc" ? "asc" : "desc",
        folder: params.has("folder") ? params.get("folder") ?? "" : null,
        status: params.get("status") ?? "",
        speaker: params.get("speaker") ?? "",
        speakerStatus: params.get("speaker_status") ?? "",
    };
}

function formatDuration(durationMs?: number) {
    if (!durationMs || durationMs <= 0) return null;

    const totalSeconds = Math.round(durationMs / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const pad = (value: number) => String(value).padStart(2, "0");

    return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

function RecordingStatusIcon({ status }: { status: AudioFile["status"] }) {
    if (status === "processing" || status === "pending") {
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--brand-solid)]" aria-label="Processing" />;
    }
    if (status === "failed") {
        return <AlertCircle className="h-3.5 w-3.5 text-[var(--error)]" aria-label="Failed" />;
    }
    if (status === "completed") {
        return <CheckCircle2 className="h-3.5 w-3.5 text-[var(--success)]" aria-label="Completed" />;
    }
    return <Clock3 className="h-3.5 w-3.5 text-[var(--text-tertiary)]" aria-label="Uploaded" />;
}

export function RecordingListSidebar({ currentAudioId, dashboardReturnPath, onClose }: RecordingListSidebarProps) {
    const navigate = useNavigate();
    const inheritedParams = useMemo(
        () => parseDashboardListParams(dashboardReturnPath),
        [dashboardReturnPath],
    );
    const [search, setSearch] = useState(inheritedParams.search);
    const selectedRowRef = useRef<HTMLButtonElement | null>(null);
    const { ref: loadMoreRef, inView } = useInView({ rootMargin: "240px" });

    useEffect(() => {
        setSearch(inheritedParams.search);
    }, [inheritedParams.search]);

    const {
        data,
        fetchNextPage,
        hasNextPage,
        isFetchingNextPage,
        isLoading,
    } = useAudioListInfinite({
        limit: 30,
        search,
        sortBy: inheritedParams.sortBy,
        sortOrder: inheritedParams.sortOrder,
        folder: inheritedParams.folder,
        status: inheritedParams.status,
        speaker: inheritedParams.speaker,
        speakerStatus: inheritedParams.speakerStatus,
    });

    const recordings = useMemo(
        () => data?.pages.flatMap((page) => page.jobs) ?? [],
        [data],
    );
    const total = data?.pages[0]?.pagination.total ?? recordings.length;

    useEffect(() => {
        if (inView && hasNextPage && !isFetchingNextPage) {
            fetchNextPage();
        }
    }, [fetchNextPage, hasNextPage, inView, isFetchingNextPage]);

    useEffect(() => {
        selectedRowRef.current?.scrollIntoView({ block: "nearest" });
    }, [currentAudioId, recordings.length]);

    const openRecording = useCallback((id: string) => {
        navigate(`/audio/${id}`, {
            state: { from: dashboardReturnPath },
            preventScrollReset: true,
        });
    }, [dashboardReturnPath, navigate]);

    return (
        <aside
            aria-label="Recordings list"
            className={cn(
                "absolute inset-y-0 left-0 z-40 flex w-[min(22rem,calc(100vw-1rem))] flex-col",
                "border-r border-[var(--border-subtle)] bg-[var(--bg-main)] shadow-[var(--shadow-float)]",
                "lg:relative lg:z-0 lg:w-[20rem] xl:w-[22rem] lg:shadow-none",
            )}
        >
            <div className="flex h-[4.25rem] flex-shrink-0 items-center justify-between border-b border-[var(--border-subtle)] px-4">
                <div className="min-w-0">
                    <h2 className="text-base font-semibold text-[var(--text-primary)]">Recordings</h2>
                    <p className="text-xs tabular-nums text-[var(--text-tertiary)]">
                        {recordings.length} of {total}
                    </p>
                </div>
                <Tooltip>
                    <TooltipTrigger asChild>
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label="Hide recordings list"
                            onClick={onClose}
                            className="h-9 w-9 text-[var(--text-secondary)]"
                        >
                            <PanelLeftClose className="h-4 w-4" />
                        </Button>
                    </TooltipTrigger>
                    <TooltipContent>Hide recordings list</TooltipContent>
                </Tooltip>
            </div>

            <div className="border-b border-[var(--border-subtle)] p-3">
                <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                    <Input
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder="Search recordings"
                        aria-label="Search recordings"
                        className="h-9 bg-[var(--bg-muted-pane)] pl-9 text-sm"
                    />
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 py-2 scrollbar-thin">
                {isLoading ? (
                    <div className="space-y-1.5">
                        {Array.from({ length: 8 }).map((_, index) => (
                            <div key={index} className="h-[4.5rem] animate-pulse rounded-[var(--radius-btn)] bg-[var(--bg-elevated)]" />
                        ))}
                    </div>
                ) : recordings.length === 0 ? (
                    <div className="px-4 py-10 text-center text-sm text-[var(--text-tertiary)]">No recordings found</div>
                ) : (
                    <div className="space-y-1">
                        {recordings.map((file) => {
                            const selected = file.id === currentAudioId;
                            const duration = formatDuration(file.duration);
                            return (
                                <button
                                    key={file.id}
                                    ref={selected ? selectedRowRef : undefined}
                                    type="button"
                                    aria-current={selected ? "page" : undefined}
                                    onClick={() => openRecording(file.id)}
                                    className={cn(
                                        "group w-full rounded-[var(--radius-btn)] border px-3 py-2.5 text-left transition-colors",
                                        selected
                                            ? "border-[var(--brand-solid)]/45 bg-[var(--brand-light)]"
                                            : "border-transparent hover:border-[var(--border-subtle)] hover:bg-[var(--bg-elevated)]",
                                    )}
                                >
                                    <div className="flex items-start gap-2">
                                        <span className={cn(
                                            "line-clamp-2 min-w-0 flex-1 text-sm font-medium leading-snug",
                                            selected ? "text-[var(--brand-solid)]" : "text-[var(--text-primary)]",
                                        )}>
                                            {file.title || "Untitled Recording"}
                                        </span>
                                        <span className="mt-0.5 flex-shrink-0">
                                            <RecordingStatusIcon status={file.status} />
                                        </span>
                                    </div>
                                    <div className="mt-1.5 flex items-center justify-between gap-3 text-xs tabular-nums text-[var(--text-tertiary)]">
                                        <span>{formatRecordingSidebarDateTime(file.recorded_at, file.created_at)}</span>
                                        {duration && <span>{duration}</span>}
                                    </div>
                                </button>
                            );
                        })}
                        <div ref={loadMoreRef} className="flex h-10 items-center justify-center">
                            {isFetchingNextPage && <Loader2 className="h-4 w-4 animate-spin text-[var(--brand-solid)]" />}
                        </div>
                    </div>
                )}
            </div>
        </aside>
    );
}
