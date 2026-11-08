#!/usr/bin/env python3
"""Score a candidate against human RTTM/UEM and timed word references.

No live database writes. Unknown turns count as missed attribution. This tool
requires an explicitly reviewed reference manifest; model agreement is not truth.
"""
import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import math
import unicodedata
import wave
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_rttm(path, uri):
    from pyannote.core import Annotation, Segment
    annotation = Annotation(uri=uri)
    for i, line in enumerate(Path(path).read_text().splitlines()):
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.split()
        if fields[0] != 'SPEAKER' or fields[1] != uri:
            raise ValueError(f'Unexpected RTTM recording at line {i+1}')
        start, duration = float(fields[3]), float(fields[4])
        if not math.isfinite(start+duration) or start < 0 or duration <= 0:
            raise ValueError('Invalid reference interval')
        annotation[Segment(start, start+duration), i] = fields[7]
    return annotation


def candidate_annotation(payload, uri):
    from pyannote.core import Annotation, Segment
    annotation = Annotation(uri=uri)
    # Score overlapping regular tracks; exclusive tracks are for word assignment.
    turns = payload.get('regular_segments', payload.get('segments', []))
    for i, turn in enumerate(turns):
        start, end = float(turn['start']), float(turn['end'])
        if not math.isfinite(start+end) or start < 0 or end <= start:
            raise ValueError('Invalid candidate interval')
        speaker = turn.get('speaker')
        if speaker and str(speaker).lower() not in ('unknown', 'speaker unclear'):
            annotation[Segment(start, end), i] = str(speaker)
    return annotation


def diarization_scores(reference, hypothesis, uem):
    from pyannote.core import Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate, JaccardErrorRate
    results = {}
    for collar in (0.0, 0.25):
        metric = DiarizationErrorRate(collar=collar, skip_overlap=False)
        detail = metric(reference, hypothesis, uem=uem, detailed=True)
        results[str(collar)] = {k: float(v) for k,v in detail.items()}
    results['jer'] = float(JaccardErrorRate(collar=0, skip_overlap=False)(reference,hypothesis,uem=uem))
    ref = reference.crop(uem); hyp = hypothesis.crop(uem)
    mapping = DiarizationErrorRate(collar=0,skip_overlap=False).optimal_mapping(ref,hyp)
    recall = {}
    for label in ref.labels():
        expected = ref.label_timeline(label).support()
        actual_labels = [h for h,r in mapping.items() if r == label]
        matched = Timeline()
        for h in actual_labels:
            for a in expected:
                for b in hyp.label_timeline(h).support():
                    if a.intersects(b): matched.add(a & b)
        denominator = expected.duration()
        recall[str(label)] = matched.support().duration()/denominator if denominator else None
    # Aggregate DER can hide the loss of a brief second speaker. Preserve the
    # actual missed reference turns for inspection, including overlapping turns.
    missed_turns = []
    brief_seconds, brief_matched = 0.0, 0.0
    for turn, _, label in ref.itertracks(yield_label=True):
        matched = Timeline()
        for h, r in mapping.items():
            if r == label:
                for actual in hyp.label_timeline(h).support():
                    if actual.intersects(turn):
                        matched.add(actual & turn)
        correct = matched.support().duration()
        duration = turn.duration
        if duration < 1:
            brief_seconds += duration
            brief_matched += correct
        if correct < duration - 1e-6:
            overlap = any(other != label and turn.intersects(segment)
                          for segment, _, other in ref.itertracks(yield_label=True))
            missed_turns.append(dict(start=turn.start, end=turn.end, speaker=str(label),
                                     matched_fraction=correct/duration,
                                     missed_seconds=duration-correct,
                                     overlapping_reference_speech=overlap))
    total = sum(ref.label_duration(label) for label in ref.labels())
    attributed = sum(ref.label_timeline(label).crop(hyp.get_timeline().support()).support().duration() for label in ref.labels())
    results.update(per_speaker_recall=recall, reference_speakers=len(ref.labels()),
                   hypothesis_speakers=len(hyp.labels()), false_solo=len(ref.labels())>1 and len(hyp.labels())==1,
                   unattributed_reference_fraction=1-attributed/total if total else None,
                   brief_turn_reference_seconds=brief_seconds,
                   brief_turn_recall=brief_matched/brief_seconds if brief_seconds else None,
                   missed_reference_turns=sorted(missed_turns, key=lambda t: -t['missed_seconds'])[:20])
    return results


def word_scores(reference, hypothesis, uri, uem):
    import meeteval.wer
    from meeteval.io import SegLST
    def in_uem(word):
        midpoint=(float(word['start'])+float(word['end']))/2
        return any(s.start <= midpoint < s.end for s in uem)
    ref=[w for w in reference if in_uem(w)]
    hyp=[w for w in hypothesis if in_uem(w)]
    def seglst(words):
        return SegLST([dict(session_id=uri,speaker=str(w['speaker']),start_time=float(w['start']),
                            end_time=float(w['end']),words=w['word']) for w in words])
    # Unknown words retain their text in WER, but cannot earn speaker-attribution credit.
    known=[w for w in hyp if w.get('speaker') and w['speaker'].lower() not in ('unknown','speaker unclear')]
    def normalized(words):
        output=[]
        for word in words:
            value=unicodedata.normalize('NFKC',word['word']).casefold()
            value=''.join(c for c in value if not unicodedata.category(c).startswith('P')).strip()
            if value:output.append({**word,'word':value})
        return output
    normalized_ref,normalized_hyp,normalized_known=normalized(ref),normalized(hyp),normalized(known)
    cp=meeteval.wer.cp_word_error_rate(seglst(normalized_ref),seglst(normalized_known))
    tcp=meeteval.wer.time_constrained_minimum_permutation_word_error_rate(seglst(normalized_ref),seglst(normalized_known),collar=5)
    wer=meeteval.wer.siso_word_error_rate(' '.join(w['word'] for w in normalized_ref),' '.join(w['word'] for w in normalized_hyp))
    verbatim=meeteval.wer.siso_word_error_rate(' '.join(w['word'] for w in ref),' '.join(w['word'] for w in hyp))
    return {'wer':dataclasses.asdict(wer), 'verbatim_wer':dataclasses.asdict(verbatim), 'cpwer':dataclasses.asdict(cp), 'tcpwer':dataclasses.asdict(tcp),
            'attribution_policy':'unattributed_words_count_as_deletions', 'tcpwer_collar_seconds':5,
            'reference_words':len(ref),'hypothesis_words':len(hyp),'unattributed_words':len(hyp)-len(known),
            'normalization':'unicode_nfkc_casefold_strip_punctuation_v1; same policy for every model'}


def full_text_wer(reference_words, text):
    """Include decoded words that an aligner failed to give timestamps."""
    import meeteval.wer
    def normalize(value):
        return ' '.join(''.join(c for c in unicodedata.normalize('NFKC', token).casefold()
                                if not unicodedata.category(c).startswith('P')) for token in value.split()).strip()
    reference = normalize(' '.join(word['word'] for word in reference_words))
    return dataclasses.asdict(meeteval.wer.siso_word_error_rate(reference, normalize(text)))


def score(manifest_path, candidate_path):
    from pyannote.core import Segment, Timeline
    manifest_path, candidate_path=Path(manifest_path),Path(candidate_path)
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('reviewed_by_human') is not True or not manifest.get('reviewer'):
        raise ValueError('Human review and reviewer provenance are required; model labels are not a reference')
    if manifest.get('split') not in ('tuning','held_out','synthetic_test'):
        raise ValueError('Reference split must be tuning, held_out or synthetic_test')
    root=manifest_path.parent
    audio=root/manifest['audio']
    if digest(audio)!=manifest['source_sha256']:
        raise ValueError('Reference source audio hash mismatch')
    uri=manifest['recording_id']
    reference=read_rttm(root/manifest['rttm'],uri)
    uem=Timeline(uri=uri)
    for line in (root/manifest['uem']).read_text().splitlines():
        if not line.strip() or line.startswith('#'): continue
        recording,channel,start,end=line.split()
        if recording!=uri: raise ValueError('UEM recording mismatch')
        start,end=float(start),float(end)
        if not math.isfinite(start+end) or start<0 or end<=start: raise ValueError('Invalid UEM interval')
        uem.add(Segment(start,end))
    if not uem: raise ValueError('Empty evaluation region')
    payload=json.loads(candidate_path.read_text())
    diar=payload.get('diarization_result') or payload
    result={'recording_id':uri,'split':manifest['split'],'reference_reviewer':manifest['reviewer'],
            'source_sha256':manifest['source_sha256'],'candidate_sha256':digest(candidate_path),
            'candidate_metadata':diar.get('metadata',{}),'collar_unit':'seconds, pyannote.metrics convention',
            'overlap_included':True,'diarization':diarization_scores(reference,candidate_annotation(diar,uri),uem),
            'scorer_versions':{p:importlib.metadata.version(p) for p in ('pyannote.metrics','meeteval')}}
    if manifest.get('words'):
        words=json.loads((root/manifest['words']).read_text())
        result['words']=word_scores(words,payload.get('word_segments',[]),uri,uem)
        result['words']['full_text_wer_status']='unavailable_for_non_wav_or_partial_uem'
        # Full text cannot be filtered to a partial UEM without guessing its
        # missing word times. Score it only when the reference covers the WAV.
        try:
            with wave.open(str(audio)) as wav:
                duration=wav.getnframes()/wav.getframerate()
            support=uem.support()
            if support.crop(Segment(0,duration)).duration() >= duration-1e-6 and isinstance(payload.get('text'),str):
                result['words']['full_text_wer']=full_text_wer(words,payload['text'])
                result['words']['full_text_wer_status']='scored_all_decoded_text_including_unaligned_words'
                result['words']['primary_text_metric']='full_text_wer'
        except (wave.Error, EOFError):
            pass
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',required=True,type=Path)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    result=score(args.reference,args.candidate)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as handle:
        json.dump(result,handle,indent=2,default=str,allow_nan=False)


if __name__=='__main__': main()
