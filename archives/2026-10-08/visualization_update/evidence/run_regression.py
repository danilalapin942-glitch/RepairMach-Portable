import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('label', choices=['stage', 'installed', 'checkout'])
args = parser.parse_args()
directories = {'stage': root.parent / 'repository', 'installed': Path('C:/RepairMach-Portable'),
               'checkout': root.parent.parent / 'github_publish/RepairMach-Portable'}
receipt = root / f'regression_{args.label}.json'
assert not receipt.exists()
started = datetime.now().isoformat()
result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'],
    cwd=directories[args.label], capture_output=True, text=True, timeout=600)
log = root / f'regression_{args.label}.log'
log.write_text(result.stdout + result.stderr, encoding='utf-8')
files = ['app/pressure_export.py', 'app/visualization_output.py', 'app/geometry_certification.py', 'app/repairmach_beta.py']
receipt.write_text(json.dumps({'started': started, 'finished': datetime.now().isoformat(),
    'directory': str(directories[args.label]), 'return_code': result.returncode, 'output': result.stdout + result.stderr,
    'code_sha256': {p: hashlib.sha256((directories[args.label] / p).read_bytes()).hexdigest() for p in files},
    'new_aerodynamic_runs': 0}, ensure_ascii=False, indent=2), encoding='utf-8')
print(result.stdout + result.stderr)
sys.exit(result.returncode)
