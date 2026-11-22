"""Verify removal of the files delivered by a Windows installation."""
import argparse
import json
from pathlib import Path


def installed_files(root):
    return {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}


def check_removed(root, shipped):
    remaining = installed_files(root)
    leaked = sorted(shipped & remaining)
    if leaked:
        raise ValueError(f'{len(leaked)} installed files remain: ' + ', '.join(leaked[:20]))
    return sorted(remaining)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['snapshot', 'verify'])
    parser.add_argument('root', type=Path)
    parser.add_argument('inventory', type=Path)
    args = parser.parse_args()
    if args.action == 'snapshot':
        shipped = installed_files(args.root)
        if not shipped:
            parser.error('empty installation')
        args.inventory.write_text(json.dumps(sorted(shipped)), encoding='utf-8')
    else:
        shipped = set(json.loads(args.inventory.read_text(encoding='utf-8')))
        user_files = check_removed(args.root, shipped)
        print(f'Uninstalled {len(shipped)} files. Additional files: {len(user_files)}', user_files[:20])
