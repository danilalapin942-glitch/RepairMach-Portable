from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import sys
import unittest
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from hybrid_pipeline import (
    POLICY_SCHEMA,
    REQUEST_SCHEMA,
    SEMIEMPIRICAL_TERM_SCHEMA,
    _certified_component_coverage,
    _load_machline_points,
    _load_parasite_points,
    _load_vspaero_source,
    _validate_certified_parasite_lineage,
    _validate_replacement_coverage,
    build_hybrid_series,
    file_sha256,
    load_hybrid_policy,
    run_hybrid_request,
    semiempirical_contributions,
    verify_xlsx_external_links,
)
from geometry_certificate import CERTIFICATE_SCHEMA, seal_certificate
from geometry_manifest import sha256_payload
from openvsp_runner import generate_parasite_drag_script
from tri_mesh import TriMesh, write_tri


SCENARIO_ID = "hybrid_request"
SCENARIO_SHA256 = "a" * 64


def policy(*, cy_alpha_method="direct"):
    cy_alpha = {
        "method": cy_alpha_method,
        "alpha_start_deg": 0.0,
        "alpha_end_deg": 1.0,
    }
    if cy_alpha_method != "direct":
        cy_alpha.update({
            "wing_geometry": {
                "aspect_ratio": 2.86665,
                "leading_edge_sweep_deg": 42.0,
            },
            "normal_mach_transition_width": 0.35,
            "supersonic_efficiency": 0.93,
            "residual_limit_ratio": 0.05,
        })
    return {
        "schema": POLICY_SCHEMA,
        "method_version": "test-hybrid-1",
        "reference_independent": True,
        "pointwise_tuning": False,
        "transonic_excluded": [0.8, 1.2],
        "matching": {
            "mach_tolerance": 1.0e-6,
            "alpha_tolerance": 1.0e-6,
            "parasite_mach_tolerance": 1.0e-6,
        },
        "quality": {"machline_residual_norm_max": 0.05},
        "drag": {
            "pressure_wave_source": "MachLine_wind_axis_CD",
            "induced_source": "VSPAERO_CDi",
            "parasite_source": "OpenVSP_ParasiteDrag",
            "require_parasite_below_mach_one": True,
            "allow_machline_wake_with_vspaero_cdi": False,
            "machline_geometry_glob": "*",
            "semiempirical_terms": [
                {
                    "id": "details",
                    "label": "Антенны и щели",
                    "enabled": True,
                    "provenance": "independent handbook method",
                    "applicability": {"mach_min": 0.8, "mach_max": 1.2},
                    "certification": {
                        "schema": SEMIEMPIRICAL_TERM_SCHEMA,
                        "method_id": "TEST-DETAILS-1",
                        "equation_version": "1",
                        "reference_independent": True,
                        "pointwise_tuning": False,
                        "uncertainty_fraction": 0.15,
                        "applicability_basis": "Test fixture over M=0.8..1.2",
                        "source": {
                            "kind": "declared_engineering_method",
                            "citation": "Synthetic independent test method",
                        },
                    },
                    "model": {
                        "type": "mach_table",
                        "points": [[0.8, 0.001], [1.2, 0.002]],
                    },
                }
            ],
        },
        "lift": {
            "coefficient_source": "VSPAERO_CLtot",
            "cy_alpha": cy_alpha,
        },
    }


def write_polar(path: Path, slopes=(0.06, 0.05)) -> None:
    path.write_text(
        "VSPAERO fixture\n"
        " Beta Mach AoA CLtot CDi CStot E\n"
        f" 0 0.8 0 0.100 0.005 0.0 inf\n"
        f" 0 0.8 1 {0.100 + slopes[0]:.6f} 0.006 0.0 inf\n"
        f" 0 1.2 0 0.080 0.006 0.0 inf\n"
        f" 0 1.2 1 {0.080 + slopes[1]:.6f} 0.007 0.0 inf\n",
        encoding="utf-8",
    )


def write_machline(path: Path, mach: float, alpha: float, cx: float, geometry="full.tri") -> None:
    angle = math.radians(alpha)
    payload = {
        "mesh_info": {"N_wake_panels": 0},
        "solver_results": {
            "solver_status_code": 0,
            "residual": {"norm": 1.0e-4, "max": 5.0e-5},
        },
        "total_forces": {"Cx": cx, "Cy": 0.0, "Cz": 0.1},
        "input": {
            "flow": {
                "freestream_mach_number": mach,
                "freestream_velocity": [100.0 * math.cos(angle), 0.0, 100.0 * math.sin(angle)],
            },
            "geometry": {"file": geometry},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    geometry_path = Path(geometry)
    solver_path = path.parents[1] / "machline.exe" if len(path.parents) > 1 else Path()
    if geometry_path.is_file() and solver_path.is_file():
        log = path.with_name(path.stem + "_run.log")
        log.write_text("MachLine completed\n", encoding="utf-8")
        manifest = path.with_name(path.stem + "_manifest.json")
        manifest.write_text(json.dumps({
            "schema": "repairmach.machline-run/1.0",
            "status": "completed",
            "solver": {"path": str(solver_path.resolve()), "sha256": file_sha256(solver_path)},
            "geometry": {"path": str(geometry_path.resolve()), "sha256": file_sha256(geometry_path)},
            "conditions": {"mach": mach, "alpha_deg": alpha},
            "outputs": {"report": str(path.resolve()), "log": str(log.resolve())},
            "output_sha256": {"report": file_sha256(path), "log": file_sha256(log)},
            "output_quality": {
                "valid": True,
                "solver_status_code": 0,
                "residual_norm": 1.0e-4,
                "residual_max": 5.0e-5,
            },
        }), encoding="utf-8")


def write_parasite(path: Path, mach=0.8, total=0.007) -> None:
    path.write_text(
        "Results_Name,Parasite_Drag\n"
        f"FC_Mach,{mach}\n"
        "FC_Sref,100\n"
        "Num_Comp,1\n"
        "Comp_Label,Wing\n"
        f"Comp_CD,{total}\n"
        f"Total_CD_Total,{total}\n",
        encoding="utf-8",
    )


def write_bound_geometry_certificate(
    path: Path,
    *,
    master: Path,
    twin: Path,
    exclusions: list[dict] | None = None,
    certified_tri: Path | None = None,
    parasite_twin: Path | None = None,
) -> dict:
    vspscript = master.parent / "vspscript.exe"
    vspaero = master.parent / "vspaero.exe"
    machline = master.parent / "machline.exe"
    for executable, content in (
        (vspscript, b"test vspscript executable"),
        (vspaero, b"test vspaero executable"),
        (machline, b"test machline executable"),
    ):
        if not executable.exists():
            executable.write_bytes(content)
    points = [
        {"mach": mach, "alpha_deg": alpha, "beta_deg": 0.0}
        for mach in (0.8, 1.2)
        for alpha in (0.0, 1.0)
    ]
    anchors = [
        {
            **point,
            "mode": "mixed" if not exclusions else "lifting",
            "mesh_levels": ["coarse", "medium", "fine"],
            "converged": True,
        }
        for point in points
    ]
    mode = "mixed" if not exclusions else "lifting"
    master_hash = file_sha256(master)
    twin_hash = file_sha256(twin)
    tri_hash = file_sha256(certified_tri) if certified_tri is not None else None
    parasite_hash = file_sha256(parasite_twin) if parasite_twin is not None else None
    certificate_root = path.parent.resolve()

    def relative(item: Path) -> str:
        return item.resolve().relative_to(certificate_root).as_posix()

    tail_setup = {
        "mode": "fixed",
        "geometry_name": "GO",
        "incidence_deg": 0.0,
    }
    scope = {
        "mach_intervals": [[0.8, 0.8], [1.2, 1.2]],
        "alpha_deg": [0.0, 1.0],
        "beta_deg": 0.0,
        "horizontal_tail": tail_setup,
        "engine_boundary": "model",
    }
    reference = {
        "area": 100.0,
        "cref": 10.0,
        "bref": 20.0,
        "center": [5.0, 0.0, 0.0],
    }
    backends = {
        "vspaero": {
            "eligible": True,
            "mode": mode,
            "blockers": [],
            "solver_geometry": {
                "path": str(twin.resolve()),
                "relative_path": relative(twin),
                "sha256": twin_hash,
            },
            "qualified_scope": {"coverage_kind": "exact_points", "points": points},
        },
        "hybrid": {
            "eligible": not bool(exclusions),
            "conditional": bool(exclusions),
            "mode": "declared_sources_required" if exclusions else "optional",
            "blockers": [],
            "qualified_scope": {"coverage_kind": "exact_points", "points": points},
        },
    }
    if exclusions:
        requirements = []
        for exclusion in exclusions:
            declared = exclusion.get("replacement_required", [])
            if isinstance(declared, str):
                declared = [declared]
            for method in declared:
                requirements.append({
                    "component": exclusion.get("component"),
                    "method": method,
                    "backend_capability_available": True,
                    "reason": "fixture_backend_coverage",
                })
        backends["hybrid"]["replacement_contract"] = {
            "schema": "repairmach.replacement-contract/1.0",
            "required": True,
            "backend_capabilities_complete": True,
            "sealed_sources_still_required": True,
            "requirements": requirements,
            "unavailable_requirements": [],
        }
    twins = {
        f"vspaero_{mode}": {
            "path": str(twin.resolve()),
            "relative_path": relative(twin),
            "sha256": twin_hash,
        },
    }
    evidence_files = [
        {
            "role": "source_master",
            "relative_path": master.name,
            "path": str(master.resolve()),
            "sha256": master_hash,
        },
        {
            "role": f"vspaero_{mode}_twin",
            "relative_path": twin.name,
            "path": str(twin.resolve()),
            "sha256": twin_hash,
        },
    ]
    tri_record = {"requested": False, "eligible": False, "status": "not_run"}
    if certified_tri is not None:
        backends["machline"] = {
            "eligible": True,
            "mode": "certified_tri",
            "blockers": [],
            "output_contract": {
                "component_coverage": {
                    "pressure_wave": {
                        "coverage_kind": "exact_openvsp_components",
                        "complete": True,
                        "components": ["Fuselage"],
                    },
                },
            },
            "solver_geometry": {
                "path": str(certified_tri.resolve()),
                "relative_path": relative(certified_tri),
                "sha256": tri_hash,
            },
            "qualified_scope": {"coverage_kind": "exact_points", "points": points},
        }
        tri_record = {
            "requested": True,
            "eligible": True,
            "status": "passed",
            "certified_tri": str(certified_tri.resolve()),
            "certified_tri_sha256": tri_hash,
        }
        evidence_files.append({
            "role": "machline_certified_tri",
            "relative_path": certified_tri.name,
            "path": str(certified_tri.resolve()),
            "sha256": tri_hash,
        })
    else:
        backends["machline"] = {
            "eligible": False,
            "mode": None,
            "qualified_scope": {"coverage_kind": "none", "points": []},
        }
    if parasite_twin is not None:
        backends["parasite_drag"] = {
            "eligible": True,
            "mode": "full_geometry",
            "blockers": [],
            "component_coverage": {
                "coverage_kind": "exact_openvsp_components",
                "complete": True,
                "components": ["Fuselage", "Wing", "GO", "VO"],
            },
            "geometry_set": 0,
            "reference_area": reference["area"],
            "solver_geometry": {
                "path": str(parasite_twin.resolve()),
                "relative_path": relative(parasite_twin),
                "sha256": parasite_hash,
            },
            "qualified_scope": {
                "coverage_kind": "exact_points",
                "points": [point for point in points if point["mach"] < 1.0],
            },
        }
        twins["parasite"] = {
            "path": str(parasite_twin.resolve()),
            "relative_path": relative(parasite_twin),
            "sha256": parasite_hash,
        }
        evidence_files.append({
            "role": "parasite_drag_twin",
            "relative_path": parasite_twin.name,
            "path": str(parasite_twin.resolve()),
            "sha256": parasite_hash,
        })
    else:
        backends["parasite_drag"] = {
            "eligible": False,
            "mode": None,
            "qualified_scope": {"coverage_kind": "none", "points": []},
        }
    certificate = seal_certificate({
        "schema": CERTIFICATE_SCHEMA,
        "method_version": "RM91-GEOMETRY-CERT-1",
        "run_status": "complete",
        "verdict": "PASS_NATIVE" if not exclusions else "PASS_WITH_DECLARED_EXCLUSIONS",
        "flags": {
            "master_unchanged": True,
            "solver_eligible": True,
            "hybrid_substitution_required": bool(exclusions),
        },
        "master": {
            "source_path": str(master.resolve()),
            "sha256_before": master_hash,
            "sha256_after": master_hash,
            "canonical_sha256": master_hash,
            "unchanged": True,
        },
        "scope": scope,
        "requested_scope": scope,
        "reference": reference,
        "qualification": {"profile": "hybrid_fixture", "anchors": anchors},
        "software": {"solvers": {
            "openvsp": {
                "version": "test",
                "executable": str(vspscript.resolve()),
                "sha256": file_sha256(vspscript),
            },
            "vspaero": {
                "version": "test",
                "executable": str(vspaero.resolve()),
                "sha256": file_sha256(vspaero),
            },
            "machline": {
                "version": "test",
                "executable": str(machline.resolve()),
                "sha256": file_sha256(machline),
            },
        }},
        "backends": backends,
        "twins": twins,
        "tri": tri_record,
        "exclusions": exclusions or [],
        "scenario_eligibility": {
            SCENARIO_ID: {
                "eligible": True,
                "backend": "hybrid",
                "coverage_kind": "exact_points",
                "scenario_sha256": SCENARIO_SHA256,
            },
        },
        "eligible_scenarios": [SCENARIO_ID],
        "evidence_files": evidence_files,
    })
    path.write_text(json.dumps(certificate, ensure_ascii=False), encoding="utf-8")
    return certificate


def write_vspaero_manifest(
    path: Path,
    polar: Path,
    source_vsp3_sha256: str,
    *,
    solver_geometry_sha256: str,
    geometry_mode: str,
) -> None:
    vspscript = path.parent / "vspscript.exe"
    vspaero = path.parent / "vspaero.exe"
    log = path.with_suffix(".log")
    script = path.with_suffix(".vspscript")
    script.write_text(
        "array<double> gmres_factor(1, 1);\n"
        "array<int> wake_iterations(1, 8);\n"
        "array<int> wake_nodes(1, 24);\n"
        "array<double> wake_relaxation(1, 0.8);\n",
        encoding="utf-8",
    )
    log_lines = []
    log_lines.extend([
        "REPAIRMACH_TAIL_SETUP;MODE=fixed_incidence;NAME=GO;REQUESTED_ANGLE_DEG=0;ACTUAL_ANGLE_DEG=0;VALID=true\n",
        "REPAIRMACH_ENGINE_SETUP;MODE=model;VALID=true\n",
        "REPAIRMACH_SETUP_VALIDATION=OK\n",
        "REPAIRMACH_SET_VALIDATION=OK\n",
    ])
    for mach in (0.8, 1.2):
        for alpha in (0.0, 1.0):
            log_lines.extend([
                f"Solving... Mach: {mach:.6f} ... Alpha: {alpha:.6f} ... Beta: 0\n",
                f" 8 {mach:.5f} {alpha:.5f} 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 -1.1 0.2 1\n",
            ])
    log_lines.append("REPAIRMACH_VSPAERO_COMPLETE=1\n")
    log.write_text("".join(log_lines), encoding="utf-8")
    path.write_text(json.dumps({
        "schema": "repairmach.vspaero-run-manifest/1.0",
        "status": "completed",
        "scenario_id": SCENARIO_ID,
        "scenario_sha256": SCENARIO_SHA256,
        "source_vsp3_sha256": source_vsp3_sha256,
        "solver_geometry_sha256": solver_geometry_sha256,
        "geometry_mode": geometry_mode,
        "executables": {
            "openvsp": {"path": str(vspscript.resolve()), "sha256": file_sha256(vspscript)},
            "vspaero": {"path": str(vspaero.resolve()), "sha256": file_sha256(vspaero)},
        },
        "conditions": {
            "mach": {"start": 0.8, "end": 1.2, "points": 2},
            "alpha_deg": {"start": 0.0, "end": 1.0, "points": 2},
            "beta_deg": 0.0,
            "horizontal_tail": {
                "mode": "fixed",
                "geometry_name": "GO",
                "incidence_deg": 0.0,
            },
            "engine_boundary": "model",
        },
        "reference": {
            "area": 100.0,
            "cref": 10.0,
            "bref": 20.0,
            "center": [5.0, 0.0, 0.0],
        },
        "numerical_controls": {
            "forward_gmres_convergence_factor": 1.0,
            "wake_num_iter": 8,
            "num_wake_nodes": 24,
            "wake_relax": 0.8,
            "point_timeout_seconds": 900.0,
        },
        "outputs": {
            "polar": str(polar.resolve()),
            "log": str(log.resolve()),
            "script": str(script.resolve()),
        },
        "output_quality": {"valid": True, "max_log10_l2_residual": -0.3},
        "output_sha256": {
            "polar": file_sha256(polar),
            "log": file_sha256(log),
            "script": file_sha256(script),
        },
    }), encoding="utf-8")


def write_parasite_manifest(
    path: Path,
    results_csv: Path,
    *,
    source_vsp3: Path,
    solver_geometry: Path,
    geometry_set: int = 0,
    geometry_mode: str = "full_geometry",
) -> None:
    vspscript = path.parents[1] / "vspscript.exe"
    script = path.parent / f"{path.stem}.vspscript"
    log = path.parent / f"{path.stem}.log"
    generate_parasite_drag_script(
        script,
        solver_geometry,
        results_csv,
        0.8,
        0.0,
        100.0,
        geometry_set,
    )
    log.write_text("Parasite Drag completed\n", encoding="utf-8")
    payload = {
        "schema": "repairmach.parasite-drag-run/1.0",
        "status": "completed",
        "source_vsp3": str(source_vsp3.resolve()),
        "source_vsp3_sha256": file_sha256(source_vsp3),
        "solver_geometry": {
            "path": str(solver_geometry.resolve()),
            "sha256": file_sha256(solver_geometry),
        },
        "solver_geometry_sha256": file_sha256(solver_geometry),
        "geometry_mode": geometry_mode,
        "executables": {
            "openvsp": {"path": str(vspscript.resolve()), "sha256": file_sha256(vspscript)},
        },
        "conditions": {
            "mach": 0.8,
            "reference_area": 100.0,
            "geometry_set": geometry_set,
        },
        "outputs": {
            "results_csv": str(results_csv.resolve()),
            "script": str(script.resolve()),
            "log": str(log.resolve()),
        },
        "output_quality": {"valid": True},
        "output_sha256": {
            "results_csv": file_sha256(results_csv),
            "script": file_sha256(script),
            "log": file_sha256(log),
        },
    }
    payload["record_fingerprint"] = sha256_payload(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def reseal_parasite_manifest(path: Path, payload: dict) -> None:
    payload.pop("record_fingerprint", None)
    payload["record_fingerprint"] = sha256_payload(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def parasite_lineage_fixture(root: Path) -> dict:
    master = root / "master.vsp3"
    vspaero_twin = root / "vspaero_mixed.vsp3"
    parasite_twin = root / "parasite_full_geometry.vsp3"
    master.write_bytes(b"certified master")
    vspaero_twin.write_bytes(b"certified VSPAERO twin")
    parasite_twin.write_bytes(b"certified Parasite Drag twin")
    certificate_path = root / "certificate.json"
    write_bound_geometry_certificate(
        certificate_path,
        master=master,
        twin=vspaero_twin,
        parasite_twin=parasite_twin,
    )
    parasite_dir = root / "parasite"
    parasite_dir.mkdir()
    results_csv = parasite_dir / "M08.csv"
    write_parasite(results_csv)
    manifest = parasite_dir / "M08_manifest.json"
    write_parasite_manifest(
        manifest,
        results_csv,
        source_vsp3=master,
        solver_geometry=parasite_twin,
    )
    return {
        "master": master,
        "parasite_twin": parasite_twin,
        "certificate": certificate_path,
        "results_csv": results_csv,
        "manifest": manifest,
    }


def validate_parasite_fixture(fixture: dict) -> dict:
    _, _, lineage = _load_parasite_points([fixture["manifest"]], 1.0e-6)
    certificate = json.loads(fixture["certificate"].read_text(encoding="utf-8"))
    bundle = {
        "lineage": {"parasite_drag": lineage},
        "policy": {"matching": {"parasite_mach_tolerance": 1.0e-6}},
    }
    return _validate_certified_parasite_lineage(
        fixture["certificate"],
        certificate,
        bundle,
        [{"Mach": 0.8}],
    )


class HybridPipelineTests(unittest.TestCase):
    def test_machline_pressure_coverage_excludes_delegated_thin_components(self):
        certificate = {
            "backends": {
                "machline": {
                    "output_contract": {
                        "component_coverage": {
                            "pressure_wave": {
                                "complete": True,
                                "components": ["Fuselage", "Gondola_left"],
                            },
                            "delegated_to_vspaero": {
                                "complete": True,
                                "components": ["Wing", "GO", "VO"],
                            },
                        },
                    },
                },
            },
        }
        covered = _certified_component_coverage(
            certificate,
            backend="machline",
            channel="pressure_wave",
        )
        self.assertEqual({"fuselage", "gondola_left"}, covered)
        self.assertNotIn("vo", covered)

    def test_missing_component_coverage_fails_closed(self):
        self.assertEqual(
            set(),
            _certified_component_coverage(
                {"backends": {"machline": {"output_contract": {}}}},
                backend="machline",
                channel="pressure_wave",
            ),
        )

    def test_replacement_rejects_solver_that_does_not_contain_component(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            certificate = {
                "backends": {
                    "machline": {
                        "output_contract": {
                            "component_coverage": {
                                "pressure_wave": {
                                    "complete": True,
                                    "components": ["Fuselage"],
                                },
                            },
                        },
                    },
                },
            }
            with self.assertRaisesRegex(ValueError, "не содержит этот компонент"):
                _validate_replacement_coverage(
                    [{"component": "VO", "satisfied_by": "machline_pressure_wave"}],
                    [{
                        "component": "VO",
                        "backends": ["vspaero", "hybrid"],
                        "replacement_required": ["machline_pressure_wave_all_points"],
                    }],
                    {
                        "rows": [{
                            "Mach": 1.2,
                            "alpha_deg": 0.0,
                            "sources": {"machline": "M1p2_A0_report.json"},
                        }],
                        "sources": [],
                        "policy": {"matching": {}},
                    },
                    base=root,
                    certificate=certificate,
                    certificate_path=root / "certificate.json",
                )

    def test_vspaero_manifest_cannot_relabel_beta_tail_or_engine_boundary(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "vspscript.exe").write_bytes(b"vspscript")
            (root / "vspaero.exe").write_bytes(b"vspaero")
            polar = root / "full.polar"
            write_polar(polar)
            manifest = root / "manifest.json"
            write_vspaero_manifest(
                manifest,
                polar,
                "1" * 64,
                solver_geometry_sha256="2" * 64,
                geometry_mode="mixed",
            )
            original = json.loads(manifest.read_text(encoding="utf-8"))
            mutations = {
                "Beta": lambda item: item["conditions"].__setitem__("beta_deg", 1.0),
                "ГО": lambda item: item["conditions"]["horizontal_tail"].__setitem__(
                    "incidence_deg", 2.0
                ),
                "Engine boundary": lambda item: item["conditions"].__setitem__(
                    "engine_boundary", "to_face"
                ),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    payload = json.loads(json.dumps(original))
                    mutate(payload)
                    manifest.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, label):
                        _load_vspaero_source(manifest, 1.0e-6, 1.0e-6)

            payload = json.loads(json.dumps(original))
            payload["numerical_controls"]["wake_num_iter"] = 999
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "solver_controls"):
                _load_vspaero_source(manifest, 1.0e-6, 1.0e-6)

    def test_vspaero_manifest_cannot_relax_residual_gate_but_may_tighten_it(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "vspscript.exe").write_bytes(b"vspscript")
            (root / "vspaero.exe").write_bytes(b"vspaero")
            polar = root / "full.polar"
            write_polar(polar)
            manifest = root / "manifest.json"
            write_vspaero_manifest(
                manifest,
                polar,
                "1" * 64,
                solver_geometry_sha256="2" * 64,
                geometry_mode="mixed",
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            log = Path(payload["outputs"]["log"])

            log.write_text(
                log.read_text(encoding="utf-8").replace("-1.1", "-0.2"),
                encoding="utf-8",
            )
            payload["output_quality"]["max_log10_l2_residual"] = 999.0
            payload["output_sha256"]["log"] = file_sha256(log)
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "невязк"):
                _load_vspaero_source(manifest, 1.0e-6, 1.0e-6)

            log.write_text(
                log.read_text(encoding="utf-8").replace("-0.2", "-0.6"),
                encoding="utf-8",
            )
            payload["output_quality"]["max_log10_l2_residual"] = -0.5
            payload["output_sha256"]["log"] = file_sha256(log)
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            rows, _, lineage = _load_vspaero_source(manifest, 1.0e-6, 1.0e-6)
            self.assertEqual(4, len(rows))
            self.assertTrue(lineage["sealed"])

    def test_policy_rejects_reference_dependent_mode(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            payload = policy()
            payload["reference_independent"] = False
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "независимой"):
                load_hybrid_policy(path)

    def test_automatic_series_combines_all_declared_terms(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            polar = root / "full.polar"
            write_polar(polar)
            reports = []
            for mach, base in ((0.8, 0.02), (1.2, 0.03)):
                for alpha in (0.0, 1.0):
                    path = root / f"M{mach}_a{alpha}_report.json"
                    write_machline(path, mach, alpha, base)
                    reports.append(path)
            parasite = root / "parasite.csv"
            write_parasite(parasite)
            result = build_hybrid_series(
                vspaero_source=polar,
                machline_report_paths=reports,
                parasite_result_paths=[parasite],
                policy=policy(),
            )
        self.assertEqual("complete", result["status"])
        self.assertEqual(4, result["summary"]["complete_points"])
        m08 = next(row for row in result["rows"] if row["Mach"] == 0.8 and row["alpha_deg"] == 0.0)
        self.assertAlmostEqual(0.02 + 0.005 + 0.007 + 0.001, m08["Cx_total"])
        self.assertAlmostEqual(0.00015, m08["Cx_semiempirical_uncertainty_rss"])
        self.assertAlmostEqual(
            0.00015, m08["Cx_semiempirical_uncertainty_worst_case"]
        )
        m12 = next(row for row in result["rows"] if row["Mach"] == 1.2 and row["alpha_deg"] == 0.0)
        self.assertAlmostEqual(0.03 + 0.006 + 0.002, m12["Cx_total"])
        self.assertAlmostEqual(0.06, result["cy_alpha"][0]["Cy_alpha_final_per_deg"])
        self.assertFalse(result["reference_data_used"])
        self.assertFalse(result["pointwise_tuning_used"])

    def test_request_writes_json_and_internal_csv_outputs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            polar = root / "full.polar"
            write_polar(polar)
            report_dir = root / "machline"
            report_dir.mkdir()
            for mach in (0.8, 1.2):
                for alpha in (0.0, 1.0):
                    write_machline(report_dir / f"full_M{mach}_a{alpha}_report.json", mach, alpha, 0.02)
            parasite_dir = root / "parasite"
            parasite_dir.mkdir()
            write_parasite(parasite_dir / "M08.csv")
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({
                "schema": REQUEST_SCHEMA,
                "policy": str(policy_path),
                "vspaero_source": str(polar),
                "machline_reports_dir": str(report_dir),
                "machline_report_glob": "*_report.json",
                "parasite_results_dir": str(parasite_dir),
            }), encoding="utf-8")
            output = root / "output"
            result = run_hybrid_request(request, output_dir=output, build_workbook=False)
            saved = json.loads(result["outputs"]["json"].read_text(encoding="utf-8"))
            csv_text = result["outputs"]["points_csv"].read_text(encoding="utf-8-sig")
        self.assertEqual("complete", saved["status"])
        self.assertTrue(result["outputs"]["points_csv"].name.endswith(".csv"))
        self.assertTrue(result["outputs"]["cy_alpha_csv"].name.endswith(".csv"))
        self.assertIn("semiempirical_terms_json", csv_text.splitlines()[0])
        self.assertIn("TEST-DETAILS-1", csv_text)

    def test_sealed_hybrid_rejects_vspaero_manifest_from_another_master(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            master = root / "master.vsp3"
            twin = root / "vspaero_mixed.vsp3"
            master.write_bytes(b"certified master")
            twin.write_bytes(b"certified mixed twin")
            certificate_path = root / "certificate.json"
            write_bound_geometry_certificate(
                certificate_path,
                master=master,
                twin=twin,
            )

            polar = root / "full.polar"
            write_polar(polar)
            manifest = root / "vspaero_manifest.json"
            write_vspaero_manifest(
                manifest,
                polar,
                "0" * 64,
                solver_geometry_sha256=file_sha256(twin),
                geometry_mode="mixed",
            )
            report_dir = root / "machline"
            report_dir.mkdir()
            for mach in (1.2,):
                for alpha in (0.0, 1.0):
                    write_machline(
                        report_dir / f"full_M{mach}_a{alpha}_report.json",
                        mach,
                        alpha,
                        0.02,
                    )
            parasite_dir = root / "parasite"
            parasite_dir.mkdir()
            write_parasite(parasite_dir / "M08.csv")
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({
                "schema": REQUEST_SCHEMA,
                "policy": str(policy_path),
                "vspaero_source": str(manifest),
                "machline_reports_dir": str(report_dir),
                "machline_report_glob": "*_report.json",
                "parasite_results_dir": str(parasite_dir),
                "geometry_certificate": str(certificate_path),
                "scenario_id": SCENARIO_ID,
                "scenario_sha256": SCENARIO_SHA256,
            }), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "MASTER|source|источник|линей|геометр|постанов|geometry|setup",
            ):
                run_hybrid_request(request, output_dir=root / "output", build_workbook=False)

    def test_sealed_hybrid_requires_machine_readable_coverage_for_every_exclusion(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            master = root / "master.vsp3"
            twin = root / "vspaero_lifting.vsp3"
            master.write_bytes(b"certified master")
            twin.write_bytes(b"certified lifting twin")
            exclusion = {
                "component": "Fuselage",
                "backend": "vspaero",
                "reason": "lifting-only qualification",
                "replacement_required": "parasite_drag",
            }
            certificate_path = root / "certificate.json"
            write_bound_geometry_certificate(
                certificate_path,
                master=master,
                twin=twin,
                exclusions=[exclusion],
            )

            polar = root / "full.polar"
            write_polar(polar)
            manifest = root / "vspaero_manifest.json"
            write_vspaero_manifest(
                manifest,
                polar,
                file_sha256(master),
                solver_geometry_sha256=file_sha256(twin),
                geometry_mode="lifting",
            )
            report_dir = root / "machline"
            report_dir.mkdir()
            for mach in (0.8, 1.2):
                for alpha in (0.0, 1.0):
                    write_machline(
                        report_dir / f"full_M{mach}_a{alpha}_report.json",
                        mach,
                        alpha,
                        0.02,
                    )
            parasite_dir = root / "parasite"
            parasite_dir.mkdir()
            parasite = parasite_dir / "M08.csv"
            write_parasite(parasite)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({
                "schema": REQUEST_SCHEMA,
                "policy": str(policy_path),
                "vspaero_source": str(manifest),
                "machline_reports_dir": str(report_dir),
                "machline_report_glob": "*_report.json",
                "parasite_results_dir": str(parasite_dir),
                "geometry_certificate": str(certificate_path),
                "scenario_id": SCENARIO_ID,
                "scenario_sha256": SCENARIO_SHA256,
                # A parasite file happens to be present, but the omission of
                # replacement_coverage must not silently satisfy Fuselage.
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "exclusion|replacement|исключ|замен"):
                run_hybrid_request(request, output_dir=root / "output", build_workbook=False)

    def test_sealed_hybrid_accepts_hashed_declared_replacement_coverage(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            master = root / "master.vsp3"
            twin = root / "vspaero_lifting.vsp3"
            parasite_twin = root / "parasite_full_geometry.vsp3"
            certified_tri = root / "certified.tri"
            master.write_bytes(b"certified master")
            twin.write_bytes(b"certified lifting twin")
            parasite_twin.write_bytes(b"certified parasite twin")
            certified_tri.write_bytes(b"certified MachLine TRI")
            exclusion = {
                "component": "Fuselage",
                "backend": "vspaero",
                "reason": "lifting-only qualification",
                "replacement_required": [
                    "machline_pressure_wave_all_points",
                    "parasite_drag_subsonic",
                ],
            }
            certificate_path = root / "certificate.json"
            write_bound_geometry_certificate(
                certificate_path,
                master=master,
                twin=twin,
                exclusions=[exclusion],
                certified_tri=certified_tri,
                parasite_twin=parasite_twin,
            )
            polar = root / "full.polar"
            write_polar(polar)
            manifest = root / "vspaero_manifest.json"
            write_vspaero_manifest(
                manifest,
                polar,
                file_sha256(master),
                solver_geometry_sha256=file_sha256(twin),
                geometry_mode="lifting",
            )
            report_dir = root / "machline"
            report_dir.mkdir()
            replacement_coverage = []
            for mach in (0.8, 1.2):
                for alpha in (0.0, 1.0):
                    report = report_dir / f"full_M{mach}_a{alpha}_report.json"
                    write_machline(
                        report,
                        mach,
                        alpha,
                        0.02,
                        geometry=str(certified_tri.resolve()),
                    )
                    replacement_coverage.append({
                        "component": "Fuselage",
                        "satisfied_by": "machline_pressure_wave",
                        "mach": mach,
                        "alpha_deg": alpha,
                        "source_path": str(report.resolve()),
                        "source_sha256": file_sha256(report),
                    })
            parasite_dir = root / "parasite"
            parasite_dir.mkdir()
            parasite = parasite_dir / "M08.csv"
            write_parasite(parasite)
            parasite_manifest = parasite_dir / "M08_manifest.json"
            write_parasite_manifest(
                parasite_manifest,
                parasite,
                source_vsp3=master,
                solver_geometry=parasite_twin,
            )
            replacement_coverage.append({
                "component": "Fuselage",
                "satisfied_by": "parasite_drag",
                "mach": 0.8,
                "source_path": str(parasite_manifest.resolve()),
                "source_sha256": file_sha256(parasite_manifest),
            })
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({
                "schema": REQUEST_SCHEMA,
                "policy": str(policy_path),
                "vspaero_source": str(manifest),
                "machline_reports_dir": str(report_dir),
                "machline_report_glob": "*_report.json",
                "parasite_results": [str(parasite_manifest)],
                "geometry_certificate": str(certificate_path),
                "scenario_id": SCENARIO_ID,
                "scenario_sha256": SCENARIO_SHA256,
                "replacement_coverage": replacement_coverage,
            }), encoding="utf-8")

            result = run_hybrid_request(request, output_dir=root / "output", build_workbook=False)
            self.assertEqual("complete", result["bundle"]["status"])
            saved_coverage = result["bundle"]["replacement_coverage"]
            self.assertEqual(5, len(saved_coverage))

            request_payload = json.loads(request.read_text(encoding="utf-8"))
            request_payload["scenario_sha256"] = "b" * 64
            request.write_text(json.dumps(request_payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hybrid_request|scenario"):
                run_hybrid_request(
                    request,
                    output_dir=root / "output_bad_scenario",
                    build_workbook=False,
                )

            request_payload.pop("scenario_sha256")
            request.write_text(json.dumps(request_payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scenario_sha256"):
                run_hybrid_request(
                    request,
                    output_dir=root / "output_missing_scenario",
                    build_workbook=False,
                )

            request_payload["scenario_sha256"] = SCENARIO_SHA256
            request.write_text(json.dumps(request_payload), encoding="utf-8")
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_payload["scenario_sha256"] = "b" * 64
            manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scenario|постанов"):
                run_hybrid_request(
                    request,
                    output_dir=root / "output_manifest_scenario",
                    build_workbook=False,
                )
            self.assertTrue(all(item["component"] == "Fuselage" for item in saved_coverage))
            self.assertEqual(
                {"machline_pressure_wave", "parasite_drag"},
                {item["satisfied_by"] for item in saved_coverage},
            )
            machline_coverage = [
                item for item in saved_coverage
                if item["satisfied_by"] == "machline_pressure_wave"
            ]
            self.assertEqual(
                {(0.8, 0.0), (0.8, 1.0), (1.2, 0.0), (1.2, 1.0)},
                {(item["mach"], item["alpha_deg"]) for item in machline_coverage},
            )
            parasite_coverage = [
                item for item in saved_coverage
                if item["satisfied_by"] == "parasite_drag"
            ]
            self.assertEqual([0.8], [item["mach"] for item in parasite_coverage])
            self.assertTrue(all(Path(item["source_path"]).is_file() for item in saved_coverage))
            self.assertTrue(all(
                file_sha256(Path(item["source_path"])) == item["source_sha256"]
                for item in saved_coverage
            ))

    def test_certified_parasite_lineage_uses_actual_hashed_artifacts(self):
        with TemporaryDirectory() as temp:
            fixture = parasite_lineage_fixture(Path(temp))
            validation = validate_parasite_fixture(fixture)
            self.assertTrue(validation["valid"])
            self.assertEqual(
                file_sha256(fixture["parasite_twin"]),
                validation["solver_geometry_sha256"],
            )
            self.assertEqual(0, validation["geometry_set"])

    def test_parasite_manifest_rejects_tampered_script_log_and_csv(self):
        for role in ("script", "log", "results_csv"):
            with self.subTest(role=role), TemporaryDirectory() as temp:
                fixture = parasite_lineage_fixture(Path(temp))
                payload = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                artifact = Path(payload["outputs"][role])
                artifact.write_bytes(artifact.read_bytes() + b"\nTAMPERED")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    _load_parasite_points([fixture["manifest"]], 1.0e-6)

    def test_parasite_lineage_rejects_relabelled_solver_model(self):
        with TemporaryDirectory() as temp:
            fixture = parasite_lineage_fixture(Path(temp))
            root = Path(temp)
            rogue = root / "rogue_parasite.vsp3"
            rogue.write_bytes(b"different unqualified geometry")
            payload = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            script = Path(payload["outputs"]["script"])
            text = script.read_text(encoding="utf-8")
            old_name = str(fixture["parasite_twin"].resolve()).replace("\\", "/")
            new_name = str(rogue.resolve()).replace("\\", "/")
            self.assertIn(old_name, text)
            script.write_text(text.replace(old_name, new_name), encoding="utf-8")
            payload["solver_geometry"] = {
                "path": str(rogue.resolve()),
                "sha256": file_sha256(rogue),
            }
            payload["solver_geometry_sha256"] = file_sha256(rogue)
            payload["output_sha256"]["script"] = file_sha256(script)
            reseal_parasite_manifest(fixture["manifest"], payload)
            with self.assertRaisesRegex(ValueError, "VSP3 twin"):
                validate_parasite_fixture(fixture)

    def test_parasite_lineage_rejects_relabelled_geometry_set_and_mode(self):
        for mutation in ("geometry_set", "geometry_mode"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                fixture = parasite_lineage_fixture(Path(temp))
                payload = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
                if mutation == "geometry_set":
                    script = Path(payload["outputs"]["script"])
                    text = script.read_text(encoding="utf-8")
                    self.assertIn("geom_set[0] = 0;", text)
                    script.write_text(
                        text.replace("geom_set[0] = 0;", "geom_set[0] = 1;"),
                        encoding="utf-8",
                    )
                    payload["conditions"]["geometry_set"] = 1
                    payload["output_sha256"]["script"] = file_sha256(script)
                    expected_error = "OpenVSP Set"
                else:
                    payload["geometry_mode"] = "lifting"
                    expected_error = "mode"
                reseal_parasite_manifest(fixture["manifest"], payload)
                with self.assertRaisesRegex(ValueError, expected_error):
                    validate_parasite_fixture(fixture)

    def test_parasite_lineage_rejects_relabelled_csv_and_manifest(self):
        with TemporaryDirectory() as temp:
            fixture = parasite_lineage_fixture(Path(temp))
            payload = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            relabelled = Path(temp) / "parasite" / "relabelled.csv"
            relabelled.write_bytes(fixture["results_csv"].read_bytes())
            payload["outputs"]["results_csv"] = str(relabelled.resolve())
            payload["output_sha256"]["results_csv"] = file_sha256(relabelled)
            reseal_parasite_manifest(fixture["manifest"], payload)
            with self.assertRaisesRegex(ValueError, "results CSV.*script"):
                _load_parasite_points([fixture["manifest"]], 1.0e-6)

        with TemporaryDirectory() as temp:
            fixture = parasite_lineage_fixture(Path(temp))
            payload = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            payload["conditions"]["geometry_set"] = 1
            fixture["manifest"].write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "record_fingerprint"):
                _load_parasite_points([fixture["manifest"]], 1.0e-6)

    def test_rm921_cy_alpha_is_built_from_full_and_thin_series(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            full = root / "full.polar"
            thin = root / "thin.polar"
            write_polar(full, slopes=(0.06, 0.05))
            write_polar(thin, slopes=(0.055, 0.045))
            result = build_hybrid_series(
                vspaero_source=full,
                thin_vspaero_source=thin,
                machline_report_paths=[],
                parasite_result_paths=[],
                policy=policy(cy_alpha_method="RM92.1-CYA-WINGBODY-3-working"),
            )
        self.assertEqual(2, len(result["cy_alpha"]))
        self.assertTrue(all(row["method_version"].startswith("RM92.1") for row in result["cy_alpha"]))
        self.assertTrue(all(math.isfinite(row["Cy_alpha_final_per_deg"]) for row in result["cy_alpha"]))

    def test_different_machline_geometries_are_not_silently_mixed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            polar = root / "full.polar"
            write_polar(polar)
            first = root / "first.json"
            second = root / "second.json"
            write_machline(first, 0.8, 0.0, 0.02, geometry="full.tri")
            write_machline(second, 0.8, 0.0, 0.02, geometry="fuselage.tri")
            with self.assertRaisesRegex(ValueError, "разные геометрии"):
                build_hybrid_series(
                    vspaero_source=polar,
                    machline_report_paths=[first, second],
                    parasite_result_paths=[],
                    policy=policy(),
                )

    def test_semiempirical_table_forbids_extrapolation(self):
        test_policy = policy()
        test_policy["drag"]["semiempirical_terms"][0]["applicability"]["mach_min"] = 0.7
        with self.assertRaisesRegex(ValueError, "экстраполяция запрещена"):
            semiempirical_contributions(test_policy, 0.75)

    def test_active_semiempirical_term_requires_certified_method_passport(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            test_policy = policy()
            test_policy["drag"]["semiempirical_terms"][0].pop("certification")
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(test_policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "проверяемого паспорта"):
                load_hybrid_policy(policy_path)

    def test_semiempirical_contribution_records_uncertainty_and_fingerprint(self):
        contribution = semiempirical_contributions(policy(), 1.0)[0]
        self.assertEqual("TEST-DETAILS-1", contribution["method_id"])
        self.assertAlmostEqual(0.000225, contribution["uncertainty_cd"])
        self.assertEqual(64, len(contribution["term_fingerprint"]))

    def test_semiempirical_component_replacement_is_bound_to_every_output_point(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            test_policy = policy()
            term = test_policy["drag"]["semiempirical_terms"][0]
            term["replacement"] = {
                "method": "semiempirical_component_pressure_wave_all_points",
                "component": "VO",
                "coverage_channels": ["pressure_wave"],
                "alpha_dependence": "independent",
            }
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(test_policy), encoding="utf-8")
            loaded = load_hybrid_policy(policy_path)
            rows = []
            for mach in (1.2,):
                for alpha in (0.0, 1.0):
                    rows.append({
                        "Mach": mach,
                        "alpha_deg": alpha,
                        "semiempirical_terms": semiempirical_contributions(loaded, mach),
                    })
            bundle = {
                "policy": loaded,
                "rows": rows,
                "sources": [],
                "lineage": {"hybrid_policy": {
                    "path": str(policy_path.resolve()),
                    "sha256": file_sha256(policy_path),
                    "policy_fingerprint": sha256_payload(loaded),
                }},
            }
            validation = _validate_replacement_coverage(
                "auto",
                [{
                    "component": "VO",
                    "backends": ["vspaero", "hybrid"],
                    "replacement_required": [
                        "semiempirical_component_pressure_wave_all_points"
                    ],
                }],
                bundle,
                base=root,
                certificate={},
                certificate_path=root / "certificate.json",
            )
        self.assertTrue(validation["required"])
        self.assertEqual(2, len(validation["coverage"]))
        self.assertEqual(
            {"semiempirical_component_pressure_wave"},
            {item["satisfied_by"] for item in validation["coverage"]},
        )
        self.assertEqual(
            {(1.2, 0.0), (1.2, 1.0)},
            {(item["Mach"], item["alpha_deg"]) for item in validation["coverage"]},
        )
        self.assertTrue(all(item["term_fingerprint"] for item in validation["coverage"]))

    def test_semiempirical_component_replacement_fails_closed_on_bad_contract(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            test_policy = policy()
            term = test_policy["drag"]["semiempirical_terms"][0]
            term["replacement"] = {
                "method": "semiempirical_component_pressure_wave_all_points",
                "component": "VO",
                "coverage_channels": ["pressure_wave"],
                "alpha_dependence": "independent",
            }
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(test_policy), encoding="utf-8")
            loaded = load_hybrid_policy(policy_path)
            bundle = {
                "policy": loaded,
                "rows": [{
                    "Mach": 1.2,
                    "alpha_deg": 0.0,
                    "semiempirical_terms": semiempirical_contributions(loaded, 1.2),
                }],
                "sources": [],
                "lineage": {"hybrid_policy": {
                    "path": str(policy_path.resolve()),
                    "sha256": file_sha256(policy_path),
                    "policy_fingerprint": sha256_payload(loaded),
                }},
            }
            exclusions = [{
                "component": "VO",
                "backends": ["vspaero", "hybrid"],
                "replacement_required": [
                    "semiempirical_component_pressure_wave_all_points"
                ],
            }]
            with self.assertRaisesRegex(ValueError, "term_fingerprint"):
                _validate_replacement_coverage(
                    [{
                        "component": "VO",
                        "satisfied_by": "semiempirical_component_pressure_wave",
                        "term_id": term["id"],
                        "term_fingerprint": "f" * 64,
                    }],
                    exclusions,
                    bundle,
                    base=root,
                    certificate={},
                    certificate_path=root / "certificate.json",
                )

            bad_policy = policy()
            bad_policy["drag"]["semiempirical_terms"][0]["replacement"] = {
                "method": "semiempirical_component_pressure_wave_all_points",
                "component": "VO",
                "coverage_channels": ["total_component_drag"],
                "alpha_dependence": "independent",
            }
            policy_path.write_text(json.dumps(bad_policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "coverage_channels"):
                load_hybrid_policy(policy_path)

    def test_sealed_hybrid_accepts_semiempirical_pressure_and_parasite_for_thin_exclusion(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            master = root / "master.vsp3"
            lifting_twin = root / "vspaero_lifting.vsp3"
            parasite_twin = root / "parasite_full_geometry.vsp3"
            certified_tri = root / "certified.tri"
            master.write_bytes(b"certified master")
            lifting_twin.write_bytes(b"certified lifting twin without VO")
            parasite_twin.write_bytes(b"certified parasite twin with VO")
            certified_tri.write_bytes(b"certified thick-body MachLine TRI")
            exclusion = {
                "component": "VO",
                "backends": ["vspaero", "hybrid"],
                "reason": "thin surface delegated to a sealed pressure method",
                "replacement_required": [
                    "semiempirical_component_pressure_wave_all_points",
                    "parasite_drag_subsonic",
                ],
            }
            certificate_path = root / "certificate.json"
            write_bound_geometry_certificate(
                certificate_path,
                master=master,
                twin=lifting_twin,
                exclusions=[exclusion],
                certified_tri=certified_tri,
                parasite_twin=parasite_twin,
            )

            polar = root / "full.polar"
            write_polar(polar)
            manifest = root / "vspaero_manifest.json"
            write_vspaero_manifest(
                manifest,
                polar,
                file_sha256(master),
                solver_geometry_sha256=file_sha256(lifting_twin),
                geometry_mode="lifting",
            )
            report_dir = root / "machline"
            report_dir.mkdir()
            for mach in (0.8, 1.2):
                for alpha in (0.0, 1.0):
                    write_machline(
                        report_dir / f"full_M{mach}_a{alpha}_report.json",
                        mach,
                        alpha,
                        0.02,
                        geometry=str(certified_tri.resolve()),
                    )
            parasite_dir = root / "parasite"
            parasite_dir.mkdir()
            parasite = parasite_dir / "M08.csv"
            write_parasite(parasite)
            parasite_manifest = parasite_dir / "M08_manifest.json"
            write_parasite_manifest(
                parasite_manifest,
                parasite,
                source_vsp3=master,
                solver_geometry=parasite_twin,
            )

            test_policy = policy()
            test_policy["drag"]["semiempirical_terms"][0]["replacement"] = {
                "method": "semiempirical_component_pressure_wave_all_points",
                "component": "VO",
                "coverage_channels": ["pressure_wave"],
                "alpha_dependence": "independent",
            }
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(test_policy), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({
                "schema": REQUEST_SCHEMA,
                "policy": str(policy_path),
                "vspaero_source": str(manifest),
                "machline_reports_dir": str(report_dir),
                "machline_report_glob": "*_report.json",
                "parasite_results": [str(parasite_manifest)],
                "geometry_certificate": str(certificate_path),
                "scenario_id": SCENARIO_ID,
                "scenario_sha256": SCENARIO_SHA256,
                "replacement_coverage": "auto",
            }), encoding="utf-8")
            result = run_hybrid_request(
                request,
                output_dir=root / "output",
                build_workbook=False,
            )

        self.assertEqual("complete", result["bundle"]["status"])
        coverage = result["bundle"]["replacement_coverage"]
        self.assertEqual(
            {"semiempirical_component_pressure_wave", "parasite_drag"},
            {item["satisfied_by"] for item in coverage},
        )
        self.assertEqual(
            4,
            len([
                item for item in coverage
                if item["satisfied_by"] == "semiempirical_component_pressure_wave"
            ]),
        )
        self.assertTrue(any(
            item["satisfied_by"] == "parasite_drag" and item.get("mach") == 0.8
            for item in coverage
        ))

    def test_semiempirical_source_file_hash_is_verified(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "method.md"
            source.write_text("independent method", encoding="utf-8")
            test_policy = policy()
            source_record = test_policy["drag"]["semiempirical_terms"][0][
                "certification"
            ]["source"]
            source_record.update({"path": source.name, "sha256": file_sha256(source)})
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(test_policy), encoding="utf-8")
            loaded = load_hybrid_policy(policy_path)
            self.assertEqual("details", loaded["drag"]["semiempirical_terms"][0]["id"])
            polar = root / "full.polar"
            write_polar(polar)
            result = build_hybrid_series(
                vspaero_source=polar,
                machline_report_paths=[],
                parasite_result_paths=[],
                policy=loaded,
                policy_path=policy_path,
            )
            self.assertEqual(4, result["summary"]["points"])

            source.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Нарушен hash"):
                load_hybrid_policy(policy_path)

    def test_hybrid_consumes_sealed_masked_machline_force(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "machline.exe").write_bytes(b"solver")
            tri = root / "certified_mesh.tri"
            write_tri(
                tri,
                TriMesh(
                    [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
                    [(0, 1, 2), (0, 3, 1)],
                    [1, 2],
                ),
            )
            results = root / "results"
            results.mkdir()
            report = results / "case_report.json"
            write_machline(report, 1.2, 0.0, -0.5, geometry=str(tri))
            mask = root / "force_integration_mask.json"
            mask.write_text(json.dumps({
                "schema": "repairmach.machline-force-mask/1.0",
                "certified_tri_sha256": file_sha256(tri),
            }), encoding="utf-8")
            masked = results / "case_report_masked_force.json"
            masked.write_text(json.dumps({
                "schema": "repairmach.machline-masked-force/1.0",
                "inputs": {
                    "tri": {"sha256": file_sha256(tri)},
                    "report": {"sha256": file_sha256(report)},
                    "force_mask": {"sha256": file_sha256(mask)},
                },
                "result": {
                    "alignment": {"verified": True},
                    "masked_mesh_axes": {"Cx": 0.041, "Cy": 0.0, "Cz": 0.003},
                    "masked_wind_axes": {"cd": 0.041, "cl": 0.003, "cy_span": 0.0},
                },
            }), encoding="utf-8")
            manifest_path = results / "case_report_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["force_output"] = {
                "kind": "masked_thick_body_pressure_wave",
                "base_drag_replacement_required": True,
            }
            manifest["outputs"].update({
                "masked_force": str(masked),
                "force_mask": str(mask),
            })
            manifest["output_sha256"].update({
                "masked_force": file_sha256(masked),
                "force_mask": file_sha256(mask),
            })
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            points, sources = _load_machline_points([report], policy())
        self.assertEqual(1, len(points))
        self.assertAlmostEqual(0.041, points[0]["cd"])
        self.assertEqual("masked_thick_body_pressure_wave", points[0]["force_output_kind"])
        self.assertTrue(points[0]["base_drag_replacement_required"])
        self.assertIn("machline_masked_force", [kind for kind, _ in sources])

    def test_xlsx_external_link_scan(self):
        with TemporaryDirectory() as temp:
            clean = Path(temp) / "clean.xlsx"
            linked = Path(temp) / "linked.xlsx"
            with zipfile.ZipFile(clean, "w") as archive:
                archive.writestr("xl/workbook.xml", "<workbook/>")
            with zipfile.ZipFile(linked, "w") as archive:
                archive.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
            self.assertEqual([], verify_xlsx_external_links(clean))
            self.assertTrue(verify_xlsx_external_links(linked))


if __name__ == "__main__":
    unittest.main()
