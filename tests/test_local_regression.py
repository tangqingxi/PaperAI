"""可选的本地只读样例回归；公开仓库不携带用户论文。"""
import os
from pathlib import Path
import re
import unittest
from paperai.io import digest
from paperai.scan import PAGE, TAG, scan, pages


@unittest.skipUnless(os.environ.get('PAPERAI_MENG_ROOT'), '未提供本地Meng回归样例，不能据此宣称实测')
class MengRegressionTests(unittest.TestCase):
    def test_existing_answer_and_historical_failures(self):
        folder = Path(os.environ['PAPERAI_MENG_ROOT'])
        files = [folder / n for n in ['paper_verified.md', 'paper_raw.md', 'QA_REPORT.md', 'paper_raw.manifest.json']]
        before = {p: digest(p) for p in files}
        self.assertEqual(before[files[0]], 'bb181d6b223e88494108d8b17f5900bd967a3d1f37a08de5ab79dd3d36e76cc6')
        final, raw = [p.read_text(encoding='utf-8') for p in files[:2]]
        self.assertEqual(PAGE.findall(final), [str(n) for n in range(1, 25)])
        expected = [str(n) for n in range(1, 136)] + [f'A.{n}' for n in range(1, 16)]
        self.assertEqual(TAG.findall(final), expected)
        self.assertEqual(re.findall(r'<a id="eq-([^"]+)"', final), [x.replace('A.', 'A-') for x in expected])
        rows = [x for x in pages(final)[2].splitlines() if x.startswith('|') and x not in ('|Symbol|Definition|', '|---|---|')]
        self.assertEqual(len(rows), 121)
        self.assertEqual(re.findall(r'!\[Fig\. (\d+)', final), [str(n) for n in range(1, 23)])
        self.assertEqual(re.findall(r'^\*\*Table (\d+)\*\*', final, re.M), ['1', '2', '3'])
        self.assertEqual(re.findall(r'^[>\s*-]*\[(\d+)\]', final, re.M), [str(n) for n in range(1, 58)])
        self.assertIn('Algorithm 1', final)
        self.assertTrue(all(label in final for label in ('S1', 'S2', 'S3', '(A.9)')))
        kinds = {x['kind'] for x in scan(raw)}
        self.assertTrue({'combining_modifier', 'private_math_glyph', 'html_math_scope', 'control_character'} <= kinds)
        self.assertEqual(scan(final, folder), [])
        self.assertEqual(before, {p: digest(p) for p in files})


if __name__ == '__main__':
    unittest.main()
