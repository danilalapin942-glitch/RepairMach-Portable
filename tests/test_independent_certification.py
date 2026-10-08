"""Contract tests; synthetic meshes are not aircraft accuracy validation."""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import geometry_certification as cert
import repairmach_beta as app
import test_geometry_certification as fixtures
from geometry_certificate import seal_certificate, verify_certificate
from geometry_manifest import sha256_file, sha256_payload
from machline_open_nozzle import open_declared_nozzles
from machline_geometry import replace_downstream_axial_caps
from tri_mesh import TriMesh, diagnose, write_tri


def box():
    vertices = [(0,-1,-1),(0,1,-1),(0,1,1),(0,-1,1),
                (1,-1,-1),(1,1,-1),(1,1,1),(1,-1,1),(1,0,0)]
    faces = [(0,2,1),(0,3,2),(4,5,8),(5,6,8),(6,7,8),(7,4,8),
             (0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return TriMesh(vertices, faces, [1]*len(faces))


class OutletTests(unittest.TestCase):
    def prepare(self, mesh=None, **kwargs):
        options = dict(declarations=[{"component_name":"Gondola_S_Surf0", "plane_x":1}],
                       component_names={1:"Gondola_S_Surf0"}, reference={"area":100,"cref":10},
                       scope={"mach_intervals":[[1.2,1.7]],"alpha_deg":[0,0],"beta_deg":0})
        options.update(kwargs)
        return open_declared_nozzles(mesh or box(), **options)

    def test_exact_disk_only_removed_without_changing_input_and_vertices_compacted(self):
        mesh = box()
        before = deepcopy(mesh)
        candidate, audit = self.prepare(mesh)
        self.assertTrue(audit["valid"], audit)
        self.assertEqual(before, mesh)
        self.assertEqual(4, audit["boundary_edges"])
        self.assertEqual(1, audit["removed_vertex_count"])
        self.assertEqual([3,4,5,6], audit["removed_face_ids"])
        self.assertEqual(8, len(candidate.vertices))
        self.assertTrue(diagnose(candidate)["machline_safe_topology"])
        self.assertFalse(diagnose(candidate)["watertight"])

    def test_missing_ambiguous_wrong_and_nonfinite_declarations_rejected(self):
        for declarations in ([], [None], [{"component_name":"Fuselage","plane_x":1}],
                             [{"component_name":"Gondola_S_Surf0","plane_x":0.8}],
                             [{"component_name":"Gondola_S_Surf0","plane_x":float("nan")}],
                             [{"component_name":"Gondola_S_Surf0","plane_x":1}]*2):
            with self.subTest(declarations=declarations):
                mesh = box()
                candidate, audit = self.prepare(mesh, declarations=declarations)
                self.assertFalse(audit["valid"])
                self.assertIs(mesh, candidate)

    def test_unclaimed_hole_blocks_the_branch(self):
        mesh = box()
        mesh.faces.pop(0)
        mesh.components.pop(0)
        candidate, audit = self.prepare(mesh)
        self.assertFalse(audit["valid"])
        self.assertIn("незаявленные", audit["errors"][0])
        self.assertIs(candidate, mesh)

    def test_existing_open_outlet_is_audited_without_further_removal(self):
        candidate, _ = self.prepare()
        same, audit = self.prepare(candidate)
        self.assertTrue(audit["valid"], audit)
        self.assertEqual([], audit["removed_face_ids"])
        self.assertEqual(candidate, same)

    def test_downstream_aircraft_geometry_blocks_supersonic_open_outlet(self):
        context = box()
        context.vertices += [(1.5,3,0),(1.5,4,0),(1.5,3,1)]
        context.faces += [(9,10,11)]
        context.components += [2]
        _, audit = self.prepare(context_mesh=context)
        self.assertFalse(audit["valid"])
        self.assertIn("позади", audit["errors"][0])

    def test_subsonic_geometry_audit_is_not_subsonic_solver_qualification(self):
        scope = {"mach_intervals":[[0,.8]],"alpha_deg":[0,5],"beta_deg":0}
        mesh, audit = self.prepare(scope=scope)
        self.assertTrue(audit["valid"])
        self.assertFalse(audit["subsonic_solver_qualified"])
        result = cert._run_machline_geometry_qualification(
            executable=None, mesh_path=Path("unused"), mesh=mesh, run_dir=Path("unused"),
            scope=scope, reference={}, force_mask_path=Path("unused"), force_contract={},
            policy={"topology_mode":"open_nozzle"})
        self.assertFalse(result["valid"])
        self.assertEqual("open_nozzle_subsonic_not_qualified", result["status"])

    def test_subsonic_scope_does_not_call_max_of_empty_supersonic_machs(self):
        mesh = box()
        candidate, audit = replace_downstream_axial_caps(
            mesh, scope={"mach_intervals":[[0,.8]],"alpha_deg":[0,5],"beta_deg":0},
            component_names={1:"Fuselage_S_Surf0"}, reference={"area":100,"cref":10},
            policy={"enabled":True,"component_name_prefixes":["Fuselage"]})
        self.assertIs(candidate, mesh)
        self.assertFalse(audit["accepted"])

    def test_tri_pipeline_scans_open_solver_copy_not_the_removed_disk(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mesh = box()
            mesh.vertices += [(0,3,0),(0,4,0),(.5,3,0)]
            mesh.faces += [(9,10,11)]
            mesh.components += [2]
            tri, twin = root / "mesh.tri", root / "model.vsp3"
            write_tri(tri,mesh)
            twin.write_text("<VSP3/>")
            policy = cert.load_geometry_policy(fixtures.POLICY_PATH)
            policy["machline"].update(topology_mode="open_nozzle",open_nozzles=[{
                "component_name":"Fuselage_S_Surf0","plane_x":1}])
            lineage = fixtures.machline_export_record(tri,twin,policy)
            with patch.object(cert,"scan_scope",return_value=[]) as scan, \
                 patch.object(cert,"_run_machline_geometry_qualification",return_value={"valid":False,"probes":[]}):
                result,_ = cert._tri_certification(tri,root / "run",policy,
                    {"mach_intervals":[[1.2,1.7]],"alpha_deg":[0,0],"beta_deg":0},
                    export_record=lineage,reference={"area":100,"cref":10})
            audit = result["topology_contract"]["declared_open_nozzles"]
            self.assertTrue(audit["valid"],audit)
            self.assertEqual(11,len(scan.call_args.args[0].faces))
            self.assertFalse(result["standalone_total_force_eligible"])


class BranchTests(unittest.TestCase):
    def test_orchestration_does_not_run_the_other_backend(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / "master.vsp3"
            master.write_text("<VSP3/>")
            exe = root / "solver.exe"
            exe.write_bytes(b"test executable")
            inventory = fixtures.valid_inventory()
            inventory["openvsp_version"] = "fixture"
            def apply(**kw):
                dest = kw["output_path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(master.read_bytes())
                return {"path":str(dest),"sha256":sha256_file(dest),"canonical_sha256":sha256_file(dest),
                        "name":kw["twin_name"],"valid":True,"errors":[]}
            def mesh_check(_i, _a, expected):
                return {"valid":True,"signature_sha256":sha256_payload(expected),"errors":[]}
            with patch.object(cert,"inventory_model",return_value=(inventory,None,None)), \
                 patch.object(cert,"apply_model_actions",side_effect=apply), \
                 patch.object(cert,"verify_mesh_inventory",side_effect=mesh_check), \
                 patch.object(cert,"_export_machline_tri",side_effect=RuntimeError("ML export failed")) as ml, \
                 patch.object(cert,"_run_vspaero_qualification",return_value={"valid":True,"converged":True,"anchors":[]}) as vsp:
                arguments = dict(master_path=master, output_root=root / "cert", project_name="branches",
                                 reference=fixtures.reference(),policy_path=fixtures.POLICY_PATH,
                                 executables={"vspscript":exe,"vspaero":exe,"machline":exe})
                result = cert.certify_geometry(**arguments,backend_branch="vspaero")
                self.assertNotEqual("FAIL",result["certificate"]["verdict"])
                self.assertFalse(result["certificate"]["backends"]["machline"]["eligible"])
                self.assertNotIn("machline",result["certificate"]["twins"])
                ml.assert_not_called()
                self.assertTrue(vsp.called)
                vsp.reset_mock()
                result = cert.certify_geometry(**arguments,backend_branch="machline_open_nozzle")
                self.assertEqual("FAIL",result["certificate"]["verdict"])
                self.assertNotIn("vspaero_mixed",result["certificate"]["twins"])
                vsp.assert_not_called()
                ml.assert_called_once()
            self.assertEqual("<VSP3/>",master.read_text())

    def test_machline_certificate_cannot_grant_other_backends_or_unrun_points(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, twin = root / "master.vsp3", root / "mesh.tri"
            master.write_text("<VSP3/>")
            twin.write_text("fixture")
            fixture = fixtures.GeometryCertificateTests()
            payload = fixture._certificate(master, twin)
            payload.update(certification_branch="machline_open_nozzle",primary_backend="machline")
            vsp = payload["backends"]["vspaero"]
            vsp["eligible"] = False
            ml = deepcopy(vsp)
            ml.update(eligible=True,mode="certified_thick_body_pressure_wave",
                      force_integration_mask=ml["solver_geometry"], audit_geometry=ml["solver_geometry"],
                      output_contract={"topology_mode":"open_nozzle","standalone_total_force_eligible":False,
                                       "declared_open_nozzles":{"valid":True,"outlets":[{"component_id":1}],"errors":[]}},
                      qualification={"valid":True,"limits":{"residual_norm":1e-5,"residual_max":1e-5},
                                     "probes":[{"mach":1.2,"alpha_deg":0,"valid":True,"errors":[],"solver_status_code":0,
                                                "superinclined_panels":0,"residual_norm":1e-8,"residual_max":1e-8}]},
                      qualified_scope={"coverage_kind":"exact_points","points":[{"mach":1.2,"alpha_deg":0}]})
            payload["backends"]["machline"] = ml
            payload["software"]["solvers"]["machline"] = payload["software"]["solvers"]["vspaero"]
            path = root / "certificate.json"
            path.write_text(json.dumps(seal_certificate(payload)))
            self.assertTrue(verify_certificate(path,backend="machline",mach=1.2,alpha_deg=0)["valid"])
            for backend, mach in (("vspaero",1.2),("hybrid",1.2),("machline",1.3),("machline",float("nan"))):
                self.assertFalse(verify_certificate(path,backend=backend,mach=mach,alpha_deg=0)["valid"])
            for field, value in (("solver_status_code",1),("residual_norm",1.0),("superinclined_panels",1)):
                corrupt = deepcopy(payload)
                corrupt["backends"]["machline"]["qualification"]["probes"][0][field] = value
                path.write_text(json.dumps(seal_certificate(corrupt)))
                self.assertFalse(verify_certificate(path,backend="machline")["valid"])

    def test_native_menu_routes_have_distinct_outputs_and_pointers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / "master.vsp3"
            master.write_text("<VSP3/>")
            (root / "project_config.json").write_text("{}")
            result = {"certificate":{"verdict":"FAIL","flags":{},"findings":[],"master":{}},
                      "certificate_path":root / "certificate.json","report_path":root / "certificate.md"}
            with patch.object(app,"load_settings",return_value={}),patch.object(app,"default_project_path",return_value=root), \
                 patch.object(app,"find_openvsp_dir"),patch.object(app,"tool_paths",return_value={"vspscript":master,"vspaero":master}), \
                 patch.object(app,"find_machline",return_value=master),patch.object(app,"load_project_reference",return_value={
                    "area":100,"longitudinal_length":10,"lateral_length":10,"center":[0,0,0]}), \
                 patch.object(app,"certify_geometry",return_value=result) as run,patch("builtins.print"):
                for branch, folder, pointer in (("vspaero","11_cert_vspaero","vspaero"),
                                                 ("machline_open_nozzle","11_cert_machline","machline")):
                    with patch("builtins.input",side_effect=[str(master)]):
                        app.geometry_certification_workflow({"certification_branch":branch})
                    self.assertEqual(branch,run.call_args.kwargs["backend_branch"])
                    self.assertEqual(root / folder,run.call_args.kwargs["output_root"])
                    self.assertTrue((root / "11_geometry_certification" / f"latest_{pointer}_failed_certificate.json").is_file())
                self.assertFalse((root / "11_geometry_certification/latest_certificate.json").exists())


if __name__ == "__main__":
    unittest.main()
