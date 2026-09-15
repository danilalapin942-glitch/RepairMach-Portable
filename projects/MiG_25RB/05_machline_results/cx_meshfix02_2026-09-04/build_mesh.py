from __future__ import annotations

import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APP = ROOT / "github_publish" / "RepairMach-Portable" / "app"
sys.path.insert(0, str(APP))

from openvsp_runner import run_vspscript  # noqa: E402


def main() -> None:
    executable = Path(
        r"C:\OpenVSP-3.51.0-win64-Python3.13\OpenVSP-3.51.0-win64\vspscript.exe"
    )
    script = HERE / "build_coarse_mesh.vspscript"
    log = HERE / "logs" / "build_coarse_mesh.log"
    return_code = run_vspscript(executable, script, log, HERE, timeout_seconds=180.0)
    print(f"return_code={return_code}")
    print(log.read_text(encoding="utf-8", errors="replace"))
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
