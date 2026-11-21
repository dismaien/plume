import numpy as np

from compare_assigned_voices import compare_clip, guard_boundaries


def changes(*pairs):
    return {i: dict(index=i, proposed=person) for i, person in pairs}


def test_new_split_needs_support_for_the_word_on_the_other_side():
    words = [dict(word=w, speaker='a') for w in ('pretty', 'huge', 'margin')]
    assert guard_boundaries(words, changes((0, 'b'), (1, 'b')), lambda i, s: False) == []
    assert len(guard_boundaries(words, changes((0, 'b'), (1, 'b')),
                               lambda i, s: (i, s) == (2, 'a'))) == 2


def test_real_reply_survives_when_neighbors_are_also_candidates():
    words = [dict(word=w, speaker=s) for w, s in [('well', 'a'), ('yeah', 'b'), ('then', 'c')]]
    proposed = changes((1, 'a'), (2, 'a'))
    assert guard_boundaries(words, proposed, lambda i, s: True) == [proposed[2]]


def test_only_changes_which_survive_neighbor_rejection_are_returned():
    words = [dict(word='word', speaker='a') for _ in range(3)]
    # The first word's proposed boundary loses its corroboration after the
    # second proposal is withheld. A second pass must reconsider that boundary.
    assert guard_boundaries(words, changes((0, 'b'), (1, 'c')),
                            lambda i, s: (i, s) == (1, 'c')) == []


def test_two_disjoint_voice_panels_must_agree():
    pools = {'a': [(0, 1), (3, 4)], 'b': [(5, 6), (7, 8)]}
    vectors = {(0, 1): [0., 1.], (3, 4): [0., 1.], (5, 6): [1., 0.],
               (7, 8): [1., 0.], (10, 11): [1., 0.]}
    def encode(a, b):
        return np.asarray(vectors[a, b])
    assert compare_clip(10, 11, pools, 'a', 'b', encode) == 'b'
    vectors[3, 4] = [1., 0.]
    assert compare_clip(10, 11, pools, 'a', 'b', encode) is None
