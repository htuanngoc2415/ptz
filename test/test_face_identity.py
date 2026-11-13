import unittest
import numpy as np
from ai.face.face_matcher import FaceMatcher
from ai.face.identity_confirmation import IdentityConfirmation


class IdentityTests(unittest.TestCase):
    def test_confirmation_unknown_switch_and_expiry(self):
        context = IdentityConfirmation(2, 3.0)
        def update(name, matched, now):
            return context.update(dict(identity=name, matched=matched, score=.8), now)
        self.assertFalse(update('A', True, 0)['confirmed'])
        self.assertTrue(update('A', True, .3)['confirmed'])
        self.assertFalse(update('B', True, .6)['confirmed'])
        self.assertEqual(update('unknown', False, .9)['confirm_count'], 0)
        self.assertFalse(update('B', True, 1.2)['confirmed'])
        self.assertFalse(update('B', True, 4.3)['confirmed'])
        context.reset()
        self.assertFalse(update('B', True, 4.6)['confirmed'])

    def test_second_score_is_different_identity_not_template(self):
        query = np.zeros(512, np.float32); query[0] = 1
        second = np.zeros(512, np.float32); second[:2] = [.4, np.sqrt(.84)]
        matcher = FaceMatcher({'A': np.stack([query, query]), 'B': second}, .45)
        result = matcher.match(query)
        self.assertTrue(result['matched'])
        self.assertAlmostEqual(result['second_score'], .4)
        self.assertAlmostEqual(result['margin'], .6)
        unknown = FaceMatcher({'B': second}, .45).match(query)
        self.assertFalse(unknown['matched'])
        self.assertEqual(unknown['identity'], 'unknown')
        self.assertEqual(unknown['nearest_identity'], 'B')
        self.assertIsNone(unknown['second_score'])


if __name__ == '__main__':
    unittest.main()
