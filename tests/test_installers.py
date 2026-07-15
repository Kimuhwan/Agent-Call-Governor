from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def assert_installed(self, codex_home: Path) -> None:
        destination = codex_home / "skills" / "agent-call-governor"
        self.assertTrue((destination / "SKILL.md").is_file())
        self.assertTrue((destination / "scripts" / "agent_call_governor_runtime" / "__init__.py").is_file())
        self.assertFalse((codex_home / "skills" / "skills" / "agent-call-governor").exists())

    @unittest.skipUnless(os.name == "nt", "PowerShell installer runs on Windows")
    def test_powershell_installer_copies_moved_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "CODEX_HOME": directory}
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install.ps1")],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_installed(Path(directory))

    @unittest.skipIf(os.name == "nt", "POSIX installer runs on Linux")
    def test_posix_installer_copies_moved_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "CODEX_HOME": directory}
            result = subprocess.run(
                ["sh", str(ROOT / "install.sh")], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_installed(Path(directory))


if __name__ == "__main__":
    unittest.main()
