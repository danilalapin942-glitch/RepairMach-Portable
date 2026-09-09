from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from blind_study import (
    attach_reference_after_seal,
    preflight_blind_inputs,
    prepare_blind_package,
    seal_predictions,
    verify_blind_package,
    verify_prediction_seal,
)


class BlindStudyTests(unittest.TestCase):
    def _prepare(self, root: Path) -> Path:
        geometry = root / "aircraft.vsp3"
        project_config = root / "project_config.json"
        catalog = root / "calculation_scenarios.json"
        settings = root / "repairmach_settings.json"
        solver = root / "vspaero.exe"
        geometry.write_bytes(b"vsp geometry")
        project_config.write_text(
            '{"default_reference":{"area":100,"longitudinal_length":5,'
            '"lateral_length":20,"center":[1,0,0]}}',
            encoding="utf-8",
        )
        catalog.write_text('{"catalog_version":"9.1"}', encoding="utf-8")
        settings.write_text('{"repairmach_version":"9.1"}', encoding="utf-8")
        solver.write_bytes(b"solver binary")
        return prepare_blind_package(
            root / "blind_run",
            repairmach_version="9.1",
            project_name="Independent_Aircraft",
            geometry_path=geometry,
            project_config_path=project_config,
            scenario_catalog_path=catalog,
            settings_path=settings,
            selected_scenario={
                "id": "fixed_go_full_adh",
                "availability": "ready",
                "execution": "vspaero_study",
                "tail_incidence": "fixed",
                "tail_geometry_name": "GO",
                "tail_incidence_deg": 0.0,
                "acceptance_criteria": {
                    "numerical_percent": 5.0,
                    "total_mean_percent": 11.0,
                },
                "vspaero_cases": [{"mach_start": 0.8, "mach_end": 0.8}],
            },
            method_declaration={
                "method_version": "RM92.1-CYA-WINGBODY-3-working",
                "pointwise_tuning": False,
                "geometry_adjustment_after_seal": False,
            },
            solver_paths=[("VSPAERO", solver)],
        )

    def test_prepared_package_is_self_verifying(self):
        with TemporaryDirectory() as tmp:
            manifest = self._prepare(Path(tmp))
            result = verify_blind_package(manifest)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertTrue(result["valid"])
        self.assertEqual("inputs_sealed", result["state"])
        self.assertFalse(payload["blind_rules"]["reference_data_present"])
        self.assertEqual(4, len(payload["inputs"]))
        self.assertEqual(64, len(result["package_fingerprint"]))

    def test_input_tampering_breaks_package(self):
        with TemporaryDirectory() as tmp:
            manifest = self._prepare(Path(tmp))
            geometry = manifest.parent / "inputs" / "geometry.vsp3"
            geometry.write_bytes(b"changed after seal")
            result = verify_blind_package(manifest)
        self.assertFalse(result["valid"])
        self.assertTrue(any("geometry" in item for item in result["errors"]))

    def test_prediction_must_be_sealed_before_reference(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._prepare(root)
            prediction = root / "prediction.csv"
            prediction.write_text("Mach,Cx0\n1.2,0.05\n", encoding="utf-8")
            prediction_seal = seal_predictions(manifest, [prediction])
            self.assertTrue(verify_prediction_seal(prediction_seal)["valid"])
            reference = root / "wind_tunnel.csv"
            reference.write_text("Mach,Cx0\n1.2,0.051\n", encoding="utf-8")
            reference_manifest = attach_reference_after_seal(prediction_seal, [reference])
            payload = json.loads(reference_manifest.read_text(encoding="utf-8"))
        self.assertEqual("unblinded", payload["state"])
        self.assertEqual(1, len(payload["reference_files"]))

    def test_prediction_can_be_written_directly_into_package(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._prepare(root)
            prediction = manifest.parent / "predictions" / "prediction.csv"
            prediction.write_text("Mach,Cx0\n1.2,0.05\n", encoding="utf-8")
            prediction_seal = seal_predictions(manifest, [prediction])
            self.assertTrue(verify_prediction_seal(prediction_seal)["valid"])

    def test_modified_prediction_blocks_unblinding(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._prepare(root)
            prediction = root / "prediction.csv"
            prediction.write_text("Mach,Cx0\n1.2,0.05\n", encoding="utf-8")
            prediction_seal = seal_predictions(manifest, [prediction])
            frozen_prediction = manifest.parent / "predictions" / "prediction.csv"
            frozen_prediction.write_text("Mach,Cx0\n1.2,0.04\n", encoding="utf-8")
            reference = root / "reference.csv"
            reference.write_text("Mach,Cx0\n1.2,0.051\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Прогноз не прошёл"):
                attach_reference_after_seal(prediction_seal, [reference])

    def test_early_reference_contamination_blocks_prediction_seal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._prepare(root)
            contamination = manifest.parent / "after_unblind" / "opened_reference.csv"
            contamination.write_text("Mach,Cx0\n1.2,0.051\n", encoding="utf-8")
            prediction = root / "prediction.csv"
            prediction.write_text("Mach,Cx0\n1.2,0.05\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "после раскрытия"):
                seal_predictions(manifest, [prediction])

    def test_existing_package_cannot_be_overwritten(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._prepare(root)
            with self.assertRaises(FileExistsError):
                self._prepare(root)

    def test_preflight_rejects_optional_tail_and_reference_input(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            geometry = root / "aircraft.vsp3"
            config = root / "project_config.json"
            reference = root / "book_curve.csv"
            geometry.write_bytes(b"vsp")
            config.write_text(
                '{"default_reference":{"area":100,"longitudinal_length":5,'
                '"lateral_length":20,"center":[1,0,0]}}',
                encoding="utf-8",
            )
            reference.write_text("Mach,Cx0\n", encoding="utf-8")
            result = preflight_blind_inputs(
                geometry_path=geometry,
                project_config_path=config,
                selected_scenario={
                    "availability": "ready",
                    "execution": "vspaero_study",
                    "tail_incidence": "optional",
                    "tail_geometry_name": "GO",
                    "tail_incidence_deg": 0.0,
                    "acceptance_criteria": {
                        "numerical_percent": 5.0,
                        "total_mean_percent": 11.0,
                    },
                    "vspaero_cases": [{"mach_start": 0.8}],
                },
                method_declaration={
                    "method_version": "frozen",
                    "pointwise_tuning": False,
                    "geometry_adjustment_after_seal": False,
                },
                additional_inputs=[("reference_curve", reference)],
            )
        self.assertFalse(result["valid"])
        self.assertTrue(any("GO" in item for item in result["errors"]))
        self.assertTrue(any("Эталонный" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
