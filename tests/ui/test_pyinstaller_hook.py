from __future__ import annotations

import runpy
import sys
import types
import unittest
from pathlib import Path


class TkinterDnDHookTests(unittest.TestCase):
    def test_hook_collects_the_package_data_containing_tkdnd_binaries(self) -> None:
        hooks = types.ModuleType("PyInstaller.utils.hooks")
        hooks.collect_data_files = lambda package: [(package, "tkdnd")]
        utils = types.ModuleType("PyInstaller.utils")
        pyinstaller = types.ModuleType("PyInstaller")
        previous = {name: sys.modules.get(name) for name in ("PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks")}
        sys.modules.update(
            {"PyInstaller": pyinstaller, "PyInstaller.utils": utils, "PyInstaller.utils.hooks": hooks}
        )
        try:
            hook_path = Path(__file__).parents[2] / "tools" / "pyinstaller_hooks" / "hook-tkinterdnd2.py"
            namespace = runpy.run_path(str(hook_path))
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertEqual(namespace["datas"], [("tkinterdnd2", "tkdnd")])


class BuildSpecTests(unittest.TestCase):
    def test_spec_packages_composition_entrypoint_and_runtime_data_without_planning_documents(self) -> None:
        hooks = types.ModuleType("PyInstaller.utils.hooks")
        hooks.collect_data_files = lambda package: [("tkdnd.dll", "tkinterdnd2/tkdnd")]
        utils = types.ModuleType("PyInstaller.utils")
        pyinstaller = types.ModuleType("PyInstaller")
        previous = {name: sys.modules.get(name) for name in ("PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks")}
        sys.modules.update(
            {"PyInstaller": pyinstaller, "PyInstaller.utils": utils, "PyInstaller.utils.hooks": hooks}
        )
        captured: dict[str, object] = {}

        class AnalysisCapture:
            def __init__(self, scripts, **kwargs):
                captured["scripts"] = scripts
                captured["analysis"] = kwargs
                self.pure = []
                self.scripts = []
                self.binaries = []
                self.datas = []

        def executable_capture(*args, **kwargs):
            captured["exe"] = kwargs

        try:
            spec_path = Path(__file__).parents[2] / "PlanningPing.spec"
            runpy.run_path(
                str(spec_path),
                init_globals={"Analysis": AnalysisCapture, "PYZ": lambda pure: pure, "EXE": executable_capture},
            )
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual([r"src\planning_ping\__main__.py"], captured["scripts"])
        analysis = captured["analysis"]
        self.assertIn(
            (r"src\planning_ping\data\planning_authorities.geojson", r"planning_ping\data"),
            analysis["datas"],
        )
        self.assertIn("selenium.webdriver.chrome.webdriver", analysis["hiddenimports"])
        self.assertIn("selenium.webdriver.edge.webdriver", analysis["hiddenimports"])
        self.assertEqual([r"tools\pyinstaller_hooks"], analysis["hookspath"])
        self.assertFalse(any(Path(source).suffix.casefold() in {".pdf", ".doc", ".docx"} for source, _ in analysis["datas"]))
        self.assertEqual({"name": "PlanningPing", "console": False}, {key: captured["exe"][key] for key in ("name", "console")})


if __name__ == "__main__":
    unittest.main()
