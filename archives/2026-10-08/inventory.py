from pathlib import Path
import hashlib
import json
import re

WORKSPACE = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
CHECKOUT = WORKSPACE / 'github_publish' / 'RepairMach-Portable'
INSTALLED = Path('C:/RepairMach-Portable')
ROOTS = {
    'installed': INSTALLED,
    'aircraft_models_drawings': Path('C:/Multifunctional UAV'),
    'YFQ44_calculations': Path('C:/Users/admin/Documents/Codex/2026-09-13/yfq-44-openvsp'),
    'YFQ44_report': Path('C:/Users/admin/Documents/Codex/2026-09-15/yfq-44-report-only'),
}
for name in ('acceptance', 'diagnostics', 'outputs', 'daytime_lift_2026-08-28',
             'mig25rb_refinement_2026-09-01', 'overnight_m14_m16_2026-08-31',
             'overnight_mig25rb_2026-08-31', 'overnight_refinement_2026-08-27',
             'overnight_sweep', 'q20c', 'q20go', 'q20p', 'q28',
             'repeat_mig25rb_2026-09-01', 'short_hybrid_sweep', 'YFQ_44A_blank',
             'generated_inputs', 'RepairMach_9_Local', 'RepairMach_Portable_Candidate'):
    ROOTS['workspace_' + name] = WORKSPACE / name
ROOTS['MQ20_backend_experiment_runs'] = WORKSPACE / 'q20build' / 'runs'
ROOTS['MQ20_backend_experiment_bundles'] = WORKSPACE / 'q20build' / 'bundles'

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', '.pytest_cache'}
SKIP_SUFFIXES = {'.pyc', '.pyo', '.dwl', '.dwl2', '.obj', '.o', '.mod'}
TEXT_SUFFIXES = {'.py', '.json', '.csv', '.md', '.txt', '.log', '.ps1', '.bat',
                 '.vspscript', '.xml', '.toml', '.yml', '.yaml', '.ini', '.cfg'}
SECRET_PATTERNS = {
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'github_token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'aws_access_key': re.compile(rb'(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])'),
    'openai_key': re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}'),
    'credential_url': re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@'),
}

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()

def selected(root):
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name.startswith('~$') or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path

def main():
    inventory = []
    secret_findings = []
    for name, root in ROOTS.items():
        if not root.exists():
            continue
        records = []
        for path in selected(root):
            stat = path.stat()
            records.append({'path': path.relative_to(root).as_posix(), 'bytes': stat.st_size,
                            'mtime_ns': stat.st_mtime_ns, 'sha256': sha(path)})
            if path.suffix.lower() in TEXT_SUFFIXES:
                with path.open('rb') as f:
                    tail = b''
                    kinds = set()
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        data = tail + chunk
                        kinds.update(k for k, pattern in SECRET_PATTERNS.items() if pattern.search(data))
                        tail = data[-1024:]
                if kinds:
                    secret_findings.append({'root': name, 'path': records[-1]['path'], 'kinds': sorted(kinds)})
        inventory.append({'name': name, 'source_root': str(root), 'files': records,
                          'bytes': sum(r['bytes'] for r in records)})
        print(name, len(records), round(inventory[-1]['bytes'] / 1024**2, 2), flush=True)
    differences = []
    for folder in ('app', 'config', 'tests', 'docs'):
        for path in selected(INSTALLED / folder):
            rel = path.relative_to(INSTALLED)
            other = CHECKOUT / rel
            a = sha(path)
            b = sha(other) if other.is_file() else None
            if a != b:
                differences.append({'path': rel.as_posix(), 'installed_sha256': a,
                                    'checkout_sha256': b})
    OUT.mkdir(exist_ok=True)
    (OUT / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'preparation_check.json').write_text(json.dumps({
        'secret_findings': secret_findings, 'installed_checkout_differences': differences,
        'selected_roots': len(inventory), 'selected_files': sum(len(x['files']) for x in inventory),
        'selected_bytes': sum(x['bytes'] for x in inventory),
        'excluded': 'Git metadata, dependency caches, compiled object caches, Office/CAD lock files; upstream source downloads and unrelated temporary folders not selected',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'secret_findings': secret_findings, 'code_differences': differences}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
