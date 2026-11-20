import os
import subprocess
import sys
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter, Transformation
from compare_pdfs import compare, read_pages

ROOT = Path(__file__).resolve().parents[2]
PROOF = (ROOT / 'proofs/numbering/odd-even.pdf').read_bytes()


def modified_pdf(change):
    writer = PdfWriter(clone_from=BytesIO(PROOF))
    change(writer.pages[0])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class CompareTests(unittest.TestCase):
    def test_identical(self):
        self.assertIsNone(compare(PROOF, PROOF))

    def test_different_and_malformed(self):
        other = (ROOT / 'proofs/numbering/numbered.pdf').read_bytes()
        self.assertIsNotNone(compare(PROOF, other))
        self.assertIn('malformed PDF', compare(b'bad', b'bad'))

    def test_page_size_with_identical_text(self):
        changed = modified_pdf(lambda page: setattr(page.mediabox, 'upper_right',
                                                    (float(page.mediabox.right) + 1, page.mediabox.top)))
        self.assertEqual([line.text for line in read_pages(PROOF)[0].lines],
                         [line.text for line in read_pages(changed)[0].lines])
        self.assertIn('width', compare(PROOF, changed) or '')

    def test_shifted_text(self):
        changed = modified_pdf(lambda page: page.add_transformation(Transformation().translate(tx=1)))
        self.assertEqual([line.text for line in read_pages(PROOF)[0].lines],
                         [line.text for line in read_pages(changed)[0].lines])
        self.assertIn('x0', compare(PROOF, changed))

    def test_glyph_count_text_and_font(self):
        pages = read_pages(PROOF)
        page, line = pages[0], pages[0].lines[0]
        char = line.chars[0]
        def altered(chars):
            return [replace(page, lines=(replace(line, chars=chars), *page.lines[1:])), *pages[1:]]
        for chars, expected in ((line.chars[:-1], 'char count'),
                                ((replace(char, text='different'), *line.chars[1:]), 'text'),
                                ((replace(char, font='bad+' + char.font.split('+')[-1]), *line.chars[1:]), 'font')):
            with self.subTest(expected=expected), patch('compare_pdfs.read_pages', side_effect=[pages, altered(chars)]):
                self.assertIn(expected, compare(PROOF, PROOF) or '')

    def test_valid_subset_tags_and_tolerance(self):
        pages = read_pages(PROOF)
        page, line = pages[0], pages[0].lines[0]
        char = line.chars[0]
        font = char.font.split('+')[-1]
        first = replace(page, lines=(replace(line, chars=(replace(char, font='ABCDEF+' + font), *line.chars[1:])), *page.lines[1:]))
        second = replace(first, width=first.width + 0.04, height=first.height + 0.04,
                         lines=(replace(line, chars=(replace(char, font='ZYXWVU+' + font), *line.chars[1:])), *page.lines[1:]))
        with patch('compare_pdfs.read_pages', side_effect=[[first, *pages[1:]], [second, *pages[1:]]]):
            self.assertIsNone(compare(PROOF, PROOF))
        second = replace(second, height=first.height + 0.06)
        with patch('compare_pdfs.read_pages', side_effect=[[first, *pages[1:]], [second, *pages[1:]]]):
            self.assertIn('height', compare(PROOF, PROOF) or '')

    def test_import_ignores_installed_tests_package(self):
        script = "import sys, types, runpy; sys.modules['tests'] = types.ModuleType('tests'); runpy.run_path(sys.argv[1], run_name='import_check')"
        result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'desktop/qa/compare_pdfs.py')],
                                cwd=ROOT, capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
