"""Print the release artifact inventory and optionally write its SHA256SUMS."""
import argparse
from pathlib import Path

from portable import portable_filename, sha256

FILES = {
    'linux': 'jjokgyeol-linux-x86_64.deb',
    'macos': 'jjokgyeol-macos-arm64.dmg',
    'windows': 'jjokgyeol-windows-x64-setup.exe',
}


def files_for(tag, platform=None):
    portable = portable_filename(tag)
    files = [FILES[platform]] if platform else list(FILES.values())
    if platform in (None, 'windows'):
        files.append(portable)
    return files


def write_checksums(directory, files):
    actual = {path.name for path in directory.iterdir()}
    expected = set(files)
    if actual != expected:
        raise ValueError(f'Unexpected release inventory: missing {sorted(expected - actual)}, extra {sorted(actual - expected)}')
    paths = [directory / name for name in files]
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError(f'Invalid release artifact: {path}')
    sums = ''.join(f'{sha256(path)}  {path.name}\n' for path in paths)
    (directory / 'SHA256SUMS').write_text(sums, encoding='ascii', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag')
    parser.add_argument('--platform', choices=FILES)
    parser.add_argument('--checksums', type=Path)
    args = parser.parse_args()
    files = files_for(args.tag, args.platform)
    if args.checksums:
        write_checksums(args.checksums, files)
    print('\n'.join(files))
