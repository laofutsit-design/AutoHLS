"""Dispatch-only tests; real HLS evidence is produced separately on the cloud."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Linux launcher tests")
class CloudLauncherTests(unittest.TestCase):
    def test_dispatch_preserves_paths_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory(prefix="cloud launcher ") as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            launcher = scripts / "cloud-xilinx.sh"
            shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/cloud-xilinx.sh", launcher)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            sudo = fake_bin / "sudo"
            sudo.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(37)\n")
            sudo.chmod(0o755)
            flock = fake_bin / "flock"
            flock.write_text(f"#!{sys.executable}\nimport os,sys\nassert sys.argv[1].endswith('/vivado2019.run.lock')\nos.execvp(sys.argv[2], sys.argv[2:])\n")
            flock.chmod(0o755)
            work = root / "run with spaces"
            work.mkdir()
            env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", str(launcher), "vivado_hls", "-f", "file [1].tcl"],
                                    cwd=work, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 37)
            args = json.loads(result.stdout)
            self.assertIn(f"--bind={root}:{root}", args)
            self.assertIn(f"--chdir={work}", args)
            self.assertIn("--user=ubuntu", args)
            self.assertIn("--private-network", args)
            self.assertEqual(args[-3:], ["/opt/Xilinx/Vivado/2019.1/bin/vivado_hls", "-f", "file [1].tcl"])

    def test_rejects_other_tools_and_outside_working_directory(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts/cloud-xilinx.sh"
        for tool, message in (("bash", "Unsupported tool"), ("vivado", "inside this release")):
            result = subprocess.run(["bash", str(launcher), tool], cwd="/tmp", capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
