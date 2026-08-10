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


if __name__ == "__main__":
    unittest.main()
