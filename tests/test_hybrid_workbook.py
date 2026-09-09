from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from hybrid_pipeline import find_node_executable, find_node_modules


BUILDER = ROOT / "app" / "hybrid_workbook.mjs"


def hybrid_bundle(*, zero_alpha_interval: bool = False) -> dict:
    mach_values = [0.2, 0.8, 1.2, 1.7]
    rows = []
    cy_alpha = []
    for mach in mach_values:
        cy0 = 0.01 * mach
        slope = 0.05 + 0.005 * mach
        for alpha in (0.0, 1.0):
            pressure = 0.012 + 0.004 * mach
            induced = 0.0005 * alpha
            viscous = 0.010
            semi = 0.001
            rows.append({
                "Mach": mach,
                "alpha_deg": alpha,
                "Cy": cy0 + slope * alpha,
                "CY": cy0 + slope * alpha,
                "Cx_pressure_wave": pressure,
                "Cx_induced": induced,
                "Cx_viscous": viscous,
                "Cx_semiempirical": semi,
                "Cx_total": pressure + induced + viscous + semi,
                "status": "complete",
                "quality": {"machline_residual_norm": 1.0e-5},
                "sources": {
                    "vspaero": "[external.xlsx]Sheet1!A1",
                    "machline": "machline_report.json",
                    "parasite_drag": "parasite.csv",
                },
                "semiempirical_terms": [{
                    "id": "details",
                    "label": "Антенны и щели",
                    "cd": semi,
                    "model": "fixed",
                    "provenance": "independent method",
                }],
            })
        alpha_end = 0.0 if zero_alpha_interval else 1.0
        cy_alpha.append({
            "Mach": mach,
            "alpha_start_deg": 0.0,
            "alpha_end_deg": alpha_end,
            "Cy_start": cy0,
            "Cy_end": cy0 + slope,
            "Cy_alpha_direct_per_deg": slope,
            "Cy_alpha_final_per_deg": slope,
            "method_version": "test-method",
            "status": "complete",
            "source": "direct VSPAERO",
        })
    return {
        "schema": "repairmach.hybrid-series/1.0",
        "method_version": "test-method",
        "status": "complete",
        "reference_data_used": False,
        "pointwise_tuning_used": False,
        "summary": {
            "points": len(rows),
            "complete_points": len(rows),
            "incomplete_points": 0,
            "cy_alpha_points": len(cy_alpha),
            "errors": 0,
            "warnings": 0,
        },
        "rows": rows,
        "cy_alpha": cy_alpha,
        "policy": {
            "drag": {
                "pressure_wave_source": "MachLine_wind_axis_CD",
                "induced_source": "VSPAERO_CDi",
                "parasite_source": "OpenVSP_ParasiteDrag",
            },
            "lift": {"cy_alpha": {"method": "direct"}},
            "transonic_excluded": [0.9, 1.1],
        },
        "sources": [{
            "role": "VSPAERO full model",
            "file_name": "source-name.polar",
            "size_bytes": 1234,
            "sha256": "a" * 64,
        }],
    }


class HybridWorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = find_node_executable()
        cls.node_modules = find_node_modules()

    def run_builder(
        self,
        root: Path,
        bundle: dict,
        *,
        fail_stage: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        input_path = root / "hybrid_result.json"
        output_path = root / "RepairMach_hybrid_results.xlsx"
        verification_path = root / "workbook_verification.json"
        previews = root / "previews"
        input_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env.pop("REPAIRMACH_WORKBOOK_TEST_FAIL_STAGE", None)
        if fail_stage is not None:
            env["REPAIRMACH_WORKBOOK_TEST_FAIL_STAGE"] = fail_stage
        return subprocess.run(
            [
                str(self.node), str(BUILDER),
                "--input", str(input_path),
                "--output", str(output_path),
                "--verification", str(verification_path),
                "--previews", str(previews),
                "--node-modules", str(self.node_modules),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )

    def assert_no_published_or_staged_outputs(self, root: Path) -> None:
        self.assertFalse((root / "RepairMach_hybrid_results.xlsx").exists())
        self.assertFalse((root / "workbook_verification.json").exists())
        self.assertFalse((root / "previews").exists())
        self.assertFalse(
            [item.name for item in root.iterdir() if ".tmp-" in item.name],
            "temporary workbook artifacts were not cleaned up",
        )

    @unittest.skipUnless(find_node_executable() and find_node_modules(), "artifact-tool runtime unavailable")
    def test_mach_charts_are_xy_scatter_and_internal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_builder(root, hybrid_bundle())
            self.assertEqual(0, result.returncode, result.stdout)
            output = root / "RepairMach_hybrid_results.xlsx"
            verification = json.loads((root / "workbook_verification.json").read_text(encoding="utf-8"))
            self.assertEqual("passed", verification["build_status"])
            self.assertEqual("scatter", verification["chart_type"])
            self.assertTrue(verification["charts_use_numeric_mach_axis"])
            self.assertTrue(verification["charts_use_internal_cells"])
            self.assertFalse(verification["external_links_detected"])
            self.assertEqual(0, verification["formula_error_count"])
            self.assertEqual("final_exported_xlsx", verification["previews_source"])
            self.assertTrue(all(item["size_bytes"] > 0 for item in verification["preview_details"]))

            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                chart_names = [name for name in names if "/charts/chart" in name]
                chart_xml = [archive.read(name).decode("utf-8") for name in chart_names]
                self.assertEqual(3, len(chart_xml))
                self.assertTrue(all("<c:scatterChart" in xml for xml in chart_xml))
                self.assertTrue(all("<c:lineChart" not in xml for xml in chart_xml))
                self.assertTrue(all("<c:catAx" not in xml for xml in chart_xml))
                self.assertTrue(all(xml.count("<c:valAx") >= 2 for xml in chart_xml))
                expected_x = "'Сводка'!$A$17:$A$20"
                self.assertTrue(all(f"<c:xVal><c:numRef><c:f>{expected_x}</c:f>" in xml for xml in chart_xml))
                self.assertTrue(all("<c:yVal><c:numRef><c:f>" in xml for xml in chart_xml))
                self.assertTrue(all('<c:scatterStyle val="lineMarker" />' in xml for xml in chart_xml))
                self.assertFalse(any(name.startswith("xl/externalLinks/") for name in names))
                self.assertNotIn("[external.xlsx]", "\n".join(chart_xml))
                cell_xml = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in names
                    if name == "xl/sharedStrings.xml" or name.startswith("xl/worksheets/sheet")
                )
                self.assertIn("source-name.polar", cell_xml)
                self.assertIn("a" * 64, cell_xml)

    @unittest.skipUnless(find_node_executable() and find_node_modules(), "artifact-tool runtime unavailable")
    def test_formula_error_blocks_atomic_publication(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_builder(root, hybrid_bundle(zero_alpha_interval=True))
            self.assertNotEqual(0, result.returncode)
            self.assertIn("формульные ошибки", result.stdout)
            self.assert_no_published_or_staged_outputs(root)

    @unittest.skipUnless(find_node_executable() and find_node_modules(), "artifact-tool runtime unavailable")
    def test_post_save_failure_rolls_back_every_artifact(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_builder(root, hybrid_bundle(), fail_stage="after_export_save")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("after_export_save", result.stdout)
            self.assert_no_published_or_staged_outputs(root)

    @unittest.skipUnless(find_node_executable() and find_node_modules(), "artifact-tool runtime unavailable")
    def test_stale_passed_verification_and_previews_are_removed_on_late_failure(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "RepairMach_hybrid_results.xlsx").write_bytes(b"stale workbook")
            (root / "workbook_verification.json").write_text(
                json.dumps({"build_status": "passed", "stale": True}),
                encoding="utf-8",
            )
            previews = root / "previews"
            previews.mkdir()
            (previews / "stale.png").write_bytes(b"stale preview")

            result = self.run_builder(
                root,
                hybrid_bundle(),
                fail_stage="after_first_preview_write",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("after_first_preview_write", result.stdout)
            self.assert_no_published_or_staged_outputs(root)

    @unittest.skipUnless(find_node_executable(), "Node.js runtime unavailable")
    def test_external_link_part_fails_closed_in_audit_mode(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            linked = root / "linked.xlsx"
            verification_path = root / "verification.json"
            with zipfile.ZipFile(linked, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("xl/workbook.xml", "<workbook/>")
                archive.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
            result = subprocess.run(
                [
                    str(self.node), str(BUILDER),
                    "--audit-xlsx", str(linked),
                    "--verification", str(verification_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            verification = json.loads(verification_path.read_text(encoding="utf-8"))
            self.assertEqual("failed", verification["build_status"])
            self.assertTrue(verification["external_links_detected"])
            self.assertIn("xl/externalLinks/externalLink1.xml", verification["external_link_parts"])


if __name__ == "__main__":
    unittest.main()
