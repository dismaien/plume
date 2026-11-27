import unittest
from check import differences, source_words, windows


class WordingTest(unittest.TestCase):
    def test_only_lexical_changes_are_reported(self):
        words = [("Hallo,", 0, 0.9), ("yeah", 1, 1.2), ("Welt!", 1.3, 2)]
        self.assertEqual(list(differences(words, "hallo Yeah. Welt", 0, 2)), [])
        (diff,) = differences(words, "Hallo Welt", 0, 2)
        self.assertTrue(1 < diff["target"] < 1.2)
        self.assertIn("yeah", diff["original"])

    def test_unaligned_words_and_overlapping_turns_survive_window_cuts(self):
        doc = {
            "segments": [
                {"start": 0, "end": 24, "text": "Ja wenn alles klappt"},
                {"start": 23.9, "end": 24.1, "text": "yeah"},
                {"start": 28, "end": 52, "text": "Okay"},
            ],
            "word_segments": [{"start": 19, "end": 21, "word": "klappt"}],
        }
        words = source_words(doc, 56)
        clips = list(windows(words, 56))
        self.assertTrue(all(0 < b - a <= 30 for a, b, _ in clips))
        self.assertEqual(
            [w[0] for _, _, group in clips for w in group],
            ["Ja", "wenn", "alles", "klappt", "yeah", "Okay"],
        )
        self.assertEqual(clips[0][0], 0)
        self.assertEqual(clips[0][1], clips[1][0])
        self.assertEqual(clips[-1][1], 56)


if __name__ == "__main__":
    unittest.main()
