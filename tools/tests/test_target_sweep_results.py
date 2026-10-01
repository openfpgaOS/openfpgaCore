#!/usr/bin/env python3
"""Timing sign-off must reject incomplete evidence and preserve negative slack."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sweep_target_matrix import parse_result, seed_candidates


class SeedSelection(unittest.TestCase):
    def test_full_range_includes_saved_seeds_once(self):
        rows = [{'variant': 'os30', 'seed': 2}, {'variant': 'os20', 'seed': 33}]
        tasks = seed_candidates(rows, [0, 1, 2], list(range(1, 31)))
        self.assertEqual(len(tasks), 61)
        self.assertEqual({seed for row, seed in tasks if row['variant'] == 'os30'}, set(range(1, 31)))
        self.assertEqual({seed for row, seed in tasks if row['variant'] == 'os20'}, set(range(1, 31)) | {33})

    def test_legacy_relative_seeds_are_preserved(self):
        rows = [{'variant': 'mister', 'seed': 30}]
        self.assertEqual([seed for _, seed in seed_candidates(rows, [0, 1, 2])], [30, 31, 32])

    def test_invalid_or_duplicate_seeds_are_rejected(self):
        rows = [{'variant': 'os30', 'seed': 2}]
        for offsets, seeds in [([0, 0], None), ([-2], None), ([0], [0, 1]), ([0], [1, 1])]:
            with self.subTest(offsets=offsets, seeds=seeds), self.assertRaises(ValueError):
                seed_candidates(rows, offsets, seeds)


class TimingResults(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / 'timing').mkdir()
        output = self.directory / 'output_files'
        output.mkdir()
        (output / 'core.fit.summary').write_text('Logic utilization (in ALMs) : 15,717 / 18,480\n')
        # Quartus reports can contain Latin-1 degree signs.
        (output / 'core.fit.rpt').write_bytes(b'Temperature 85\xb0C\n; M10K blocks ; 297 / 308 ;\n')
        for extension in ('sof', 'rbf'):
            (output / ('core.' + extension)).write_bytes(b'fixture')
        self.rows = [[model, temperature, check, '0.015']
                     for model, temperature in [('slow', '0'), ('slow', '85'),
                                                ('fast', '0'), ('fast', '85')]
                     for check in ['setup', 'hold', 'recovery', 'removal', 'pulse_width']]

    def result(self):
        with (self.directory / 'timing/summary.tsv').open('w') as stream:
            writer = csv.writer(stream, delimiter='\t')
            writer.writerow(['model', 'temperature', 'check', 'slack'])
            writer.writerows(self.rows)
        return parse_result(self.directory, 'core')

    def test_complete_positive_report_with_non_utf8_resources(self):
        result = self.result()
        self.assertTrue(result['all_checks_pass'])
        self.assertEqual((result['alms'], result['m10ks']), (15717, 297))

    def test_negative_hold_fails_even_with_positive_setup(self):
        self.rows[-4][-1] = '-0.001'
        result = self.result()
        self.assertFalse(result['all_checks_pass'])
        self.assertEqual(result['worst']['hold'], -0.001)

    def test_duplicate_does_not_replace_missing_corner_check(self):
        self.rows[-1] = self.rows[0]
        with self.assertRaises(ValueError):
            self.result()

    def test_missing_required_paths_at_one_corner(self):
        self.rows[0][-1] = 'no_paths'
        with self.assertRaises(ValueError):
            self.result()

    def test_optional_asynchronous_checks_can_have_no_paths(self):
        for row in self.rows:
            if row[2] in ('recovery', 'removal'):
                row[-1] = 'no_paths'
        self.assertTrue(self.result()['all_checks_pass'])

    def test_non_finite_slack_is_not_sign_off(self):
        for value in ('nan', 'inf', '-inf'):
            with self.subTest(value=value):
                self.rows[0][-1] = value
                with self.assertRaises(ValueError):
                    self.result()


if __name__ == '__main__':
    unittest.main()
