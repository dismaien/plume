"""Conservative word proposals, never transcript edits or accepted-name writes.

Frozen policy: keep explicit unknowns; question an existing machine label only
when the primary regular timeline is ambiguous and the independent activity
model has a strong winner. Activity is not calibrated identity confidence.
"""
import hashlib
import math
from pathlib import Path

POLICY = dict(activity_probability=.9,activity_margin=.25,mapping_jaccard=.25,
              other_regular_support=.25,primary_regular_support=.5,
              preserve_unknown=True,mapping_activity_threshold=.4,version='primary_speakers_review_unmatched_v2')


def annotation(payload):
    from pyannote.core import Annotation,Segment
    result=Annotation(uri='plume')
    for index,turn in enumerate(payload.get('regular_segments',payload.get('segments',[]))):
        speaker=turn.get('speaker')
        if speaker and str(speaker).strip().lower() not in ('unknown','speaker unclear'):
            result[Segment(float(turn['start']),float(turn['end'])),index]=str(speaker)
    return result


def propose(primary,native,words):
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
                and max([v for person,v in support.items() if person!=current],default=0)<POLICY['other_regular_support']):continue
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
    return dict(policy=POLICY,proposals=proposals,mapping=mapping,
                confidence_kind='uncalibrated frame activity; not person identity probability')
