from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import openvsp_runner


class OpenVspDiscoveryTests(TestCase):
    @staticmethod
    def _make_install(path: Path) -> Path:
        path.mkdir(parents=True)
        (path / "vspscript.exe").write_bytes(b"portable OpenVSP")
        return path

    def test_bundled_openvsp_is_found_without_system_installation(self):
        with TemporaryDirectory() as temp:
            package_root = Path(temp)
            expected = self._make_install(package_root / "engines" / "OpenVSP")
            with mock.patch.object(openvsp_runner, "PACKAGE_ROOT", package_root), mock.patch.dict(
                openvsp_runner.os.environ, {}, clear=True
            ), mock.patch.object(openvsp_runner.shutil, "which", return_value=None):
                self.assertEqual(expected.resolve(), openvsp_runner.find_openvsp_dir())

    def test_relative_configured_path_is_package_relative(self):
        with TemporaryDirectory() as temp:
            package_root = Path(temp)
            expected = self._make_install(package_root / "vendor" / "OpenVSP")
            with mock.patch.object(openvsp_runner, "PACKAGE_ROOT", package_root), mock.patch.dict(
                openvsp_runner.os.environ, {}, clear=True
            ), mock.patch.object(openvsp_runner.shutil, "which", return_value=None):
                self.assertEqual(
                    expected.resolve(),
                    openvsp_runner.find_openvsp_dir("vendor/OpenVSP"),
                )
