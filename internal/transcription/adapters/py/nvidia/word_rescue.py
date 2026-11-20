"""Conservative word proposals, never transcript edits or accepted-name writes.

Word proposals use independent activity to resolve missing labels and
ambiguous primary turns. Mixed-track recovery returns acoustic spans for
fresh assembly on the original clock.
"""
import hashlib
import json
import math
from pathlib import Path

POLICY = dict(activity_probability=.9,activity_margin=.25,mapping_jaccard=.25,
              other_regular_support=.25,primary_regular_support=.5,
              preserve_explicit_unknown=True,mapping_activity_threshold=.4,version='anchored_native_word_gaps_v4')


def annotation(payload):
    from pyannote.core import Annotation,Segment
    result=Annotation(uri='plume')
    for index,turn in enumerate(payload.get('regular_segments',payload.get('segments',[]))):
        speaker=turn.get('speaker')
        if speaker and str(speaker).strip().lower() not in ('unknown','speaker unclear'):
            result[Segment(float(turn['start']),float(turn['end'])),index]=str(speaker)
    return result


def propose(primary,native,words,metadata=None):
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from scipy.special import expit
    from pyannote.core import Segment
    community,nemo=annotation(primary),annotation(native)
    people,channels=community.labels(),nemo.labels()
    primary_support={person:community.label_timeline(person).support() for person in people}
    native_support={channel:nemo.label_timeline(channel).support() for channel in channels}
    weights=np.zeros((len(people),len(channels)))
    for i,person in enumerate(people):
        first=primary_support[person]
        for j,channel in enumerate(channels):
            second=native_support[channel]
            intersection=first.crop(second).support().duration()
            union=first.duration()+second.duration()-intersection
            weights[i,j]=intersection/union if union else 0
    rows,cols=linear_sum_assignment(weights,maximize=True)
    mapping={channels[j]:people[i] for i,j in zip(rows,cols) if weights[i,j]>=POLICY['mapping_jaccard']}
    # A voice heard only during overlap may be too brief for global Jaccard.
    # Keep it eligible when the same native channel covers its entire activity
    # and supports it locally on the word. Never allocate another identity.
    exclusive_people = {turn['speaker'] for turn in primary.get('segments', [])}
    overlap_only = set(people) - exclusive_people - set(mapping.values()) if exclusive_people else set()
    brief_matches = {
        channel: [person for person in overlap_only
                  if primary_support[person].duration() > 0 and
                  primary_support[person].crop(native_support[channel]).support().duration()
                  >= .9 * primary_support[person].duration()]
        for channel in set(channels) - set(mapping)
    }
    artifact=native['activity_artifact'];path=Path(artifact['path'])
    with path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=artifact['sha256']:
            raise ValueError('Native frame artifact changed')
    with np.load(path,allow_pickle=False) as data:
        from activity_timeline import validate
        logits,mask=validate(data['logits'],data['attention_mask'],float(data['frame_seconds']),float(data['audio_duration']))
        probabilities=expit(logits);step=float(data['frame_seconds'])
    proposals=[]
    for index,word in enumerate(words):
        current=word.get('speaker')
        if not current or str(current).strip().lower() in ('unknown','speaker unclear'):continue
        start,end=float(word['start']),float(word['end']);duration=end-start
        if not math.isfinite(start+end) or duration<0:continue
        left=max(0,int(math.floor(start/step)))
        right=min(len(mask),max(left+1,int(math.ceil(end/step))))
        if left>=right:continue
        frames=np.arange(left,right)
        frame_weights=(np.maximum(0,np.minimum((frames+1)*step,end)-np.maximum(frames*step,start))
                       if duration else np.ones(len(frames)))
        frame_weights=frame_weights*mask[left:right]
        if not frame_weights.sum():continue
        p=np.average(probabilities[left:right],axis=0,weights=frame_weights)
        order=np.argsort(p);best=int(order[-1]);activity=float(p[best])
        margin=float(p[best]-p[order[-2]]) if len(order)>1 else activity
        if activity<POLICY['activity_probability'] or margin<POLICY['activity_margin']:continue
        channel=f'speaker_{best}'
        if mapping.get(channel)==current:continue
        support=({person:primary_support[person].crop(Segment(start,end)).support().duration()/duration for person in people}
                 if duration else {person:float(any(t.start<=start<t.end for t in primary_support[person])) for person in people})
        if (support.get(current,0)>=POLICY['primary_regular_support']
                and max([v for person,v in support.items() if person!=current],default=0)<POLICY['other_regular_support']):
            continue
        proposed = mapping.get(channel)
        if proposed is None:
            supported = [person for person in brief_matches.get(channel, [])
                         if support.get(person, 0) >= POLICY['primary_regular_support']]
            proposed = supported[0] if len(supported) == 1 else None
        if proposed == current:
            continue
        proposal = dict(index=index, start=start, end=end, word=word['word'],
                        previous=current, proposed=proposed or '', activity=activity, margin=margin)
        if proposed is None:
            proposal['review_channel'] = channel
        proposals.append(proposal)
    from gap_evidence import gap_proposals, local_voice_evidence, unmapped_spans
    evidence = local_voice_evidence(primary, probabilities, mask, step)
    already_proposed = {p['index'] for p in proposals}
    proposals.extend(p for p in gap_proposals(evidence, probabilities, step, words, metadata or {})
                     if p['index'] not in already_proposed)
    local_spans = {}
    if primary.get('mixed_segments') or primary.get('mixed_regular_segments'):
        local_spans = unmapped_spans(evidence, probabilities, mapping, step)
    return dict(policy=POLICY,proposals=proposals,mapping=mapping,
                mixed_activity=recover_mixed_activity(primary, native, mapping, local_spans),
                confidence_kind='uncalibrated frame activity; not person identity probability')


def recover_mixed_activity(primary, native, mapping, local_spans=None):
    """Project independently recognized turns onto disputed primary regions."""
    from bisect import bisect_left
    from collections import Counter, defaultdict

    mixed = json.loads((primary.get('metadata') or {}).get('speaker_mixed_tracks', '{}'))
    if not mixed:
        return {}
    channels = sorted(native.get('segments', []), key=lambda r: r['start'])
    starts = [r['start'] for r in channels]
    # Native tracks can overlap. Prefix maxima keep the seek correct even
    # when one long-running channel contains many shorter turns.
    ends = []
    for r in channels:
        ends.append(max(r['end'], ends[-1] if ends else 0))
    result = {}
    for field in ('mixed_segments', 'mixed_regular_segments'):
        recovered = []
        for index, region in enumerate(primary.get(field, [])):
            start, end = region['start'], region['end']
            targets = mixed.get(region['speaker'], [])
            spans = [(max(start, r['start']), min(end, r['end']), r['speaker'])
                     for r in channels[bisect_left(ends, start):bisect_left(starts, end)]
                     if r['end'] > start]
            if not spans:
                continue
            by_channel = defaultdict(float)
            for a, b, channel in spans:
                by_channel[channel] += b - a
            if len(by_channel) == 1:
                channel, seconds = next(iter(by_channel.items()))
                gaps = [spans[0][0] - start, end - spans[-1][1]]
                gaps.extend(b[0] - a[1] for a, b in zip(spans, spans[1:]))
                if seconds >= .35 * (end - start) and max(gaps) <= .35:
                    spans = [(start, end, channel)]
            events = defaultdict(Counter)
            for a, b, channel in spans:
                events[a][channel] += 1
                events[b][channel] -= 1
            active = Counter()
            previous = start
            for boundary, changes in sorted(events.items()):
                channels_here = [c for c, count in active.items() if count > 0]
                if boundary > previous and (field == 'mixed_regular_segments' or len(channels_here) == 1):
                    for channel in channels_here:
                        person = mapping.get(channel)
                        if person in targets:
                            recovered.append(dict(index=index, start=previous, end=boundary, speaker=person))
                        elif local_spans and channel in local_spans:
                            ends_local, local_rows = local_spans[channel]
                            j = bisect_left(ends_local, previous)
                            while j < len(local_rows) and local_rows[j][0] < boundary:
                                a, b, person = local_rows[j]
                                a, b = max(a, previous), min(b, boundary)
                                if b > a and person in targets:
                                    recovered.append(dict(index=index, start=a, end=b, speaker=person))
                                j += 1
                active.update(changes)
                previous = boundary
        result[field] = recovered
    return result
