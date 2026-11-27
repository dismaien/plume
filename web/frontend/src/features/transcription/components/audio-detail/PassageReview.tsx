import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/features/auth/hooks/useAuth';
import type { Transcript } from '@/features/transcription/hooks/useAudioDetail';
import { formatSpeakerLabel } from '@/lib/speaker-utils';
import { getReviewPassages, samePassage, type ReviewPassage } from './passage-review';
import { Button } from '@/components/ui/button';

export function PassageReview({
    audioId,
    target,
    speakers,
    mappings,
    onSaved,
    onSkip,
    onClose,
    onReplay,
}: {
    audioId: string;
    target: ReviewPassage;
    speakers: string[];
    mappings: Record<string, string>;
    onSaved: (transcript: Transcript, warning?: string) => void;
    onSkip: () => void;
    onClose: () => void;
    onReplay: () => void;
}) {
    const { getAuthHeaders } = useAuth();
    const headers = useRef(getAuthHeaders);
    headers.current = getAuthHeaders;
    const client = useQueryClient();
    const [text, setText] = useState(target.original);
    const [speaker, setSpeaker] = useState(target.speaker);
    const [newName, setNewName] = useState('');
    const newSpeakerOption = '__new_passage_speaker__';
    const selectedSpeaker =
        speaker === newSpeakerOption
            ? speakers.find(
                  (s) =>
                      (mappings[s] || formatSpeakerLabel(s)).toLocaleLowerCase() ===
                      newName.trim().toLocaleLowerCase(),
              ) || newName.trim()
            : speaker;
    const [version, setVersion] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [attempt, setAttempt] = useState(0);
    const endpoint = `/api/v1/transcription/${audioId}/transcript/editor`;
    useEffect(() => {
        const controller = new AbortController();
        setVersion('');
        setError('');
        (async () => {
            const res = await fetch(endpoint, { headers: headers.current(), signal: controller.signal });
            if (!res.ok) throw Error('Could not open the saved passage.');
            const doc = await res.json();
            if (!getReviewPassages(doc.transcript).some((p) => samePassage(p, target)))
                throw Error('This passage has changed. Close review and reopen the latest transcript.');
            setVersion(doc.version);
        })().catch((e) => {
            if (!controller.signal.aborted) setError(e.message);
        });
        return () => controller.abort();
    }, [endpoint, target, attempt]);
    const save = async (who: string) => {
        if (!version || busy || !text.trim()) return;
        setBusy(true);
        setError('');
        try {
            const res = await fetch(endpoint, {
                method: 'PUT',
                headers: { ...headers.current(), 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    expected_version: version,
                    passage_review: { ...target, speaker: who, text: text.trim() },
                }),
            });
            const doc = await res.json();
            if (!res.ok) {
                if (res.status === 409) setVersion('');
                throw Error(doc.error || 'Could not save this review.');
            }
            client.setQueryData(['transcript', audioId], doc.transcript);
            for (const key of [
                ['transcript', audioId],
                ['audio', audioId],
                ['audioFiles'],
                ['transcriptEditor', audioId],
                ['transcriptHistory', audioId],
            ])
                void client.invalidateQueries({ queryKey: key });
            onSaved(doc.transcript, doc.artifact_warning);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not save this review.');
        } finally {
            setBusy(false);
        }
    };
    return (
        <form
            aria-label="Review passage"
            className="my-3 space-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-card)] p-4 text-sm text-[var(--text-primary)]"
            onSubmit={(e) => {
                e.preventDefault();
                void save(selectedSpeaker);
            }}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <strong>Review passage</strong>
                <Button type="button" variant="ghost" size="sm" onClick={onReplay}>
                    Listen from here
                </Button>
            </div>
            <label className="block">
                Words
                <textarea
                    aria-label="Passage words"
                    value={text}
                    disabled={busy}
                    onChange={(e) => setText(e.target.value)}
                    rows={2}
                    className="mt-1 block w-full rounded border bg-transparent p-2"
                />
            </label>
            <label className="block">
                Speaker
                <select
                    aria-label="Passage speaker"
                    value={speaker}
                    disabled={busy}
                    onChange={(e) => setSpeaker(e.target.value)}
                    className="ml-2 rounded border bg-[var(--bg-card)] p-1"
                >
                    <option value="">Speaker unclear</option>
                    {speakers.map((s) => (
                        <option key={s} value={s}>
                            {mappings[s] || formatSpeakerLabel(s)}
                        </option>
                    ))}
                    <option value={newSpeakerOption}>Enter a name…</option>
                </select>
            </label>
            {speaker === newSpeakerOption && (
                <label className="block">
                    Speaker name
                    <input
                        className="ml-2 rounded border bg-transparent p-1"
                        value={newName}
                        maxLength={90}
                        onChange={(e) => setNewName(e.target.value)}
                        disabled={busy}
                    />
                </label>
            )}
            {error && (
                <p role="alert">
                    {error}{' '}
                    {!version && (
                        <button type="button" className="underline" onClick={() => setAttempt((n) => n + 1)}>
                            Reload saved passage
                        </button>
                    )}
                </p>
            )}
            <div className="flex flex-wrap gap-2">
                <Button
                    type="submit"
                    size="sm"
                    disabled={busy || !version || !text.trim() || !selectedSpeaker}
                >
                    {busy
                        ? 'Saving…'
                        : speaker === target.speaker && text.trim() === target.original
                          ? 'Confirm speaker'
                          : 'Save correction'}
                </Button>
                <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy || !version || !text.trim()}
                    onClick={() => void save('')}
                >
                    Mark reviewed and unclear
                </Button>
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onSkip}>
                    Skip
                </Button>
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onClose}>
                    Close
                </Button>
            </div>
        </form>
    );
}
