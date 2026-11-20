"""Replay native frame logits without loading a model or inventing identities."""
import hashlib
import math
from pathlib import Path


def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def validate(logits, mask, frame_seconds, duration):
    import numpy as np
    logits,mask=np.asarray(logits),np.asarray(mask)
    if logits.ndim!=2 or not logits.shape[1] or mask.shape!=(logits.shape[0],):
        raise ValueError('Frame logits/mask shape mismatch')
    if not np.isfinite(logits).all() or not np.isfinite(mask).all() or not np.isin(mask,[0,1]).all():
        raise ValueError('Invalid frame scores or validity mask')
    if not math.isfinite(frame_seconds+duration) or frame_seconds<=0 or duration<=0:
        raise ValueError('Invalid frame clock')
    return logits,mask.astype(bool)


def save_activity(path, logits, mask, frame_seconds, duration):
    import numpy as np
    logits,mask=validate(logits,mask,frame_seconds,duration)
    path=Path(path)
    with path.open('xb') as output:
        np.savez_compressed(output,logits=logits,attention_mask=mask,
                            frame_seconds=frame_seconds,audio_duration=duration)
    path.chmod(0o600)
    return dict(path=str(path.resolve()),sha256=file_hash(path),
                format='native_frame_logits_npz_v1',frame_seconds=frame_seconds,
                note='frame activity, not calibrated person-identity confidence')


def extract_segments(logits, mask, frame_seconds, duration, threshold):
    import numpy as np
    logits,mask=validate(logits,mask,frame_seconds,duration)
    if not math.isfinite(threshold) or not 0<threshold<1:
        raise ValueError('Activity threshold must be between zero and one')
    cutoff=math.log(threshold/(1-threshold))
    active=(logits>cutoff)&mask[:,None]
    # Match the official processor: strict threshold, one continuous turn per
    # active run, no minimum-duration pruning and no merging simultaneous people.
    changes=np.diff(np.pad(active.astype(np.int8),((1,1),(0,0))),axis=0)
    turns=[]
    for speaker in range(logits.shape[1]):
        starts=np.flatnonzero(changes[:,speaker]==1)
        ends=np.flatnonzero(changes[:,speaker]==-1)
        for start,end in zip(starts,ends):
            begin=round(float(start*frame_seconds),8);finish=min(round(float(end*frame_seconds),8),duration)
            if finish>begin:turns.append(dict(start=begin,end=finish,speaker=f'speaker_{speaker}'))
    return sorted(turns,key=lambda t:(t['start'],t['end'],t['speaker']))


def replay(artifact, threshold):
    import numpy as np
    path=Path(artifact['path'])
    if file_hash(path)!=artifact['sha256']:raise ValueError('Frame artifact changed')
    with np.load(path,allow_pickle=False) as data:
        return extract_segments(data['logits'],data['attention_mask'],float(data['frame_seconds']),
                                float(data['audio_duration']),threshold)
