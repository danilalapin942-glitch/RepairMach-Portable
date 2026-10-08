"""Validate and restore one published ZIP archive to a new directory."""
from pathlib import Path
import argparse
import hashlib
import json
import tempfile
import zipfile

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_directory', type=Path, help='Directory containing manifest.json and ZIP parts')
    parser.add_argument('destination', type=Path, help='A new output directory; existing directories are refused')
    args = parser.parse_args()
    manifest = json.loads((args.archive_directory / 'manifest.json').read_text(encoding='utf-8'))
    if args.destination.exists():
        raise SystemExit('Destination already exists; choose a new directory')
    with tempfile.TemporaryFile() as joined:
        total_hash = hashlib.sha256()
        for part in manifest['parts']:
            name = part['file']
            if Path(name).name != name:
                raise SystemExit('Invalid archive part path')
            h = hashlib.sha256()
            size = 0
            with (args.archive_directory / name).open('rb') as f:
                for data in iter(lambda: f.read(1024**2), b''):
                    h.update(data)
                    total_hash.update(data)
                    joined.write(data)
                    size += len(data)
            if size != part['bytes'] or h.hexdigest() != part['sha256']:
                raise SystemExit(f'Archive part verification failed: {name}')
        if total_hash.hexdigest() != manifest['archive_sha256']:
            raise SystemExit('Full archive SHA-256 mismatch')
        joined.seek(0)
        with zipfile.ZipFile(joined) as z:
            records = {r['path']: r for r in manifest['source_files']}
            names = z.namelist()
            if len(names) != len(records) or set(names) != set(records):
                raise SystemExit('Archive file inventory mismatch')
            resolved_dest = args.destination.resolve()
            for name in names:
                candidate = (resolved_dest / name).resolve()
                if not candidate.is_relative_to(resolved_dest) or '..' in Path(name).parts:
                    raise SystemExit('Unsafe archive entry')
                h = hashlib.sha256()
                with z.open(name) as f:
                    for data in iter(lambda: f.read(1024**2), b''):
                        h.update(data)
                if h.hexdigest() != records[name]['sha256']:
                    raise SystemExit(f'Archived file verification failed: {name}')
            args.destination.mkdir(parents=True)
            z.extractall(args.destination)
    print(f'Verified and restored {len(records)} files to {args.destination}')

if __name__ == '__main__':
    main()
