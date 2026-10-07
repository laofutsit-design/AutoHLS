"""Compile and execute reviewed testbenches. No FPGA timing is inferred."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import time


def find_compiler() -> str | None:
    located = next((path for name in ("g++", "clang++", "cl") if (path := shutil.which(name))), None)
    if located:
        return located
    # Some Python launch environments restore PATH after vcvars initialization.
    # The explicit developer-environment location still supplies the real tool.
    if os.environ.get("VCToolsInstallDir"):
        msvc = Path(os.environ["VCToolsInstallDir"]) / "bin" / "Hostx64" / "x64" / "cl.exe"
        if msvc.is_file():
            return str(msvc)
    return None


def verify_native(source: Path, testbench: Path, directory: Path, timeout: int = 60) -> dict:
    compiler = find_compiler()
    if not compiler:
        raise RuntimeError("No C++ compiler on PATH; use an x64 Native Tools prompt or install GCC/Clang")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / ("verify.exe" if os.name == "nt" else "verify")
    if Path(compiler).name.lower() == "cl.exe":
        command = [compiler, "/nologo", "/EHsc", "/std:c++14", "/Od", "/utf-8", str(source.resolve()),
                   str(testbench.resolve()), f"/Fe:{executable}"]
    else:
        command = [compiler, "-std=c++14", "-O0", "-Wno-unknown-pragmas", str(source.resolve()),
                   str(testbench.resolve()), "-o", str(executable)]
    record = {"engine": "native-cpp", "command": command, "compiler": compiler, "passed": False,
              "rtl_verified": False, "cases": 0, "checks": 0}
    started = time.monotonic()
    for stage, args in (("compile", command), ("test", [str(executable)])):
        try:
            process = subprocess.run(args, cwd=directory, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            record.update(stage=stage, status="timeout")
            break
        log = process.stdout + process.stderr
        (directory / f"{stage}.log").write_text(log, encoding="utf-8")
        record[f"{stage}_exit_code"] = process.returncode
        if process.returncode:
            record.update(stage=stage, status="failed")
            break
        if stage == "test":
            match = re.search(r"^PASS cases=(\d+) checks=(\d+) seed=(\d+)\s*$", process.stdout, re.MULTILINE)
            record.update(stage=stage, status="passed" if match else "missing_test_evidence", passed=bool(match))
            if match:
                record.update(cases=int(match[1]), checks=int(match[2]), seed=int(match[3]))
    record["wall_seconds"] = round(time.monotonic() - started, 3)
    return record
