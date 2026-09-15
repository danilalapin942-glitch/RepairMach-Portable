"""Add a conservative buffer to the two panels nearest the M=1.2 Mach limit."""

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP = ROOT / "github_publish" / "RepairMach-Portable" / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from mach_repair import repair_mach_criterion, scan_mach_criterion, summarize_scan  # noqa: E402
from tri_mesh import diagnose, read_tri, write_tri  # noqa: E402


SOURCE = HERE / "mesh" / "MiG25_MeshFix02_coarse8_wakecone_M1p2.tri"
TARGET = HERE / "mesh" / "MiG25_MeshFix02_coarse8_wakecone_margin010_M1p2.tri"
REPORT = TARGET.with_suffix(".margin_repair.json")
PHYSICAL_MACH = 1.2
DESIRED_MARGIN = 0.01
EFFECTIVE_MACH = 1.0 / (1.0 / PHYSICAL_MACH - DESIRED_MARGIN)


source = read_tri(SOURCE)
before = scan_mach_criterion(source, PHYSICAL_MACH)
repaired, log, _ = repair_mach_criterion(
    source,
    mach=EFFECTIVE_MACH,
    flow=(1.0, 0.0, 0.0),
    safety_margin=0.001,
    max_repairs=20,
)
after = scan_mach_criterion(repaired, PHYSICAL_MACH)
if any(item["mach_margin"] > 0.0 for item in after):
    raise RuntimeError("Buffered solver mesh contains superinclined panels at M=1.2")
write_tri(TARGET, repaired)
payload = {
    "source": str(SOURCE),
    "target": str(TARGET),
    "physical_mach": PHYSICAL_MACH,
    "desired_margin": DESIRED_MARGIN,
    "effective_repair_mach": EFFECTIVE_MACH,
    "scan_before": summarize_scan(before),
    "repair_log": log,
    "scan_after": summarize_scan(after),
    "topology_after": diagnose(repaired),
}
REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
