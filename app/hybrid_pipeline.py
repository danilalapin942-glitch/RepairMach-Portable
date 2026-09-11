#!/usr/bin/env python3
"""Deterministic automatic hybrid post-processing for RepairMach 9.1.

The module combines only predeclared physical terms.  It never searches for a
configuration by proximity to reference data and it never applies a hidden
pointwise offset.  Every selected source file and every contribution is kept
in the result bundle so the calculation can be audited or sealed for a blind
study.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import argparse
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from aero_hybrid import (
    load_machline_report,
    machline_wind_axes,
    parse_openvsp_results_csv,
    parse_vspaero_polar,
    validate_vspaero_run_outputs,
)
from validation_metrics import cy_alpha_per_degree, hybrid_cy_alpha_rm921_point
from geometry_action_executor import resolve_hybrid_corrective_actions
from geometry_certificate import verify_certificate
from geometry_manifest import sha256_payload
from vspaero_runner import parse_set_report


REQUEST_SCHEMA = "repairmach.hybrid-request/1.0"
POLICY_SCHEMA = "repairmach.hybrid-policy/1.0"
RESULT_SCHEMA = "repairmach.hybrid-series/1.0"
SEMIEMPIRICAL_TERM_SCHEMA = "repairmach.semiempirical-term/1.0"
SEMIEMPIRICAL_COMPONENT_REPLACEMENT = (
    "semiempirical_component_pressure_wave_all_points"
)
WORKBOOK_BUILDER = Path(__file__).with_name("hybrid_workbook.mjs")
TRANSONIC_DEFAULT = (0.8, 1.2)
VSPAERO_MAX_LOG10_L2_RESIDUAL = -0.3
PARASITE_MANIFEST_SCHEMA = "repairmach.parasite-drag-run/1.0"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_hybrid_policy(path: Path) -> dict:
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_hybrid_policy(policy, policy_path=Path(path))
    return policy


def build_hybrid_series(
    *,
    vspaero_source: Path,
    machline_report_paths: list[Path],
    parasite_result_paths: list[Path],
    policy: dict,
    thin_vspaero_source: Path | None = None,
    policy_path: Path | None = None,
) -> dict:
    """Combine a VSPAERO study, MachLine reports and declared drag terms."""
    _validate_loaded_policy(policy, policy_path=policy_path)
    matching = policy["matching"]
    mach_tolerance = float(matching["mach_tolerance"])
    alpha_tolerance = float(matching["alpha_tolerance"])

    full_rows, full_sources, full_lineage = _load_vspaero_source(
        Path(vspaero_source), mach_tolerance, alpha_tolerance
    )
    machline_points, machline_sources = _load_machline_points(
        [Path(path) for path in machline_report_paths], policy
    )
    parasite_points, parasite_sources, parasite_lineage = _load_parasite_points(
        [Path(path) for path in parasite_result_paths],
        float(matching["parasite_mach_tolerance"]),
    )

    errors: list[str] = []
    warnings: list[str] = []
    output_rows = []
    drag_policy = policy["drag"]
    gap = tuple(float(value) for value in policy.get("transonic_excluded", TRANSONIC_DEFAULT))

    for vsp in sorted(full_rows, key=lambda item: (item["Mach"], item["AoA"])):
        mach = float(vsp["Mach"])
        alpha = float(vsp["AoA"])
        if gap[0] < mach < gap[1]:
            continue
        row_errors = []
        row_warnings = []
        ml = _match_condition(machline_points, mach, alpha, mach_tolerance, alpha_tolerance)
        pressure_wave = None
        if ml is None:
            row_errors.append("нет совпадающего отчёта MachLine")
        else:
            pressure_wave = float(ml["cd"])
            if (
                drag_policy.get("induced_source") == "VSPAERO_CDi"
                and int(ml.get("wake_panels", 0)) > 0
                and not drag_policy.get("allow_machline_wake_with_vspaero_cdi", False)
            ):
                pressure_wave = None
                row_errors.append(
                    "в MachLine активен след при добавлении VSPAERO CDi: возможен двойной учёт"
                )

        induced = (
            float(vsp.get("CDi", 0.0))
            if drag_policy.get("induced_source") == "VSPAERO_CDi"
            else 0.0
        )
        parasite = 0.0
        parasite_source = None
        if drag_policy.get("parasite_source") == "OpenVSP_ParasiteDrag" and mach < 1.0:
            candidate = _match_mach(
                parasite_points,
                mach,
                float(matching["parasite_mach_tolerance"]),
            )
            if candidate is None:
                if drag_policy.get("require_parasite_below_mach_one", True):
                    row_errors.append("нет совпадающего OpenVSP Parasite Drag")
                else:
                    row_warnings.append("вязкая добавка OpenVSP отсутствует и принята равной нулю")
            else:
                parasite = float(candidate["total_cd"])
                parasite_source = candidate["source"]

        semi_terms = semiempirical_contributions(policy, mach)
        if (
            ml is not None
            and ml.get("base_drag_replacement_required")
            and not any(item.get("id") == "base_drag" for item in semi_terms)
        ):
            row_errors.append(
                "сертифицированная сетка MachLine требует активное "
                "полуэмпирическое слагаемое base_drag"
            )
        semi_total = sum(float(item["cd"]) for item in semi_terms)
        semi_uncertainty_rss = math.sqrt(
            sum(float(item["uncertainty_cd"]) ** 2 for item in semi_terms)
        )
        semi_uncertainty_worst_case = sum(
            float(item["uncertainty_cd"]) for item in semi_terms
        )
        total_cd = None
        if pressure_wave is not None and not row_errors:
            total_cd = pressure_wave + induced + parasite + semi_total
        status = "complete" if total_cd is not None else "incomplete"
        errors.extend(f"M={mach:g}, alpha={alpha:g}: {item}" for item in row_errors)
        warnings.extend(f"M={mach:g}, alpha={alpha:g}: {item}" for item in row_warnings)
        output_rows.append(
            {
                "Mach": mach,
                "alpha_deg": alpha,
                "Cy": float(vsp["CLtot"]),
                "CY": float(vsp.get("CStot", 0.0)),
                "Cx_pressure_wave": pressure_wave,
                "Cx_induced": induced,
                "Cx_viscous": parasite,
                "Cx_semiempirical": semi_total,
                "Cx_semiempirical_uncertainty_rss": semi_uncertainty_rss,
                "Cx_semiempirical_uncertainty_worst_case": semi_uncertainty_worst_case,
                "Cx_total": total_cd,
                "status": status,
                "errors": row_errors,
                "warnings": row_warnings,
                "semiempirical_terms": semi_terms,
                "sources": {
                    "vspaero": vsp["_source"],
                    "machline": ml["source"] if ml else None,
                    "parasite_drag": parasite_source,
                },
                "quality": {
                    "machline_residual_norm": ml.get("residual_norm") if ml else None,
                    "machline_residual_max": ml.get("residual_max") if ml else None,
                    "vspaero_log10_l2": vsp.get("L2Res"),
                },
            }
        )

    cy_alpha, cy_alpha_errors, thin_sources, thin_lineage = _build_cy_alpha(
        full_rows=full_rows,
        thin_vspaero_source=thin_vspaero_source,
        policy=policy,
    )
    errors.extend(cy_alpha_errors)

    policy_sources = []
    policy_lineage = None
    if policy_path is not None:
        resolved_policy_path = Path(policy_path).resolve()
        if not resolved_policy_path.is_file():
            raise FileNotFoundError(f"Не найден файл гибридной политики: {resolved_policy_path}")
        policy_sources.append(("hybrid_policy", resolved_policy_path))
        policy_lineage = {
            "path": str(resolved_policy_path),
            "sha256": file_sha256(resolved_policy_path),
            "policy_fingerprint": sha256_payload(policy),
        }
    source_records = _unique_source_records(
        full_sources + machline_sources + parasite_sources + thin_sources + policy_sources
    )
    complete_rows = sum(row["status"] == "complete" for row in output_rows)
    status = "complete" if output_rows and complete_rows == len(output_rows) and not cy_alpha_errors else "incomplete"
    return {
        "schema": RESULT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repairmach_version": "9.1",
        "method_version": policy["method_version"],
        "status": status,
        "reference_data_used": False,
        "pointwise_tuning_used": False,
        "policy": policy,
        "summary": {
            "points": len(output_rows),
            "complete_points": complete_rows,
            "incomplete_points": len(output_rows) - complete_rows,
            "cy_alpha_points": len(cy_alpha),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "rows": output_rows,
        "cy_alpha": cy_alpha,
        "sources": source_records,
        "lineage": {
            "vspaero": full_lineage,
            "thin_vspaero": thin_lineage,
            "machline": _machline_lineage(machline_points),
            "parasite_drag": parasite_lineage,
            "hybrid_policy": policy_lineage,
        },
        "errors": errors,
        "warnings": warnings,
    }


def semiempirical_contributions(policy: dict, mach: float) -> list[dict]:
    result = []
    for term in policy.get("drag", {}).get("semiempirical_terms", []):
        if not term.get("enabled", True):
            continue
        applicability = term.get("applicability", {})
        lower = float(applicability.get("mach_min", -math.inf))
        upper = float(applicability.get("mach_max", math.inf))
        if mach < lower or mach > upper:
            continue
        model = term["model"]
        if model["type"] == "constant":
            value = float(model["value"])
            interpolation = "constant"
        else:
            table = sorted(
                [(float(point[0]), float(point[1])) for point in model["points"]],
                key=lambda item: item[0],
            )
            value = _linear_table_value(table, float(mach))
            if value is None:
                raise ValueError(
                    f"Полуэмпирический член {term['id']} не покрывает M={mach:g}; "
                    "экстраполяция запрещена"
                )
            interpolation = "linear_inside_declared_table"
        result.append(
            {
                "id": term["id"],
                "label": term["label"],
                "cd": value,
                "model": model["type"],
                "evaluation": interpolation,
                "provenance": term["provenance"],
                "certification_schema": term["certification"]["schema"],
                "method_id": term["certification"]["method_id"],
                "equation_version": term["certification"]["equation_version"],
                "uncertainty_fraction": float(
                    term["certification"]["uncertainty_fraction"]
                ),
                "uncertainty_cd": abs(value) * float(
                    term["certification"]["uncertainty_fraction"]
                ),
                "applicability_basis": term["certification"]["applicability_basis"],
                "source": term["certification"]["source"],
                "term_fingerprint": sha256_payload(term),
                "replacement": term.get("replacement"),
            }
        )
    return result


def write_hybrid_outputs(bundle: dict, output_dir: Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "hybrid_result.json"
    csv_path = output_dir / "hybrid_points.csv"
    cy_alpha_path = output_dir / "hybrid_cy_alpha.csv"
    json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    point_headers = [
        "Mach", "alpha_deg", "Cy", "CY", "Cx_pressure_wave", "Cx_induced",
        "Cx_viscous", "Cx_semiempirical", "Cx_semiempirical_uncertainty_rss",
        "Cx_semiempirical_uncertainty_worst_case", "semiempirical_terms_json",
        "Cx_total", "status",
        "machline_residual_norm", "machline_residual_max", "vspaero_log10_l2",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=point_headers, delimiter=";")
        writer.writeheader()
        for row in bundle["rows"]:
            writer.writerow(
                {
                    **{key: row.get(key) for key in point_headers},
                    "machline_residual_norm": row["quality"].get("machline_residual_norm"),
                    "machline_residual_max": row["quality"].get("machline_residual_max"),
                    "vspaero_log10_l2": row["quality"].get("vspaero_log10_l2"),
                    "semiempirical_terms_json": json.dumps(
                        row.get("semiempirical_terms", []),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
    cy_headers = [
        "Mach", "Cy_start", "Cy_end", "alpha_start_deg", "alpha_end_deg",
        "Cy_alpha_direct_per_deg", "Cy_alpha_final_per_deg", "method_version", "status",
    ]
    with cy_alpha_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cy_headers, delimiter=";")
        writer.writeheader()
        for row in bundle["cy_alpha"]:
            writer.writerow({key: row.get(key) for key in cy_headers})
    return {"json": json_path, "points_csv": csv_path, "cy_alpha_csv": cy_alpha_path}


def run_hybrid_request(
    request_path: Path,
    *,
    output_dir: Path | None = None,
    build_workbook: bool = True,
    node_executable: Path | None = None,
    node_modules: Path | None = None,
) -> dict:
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("Неизвестная схема задания гибридного расчёта")
    base = request_path.parent
    policy_path = _resolve_request_path(base, request["policy"])
    policy = load_hybrid_policy(policy_path)
    vspaero_source = _resolve_request_path(base, request["vspaero_source"])
    thin_source = request.get("thin_vspaero_source")
    thin_path = _resolve_request_path(base, thin_source) if thin_source else None
    machline_paths = _expand_request_files(
        base,
        request.get("machline_reports", []),
        request.get("machline_reports_dir"),
        request.get("machline_report_glob", "*_report.json"),
    )
    parasite_paths = _expand_request_files(
        base,
        request.get("parasite_results", []),
        request.get("parasite_results_dir"),
        request.get("parasite_result_glob", "*.csv"),
    )
    bundle = build_hybrid_series(
        vspaero_source=vspaero_source,
        machline_report_paths=machline_paths,
        parasite_result_paths=parasite_paths,
        policy=policy,
        thin_vspaero_source=thin_path,
        policy_path=policy_path,
    )
    certificate_text = request.get("geometry_certificate")
    if certificate_text:
        certificate_path = _resolve_request_path(base, certificate_text)
        scenario_id = str(request.get("scenario_id", "")).strip()
        scenario_sha256 = _normalise_sha256(request.get("scenario_sha256"))
        if not scenario_id or scenario_sha256 is None:
            raise ValueError(
                "Запечатанный гибридный расчёт требует scenario_id и корректный "
                "scenario_sha256 текущего сценария"
            )
        verification = verify_certificate(
            certificate_path,
            backend="vspaero",
            scenario_id=scenario_id,
            scenario_fingerprint=scenario_sha256,
        )
        if not verification["valid"]:
            raise ValueError(
                "Сертификат геометрии не принят: " + "; ".join(verification["errors"])
            )
        certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
        replacement_validation = _validate_replacement_coverage(
            request.get("replacement_coverage"),
            certificate.get("exclusions", []),
            bundle,
            base=base,
            certificate=certificate,
            certificate_path=certificate_path,
        )
        expected_master_sha256 = _normalise_sha256(
            certificate.get("master", {}).get("sha256_before")
        )
        if expected_master_sha256 is None:
            raise ValueError("Сертификат не содержит допустимый SHA-256 MASTER")
        lineage_validation = _validate_certified_vspaero_lineage(
            bundle.get("lineage", {}).get("vspaero"),
            certificate,
            label="основной VSPAERO",
            expected_scenario_id=scenario_id,
            expected_scenario_sha256=scenario_sha256,
        )
        thin_lineage = bundle.get("lineage", {}).get("thin_vspaero")
        if thin_path is not None:
            lineage_validation["thin_vspaero"] = _validate_certified_vspaero_lineage(
                thin_lineage,
                certificate,
                label="thin VSPAERO",
                expected_scenario_id=scenario_id,
                expected_scenario_sha256=scenario_sha256,
            )
        machline_lineage_validation = _validate_certified_machline_lineage(
            certificate_path,
            certificate,
            bundle,
        )
        subsonic_rows = [
            row for row in bundle.get("rows", []) if float(row["Mach"]) < 1.0
        ]
        parasite_lineage_validation = None
        if (
            subsonic_rows
            and policy.get("drag", {}).get("parasite_source") == "OpenVSP_ParasiteDrag"
        ):
            parasite_lineage_validation = _validate_certified_parasite_lineage(
                certificate_path,
                certificate,
                bundle,
                subsonic_rows,
            )

        condition_checks = []
        for row in bundle.get("rows", []):
            point_verification = verify_certificate(
                certificate_path,
                backend="vspaero",
                mach=float(row["Mach"]),
                alpha_deg=float(row["alpha_deg"]),
                scenario_id=scenario_id,
                scenario_fingerprint=scenario_sha256,
            )
            condition_checks.append({
                "Mach": float(row["Mach"]),
                "alpha_deg": float(row["alpha_deg"]),
                "valid": bool(point_verification["valid"]),
                "errors": list(point_verification["errors"]),
            })
        invalid_conditions = [item for item in condition_checks if not item["valid"]]
        if invalid_conditions:
            details = []
            for item in invalid_conditions:
                details.append(
                    f"M={item['Mach']:g}, alpha={item['alpha_deg']:g}: "
                    + "; ".join(item["errors"])
                )
            raise ValueError(
                "Режимы гибридного расчёта выходят за сертификат: " + " | ".join(details)
            )

        bundle["geometry_certificate"] = {
            "certificate_id": certificate.get("certificate_id"),
            "certificate_fingerprint": certificate.get("certificate_fingerprint"),
            "method_version": certificate.get("method_version"),
            "verdict": certificate.get("verdict"),
            "master_sha256": certificate.get("master", {}).get("sha256_before"),
            "master_canonical_sha256": certificate.get("master", {}).get("canonical_sha256"),
            "flags": certificate.get("flags", {}),
            "exclusions": certificate.get("exclusions", []),
            "scope": certificate.get("qualified_scope", certificate.get("scope", {})),
            "scenario_id": scenario_id,
            "scenario_sha256": scenario_sha256,
        }
        bundle["replacement_coverage"] = replacement_validation["coverage"]
        bundle["validation"] = {
            "mode": "sealed_geometry_certificate",
            "valid": True,
            "certificate": verification,
            "vspaero_lineage": lineage_validation,
            "machline_lineage": machline_lineage_validation,
            "parasite_drag_lineage": parasite_lineage_validation,
            "conditions": condition_checks,
            "replacement_coverage": {
                "required": replacement_validation["required"],
                "valid": True,
                "lineage": replacement_validation.get("lineage_validation"),
            },
        }
        bundle["sources"].append({
            "role": "geometry_certificate",
            "file_name": certificate_path.name,
            "path": str(certificate_path.resolve()),
            "size_bytes": certificate_path.stat().st_size,
            "sha256": file_sha256(certificate_path),
        })
        bundle["corrective_action_execution"] = resolve_hybrid_corrective_actions(
            certificate, bundle
        )
    else:
        bundle["geometry_certificate"] = None
        bundle["replacement_coverage"] = []
        bundle["validation"] = {
            "mode": "repairmach_9.1_compatibility_unsealed",
            "valid": True,
            "certificate": None,
            "vspaero_lineage": {
                "accepted_for_compatibility": True,
                "sealed": bool(bundle.get("lineage", {}).get("vspaero", {}).get("sealed")),
            },
            "conditions": [],
            "replacement_coverage": {"required": False, "valid": None},
        }
        bundle.setdefault("warnings", []).append(
            "Сертификат геометрии не приложен; совместимость 9.1 сохранена, но слепой расчёт не запечатан полностью"
        )
        bundle["summary"]["warnings"] = len(bundle["warnings"])
    output = Path(output_dir) if output_dir else base / "hybrid_output"
    outputs = write_hybrid_outputs(bundle, output)
    if build_workbook:
        workbook_path = output / "RepairMach_hybrid_results.xlsx"
        verification_path = output / "workbook_verification.json"
        previews_dir = output / "previews"
        build_hybrid_workbook(
            outputs["json"],
            workbook_path,
            verification_path,
            previews_dir,
            node_executable=node_executable,
            node_modules=node_modules,
        )
        outputs["workbook"] = workbook_path
        outputs["workbook_verification"] = verification_path
        if certificate_text:
            workbook_verification = json.loads(
                verification_path.read_text(encoding="utf-8")
            )
            bundle["corrective_action_execution"] = resolve_hybrid_corrective_actions(
                certificate, bundle, workbook_verification=workbook_verification
            )
            outputs["json"].write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            execution_path = output / "corrective_action_execution.json"
            execution_path.write_text(
                json.dumps(
                    bundle["corrective_action_execution"],
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            outputs["corrective_action_execution"] = execution_path
    return {"bundle": bundle, "outputs": outputs}


def build_hybrid_workbook(
    bundle_path: Path,
    output_path: Path,
    verification_path: Path,
    previews_dir: Path,
    *,
    node_executable: Path | None = None,
    node_modules: Path | None = None,
) -> None:
    node = Path(node_executable) if node_executable else find_node_executable()
    modules = Path(node_modules) if node_modules else find_node_modules()
    if node is None or not node.is_file():
        raise FileNotFoundError(
            "Не найден Node.js для построения Excel. Задайте REPAIRMACH_NODE."
        )
    if modules is None or not (modules / "@oai" / "artifact-tool").is_dir():
        raise FileNotFoundError(
            "Не найден модуль @oai/artifact-tool. Задайте REPAIRMACH_NODE_MODULES."
        )
    if not WORKBOOK_BUILDER.is_file():
        raise FileNotFoundError(f"Не найден построитель Excel: {WORKBOOK_BUILDER}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(node), str(WORKBOOK_BUILDER),
            "--input", str(Path(bundle_path).resolve()),
            "--output", str(Path(output_path).resolve()),
            "--verification", str(Path(verification_path).resolve()),
            "--previews", str(Path(previews_dir).resolve()),
            "--node-modules", str(modules.resolve()),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    log_path = output_path.parent / "workbook.log"
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0 or not output_path.is_file():
        raise RuntimeError(
            f"Excel не построен (код {completed.returncode}). Подробности: {log_path}"
        )
    external_links = verify_xlsx_external_links(output_path)
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    verification["external_links_detected"] = bool(external_links)
    verification["external_link_parts"] = external_links
    verification_path.write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if external_links:
        raise RuntimeError(
            "В построенной книге обнаружены внешние ссылки: " + ", ".join(external_links)
        )


def verify_xlsx_external_links(path: Path) -> list[str]:
    """Return XLSX package parts that indicate an external workbook link."""
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        findings = [name for name in names if name.startswith("xl/externalLinks/")]
        relation_names = [name for name in names if name.endswith(".rels")]
        for name in relation_names:
            text = archive.read(name).decode("utf-8", errors="replace")
            if "externalLinkPath" in text or "externalLinks" in text:
                findings.append(name)
    return sorted(set(findings))


def find_node_executable() -> Path | None:
    candidates = []
    configured = os.environ.get("REPAIRMACH_NODE")
    if configured:
        candidates.append(Path(configured))
    discovered = shutil.which("node")
    if discovered:
        candidates.append(Path(discovered))
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(
            Path(user_profile)
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def find_node_modules() -> Path | None:
    candidates = []
    configured = os.environ.get("REPAIRMACH_NODE_MODULES")
    if configured:
        candidates.append(Path(configured))
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(
            Path(user_profile)
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
        )
    return next(
        (
            path.resolve()
            for path in candidates
            if (path / "@oai" / "artifact-tool").is_dir()
        ),
        None,
    )


def latest_completed_vspaero_study(runs_dir: Path) -> Path | None:
    candidates = []
    for path in Path(runs_dir).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("schema") == "repairmach.vspaero-standard-study/1.0"
            and payload.get("status") == "completed"
        ):
            candidates.append(path)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _validate_loaded_policy(policy: dict, *, policy_path: Path | None = None) -> None:
    """Validate in-memory policies exactly like file-backed policies.

    A caller that declares relative evidence files must also provide the policy
    path.  This keeps those hashes verifiable without writing a temporary policy
    into an unrelated directory.
    """
    _validate_hybrid_policy(policy, policy_path=policy_path)


def _validate_hybrid_policy(policy: dict, *, policy_path: Path | None = None) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise ValueError("Неизвестная схема методики гибридного расчёта")
    if policy.get("pointwise_tuning") is not False:
        raise ValueError("Поточечная подстройка должна быть явно запрещена")
    if policy.get("reference_independent") is not True:
        raise ValueError("Методика должна быть явно независимой от эталонной кривой")
    if not str(policy.get("method_version", "")).strip():
        raise ValueError("Не указана версия гибридной методики")

    matching = policy.get("matching", {})
    for key in ("mach_tolerance", "alpha_tolerance", "parasite_mach_tolerance"):
        value = float(matching.get(key, 0.0))
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"Параметр matching.{key} должен быть положительным")

    drag = policy.get("drag", {})
    if drag.get("pressure_wave_source") != "MachLine_wind_axis_CD":
        raise ValueError("Поддерживается только давление/волновое сопротивление MachLine")
    if drag.get("induced_source") not in ("VSPAERO_CDi", "none"):
        raise ValueError("Неизвестный источник индуктивного сопротивления")
    if drag.get("parasite_source") not in ("OpenVSP_ParasiteDrag", "none"):
        raise ValueError("Неизвестный источник вязкого сопротивления")
    semiempirical_terms = drag.get("semiempirical_terms", [])
    term_ids = [str(term.get("id", "")).strip() for term in semiempirical_terms]
    if len(set(term_ids)) != len(term_ids):
        raise ValueError("Полуэмпирические члены должны иметь уникальные id")
    for term in semiempirical_terms:
        _validate_semiempirical_term(term, policy_path=policy_path)

    lift = policy.get("lift", {})
    if lift.get("coefficient_source") != "VSPAERO_CLtot":
        raise ValueError("Поддерживается только VSPAERO CLtot как источник Cy")
    slope_method = lift.get("cy_alpha", {}).get("method", "direct")
    if slope_method not in ("direct", "RM92.1-CYA-WINGBODY-3-working"):
        raise ValueError("Неизвестная методика Cyα")


def _validate_semiempirical_term(term: dict, *, policy_path: Path | None = None) -> None:
    if not str(term.get("id", "")).strip() or not str(term.get("label", "")).strip():
        raise ValueError("Полуэмпирический член должен иметь id и label")
    enabled = term.get("enabled", True) is True
    if enabled and not str(term.get("provenance", "")).strip():
        raise ValueError(f"Для члена {term.get('id')} не указан источник методики")

    applicability = term.get("applicability", {})
    try:
        mach_min = float(applicability["mach_min"])
        mach_max = float(applicability["mach_max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Для члена {term.get('id')} нужен явный диапазон применимости Mach"
        ) from exc
    if (
        not math.isfinite(mach_min)
        or not math.isfinite(mach_max)
        or mach_min < 0.0
        or mach_max < mach_min
    ):
        raise ValueError(f"Некорректный диапазон Mach у члена {term.get('id')}")

    model = term.get("model", {})
    model_type = model.get("type")
    if model_type == "constant":
        values = [model.get("value")]
    elif model_type == "mach_table":
        points = model.get("points", [])
        if len(points) < 2:
            raise ValueError(f"Таблица {term.get('id')} должна содержать минимум две точки")
        mach_values = [float(point[0]) for point in points]
        if len(set(mach_values)) != len(mach_values):
            raise ValueError(f"В таблице {term.get('id')} повторяются числа Mach")
        if any(not math.isfinite(value) or value < 0.0 for value in mach_values):
            raise ValueError(f"Некорректные числа Mach в таблице {term.get('id')}")
        if min(mach_values) > mach_min or max(mach_values) < mach_max:
            raise ValueError(
                f"Таблица {term.get('id')} не покрывает заявленный диапазон применимости"
            )
        values = [point[1] for point in points]
    else:
        raise ValueError(f"Неизвестная модель полуэмпирического члена {term.get('id')}")
    for value in values:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0.0:
            raise ValueError(f"Вклад {term.get('id')} должен быть конечным и неотрицательным")

    replacement = term.get("replacement")
    if replacement is not None:
        if not isinstance(replacement, dict):
            raise ValueError(f"replacement члена {term.get('id')} должен быть объектом")
        if replacement.get("method") != SEMIEMPIRICAL_COMPONENT_REPLACEMENT:
            raise ValueError(
                f"Неизвестный способ покомпонентного замещения у члена {term.get('id')}"
            )
        component = replacement.get("component")
        if (
            not isinstance(component, str)
            or not component.strip()
            or component != component.strip()
        ):
            raise ValueError(
                f"replacement члена {term.get('id')} требует точное имя component"
            )
        channels = replacement.get("coverage_channels")
        if channels != ["pressure_wave"]:
            raise ValueError(
                f"replacement члена {term.get('id')} должен объявлять ровно "
                "coverage_channels=['pressure_wave']"
            )
        if replacement.get("alpha_dependence") != "independent":
            raise ValueError(
                f"replacement члена {term.get('id')} должен явно объявлять "
                "alpha_dependence='independent'"
            )

    if not enabled:
        return

    certification = term.get("certification")
    if not isinstance(certification, dict):
        raise ValueError(
            f"Активный член {term.get('id')} не имеет проверяемого паспорта методики"
        )
    if certification.get("schema") != SEMIEMPIRICAL_TERM_SCHEMA:
        raise ValueError(f"Неизвестная схема паспорта члена {term.get('id')}")
    for field in ("method_id", "equation_version", "applicability_basis"):
        if not str(certification.get(field, "")).strip():
            raise ValueError(
                f"В паспорте члена {term.get('id')} не заполнено поле {field}"
            )
    if certification.get("reference_independent") is not True:
        raise ValueError(
            f"Член {term.get('id')} должен быть независим от эталонной кривой"
        )
    if certification.get("pointwise_tuning") is not False:
        raise ValueError(
            f"Для члена {term.get('id')} должна быть запрещена поточечная подстройка"
        )
    uncertainty = _finite_or_none(certification.get("uncertainty_fraction"))
    if uncertainty is None or not 0.0 <= uncertainty <= 1.0:
        raise ValueError(
            f"Для члена {term.get('id')} нужна относительная неопределённость от 0 до 1"
        )

    source = certification.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"В паспорте члена {term.get('id')} отсутствует источник")
    if source.get("kind") not in (
        "published_method",
        "validated_dataset",
        "declared_engineering_method",
    ):
        raise ValueError(f"Неизвестный тип источника у члена {term.get('id')}")
    if not str(source.get("citation", "")).strip():
        raise ValueError(f"В паспорте члена {term.get('id')} нет ссылки на источник")

    source_path_text = str(source.get("path", "")).strip()
    source_sha256 = _normalise_sha256(source.get("sha256"))
    if source_path_text or source.get("sha256") is not None:
        if not source_path_text or source_sha256 is None:
            raise ValueError(
                f"Файл-источник члена {term.get('id')} должен иметь path и sha256"
            )
        source_path = Path(source_path_text)
        if not source_path.is_absolute():
            if policy_path is None:
                raise ValueError(
                    f"Относительный файл-источник члена {term.get('id')} нельзя проверить без policy_path"
                )
            source_path = Path(policy_path).resolve().parent / source_path
        if not source_path.is_file():
            raise ValueError(f"Не найден файл-источник члена {term.get('id')}: {source_path}")
        if file_sha256(source_path) != source_sha256:
            raise ValueError(f"Нарушен hash файла-источника члена {term.get('id')}")


def _load_vspaero_source(
    source: Path,
    mach_tolerance: float,
    alpha_tolerance: float,
) -> tuple[list[dict], list[tuple[str, Path]], dict]:
    """Load VSPAERO rows and preserve their geometry lineage.

    A bare POLAR remains supported for RepairMach 9.1 compatibility, but it
    cannot prove which VSP3 produced it and is therefore deliberately marked
    ``sealed=false``.  A JSON source is sealed only when every POLAR selected
    from it carries a well-formed ``source_vsp3_sha256`` value.  The hash can
    be supplied by the individual run or inherited from the parent manifest.
    """
    if not source.is_file():
        raise FileNotFoundError(f"Не найден источник VSPAERO: {source}")
    source = source.resolve()
    polar_items: list[dict] = []
    manifest_payload = None
    if source.suffix.lower() == ".polar":
        polar_items.append({
            "path": source,
            "source_vsp3_sha256": None,
            "solver_geometry_sha256": None,
            "geometry_mode": None,
            "setup": {"complete": False},
            "source_vsp3": None,
            "scenario_id": None,
            "scenario_sha256": None,
            "declared_output_sha256": None,
            "declared_script_sha256": None,
            "output_quality_valid": False,
            "producer_status": None,
            "executable_sha256": {},
            "evidence_payload": None,
            "origin": "direct_polar",
        })
    else:
        payload = json.loads(source.read_text(encoding="utf-8"))
        manifest_payload = payload
        inherited_hash = _manifest_master_geometry_sha256(payload)
        inherited_solver_hash = _manifest_solver_geometry_sha256(payload)
        inherited_mode = _manifest_geometry_mode(payload)
        inherited_setup = _manifest_vspaero_setup(payload)
        inherited_output_hash = _manifest_output_sha256(payload, "polar")
        inherited_quality = _manifest_output_quality_valid(payload)
        inherited_executables = {
            role: _manifest_executable_sha256(payload, role)
            for role in ("openvsp", "vspaero")
        }
        inherited_status = payload.get("status")
        inherited_source = payload.get("source_vsp3")
        inherited_scenario = payload.get("scenario_id")
        inherited_scenario_sha256 = _manifest_scenario_sha256(payload)
        inherited_script_sha256 = _manifest_output_sha256(payload, "script")
        for item in payload.get("polar_sources", []):
            if isinstance(item, dict):
                value = item.get("polar") or item.get("path") or item.get("file")
                item_hash = _manifest_master_geometry_sha256(item) or inherited_hash
                item_solver_hash = _manifest_solver_geometry_sha256(item) or inherited_solver_hash
                item_mode = _manifest_geometry_mode(item) or inherited_mode
                item_setup = _manifest_vspaero_setup(item)
                if not item_setup.get("complete"):
                    item_setup = inherited_setup
                item_source = item.get("source_vsp3") or inherited_source
                item_scenario = item.get("scenario_id") or inherited_scenario
                item_scenario_sha256 = (
                    _manifest_scenario_sha256(item) or inherited_scenario_sha256
                )
                item_output_hash = (
                    _manifest_output_sha256(item, "polar") or inherited_output_hash
                )
                item_script_hash = (
                    _manifest_output_sha256(item, "script") or inherited_script_sha256
                )
                item_quality = (
                    _manifest_output_quality_valid(item) or inherited_quality
                )
                item_status = item.get("status", inherited_status)
                item_executables = {
                    role: _manifest_executable_sha256(item, role)
                    or inherited_executables.get(role)
                    for role in ("openvsp", "vspaero")
                }
            else:
                value = item
                item_hash = inherited_hash
                item_solver_hash = inherited_solver_hash
                item_mode = inherited_mode
                item_setup = inherited_setup
                item_source = inherited_source
                item_scenario = inherited_scenario
                item_scenario_sha256 = inherited_scenario_sha256
                item_output_hash = inherited_output_hash
                item_script_hash = inherited_script_sha256
                item_quality = inherited_quality
                item_status = inherited_status
                item_executables = dict(inherited_executables)
            if value:
                polar_items.append({
                    "path": _resolve_manifest_output(source.parent, value),
                    "source_vsp3_sha256": item_hash,
                    "solver_geometry_sha256": item_solver_hash,
                    "geometry_mode": item_mode,
                    "setup": item_setup,
                    "source_vsp3": item_source,
                    "scenario_id": item_scenario,
                    "scenario_sha256": item_scenario_sha256,
                    "declared_output_sha256": item_output_hash,
                    "declared_script_sha256": item_script_hash,
                    "output_quality_valid": item_quality,
                    "producer_status": item_status,
                    "executable_sha256": item_executables,
                    "evidence_payload": item if isinstance(item, dict) else payload,
                    "origin": "polar_sources",
                })
        for run in payload.get("runs", []):
            if run.get("status") == "completed" and run.get("outputs", {}).get("polar"):
                polar_items.append({
                    "path": _resolve_manifest_output(source.parent, run["outputs"]["polar"]),
                    "source_vsp3_sha256": (
                        _manifest_master_geometry_sha256(run) or inherited_hash
                    ),
                    "solver_geometry_sha256": (
                        _manifest_solver_geometry_sha256(run) or inherited_solver_hash
                    ),
                    "geometry_mode": _manifest_geometry_mode(run) or inherited_mode,
                    "setup": _manifest_vspaero_setup(run),
                    "source_vsp3": run.get("source_vsp3") or inherited_source,
                    "scenario_id": run.get("scenario_id") or inherited_scenario,
                    "scenario_sha256": (
                        _manifest_scenario_sha256(run) or inherited_scenario_sha256
                    ),
                    "declared_output_sha256": (
                        _manifest_output_sha256(run, "polar") or inherited_output_hash
                    ),
                    "declared_script_sha256": (
                        _manifest_output_sha256(run, "script") or inherited_script_sha256
                    ),
                    "output_quality_valid": (
                        _manifest_output_quality_valid(run) or inherited_quality
                    ),
                    "producer_status": run.get("status", inherited_status),
                    "executable_sha256": {
                        role: _manifest_executable_sha256(run, role)
                        or inherited_executables.get(role)
                        for role in ("openvsp", "vspaero")
                    },
                    "evidence_payload": run,
                    "origin": "completed_run",
                })
        if not polar_items and payload.get("outputs", {}).get("polar"):
            polar_items.append({
                "path": _resolve_manifest_output(source.parent, payload["outputs"]["polar"]),
                "source_vsp3_sha256": inherited_hash,
                "solver_geometry_sha256": inherited_solver_hash,
                "geometry_mode": inherited_mode,
                "setup": inherited_setup,
                "source_vsp3": inherited_source,
                "scenario_id": inherited_scenario,
                "scenario_sha256": inherited_scenario_sha256,
                "declared_output_sha256": inherited_output_hash,
                "declared_script_sha256": inherited_script_sha256,
                "output_quality_valid": inherited_quality,
                "producer_status": inherited_status,
                "executable_sha256": dict(inherited_executables),
                "evidence_payload": payload,
                "origin": "manifest_outputs",
            })

    # A study can list the same POLAR both as a derived source and as a run
    # output.  Merge identical declarations, but never hide contradictory
    # geometry hashes for the same aerodynamic result.
    merged: dict[str, dict] = {}
    for item in polar_items:
        path = Path(item["path"]).resolve()
        key = str(path).lower()
        prior = merged.get(key)
        if prior is None:
            merged[key] = {**item, "path": path}
            continue
        old_hash = prior.get("source_vsp3_sha256")
        new_hash = item.get("source_vsp3_sha256")
        if old_hash and new_hash and old_hash != new_hash:
            raise ValueError(
                f"Противоречивая геометрическая линия VSPAERO для {path.name}"
            )
        if not old_hash and new_hash:
            prior["source_vsp3_sha256"] = new_hash
        old_solver_hash = prior.get("solver_geometry_sha256")
        new_solver_hash = item.get("solver_geometry_sha256")
        if old_solver_hash and new_solver_hash and old_solver_hash != new_solver_hash:
            raise ValueError(
                f"Противоречивый VSPAERO solver geometry для {path.name}"
            )
        prior["solver_geometry_sha256"] = old_solver_hash or new_solver_hash
        old_mode = prior.get("geometry_mode")
        new_mode = item.get("geometry_mode")
        if old_mode and new_mode and old_mode != new_mode:
            raise ValueError(f"Противоречивый VSPAERO geometry mode для {path.name}")
        prior["geometry_mode"] = old_mode or new_mode
        if (
            prior.get("setup", {}).get("complete")
            and item.get("setup", {}).get("complete")
            and json.dumps(prior["setup"], sort_keys=True) != json.dumps(item["setup"], sort_keys=True)
        ):
            raise ValueError(f"Противоречивая постановка VSPAERO для {path.name}")
        if not prior.get("setup", {}).get("complete") and item.get("setup", {}).get("complete"):
            prior["setup"] = item["setup"]
        prior["source_vsp3"] = prior.get("source_vsp3") or item.get("source_vsp3")
        old_scenario_id = prior.get("scenario_id")
        new_scenario_id = item.get("scenario_id")
        if old_scenario_id and new_scenario_id and old_scenario_id != new_scenario_id:
            raise ValueError(f"Противоречивый scenario_id для {path.name}")
        prior["scenario_id"] = old_scenario_id or new_scenario_id
        old_scenario_sha = prior.get("scenario_sha256")
        new_scenario_sha = item.get("scenario_sha256")
        if old_scenario_sha and new_scenario_sha and old_scenario_sha != new_scenario_sha:
            raise ValueError(f"Противоречивый scenario_sha256 для {path.name}")
        prior["scenario_sha256"] = old_scenario_sha or new_scenario_sha
        old_output_hash = prior.get("declared_output_sha256")
        new_output_hash = item.get("declared_output_sha256")
        if old_output_hash and new_output_hash and old_output_hash != new_output_hash:
            raise ValueError(f"Противоречивый SHA-256 POLAR для {path.name}")
        prior["declared_output_sha256"] = old_output_hash or new_output_hash
        old_script_hash = prior.get("declared_script_sha256")
        new_script_hash = item.get("declared_script_sha256")
        if old_script_hash and new_script_hash and old_script_hash != new_script_hash:
            raise ValueError(f"Противоречивый SHA-256 script для {path.name}")
        prior["declared_script_sha256"] = old_script_hash or new_script_hash
        prior["output_quality_valid"] = bool(
            prior.get("output_quality_valid") or item.get("output_quality_valid")
        )
        prior["producer_status"] = prior.get("producer_status") or item.get("producer_status")
        prior_executables = prior.setdefault("executable_sha256", {})
        for role, value in item.get("executable_sha256", {}).items():
            old_value = prior_executables.get(role)
            if old_value and value and old_value != value:
                raise ValueError(
                    f"Противоречивый SHA-256 решателя {role} для {path.name}"
                )
            prior_executables[role] = old_value or value
        if item.get("evidence_payload") and _manifest_output_value(
            item["evidence_payload"], "log"
        ):
            prior["evidence_payload"] = item["evidence_payload"]
    polar_items = list(merged.values())
    if not polar_items:
        raise ValueError("Манифест VSPAERO не содержит POLAR")

    rows = []
    records = []
    lineage_polars = []
    for item in polar_items:
        path = Path(item["path"])
        if not path.is_file():
            raise FileNotFoundError(f"Не найден POLAR из манифеста: {path}")
        records.append(("vspaero_polar", path))
        parsed_rows = parse_vspaero_polar(
            path, required_fields=("Beta", "Mach", "AoA", "CLtot", "CDi")
        )
        actual_output_hash = file_sha256(path)
        declared_output_hash = _normalise_sha256(item.get("declared_output_sha256"))
        output_hash_bound = bool(
            declared_output_hash and declared_output_hash == actual_output_hash
        )
        output_quality_revalidated = False
        log_lineage = None
        script_lineage = None
        numerical_controls_valid = False
        actual_setup = None
        evidence_payload = item.get("evidence_payload")
        if isinstance(evidence_payload, dict):
            log_value = _manifest_output_value(evidence_payload, "log")
            conditions = evidence_payload.get("conditions", {})
            mach_axis = conditions.get("mach", {}) if isinstance(conditions, dict) else {}
            alpha_axis = conditions.get("alpha_deg", {}) if isinstance(conditions, dict) else {}
            if (
                item.get("producer_status") == "completed"
                or item.get("output_quality_valid")
                or item.get("declared_output_sha256")
            ):
                if not log_value or not isinstance(mach_axis, dict) or not isinstance(alpha_axis, dict):
                    raise ValueError(
                        f"Манифест {path.name} не содержит лог и полную сетку условий VSPAERO"
                    )
                log_path = _resolve_manifest_output(source.parent, log_value)
                if not log_path.is_file():
                    raise FileNotFoundError(f"Не найден лог VSPAERO из манифеста: {log_path}")
                declared_log_hash = _manifest_output_sha256(evidence_payload, "log")
                actual_log_hash = file_sha256(log_path)
                if declared_log_hash != actual_log_hash:
                    raise ValueError(f"SHA-256 лога VSPAERO не соответствует манифесту: {log_path.name}")
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                set_report = parse_set_report(log_text)
                if not set_report.get("valid") or not set_report.get("calculation_complete"):
                    raise ValueError(
                        f"Лог VSPAERO {log_path.name} не подтверждает фактическую постановку "
                        "ГО/Engine boundary и завершение расчёта"
                    )
                tail_setup = set_report.get("tail_setup", {})
                actual_tail = {"mode": tail_setup.get("MODE")}
                if str(tail_setup.get("MODE", "")).strip().lower() == "fixed_incidence":
                    actual_tail.update({
                        "geometry_name": tail_setup.get("NAME"),
                        "incidence_deg": tail_setup.get("ACTUAL_ANGLE_DEG"),
                    })
                engine_setup = set_report.get("engine_setup", {})
                actual_engine_boundary = _normalise_engine_boundary(
                    engine_setup.get("MODE")
                )
                declared_setup = item.get("setup", {})
                if not _tail_setups_equal(
                    actual_tail, declared_setup.get("horizontal_tail")
                ):
                    raise ValueError(
                        f"Лог VSPAERO {log_path.name}: фактический угол/режим ГО "
                        "не совпадает с манифестом"
                    )
                if actual_engine_boundary != _normalise_engine_boundary(
                    declared_setup.get("engine_boundary")
                ):
                    raise ValueError(
                        f"Лог VSPAERO {log_path.name}: фактический Engine boundary "
                        "не совпадает с манифестом"
                    )
                declared_beta = _finite_or_none(declared_setup.get("beta_deg"))
                if declared_beta is None or any(
                    _finite_or_none(row.get("Beta")) is None
                    or abs(float(row["Beta"]) - declared_beta) > 1.0e-9
                    for row in parsed_rows
                ):
                    raise ValueError(
                        f"POLAR {path.name}: фактический Beta не совпадает с манифестом"
                    )
                actual_setup = {
                    "horizontal_tail": actual_tail,
                    "engine_boundary": actual_engine_boundary,
                    "beta_deg": declared_beta,
                }
                script_value = _manifest_output_value(evidence_payload, "script")
                declared_script_hash = _normalise_sha256(
                    item.get("declared_script_sha256")
                )
                if not script_value or declared_script_hash is None:
                    raise ValueError(
                        f"Манифест {path.name} не содержит script и его SHA-256"
                    )
                script_path = _resolve_manifest_output(source.parent, script_value)
                if not script_path.is_file():
                    raise FileNotFoundError(
                        f"Не найден script VSPAERO из манифеста: {script_path}"
                    )
                actual_script_hash = file_sha256(script_path)
                if actual_script_hash != declared_script_hash:
                    raise ValueError(
                        f"SHA-256 script VSPAERO не соответствует манифесту: {script_path.name}"
                    )
                declared_controls = evidence_payload.get("numerical_controls")
                actual_controls = _parse_vspaero_script_controls(
                    script_path.read_text(encoding="utf-8", errors="replace")
                )
                if not _vspaero_controls_equal(actual_controls, declared_controls):
                    raise ValueError(
                        f"Script VSPAERO {script_path.name}: фактические solver_controls "
                        "не совпадают с манифестом"
                    )
                numerical_controls_valid = True
                records.append(("vspaero_script", script_path))
                script_lineage = {
                    "path": str(script_path.resolve()),
                    "sha256": actual_script_hash,
                    "declared_sha256": declared_script_hash,
                    "numerical_controls": actual_controls,
                }
                declared_residual_limit = float(
                    evidence_payload.get("output_quality", {}).get(
                        "max_log10_l2_residual", VSPAERO_MAX_LOG10_L2_RESIDUAL
                    )
                )
                if not math.isfinite(declared_residual_limit):
                    raise ValueError(
                        f"Манифест {path.name} содержит некорректный порог невязки VSPAERO"
                    )
                # A producer may certify a stricter local requirement, but an
                # input manifest must never relax the RepairMach method gate.
                effective_residual_limit = min(
                    declared_residual_limit, VSPAERO_MAX_LOG10_L2_RESIDUAL
                )
                quality = validate_vspaero_run_outputs(
                    path,
                    log_path,
                    mach_start=float(mach_axis["start"]),
                    mach_end=float(mach_axis["end"]),
                    mach_points=int(mach_axis["points"]),
                    alpha_start=float(alpha_axis["start"]),
                    alpha_end=float(alpha_axis["end"]),
                    alpha_points=int(alpha_axis["points"]),
                    max_log10_l2_residual=effective_residual_limit,
                )
                output_quality_revalidated = bool(quality.get("valid"))
                records.append(("vspaero_log", log_path))
                log_lineage = {
                    "path": str(log_path.resolve()),
                    "sha256": actual_log_hash,
                    "declared_sha256": declared_log_hash,
                }
        for row in parsed_rows:
            rows.append({**row, "_source": path.name})
        lineage_polars.append({
            "file_name": path.name,
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": actual_output_hash,
            "declared_output_sha256": declared_output_hash,
            "output_hash_bound": output_hash_bound,
            "output_quality_declared_valid": bool(item.get("output_quality_valid")),
            "output_quality_valid": output_quality_revalidated,
            "log": log_lineage,
            "script": script_lineage,
            "numerical_controls_valid": numerical_controls_valid,
            "producer_status": item.get("producer_status"),
            "executable_sha256": dict(item.get("executable_sha256", {})),
            "source_vsp3": item.get("source_vsp3"),
            "source_vsp3_sha256": item.get("source_vsp3_sha256"),
            "solver_geometry_sha256": item.get("solver_geometry_sha256"),
            "geometry_mode": item.get("geometry_mode"),
            "setup": item.get("setup"),
            "actual_setup": actual_setup,
            "points": [
                {
                    "Mach": float(row["Mach"]),
                    "alpha_deg": float(row["AoA"]),
                }
                for row in parsed_rows
            ],
            "scenario_id": item.get("scenario_id"),
            "scenario_sha256": item.get("scenario_sha256"),
            "origin": item.get("origin"),
        })
    geometry_hashes = sorted({
        item["source_vsp3_sha256"]
        for item in lineage_polars
        if item.get("source_vsp3_sha256")
    })
    sealed = bool(
        manifest_payload is not None
        and lineage_polars
        and all(
            item.get("source_vsp3_sha256")
            and item.get("solver_geometry_sha256")
            and item.get("geometry_mode")
            and item.get("setup", {}).get("complete")
            and item.get("output_hash_bound")
            and item.get("output_quality_valid")
            and item.get("script", {}).get("sha256")
            and item.get("numerical_controls_valid")
            and item.get("producer_status") == "completed"
            for item in lineage_polars
        )
    )
    lineage = {
        "source_kind": "manifest" if manifest_payload is not None else "direct_polar",
        "sealed": sealed,
        "manifest": (
            {
                "file_name": source.name,
                "path": str(source),
                "size_bytes": source.stat().st_size,
                "sha256": file_sha256(source),
                "schema": manifest_payload.get("schema"),
                "scenario_id": manifest_payload.get("scenario_id"),
                "scenario_sha256": _manifest_scenario_sha256(manifest_payload),
            }
            if manifest_payload is not None
            else None
        ),
        "source_vsp3_sha256s": geometry_hashes,
        "solver_geometry_sha256s": sorted({
            item["solver_geometry_sha256"]
            for item in lineage_polars
            if item.get("solver_geometry_sha256")
        }),
        "geometry_modes": sorted({
            str(item["geometry_mode"])
            for item in lineage_polars
            if item.get("geometry_mode")
        }),
        "polar_sources": lineage_polars,
    }
    return (
        _deduplicate_vspaero(rows, mach_tolerance, alpha_tolerance),
        records,
        lineage,
    )


def _deduplicate_vspaero(rows: list[dict], mach_tolerance: float, alpha_tolerance: float) -> list[dict]:
    selected = []
    for row in rows:
        matches = [
            item for item in selected
            if abs(float(item["Mach"]) - float(row["Mach"])) <= mach_tolerance
            and abs(float(item["AoA"]) - float(row["AoA"])) <= alpha_tolerance
        ]
        if not matches:
            selected.append(row)
            continue
        current = matches[0]
        if _same_vspaero_solution(current, row):
            if _vsp_quality(row) < _vsp_quality(current):
                selected[selected.index(current)] = row
            continue
        raise ValueError(
            "Неоднозначные точки VSPAERO для "
            f"M={row['Mach']:g}, alpha={row['AoA']:g}: "
            f"{current['_source']} и {row['_source']}"
        )
    return selected


def _same_vspaero_solution(left: dict, right: dict) -> bool:
    keys = ("CLtot", "CDi", "CStot")
    return all(abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) <= 1.0e-9 for key in keys)


def _vsp_quality(row: dict) -> float:
    return float(row.get("L2Res", math.inf))


def _load_machline_points(paths: list[Path], policy: dict) -> tuple[list[dict], list[tuple[str, Path]]]:
    points = []
    sources = []
    residual_limit = float(policy.get("quality", {}).get("machline_residual_norm_max", math.inf))
    pattern = str(policy.get("drag", {}).get("machline_geometry_glob", "*"))
    for path in paths:
        path = Path(path).resolve()
        report = load_machline_report(path)
        run_manifest_path = path.with_name(path.stem + "_manifest.json")
        run_manifest = None
        output_hash_bound = False
        producer_quality_valid = False
        producer_status = None
        solver_sha256 = None
        if run_manifest_path.is_file():
            run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            if run_manifest.get("schema") != "repairmach.machline-run/1.0":
                raise ValueError(f"Неизвестная схема манифеста MachLine: {run_manifest_path.name}")
            producer_status = run_manifest.get("status")
            producer_quality_valid = _manifest_output_quality_valid(run_manifest)
            declared_report_hash = _manifest_output_sha256(run_manifest, "report")
            output_hash_bound = bool(
                declared_report_hash and declared_report_hash == file_sha256(path)
            )
            solver_sha256 = _normalise_sha256(
                run_manifest.get("solver", {}).get("sha256")
            )
            if producer_status == "completed" and (
                not producer_quality_valid or not output_hash_bound or solver_sha256 is None
            ):
                raise ValueError(
                    f"Манифест MachLine не подтверждает целостность результата: {path.name}"
                )
            sources.append(("machline_manifest", run_manifest_path))
        geometry = str(report.get("input", {}).get("geometry", {}).get("file", ""))
        manifest_geometry = (
            run_manifest.get("geometry", {}).get("path")
            if isinstance(run_manifest, dict)
            and isinstance(run_manifest.get("geometry"), dict)
            else None
        )
        geometry_path = (
            _resolve_manifest_output(run_manifest_path.parent, manifest_geometry)
            if manifest_geometry else _resolve_report_geometry(path, geometry)
        )
        if geometry_path is not None and not geometry_path.is_file():
            geometry_path = None
        geometry_sha256 = file_sha256(geometry_path) if geometry_path is not None else None
        if pattern != "*" and not fnmatch.fnmatch(Path(geometry).name.lower(), pattern.lower()):
            continue
        status_code = int(report["solver_results"]["solver_status_code"])
        residual = report["solver_results"]["residual"]
        residual_norm = _finite_or_none(residual.get("norm"))
        residual_max = _finite_or_none(residual.get("max"))
        if residual_norm is None or residual_max is None:
            raise ValueError(f"MachLine report не содержит конечную невязку: {path.name}")
        if status_code != 0:
            continue
        if residual_norm is not None and residual_norm > residual_limit:
            continue
        axes = machline_wind_axes(report)
        base_drag_replacement_required = False
        force_output_kind = "raw_machline_total_forces"
        if run_manifest is not None and isinstance(run_manifest.get("force_output"), dict):
            force_output = run_manifest["force_output"]
            if force_output.get("kind") != "masked_thick_body_pressure_wave":
                raise ValueError(f"Неизвестный вид маскированных сил: {path.name}")
            masked_value = _manifest_output_value(run_manifest, "masked_force")
            mask_value = _manifest_output_value(run_manifest, "force_mask")
            masked_path = (
                _resolve_manifest_output(run_manifest_path.parent, masked_value)
                if masked_value else None
            )
            mask_path = (
                _resolve_manifest_output(run_manifest_path.parent, mask_value)
                if mask_value else None
            )
            if masked_path is None or not masked_path.is_file():
                raise ValueError(f"Манифест не содержит masked_force: {path.name}")
            if mask_path is None or not mask_path.is_file():
                raise ValueError(f"Манифест не содержит force_mask: {path.name}")
            if (
                _manifest_output_sha256(run_manifest, "masked_force") != file_sha256(masked_path)
                or _manifest_output_sha256(run_manifest, "force_mask") != file_sha256(mask_path)
            ):
                raise ValueError(f"Нарушен hash маскированных сил: {path.name}")
            mask_record = json.loads(mask_path.read_text(encoding="utf-8"))
            if (
                mask_record.get("schema") != "repairmach.machline-force-mask/1.0"
                or mask_record.get("certified_tri_sha256") != geometry_sha256
            ):
                raise ValueError(f"Force mask не соответствует TRI: {path.name}")
            masked_record = json.loads(masked_path.read_text(encoding="utf-8"))
            if masked_record.get("schema") != "repairmach.machline-masked-force/1.0":
                raise ValueError(f"Неизвестная схема masked_force: {path.name}")
            inputs = masked_record.get("inputs", {})
            result = masked_record.get("result", {})
            wind = result.get("masked_wind_axes", {})
            mesh_axes = result.get("masked_mesh_axes", {})
            if (
                inputs.get("tri", {}).get("sha256") != geometry_sha256
                or inputs.get("report", {}).get("sha256") != file_sha256(path)
                or inputs.get("force_mask", {}).get("sha256") != file_sha256(mask_path)
                or result.get("alignment", {}).get("verified") is not True
            ):
                raise ValueError(f"Masked force не связан с report/TRI/mask: {path.name}")
            values = {
                "cd": _finite_or_none(wind.get("cd")),
                "cl": _finite_or_none(wind.get("cl")),
                "cy": _finite_or_none(mesh_axes.get("Cy")),
                "cx": _finite_or_none(mesh_axes.get("Cx")),
                "cz": _finite_or_none(mesh_axes.get("Cz")),
            }
            if any(value is None for value in values.values()):
                raise ValueError(f"Masked force содержит нечисловой коэффициент: {path.name}")
            conditions = run_manifest.get("conditions", {})
            axes = {
                "mach": float(conditions.get("mach")),
                "alpha_deg": float(conditions.get("alpha_deg")),
                **values,
            }
            base_drag_replacement_required = bool(
                force_output.get("base_drag_replacement_required")
            )
            force_output_kind = "masked_thick_body_pressure_wave"
            sources.extend([
                ("machline_masked_force", masked_path),
                ("machline_force_mask", mask_path),
            ])
        if run_manifest is not None:
            declared_geometry_hash = _normalise_sha256(
                run_manifest.get("geometry", {}).get("sha256")
            )
            declared_conditions = run_manifest.get("conditions", {})
            declared_log_hash = _manifest_output_sha256(run_manifest, "log")
            log_value = _manifest_output_value(run_manifest, "log")
            log_path = (
                _resolve_manifest_output(run_manifest_path.parent, log_value)
                if log_value else None
            )
            quality = run_manifest.get("output_quality", {})
            if (
                declared_geometry_hash != geometry_sha256
                or _finite_or_none(declared_conditions.get("mach")) is None
                or abs(float(declared_conditions["mach"]) - axes["mach"]) > 1.0e-8
                or _finite_or_none(declared_conditions.get("alpha_deg")) is None
                or abs(float(declared_conditions["alpha_deg"]) - axes["alpha_deg"]) > 1.0e-6
                or log_path is None
                or not log_path.is_file()
                or declared_log_hash != file_sha256(log_path)
                or int(quality.get("solver_status_code", -1)) != status_code
                or _finite_or_none(quality.get("residual_norm")) is None
                or abs(float(quality["residual_norm"]) - residual_norm) > 1.0e-12
                or _finite_or_none(quality.get("residual_max")) is None
                or abs(float(quality["residual_max"]) - residual_max) > 1.0e-12
            ):
                raise ValueError(
                    f"Манифест MachLine не соответствует отчёту или TRI: {path.name}"
                )
        points.append(
            {
                **axes,
                "source": path.name,
                "path": path,
                "geometry": geometry,
                "geometry_name": Path(geometry).name,
                "geometry_path": str(geometry_path) if geometry_path is not None else None,
                "geometry_sha256": geometry_sha256,
                "wake_panels": int(report.get("mesh_info", {}).get("N_wake_panels", 0)),
                "residual_norm": residual_norm,
                "residual_max": residual_max,
                "run_manifest": str(run_manifest_path.resolve()) if run_manifest else None,
                "output_hash_bound": output_hash_bound,
                "producer_quality_valid": producer_quality_valid,
                "producer_status": producer_status,
                "solver_sha256": solver_sha256,
                "force_output_kind": force_output_kind,
                "base_drag_replacement_required": base_drag_replacement_required,
            }
        )
        sources.append(("machline_report", path))
    return _deduplicate_machline(points, policy), sources


def _resolve_report_geometry(report_path: Path, value: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    declared = Path(text)
    if declared.is_absolute():
        candidate = declared
    else:
        candidate = report_path.parent / declared
    return candidate.resolve() if candidate.is_file() else None


def _deduplicate_machline(points: list[dict], policy: dict) -> list[dict]:
    tolerance = policy["matching"]
    selected = []
    for point in points:
        matches = [
            item for item in selected
            if abs(item["mach"] - point["mach"]) <= float(tolerance["mach_tolerance"])
            and abs(item["alpha_deg"] - point["alpha_deg"]) <= float(tolerance["alpha_tolerance"])
        ]
        if not matches:
            selected.append(point)
            continue
        current = matches[0]
        if current["geometry_name"].lower() != point["geometry_name"].lower():
            raise ValueError(
                "Для одной точки найдены разные геометрии MachLine: "
                f"{current['geometry_name']} и {point['geometry_name']}"
            )
        if _ml_quality(point) < _ml_quality(current):
            selected[selected.index(current)] = point
    return selected


def _machline_lineage(points: list[dict]) -> dict:
    reports = []
    for point in points:
        report_path = Path(point["path"]).resolve()
        reports.append({
            "file_name": report_path.name,
            "path": str(report_path),
            "sha256": file_sha256(report_path),
            "Mach": float(point["mach"]),
            "alpha_deg": float(point["alpha_deg"]),
            "geometry_declared": point.get("geometry"),
            "geometry_path": point.get("geometry_path"),
            "geometry_sha256": point.get("geometry_sha256"),
            "run_manifest": point.get("run_manifest"),
            "output_hash_bound": point.get("output_hash_bound"),
            "producer_quality_valid": point.get("producer_quality_valid"),
            "producer_status": point.get("producer_status"),
            "solver_sha256": point.get("solver_sha256"),
        })
    return {
        "sealed": bool(reports and all(
            item.get("geometry_sha256")
            and item.get("run_manifest")
            and item.get("output_hash_bound")
            and item.get("producer_quality_valid")
            and item.get("producer_status") == "completed"
            and item.get("solver_sha256")
            for item in reports
        )),
        "reports": reports,
        "geometry_sha256s": sorted({
            item["geometry_sha256"] for item in reports if item.get("geometry_sha256")
        }),
    }


def _ml_quality(point: dict) -> tuple[float, float]:
    return (
        point["residual_norm"] if point["residual_norm"] is not None else math.inf,
        point["residual_max"] if point["residual_max"] is not None else math.inf,
    )


_SCRIPT_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _parasite_manifest_fingerprint_valid(payload: dict) -> bool:
    """Verify the producer seal before trusting any manifest declaration."""
    if not isinstance(payload, dict):
        return False
    expected = _normalise_sha256(payload.get("record_fingerprint"))
    if expected is None:
        return False
    unsigned = dict(payload)
    unsigned.pop("record_fingerprint", None)
    return sha256_payload(unsigned) == expected


def _one_script_match(text: str, pattern: str, label: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(
            f"Сценарий Parasite Drag должен однозначно задавать {label}; найдено {len(matches)}"
        )
    return str(matches[0])


def _parse_parasite_script_binding(script_path: Path) -> dict:
    """Read the actual generated script instead of trusting manifest labels."""
    text = Path(script_path).read_text(encoding="utf-8", errors="strict")
    return {
        "solver_vsp3": _one_script_match(
            text,
            r'ReadVSPFile\(\s*"([^"]+)"\s*\)\s*;',
            "ReadVSPFile",
        ),
        "results_csv": _one_script_match(
            text,
            r'WriteResultsCSVFile\(\s*result_id\s*,\s*"([^"]+)"\s*\)\s*;',
            "WriteResultsCSVFile",
        ),
        "mach": float(_one_script_match(
            text,
            rf"speed\[0\]\s*=\s*({_SCRIPT_FLOAT})\s*;",
            "Mach",
        )),
        "reference_area": float(_one_script_match(
            text,
            rf"sref\[0\]\s*=\s*({_SCRIPT_FLOAT})\s*;",
            "Sref",
        )),
        "geometry_set": int(_one_script_match(
            text,
            r"geom_set\[0\]\s*=\s*(-?\d+)\s*;",
            "OpenVSP GeomSet",
        )),
    }


def _same_resolved_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
        str(Path(right).resolve())
    )


def _required_manifest_file(
    manifest: Path,
    value,
    *,
    label: str,
) -> Path:
    if not str(value or "").strip():
        raise ValueError(f"Манифест Parasite Drag не содержит {label}")
    path = _resolve_manifest_output(manifest.parent, value)
    if not path.is_file():
        raise FileNotFoundError(f"Не найден {label} из манифеста Parasite Drag: {path}")
    return path


def _require_declared_file_hash(
    manifest_payload: dict,
    path: Path,
    role: str,
) -> str:
    expected = _manifest_output_sha256(manifest_payload, role)
    if expected is None:
        raise ValueError(f"Манифест Parasite Drag не содержит SHA-256 {role}")
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"Parasite Drag {role}: SHA-256 файла не совпадает с манифестом")
    return actual


def _load_parasite_points(
    paths: list[Path],
    tolerance: float,
) -> tuple[list[dict], list[tuple[str, Path]], dict]:
    points = []
    sources = []
    lineage_items = []
    for requested_path in paths:
        requested_path = Path(requested_path).resolve()
        manifest = None
        manifest_payload = None
        csv_path = requested_path
        script_path = None
        log_path = None
        source_vsp3_path = None
        solver_vsp3_path = None
        executable_path = None
        script_binding = None
        actual_source_hash = None
        actual_solver_hash = None
        actual_executable_hash = None
        actual_output_hashes: dict[str, str] = {}
        manifest_fingerprint_valid = False
        if requested_path.suffix.lower() == ".json":
            manifest = requested_path
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            if manifest_payload.get("schema") != PARASITE_MANIFEST_SCHEMA:
                raise ValueError(
                    f"Неизвестная схема манифеста Parasite Drag: {manifest}"
                )
            manifest_fingerprint_valid = _parasite_manifest_fingerprint_valid(
                manifest_payload
            )
            if not manifest_fingerprint_valid:
                raise ValueError(
                    f"Нарушена печать record_fingerprint манифеста Parasite Drag: {manifest}"
                )
            if manifest_payload.get("status") != "completed":
                raise ValueError(f"Parasite Drag producer не завершён: {manifest}")
            if not _manifest_output_quality_valid(manifest_payload):
                raise ValueError(f"Parasite Drag producer не подтвердил качество: {manifest}")

            csv_path = _required_manifest_file(
                manifest,
                _manifest_output_value(manifest_payload, "results_csv"),
                label="results_csv",
            )
            script_path = _required_manifest_file(
                manifest,
                _manifest_output_value(manifest_payload, "script"),
                label="script",
            )
            log_path = _required_manifest_file(
                manifest,
                _manifest_output_value(manifest_payload, "log"),
                label="log",
            )
            for role, path in (
                ("results_csv", csv_path),
                ("script", script_path),
                ("log", log_path),
            ):
                actual_output_hashes[role] = _require_declared_file_hash(
                    manifest_payload, path, role
                )

            solver_record = manifest_payload.get("solver_geometry")
            if not isinstance(solver_record, dict):
                raise ValueError("Манифест Parasite Drag не содержит solver_geometry")
            solver_vsp3_path = _required_manifest_file(
                manifest,
                solver_record.get("path"),
                label="фактический solver VSP3",
            )
            actual_solver_hash = file_sha256(solver_vsp3_path)
            declared_solver_hash = _manifest_solver_geometry_sha256(manifest_payload)
            nested_solver_hash = _normalise_sha256(solver_record.get("sha256"))
            if (
                declared_solver_hash is None
                or nested_solver_hash is None
                or declared_solver_hash != nested_solver_hash
                or actual_solver_hash != declared_solver_hash
            ):
                raise ValueError(
                    "Parasite Drag solver VSP3: фактический SHA-256 не совпадает с манифестом"
                )

            source_vsp3_path = _required_manifest_file(
                manifest,
                manifest_payload.get("source_vsp3"),
                label="исходный MASTER VSP3",
            )
            actual_source_hash = file_sha256(source_vsp3_path)
            declared_source_hash = _manifest_master_geometry_sha256(manifest_payload)
            if declared_source_hash is None or actual_source_hash != declared_source_hash:
                raise ValueError(
                    "Parasite Drag MASTER VSP3: фактический SHA-256 не совпадает с манифестом"
                )

            executable_record = manifest_payload.get("executables", {}).get("openvsp")
            if not isinstance(executable_record, dict):
                raise ValueError("Манифест Parasite Drag не содержит executable OpenVSP")
            executable_path = _required_manifest_file(
                manifest,
                executable_record.get("path"),
                label="vspscript.exe",
            )
            actual_executable_hash = file_sha256(executable_path)
            declared_executable_hash = _manifest_executable_sha256(
                manifest_payload, "openvsp"
            )
            if (
                declared_executable_hash is None
                or actual_executable_hash != declared_executable_hash
            ):
                raise ValueError(
                    "Parasite Drag vspscript.exe: фактический SHA-256 не совпадает с манифестом"
                )

            script_binding = _parse_parasite_script_binding(script_path)
            script_solver_path = _resolve_manifest_output(
                script_path.parent, script_binding["solver_vsp3"]
            )
            if not script_solver_path.is_file() or not _same_resolved_path(
                script_solver_path, solver_vsp3_path
            ):
                raise ValueError(
                    "Parasite Drag: модель ReadVSPFile в script не совпадает с фактическим solver VSP3"
                )
            script_csv_path = _resolve_manifest_output(
                script_path.parent, script_binding["results_csv"]
            )
            if not script_csv_path.is_file() or not _same_resolved_path(
                script_csv_path, csv_path
            ):
                raise ValueError(
                    "Parasite Drag: results CSV в script не совпадает с файлом манифеста"
                )
            sources.append(("openvsp_parasite_manifest", manifest))
        parsed = parse_openvsp_results_csv(csv_path, strict=True)
        if parsed["mach"] is None:
            continue
        source_hash = (
            _manifest_master_geometry_sha256(manifest_payload)
            if manifest_payload is not None else None
        )
        solver_hash = _manifest_solver_geometry_sha256(manifest_payload or {})
        executable_hash = _manifest_executable_sha256(
            manifest_payload or {}, "openvsp"
        )
        geometry_mode = _manifest_geometry_mode(manifest_payload or {})
        declared_mach = None
        declared_reference_area = None
        declared_geometry_set = None
        if manifest_payload is not None:
            declared_conditions = manifest_payload.get("conditions", {})
            if isinstance(declared_conditions, dict):
                declared_mach = _finite_or_none(declared_conditions.get("mach"))
                declared_reference_area = _finite_or_none(
                    declared_conditions.get("reference_area")
                )
                try:
                    declared_geometry_set = int(declared_conditions.get("geometry_set"))
                except (TypeError, ValueError):
                    declared_geometry_set = None
        parsed_reference_area = _finite_or_none(parsed.get("reference_area"))
        condition_bound = bool(
            declared_mach is not None
            and abs(declared_mach - float(parsed["mach"])) <= tolerance
            and declared_reference_area is not None
            and declared_reference_area > 0.0
            and parsed_reference_area is not None
            and abs(parsed_reference_area - declared_reference_area)
            <= 1.0e-9 * max(1.0, abs(declared_reference_area))
            and declared_geometry_set is not None
        )
        actual_output_hash = file_sha256(csv_path)
        declared_output_hash = _manifest_output_sha256(
            manifest_payload or {}, "results_csv"
        )
        output_hash_bound = bool(
            declared_output_hash and declared_output_hash == actual_output_hash
        )
        source_label = manifest.name if manifest is not None else csv_path.name
        point = {
            "mach": float(parsed["mach"]),
            "total_cd": float(parsed["total_cd"]),
            "components": parsed["components"],
            "source": source_label,
            "path": manifest or csv_path,
            "results_csv": str(csv_path.resolve()),
        }
        matches = [item for item in points if abs(item["mach"] - point["mach"]) <= tolerance]
        if matches:
            current = matches[0]
            if abs(current["total_cd"] - point["total_cd"]) > 1.0e-10:
                raise ValueError(f"Неоднозначные Parasite Drag CSV для M={point['mach']:g}")
            # If both the CSV and its manifest were supplied, retain the
            # manifest-backed declaration because only it can be sealed.
            if manifest is None or Path(current["path"]).suffix.lower() == ".json":
                continue
            points.remove(current)
            lineage_items = [
                item for item in lineage_items
                if abs(float(item["Mach"]) - point["mach"]) > tolerance
            ]
        points.append(point)
        sources.append(("openvsp_parasite_drag", csv_path))
        lineage_items.append({
            "source_kind": "manifest" if manifest is not None else "direct_csv",
            "manifest": (
                {
                    "file_name": manifest.name,
                    "path": str(manifest.resolve()),
                    "sha256": file_sha256(manifest),
                    "schema": manifest_payload.get("schema"),
                }
                if manifest is not None else None
            ),
            "results_csv": {
                "file_name": csv_path.name,
                "path": str(csv_path.resolve()),
                "sha256": actual_output_hash,
                "declared_output_sha256": declared_output_hash,
                "output_hash_bound": output_hash_bound,
            },
            "Mach": float(parsed["mach"]),
            "source_vsp3_sha256": source_hash,
            "solver_geometry_sha256": solver_hash,
            "executable_sha256": executable_hash,
            "actual_source_vsp3": (
                str(source_vsp3_path.resolve()) if source_vsp3_path else None
            ),
            "actual_source_vsp3_sha256": actual_source_hash,
            "actual_solver_geometry": (
                str(solver_vsp3_path.resolve()) if solver_vsp3_path else None
            ),
            "actual_solver_geometry_sha256": actual_solver_hash,
            "actual_executable": (
                str(executable_path.resolve()) if executable_path else None
            ),
            "actual_executable_sha256": actual_executable_hash,
            "script": (
                {
                    "path": str(script_path.resolve()),
                    "sha256": actual_output_hashes.get("script"),
                }
                if script_path else None
            ),
            "log": (
                {
                    "path": str(log_path.resolve()),
                    "sha256": actual_output_hashes.get("log"),
                }
                if log_path else None
            ),
            "script_binding": script_binding,
            "manifest_fingerprint_valid": manifest_fingerprint_valid,
            "geometry_mode": geometry_mode,
            "condition_bound": bool(
                condition_bound
                and script_binding is not None
                and abs(float(script_binding["mach"]) - float(parsed["mach"])) <= tolerance
                and abs(float(script_binding["mach"]) - float(declared_mach)) <= tolerance
                and abs(float(script_binding["reference_area"]) - float(parsed_reference_area))
                <= 1.0e-9 * max(1.0, abs(float(script_binding["reference_area"])))
                and abs(float(script_binding["reference_area"]) - float(declared_reference_area))
                <= 1.0e-9 * max(1.0, abs(float(script_binding["reference_area"])))
                and int(script_binding["geometry_set"]) == declared_geometry_set
            ),
            "reference_area": declared_reference_area,
            "reported_reference_area": parsed_reference_area,
            "geometry_set": declared_geometry_set,
            "script_reference_area": (
                float(script_binding["reference_area"]) if script_binding else None
            ),
            "script_geometry_set": (
                int(script_binding["geometry_set"]) if script_binding else None
            ),
            "output_quality_valid": _manifest_output_quality_valid(
                manifest_payload or {}
            ),
            "producer_status": (
                manifest_payload.get("status") if manifest_payload is not None else None
            ),
        })
    lineage = {
        "sealed": bool(
            lineage_items
            and all(
                item.get("source_kind") == "manifest"
                and item.get("manifest_fingerprint_valid")
                and item.get("source_vsp3_sha256")
                and item.get("solver_geometry_sha256")
                and item.get("actual_source_vsp3_sha256")
                and item.get("actual_solver_geometry_sha256")
                and item.get("actual_executable_sha256")
                and item.get("geometry_mode")
                and item.get("condition_bound")
                and item.get("results_csv", {}).get("output_hash_bound")
                and item.get("script", {}).get("sha256")
                and item.get("log", {}).get("sha256")
                and item.get("output_quality_valid")
                and item.get("producer_status") == "completed"
                for item in lineage_items
            )
        ),
        "points": lineage_items,
    }
    return points, sources, lineage


def _build_cy_alpha(
    *,
    full_rows: list[dict],
    thin_vspaero_source: Path | None,
    policy: dict,
) -> tuple[list[dict], list[str], list[tuple[str, Path]], dict | None]:
    errors = []
    lift = policy["lift"]
    definition = lift.get("cy_alpha", {})
    alpha_start = float(definition.get("alpha_start_deg", 0.0))
    alpha_end = float(definition.get("alpha_end_deg", 1.0))
    gap = tuple(float(value) for value in policy.get("transonic_excluded", TRANSONIC_DEFAULT))
    full_rows = [
        row for row in full_rows
        if not gap[0] < float(row["Mach"]) < gap[1]
    ]
    try:
        direct = cy_alpha_per_degree(
            full_rows,
            alpha_start_deg=alpha_start,
            alpha_step_deg=alpha_end - alpha_start,
            tolerance=float(policy["matching"]["alpha_tolerance"]),
        )
    except ValueError as exc:
        return [], [f"Cyα: {exc}"], [], None

    method = definition.get("method", "direct")
    if method == "direct":
        return [
            {
                **point,
                "Cy_alpha_direct_per_deg": point["Cy_alpha_per_deg"],
                "Cy_alpha_final_per_deg": point["Cy_alpha_per_deg"],
                "method_version": "direct_VSPAERO",
                "status": "complete",
            }
            for point in direct
        ], [], [], None

    if thin_vspaero_source is None:
        return [], ["Cyα RM92.1: не указан thin_vspaero_source"], [], None
    thin_rows, thin_sources, thin_lineage = _load_vspaero_source(
        Path(thin_vspaero_source),
        float(policy["matching"]["mach_tolerance"]),
        float(policy["matching"]["alpha_tolerance"]),
    )
    thin_rows = [
        row for row in thin_rows
        if not gap[0] < float(row["Mach"]) < gap[1]
    ]
    try:
        thin_points = cy_alpha_per_degree(
            thin_rows,
            alpha_start_deg=alpha_start,
            alpha_step_deg=alpha_end - alpha_start,
            tolerance=float(policy["matching"]["alpha_tolerance"]),
        )
    except ValueError as exc:
        return [], [f"Cyα RM92.1: {exc}"], thin_sources, thin_lineage
    geometry = definition.get("wing_geometry", {})
    try:
        aspect_ratio = float(geometry["aspect_ratio"])
        sweep = float(geometry["leading_edge_sweep_deg"])
    except (KeyError, TypeError, ValueError) as exc:
        return [], ["Cyα RM92.1: не заданы aspect_ratio и leading_edge_sweep_deg"], thin_sources, thin_lineage

    thin_by_mach = {round(float(point["Mach"]), 9): point for point in thin_points}
    result = []
    for point in direct:
        mach = float(point["Mach"])
        thin = thin_by_mach.get(round(mach, 9))
        if thin is None:
            errors.append(f"Cyα RM92.1: нет тонкостенной точки M={mach:g}")
            continue
        try:
            hybrid = hybrid_cy_alpha_rm921_point(
                mach=mach,
                full_vspaero=float(point["Cy_alpha_per_deg"]),
                thin_all_vspaero=float(thin["Cy_alpha_per_deg"]),
                aspect_ratio=aspect_ratio,
                leading_edge_sweep_deg=sweep,
                normal_mach_transition_width=float(definition.get("normal_mach_transition_width", 0.35)),
                supersonic_efficiency=float(definition.get("supersonic_efficiency", 0.93)),
                residual_limit_ratio=float(definition.get("residual_limit_ratio", 0.05)),
            )
        except ValueError as exc:
            errors.append(f"Cyα RM92.1 M={mach:g}: {exc}")
            continue
        result.append(
            {
                **point,
                "Cy_alpha_direct_per_deg": point["Cy_alpha_per_deg"],
                "Cy_alpha_final_per_deg": hybrid["Cy_alpha_per_deg"],
                "method_version": hybrid["method_version"],
                "status": "complete",
                "hybrid_details": hybrid,
            }
        )
    return result, errors, thin_sources, thin_lineage


def _match_condition(points: list[dict], mach: float, alpha: float, mach_tol: float, alpha_tol: float) -> dict | None:
    matches = [
        point for point in points
        if abs(point["mach"] - mach) <= mach_tol and abs(point["alpha_deg"] - alpha) <= alpha_tol
    ]
    if len(matches) > 1:
        raise ValueError(f"Неоднозначная точка MachLine M={mach:g}, alpha={alpha:g}")
    return matches[0] if matches else None


def _match_mach(points: list[dict], mach: float, tolerance: float) -> dict | None:
    matches = [point for point in points if abs(point["mach"] - mach) <= tolerance]
    if len(matches) > 1:
        raise ValueError(f"Неоднозначная точка Parasite Drag M={mach:g}")
    return matches[0] if matches else None


def _linear_table_value(points: list[tuple[float, float]], value: float) -> float | None:
    for mach, result in points:
        if abs(value - mach) <= 1.0e-12:
            return result
    for (m1, v1), (m2, v2) in zip(points, points[1:]):
        if m1 < value < m2:
            return v1 + (value - m1) * (v2 - v1) / (m2 - m1)
    return None


def _unique_source_records(sources: list[tuple[str, Path]]) -> list[dict]:
    result = []
    seen = set()
    for role, path in sources:
        path = Path(path).resolve()
        key = (role, str(path).lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "role": role,
                "file_name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return result


def _normalise_sha256(value) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        return None
    return text


def _manifest_master_geometry_sha256(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    geometry_cert = payload.get("geometry_certification", {})
    backend_binding = payload.get("backend_binding", {})
    values = [
        payload.get("master_vsp3_sha256"),
        geometry_cert.get("master_sha256") if isinstance(geometry_cert, dict) else None,
        backend_binding.get("master_sha256") if isinstance(backend_binding, dict) else None,
        # Compatibility for manifests in which source_vsp3 was the MASTER.
        payload.get("source_vsp3_sha256"),
    ]
    return next((value for value in (_normalise_sha256(item) for item in values) if value), None)


def _manifest_solver_geometry_sha256(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    geometry_cert = payload.get("geometry_certification", {})
    backend_binding = payload.get("backend_binding", {})
    solver_geometry = payload.get("solver_geometry", {})
    values = [
        payload.get("solver_geometry_sha256"),
        payload.get("working_vsp3_sha256"),
        payload.get("certified_twin_sha256"),
        geometry_cert.get("solver_geometry_sha256") if isinstance(geometry_cert, dict) else None,
        geometry_cert.get("certified_twin_sha256") if isinstance(geometry_cert, dict) else None,
        backend_binding.get("solver_geometry_sha256") if isinstance(backend_binding, dict) else None,
        solver_geometry.get("sha256") if isinstance(solver_geometry, dict) else None,
    ]
    return next((value for value in (_normalise_sha256(item) for item in values) if value), None)


def _manifest_output_sha256(payload: dict, role: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = []
    for key in ("output_sha256", "outputs_sha256"):
        values = payload.get(key, {})
        if isinstance(values, dict):
            candidates.append(values.get(role))
    outputs = payload.get("outputs", {})
    if isinstance(outputs, dict):
        item = outputs.get(role)
        if isinstance(item, dict):
            candidates.extend((item.get("sha256"), item.get("hash")))
    return next(
        (value for value in (_normalise_sha256(item) for item in candidates) if value),
        None,
    )


def _manifest_scenario_sha256(payload: dict) -> str | None:
    """Return the immutable calculation-scenario fingerprint from a manifest."""
    if not isinstance(payload, dict):
        return None
    geometry_certificate = payload.get("geometry_certificate", {})
    certificate_binding = payload.get("certificate_binding", {})
    candidates = [
        payload.get("scenario_sha256"),
        geometry_certificate.get("scenario_sha256")
        if isinstance(geometry_certificate, dict)
        else None,
        certificate_binding.get("scenario_sha256")
        if isinstance(certificate_binding, dict)
        else None,
    ]
    return next(
        (value for value in (_normalise_sha256(item) for item in candidates) if value),
        None,
    )


_VSPAERO_SCRIPT_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_VSPAERO_SCRIPT_INTEGER = r"[+-]?\d+"


def _parse_vspaero_script_controls(text: str) -> dict:
    """Read the four solver controls emitted by ``build_vspaero_script``."""
    patterns = {
        "forward_gmres_convergence_factor": (
            rf"array\s*<\s*double\s*>\s+gmres_factor\s*\(\s*1\s*,\s*({_VSPAERO_SCRIPT_NUMBER})\s*\)",
            float,
        ),
        "wake_num_iter": (
            rf"array\s*<\s*int\s*>\s+wake_iterations\s*\(\s*1\s*,\s*({_VSPAERO_SCRIPT_INTEGER})\s*\)",
            int,
        ),
        "num_wake_nodes": (
            rf"array\s*<\s*int\s*>\s+wake_nodes\s*\(\s*1\s*,\s*({_VSPAERO_SCRIPT_INTEGER})\s*\)",
            int,
        ),
        "wake_relax": (
            rf"array\s*<\s*double\s*>\s+wake_relaxation\s*\(\s*1\s*,\s*({_VSPAERO_SCRIPT_NUMBER})\s*\)",
            float,
        ),
    }
    controls = {}
    for key, (pattern, converter) in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            return {}
        try:
            number = float(match.group(1))
            controls[key] = converter(number)
        except (TypeError, ValueError, OverflowError):
            return {}
    return controls


def _vspaero_controls_equal(actual, declared) -> bool:
    if not isinstance(actual, dict) or not isinstance(declared, dict):
        return False
    integer_keys = ("wake_num_iter", "num_wake_nodes")
    float_keys = ("forward_gmres_convergence_factor", "wake_relax")
    for key in integer_keys:
        actual_value = _finite_or_none(actual.get(key))
        declared_value = _finite_or_none(declared.get(key))
        if (
            actual_value is None
            or declared_value is None
            or not actual_value.is_integer()
            or not declared_value.is_integer()
            or int(actual_value) != int(declared_value)
        ):
            return False
    for key in float_keys:
        actual_value = _finite_or_none(actual.get(key))
        declared_value = _finite_or_none(declared.get(key))
        if actual_value is None or declared_value is None:
            return False
        if abs(actual_value - declared_value) > 1.0e-12 * max(
            1.0, abs(actual_value), abs(declared_value)
        ):
            return False
    return True


def _manifest_executable_sha256(payload: dict, role: str) -> str | None:
    """Return the producer-recorded binary fingerprint for a solver role."""
    if not isinstance(payload, dict):
        return None
    values = payload.get("executables", {})
    item = values.get(role) if isinstance(values, dict) else None
    candidates = []
    if isinstance(item, dict):
        candidates.extend((item.get("sha256"), item.get("hash")))
    elif item is not None:
        candidates.append(item)
    return next(
        (value for value in (_normalise_sha256(candidate) for candidate in candidates) if value),
        None,
    )


def _manifest_output_value(payload: dict, role: str):
    if not isinstance(payload, dict):
        return None
    outputs = payload.get("outputs", {})
    if not isinstance(outputs, dict):
        return None
    value = outputs.get(role)
    if isinstance(value, dict):
        return value.get("path") or value.get("file")
    return value


def _manifest_output_quality_valid(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    quality = payload.get("output_quality")
    return isinstance(quality, dict) and quality.get("valid") is True


def _manifest_geometry_mode(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    geometry_cert = payload.get("geometry_certification", {})
    backend_binding = payload.get("backend_binding", {})
    values = [
        payload.get("geometry_mode"),
        payload.get("vspaero_mode"),
        geometry_cert.get("selected_mode") if isinstance(geometry_cert, dict) else None,
        geometry_cert.get("mode") if isinstance(geometry_cert, dict) else None,
        backend_binding.get("mode") if isinstance(backend_binding, dict) else None,
    ]
    return next((str(item).strip() for item in values if str(item or "").strip()), None)


def _manifest_vspaero_setup(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"complete": False}
    conditions = payload.get("conditions", {})
    if not isinstance(conditions, dict):
        return {"complete": False}
    beta_present = "beta_deg" in conditions
    beta = _finite_or_none(conditions.get("beta_deg")) if beta_present else None
    horizontal_tail = conditions.get("horizontal_tail")
    tail_complete = isinstance(horizontal_tail, dict) and bool(
        str(horizontal_tail.get("mode", "")).strip()
    )
    if tail_complete and str(horizontal_tail.get("mode", "")).strip().lower() in {
        "fixed", "fixed_incidence"
    }:
        tail_complete = bool(str(horizontal_tail.get("geometry_name", "")).strip()) and (
            _finite_or_none(horizontal_tail.get("incidence_deg")) is not None
        )
    engine_present = "engine_boundary" in payload or "engine_boundary" in conditions
    engine_boundary = (
        payload.get("engine_boundary")
        if "engine_boundary" in payload
        else conditions.get("engine_boundary")
    )
    reference = payload.get("reference")
    reference_complete = _reference_setup_valid(reference)
    return {
        "complete": bool(
            beta_present and beta is not None and tail_complete and engine_present
            and reference_complete
        ),
        "beta_deg": beta,
        "horizontal_tail": horizontal_tail if isinstance(horizontal_tail, dict) else None,
        "engine_boundary": engine_boundary,
        "reference": reference if isinstance(reference, dict) else None,
    }


def _validate_certified_vspaero_lineage(
    lineage: dict | None,
    certificate: dict,
    *,
    label: str,
    expected_scenario_id: str,
    expected_scenario_sha256: str,
) -> dict:
    """Require VSPAERO results from the exact certified solver twin and setup."""
    if not isinstance(lineage, dict) or not lineage.get("sealed", False):
        raise ValueError(
            f"{label}: прямой или незапечатанный POLAR запрещён при приложенном "
            "сертификате; JSON-манифест должен хранить MASTER, solver twin, "
            "geometry mode, beta, ГО и engine_boundary"
        )
    expected_master_sha256 = _normalise_sha256(
        certificate.get("master", {}).get("sha256_before")
    )
    backend = certificate.get("backends", {}).get("vspaero", {})
    expected_solver_sha256 = _normalise_sha256(
        backend.get("solver_geometry", {}).get("sha256")
    )
    expected_mode = str(backend.get("mode", "")).strip()
    certified_solvers = certificate.get("software", {}).get("solvers", {})
    expected_executable_sha256 = {
        role: _normalise_sha256(
            certified_solvers.get(role, {}).get("sha256")
            if isinstance(certified_solvers.get(role), dict)
            else None
        )
        for role in ("openvsp", "vspaero")
    }
    if expected_master_sha256 is None:
        raise ValueError(f"{label}: сертификат не содержит SHA-256 MASTER")
    if expected_solver_sha256 is None:
        raise ValueError(
            f"{label}: сертификат не содержит backends.vspaero.solver_geometry.sha256"
        )
    if not expected_mode:
        raise ValueError(f"{label}: сертификат не содержит VSPAERO mode")
    if any(value is None for value in expected_executable_sha256.values()):
        raise ValueError(f"{label}: сертификат не фиксирует оба исполняемых файла VSPAERO")
    scenario_item = certificate.get("scenario_eligibility", {}).get(
        expected_scenario_id
    )
    certified_scenario_sha256 = (
        _normalise_sha256(scenario_item.get("scenario_sha256"))
        if isinstance(scenario_item, dict)
        else None
    )
    if (
        not isinstance(scenario_item, dict)
        or scenario_item.get("eligible") is not True
        or certified_scenario_sha256 != expected_scenario_sha256
    ):
        raise ValueError(
            f"{label}: scenario_id/scenario_sha256 не соответствует сертификату"
        )
    qualified_scope = backend.get("qualified_scope") or certificate.get("qualified_scope", {})
    expected_beta = _finite_or_none(qualified_scope.get("beta_deg"))
    if expected_beta is None:
        exact_betas = {
            _finite_or_none(point.get("beta_deg"))
            for point in qualified_scope.get("points", [])
        }
        exact_betas.discard(None)
        expected_beta = next(iter(exact_betas)) if len(exact_betas) == 1 else None
    expected_tail = (
        qualified_scope.get("horizontal_tail")
        or certificate.get("scope", {}).get("horizontal_tail")
    )
    expected_reference = certificate.get("reference")
    if (
        expected_beta is None
        or not isinstance(expected_tail, dict)
        or not _reference_setup_valid(expected_reference)
    ):
        raise ValueError(
            f"{label}: сертификат не фиксирует beta, постановку ГО и опорные величины"
        )
    polar_sources = lineage.get("polar_sources", [])
    if not polar_sources:
        raise ValueError(f"{label}: манифест не содержит прослеживаемых POLAR")
    mismatches = []
    for item in polar_sources:
        if (
            str(item.get("scenario_id", "")).strip() != expected_scenario_id
            or _normalise_sha256(item.get("scenario_sha256"))
            != expected_scenario_sha256
        ):
            mismatches.append({
                "file_name": item.get("file_name"),
                "scenario_id": item.get("scenario_id"),
                "scenario_sha256": item.get("scenario_sha256"),
            })
            continue
        actual_executables = item.get("executable_sha256", {})
        if any(
            _normalise_sha256(actual_executables.get(role)) != expected
            for role, expected in expected_executable_sha256.items()
        ):
            mismatches.append({
                "file_name": item.get("file_name"),
                "executable_sha256": actual_executables,
            })
            continue
        actual = _normalise_sha256(item.get("source_vsp3_sha256"))
        if actual != expected_master_sha256:
            mismatches.append({
                "file_name": item.get("file_name"),
                "source_vsp3_sha256": actual,
            })
            continue
        if _normalise_sha256(item.get("solver_geometry_sha256")) != expected_solver_sha256:
            mismatches.append({
                "file_name": item.get("file_name"),
                "solver_geometry_sha256": item.get("solver_geometry_sha256"),
            })
            continue
        if str(item.get("geometry_mode", "")).strip() != expected_mode:
            mismatches.append({
                "file_name": item.get("file_name"),
                "geometry_mode": item.get("geometry_mode"),
            })
            continue
        setup = item.get("setup", {})
        if not setup.get("complete") or abs(float(setup["beta_deg"]) - expected_beta) > 1.0e-9:
            mismatches.append({"file_name": item.get("file_name"), "setup": setup})
            continue
        if not _tail_setups_equal(setup.get("horizontal_tail"), expected_tail):
            mismatches.append({"file_name": item.get("file_name"), "setup": setup})
            continue
        if not _reference_setups_equal(setup.get("reference"), expected_reference):
            mismatches.append({
                "file_name": item.get("file_name"),
                "reference": setup.get("reference"),
            })
            continue
        actual_boundary = _normalise_engine_boundary(setup.get("engine_boundary"))
        expected_boundaries = {
            _qualified_engine_boundary(
                qualified_scope,
                float(point["Mach"]),
                float(point["alpha_deg"]),
            )
            for point in item.get("points", [])
        }
        if not expected_boundaries or None in expected_boundaries or expected_boundaries != {actual_boundary}:
            mismatches.append({
                "file_name": item.get("file_name"),
                "setup": setup,
                "qualified_engine_boundaries": sorted(
                    str(value) for value in expected_boundaries
                ),
            })
    if mismatches:
        names = ", ".join(str(item.get("file_name")) for item in mismatches)
        raise ValueError(
            f"{label}: геометрия или постановка не соответствует сертификату "
            f"для {names}"
        )
    return {
        "valid": True,
        "sealed": True,
        "expected_master_sha256": expected_master_sha256,
        "expected_solver_geometry_sha256": expected_solver_sha256,
        "geometry_mode": expected_mode,
        "beta_deg": expected_beta,
        "horizontal_tail": expected_tail,
        "reference": expected_reference,
        "executable_sha256": expected_executable_sha256,
        "scenario_id": expected_scenario_id,
        "scenario_sha256": expected_scenario_sha256,
        "source_vsp3_sha256s": list(lineage.get("source_vsp3_sha256s", [])),
        "polar_count": len(polar_sources),
        "manifest": lineage.get("manifest"),
    }


def _tail_setups_equal(actual, expected) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    mode_aliases = {"fixed": "fixed", "fixed_incidence": "fixed", "model_default": "model_default"}
    actual_mode = mode_aliases.get(str(actual.get("mode", "")).strip().lower())
    expected_mode = mode_aliases.get(str(expected.get("mode", "")).strip().lower())
    if actual_mode != expected_mode or actual_mode is None:
        return False
    if actual_mode == "fixed":
        if str(actual.get("geometry_name", "")).strip() != str(expected.get("geometry_name", "")).strip():
            return False
        actual_incidence = _finite_or_none(actual.get("incidence_deg"))
        expected_incidence = _finite_or_none(expected.get("incidence_deg"))
        if actual_incidence is None or expected_incidence is None:
            return False
        if abs(actual_incidence - expected_incidence) > 1.0e-9:
            return False
    return True


def _reference_setup_valid(value) -> bool:
    if not isinstance(value, dict):
        return False
    scalars = [_finite_or_none(value.get(key)) for key in ("area", "cref", "bref")]
    center = value.get("center")
    return bool(
        all(item is not None and item > 0.0 for item in scalars)
        and isinstance(center, list)
        and len(center) == 3
        and all(_finite_or_none(item) is not None for item in center)
    )


def _reference_setups_equal(actual, expected) -> bool:
    if not _reference_setup_valid(actual) or not _reference_setup_valid(expected):
        return False
    for key in ("area", "cref", "bref"):
        left, right = float(actual[key]), float(expected[key])
        if abs(left - right) > 1.0e-9 * max(1.0, abs(right)):
            return False
    return all(
        abs(float(left) - float(right)) <= 1.0e-9 * max(1.0, abs(float(right)))
        for left, right in zip(actual["center"], expected["center"])
    )


def _normalise_engine_boundary(value) -> str:
    text = str(value or "model").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "none": "model",
        "default": "model",
        "model_default": "model",
        "toface": "to_face",
    }
    return aliases.get(text, text)


def _qualified_engine_boundary(scope: dict, mach: float, alpha_deg: float) -> str | None:
    tolerance = 1.0e-8
    if scope.get("coverage_kind") == "exact_points":
        values = {
            _normalise_engine_boundary(point.get("engine_boundary"))
            for point in scope.get("points", [])
            if abs(float(point["mach"]) - mach) <= tolerance
            and abs(float(point["alpha_deg"]) - alpha_deg) <= tolerance
        }
        return next(iter(values)) if len(values) == 1 else None
    if scope.get("coverage_kind") == "anchor_envelope":
        alpha_range = scope.get("alpha_deg", [])
        if len(alpha_range) != 2 or not float(alpha_range[0]) <= alpha_deg <= float(alpha_range[1]):
            return None
        values = {
            _normalise_engine_boundary(region.get("engine_boundary"))
            for region in scope.get("boundary_regions", [])
            if float(region["mach_interval"][0]) <= mach <= float(region["mach_interval"][1])
        }
        return next(iter(values)) if len(values) == 1 else None
    return None


def _validate_certified_machline_lineage(
    certificate_path: Path,
    certificate: dict,
    bundle: dict,
) -> dict:
    verification = verify_certificate(certificate_path, backend="machline")
    if not verification["valid"]:
        raise ValueError(
            "MachLine replacement не сертифицирован: " + "; ".join(verification["errors"])
        )
    expected_tri_sha256 = _normalise_sha256(
        certificate.get("tri", {}).get("certified_tri_sha256")
    )
    if expected_tri_sha256 is None:
        raise ValueError("Сертификат MachLine не содержит certified_tri_sha256")
    expected_solver_sha256 = _normalise_sha256(
        certificate.get("software", {}).get("solvers", {}).get("machline", {}).get("sha256")
    )
    if expected_solver_sha256 is None:
        raise ValueError("Сертификат MachLine не содержит SHA-256 исполняемого файла")
    lineage = bundle.get("lineage", {}).get("machline", {})
    if not lineage.get("sealed", False):
        raise ValueError(
            "MachLine lineage не запечатана: каждый report.input.geometry.file "
            "должен разрешаться в существующий TRI"
        )
    reports = list(lineage.get("reports", []))
    matching = bundle.get("policy", {}).get("matching", {})
    mach_tol = float(matching.get("mach_tolerance", 1.0e-6))
    alpha_tol = float(matching.get("alpha_tolerance", 1.0e-6))
    checked = []
    for row in bundle.get("rows", []):
        candidates = [
            item for item in reports
            if item.get("file_name") == row.get("sources", {}).get("machline")
            and abs(float(item["Mach"]) - float(row["Mach"])) <= mach_tol
            and abs(float(item["alpha_deg"]) - float(row["alpha_deg"])) <= alpha_tol
        ]
        if len(candidates) != 1:
            raise ValueError(
                "MachLine lineage не определена однозначно для "
                f"M={row['Mach']:g}, alpha={row['alpha_deg']:g}"
            )
        item = candidates[0]
        if _normalise_sha256(item.get("geometry_sha256")) != expected_tri_sha256:
            raise ValueError(
                "MachLine report использует TRI, не совпадающий с "
                f"сертифицированным: {item.get('file_name')}"
            )
        if _normalise_sha256(item.get("solver_sha256")) != expected_solver_sha256:
            raise ValueError(
                f"MachLine report создан другим исполняемым файлом: {item.get('file_name')}"
            )
        checked.append({
            "report": item.get("file_name"),
            "Mach": item.get("Mach"),
            "alpha_deg": item.get("alpha_deg"),
            "geometry_path": item.get("geometry_path"),
            "geometry_sha256": item.get("geometry_sha256"),
        })
    return {
        "valid": True,
        "backend": verification,
        "certified_tri_sha256": expected_tri_sha256,
        "solver_sha256": expected_solver_sha256,
        "reports": checked,
    }


def _validate_certified_parasite_lineage(
    certificate_path: Path,
    certificate: dict,
    bundle: dict,
    subsonic_rows: list[dict],
) -> dict:
    verification = verify_certificate(certificate_path, backend="parasite_drag")
    if not verification["valid"]:
        raise ValueError(
            "Parasite Drag replacement не сертифицирован: "
            + "; ".join(verification["errors"])
        )
    backend = certificate.get("backends", {}).get("parasite_drag", {})
    expected_solver_sha256 = _normalise_sha256(
        backend.get("solver_geometry", {}).get("sha256")
    )
    if expected_solver_sha256 is None:
        raise ValueError(
            "Сертификат Parasite Drag не содержит backends.parasite_drag.solver_geometry.sha256"
        )
    expected_mode = str(backend.get("mode", "")).strip()
    certified_openvsp = certificate.get("software", {}).get("solvers", {}).get("openvsp")
    expected_executable_sha256 = _normalise_sha256(
        certified_openvsp.get("sha256") if isinstance(certified_openvsp, dict) else None
    )
    expected_reference_area = _finite_or_none(backend.get("reference_area"))
    expected_geometry_set = backend.get("geometry_set")
    if expected_reference_area is None:
        expected_reference_area = _finite_or_none(
            certificate.get("reference", {}).get("area")
        )
    try:
        expected_geometry_set = int(expected_geometry_set)
    except (TypeError, ValueError):
        expected_geometry_set = None
    expected_master_sha256 = _normalise_sha256(
        certificate.get("master", {}).get("sha256_before")
    )
    if expected_reference_area is None or expected_geometry_set is None:
        raise ValueError(
            "Сертификат Parasite Drag не фиксирует reference_area и geometry_set"
        )
    if expected_executable_sha256 is None:
        raise ValueError("Сертификат Parasite Drag не фиксирует исполняемый файл OpenVSP")
    lineage = bundle.get("lineage", {}).get("parasite_drag", {})
    if not lineage.get("sealed", False):
        raise ValueError(
            "Parasite Drag lineage не запечатана: голый CSV запрещён; нужен "
            "JSON-манифест с source_vsp3_sha256, solver geometry и mode"
        )
    points = list(lineage.get("points", []))
    tolerance = float(
        bundle.get("policy", {}).get("matching", {}).get("parasite_mach_tolerance", 1.0e-6)
    )
    checked = []
    for mach in sorted({float(row["Mach"]) for row in subsonic_rows}):
        candidates = [
            item for item in points
            if abs(float(item["Mach"]) - mach) <= tolerance
        ]
        if len(candidates) != 1:
            raise ValueError(f"Parasite Drag lineage не определена однозначно для M={mach:g}")
        item = candidates[0]
        if (
            _normalise_sha256(item.get("actual_executable_sha256"))
            != expected_executable_sha256
        ):
            raise ValueError(
                f"Parasite Drag M={mach:g}: использован несертифицированный vspscript.exe"
            )
        if (
            _normalise_sha256(item.get("actual_source_vsp3_sha256"))
            != expected_master_sha256
        ):
            raise ValueError(
                f"Parasite Drag M={mach:g}: источник не восходит к MASTER сертификата"
            )
        if (
            _normalise_sha256(item.get("actual_solver_geometry_sha256"))
            != expected_solver_sha256
        ):
            raise ValueError(
                f"Parasite Drag M={mach:g}: использован несертифицированный VSP3 twin"
            )
        # Mode is metadata only.  Eligibility is grounded in the actual VSP3
        # bytes above; the declaration must still be internally consistent.
        if str(item.get("geometry_mode", "")).strip() != expected_mode:
            raise ValueError(
                f"Parasite Drag M={mach:g}: mode не соответствует сертификату"
            )
        if (
            _finite_or_none(item.get("script_reference_area")) is None
            or abs(float(item["script_reference_area"]) - expected_reference_area)
            > 1.0e-9 * max(1.0, abs(expected_reference_area))
        ):
            raise ValueError(
                f"Parasite Drag M={mach:g}: Sref не соответствует сертификату"
            )
        if item.get("script_geometry_set") != expected_geometry_set:
            raise ValueError(
                f"Parasite Drag M={mach:g}: OpenVSP Set не соответствует сертификату"
            )
        checked.append(item)
    return {
        "valid": True,
        "backend": verification,
        "solver_geometry_sha256": expected_solver_sha256,
        "mode": expected_mode,
        "reference_area": expected_reference_area,
        "geometry_set": expected_geometry_set,
        "executable_sha256": expected_executable_sha256,
        "points": checked,
    }


def _exclusion_applies_to_vspaero(exclusion: dict) -> bool:
    declared = exclusion.get("backends", exclusion.get("backend"))
    if declared is None:
        return True
    if isinstance(declared, str):
        names = [declared]
    else:
        names = list(declared)
    return any(str(name).strip().lower() in {"vspaero", "hybrid", "all"} for name in names)


def _not_physical_declaration_allowed(exclusion: dict) -> bool:
    declared = exclusion.get("replacement_required", [])
    if isinstance(declared, list) and any(
        str(item).strip().lower() == "not_physical" for item in declared
    ):
        return True
    text = str(declared).strip().lower()
    explicit_phrases = (
        "no additional aerodynamic surface",
        "no additional aero surface",
        "not a physical aerodynamic surface",
        "не является дополнительной аэродинамической поверхностью",
        "дополнительная аэродинамическая поверхность не требуется",
        "нет дополнительной аэродинамической поверхности",
    )
    return any(phrase in text for phrase in explicit_phrases)


def _coverage_methods(item: dict) -> list[str]:
    values = []
    for key in ("satisfied_by", "methods", "method"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    values.append(entry.get("type") or entry.get("method") or entry.get("role"))
                else:
                    values.append(entry)
    aliases = {
        "machline": "machline_pressure_wave",
        "machline_pressure_wave": "machline_pressure_wave",
        "machline_wind_axis_cd": "machline_pressure_wave",
        "pressure_wave": "machline_pressure_wave",
        "parasite": "parasite_drag",
        "parasite_drag": "parasite_drag",
        "openvsp_parasitedrag": "parasite_drag",
        "openvsp_parasite_drag": "parasite_drag",
        "semiempirical_component_pressure_wave_all_points": "semiempirical_component_pressure_wave",
        "semiempirical_component_pressure_wave": "semiempirical_component_pressure_wave",
        "not_physical": "not_physical",
        "reference_only": "not_physical",
        "reference-only": "not_physical",
    }
    result = []
    for value in values:
        key = str(value or "").strip().lower()
        normalised = aliases.get(key, key)
        if normalised and normalised not in result:
            result.append(normalised)
    return result


def _declared_exclusion_methods(exclusion: dict) -> list[str]:
    declared = exclusion.get("replacement_required", [])
    if isinstance(declared, str):
        declared = [declared]
    return [str(item).strip().lower() for item in declared]


def _semiempirical_component_terms(policy: dict, component: str) -> list[dict]:
    result = []
    for term in policy.get("drag", {}).get("semiempirical_terms", []):
        replacement = term.get("replacement")
        if (
            term.get("enabled", True) is True
            and isinstance(replacement, dict)
            and replacement.get("method") == SEMIEMPIRICAL_COMPONENT_REPLACEMENT
            and str(replacement.get("component", "")).casefold() == component.casefold()
        ):
            result.append(term)
    return result


def _validate_semiempirical_component_coverage(
    *,
    component: str,
    entries: list[dict],
    bundle: dict,
) -> list[dict]:
    """Prove one sealed component term is evaluated at every output point."""
    methods = []
    for entry in entries:
        for method in _coverage_methods(entry):
            if method not in methods:
                methods.append(method)
    if methods != ["semiempirical_component_pressure_wave"]:
        raise ValueError(
            f"replacement_coverage {component}: полуэмпирический pressure/wave-ряд нельзя "
            "смешивать с другими методами"
        )

    terms = _semiempirical_component_terms(bundle.get("policy", {}), component)
    if len(terms) != 1:
        raise ValueError(
            f"replacement_coverage {component}: требуется ровно один активный "
            "покомпонентный полуэмпирический член, найдено {len(terms)}"
        )
    term = terms[0]
    term_id = str(term["id"])
    term_fingerprint = sha256_payload(term)
    passport = term["certification"]
    policy_lineage = bundle.get("lineage", {}).get("hybrid_policy")
    if not isinstance(policy_lineage, dict):
        raise ValueError(
            f"replacement_coverage {component}: файл гибридной политики не запечатан"
        )
    policy_path = Path(str(policy_lineage.get("path", "")))
    policy_sha256 = _normalise_sha256(policy_lineage.get("sha256"))
    if (
        policy_sha256 is None
        or not policy_path.is_file()
        or file_sha256(policy_path) != policy_sha256
        or policy_lineage.get("policy_fingerprint") != sha256_payload(bundle.get("policy", {}))
    ):
        raise ValueError(
            f"replacement_coverage {component}: нарушена целостность гибридной политики"
        )

    for entry in entries:
        declared_id = entry.get("term_id")
        declared_fingerprint = _normalise_sha256(entry.get("term_fingerprint"))
        if declared_id is not None and str(declared_id) != term_id:
            raise ValueError(
                f"replacement_coverage {component}: term_id не совпадает с активной методикой"
            )
        if entry.get("term_fingerprint") is not None and declared_fingerprint != term_fingerprint:
            raise ValueError(
                f"replacement_coverage {component}: term_fingerprint не совпадает"
            )

    rows = list(bundle.get("rows", []))
    if not rows:
        raise ValueError(
            f"replacement_coverage {component}: гибридный ряд не содержит расчётных точек"
        )
    result = []
    for row in rows:
        matches = [
            item for item in row.get("semiempirical_terms", [])
            if item.get("id") == term_id
            and item.get("term_fingerprint") == term_fingerprint
            and isinstance(item.get("replacement"), dict)
            and str(item["replacement"].get("component", "")).casefold()
            == component.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"replacement_coverage {component}: покомпонентный член не покрывает "
                f"M={row['Mach']:g}, alpha={row['alpha_deg']:g}"
            )
        contribution = matches[0]
        result.append({
            "component": component,
            "classification": "physical_component",
            "satisfied_by": "semiempirical_component_pressure_wave",
            "coverage_channel": "pressure_wave",
            "alpha_dependence": "independent",
            "Mach": float(row["Mach"]),
            "alpha_deg": float(row["alpha_deg"]),
            "cd": float(contribution["cd"]),
            "uncertainty_cd": float(contribution["uncertainty_cd"]),
            "term_id": term_id,
            "term_fingerprint": term_fingerprint,
            "method_id": passport["method_id"],
            "equation_version": passport["equation_version"],
            "source_path": str(policy_path.resolve()),
            "source_sha256": policy_sha256,
        })
    return result


def _validate_parasite_component_coverage(
    *,
    component: str,
    evidence: list[dict],
    source_records: list[dict],
    subsonic_rows: list[dict],
    bundle: dict,
    base: Path,
    certificate: dict,
    certificate_path: Path,
) -> tuple[list[dict], dict]:
    parasite_components = _certified_component_coverage(
        certificate,
        backend="parasite_drag",
    )
    if component.casefold() not in parasite_components:
        raise ValueError(
            f"replacement_coverage {component}: сертифицированный "
            "Parasite Drag не содержит этот компонент"
        )
    if any(not row.get("sources", {}).get("parasite_drag") for row in subsonic_rows):
        raise ValueError(
            f"replacement_coverage {component}: Parasite Drag не покрывает все дозвуковые точки"
        )
    lineage = _validate_certified_parasite_lineage(
        certificate_path,
        certificate,
        bundle,
        subsonic_rows,
    )
    validated = _validate_coverage_evidence(
        component=component,
        method="parasite_drag",
        evidence=evidence,
        source_records=source_records,
        base=base,
    )
    matching = bundle.get("policy", {}).get("matching", {})
    mach_tol = float(matching.get("parasite_mach_tolerance", 1.0e-6))
    for mach in sorted({float(row["Mach"]) for row in subsonic_rows}):
        source_names = {
            row.get("sources", {}).get("parasite_drag")
            for row in subsonic_rows
            if abs(float(row["Mach"]) - mach) <= mach_tol
        }
        candidates = [
            item for item in validated
            if item.get("mach") is not None
            and abs(float(item["mach"]) - mach) <= mach_tol
            and Path(item["source_path"]).name in source_names
        ]
        if not candidates:
            raise ValueError(
                f"replacement_coverage {component}/parasite_drag: "
                f"нет запечатанного источника для дозвуковой точки M={mach:g}"
            )
    return ([
        {
            "component": component,
            "classification": "physical_component",
            "satisfied_by": "parasite_drag",
            "source_path": item["source_path"],
            "source_sha256": item["source_sha256"],
            **({"mach": item["mach"]} if item.get("mach") is not None else {}),
        }
        for item in validated
    ], lineage)


def _coverage_evidence(item: dict, methods: list[str]) -> list[dict]:
    evidence = []
    if item.get("source_path") is not None or item.get("source_sha256") is not None:
        evidence.append({
            "method": methods[0] if len(methods) == 1 else item.get("source_method"),
            "source_path": item.get("source_path"),
            "source_sha256": item.get("source_sha256"),
            "mach": item.get("mach", item.get("Mach")),
            "alpha_deg": item.get("alpha_deg", item.get("alpha")),
        })
    for key in ("sources", "source_evidence"):
        for source in item.get(key, []) or []:
            if isinstance(source, dict):
                evidence.append({
                    "method": source.get("method") or source.get("type") or source.get("role"),
                    "source_path": source.get("source_path") or source.get("path"),
                    "source_sha256": source.get("source_sha256") or source.get("sha256"),
                    "mach": source.get("mach", source.get("Mach", item.get("mach", item.get("Mach")))),
                    "alpha_deg": source.get("alpha_deg", source.get("alpha", item.get("alpha_deg", item.get("alpha")))),
                })
    return evidence


def _certified_component_coverage(
    certificate: dict,
    *,
    backend: str,
    channel: str | None = None,
) -> set[str]:
    """Return exact component names represented by a certified backend.

    Physical replacement is not proved merely because a solver report exists.
    The certificate must also state that the solver geometry contains the
    omitted OpenVSP component.  Missing legacy coverage is deliberately
    treated as empty (fail closed).
    """
    item = certificate.get("backends", {}).get(backend, {})
    if backend == "machline":
        coverage = item.get("output_contract", {}).get("component_coverage", {})
        if channel:
            coverage = coverage.get(channel, {})
    else:
        coverage = item.get("component_coverage", {})
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        return set()
    components = coverage.get("components", [])
    if not isinstance(components, list):
        return set()
    return {
        str(component).strip().casefold()
        for component in components
        if str(component).strip()
    }


def _validate_coverage_evidence(
    *,
    component: str,
    method: str,
    evidence: list[dict],
    source_records: list[dict],
    base: Path,
) -> list[dict]:
    expected_role = {
        "machline_pressure_wave": "machline_report",
        "parasite_drag": "openvsp_parasite_manifest",
    }[method]
    candidates = []
    for item in evidence:
        item_method = _coverage_methods({"method": item.get("method")})
        if item_method and method not in item_method:
            continue
        if not item.get("source_path") or not item.get("source_sha256"):
            continue
        path = _resolve_request_path(base, item["source_path"])
        expected_hash = _normalise_sha256(item.get("source_sha256"))
        if expected_hash is None:
            raise ValueError(
                f"replacement_coverage {component}/{method}: неверный source_sha256"
            )
        if not path.is_file():
            raise ValueError(
                f"replacement_coverage {component}/{method}: источник отсутствует: {path}"
            )
        actual_hash = file_sha256(path)
        if actual_hash != expected_hash:
            raise ValueError(
                f"replacement_coverage {component}/{method}: SHA-256 источника изменился"
            )
        matching_record = next(
            (
                record for record in source_records
                if record.get("role") == expected_role
                and _normalise_sha256(record.get("sha256")) == actual_hash
                and str(record.get("path", "")).lower() == str(path.resolve()).lower()
            ),
            None,
        )
        if matching_record is None:
            raise ValueError(
                f"replacement_coverage {component}/{method}: заявленный источник "
                "не использован в этом гибридном расчёте"
            )
        candidates.append({
            "method": method,
            "source_path": str(path.resolve()),
            "source_sha256": actual_hash,
            "role": expected_role,
            "mach": _finite_or_none(item.get("mach")),
            "alpha_deg": _finite_or_none(item.get("alpha_deg")),
        })
    if not candidates:
        raise ValueError(
            f"replacement_coverage {component}/{method}: требуется хотя бы один "
            "реально использованный источник с source_path и source_sha256"
        )
    return candidates


def _validate_replacement_coverage(
    declared_coverage,
    exclusions: list[dict],
    bundle: dict,
    *,
    base: Path,
    certificate: dict,
    certificate_path: Path,
) -> dict:
    """Validate explicit physical replacement of VSPAERO exclusions.

    A reference-only construction surface may be declared ``not_physical``
    only when the certificate itself explicitly says that no additional
    aerodynamic surface exists.  Every other excluded component is treated
    conservatively as physical: MachLine must cover every output condition,
    and Parasite Drag must cover every subsonic condition.
    """
    relevant = [item for item in exclusions if _exclusion_applies_to_vspaero(item)]
    if not relevant:
        return {"required": False, "coverage": []}
    if isinstance(declared_coverage, str) and declared_coverage.strip().lower() == "auto":
        declared_coverage = _automatic_replacement_coverage(relevant, bundle)
    if not isinstance(declared_coverage, list) or not declared_coverage:
        raise ValueError(
            "Сертификат содержит VSPAERO exclusion: требуется явный "
            "request.replacement_coverage для каждого исключённого компонента"
        )

    expected_names = {str(item.get("component", item.get("role", ""))).strip().casefold() for item in relevant}
    by_component: dict[str, list[dict]] = {}
    for item in declared_coverage:
        if not isinstance(item, dict):
            raise ValueError("Каждый элемент replacement_coverage должен быть объектом")
        component = str(item.get("component", "")).strip()
        if not component:
            raise ValueError("В replacement_coverage не указан component")
        key = component.casefold()
        if key not in expected_names:
            raise ValueError(
                f"replacement_coverage содержит компонент вне exclusion сертификата: {component}"
            )
        by_component.setdefault(key, []).append(item)

    rows = list(bundle.get("rows", []))
    subsonic_rows = [row for row in rows if float(row["Mach"]) < 1.0]
    source_records = list(bundle.get("sources", []))
    result = []
    physical_lineage_validation = None
    for exclusion in relevant:
        component = str(exclusion.get("component", exclusion.get("role", ""))).strip()
        declared_methods = _declared_exclusion_methods(exclusion)
        entries = by_component.get(component.casefold(), [])
        if not entries:
            raise ValueError(
                f"Для exclusion {component} отсутствует replacement_coverage"
            )
        methods = []
        evidence = []
        for entry in entries:
            entry_methods = _coverage_methods(entry)
            for method in entry_methods:
                if method not in methods:
                    methods.append(method)
            evidence.extend(_coverage_evidence(entry, entry_methods))

        if "not_physical" in methods:
            if len(methods) != 1:
                raise ValueError(
                    f"replacement_coverage {component}: not_physical нельзя смешивать "
                    "с физическими источниками"
                )
            if not _not_physical_declaration_allowed(exclusion):
                raise ValueError(
                    f"replacement_coverage {component}: not_physical не подтверждён "
                    "полем replacement_required сертификата"
                )
            result.append({
                "component": component,
                "classification": "reference_only_not_physical",
                "satisfied_by": "not_physical",
                "source_path": None,
                "source_sha256": None,
            })
            continue

        if SEMIEMPIRICAL_COMPONENT_REPLACEMENT in declared_methods:
            allowed_declared = {
                SEMIEMPIRICAL_COMPONENT_REPLACEMENT,
                "parasite_drag_subsonic",
            }
            if (
                set(declared_methods) - allowed_declared
                or "machline_pressure_wave_all_points" in declared_methods
            ):
                raise ValueError(
                    f"replacement_coverage {component}: полуэмпирический и численный "
                    "pressure/wave-каналы нельзя смешивать"
                )
            if subsonic_rows and "parasite_drag_subsonic" not in declared_methods:
                raise ValueError(
                    f"replacement_coverage {component}: для дозвуковых точек вместе с "
                    "полуэмпирическим pressure/wave требуется parasite_drag_subsonic"
                )
            allowed_actual = {"semiempirical_component_pressure_wave", "parasite_drag"}
            if set(methods) - allowed_actual:
                raise ValueError(
                    f"replacement_coverage {component}: обнаружен неразрешённый метод"
                )
            semi_entries = [
                entry for entry in entries
                if "semiempirical_component_pressure_wave" in _coverage_methods(entry)
            ]
            result.extend(_validate_semiempirical_component_coverage(
                component=component,
                entries=semi_entries,
                bundle=bundle,
            ))
            if subsonic_rows:
                if "parasite_drag" not in methods:
                    raise ValueError(
                        f"replacement_coverage {component}: отсутствует обязательный "
                        "метод parasite_drag"
                    )
                parasite_records, parasite_lineage = _validate_parasite_component_coverage(
                    component=component,
                    evidence=evidence,
                    source_records=source_records,
                    subsonic_rows=subsonic_rows,
                    bundle=bundle,
                    base=base,
                    certificate=certificate,
                    certificate_path=certificate_path,
                )
                result.extend(parasite_records)
                if physical_lineage_validation is None:
                    physical_lineage_validation = {
                        "machline": None,
                        "parasite_drag": parasite_lineage,
                    }
            continue

        if "semiempirical_component_pressure_wave" in methods:
            raise ValueError(
                f"replacement_coverage {component}: полуэмпирический ряд не разрешён "
                "полем replacement_required сертификата"
            )

        required_methods = ["machline_pressure_wave"]
        if subsonic_rows:
            required_methods.append("parasite_drag")
        missing_methods = [method for method in required_methods if method not in methods]
        if missing_methods:
            raise ValueError(
                f"replacement_coverage {component}: отсутствуют обязательные методы "
                + ", ".join(missing_methods)
            )

        machline_components = _certified_component_coverage(
            certificate,
            backend="machline",
            channel="pressure_wave",
        )
        if component.casefold() not in machline_components:
            raise ValueError(
                f"replacement_coverage {component}: сертифицированный MachLine "
                "pressure/wave не содержит этот компонент"
            )
        if subsonic_rows:
            parasite_components = _certified_component_coverage(
                certificate,
                backend="parasite_drag",
            )
            if component.casefold() not in parasite_components:
                raise ValueError(
                    f"replacement_coverage {component}: сертифицированный "
                    "Parasite Drag не содержит этот компонент"
                )

        if physical_lineage_validation is None:
            physical_lineage_validation = {
                "machline": _validate_certified_machline_lineage(
                    certificate_path,
                    certificate,
                    bundle,
                ),
                "parasite_drag": (
                    _validate_certified_parasite_lineage(
                        certificate_path,
                        certificate,
                        bundle,
                        subsonic_rows,
                    )
                    if subsonic_rows else None
                ),
            }

        if any(not row.get("sources", {}).get("machline") for row in rows):
            raise ValueError(
                f"replacement_coverage {component}: MachLine pressure/wave не покрывает все точки"
            )
        if any(not row.get("sources", {}).get("parasite_drag") for row in subsonic_rows):
            raise ValueError(
                f"replacement_coverage {component}: Parasite Drag не покрывает все дозвуковые точки"
            )

        validated_by_method = {}
        for method in required_methods:
            validated_by_method[method] = _validate_coverage_evidence(
                component=component,
                method=method,
                evidence=evidence,
                source_records=source_records,
                base=base,
            )

        matching = bundle.get("policy", {}).get("matching", {})
        mach_tol = float(matching.get("mach_tolerance", 1.0e-6))
        alpha_tol = float(matching.get("alpha_tolerance", 1.0e-6))
        for row in rows:
            candidates = [
                item for item in validated_by_method["machline_pressure_wave"]
                if item.get("mach") is not None
                and item.get("alpha_deg") is not None
                and abs(float(item["mach"]) - float(row["Mach"])) <= mach_tol
                and abs(float(item["alpha_deg"]) - float(row["alpha_deg"])) <= alpha_tol
                and Path(item["source_path"]).name == row.get("sources", {}).get("machline")
            ]
            if not candidates:
                raise ValueError(
                    f"replacement_coverage {component}/machline_pressure_wave: "
                    f"нет запечатанного источника для M={row['Mach']:g}, "
                    f"alpha={row['alpha_deg']:g}"
                )
        for mach in sorted({float(row["Mach"]) for row in subsonic_rows}):
            source_names = {
                row.get("sources", {}).get("parasite_drag")
                for row in subsonic_rows
                if abs(float(row["Mach"]) - mach) <= mach_tol
            }
            candidates = [
                item for item in validated_by_method["parasite_drag"]
                if item.get("mach") is not None
                and abs(float(item["mach"]) - mach) <= mach_tol
                and Path(item["source_path"]).name in source_names
            ]
            if not candidates:
                raise ValueError(
                    f"replacement_coverage {component}/parasite_drag: "
                    f"нет запечатанного источника для дозвуковой точки M={mach:g}"
                )

        for method in required_methods:
            for item in validated_by_method[method]:
                saved = {
                    "component": component,
                    "classification": "physical_component",
                    "satisfied_by": method,
                    "source_path": item["source_path"],
                    "source_sha256": item["source_sha256"],
                }
                if item.get("mach") is not None:
                    saved["mach"] = item["mach"]
                if item.get("alpha_deg") is not None:
                    saved["alpha_deg"] = item["alpha_deg"]
                result.append(saved)
    return {
        "required": True,
        "coverage": result,
        "lineage_validation": physical_lineage_validation,
    }


def _automatic_replacement_coverage(exclusions: list[dict], bundle: dict) -> list[dict]:
    """Expand ``replacement_coverage='auto'`` from sources actually selected."""
    rows = list(bundle.get("rows", []))
    machline_reports = list(bundle.get("lineage", {}).get("machline", {}).get("reports", []))
    parasite_points = list(bundle.get("lineage", {}).get("parasite_drag", {}).get("points", []))
    matching = bundle.get("policy", {}).get("matching", {})
    mach_tol = float(matching.get("mach_tolerance", 1.0e-6))
    alpha_tol = float(matching.get("alpha_tolerance", 1.0e-6))
    parasite_tol = float(matching.get("parasite_mach_tolerance", 1.0e-6))
    result = []
    for exclusion in exclusions:
        component = str(exclusion.get("component", exclusion.get("role", ""))).strip()
        declared_methods = _declared_exclusion_methods(exclusion)
        if _not_physical_declaration_allowed(exclusion):
            result.append({
                "component": component,
                "satisfied_by": "not_physical",
            })
            continue
        use_semiempirical_pressure = SEMIEMPIRICAL_COMPONENT_REPLACEMENT in declared_methods
        if use_semiempirical_pressure:
            terms = _semiempirical_component_terms(bundle.get("policy", {}), component)
            if len(terms) == 1:
                result.append({
                    "component": component,
                    "satisfied_by": "semiempirical_component_pressure_wave",
                    "term_id": terms[0]["id"],
                    "term_fingerprint": sha256_payload(terms[0]),
                })
        else:
            for row in rows:
                candidates = [
                    item for item in machline_reports
                    if item.get("file_name") == row.get("sources", {}).get("machline")
                    and abs(float(item["Mach"]) - float(row["Mach"])) <= mach_tol
                    and abs(float(item["alpha_deg"]) - float(row["alpha_deg"])) <= alpha_tol
                ]
                if len(candidates) == 1:
                    report = candidates[0]
                    result.append({
                        "component": component,
                        "satisfied_by": "machline_pressure_wave",
                        "mach": float(row["Mach"]),
                        "alpha_deg": float(row["alpha_deg"]),
                        "source_path": report.get("path"),
                        "source_sha256": report.get("sha256"),
                    })
        for mach in sorted({
            float(row["Mach"]) for row in rows
            if float(row["Mach"]) < 1.0
            and "parasite_drag_subsonic" in declared_methods
        }):
            candidates = [
                item for item in parasite_points
                if abs(float(item["Mach"]) - mach) <= parasite_tol
            ]
            if len(candidates) == 1 and candidates[0].get("manifest"):
                manifest = candidates[0]["manifest"]
                result.append({
                    "component": component,
                    "satisfied_by": "parasite_drag",
                    "mach": mach,
                    "source_path": manifest.get("path"),
                    "source_sha256": manifest.get("sha256"),
                })
    return result


def _resolve_request_path(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _resolve_manifest_output(base: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_file():
        return path.resolve()
    candidate = base / path
    if candidate.is_file():
        return candidate.resolve()
    # Moved blind packages retain file names even when original absolute paths no longer exist.
    matches = list(base.rglob(path.name))
    return matches[0].resolve() if len(matches) == 1 else path.resolve()


def _expand_request_files(
    base: Path,
    explicit: list[str],
    directory: str | None,
    pattern: str,
) -> list[Path]:
    paths = [_resolve_request_path(base, item) for item in explicit]
    if directory:
        root = _resolve_request_path(base, directory)
        if not root.is_dir():
            raise FileNotFoundError(f"Не найдена папка результатов: {root}")
        paths.extend(path for path in root.rglob(pattern) if path.is_file())
    paths = list(dict.fromkeys(path.resolve() for path in paths))
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Не найдены входные файлы: " + "; ".join(map(str, missing)))
    return paths


def _finite_or_none(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Автоматический гибридный расчёт RepairMach и построение Excel"
    )
    parser.add_argument("request", type=Path, help="repairmach.hybrid-request JSON")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--no-workbook", action="store_true")
    args = parser.parse_args(argv)
    result = run_hybrid_request(
        args.request,
        output_dir=args.output_dir,
        build_workbook=not args.no_workbook,
    )
    print(json.dumps({
        "status": result["bundle"]["status"],
        "summary": result["bundle"]["summary"],
        "outputs": {key: str(value) for key, value in result["outputs"].items()},
    }, ensure_ascii=False, indent=2))
    return 0 if result["bundle"]["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
