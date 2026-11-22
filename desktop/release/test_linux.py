from pathlib import Path
import subprocess
import unittest


RELEASE = Path(__file__).resolve().parent


class LinuxScriptTest(unittest.TestCase):
    def test_requires_resources_and_output_arguments(self):
        result = subprocess.run(['bash', str(RELEASE / 'linux.sh')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('EXTRACTED_RESOURCES OUTPUT_DIR', result.stderr)


if __name__ == '__main__':
    unittest.main()
