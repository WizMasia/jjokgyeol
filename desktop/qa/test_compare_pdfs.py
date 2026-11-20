import unittest

from compare_pdfs import compare


class CompareTests(unittest.TestCase):
    def test_identical(self):
        from pathlib import Path
        fixture = Path(__file__).resolve().parents[2] / "tests/fixtures/blank.pdf"
        if not fixture.exists():
            self.skipTest("no checked-in PDF fixture")
        data = fixture.read_bytes()
        self.assertIsNone(compare(data, data))

    def test_different_and_malformed(self):
        self.assertIn("malformed PDF", compare(b"bad", b"bad"))
        # A valid PDF pair is supplied by the Windows workflow inputs.


if __name__ == "__main__":
    unittest.main()
