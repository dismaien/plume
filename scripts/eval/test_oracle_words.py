import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('oracle_words', Path(__file__).with_name('score_oracle_words.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def words(labels):
    return [dict(start=i, end=i+1, word='same', **({'speaker': label} if label else {})) for i, label in enumerate(labels)]


def test_wrong_labels_and_uncertainty_are_not_conflated():
    result = module.fixed_word_attribution(words(['a','a','b','b']), words(['x','x','x',None]))
    assert result['correctly_attributed'] == 2
    assert result['wrong_person'] == 1 and result['unattributed'] == 1


def test_global_name_permutation_is_free_but_word_edits_are_rejected():
    reference = words(['a','b'])
    assert module.fixed_word_attribution(reference, words(['z','y']))['correctly_attributed'] == 2
    changed = words(['z','y']); changed[0]['word'] = 'changed'
    with pytest.raises(ValueError, match='changed the fixed words'):
        module.fixed_word_attribution(reference, changed)


def test_all_unknown_is_no_correct_attribution():
    result = module.fixed_word_attribution(words(['a','b']), words([None,None]))
    assert result['correctly_attributed'] == 0 and result['unattributed'] == 2
    assert result['known_label_precision'] is None
