import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BookMarked, Check, FolderInput, Loader2, MoreHorizontal, StopCircle, Trash2, Type, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { TranscribeDDialog } from "@/components/TranscribeDDialog";
import type { WhisperXParams } from "@/components/TranscriptionConfigDialog";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/features/auth/hooks/useAuth";
import { useFolders, useMoveToFolder } from "@/features/transcription/hooks/useFolders";
import type { AudioFile } from "@/features/transcription/hooks/useAudioFiles";

type Action = "transcribe" | "delete" | "stop";
const recordingTitle = (file: AudioFile) => file.title || file.audio_path.split(/[\\/]/).pop() || "Untitled Recording";

// One controller/dialog set for the whole sidebar, including when a moved row
// disappears from the current folder filter while its request is finishing.
export function useRecordingActions(onDeleted: (id: string) => void) {
    const { getAuthHeaders } = useAuth();
    const { toast } = useToast();
    const queryClient = useQueryClient();
    const { data: folders = [] } = useFolders();
    const moveToFolder = useMoveToFolder();
    const [selection, setSelection] = useState<{ file: AudioFile; action: Action } | null>(null);
    const [busy, setBusy] = useState<Set<string>>(new Set());
    const inFlight = useRef(new Set<string>());
    const [error, setError] = useState<string | null>(null);

    const refresh = (id: string) => {
        void queryClient.invalidateQueries({ queryKey: ["audioFiles"] });
        void queryClient.invalidateQueries({ queryKey: ["audio", id] });
        void queryClient.invalidateQueries({ queryKey: ["transcript", id] });
    };
    const request = async (url: string, method: string, body?: unknown) => {
        const response = await fetch(url, {
            method,
            headers: { ...getAuthHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        if (!response.ok) {
            const result = await response.json().catch(() => null);
            throw new Error(result?.error || "The recording could not be updated. Please try again.");
        }
    };
    const run = async (file: AudioFile, action: () => Promise<void>, inDialog = false) => {
        if (inFlight.current.has(file.id)) return;
        inFlight.current.add(file.id);
        setBusy(new Set(inFlight.current));
        setError(null);
        try {
            await action();
            refresh(file.id);
            if (inDialog) setSelection(null);
        } catch (cause) {
            const message = cause instanceof Error ? cause.message : "Please try again.";
            if (inDialog) setError(message);
            toast({ title: "Recording action failed", description: message });
        } finally {
            inFlight.current.delete(file.id);
            setBusy(new Set(inFlight.current));
        }
    };
    const select = (file: AudioFile, action: Action) => {
        setError(null);
        setSelection({ file, action });
    };
    const close = (open: boolean) => {
        if (!open && selection && !inFlight.current.has(selection.file.id)) setSelection(null);
    };
    const start = async (params: WhisperXParams) => {
        if (!selection) return;
        const file = selection.file;
        if (!!file.is_multi_track !== !!params.is_multi_track_enabled) {
            toast({ title: "Choose a compatible profile", description: file.is_multi_track
                ? "This recording requires a profile with multi-track transcription enabled."
                : "Choose a profile for single-track audio." });
            return;
        }
        await run(file, () => request(`/api/v1/transcription/${file.id}/start`, "POST", params), true);
    };
    const confirm = async () => {
        if (!selection) return;
        const { file, action } = selection;
        await run(file, async () => {
            if (action === "delete") {
                await request(`/api/v1/transcription/${file.id}`, "DELETE");
                onDeleted(file.id);
            } else {
                await request(`/api/v1/transcription/${file.id}/kill`, "POST");
            }
        }, true);
    };
    const deleting = selection?.action === "delete";
    const loading = !!selection && busy.has(selection.file.id);

    return {
        menu: (file: AudioFile) => {
            const active = file.status === "processing" || file.status === "pending";
            const synced = !!file.obsidian_synced_at;
            const stale = synced && !!file.updated_at && new Date(file.updated_at).getTime() - new Date(file.obsidian_synced_at!).getTime() > 10000;
            return (
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0 text-[var(--text-secondary)]" aria-label={`Actions for ${recordingTitle(file)}`} disabled={busy.has(file.id)}>
                            {busy.has(file.id) ? <Loader2 className="h-4 w-4 animate-spin" /> : <MoreHorizontal className="h-4 w-4" />}
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56">
                        {!active && <DropdownMenuItem onSelect={() => select(file, "transcribe")}><Wand2 className="mr-2 h-4 w-4" />Transcribe</DropdownMenuItem>}
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger><FolderInput className="mr-2 h-4 w-4" />Move to Folder</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="max-h-72 overflow-y-auto">
                                {["", ...folders].map(folder => <DropdownMenuItem key={folder} onSelect={() => void run(file, async () => { await moveToFolder.mutateAsync({ jobId: file.id, folder }); })}>
                                    <span className="flex-1">{folder || "Unfiled"}</span>{(file.folder || "") === folder && <Check className="ml-2 h-4 w-4" />}
                                </DropdownMenuItem>)}
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                        {file.status === "completed" && <>
                            <DropdownMenuItem onSelect={() => void run(file, async () => { await request(`/api/v1/obsidian/sync/${file.id}`, "POST"); toast({ title: "Synced to Obsidian" }); })}>
                                <BookMarked className="mr-2 h-4 w-4" />{synced ? stale ? "Resync to Obsidian (outdated)" : "Resync to Obsidian" : "Publish to Obsidian"}
                            </DropdownMenuItem>
                            <DropdownMenuItem onSelect={() => void run(file, async () => { await request(`/api/v1/transcription/${file.id}/title/auto?force=true`, "POST"); toast({ title: "Title generated" }); })}>
                                <Type className="mr-2 h-4 w-4" />{file.title ? "Regenerate Title" : "Generate Title"}
                            </DropdownMenuItem>
                        </>}
                        <DropdownMenuSeparator />
                        <DropdownMenuItem className="text-[var(--error)]" onSelect={() => select(file, active ? "stop" : "delete")}>
                            {active ? <StopCircle className="mr-2 h-4 w-4" /> : <Trash2 className="mr-2 h-4 w-4" />}{active ? "Stop Transcription" : "Delete Recording"}
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            );
        },
        dialogs: <>
            {selection?.action === "transcribe" && <TranscribeDDialog open onOpenChange={close} onStartTranscription={start} loading={loading} />}
            <AlertDialog open={!!selection && selection.action !== "transcribe"} onOpenChange={close}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{deleting ? "Delete Recording?" : "Stop Transcription?"}</AlertDialogTitle>
                        <AlertDialogDescription>
                            {deleting ? `Permanently delete “${selection ? recordingTitle(selection.file) : ""}” and its transcription data? This cannot be undone.`
                                : `Stop transcription for “${selection ? recordingTitle(selection.file) : ""}”? Partially transcribed data may be saved.`}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {error && <p role="alert" className="text-sm text-[var(--error)]">{error}</p>}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={loading}>Cancel</AlertDialogCancel>
                        <AlertDialogAction disabled={loading} className="bg-[var(--error)] text-white hover:opacity-90" onClick={event => { event.preventDefault(); void confirm(); }}>
                            {loading ? "Working…" : deleting ? "Delete" : "Stop Transcription"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>,
    };
}
