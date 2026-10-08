"""Operator-requested, bounded recheck of one previously attempted native anchor.

This diagnostic scenario cannot issue a certificate or bypass a failed one.
It reuses the certificate's sealed geometry, component sets and acceptance limits.
Only the program's numerical recovery schedule may be updated.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from geometry_certificate import certificate_integrity_errors
from geometry_certification import _run_vspaero_ladder, _run_one_vspaero_probe, _probe_repeatability
from geometry_manifest import sha256_file, write_json


def review_anchor(*, certificate_path, output_root, policy_path, mode, mach, alpha_deg):
    certificate_path = Path(certificate_path).resolve()
    cert = json.loads(certificate_path.read_text(encoding="utf-8"))
    errors = certificate_integrity_errors(cert)
    if mode not in {"lifting", "mixed"}:
        raise ValueError("Режим проверки: lifting или mixed")
    master = Path(cert["master"]["source_path"])
    expected = cert["master"]["sha256_before"]
    if sha256_file(master) != expected:
        errors.append("MASTER изменён после исходного сертификата")
    for item in cert.get("evidence_files", []):
        path = Path(item["path"])
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            errors.append(f"Изменён артефакт: {path}")
    solvers = cert["software"]["solvers"]
    for name in ("openvsp", "vspaero"):
        info = solvers[name]
        if sha256_file(Path(info["executable"])) != info["sha256"]:
            errors.append(f"Изменён решатель {name}")
    anchors = cert["probes"].get(f"vspaero_{mode}", {}).get("anchors", [])
    old = next((a for a in anchors if abs(float(a["mach"]) - mach) < 1e-9
                and abs(float(a["alpha_deg"]) - alpha_deg) < 1e-9), None)
    if old is None:
        errors.append("Точка отсутствует среди ранее выполненных опорных проверок")
    if errors:
        raise ValueError("; ".join(errors))
    meshes = cert["mesh_levels"][f"vspaero_{mode}"]
    for item in meshes.values():
        if item.get("path") and sha256_file(Path(item["path"])) != item["sha256"]:
            raise ValueError("Hash сеточного двойника изменён")
    policy = json.loads(Path(cert["policy"]["snapshot"]).read_text(encoding="utf-8"))
    current = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    policy["probes"]["vspaero"]["numerical_recovery"] = deepcopy(current["probes"]["vspaero"]["numerical_recovery"])
    root = Path(output_root) / datetime.now().strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=False)
    record = {"schema": "repairmach.vspaero-anchor-review/1.0", "state": "running",
              "source_certificate": str(certificate_path), "source_certificate_sha256": sha256_file(certificate_path),
              "master_sha256": expected, "mode": mode, "mach": mach, "alpha_deg": alpha_deg,
              "eligible_scenarios": [], "certificate_issued": False,
              "purpose": "Native numerical diagnostic, NOT full aircraft certification"}
    write_json(root / "status.json", record)
    write_json(root / "policy_snapshot.json", policy)
    print(f"Проверка опорной точки: {root}", flush=True)
    try:
        result = _run_vspaero_ladder(
            mode=mode, meshes=meshes, run_dir=root, reference=cert["reference"], policy=policy,
            executable=Path(solvers["openvsp"]["executable"]),
            point={"mach": mach, "alpha_deg": alpha_deg, "engine_boundary": old.get("engine_boundary", "model")},
        )
        record["ladder"] = result
        fine = next((p for p in result.get("probes", []) if p.get("level") == "fine" and p.get("valid")), None)
        if fine:
            # A longer wake solve guards against accepting a lucky last iteration.
            controls = deepcopy(fine["numerical_controls"])
            controls["wake_num_iter"] = max(96, int(controls["wake_num_iter"] * 1.5))
            longer = _run_one_vspaero_probe(
                model=Path(meshes["fine"]["path"]), probe_dir=root / "wake_verification", mode=mode, level="fine_longer_wake",
                reference=cert["reference"], policy=policy, vspscript_executable=Path(solvers["openvsp"]["executable"]),
                mach=mach, alpha_deg=alpha_deg, engine_boundary=old.get("engine_boundary", "model"),
                numerical_overrides=controls,
            )
            record["wake_verification"] = longer
            record["wake_repeatability"] = _probe_repeatability([fine, longer], policy)
        record["master_unchanged"] = sha256_file(master) == expected
        record["point_verified"] = bool(result.get("valid") and result.get("converged")
                                         and record.get("wake_repeatability", {}).get("valid")
                                         and record["master_unchanged"])
        record["state"] = "complete"
    except Exception as exc:
        record.update(state="failed", error=str(exc), point_verified=False,
                      master_unchanged=sha256_file(master) == expected)
    write_json(root / "status.json", record)
    lines = ["# Перепроверка опорной точки VSPAERO", "", f"M={mach:g}, alpha={alpha_deg:g}, mode={mode}",
             f"", f"Точка подтверждена: {record.get('point_verified', False)}", "",
             "Это диагностика одной опоры. Исходный FAIL-сертификат не заменён; допуска на всю АДХ нет.", "",
             "| Сетка | Принята | CL | CD |", "|---|---|---|---|"]
    for probe in record.get("ladder", {}).get("probes", []):
        values = probe.get("values") or {}
        lines.append(f"| {probe.get('level')} | {probe.get('valid')} | {values.get('CLtot')} | {values.get('CDtot')} |")
    lines += ["", "Полные невязки, повторы и сеточная сходимость: status.json.",
              "Поля давления каждой попытки: её папка paraview. Непринятые попытки помечены diagnostic_not_accepted.",
              f"MASTER неизменён: {record.get('master_unchanged')}"]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root, record
