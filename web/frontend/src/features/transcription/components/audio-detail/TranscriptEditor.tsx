import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, History } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/features/auth/hooks/useAuth';
import type { Transcript } from '@/features/transcription/hooks/useAudioDetail';

type Part = { text: string; speaker: string };
type Document = { version: string; transcript: Transcript | string; artifact_warning?: string };
type Revision = { id: number; kind: string; created_at: string; restored_from_id?: number };
type HistoryPage = { revisions: Revision[]; next_before: number | null };
type RevisionDetail = { revision: Revision; transcript: Transcript | string };
type Send = <T>(suffix: string, body?: unknown) => Promise<T>;
export type TranscriptEditTarget = { requestId: number; startTime: number; selectedText: string };
const normalize = (raw: Transcript | string): Transcript => typeof raw === 'string' ? { text: raw } : raw;
const textOf = (raw: Transcript | string, mappings: Record<string, string>) => {
    const t = normalize(raw);
    return t.segments?.length ? t.segments.map(s => `${s.speaker ? (mappings[s.speaker] || s.speaker_name || s.speaker) + ': ' : ''}${s.text}`).join('\n\n') : t.text || '';
};
const revisionLabel = (kind: string) => ({ original: 'Original transcript', edit: 'Text correction', speaker_edit: 'Speaker correction', passage_review: 'Passage review', restore: 'Restored version', before_edit: 'Before correction', before_rerun: 'Before rerun', candidate: 'Rerun candidate — review before accepting', candidate_accept: 'Accepted rerun' }[kind] || 'Saved version');
const time = (seconds: number) => Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : '';

export function TranscriptEditor({ audioId, speakerMappings, contextualTarget = null }: {
    audioId: string;
    speakerMappings: Record<string, string>;
    contextualTarget?: TranscriptEditTarget | null;
}) {
    const [mode, setMode] = useState<'edit' | 'history' | null>(null);
    const [editTarget, setEditTarget] = useState<TranscriptEditTarget | null>(null);

    useEffect(() => {
        if (!contextualTarget) return;
        setEditTarget(contextualTarget);
        setMode('edit');
    }, [contextualTarget]);

    return <>
        <div className="mb-4 flex flex-wrap justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => { setEditTarget(null); setMode('edit'); }}><Pencil className="h-4 w-4" />Edit text</Button>
            <Button variant="ghost" size="sm" onClick={() => { setEditTarget(null); setMode('history'); }}><History className="h-4 w-4" />Edit history</Button>
        </div>
        {mode && <LoadEditor
            key={`${audioId}:${editTarget?.requestId ?? 'toolbar'}`}
            audioId={audioId}
            initialMode={mode}
            initialTarget={editTarget}
            speakerMappings={speakerMappings}
            onClose={() => setMode(null)}
        />}
    </>;
}

function LoadEditor({ audioId, initialMode, initialTarget, speakerMappings, onClose }: {
    audioId: string; initialMode: 'edit' | 'history'; initialTarget: TranscriptEditTarget | null; speakerMappings: Record<string, string>; onClose: () => void;
}) {
    const { getAuthHeaders } = useAuth();
    const send: Send = async <T,>(suffix: string, body?: unknown): Promise<T> => {
        const response = await fetch(`/api/v1/transcription/${audioId}/transcript/${suffix}`, {
            method: body === undefined ? 'GET' : 'PUT',
            headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load or save the transcript.');
        return data as T;
    };
    const query = useQuery({ queryKey: ['transcriptEditor', audioId], queryFn: () => send<Document>('editor'), refetchOnWindowFocus: false, gcTime: 0, retry: false });
    if (!query.data || query.isPending) return <Dialog open onOpenChange={onClose}>
        <DialogContent><DialogHeader><DialogTitle>Edit transcript</DialogTitle><DialogDescription>Loading your saved transcript…</DialogDescription></DialogHeader>
            {query.error && <><p role="alert">{query.error.message}</p><Button onClick={() => query.refetch()}>Try again</Button></>}
        </DialogContent>
    </Dialog>;
    return <EditorSession audioId={audioId} initial={query.data} initialMode={initialMode} initialTarget={initialTarget} speakerMappings={speakerMappings} send={send} onClose={onClose} />;
}

function EditorSession({ audioId, initial, initialMode, initialTarget, speakerMappings, send, onClose }: {
    audioId: string; initial: Document; initialMode: 'edit' | 'history'; initialTarget: TranscriptEditTarget | null; speakerMappings: Record<string, string>; send: Send; onClose: () => void;
}) {
    // Freeze the base version while typing. Refetches must never rebase a draft silently.
    const [base] = useState(initial);
    const original = normalize(base.transcript);
    const segmented = !!original.segments?.length;
    const [parts, setParts] = useState<Part[][]>(() => segmented ? original.segments!.map(s => s.text.trim() ? [{ text: s.text, speaker: s.speaker || '' }] : []) : [[{ text: original.text || '', speaker: '' }]]);
    const texts = parts.map(section => section.map(p => p.text).join(''));
    const speakers = [...new Set(original.segments?.map(s => s.speaker || '') || [])];
    const speakerName = (key: string) => speakerMappings[key] || original.segments?.find(s => s.speaker === key)?.speaker_name || key || 'Unassigned';
    const attributionChanged = segmented && parts.some((section, i) => section.length !== 1 || section[0].speaker !== (original.segments![i].speaker || ''));
    const [tab, setTab] = useState(initialMode);
    const targetSection = useMemo(() => {
        if (!initialTarget || !original.segments?.length) return 0;
        const containing = original.segments.findIndex(segment =>
            initialTarget.startTime >= segment.start && initialTarget.startTime <= segment.end
        );
        if (containing >= 0) return containing;
        const distance = (index: number) => {
            const segment = original.segments![index];
            if (initialTarget.startTime < segment.start) return segment.start - initialTarget.startTime;
            return initialTarget.startTime - segment.end;
        };
        return original.segments.reduce((closest, _segment, index) =>
            distance(index) < distance(closest) ? index : closest
        , 0);
    }, [initialTarget, original.segments]);
    const pageSize = 30;
    const visibleSections = parts.flatMap((section, index) => section.length > 0 ? [index] : []);
    const [page, setPage] = useState(() => Math.floor(Math.max(0, visibleSections.indexOf(targetSection)) / pageSize));
    const lastPage = Math.max(0, Math.ceil(visibleSections.length / pageSize) - 1);
    const visiblePage = Math.min(page, lastPage);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [discard, setDiscard] = useState(false);
    const [selected, setSelected] = useState<number | null>(null);
    const [older, setOlder] = useState<Revision[]>([]);
    const [cursor, setCursor] = useState<number | null | undefined>(undefined);
    const client = useQueryClient();
    const history = useQuery({ queryKey: ['transcriptHistory', audioId], queryFn: () => send<HistoryPage>('revisions'), enabled: tab === 'history', refetchOnWindowFocus: false });
    const snapshot = useQuery({ queryKey: ['transcriptRevision', audioId, selected], queryFn: () => send<RevisionDetail>(`revisions/${selected}`), enabled: selected !== null, retry: false });
    const dirty = attributionChanged || texts.some((text, i) => text !== (segmented ? original.segments![i].text : original.text || ''));
    const close = () => { if (!busy) { if (dirty) setDiscard(true); else onClose(); } };
    const save = async (restoreID?: number) => {
        setBusy(true); setError('');
        try {
            const result = await send<Document>('editor', {
                expected_version: base.version,
                ...(restoreID !== undefined ? { restore_id: restoreID } : segmented ? { segment_parts: parts } : { text: texts[0] }),
            });
            client.setQueryData(['transcript', audioId], normalize(result.transcript));
            for (const queryKey of [['transcript', audioId], ['audio', audioId], ['audioFiles'], ['transcriptHistory', audioId], ['speakerMappings', audioId]]) {
                void client.invalidateQueries({ queryKey });
            }
            if (result.artifact_warning) { setError(result.artifact_warning); return; }
            onClose();
        } catch (e) { setError(e instanceof Error ? e.message : 'Could not save. Your draft is still here.'); }
        finally { setBusy(false); }
    };
    const loadOlder = async () => {
        const before = cursor === undefined ? history.data?.next_before : cursor;
        if (!before) return;
        setBusy(true); setError('');
        try { const next = await send<HistoryPage>(`revisions?before=${before}`); setOlder(prev => [...prev, ...next.revisions]); setCursor(next.next_before); }
        catch (e) { setError(e instanceof Error ? e.message : 'Could not load older versions.'); }
        finally { setBusy(false); }
    };
    const revision = snapshot.data;
    const rows = [...(history.data?.revisions || []), ...older];
    return <Dialog open onOpenChange={close}>
        <DialogContent
            className="max-w-4xl flex flex-col overflow-hidden"
            showCloseButton={!busy}
            onOpenAutoFocus={event => {
                if (initialTarget) event.preventDefault();
            }}
        >
            <DialogHeader>
                <DialogTitle>{tab === 'edit' ? 'Edit transcript' : 'Transcript edit history'}</DialogTitle>
                <DialogDescription>Each save keeps an earlier version you can restore. Text and speaker corrections are both saved in history.</DialogDescription>
            </DialogHeader>
            <div className="flex gap-2">
                <Button variant={tab === 'edit' ? 'secondary' : 'ghost'} size="sm" onClick={() => setTab('edit')}>Edit text</Button>
                <Button variant={tab === 'history' ? 'secondary' : 'ghost'} size="sm" onClick={() => setTab('history')}>History</Button>
            </div>
            <div className="min-h-0 overflow-y-auto space-y-4 pr-1">
                {error && <p role="alert" className="text-destructive whitespace-pre-wrap">{error}</p>}
                {tab === 'edit' ? <>
                    <p className="text-sm text-muted-foreground">Correct text or choose its speaker. For part of a section, highlight the phrase and separate it first. Split times are approximate; word highlighting pauses after corrections.</p>
                    {visibleSections.slice(visiblePage * pageSize, (visiblePage + 1) * pageSize).map((i, index) => {
                        const sectionNumber = visiblePage * pageSize + index + 1;
                        const seg = original.segments?.[i];
                        return <div key={i} className="space-y-2">
                            <label htmlFor={`transcript-section-${i}`} className="text-sm font-medium">
                                {segmented ? `Section ${sectionNumber}` : 'Transcript text'}
                                {seg && <span className="ml-2 text-muted-foreground">{time(seg.start)} · {seg.speaker ? speakerMappings[seg.speaker] || seg.speaker_name || seg.speaker : ''}</span>}
                            </label>
                            {parts[i].map((part, partIndex) => <PartEditor key={partIndex} part={part} busy={busy}
                                label={segmented ? `Section ${sectionNumber}${parts[i].length > 1 ? ` part ${partIndex + 1}` : ''}` : 'Transcript'}
                                speakers={segmented ? speakers : []} speakerName={speakerName}
                                autoFocus={tab === 'edit' && i === targetSection && partIndex === 0 && !!initialTarget}
                                initialSelectedText={i === targetSection && partIndex === 0 ? initialTarget?.selectedText : undefined}
                                onChange={next => setParts(previous => previous.map((section, sectionIndex) => sectionIndex === i ? section.flatMap((value, j) => j === partIndex ? next : [value]) : section))}
                                onBlur={() => setParts(previous => previous.map((section, sectionIndex) => sectionIndex === i ? section.filter(value => value.text.trim()) : section))} />)}
                        </div>;
                    })}
                    {visibleSections.length === 0 && <p className="text-sm text-muted-foreground">Transcript is empty.</p>}
                    {visibleSections.length > pageSize && <div className="flex items-center gap-3">
                        <Button variant="outline" size="sm" disabled={visiblePage === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>Previous sections</Button>
                        <span className="text-sm">{visiblePage * pageSize + 1}–{Math.min(visibleSections.length, (visiblePage + 1) * pageSize)} of {visibleSections.length}</span>
                        <Button variant="outline" size="sm" disabled={visiblePage >= lastPage} onClick={() => setPage(p => Math.min(lastPage, p + 1))}>Next sections</Button>
                    </div>}
                </> : <>
                    {history.isPending && <p>Loading history…</p>}
                    {history.error && <p role="alert">Could not load history. <button onClick={() => history.refetch()}>Try again</button></p>}
                    {history.isSuccess && rows.length === 0 && <p>No saved edits yet. Your original will be preserved when you save your first correction.</p>}
                    <div className="flex flex-wrap gap-2" aria-label="Saved versions">
                        {rows.map(r => <Button key={r.id} size="sm" variant={selected === r.id ? 'secondary' : 'outline'} onClick={() => setSelected(r.id)}>
                            {revisionLabel(r.kind)} · {new Date(r.created_at).toLocaleString()}
                        </Button>)}
                    </div>
                    {(cursor === undefined ? history.data?.next_before : cursor) && <Button variant="outline" size="sm" disabled={busy} onClick={loadOlder}>Older versions</Button>}
                    {selected && snapshot.isPending && <p>Loading version…</p>}
                    {snapshot.error && <p role="alert">Could not load this version. <button onClick={() => snapshot.refetch()}>Try again</button></p>}
                    {revision && <>
                        <div className="grid gap-4 md:grid-cols-2">
                            <div><h3 className="font-medium mb-2">Selected version</h3><p data-testid="revision-text" className="whitespace-pre-wrap break-words rounded border p-3 text-sm">{textOf(revision.transcript, revision.revision.kind === 'candidate' ? {} : speakerMappings) || '(Empty transcript)'}</p></div>
                            <div><h3 className="font-medium mb-2">Current transcript</h3><p className="whitespace-pre-wrap break-words rounded border p-3 text-sm">{textOf(base.transcript, speakerMappings) || '(Empty transcript)'}</p></div>
                        </div>
                        <p className="text-sm text-muted-foreground">Restoring creates a new saved version. Later corrections remain in history.</p>
                        {dirty && <p className="text-sm">Save or discard your unsaved text before restoring a version.</p>}
                        <Button disabled={busy || dirty || JSON.stringify(revision.transcript) === JSON.stringify(base.transcript)} onClick={() => save(revision.revision.id)}>{revision.revision.kind === 'candidate' ? 'Accept this candidate' : 'Restore this version'}</Button>
                    </>}
                </>}
            </div>
            {discard ? <div className="border rounded p-3 space-y-3"><p>Discard your unsaved corrections?</p><div className="flex gap-2"><Button variant="outline" onClick={() => setDiscard(false)}>Keep editing</Button><Button variant="destructive" onClick={onClose}>Discard changes</Button></div></div> : <DialogFooter>
                <Button variant="outline" onClick={close} disabled={busy}>{tab === 'edit' ? 'Cancel' : 'Close'}</Button>
                {tab === 'edit' && <Button disabled={!dirty || busy} onClick={() => save()}>{busy ? 'Saving…' : 'Save correction'}</Button>}
            </DialogFooter>}
        </DialogContent>
    </Dialog>;
}

function PartEditor({ part, label, speakers, speakerName, busy, autoFocus = false, initialSelectedText, onChange, onBlur }: {
    part: Part; label: string; speakers: string[]; speakerName: (key: string) => string; busy: boolean;
    autoFocus?: boolean; initialSelectedText?: string; onChange: (parts: Part[]) => void; onBlur: () => void;
}) {
    const [selection, setSelection] = useState({ start: 0, end: 0 });
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const initialTextRef = useRef(part.text);
    const didInitialFocusRef = useRef(false);

    useEffect(() => {
        if (!autoFocus || !textareaRef.current || didInitialFocusRef.current) return;
        const field = textareaRef.current;
        const initialText = initialTextRef.current;
        const selected = initialSelectedText?.trim() || '';
        let start = selected ? initialText.indexOf(selected) : -1;
        let end = start >= 0 ? start + selected.length : start;
        if (selected && start < 0 && selected.includes(initialText.trim())) {
            start = initialText.indexOf(initialText.trim());
            end = start + initialText.trim().length;
        }
        if (start < 0) start = end = 0;
        const focusTimer = window.setTimeout(() => {
            didInitialFocusRef.current = true;
            field.focus();
            field.setSelectionRange(start, end);
            setSelection({ start, end });
            field.scrollIntoView({ block: 'center' });
        });
        return () => window.clearTimeout(focusTimer);
    }, [autoFocus, initialSelectedText]);

    const canSplit = selection.end > selection.start && (selection.start > 0 || selection.end < part.text.length);
    const split = () => {
        const chunks = [part.text.slice(0, selection.start), part.text.slice(selection.start, selection.end), part.text.slice(selection.end)];
        // Keep whitespace with neighboring speech rather than creating empty turns.
        if (!chunks[1].trim()) return;
        if (!chunks[0].trim()) { chunks[1] = chunks[0] + chunks[1]; chunks[0] = ''; }
        if (!chunks[2].trim()) { chunks[1] += chunks[2]; chunks[2] = ''; }
        onChange(chunks.filter(Boolean).map(text => ({ text, speaker: part.speaker })));
        setSelection({ start: 0, end: 0 });
    };
    return <div className="space-y-2 rounded border p-3">
        {speakers.length > 0 && <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm">Speaker <select aria-label={`${label} speaker`} value={part.speaker} disabled={busy}
                className="ml-2 max-w-full rounded border bg-background p-2" onChange={e => onChange([{ ...part, speaker: e.target.value }])}>
                {speakers.map(key => <option key={key} value={key}>{speakerName(key)}</option>)}
            </select></label>
            <Button variant="outline" size="sm" disabled={busy || !canSplit} onClick={split}>Separate selected phrase</Button>
        </div>}
        <Textarea ref={textareaRef} autoFocus={autoFocus} aria-label={`${label} text`} value={part.text} disabled={busy}
            rows={Math.min(10, Math.max(3, Math.ceil(part.text.length / 95)))}
            onSelect={e => setSelection({ start: e.currentTarget.selectionStart, end: e.currentTarget.selectionEnd })}
            onChange={e => { onChange([{ ...part, text: e.target.value }]); setSelection({ start: 0, end: 0 }); }}
            onBlur={onBlur}
            className="font-reading text-base leading-relaxed" />
    </div>;
}
