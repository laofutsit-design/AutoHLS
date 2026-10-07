"""Vivado / Vitis HLS integration helpers.

This module only prepares and parses real synthesis runs.  The web demo never
labels simulated metrics as measured hardware results.
"""

from __future__ import annotations

import shutil
import math
import re
import subprocess
from datetime import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .core import DEVICE


def detect_vitis() -> dict[str, Any]:
    # Keep the public helper name for existing callers; prefer PYNQ 2.5's tool.
    for name in ("vivado_hls", "vitis_hls"):
        executable = shutil.which(name)
        if executable:
            return {"available": True, "executable": executable, "engine": name.replace("_", "-"), "mode": "real"}
    return {"available": False, "executable": None, "engine": None, "mode": "demo"}


def _tcl_path(value: str | Path) -> str:
    # Double-quoted Tcl words require substitution characters to be escaped.
    value = str(value).replace("\\", "/")
    if "\n" in value or "\r" in value:
        raise ValueError("A Tcl path cannot contain a newline")
    for char in ('$', '[', ']', '"'):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def build_tcl(source_file: str, top: str, part: str, clock_ns: float, solution: str = "solution1",
              testbench: str | None = None, cosim: bool = False) -> str:
    """Build a minimal, auditable synthesis script."""
    if not re.fullmatch(r"[A-Za-z_]\w*", top) or not re.fullmatch(r"[A-Za-z_]\w*", solution):
        raise ValueError("Invalid top function or solution name")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", part):
        raise ValueError("Invalid device part")
    if not math.isfinite(clock_ns) or clock_ns <= 0:
        raise ValueError("Clock period must be positive and finite")
    if cosim and not testbench:
        raise ValueError("RTL co-simulation requires a testbench")
    return "\n".join(
        [
            "open_project autohls_project",
            f"set_top {top}",
            f"add_files {_tcl_path(source_file)}",
            *([f"add_files -tb {_tcl_path(testbench)}"] if testbench else []),
            f"open_solution -reset {solution}",
            f"set_part {{{part}}}",
            f"create_clock -period {clock_ns} -name default",
            *(["csim_design"] if testbench else []),
            "csynth_design",
            *(["cosim_design -rtl verilog"] if cosim else []),
            "exit",
            "",
        ]
    )


def run_synthesis(
    source_file: str | Path,
    top: str,
    output_root: str | Path,
    part: str = DEVICE["part"],
    clock_ns: float = DEVICE["clock_ns"],
    timeout_seconds: int = 1_800,
    testbench: str | Path | None = None,
    cosim: bool = False,
) -> dict[str, Any]:
    """Run one real baseline synthesis and return parsed measurements.

    Runs are created in timestamped directories so existing reports are never
    overwritten.  A caller must explicitly choose this function; the dashboard
    does not start long-running synthesis jobs implicitly.
    """
    tool = detect_vitis()
    executable = tool["executable"]
    if executable is None:
        raise RuntimeError("未检测到 vivado_hls 或 vitis_hls，请先配置 AMD/Xilinx HLS 环境")
    source = Path(source_file).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"源文件不存在: {source}")
    run_name = datetime.now().strftime("run-%Y%m%d-%H%M%S-%f")
    run_dir = Path(output_root).resolve() / run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    tcl_file = run_dir / "run_hls.tcl"
    tb = str(Path(testbench).resolve()) if testbench else None
    tcl_file.write_text(build_tcl(str(source), top, part, clock_ns, testbench=tb, cosim=cosim), encoding="utf-8")
    try:
        process = subprocess.run(
            [executable, "-f", str(tcl_file)],
            cwd=run_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        for filename, content in (("vitis.stdout.log", exc.stdout), ("vitis.stderr.log", exc.stderr)):
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else (content or "")
            (run_dir / filename).write_text(text, encoding="utf-8")
        raise RuntimeError(f"{tool['engine']} timed out after {timeout_seconds}s; partial logs: {run_dir}") from exc
    (run_dir / "vitis.stdout.log").write_text(process.stdout, encoding="utf-8")
    (run_dir / "vitis.stderr.log").write_text(process.stderr, encoding="utf-8")
    if process.returncode != 0:
        raise RuntimeError(f"{tool['engine']} 综合失败（退出码 {process.returncode}），日志位于 {run_dir}")
    reports = list(run_dir.glob("autohls_project/solution1/syn/report/*_csynth.xml"))
    if len(reports) != 1:
        raise RuntimeError(f"综合完成但未找到 csynth.xml，运行目录: {run_dir}")
    metrics = parse_csynth_xml(reports[0], target_clock_ns=clock_ns)
    cosim_passed = False
    if cosim:
        co_reports = list(run_dir.glob("autohls_project/solution1/sim/report/*_cosim.rpt"))
        cosim_passed = any(re.search(r"\|\s*Verilog\s*\|\s*Pass\s*\|", item.read_text(errors="replace"), re.I)
                          for item in co_reports)
        if not cosim_passed:
            raise RuntimeError(f"RTL co-simulation has no Verilog Pass evidence: {run_dir}")
    return {"engine": tool["engine"], "part": part, "run_dir": str(run_dir), "report": str(reports[0]), "metrics": metrics,
            "csim_passed": bool(testbench), "cosim_passed": cosim_passed, "tool": executable}


def parse_csynth_xml(path: str | Path, target_clock_ns: float | None = None) -> dict[str, Any]:
    """Parse the common fields from a Vitis/Vivado HLS csynth XML report."""
    root = ET.parse(path).getroot()

    def number(xpath: str, cast: type = int) -> int | float | None:
        node = root.find(xpath)
        if node is None or node.text is None or node.text.strip() in {"", "undef"}:
            return None
        try:
            value = float(node.text)
            if not math.isfinite(value) or value < 0 or (cast is int and not value.is_integer()):
                return None
            return cast(value)
        except (TypeError, ValueError):
            return None

    latency = ".//PerformanceEstimates/SummaryOfOverallLatency/"
    cycles = number(latency + "Worst-caseLatency")
    latency_basis = "worst_case"
    if cycles is None:
        cycles = number(latency + "Average-caseLatency")
        latency_basis = "average_case" if cycles is not None else "unknown"
    clock = number(".//PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod", float)
    interval = number(latency + "Interval-max")
    target = target_clock_ns if target_clock_ns is not None else number(".//UserAssignments/TargetClockPeriod", float)
    if target is not None and (not math.isfinite(target) or target <= 0):
        raise ValueError("Invalid target clock period")
    period = target if target is not None else clock
    dsp = number(".//AreaEstimates/Resources/DSP")
    if dsp is None:
        dsp = number(".//AreaEstimates/Resources/DSP48E")
    resources = {
        "lut": number(".//AreaEstimates/Resources/LUT"),
        "ff": number(".//AreaEstimates/Resources/FF"),
        "dsp": dsp,
        "bram": number(".//AreaEstimates/Resources/BRAM_18K"),
    }
    return {
        "cycles": cycles,
        "clock_ns": clock,
        "latency_us": cycles * period / 1_000 if cycles is not None and period else None,
        "target_clock_ns": target,
        "latency_basis": latency_basis,
        "latency_clock_basis": "target" if target is not None else "estimated",
        "timing_met": clock <= target if clock is not None and target is not None else None,
        "ii": interval,
        "resources": resources,
        "resource_units": {"bram": "BRAM_18K"},
    }
