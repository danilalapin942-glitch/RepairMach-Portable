"""Archive an already inspected snapshot without changing source projects."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile
from inventory import SECRET_PATTERNS, TEXT_SUFFIXES, selected

OUT = Path(__file__).resolve().parent
REPO = OUT / 'repository'
ARCHIVE = REPO / 'archives' / '2026-10-08'
PART_LIMIT = 90 * 1024**2

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()

def main():
    check = json.loads((OUT / 'preparation_check.json').read_text(encoding='utf-8'))
    if check['secret_findings']:
        raise SystemExit('Potential secrets require review before any archive is created')
    preserved_distribution_paths = {'app/openvsp_runner.py', 'config/repairmach_settings.json'}
    reviewed_differences = {d['path'] for d in check['installed_checkout_differences']}
    if reviewed_differences != preserved_distribution_paths:
        raise SystemExit('Unexpected installed/checkout code differences require review')
    roots = json.loads((OUT / 'inventory.json').read_text(encoding='utf-8'))
    workspace = OUT.parent
    extra_roots = {
        'MQ20_backend_experiment_preflight': workspace / 'q20build' / 'preflight',
        'MQ20_backend_experiment_postprocessor_check': workspace / 'q20build' / 'postprocessor_check',
        'MQ20_backend_experiment_postprocessor_routes': workspace / 'q20build' / 'postprocessor_routes',
        'MQ20_backend_experiment_postprocessor_routes_v2': workspace / 'q20build' / 'postprocessor_routes_v2',
        'MQ20_backend_experiment_variants': workspace / 'q20build' / 'variants',
        'MQ20_backend_experiment_source_3510': workspace / 'q20build' / 'source_3510',
    }
    for name, source_root in extra_roots.items():
        if not source_root.exists():
            continue
        records = []
        for source in selected(source_root):
            if source.suffix.lower() in TEXT_SUFFIXES:
                data = source.read_bytes()
                if any(pattern.search(data) for pattern in SECRET_PATTERNS.values()):
                    raise SystemExit(f'Potential secret in supplementary source: {name}/{source.name}')
            stat = source.stat()
            records.append({'path': source.relative_to(source_root).as_posix(), 'bytes': stat.st_size,
                            'mtime_ns': stat.st_mtime_ns, 'sha256': digest(source)})
        roots.append({'name': name, 'source_root': str(source_root), 'files': records,
                      'bytes': sum(r['bytes'] for r in records)})
    direct_files = [p for p in workspace.iterdir() if p.is_file() and p.suffix.lower() in {'.vtk', '.vtp', '.json', '.py'}]
    roots.append({'name': 'workspace_root_calculation_files', 'source_root': str(workspace),
                  'files': [{'path': p.name, 'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns,
                             'sha256': digest(p)} for p in direct_files],
                  'bytes': sum(p.stat().st_size for p in direct_files)})
    installed = next(r for r in roots if r['name'] == 'installed')
    staged = []
    for record in installed['files']:
        rel = Path(record['path'])
        if rel.parts[0] not in {'app', 'config', 'tests', 'docs'}:
            continue
        if rel.as_posix() in preserved_distribution_paths:
            # Keep the already-published portable path resolver, bundled-engine preference
            # and distribution default project. The exact installed settings remain archived.
            staged.append({'path': rel.as_posix(), 'sha256': digest(REPO / rel),
                           'source': 'existing public portable distribution',
                           'installed_archived_sha256': record['sha256']})
            continue
        source = Path(installed['source_root']) / rel
        dest = REPO / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        if digest(dest) != record['sha256']:
            raise SystemExit(f'Staging mismatch: {rel}')
        staged.append({'path': rel.as_posix(), 'sha256': record['sha256']})
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    summaries = []
    for root in roots:
        name = root['name']
        dest = ARCHIVE / name
        dest.mkdir(exist_ok=True)
        archive = OUT / (name + '.zip')
        if archive.exists():
            raise SystemExit(f'Refusing to replace prepared archive: {archive}')
        print('Packaging', name, len(root['files']), flush=True)
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                             allowZip64=True) as z:
            for record in root['files']:
                source = Path(root['source_root']) / record['path']
                if source.stat().st_size != record['bytes'] or digest(source) != record['sha256']:
                    raise SystemExit(f'Source changed after inventory: {name}/{record["path"]}')
                z.write(source, arcname=record['path'])
        # Read every archived byte and verify it against the inspected source manifest.
        with zipfile.ZipFile(archive) as z:
            if len(z.infolist()) != len(root['files']):
                raise SystemExit(f'Archive entry count mismatch: {name}')
            for record in root['files']:
                h = hashlib.sha256()
                with z.open(record['path']) as f:
                    for block in iter(lambda: f.read(1024**2), b''):
                        h.update(block)
                if h.hexdigest() != record['sha256']:
                    raise SystemExit(f'Archive hash mismatch: {name}/{record["path"]}')
        full_hash = digest(archive)
        chunks = []
        if archive.stat().st_size <= PART_LIMIT:
            target = dest / (name + '.zip')
            shutil.copy2(archive, target)
            chunks.append({'file': target.name, 'bytes': target.stat().st_size, 'sha256': digest(target)})
        else:
            with archive.open('rb') as f:
                index = 1
                while True:
                    block = f.read(PART_LIMIT)
                    if not block:
                        break
                    target = dest / f'{name}.zip.part{index:03d}'
                    target.write_bytes(block)
                    chunks.append({'file': target.name, 'bytes': len(block),
                                   'sha256': hashlib.sha256(block).hexdigest()})
                    index += 1
        manifest = {'schema': 'repairmach.project-archive/1.0', 'snapshot_date': '2026-10-08',
                    'name': name, 'source_root': root['source_root'], 'source_files': root['files'],
                    'source_bytes': root['bytes'], 'archive_bytes': archive.stat().st_size,
                    'archive_sha256': full_hash, 'parts': chunks,
                    'verification': 'Every archived file SHA-256 matches the inspected source; ZIP CRC verified while reading',
                    'result_semantics': 'Historical statuses are preserved; inclusion does not qualify a model or result'}
        (dest / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        summaries.append({k: manifest[k] for k in ('name', 'source_bytes', 'archive_bytes', 'archive_sha256', 'parts')}
                         | {'file_count': len(root['files'])})
        (ARCHIVE / 'index.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Verified', name, 'archive MiB', round(archive.stat().st_size / 1024**2, 2), flush=True)
    (ARCHIVE / 'code_snapshot.json').write_text(json.dumps(staged, indent=2), encoding='utf-8')
    shutil.copy2(OUT / 'preparation_check.json', ARCHIVE / 'preparation_check.json')
    for name in ('restore_archive.py', 'inventory.py', 'package_snapshot.py'):
        shutil.copy2(OUT / name, ARCHIVE / name)
    print('ARCHIVES COMPLETE', len(summaries), sum(s['file_count'] for s in summaries), flush=True)

if __name__ == '__main__':
    main()
