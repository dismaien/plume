#!/usr/bin/env python3
"""Measure speaker attribution with fixed MANUAL words/times, not ASR quality.

Published AMI words are stripped of their speaker labels before entering the
production assembler. Compare assembler versions without rerunning inference.
The resulting WER=0 is by construction and must never be called ASR accuracy.
"""
import argparse
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from score_reference import digest, score

MANUAL_SHA256 = 'b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d'
MANUAL_URL = 'https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip'


def fixed_word_attribution(reference, hypothesis):
    """Separate wrong person labels from explicit uncertainty at fixed words."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    if len(reference) != len(hypothesis) or any(
        any(r[k] != h[k] for k in ('word', 'start', 'end')) for r, h in zip(reference, hypothesis)
    ):
        raise ValueError('Oracle assembler changed the fixed words or their timing')
    people = sorted({w['speaker'] for w in reference})
    labels = sorted({w['speaker'] for w in hypothesis if w.get('speaker')})
    matrix = np.zeros((len(people), len(labels)), dtype=int)
    for expected, actual in zip(reference, hypothesis):
        if actual.get('speaker'):
            matrix[people.index(expected['speaker']), labels.index(actual['speaker'])] += 1
    rows, columns = linear_sum_assignment(matrix, maximize=True)
    mapping = {labels[c]: people[r] for r, c in zip(rows, columns)}
    correct = sum(bool(h.get('speaker')) and mapping.get(h['speaker']) == r['speaker'] for r, h in zip(reference, hypothesis))
    unknown = sum(not h.get('speaker') for h in hypothesis)
    return dict(reference_words=len(reference), correctly_attributed=correct,
                wrong_person=len(reference)-correct-unknown, unattributed=unknown,
                known_label_precision=correct/(len(reference)-unknown) if len(reference)>unknown else None,
                policy='one optimal global speaker permutation; fixed manual words/times; not ASR accuracy')


def manual_words(archive, meeting='IS1009a'):
    if digest(archive) != MANUAL_SHA256:
        raise ValueError('AMI manual annotation archive hash mismatch')
    words = []
    with zipfile.ZipFile(archive) as corpus:
        meetings = ET.fromstring(corpus.read('corpusResources/meetings.xml'))
        speakers = next(m for m in meetings if m.get('observation') == meeting)
        mapping = {speaker.get('nxt_agent'): speaker.get('global_name') for speaker in speakers}
        for agent, identity in mapping.items():
            annotation = ET.fromstring(corpus.read(f'words/{meeting}.{agent}.words.xml'))
            for word in annotation:
                if word.tag != 'w' or word.get('punc') == 'true' or not word.text:
                    continue
                start, end = word.get('starttime'), word.get('endtime')
                if start is None or end is None:
                    continue
                words.append(dict(start=float(start), end=float(end), word=word.text, speaker=identity))
    return sorted(words, key=lambda w: (w['start'], w['end'], w['speaker']))


def run(args):
    words = manual_words(args.annotations)
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for reference in sorted(args.benchmark.glob('*/reference.json')):
        manifest = json.loads(reference.read_text())
        begin, end = manifest['excerpt_start'], manifest['excerpt_end']
        directory = args.out / manifest['recording_id']; directory.mkdir()
        selected = [{**word, 'start': max(begin, word['start'])-begin, 'end': min(end, word['end'])-begin}
                    for word in words if begin <= (word['start']+word['end'])/2 < end]
        (directory / 'words.json').write_text(json.dumps(selected, indent=2))
        manifest.update(audio=str((reference.parent / manifest['audio']).resolve()),
                        rttm=str((reference.parent / manifest['rttm']).resolve()),
                        uem=str((reference.parent / manifest['uem']).resolve()), words='words.json',
                        word_reference_url=MANUAL_URL, word_reference_archive_sha256=MANUAL_SHA256,
                        input_kind='oracle_manual_words_and_times; not an ASR benchmark')
        (directory / 'reference.json').write_text(json.dumps(manifest, indent=2))
        # The assembler never receives the reference speaker labels.
        text = ' '.join(word['word'] for word in selected)
        frozen = dict(text=text, word_segments=[{k: v for k, v in word.items() if k != 'speaker'} for word in selected],
                      segments=[dict(start=0, end=end-begin, text=text)],
                      metadata={'evaluation_input': 'oracle_manual_words_and_times_not_ASR_output'})
        (directory / 'frozen.json').write_text(json.dumps(frozen, indent=2))
        for candidate in ('community1-auto', 'sortformer-heads', 'sortformer-recluster'):
            diarization = reference.parent / f'{candidate}.json'
            if not diarization.exists():
                continue
            for label, assembler in [('previous', args.previous_assembler), ('overlap-aware', args.assembler)]:
                output = directory / f'{candidate}-{label}.json'
                command = [str(assembler.resolve()), '-transcript', str(directory / 'frozen.json'),
                           '-diarization', str(diarization), '-out', str(output)]
                if label == 'overlap-aware':
                    command.append('-overlap-review')
                process = subprocess.run(command,
                                         capture_output=True, text=True)
                if process.returncode:
                    raise RuntimeError(f'Production assembler failed: {process.stderr}')
                measured = score(directory / 'reference.json', output)
                results.append(dict(excerpt=manifest['recording_id'], engine=candidate, assignment=label,
                                    words=measured['words'], candidate_sha256=digest(diarization),
                                    fixed_word_attribution=fixed_word_attribution(selected, json.loads(output.read_text())['word_segments']),
                                    assembler_sha256=digest(assembler), source_sha256=manifest['source_sha256']))
    summary = dict(input_kind='oracle_manual_words_and_times; NOT measured ASR accuracy',
                   annotation_url=MANUAL_URL, annotation_sha256=MANUAL_SHA256, results=results,
                   limitation='Fixed tuning excerpts from one public meeting; two-speaker excerpt overlaps opening excerpt')
    (args.out / 'results.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
    for result in results:
        print(result['excerpt'], result['engine'], result['assignment'],
              'cpWER', round(100*result['words']['cpwer']['error_rate'], 2),
              'attribution', result['fixed_word_attribution'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', type=Path, required=True)
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--previous-assembler', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args())
