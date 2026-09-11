from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from geometry_certificate import (
    CERTIFICATE_SCHEMA,
    certificate_integrity_errors,
    seal_certificate,
    verify_certificate,
    write_certificate_report,
)
from geometry_certification import (
    MACHLINE_TRI_EXPORT_SCHEMA,
    _component_geometry_coverage,
    _build_replacement_contract,
    _export_machline_tri,
    _run_vspaero_ladder,
    _run_vspaero_component_isolation,
    _run_vspaero_qualification,
    _seal_machline_export_record,
    _tri_certification,
    _unique_run_dir,
    _validate_scope,
    classify_vspaero_health,
    certify_geometry,
    mesh_convergence,
    parse_vspaero_health,
)
from geometry_delta import compute_geometry_deltas, delta_within_policy
from geometry_manifest import (
    canonical_vsp3_sha256,
    parse_inventory_log,
    sha256_file,
    sha256_payload,
)
from geometry_rules import (
    effective_policy,
    load_geometry_policy,
    semantic_audit,
    validate_geometry_policy,
    validate_reference,
)
from machline_geometry import COORDINATE_CONVENTION
from geometry_twins import (
    apply_model_actions,
    build_transformation_plan,
    copy_passthrough,
    generate_apply_script,
    mesh_actions,
    verify_mesh_inventory,
    verify_plan,
)
from tri_mesh import TriMesh, read_tri


POLICY_PATH = ROOT / "config" / "geometry_certification.json"


def reference() -> dict:
    return {
        "area": 100.0,
        "cref": 10.0,
        "bref": 20.0,
        "center": [5.0, 0.0, 0.0],
    }


def component(
    geom_id: str,
    name: str,
    geom_type: str,
    *,
    set_1: bool,
    set_2: bool,
    parameters: list[dict] | None = None,
    wing_terminal: dict | None = None,
    total_surfaces: int | None = 1,
) -> dict:
    item = {
        "id": geom_id,
        "name": name,
        "type": geom_type,
        "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
        "sets": {"1": set_1, "2": set_2},
        "parameters": parameters or [],
        "main_surfaces": 1,
        "total_surfaces": total_surfaces,
    }
    if wing_terminal is not None:
        item["wing_terminal"] = wing_terminal
    return item


def valid_inventory(*extra: dict) -> dict:
    return {
        "schema": "repairmach.openvsp-inventory/1.0",
        "valid": True,
        "errors": [],
        "components": [
            component("fuse", "Fuselage", "Fuselage", set_1=True, set_2=False),
            component("wing", "Wing", "Wing", set_1=False, set_2=True),
            *extra,
        ],
    }


def plan_for(inventory: dict, policy: dict | None = None) -> tuple[dict, dict]:
    policy = policy or load_geometry_policy(POLICY_PATH)
    audit = semantic_audit(inventory, reference(), policy)
    bindings = {
        "master_sha256": "master-hash",
        "policy_sha256": sha256_payload(policy),
        "reference_sha256": sha256_payload(reference()),
        "scope_sha256": sha256_payload(policy["scope"]),
    }
    plan = build_transformation_plan(
        inventory=inventory,
        semantic_audit=audit,
        reference=reference(),
        policy=policy,
        **bindings,
    )
    return plan, bindings


def diagnostic_report(*, faces: int = 1000, **overrides: int | bool | float) -> dict:
    report = {
        "vertices": 500,
        "faces": faces,
        "connected_components": 1,
        "bbox_diagonal": 1.0,
        "merge_tolerance": 1.0e-10,
        "invalid_index_faces": 0,
        "invalid_index_face_ids": [],
        "repeated_vertex_faces": 0,
        "repeated_vertex_face_ids": [],
        "degenerate_faces": 0,
        "degenerate_face_ids": [],
        "duplicate_faces": 0,
        "duplicate_face_ids": [],
        "duplicate_vertices": 0,
        "unreferenced_vertices": 0,
        "boundary_edges": 0,
        "nonmanifold_edges": 0,
        "inconsistent_orientation_edges": 0,
        "safe_repairs_available": 0,
        "watertight": True,
        "machline_safe_topology": True,
    }
    report.update(overrides)
    return report


def machline_export_record(tri: Path, source_twin: Path, policy: dict) -> dict:
    """Minimal valid automatic-export lineage fixture."""
    thick_user = int(policy["sets"]["thick_user_set"])
    thin_user = int(policy["sets"]["thin_user_set"])
    union_user = int(policy["sets"]["machline_export_user_set"])
    key_path = tri.with_suffix(".key")
    key_path.write_text("Color Name BCType\n1.0 Fuselage_S_Surf0 0\n", encoding="utf-8")
    thin_path = tri.with_suffix(".vspgeom")
    thin_key_path = tri.with_suffix(".vkey")
    thin_path.write_text("fixture thin mesh", encoding="utf-8")
    thin_key_path.write_text("fixture thin key", encoding="utf-8")
    return _seal_machline_export_record({
        "schema": MACHLINE_TRI_EXPORT_SCHEMA,
        "status": "passed",
        "source_solver_twin": {
            "role": "machline_fine",
            "mesh_level": "fine",
            "path": str(source_twin.resolve()),
            "sha256": sha256_file(source_twin),
            "canonical_sha256": sha256_file(source_twin),
        },
        "certification_bindings": {
            "master_sha256": "a" * 64,
            "plan_sha256": "b" * 64,
            "base_twin_sha256": "c" * 64,
            "fine_twin_sha256": sha256_file(source_twin),
            "fine_mesh_signature_sha256": "d" * 64,
        },
        "source_unchanged": True,
        "export": {
            "format": "OpenVSP_EXPORT_NASCART",
            "source_thick_user_set": thick_user,
            "source_thick_api_set": thick_user + 3,
            "source_thin_user_set": thin_user,
            "source_thin_api_set": thin_user + 3,
            "union_user_set": union_user,
            "union_api_set": union_user + 3,
            "thin_export_set": "SET_NONE",
            "include_subsurfaces": True,
            "coordinate_convention": COORDINATE_CONVENTION,
            "mesh_representation": "NASCART_thick_plus_VSPGeom_VLM_thin",
            "key_path": str(key_path.resolve()),
            "key_sha256": sha256_file(key_path),
            "thin_path": str(thin_path.resolve()),
            "thin_sha256": sha256_file(thin_path),
            "thin_key_path": str(thin_key_path.resolve()),
            "thin_key_sha256": sha256_file(thin_key_path),
            "component_map": {"1": "Fuselage_S_Surf0", "2": "Wing_C"},
            "component_roles": {"1": "thick", "2": "thin"},
            "component_face_counts": {"1": 1, "2": 1},
            "union_geometry_names": ["Fuselage", "Wing"],
            "path": str(tri.resolve()),
            "sha256": sha256_file(tri),
        },
        "tool": {},
        "legacy_tri_hint": None,
        "errors": [],
        "manifest_path": str((tri.parent / "export_manifest.json").resolve()),
    })


class GeometryPolicyTests(unittest.TestCase):
    def test_component_coverage_maps_only_exact_openvsp_geometries(self):
        coverage = _component_geometry_coverage(
            {1, 2},
            {1: "Fuselage_S_Surf0", 2: "Gondola_left_S_Surf0"},
            ["Fuselage", "Gondola_left", "Wing", "VO"],
        )
        self.assertTrue(coverage["complete"])
        self.assertEqual(["Fuselage", "Gondola_left"], coverage["components"])
        self.assertEqual({"1": "Fuselage", "2": "Gondola_left"}, coverage["resolved_component_ids"])

    def test_component_coverage_keeps_unresolved_ids_as_blocking_evidence(self):
        coverage = _component_geometry_coverage(
            {7},
            {7: "Unknown_S_Surf0"},
            ["Fuselage", "Wing"],
        )
        self.assertFalse(coverage["complete"])
        self.assertEqual([], coverage["components"])
        self.assertEqual(7, coverage["unresolved_components"][0]["component_id"])

    def test_replacement_contract_is_component_aware(self):
        contract = _build_replacement_contract(
            [
                {
                    "component": "Fuselage",
                    "backends": ["vspaero", "hybrid"],
                    "replacement_required": [
                        "machline_pressure_wave_all_points",
                        "parasite_drag_subsonic",
                    ],
                },
                {
                    "component": "VO",
                    "backends": ["vspaero", "hybrid"],
                    "replacement_required": [
                        "machline_pressure_wave_all_points",
                        "parasite_drag_subsonic",
                    ],
                },
            ],
            machline_eligible=True,
            machline_components=["Fuselage"],
            parasite_eligible=True,
            parasite_components=["Fuselage", "Wing", "GO", "VO"],
            base_drag_required=True,
        )
        unavailable = {
            (item["component"], item["method"])
            for item in contract["unavailable_requirements"]
        }
        self.assertEqual({("VO", "machline_pressure_wave_all_points")}, unavailable)
        self.assertFalse(contract["backend_capabilities_complete"])
        self.assertTrue(any(
            item["method"] == "base_drag_semiempirical"
            for item in contract["requirements"]
        ))

    def test_run_directory_is_short_and_keeps_identity_hash(self):
        with TemporaryDirectory() as tmp:
            path = _unique_run_dir(
                Path(tmp),
                "Very_Long_Project_Name_For_A_Blind_Aircraft",
                "Very_Long_Model_File_Name_With_Many_Versions_And_Notes",
            )
        self.assertLessEqual(len(path.name), 56)
        self.assertRegex(path.name, r"^\d{8}_\d{6}_.+_[0-9a-f]{8}$")

    def test_repository_policy_is_valid_and_complete(self):
        policy = load_geometry_policy(POLICY_PATH)
        self.assertEqual("RM91-GEOMETRY-CERT-1", policy["method_version"])
        self.assertEqual(
            ["coarse", "medium", "fine", "extra_fine", "ultra_fine"],
            list(policy["mesh_levels"]),
        )
        self.assertTrue(policy["convergence"]["adaptive_refinement"]["enabled"])
        self.assertEqual(
            [8, 4],
            policy["convergence"]["adaptive_refinement"]
            ["local_span_plateau"]["backoff_offsets"],
        )
        self.assertEqual(0.0, policy["probes"]["vspaero"]["max_log10_max_residual"])
        self.assertEqual(
            2,
            policy["probes"]["vspaero"]["numerical_recovery"]["attempts"],
        )
        self.assertEqual(
            1,
            policy["probes"]["vspaero"]["numerical_recovery"]["controls"]["ncpu"],
        )
        self.assertEqual({"Wing", "Fuselage", "GO", "VO", "Gondola"}, set(policy["semantics"]["roles"]))

    def test_effective_policy_merges_without_mutating_base(self):
        base = load_geometry_policy(POLICY_PATH)
        original = deepcopy(base)
        merged = effective_policy(base, {
            "semantics": {"aliases": {"VentralVO": "VO"}},
            "convergence": {"relative_tolerance": 0.04},
        })
        self.assertEqual(original, base)
        self.assertEqual("VO", merged["semantics"]["aliases"]["VentralVO"])
        self.assertEqual(0.04, merged["convergence"]["relative_tolerance"])
        self.assertEqual(original["mesh_levels"], merged["mesh_levels"])

    def test_policy_rejects_alias_to_unknown_role(self):
        policy = load_geometry_policy(POLICY_PATH)
        policy["semantics"]["aliases"]["Tail"] = "HorizontalTail"
        with self.assertRaisesRegex(ValueError, "Alias"):
            validate_geometry_policy(policy)

    def test_policy_rejects_nonincreasing_mesh_ladder(self):
        policy = load_geometry_policy(POLICY_PATH)
        policy["mesh_levels"]["medium"]["thin_tess_w"] = policy["mesh_levels"]["coarse"]["thin_tess_w"]
        with self.assertRaisesRegex(ValueError, "строго возрастать"):
            validate_geometry_policy(policy)

    def test_policy_validates_every_consumed_mesh_parameter(self):
        for key in ("thin_tess_u", "thick_sect_tess_u"):
            with self.subTest(key=key):
                policy = load_geometry_policy(POLICY_PATH)
                del policy["mesh_levels"]["medium"][key]
                with self.assertRaisesRegex(ValueError, key):
                    validate_geometry_policy(policy)

                policy = load_geometry_policy(POLICY_PATH)
                policy["mesh_levels"]["medium"][key] = policy["mesh_levels"]["coarse"][key]
                with self.assertRaisesRegex(ValueError, "строго возрастать"):
                    validate_geometry_policy(policy)

    def test_policy_rejects_ambiguous_declared_exclusion_contracts(self):
        valid = {
            "component": "Wing2",
            "backends": ["vspaero", "hybrid"],
            "reason": "reference-only construction surface",
            "replacement_required": ["not_physical"],
        }
        invalid_variants = [
            ({**valid, "component": " "}, "component"),
            ({**valid, "backends": "vspaero"}, "backends"),
            ({**valid, "backends": ["VSPAERO"]}, "канонические"),
            ({**valid, "backends": ["all"]}, "неподдерживаемые"),
            ({**valid, "backends": ["vspaero"]}, "должен быть ровно"),
            ({**valid, "backends": ["machline"]}, "неподдерживаемые"),
            ({**valid, "backends": ["parasite_drag"]}, "неподдерживаемые"),
            ({**valid, "backend": "vspaero"}, "backend запрещён"),
            ({**valid, "reason": ""}, "reason"),
            ({**valid, "replacement_required": "not_physical"}, "replacement_required"),
            ({**valid, "replacement_required": ["semiempirical"]}, "неподдерживаемые"),
            ({**valid, "replacement_required": [
                "not_physical", "parasite_drag_subsonic"
            ]}, "единственным"),
        ]
        for exclusion, message in invalid_variants:
            with self.subTest(exclusion=exclusion):
                policy = load_geometry_policy(POLICY_PATH)
                policy["semantics"]["declared_exclusions"] = [exclusion]
                with self.assertRaisesRegex(ValueError, message):
                    validate_geometry_policy(policy)

        policy = load_geometry_policy(POLICY_PATH)
        policy["semantics"]["declared_exclusions"] = [valid, deepcopy(valid)]
        with self.assertRaisesRegex(ValueError, "более одного раза"):
            validate_geometry_policy(policy)

    def test_reference_validation_rejects_nonpositive_and_nonfinite_values(self):
        findings = validate_reference({
            "area": 0.0,
            "cref": "nan",
            "bref": -1.0,
            "center": [1.0, float("inf"), 0.0],
        })
        self.assertEqual(4, len(findings))
        self.assertTrue(all(item["severity"] == "BLOCKER" for item in findings))

    def test_scope_normalization_and_validation(self):
        scope = _validate_scope({
            "mach_intervals": [[0, "0.8"], [1.2, 2.2]],
            "alpha_deg": [0, "5"],
            "beta_deg": "0",
        })
        self.assertEqual([[0.0, 0.8], [1.2, 2.2]], scope["mach_intervals"])
        with self.assertRaisesRegex(ValueError, "Mach"):
            _validate_scope({"mach_intervals": [[1.0, 0.8]], "alpha_deg": [0, 5]})


class GeometrySemanticAuditTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_geometry_policy(POLICY_PATH)

    def test_canonical_suffixes_are_accepted(self):
        inventory = valid_inventory(
            component("go", "GO_L", "Wing", set_1=False, set_2=True),
            component("vo", "VO_upper", "Wing", set_1=False, set_2=True),
            component("pod", "Gondola_R", "Stack", set_1=True, set_2=False),
        )
        audit = semantic_audit(inventory, reference(), self.policy)
        self.assertTrue(audit["valid"])
        self.assertFalse(audit["findings"])
        sources = {item["name"]: item["semantic_source"] for item in audit["recognized_components"]}
        self.assertEqual("canonical_suffix", sources["VO_upper"])

    def test_explicit_alias_is_accepted_but_never_guessed(self):
        policy = effective_policy(self.policy, {"semantics": {"aliases": {"TailLeft": "VO"}}})
        inventory = valid_inventory(
            component("alias", "TailLeft", "Wing", set_1=False, set_2=True),
            component("unknown", "Canard", "Wing", set_1=False, set_2=True),
        )
        audit = semantic_audit(inventory, reference(), policy)
        alias = next(item for item in audit["recognized_components"] if item["name"] == "TailLeft")
        self.assertEqual("explicit_alias", alias["semantic_source"])
        self.assertEqual("VO", alias["semantic_base"])
        unknown = [item for item in audit["findings"] if item["code"] == "GEO-NAME-001"]
        self.assertEqual(["Canard"], [item["component"] for item in unknown])
        self.assertFalse(audit["valid"])

    def test_duplicate_exact_name_is_a_blocker(self):
        inventory = valid_inventory(
            component("wing2", "Wing", "Wing", set_1=False, set_2=True),
        )
        audit = semantic_audit(inventory, reference(), self.policy)
        duplicate = [item for item in audit["findings"] if item["code"] == "GEO-NAME-002"]
        self.assertEqual(1, len(duplicate))
        self.assertEqual(2, duplicate[0]["evidence"]["count"])
        self.assertFalse(audit["valid"])

    def test_missing_required_component_is_a_blocker(self):
        inventory = {"components": [
            component("wing", "Wing", "Wing", set_1=False, set_2=True),
        ]}
        audit = semantic_audit(inventory, reference(), self.policy)
        missing = [item for item in audit["findings"] if item["code"] == "GEO-NAME-003"]
        self.assertEqual(["Fuselage"], [item["component"] for item in missing])

    def test_wrong_set_is_repairable_and_role_is_recorded(self):
        inventory = valid_inventory(
            component("go", "GO", "Wing", set_1=True, set_2=False),
        )
        audit = semantic_audit(inventory, reference(), self.policy)
        self.assertTrue(audit["valid"])
        issue = next(item for item in audit["findings"] if item.get("component") == "GO")
        self.assertEqual("REPAIRABLE", issue["severity"])
        go = next(item for item in audit["recognized_components"] if item["name"] == "GO")
        self.assertEqual("thin", go["semantic_role"])

    def test_thin_role_requires_openvsp_wing_type(self):
        inventory = valid_inventory(
            component("go", "GO", "Fuselage", set_1=False, set_2=True),
        )
        audit = semantic_audit(inventory, reference(), self.policy)
        self.assertFalse(audit["valid"])
        self.assertTrue(any(item["code"] == "GEO-SET-002" for item in audit["findings"]))

    def test_declared_exclusion_is_traceable_and_not_treated_as_unknown(self):
        policy = effective_policy(self.policy, {"semantics": {"declared_exclusions": [{
            "component": "Wing2",
            "backends": ["vspaero", "hybrid"],
            "reason": "hidden auxiliary geometry",
            "replacement_required": ["not_physical"],
        }]}})
        inventory = valid_inventory(
            component("aux", "Wing2", "Wing", set_1=False, set_2=False),
        )
        audit = semantic_audit(inventory, reference(), policy)
        self.assertTrue(audit["valid"])
        self.assertEqual("GEO-EXCL-001", audit["findings"][0]["code"])
        self.assertEqual("WARNING", audit["findings"][0]["severity"])
        self.assertFalse(any(item["code"] == "GEO-NAME-001" for item in audit["findings"]))

    def test_declared_exclusion_must_resolve_to_exactly_one_inventory_component(self):
        policy = effective_policy(self.policy, {"semantics": {"declared_exclusions": [{
            "component": "Wing2",
            "backends": ["vspaero", "hybrid"],
            "reason": "reference-only construction surface",
            "replacement_required": ["not_physical"],
        }]}})

        missing = semantic_audit(valid_inventory(), reference(), policy)
        self.assertFalse(missing["valid"])
        issue = next(item for item in missing["findings"] if item["code"] == "GEO-EXCL-002")
        self.assertEqual(0, issue["evidence"]["count"])

        duplicate = semantic_audit(valid_inventory(
            component("aux1", "Wing2", "Wing", set_1=False, set_2=False),
            component("aux2", "Wing2", "Wing", set_1=False, set_2=False),
        ), reference(), policy)
        self.assertFalse(duplicate["valid"])
        issue = next(item for item in duplicate["findings"] if item["code"] == "GEO-EXCL-002")
        self.assertEqual(2, issue["evidence"]["count"])


class GeometryPlanTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_geometry_policy(POLICY_PATH)

    def test_plan_is_deterministic_and_bound_to_all_inputs(self):
        inventory = valid_inventory()
        first, bindings = plan_for(inventory, self.policy)
        second, _ = plan_for(inventory, self.policy)
        self.assertEqual(first, second)
        self.assertEqual(bindings, first["bindings"])
        self.assertEqual([], verify_plan(first, **bindings))

    def test_plan_tampering_is_detected_before_apply(self):
        inventory = valid_inventory(
            component("go", "GO", "Wing", set_1=True, set_2=False),
        )
        plan, bindings = plan_for(inventory, self.policy)
        self.assertTrue(plan["actions"])
        tampered = deepcopy(plan)
        tampered["actions"][0]["expected_user_set"] = 99
        errors = verify_plan(tampered, **bindings)
        self.assertTrue(any("Хэш плана" in item for item in errors))

    def test_plan_binding_change_is_detected(self):
        plan, bindings = plan_for(valid_inventory(), self.policy)
        changed = dict(bindings)
        changed["master_sha256"] = "different-master"
        errors = verify_plan(plan, **changed)
        self.assertTrue(any("master_sha256" in item for item in errors))

    def test_blocked_semantic_plan_can_never_be_applied(self):
        inventory = valid_inventory(
            component("wing2", "Wing", "Wing", set_1=False, set_2=True),
        )
        plan, bindings = plan_for(inventory, self.policy)
        self.assertTrue(plan["blocked"])
        self.assertIn("План содержит блокирующие дефекты", verify_plan(plan, **bindings))

    def test_zero_tip_is_regularized_only_in_solver_twins(self):
        terminal = {
            "tip_parm_id": "tip-id",
            "tip_chord": 0.0,
            "root_parm_id": "root-id",
            "root_chord": 1.0,
            "span_parm_id": "span-id",
            "span": 2.0,
            "xsec_count": 3,
        }
        inventory = valid_inventory()
        inventory["components"][1]["wing_terminal"] = terminal
        plan, _ = plan_for(inventory, self.policy)
        action = next(item for item in plan["actions"] if item["reason_code"] == "GEO-TIP-001")
        self.assertEqual(0.01, action["after"])
        self.assertEqual(["vspaero_mixed", "vspaero_lifting", "machline"], action["target_twins"])
        self.assertEqual(0.0, inventory["components"][1]["wing_terminal"]["tip_chord"])

    def test_zero_tip_over_area_budget_blocks_plan_instead_of_forcing_change(self):
        terminal = {
            "tip_parm_id": "tip-id",
            "tip_chord": 0.0,
            "root_parm_id": "root-id",
            "root_chord": 1000.0,
            "span_parm_id": "span-id",
            "span": 1000.0,
            "xsec_count": 3,
        }
        inventory = valid_inventory()
        inventory["components"][1]["wing_terminal"] = terminal
        plan, bindings = plan_for(inventory, self.policy)
        self.assertTrue(plan["blocked"])
        self.assertTrue(any(item["code"] == "GEO-TIP-002" for item in plan["findings"]))
        self.assertFalse(any(item.get("reason_code") == "GEO-TIP-001" for item in plan["actions"]))
        self.assertIn("План содержит блокирующие дефекты", verify_plan(plan, **bindings))

    def test_declared_exclusion_generates_vspaero_twin_clear_sets(self):
        policy = effective_policy(self.policy, {"semantics": {"declared_exclusions": [{
            "component": "Wing2",
            "backends": ["vspaero", "hybrid"],
            "reason": "not part of physical model",
            "replacement_required": ["not_physical"],
        }]}})
        inventory = valid_inventory(
            component("aux", "Wing2", "Wing", set_1=False, set_2=True),
        )
        plan, _ = plan_for(inventory, policy)
        action = next(item for item in plan["actions"] if item["action"] == "clear_sets")
        self.assertEqual(["vspaero_mixed", "vspaero_lifting"], action["target_twins"])
        self.assertEqual([1, 2], action["user_sets"])

    def test_transformation_plan_rejects_unsupported_backend_contract_if_validation_is_bypassed(self):
        inventory = valid_inventory(
            component("aux", "Wing2", "Wing", set_1=False, set_2=True),
        )
        for backends in ("vspaero", ["machline"], ["hybrid", "vspaero"]):
            with self.subTest(backends=backends):
                policy = deepcopy(self.policy)
                policy["semantics"]["declared_exclusions"] = [{
                    "component": "Wing2",
                    "backends": backends,
                    "reason": "malformed legacy declaration",
                }]
                plan, _ = plan_for(inventory, policy)
                self.assertTrue(plan["blocked"])
                self.assertTrue(any(
                    item["code"] == "GEO-EXCL-002" for item in plan["findings"]
                ))
                self.assertFalse(any(
                    item["action"] == "clear_sets" for item in plan["actions"]
                ))

    def test_apply_script_checks_parameter_binding_and_writes_new_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "master.vsp3"
            destination = root / "twin.vsp3"
            source.write_text("<VSP3/>", encoding="utf-8")
            script = root / "apply.vspscript"
            generate_apply_script(script, source, destination, [{
                "action": "set_parameter",
                "target_twins": ["machline"],
                "geom_id": "wing",
                "component": "Wing",
                "parm_id": "tip-id",
                "before": 0.0,
                "after": 0.01,
            }], twin_name="machline")
            text = script.read_text(encoding="utf-8")
            self.assertIn("REPAIRMACH_APPLY_ABORT=PARAMETER_BINDING", text)
            self.assertIn("REPAIRMACH_APPLY_ABORT=PARAMETER_NOT_APPLIED", text)
            self.assertGreater(
                text.index('GetParmVal( "tip-id" )', text.index('SetParmVal( "tip-id"')),
                text.index('SetParmVal( "tip-id"'),
            )
            self.assertGreater(
                text.index('Print( "REPAIRMACH_APPLY_OK=1" )'),
                text.rindex('Print( "OPENVSP_ERROR="'),
            )
            self.assertIn(str(destination.resolve()).replace("\\", "/"), text)
            self.assertNotEqual(source.resolve(), destination.resolve())

    def test_apply_model_actions_rejects_openvsp_error_after_false_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "master.vsp3"
            destination = root / "twin.vsp3"
            source.write_text("<VSP3/>", encoding="utf-8")
            destination.write_text("stale", encoding="utf-8")
            action = {
                "action": "set_parameter",
                "target_twins": ["machline"],
                "geom_id": "wing",
                "component": "Wing",
                "parm_id": "tip-id",
                "before": 0.0,
                "after": 0.01,
            }

            def fake_run(_exe, _script, log, **_kwargs):
                self.assertFalse(destination.exists())
                destination.write_text("new twin", encoding="utf-8")
                Path(log).write_text(
                    "APPLIED;ACTION=set_parameter;PARM_ID=tip-id;VALUE=0.01\n"
                    "REPAIRMACH_APPLY_OK=1\n"
                    "OPENVSP_ERROR=SetParmVal was ignored\n",
                    encoding="utf-8",
                )
                return 0

            with patch("geometry_twins.run_vspscript", side_effect=fake_run):
                record = apply_model_actions(
                    source_path=source,
                    output_path=destination,
                    actions=[action],
                    twin_name="machline",
                    vspscript_executable=root / "vspscript.exe",
                    work_dir=root / "work",
                )
            self.assertFalse(record["valid"])
            self.assertTrue(any("OpenVSP сообщил ошибки" in item for item in record["errors"]))

    def test_apply_model_actions_requires_every_applied_confirmation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "master.vsp3"
            destination = root / "twin.vsp3"
            source.write_text("<VSP3/>", encoding="utf-8")

            def fake_run(_exe, _script, log, **_kwargs):
                destination.write_text("new twin", encoding="utf-8")
                Path(log).write_text("REPAIRMACH_APPLY_OK=1\n", encoding="utf-8")
                return 0

            with patch("geometry_twins.run_vspscript", side_effect=fake_run):
                record = apply_model_actions(
                    source_path=source,
                    output_path=destination,
                    actions=[{
                        "action": "set_parameter",
                        "target_twins": ["machline"],
                        "geom_id": "wing",
                        "component": "Wing",
                        "parm_id": "tip-id",
                        "before": 0.0,
                        "after": 0.01,
                    }],
                    twin_name="machline",
                    vspscript_executable=root / "vspscript.exe",
                    work_dir=root / "work",
                )
            self.assertFalse(record["valid"])
            self.assertEqual(1, record["expected_applied_actions"])
            self.assertEqual(0, record["confirmed_applied_actions"])

    def test_mesh_actions_use_audited_semantic_role(self):
        parameters = [
            {"id": "w", "name": "Tess_W", "group": "Shape", "value": 10.0},
            {"id": "u", "name": "Tess_U", "group": "Shape", "value": 10.0},
            {"id": "s", "name": "SectTess_U", "group": "Shape", "value": 3.0},
        ]
        inventory = valid_inventory()
        inventory["components"][1]["parameters"] = parameters
        audit = semantic_audit(inventory, reference(), self.policy)
        actions = mesh_actions(inventory, audit, self.policy["mesh_levels"]["coarse"], "vspaero_mixed")
        after = {item["parm_name"]: item["after"] for item in actions}
        self.assertEqual({"Tess_W": 33, "Tess_U": 12, "SectTess_U": 6}, after)

    def test_mesh_inventory_must_match_persisted_policy_values(self):
        def parms(prefix, tess_w, tess_u, sect):
            return [
                {"id": prefix + "w", "name": "Tess_W", "group": "Shape", "value": tess_w},
                {"id": prefix + "u", "name": "Tess_U", "group": "Shape", "value": tess_u},
                {"id": prefix + "s", "name": "SectTess_U", "group": "XSec", "value": sect},
            ]

        inventory = valid_inventory()
        inventory["components"][0]["parameters"] = parms("f", 25, 31, 6)
        inventory["components"][1]["parameters"] = parms("w", 33, 12, 6)
        audit = semantic_audit(inventory, reference(), self.policy)
        verification = verify_mesh_inventory(
            inventory, audit, self.policy["mesh_levels"]["coarse"]
        )
        self.assertTrue(verification["valid"])
        self.assertTrue(verification["signature_sha256"])

        ignored_apply = deepcopy(inventory)
        ignored_apply["components"][1]["parameters"][0]["value"] = 10
        ignored_audit = semantic_audit(ignored_apply, reference(), self.policy)
        rejected = verify_mesh_inventory(
            ignored_apply, ignored_audit, self.policy["mesh_levels"]["coarse"]
        )
        self.assertFalse(rejected["valid"])
        self.assertTrue(any("Tess_W=10" in item for item in rejected["errors"]))

    def test_vspaero_ladder_does_not_run_on_unverified_mesh_twins(self):
        meshes = {
            level: {
                "path": f"{level}.vsp3",
                "valid": level != "medium",
                "mesh_verification": {"valid": level != "medium"},
            }
            for level in ("coarse", "medium", "fine")
        }
        with TemporaryDirectory() as tmp, patch(
            "geometry_certification._run_one_vspaero_probe"
        ) as probe:
            result = _run_vspaero_ladder(
                mode="mixed",
                meshes=meshes,
                run_dir=Path(tmp),
                reference=reference(),
                policy=self.policy,
                executable=Path(tmp) / "vspscript.exe",
            )
        probe.assert_not_called()
        self.assertFalse(result["valid"])
        self.assertFalse(result["converged"])
        self.assertIn("medium", result["convergence"]["errors"][0])

    def test_vspaero_ladder_stops_after_first_invalid_solver_level(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            failed_probe = {
                "level": "coarse",
                "valid": False,
                "values": {},
                "errors": ["timeout"],
            }
            with patch(
                "geometry_certification._run_one_vspaero_probe",
                return_value=failed_probe,
            ) as probe, patch(
                "geometry_certification._run_vspaero_component_isolation",
                return_value={
                    "attempted": True,
                    "groups_whose_exclusion_restored_solution": ["VO"],
                },
            ) as isolate:
                result = _run_vspaero_ladder(
                    mode="mixed",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )
            self.assertEqual(1, probe.call_count)
            isolate.assert_called_once()
            self.assertEqual(1, len(result["probes"]))
            self.assertFalse(result["valid"])
            self.assertFalse(result["converged"])
            self.assertEqual(
                ["VO"],
                result["failure_diagnostics"][
                    "groups_whose_exclusion_restored_solution"
                ],
            )

    def test_component_isolation_disables_fixed_tail_only_for_go_variant(self):
        failed_probe = {
            "valid": False,
            "set_validation": {
                "geometries": [
                    {"ID": "wing", "NAME": "Wing", "IN_SET_2": True},
                    {"ID": "go", "NAME": "GO", "IN_SET_2": True},
                    {"ID": "vo1", "NAME": "VO_upper", "IN_SET_2": True},
                    {"ID": "vo2", "NAME": "VO_lower", "IN_SET_2": True},
                ]
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.vsp3"
            model.write_text("fixture", encoding="utf-8")

            def fake_apply(*, output_path, **_kwargs):
                output_path = Path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("diagnostic", encoding="utf-8")
                return {"valid": True, "path": str(output_path)}

            with patch(
                "geometry_certification.apply_model_actions",
                side_effect=fake_apply,
            ), patch(
                "geometry_certification._run_one_vspaero_probe",
                return_value={"valid": False},
            ) as probe:
                result = _run_vspaero_component_isolation(
                    failed_probe=failed_probe,
                    model=model,
                    diagnostic_root=root / "isolation",
                    mode="lifting",
                    level="medium",
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                    mach=1.7,
                    alpha_deg=1.0,
                    engine_boundary="model",
                )

        self.assertTrue(result["attempted"])
        calls = {
            item.kwargs["probe_dir"].parent.name: item.kwargs
            for item in probe.call_args_list
        }
        self.assertTrue(calls["no_GO"]["diagnostic_ignore_fixed_tail"])
        self.assertFalse(calls["no_VO"]["diagnostic_ignore_fixed_tail"])

    def test_vspaero_ladder_uses_extra_fine_only_for_monotonic_marginal_failure(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine", "extra_fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            values = {
                "coarse": {"CLtot": 0.3333, "CDtot": 0.02236},
                "medium": {"CLtot": 0.2965, "CDtot": 0.01909},
                "fine": {"CLtot": 0.2964, "CDtot": 0.01805},
                "extra_fine": {"CLtot": 0.29635, "CDtot": 0.01780},
            }

            def make_probe(**kwargs):
                level = kwargs["level"]
                return {"level": level, "valid": True, "values": values[level]}

            with patch(
                "geometry_certification._run_one_vspaero_probe",
                side_effect=make_probe,
            ) as probe:
                result = _run_vspaero_ladder(
                    mode="lifting",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )
            self.assertEqual(4, probe.call_count)
            self.assertTrue(result["valid"])
            self.assertTrue(result["converged"])
            self.assertTrue(result["convergence"]["adaptive_refinement_attempted"])
            self.assertEqual(
                ["medium", "fine", "extra_fine"],
                result["convergence"]["level_sequence"],
            )

    def test_vspaero_ladder_recovers_residual_outlier_with_two_strict_repeats(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            base_values = {
                "coarse": {"CLtot": 0.18880, "CDtot": 0.02268},
                "medium": {"CLtot": 0.19492, "CDtot": 0.02340},
            }
            repeat_values = [
                {"CLtot": 0.1980662, "CDtot": 0.0237809},
                {"CLtot": 0.1980663, "CDtot": 0.0237810},
            ]
            repeat_index = 0

            def make_probe(**kwargs):
                nonlocal repeat_index
                level = kwargs["level"]
                if level in base_values:
                    return {"level": level, "valid": True, "values": base_values[level]}
                if level == "fine":
                    return {
                        "level": level,
                        "valid": False,
                        "finite_rows": 1,
                        "values": {"CLtot": 0.1964, "CDtot": 0.0418},
                        "output_quality_failure_kind": "residual",
                        "health_errors": [],
                        "condition_errors": [],
                        "set_validation": {"valid": True, "calculation_complete": True},
                    }
                values = repeat_values[repeat_index]
                repeat_index += 1
                return {"level": level, "valid": True, "values": values}

            with patch(
                "geometry_certification._run_one_vspaero_probe",
                side_effect=make_probe,
            ) as probe:
                result = _run_vspaero_ladder(
                    mode="lifting",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )

            self.assertEqual(5, probe.call_count)
            self.assertTrue(result["valid"])
            self.assertTrue(result["converged"])
            recovered = result["probes"][-1]["numerical_recovery"]
            self.assertTrue(recovered["valid"])
            self.assertEqual(2, len(recovered["repeat_probes"]))
            self.assertFalse(recovered["rejected_probe"]["valid"])
            self.assertEqual(1, recovered["controls"]["ncpu"])

    def test_vspaero_ladder_uses_extra_fine_to_resolve_sub_tolerance_oscillation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine", "extra_fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            values = {
                "coarse": {"CLtot": 0.41461, "CDtot": 0.03120},
                "medium": {"CLtot": 0.35225, "CDtot": 0.02405},
                "fine": {"CLtot": 0.35404, "CDtot": 0.02238},
                "extra_fine": {"CLtot": 0.35380, "CDtot": 0.02190},
            }

            def make_probe(**kwargs):
                level = kwargs["level"]
                return {"level": level, "valid": True, "values": values[level]}

            with patch(
                "geometry_certification._run_one_vspaero_probe",
                side_effect=make_probe,
            ) as probe:
                result = _run_vspaero_ladder(
                    mode="lifting",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )
            self.assertEqual(4, probe.call_count)
            self.assertTrue(result["valid"])
            self.assertTrue(result["converged"])
            self.assertTrue(result["convergence"]["adaptive_refinement_attempted"])
            self.assertTrue(result["convergence"]["adaptive_recoverable_oscillation"])
            self.assertTrue(result["convergence"]["initial_convergence"]["oscillating"])

    def test_vspaero_ladder_recovers_verified_local_span_plateau_after_dense_nan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine", "extra_fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_policy": deepcopy(self.policy["mesh_levels"][
                        level if level in self.policy["mesh_levels"] else "fine"
                    ]),
                    "post_inventory": str(root / f"{level}.json"),
                    "mesh_verification": {"valid": True},
                }
            local_meshes = {}
            for level in ("local_span_m8", "local_span_m4"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                local_meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            values = {
                "coarse": {"CLtot": 0.32938, "CDtot": 0.03544},
                "medium": {"CLtot": 0.35252, "CDtot": 0.03775},
                "fine": {"CLtot": 0.37617, "CDtot": 0.04009},
                "local_span_m8": {"CLtot": 0.37482, "CDtot": 0.039963},
                "local_span_m4": {"CLtot": 0.37459, "CDtot": 0.039941},
            }

            def make_probe(**kwargs):
                level = kwargs["level"]
                if level == "extra_fine":
                    return {
                        "level": level,
                        "valid": False,
                        "values": None,
                        "health_errors": ["nan_or_inf"],
                        "output_quality": {
                            "errors": ["nonfinite_solver"],
                        },
                    }
                return {"level": level, "valid": True, "values": values[level]}

            plateau = {
                "valid": True,
                "levels": ["local_span_m8", "local_span_m4", "fine"],
                "meshes": local_meshes,
                "errors": [],
                "axis": "thin_tess_w",
            }
            with patch(
                "geometry_certification._run_one_vspaero_probe",
                side_effect=make_probe,
            ) as probe, patch(
                "geometry_certification._build_local_span_plateau_meshes",
                return_value=plateau,
            ) as build_plateau:
                result = _run_vspaero_ladder(
                    mode="lifting",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )

            self.assertEqual(6, probe.call_count)
            build_plateau.assert_called_once()
            self.assertTrue(result["valid"])
            self.assertTrue(result["converged"])
            self.assertTrue(result["convergence"]["local_span_plateau_attempted"])
            self.assertEqual(
                ["local_span_m8", "local_span_m4", "fine"],
                result["convergence"]["level_sequence"],
            )
            self.assertFalse(
                result["convergence"]["rejected_dense_refinement"]["valid"]
            )

    def test_vspaero_ladder_does_not_use_local_plateau_for_non_nan_failure(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine", "extra_fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            values = {
                "coarse": {"CLtot": 0.32938, "CDtot": 0.03544},
                "medium": {"CLtot": 0.35252, "CDtot": 0.03775},
                "fine": {"CLtot": 0.37617, "CDtot": 0.04009},
            }

            def make_probe(**kwargs):
                level = kwargs["level"]
                if level == "extra_fine":
                    return {
                        "level": level,
                        "valid": False,
                        "values": None,
                        "health_errors": ["timeout"],
                        "output_quality": {"errors": ["missing_output"]},
                    }
                return {"level": level, "valid": True, "values": values[level]}

            with patch(
                "geometry_certification._run_one_vspaero_probe",
                side_effect=make_probe,
            ), patch(
                "geometry_certification._build_local_span_plateau_meshes"
            ) as build_plateau:
                result = _run_vspaero_ladder(
                    mode="lifting",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )

            build_plateau.assert_not_called()
            self.assertFalse(result["valid"])
            self.assertFalse(result["converged"])
            self.assertFalse(result["convergence"]["local_span_plateau_attempted"])

    def test_vspaero_ladder_uses_ultra_fine_only_for_unresolved_bounded_oscillation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meshes = {}
            for level in ("coarse", "medium", "fine", "extra_fine", "ultra_fine"):
                model = root / f"{level}.vsp3"
                model.write_text(level, encoding="utf-8")
                meshes[level] = {
                    "path": str(model),
                    "valid": True,
                    "mesh_verification": {"valid": True},
                }
            values = {
                "coarse": {"CLtot": 0.41461, "CDtot": 0.03120},
                "medium": {"CLtot": 0.35225, "CDtot": 0.02405},
                "fine": {"CLtot": 0.35404, "CDtot": 0.02238},
                "extra_fine": {"CLtot": 0.34305, "CDtot": 0.02199},
                "ultra_fine": {"CLtot": 0.34270, "CDtot": 0.02180},
            }

            def make_probe(**kwargs):
                level = kwargs["level"]
                return {"level": level, "valid": True, "values": values[level]}

            with patch(
                "geometry_certification._run_one_vspaero_probe",
                side_effect=make_probe,
            ) as probe:
                result = _run_vspaero_ladder(
                    mode="lifting",
                    meshes=meshes,
                    run_dir=root,
                    reference=reference(),
                    policy=self.policy,
                    executable=root / "vspscript.exe",
                )
            self.assertEqual(5, probe.call_count)
            self.assertTrue(result["valid"])
            self.assertTrue(result["converged"])
            self.assertTrue(result["convergence"]["oscillation_resolution_attempted"])
            self.assertEqual(
                ["fine", "extra_fine", "ultra_fine"],
                result["convergence"]["level_sequence"],
            )
            self.assertTrue(
                result["convergence"]["first_adaptive_convergence"]["oscillating"]
            )

    def test_vspaero_qualification_stops_after_first_failed_anchor(self):
        failed_ladder = {
            "valid": False,
            "converged": False,
            "probes": [],
            "convergence": {"valid": False, "converged": False, "errors": ["timeout"]},
        }
        with TemporaryDirectory() as tmp, patch(
            "geometry_certification._run_vspaero_ladder",
            return_value=failed_ladder,
        ) as ladder:
            result = _run_vspaero_qualification(
                mode="mixed",
                meshes={},
                run_dir=Path(tmp),
                reference=reference(),
                policy=self.policy,
                executable=Path(tmp) / "vspscript.exe",
            )
        self.assertEqual(1, ladder.call_count)
        self.assertEqual(1, len(result["anchors"]))
        self.assertTrue(result["convergence"]["aborted_after_failure"])
        self.assertGreater(result["convergence"]["expected_anchor_count"], 1)


class GeometryManifestAndDeltaTests(unittest.TestCase):
    def test_human_certificate_report_lists_component_replacement_contract(self):
        certificate = {
            "certificate_id": "RMC-REPLACEMENTS",
            "method_version": "test",
            "verdict": "PASS_WITH_DECLARED_EXCLUSIONS",
            "flags": {"hybrid_substitution_required": True},
            "master": {},
            "qualification": {},
            "backends": {
                "hybrid": {
                    "replacement_contract": {
                        "required": True,
                        "requirements": [{
                            "component": "VO",
                            "method": "machline_pressure_wave_all_points",
                            "backend_capability_available": False,
                            "reason": "component_absent_from_certified_machline_pressure_mesh",
                        }],
                        "unavailable_requirements": [{"component": "VO"}],
                    },
                },
            },
        }
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "certificate.md"
            write_certificate_report(report, certificate)
            text = report.read_text(encoding="utf-8")
        self.assertIn("Контракт замещающих вкладов", text)
        self.assertIn("| VO | machline_pressure_wave_all_points | нет |", text)
        self.assertIn("Гибрид нельзя считать полным", text)

    def test_human_certificate_report_includes_component_isolation(self):
        certificate = {
            "certificate_id": "RMC-TEST",
            "method_version": "test",
            "verdict": "FAIL",
            "flags": {},
            "master": {},
            "qualification": {},
            "backends": {},
            "probes": {
                "vspaero_lifting": {
                    "anchors": [{
                        "mach": 1.7,
                        "alpha_deg": 1.0,
                        "failure_diagnostics": {
                            "attempted": True,
                            "condition": {
                                "mach": 1.7,
                                "alpha_deg": 1.0,
                                "mesh_level": "medium",
                            },
                            "groups_whose_exclusion_restored_solution": ["VO"],
                            "variants": [{
                                "excluded_semantic_group": "VO",
                                "restored_finite_solution": True,
                                "probe": {
                                    "values": {
                                        "CLtot": 0.056,
                                        "CDtot": 0.007,
                                    }
                                },
                            }],
                        },
                    }]
                }
            },
        }
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "certificate.md"
            write_certificate_report(report, certificate)
            text = report.read_text(encoding="utf-8")
        self.assertIn("Автоматическая локализация", text)
        self.assertIn("| VO | конечное решение восстановлено |", text)
        self.assertIn("Локализованные группы: `VO`", text)
        self.assertIn("Нельзя выбирать единственную сетку", text)

    def test_human_certificate_report_discloses_local_span_plateau(self):
        certificate = {
            "certificate_id": "RMC-PLATEAU",
            "method_version": "test",
            "verdict": "PASS_WITH_DECLARED_EXCLUSIONS",
            "flags": {},
            "master": {},
            "qualification": {},
            "backends": {},
            "probes": {
                "vspaero_lifting": {
                    "anchors": [{
                        "mach": 1.2,
                        "alpha_deg": 5.0,
                        "convergence": {
                            "local_span_plateau_attempted": True,
                            "level_sequence": [
                                "local_span_m8",
                                "local_span_m4",
                                "fine",
                            ],
                            "quantities": {
                                "CLtot": {"medium_fine_relative": 0.004342},
                                "CDtot": {"medium_fine_relative": 0.003884},
                            },
                        },
                    }]
                }
            },
        }
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "certificate.md"
            write_certificate_report(report, certificate)
            text = report.read_text(encoding="utf-8")
        self.assertIn("Локальная сеточная полка", text)
        self.assertIn("local_span_m8, local_span_m4, fine", text)
        self.assertIn("0.434%", text)
        self.assertIn("0.388%", text)

    def test_raw_hash_detects_any_change(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "master.vsp3"
            path.write_bytes(b"first")
            before = sha256_file(path)
            path.write_bytes(b"second")
            self.assertNotEqual(before, sha256_file(path))

    def test_canonical_vsp_hash_ignores_only_transient_fixed_group_ids(self):
        first = """<VSP3><ParmContainer><Name>Fixed_Group</Name><ID>A</ID><ParmID>B</ParmID></ParmContainer><Ref>A</Ref><Node link=\"B\"/><StableID>KEEP</StableID></VSP3>"""
        second = """<VSP3><ParmContainer><Name>Fixed_Group</Name><ID>X</ID><ParmID>Y</ParmID></ParmContainer><Ref>X</Ref><Node link=\"Y\"/><StableID>KEEP</StableID></VSP3>"""
        changed = second.replace("KEEP", "CHANGED")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / name for name in ("first.vsp3", "second.vsp3", "changed.vsp3")]
            for path, text in zip(paths, (first, second, changed)):
                path.write_text(text, encoding="utf-8")
            self.assertNotEqual(sha256_file(paths[0]), sha256_file(paths[1]))
            self.assertEqual(canonical_vsp3_sha256(paths[0]), canonical_vsp3_sha256(paths[1]))
            self.assertNotEqual(canonical_vsp3_sha256(paths[1]), canonical_vsp3_sha256(paths[2]))

    def test_copy_passthrough_preserves_master_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / "master.vsp3"
            twin = root / "parasite" / "model.vsp3"
            master.write_bytes(b"immutable-model")
            master_hash = sha256_file(master)
            record = copy_passthrough(master, twin, "parasite")
            self.assertEqual(master_hash, sha256_file(master))
            self.assertEqual(master_hash, record["sha256"])
            self.assertEqual(master.read_bytes(), twin.read_bytes())

    def test_failed_certification_still_emits_failure_certificate_and_preserves_master(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / "master.vsp3"
            executable = root / "vspscript.exe"
            master.write_bytes(b"immutable-master")
            executable.write_bytes(b"fake executable")
            before = sha256_file(master)
            with patch("geometry_certification.inventory_model", side_effect=RuntimeError("probe failed")):
                result = certify_geometry(
                    master_path=master,
                    output_root=root / "certificates",
                    project_name="FailureCase",
                    reference=reference(),
                    policy_path=POLICY_PATH,
                    executables={"vspscript": executable, "vspaero": None, "machline": None},
                    run_vspaero_probes=False,
                )
            self.assertEqual(before, sha256_file(master))
            self.assertEqual("FAIL", result["certificate"]["verdict"])
            self.assertTrue(result["certificate"]["flags"]["master_unchanged"])
            self.assertTrue(result["certificate_path"].is_file())
            corrective_plan = result["certificate"]["corrective_action_plan"]
            self.assertEqual("blocked", corrective_plan["status"])
            self.assertTrue(corrective_plan["items"])
            self.assertTrue(
                (result["run_directory"] / "corrective_action_plan.json").is_file()
            )
            self.assertIn(
                "corrective_action_plan.json",
                [item["relative_path"] for item in result["certificate"]["evidence_files"]],
            )
            self.assertFalse((result["run_directory"] / ".certification.lock").exists())

    def test_machline_scoped_tri_blocker_does_not_revoke_qualified_vspaero(self):
        """A requested TRI failure is local to MachLine, not a global veto.

        This is an orchestration regression test rather than a hand-written
        certificate fixture: it exercises the final verdict aggregation after
        a successful VSPAERO mesh ladder and a blocked MachLine TRI.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / "master.vsp3"
            executable = root / "vspscript.exe"
            vspaero_executable = root / "vspaero.exe"
            tri = root / "broken.tri"
            master.write_text("<VSP3/>", encoding="utf-8")
            executable.write_bytes(b"fixture executable")
            vspaero_executable.write_bytes(b"fixture vspaero executable")
            tri.write_text("broken TRI fixture", encoding="utf-8")

            inventory = valid_inventory()
            inventory["openvsp_version"] = "3.51.0"

            def fake_inventory(model_path, **_kwargs):
                model_path = Path(model_path)
                return deepcopy(inventory), model_path.with_suffix(".vspscript"), model_path.with_suffix(".log")

            def fake_apply(*, source_path, output_path, twin_name, **_kwargs):
                source_path = Path(source_path)
                output_path = Path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(source_path.read_bytes())
                digest = sha256_file(output_path)
                return {
                    "name": twin_name,
                    "path": str(output_path.resolve()),
                    "sha256": digest,
                    "canonical_sha256": digest,
                    "valid": True,
                    "return_code": 0,
                    "script": None,
                    "log": None,
                    "errors": [],
                }

            ladder = {
                "mode": "mixed",
                "valid": True,
                "converged": True,
                "probes": [],
                "convergence": {"valid": True, "converged": True, "errors": []},
            }
            tri_blocker = {
                "code": "GEO-TRI-001",
                "severity": "BLOCKER",
                "message": "TRI topology is not MachLine-safe",
                "scope": "machline",
            }
            tri_result = {
                "requested": True,
                "eligible": False,
                "status": "blocked_topology",
            }

            def fake_mesh_verification(_inventory, _audit, expected):
                return {
                    "valid": True,
                    "components": [],
                    "parameter_signature": [],
                    "signature_sha256": sha256_payload(expected),
                    "errors": [],
                }

            with patch("geometry_certification.inventory_model", side_effect=fake_inventory), \
                    patch("geometry_certification.apply_model_actions", side_effect=fake_apply), \
                    patch("geometry_certification.verify_mesh_inventory", side_effect=fake_mesh_verification), \
                    patch("geometry_certification._run_vspaero_ladder", return_value=ladder), \
                    patch(
                        "geometry_certification._export_machline_tri",
                        return_value={"status": "passed", "export": {"path": str(tri.resolve())}},
                    ), \
                    patch(
                        "geometry_certification._tri_certification",
                        return_value=(tri_result, [tri_blocker]),
                    ) as tri_certification:
                result = certify_geometry(
                    master_path=master,
                    output_root=root / "certificates",
                    project_name="BackendScopedBlocker",
                    reference=reference(),
                    policy_path=POLICY_PATH,
                    executables={
                        "vspscript": executable,
                        "vspaero": vspaero_executable,
                        "machline": None,
                    },
                    tri_path=tri,
                    run_vspaero_probes=True,
                )

                missing_vspaero = certify_geometry(
                    master_path=master,
                    output_root=root / "certificates_missing_vspaero",
                    project_name="MissingVspaeroExecutable",
                    reference=reference(),
                    policy_path=POLICY_PATH,
                    executables={"vspscript": executable, "vspaero": None, "machline": None},
                    tri_path=tri,
                    run_vspaero_probes=True,
                )

                tri_certification.return_value = ({
                    "requested": True,
                    "eligible": True,
                    "status": "passed",
                    "certified_tri": str(tri.resolve()),
                    "certified_tri_sha256": sha256_file(tri),
                }, [])
                missing_machline = certify_geometry(
                    master_path=master,
                    output_root=root / "certificates_missing_machline",
                    project_name="MissingMachlineExecutable",
                    reference=reference(),
                    policy_path=POLICY_PATH,
                    executables={
                        "vspscript": executable,
                        "vspaero": vspaero_executable,
                        "machline": None,
                    },
                    tri_path=tri,
                    run_vspaero_probes=True,
                )

            certificate = result["certificate"]
            self.assertNotEqual("FAIL", certificate["verdict"])
            self.assertTrue(certificate["backends"]["vspaero"]["eligible"])
            self.assertFalse(certificate["backends"]["machline"]["eligible"])
            self.assertTrue(any(
                item.get("severity") == "BLOCKER" and item.get("scope") == "machline"
                for item in certificate["findings"]
            ))
            self.assertEqual("FAIL", missing_vspaero["certificate"]["verdict"])
            self.assertFalse(missing_vspaero["certificate"]["backends"]["vspaero"]["eligible"])
            self.assertTrue(any(
                item.get("code") == "GEO-SOLVER-002"
                for item in missing_vspaero["certificate"]["findings"]
            ))
            self.assertNotEqual("FAIL", missing_machline["certificate"]["verdict"])
            self.assertTrue(missing_machline["certificate"]["backends"]["vspaero"]["eligible"])
            self.assertFalse(missing_machline["certificate"]["backends"]["machline"]["eligible"])
            self.assertTrue(any(
                item.get("code") == "GEO-SOLVER-003"
                for item in missing_machline["certificate"]["findings"]
            ))

    def test_inventory_parser_records_sets_selected_parameters_and_terminal_xsec(self):
        log = """
noise
REPAIRMACH_INVENTORY_BEGIN
GEOM;ID=g1;NAME=Wing;TYPE=Wing;BMIN_X=0;BMIN_Y=-2;BMIN_Z=0;BMAX_X=4;BMAX_Y=2;BMAX_Z=0.2;SET_1=false;SET_2=true
PARM;GEOM_ID=g1;ID=p1;NAME=Tess_W;GROUP=Shape;VALUE=33
PARM;GEOM_ID=g1;ID=p2;NAME=Ignored_Parm;GROUP=Shape;VALUE=999
WING_TERMINAL;GEOM_ID=g1;XSEC_COUNT=3;TIP_ID=tip;TIP=0;ROOT_ID=root;ROOT=1.5;SPAN_ID=span;SPAN=4
REPAIRMACH_INVENTORY_END
"""
        inventory = parse_inventory_log(log)
        self.assertTrue(inventory["valid"])
        wing = inventory["components"][0]
        self.assertEqual({"1": False, "2": True}, wing["sets"])
        self.assertEqual(["Tess_W"], [item["name"] for item in wing["parameters"]])
        self.assertEqual(0.0, wing["wing_terminal"]["tip_chord"])

    def test_geometry_deltas_tolerate_unknown_surface_counts(self):
        before = valid_inventory()
        after = deepcopy(before)
        before["components"][0]["total_surfaces"] = None
        after["components"][0]["total_surfaces"] = None
        delta = compute_geometry_deltas(before, {"twin": after}, [], reference())
        self.assertEqual(1, delta["twins"]["twin"]["surface_count_before"])
        self.assertEqual(1, delta["twins"]["twin"]["surface_count_after"])

    def test_delta_budget_accepts_boundary_and_rejects_excess(self):
        policy = load_geometry_policy(POLICY_PATH)
        delta = {
            "summary": {
                "estimated_delta_s_over_sref": policy["regularization"]["budgets"]["max_area_delta_fraction"],
                "max_parameter_displacement_over_cref": policy["regularization"]["budgets"]["max_displacement_over_cref"],
            }
        }
        self.assertEqual((True, []), delta_within_policy(delta, policy))
        delta["summary"]["max_parameter_displacement_over_cref"] += 1.0e-6
        valid, errors = delta_within_policy(delta, policy)
        self.assertFalse(valid)
        self.assertTrue(any("max_parameter_displacement" in item for item in errors))


class GeometryCertificateTests(unittest.TestCase):
    @staticmethod
    def _scenario() -> dict:
        return {
            "id": "geometry_anchor_check",
            "title": "Geometry anchor check",
            "description": "Regression fixture for a sealed scenario",
            "availability": "ready",
            "execution": "vspaero_study",
            "beta_deg": 0.0,
            "tail_incidence": "fixed",
            "tail_geometry_name": "GO",
            "tail_incidence_deg": 0.0,
            "vspaero_cases": [{
                "name": "anchor",
                "mach_start": 0.8,
                "mach_end": 0.8,
                "mach_points": 1,
                "alpha_start": 1.0,
                "alpha_end": 1.0,
                "alpha_points": 1,
                "engine_boundary": "model",
            }],
        }

    def _certificate(self, master: Path, twin: Path) -> dict:
        vspscript = master.parent / "vspscript.exe"
        vspaero = master.parent / "vspaero.exe"
        if not vspscript.exists():
            vspscript.write_bytes(b"test vspscript executable")
        if not vspaero.exists():
            vspaero.write_bytes(b"test vspaero executable")
        requested_scope = {
            "mach_intervals": [[0.0, 0.8], [1.2, 2.2]],
            "alpha_deg": [0.0, 5.0],
            "beta_deg": 0.0,
        }
        anchor = {
            "mach": 0.8,
            "alpha_deg": 1.0,
            "beta_deg": 0.0,
            "mode": "mixed",
            "mesh_levels": ["coarse", "medium", "fine"],
            "converged": True,
        }
        return seal_certificate({
            "schema": CERTIFICATE_SCHEMA,
            "method_version": "RM91-GEOMETRY-CERT-1",
            "run_status": "complete",
            "verdict": "PASS_NATIVE",
            "flags": {
                "master_unchanged": True,
                "solver_eligible": True,
            },
            "master": {
                "sha256_before": sha256_file(master),
                "sha256_after": sha256_file(master),
                "unchanged": True,
            },
            # ``scope`` is retained as the 9.1 compatibility alias.  New
            # consumers must gate runs against the backend-qualified scope,
            # not this broad user request.
            "scope": requested_scope,
            "requested_scope": requested_scope,
            "qualification": {
                "profile": "single_anchor_regression_fixture",
                "anchors": [anchor],
            },
            "software": {"solvers": {
                "openvsp": {
                    "version": "3.51.0",
                    "executable": str(vspscript.resolve()),
                    "sha256": sha256_file(vspscript),
                },
                "vspaero": {
                    "version": "3.51.0",
                    "executable": str(vspaero.resolve()),
                    "sha256": sha256_file(vspaero),
                },
            }},
            "backends": {"vspaero": {
                "eligible": True,
                "mode": "mixed",
                "blockers": [],
                "solver_geometry": {
                    "path": str(twin.resolve()),
                    "relative_path": twin.name,
                    "sha256": sha256_file(twin),
                },
                "qualified_scope": {
                    "coverage_kind": "exact_points",
                    "points": [{
                        "mach": anchor["mach"],
                        "alpha_deg": anchor["alpha_deg"],
                        "beta_deg": anchor["beta_deg"],
                    }],
                },
            }},
            "twins": {"vspaero_mixed": {
                "path": str(twin.resolve()),
                "relative_path": twin.name,
                "sha256": sha256_file(twin),
            }},
            "eligible_scenarios": ["geometry_anchor_check"],
            "scenario_eligibility": {
                "geometry_anchor_check": {
                    "eligible": True,
                    "backend": "vspaero",
                    "coverage_kind": "exact_points",
                    "scenario_sha256": sha256_payload(self._scenario()),
                },
            },
        })

    def _write_certificate(self, root: Path, certificate: dict) -> Path:
        path = root / "certificate.json"
        path.write_text(json.dumps(certificate, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _reseal(certificate: dict) -> dict:
        unsealed = deepcopy(certificate)
        unsealed.pop("certificate_id", None)
        unsealed.pop("certificate_fingerprint", None)
        return seal_certificate(unsealed)

    def test_seal_is_deterministic_and_integrity_check_detects_tampering(self):
        payload = {"schema": CERTIFICATE_SCHEMA, "verdict": "PASS_NATIVE", "value": 1}
        certificate = seal_certificate(payload)
        self.assertEqual(certificate, seal_certificate(payload))
        self.assertEqual([], certificate_integrity_errors(certificate))
        certificate["value"] = 2
        self.assertTrue(any("целостность" in item for item in certificate_integrity_errors(certificate)))

    def test_valid_certificate_checks_master_backend_scope_scenario_and_solver_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            result = verify_certificate(
                path,
                master_path=master,
                backend="vspaero",
                solver_versions={"openvsp": "3.51.0", "vspaero": "3.51.0"},
                runtime_executables={
                    "openvsp": root / "vspscript.exe",
                    "vspaero": root / "vspaero.exe",
                },
                mach=0.8,
                alpha_deg=1.0,
                scenario_id="geometry_anchor_check",
                scenario=self._scenario(),
            )
            self.assertTrue(result["valid"], result["errors"])

    def test_verifier_fails_closed_on_status_master_and_backend_inconsistency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            mutations = (
                ("run status", lambda item: item.__setitem__("run_status", "failed"), "статуса complete"),
                ("master record", lambda item: item["master"].__setitem__("unchanged", False), "неизменность MASTER"),
                ("master flag", lambda item: item["flags"].__setitem__("master_unchanged", False), "Флаг неизменности"),
                ("backend blockers", lambda item: item["backends"]["vspaero"].__setitem__(
                    "blockers", [{"severity": "BLOCKER", "scope": "vspaero"}]
                ), "blockers"),
                ("eligible mismatch", lambda item: item["flags"].__setitem__("solver_eligible", False), "solver_eligible"),
            )
            for name, mutate, expected in mutations:
                with self.subTest(name=name):
                    certificate = self._certificate(master, twin)
                    mutate(certificate)
                    path = self._write_certificate(root, self._reseal(certificate))
                    result = verify_certificate(path, backend="vspaero")
                    self.assertFalse(result["valid"])
                    self.assertIn(expected, " ".join(result["errors"]))

    def test_verifier_rejects_pass_verdict_with_global_or_backend_blocker(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            for scope in ("master", "vspaero_probe"):
                with self.subTest(scope=scope):
                    certificate = self._certificate(master, twin)
                    certificate["findings"] = [{
                        "code": "TEST-BLOCKER",
                        "severity": "BLOCKER",
                        "scope": scope,
                    }]
                    path = self._write_certificate(root, self._reseal(certificate))
                    result = verify_certificate(path, backend="vspaero")
                    self.assertFalse(result["valid"])
                    self.assertIn("блокир", " ".join(result["errors"]).lower())

    def test_positive_certificate_requires_vspaero_capability_and_master_sha256(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")

            missing_backend = self._certificate(master, twin)
            missing_backend["backends"].pop("vspaero")
            missing_backend["flags"]["solver_eligible"] = False
            path = self._write_certificate(root, self._reseal(missing_backend))
            result = verify_certificate(path)
            self.assertFalse(result["valid"])
            self.assertIn("backend VSPAERO", " ".join(result["errors"]))

            invalid_master = self._certificate(master, twin)
            invalid_master["master"]["sha256_before"] = "not-a-sha256"
            invalid_master["master"]["sha256_after"] = "not-a-sha256"
            path = self._write_certificate(root, self._reseal(invalid_master))
            result = verify_certificate(path)
            self.assertFalse(result["valid"])
            self.assertIn("SHA-256 MASTER", " ".join(result["errors"]))

    def test_backend_scope_is_limited_to_actually_qualified_anchor_points(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            self.assertTrue(
                verify_certificate(path, backend="vspaero", mach=0.8, alpha_deg=1.0)["valid"]
            )
            # M=2.2 is inside requested_scope but was never qualified by a
            # converged anchor.  A certificate must not silently extrapolate.
            result = verify_certificate(path, backend="vspaero", mach=2.2, alpha_deg=1.0)
            self.assertFalse(result["valid"])
            self.assertTrue(any("области" in item or "квалифиц" in item for item in result["errors"]))

    def test_evidence_file_tampering_invalidates_certificate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            evidence = root / "fine_probe.polar"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            evidence.write_bytes(b"qualified evidence")
            certificate = self._certificate(master, twin)
            unsealed = deepcopy(certificate)
            unsealed.pop("certificate_id", None)
            unsealed.pop("certificate_fingerprint", None)
            unsealed["evidence_files"] = [{
                "role": "vspaero_mixed_fine_polar",
                "relative_path": evidence.name,
                "path": str(evidence.resolve()),
                "sha256": sha256_file(evidence),
            }]
            path = self._write_certificate(root, seal_certificate(unsealed))
            self.assertTrue(verify_certificate(path, backend="vspaero")["valid"])

            evidence.write_bytes(b"tampered after certification")
            result = verify_certificate(path, backend="vspaero")
            self.assertFalse(result["valid"])
            self.assertIn("vspaero_mixed_fine_polar", " ".join(result["errors"]))

    def test_solver_version_change_invalidates_certificate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            result = verify_certificate(path, solver_versions={"openvsp": "3.52.0"})
            self.assertFalse(result["valid"])
            self.assertTrue(any("Версия openvsp" in item for item in result["errors"]))

    def test_solver_binary_change_invalidates_backend_certificate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            (root / "vspaero.exe").write_bytes(b"replaced executable")
            result = verify_certificate(
                path,
                backend="vspaero",
                runtime_executables={
                    "openvsp": root / "vspscript.exe",
                    "vspaero": root / "vspaero.exe",
                },
            )
            self.assertFalse(result["valid"])
            self.assertTrue(any("vspaero" in item and "измен" in item for item in result["errors"]))

    def test_runtime_executables_are_mandatory_when_runtime_check_is_requested(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            result = verify_certificate(
                path,
                backend="vspaero",
                runtime_executables={"openvsp": root / "vspscript.exe"},
            )
            self.assertFalse(result["valid"])
            self.assertIn("vspaero", " ".join(result["errors"]))
            self.assertEqual(["openvsp"], result["runtime_executables_checked"])

    def test_schema_1_1_uses_relative_artifacts_after_relocation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            relocated = root / "relocated"
            source.mkdir()
            relocated.mkdir()
            master, twin = source / "master.vsp3", source / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            source_certificate = self._write_certificate(
                source, self._certificate(master, twin)
            )
            (relocated / "twin.vsp3").write_bytes(twin.read_bytes())
            relocated_certificate = relocated / "certificate.json"
            relocated_certificate.write_bytes(source_certificate.read_bytes())

            # The captured absolute path now points at a changed source file;
            # the relocated, sealed relative artifact remains authoritative.
            twin.write_bytes(b"changed old host copy")
            result = verify_certificate(relocated_certificate, backend="vspaero")
            self.assertTrue(result["valid"], result["errors"])

    def test_schema_1_1_rejects_relative_path_traversal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cert_dir = root / "certificate_run"
            cert_dir.mkdir()
            master, twin = cert_dir / "master.vsp3", cert_dir / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            (root / "outside.vsp3").write_bytes(twin.read_bytes())
            certificate = self._certificate(master, twin)
            certificate["twins"]["vspaero_mixed"]["relative_path"] = "../outside.vsp3"
            path = self._write_certificate(cert_dir, self._reseal(certificate))
            result = verify_certificate(path, backend="vspaero")
            self.assertFalse(result["valid"])
            self.assertIn("выходит за каталог сертификата", " ".join(result["errors"]))

    def test_twin_tampering_invalidates_certificate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            twin.write_bytes(b"tampered")
            result = verify_certificate(path)
            self.assertFalse(result["valid"])
            self.assertTrue(any("двойник" in item for item in result["errors"]))

    def test_master_or_scenario_mismatch_invalidates_certificate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, other, twin = root / "master.vsp3", root / "other.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            other.write_bytes(b"other")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))
            result = verify_certificate(path, master_path=other, scenario_id="night_high_accuracy")
            self.assertFalse(result["valid"])
            self.assertTrue(any("MASTER" in item for item in result["errors"]))
            self.assertTrue(any("Сценарий" in item for item in result["errors"]))

    def test_same_scenario_id_cannot_bypass_changed_conditions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "twin.vsp3"
            master.write_bytes(b"master")
            twin.write_bytes(b"twin")
            path = self._write_certificate(root, self._certificate(master, twin))

            mutations = {
                "cases": lambda item: item["vspaero_cases"][0].__setitem__("mach_end", 1.2),
                "tail": lambda item: item.__setitem__("tail_incidence_deg", 2.0),
                "beta": lambda item: item.__setitem__("beta_deg", 1.0),
                "boundary": lambda item: item["vspaero_cases"][0].__setitem__(
                    "engine_boundary", "to_face"
                ),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    current = deepcopy(self._scenario())
                    mutate(current)
                    result = verify_certificate(
                        path,
                        backend="vspaero",
                        scenario_id="geometry_anchor_check",
                        scenario=current,
                    )
                    self.assertFalse(result["valid"])
                    self.assertIn("изменён после сертификации", " ".join(result["errors"]))


class GeometryConvergenceAndTriTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_geometry_policy(POLICY_PATH)

    @staticmethod
    def probes(values: list[tuple[float, float]], *, valid: bool = True) -> list[dict]:
        return [
            {"level": level, "valid": valid, "values": {"CLtot": cl, "CDtot": cd}}
            for level, (cl, cd) in zip(("coarse", "medium", "fine"), values)
        ]

    def test_mesh_convergence_passes_when_medium_to_fine_is_within_five_percent(self):
        result = mesh_convergence(self.probes([(0.50, 0.030), (0.52, 0.031), (0.53, 0.032)]), self.policy)
        self.assertTrue(result["valid"])
        self.assertTrue(result["converged"])
        self.assertLess(result["quantities"]["CLtot"]["medium_fine_relative"], 0.05)
        self.assertTrue(result["quantities"]["CLtot"]["monotonic"])
        self.assertFalse(result["oscillating"])

    def test_mesh_convergence_rejects_oscillation_even_inside_final_tolerance(self):
        result = mesh_convergence(
            self.probes([(0.50, 0.030), (0.52, 0.031), (0.515, 0.032)]),
            self.policy,
        )
        cl = result["quantities"]["CLtot"]
        self.assertFalse(result["converged"])
        self.assertTrue(result["oscillating"])
        self.assertTrue(cl["oscillating"])
        self.assertFalse(cl["monotonic"])
        self.assertAlmostEqual(0.25, cl["contraction_ratio"])
        self.assertIn("CLtot", result["oscillating_quantities"])
        self.assertIn("Осцилляция", " ".join(result["errors"]))

    def test_mesh_convergence_ignores_sign_flip_below_significance_floor(self):
        result = mesh_convergence(
            self.probes([
                (0.34694, 0.02372),
                (0.30709, 0.01996),
                (0.30718, 0.01881),
            ]),
            self.policy,
        )
        self.assertFalse(result["quantities"]["CLtot"]["oscillating"])
        self.assertTrue(result["quantities"]["CLtot"]["converged"])
        self.assertFalse(result["converged"])
        self.assertFalse(result["quantities"]["CDtot"]["converged"])

    def test_mesh_convergence_does_not_veto_large_coarse_to_medium_change(self):
        result = mesh_convergence(
            self.probes([(0.30, 0.015), (0.50, 0.030), (0.51, 0.0305)]),
            self.policy,
        )
        self.assertTrue(result["converged"], result["errors"])
        self.assertGreater(
            result["quantities"]["CLtot"]["coarse_medium_relative"], 0.05
        )

    def test_mesh_convergence_rejects_unstable_or_missing_level(self):
        unstable = mesh_convergence(self.probes([(0.50, 0.030), (0.52, 0.031), (0.60, 0.040)]), self.policy)
        self.assertFalse(unstable["converged"])
        missing = mesh_convergence(self.probes([(0.50, 0.030), (0.52, 0.031), (0.53, 0.032)])[:2], self.policy)
        self.assertFalse(missing["valid"])

    def test_vspaero_health_recognizes_fatal_numerical_markers(self):
        errors = parse_vspaero_health(
            "REPAIRMACH_VSPSCRIPT_TIMEOUT\nnot convex\nupwind edge loop\nMag: 0\nNaN\nfatal error"
        )
        self.assertEqual(
            {"timeout", "nan_or_inf", "not_convex", "upwind_loop", "zero_magnitude", "fatal"},
            set(errors),
        )

    def test_vspaero_health_keeps_geometry_signals_as_visible_nonfatal_warnings(self):
        errors, warnings = classify_vspaero_health([
            "not_convex", "zero_magnitude", "nan_or_inf", "fatal"
        ])
        self.assertEqual(["nan_or_inf", "fatal"], errors)
        self.assertEqual(["not_convex", "zero_magnitude"], warnings)

    def test_arbitrary_tri_without_automatic_export_lineage_is_blocked_before_read(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tri = root / "arbitrary.tri"
            tri.write_text("untrusted external TRI", encoding="utf-8")
            with patch("geometry_certification.read_tri") as read_mock:
                result, findings = _tri_certification(
                    tri, root / "run", self.policy, self.policy["scope"]
                )
            read_mock.assert_not_called()
            self.assertFalse(result["eligible"])
            self.assertEqual("blocked_lineage", result["status"])
            self.assertEqual("GEO-TRI-LINEAGE-001", findings[0]["code"])

    def test_export_lineage_rejects_tampered_fine_twin_before_tri_repair(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tri = root / "exported.tri"
            twin = root / "machline_fine.vsp3"
            tri.write_text("sealed TRI", encoding="utf-8")
            twin.write_text("fine twin before export", encoding="utf-8")
            lineage = machline_export_record(tri, twin, self.policy)
            twin.write_text("fine twin changed after export", encoding="utf-8")
            with patch("geometry_certification.read_tri") as read_mock:
                result, findings = _tri_certification(
                    tri,
                    root / "run",
                    self.policy,
                    self.policy["scope"],
                    export_record=lineage,
                )
            read_mock.assert_not_called()
            self.assertEqual("blocked_lineage", result["status"])
            self.assertTrue(any("хэш Fine-двойника" in item for item in result["errors"]))
            self.assertEqual("GEO-TRI-LINEAGE-001", findings[0]["code"])

    def test_automatic_export_uses_fine_twin_and_never_uses_legacy_tri_hint(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            twin = root / "machline" / "fine" / "model.vsp3"
            legacy = root / "legacy_input.tri"
            executable = root / "vspscript.exe"
            twin.parent.mkdir(parents=True)
            twin.write_bytes(b"fine twin")
            legacy.write_bytes(b"external mesh")
            executable.write_bytes(b"fixture executable")

            def fake_run(_executable, _script, log_path, **_kwargs):
                raw = Path(log_path).parent / "openvsp_nascart_raw.tri"
                raw.write_text(
                    "3 1\n0 0 0\n1 0 0\n0 1 0\n1 2 3 7.0\n",
                    encoding="utf-8",
                )
                raw.with_suffix(".key").write_text(
                    "Color Name BCType\n7.0 Fuselage_S_Surf0 0\n",
                    encoding="utf-8",
                )
                thin = Path(log_path).parent / "openvsp_thin_model.vspgeom"
                thin.write_text(
                    "# vspgeom v3\n1\n3 1 0\n"
                    "0 0 0\n1 0 0\n0 1 0\n"
                    "1\n3 1 2 3\n1 1\n",
                    encoding="utf-8",
                )
                thin.with_suffix(".vkey").write_text(
                    "# VSPGEOM v3 Tag Key File\n"
                    "# part#,geom#,surf#,gname,gid,thick,plate,copy#,geomcopy#\n"
                    "1,1,0,Wing_C,WINGID,0,3,1,1\n",
                    encoding="utf-8",
                )
                Path(log_path).write_text(
                    "REPAIRMACH_MACHLINE_UNION_GEOM=Fuselage\n"
                    "REPAIRMACH_MACHLINE_UNION_GEOM=Wing\n"
                    "REPAIRMACH_MACHLINE_TRI_EXPORT_OK=1\n",
                    encoding="utf-8",
                )
                return 0

            solver_record = {
                "path": str(twin.resolve()),
                "sha256": sha256_file(twin),
                "canonical_sha256": sha256_file(twin),
            }
            with patch("geometry_certification.run_vspscript", side_effect=fake_run):
                record = _export_machline_tri(
                    solver_twin_record=solver_record,
                    run_dir=root / "run",
                    policy=self.policy,
                    vspscript_executable=executable,
                    certification_bindings={
                        "master_sha256": "a" * 64,
                        "plan_sha256": "b" * 64,
                        "base_twin_sha256": "c" * 64,
                        "fine_twin_sha256": sha256_file(twin),
                        "fine_mesh_signature_sha256": "d" * 64,
                    },
                    legacy_tri_hint=legacy,
                )
            self.assertEqual("passed", record["status"])
            self.assertEqual("machline_fine", record["source_solver_twin"]["role"])
            self.assertEqual(sha256_file(twin), record["source_solver_twin"]["sha256"])
            self.assertFalse(record["legacy_tri_hint"]["used_as_solver_geometry"])
            exported = Path(record["export"]["path"])
            self.assertTrue(exported.is_file())
            self.assertNotEqual(sha256_file(legacy), sha256_file(exported))
            parsed = read_tri(exported)
            self.assertEqual(2, len(parsed.faces))
            self.assertEqual([7, 8], parsed.components)
            self.assertEqual(
                "NASCART_thick_plus_VSPGeom_VLM_thin",
                record["export"]["mesh_representation"],
            )

    def test_tri_topology_blocker_prevents_any_repair(self):
        mesh = TriMesh([(0.0, 0.0, 0.0)], [], [])
        initial = diagnostic_report(invalid_index_faces=1, machline_safe_topology=False)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tri = root / "input.tri"
            twin = root / "machline_fine.vsp3"
            tri.write_text("placeholder", encoding="utf-8")
            twin.write_bytes(b"sealed fine twin")
            lineage = machline_export_record(tri, twin, self.policy)
            with patch("geometry_certification.read_tri", return_value=mesh), \
                    patch("geometry_certification.diagnose_tri", return_value=initial), \
                    patch("geometry_certification.prepare_machline_geometry") as prepare_mock:
                result, findings = _tri_certification(
                    tri, root / "run", self.policy, self.policy["scope"], export_record=lineage
                )
            prepare_mock.assert_not_called()
            self.assertEqual("blocked_topology", result["status"])
            self.assertEqual("GEO-TRI-001", findings[0]["code"])

    def test_tri_safe_repair_budget_is_minimum_of_absolute_and_fractional_limits(self):
        mesh = TriMesh([(0.0, 0.0, 0.0)], [], [])
        # 0.5% of 1000 panels is 5, below the absolute cap of 100.
        initial = diagnostic_report(duplicate_faces=6, safe_repairs_available=6)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tri = root / "input.tri"
            twin = root / "machline_fine.vsp3"
            tri.write_text("placeholder", encoding="utf-8")
            twin.write_bytes(b"sealed fine twin")
            lineage = machline_export_record(tri, twin, self.policy)
            prepared_log = {
                "duplicate_skin_repair": {"removed_faces": 6},
                "generic_repair": {},
                "downstream_axial_closure": {},
                "final_repair": {},
            }
            with patch("geometry_certification.read_tri", return_value=mesh), \
                    patch("geometry_certification.diagnose_tri", side_effect=[initial, initial, initial]), \
                    patch(
                        "geometry_certification.prepare_machline_geometry",
                        return_value=(mesh, prepared_log),
                    ), \
                    patch("geometry_certification.scan_scope", return_value=[]):
                result, findings = _tri_certification(
                    tri, root / "run", self.policy, self.policy["scope"], export_record=lineage
                )
            self.assertEqual(5, result["repair_budget"])
            self.assertEqual("repair_budget_exceeded", result["status"])
            self.assertEqual("GEO-TRI-002", findings[0]["code"])

    def test_tri_within_budget_and_clean_mach_scans_is_eligible(self):
        mesh = TriMesh(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            [(0, 1, 2)],
            [1],
        )
        initial = diagnostic_report(duplicate_faces=2, safe_repairs_available=2)
        final = diagnostic_report(faces=998)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tri = root / "input.tri"
            twin = root / "machline_fine.vsp3"
            tri.write_text("placeholder", encoding="utf-8")
            twin.write_bytes(b"sealed fine twin")
            lineage = machline_export_record(tri, twin, self.policy)
            prepared_log = {
                "duplicate_skin_repair": {"removed_faces": 2},
                "generic_repair": {},
                "downstream_axial_closure": {},
                "final_repair": {},
            }
            clean_scans = [
                {"mach": mach, "alpha_deg": alpha, "bad_panels": 0}
                for mach in (1.2, 2.2) for alpha in (0.0, 5.0)
            ]
            with patch("geometry_certification.read_tri", return_value=mesh), \
                    patch("geometry_certification.diagnose_tri", side_effect=[initial, final, final]), \
                    patch(
                        "geometry_certification.prepare_machline_geometry",
                        return_value=(mesh, prepared_log),
                    ), \
                    patch(
                        "geometry_certification._run_machline_geometry_qualification",
                        return_value={"requested": True, "valid": True, "status": "passed", "probes": []},
                    ), \
                    patch("geometry_certification.scan_scope", return_value=clean_scans):
                result, findings = _tri_certification(
                    tri, root / "run", self.policy, self.policy["scope"], export_record=lineage
                )
            self.assertEqual(["GEO-MACH-SOLVER-002"], [item["code"] for item in findings])
            self.assertTrue(result["eligible"])
            self.assertEqual("passed", result["status"])
            self.assertEqual(
                [(1.2, 0.0), (1.2, 5.0), (2.2, 0.0), (2.2, 5.0)],
                [(item["mach"], item["alpha_deg"]) for item in result["mach_scans"]],
            )
            self.assertTrue(Path(result["certified_tri"]).is_file())


if __name__ == "__main__":
    unittest.main()
