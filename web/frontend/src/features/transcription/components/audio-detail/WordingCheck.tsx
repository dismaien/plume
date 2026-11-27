import { useMemo, useState } from 'react';
import { PassageReview } from './PassageReview';
import { getReviewPassages } from './passage-review';
import type { WordingReport, Transcript } from '../../hooks/useAudioDetail';
import { Button } from '@/components/ui/button';

export function WordingCheck({ report, transcript, audioId, onListen, onEdit }: {
    report: WordingReport;
    transcript: Transcript;
    audioId: string;
    onListen: (time: number) => void;
    onEdit: (time: number) => void;
}) {
    const [index, setIndex] = useState(0);
    const differences = report.differences ?? [];
    const position = Math.min(index, differences.length - 1);
    const passage = differences[position];
    const [editingTime, setEditingTime] = useState<number | null>(null);
    const [turnIndex, setTurnIndex] = useState<number | null>(null);
    const [message, setMessage] = useState('');
    const turns = useMemo(() => editingTime === null || !passage ? [] :
        getReviewPassages(transcript, true).filter(turn => turn.end >= passage.start && turn.start <= passage.end),
        [editingTime, passage, transcript]);
    const selected = (turnIndex === null ? undefined : turns[turnIndex]) ?? turns.reduce<typeof turns[number] | undefined>((best, turn) => {
        const distance = (t: typeof turn) => Math.max(0, t.start - editingTime!, editingTime! - t.end);
        return !best || distance(turn) < distance(best) ? turn : best;
    }, undefined);
    const changePassage = (newIndex: number) => {
        setEditingTime(null);
        setMessage('');
        setTurnIndex(null);
        setIndex(newIndex);
    };
    const editPassage = () => {
        if (!passage) return;
        setMessage('');
        setTurnIndex(null);
        if (transcript.word_segments?.length || transcript.segments?.length) {
            setEditingTime(passage.target);
        } else {
            onEdit(passage.target);
        }
    };
    const style = 'mb-4 text-sm text-[var(--text-secondary)]';
    if (report.status !== 'complete') {
        return <p className={style}>{report.status === 'unavailable'
            ? 'Independent wording check unavailable on this installation.'
            : 'The independent wording check could not finish.'}</p>;
    }
    return <div className={style}>
        {!!report.incomplete && <p>{report.incomplete} {report.incomplete === 1 ? 'excerpt exceeded' : 'excerpts exceeded'} the second pass length limit.</p>}
        {passage ? <details className="rounded-lg border p-3">
            <summary className="cursor-pointer font-medium">Wording check · {differences.length} {differences.length === 1 ? 'difference' : 'differences'}</summary>
            <div className="mt-3 space-y-2">
                <p className="text-xs">Passage {position + 1} of {differences.length} · {Math.floor(passage.target / 60)}:{String(Math.floor(passage.target % 60)).padStart(2, '0')}</p>
                <p><strong>First pass</strong><br />{passage.original || 'No words in this passage.'}</p>
                <p><strong>Second pass</strong><br />{passage.alternative || 'No words in this passage.'}</p>
                <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" onClick={() => onListen(Math.max(passage.start, passage.target - 2))}>Listen</Button>
                    <Button variant="outline" size="sm" onClick={editPassage}>Edit passage</Button>
                    {differences.length > 1 && <>
                        <Button variant="ghost" size="sm" disabled={position === 0} onClick={() => changePassage(position - 1)}>Previous</Button>
                        <Button variant="ghost" size="sm" disabled={position === differences.length - 1} onClick={() => changePassage(position + 1)}>Next</Button>
                    </>}
                </div>
                {editingTime !== null && !selected && <p>No timed turn in this excerpt.</p>}
                {selected && <>
                    {turns.length > 1 && <label>Turn to edit
                        <select className="ml-2 max-w-full" value={turns.indexOf(selected)} onChange={event => {
                            setTurnIndex(Number(event.target.value));
                        }}>
                            {turns.map((turn, i) => <option key={i} value={i}>{turn.original}</option>)}
                        </select>
                    </label>}
                    <PassageReview key={JSON.stringify(selected)} wordingOnly audioId={audioId} target={selected}
                        speakers={[]} mappings={{}} onSkip={() => setEditingTime(null)} onClose={() => setEditingTime(null)}
                        onReplay={() => onListen(Math.max(0, selected.start - 1))}
                        onSaved={(_, warning) => { setEditingTime(null); setMessage(warning || 'Wording correction saved.'); }} />
                </>}
                {message && <p role="status">{message}</p>}
            </div>
        </details> : report.checked > 0 && <p>Wording check · Both passes agreed on the words in {report.checked} {report.checked === 1 ? 'excerpt' : 'excerpts'}.</p>}
    </div>;
}
