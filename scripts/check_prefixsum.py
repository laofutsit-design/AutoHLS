"""Native-only prospective prefixsum preflight. Never calls model/HLS/board tools."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

from autohls.benchmarks import Benchmark, configurations, render_candidate
from autohls.verification import find_compiler, verify_native


ROOT = Path(__file__).resolve().parents[1]
PREFIXSUM = Benchmark("prefixsum", "SCAN", (("input", 1),))
KERNEL_FAULTS = {
    "exclusive": ("acc += input[i];\n        output[i] = acc;", "output[i] = acc;\n        acc += input[i];"),
    "reset_each_step": ("acc += input[i];", "acc = input[i];"),
    "skip_last": ("i < SIZE;", "i < SIZE - 1;"),
    "narrow_accumulator": ("int32_t acc = 0;", "int16_t acc = 0;"),
    "cross_call_state": ("int32_t acc = 0;", "static int32_t acc = 0;"),
    "input_write": ("output[i] = acc;", "output[i] = acc; const_cast<int16_t*>(input)[i] = 123;"),
    "reversed_output": ("output[i] = acc;", "output[SIZE - 1 - i] = acc;"),
    "no_output": ("output[i] = acc;", "/* intentionally missing output */"),
}
GUARDS = ("input.before", "input.after", "output.before", "output.after")


def fault_inputs():
    source = PREFIXSUM.source.read_text(encoding="utf-8")
    testbench = PREFIXSUM.testbench.read_text(encoding="utf-8")
    for name, (old, new) in KERNEL_FAULTS.items():
        if source.count(old) != 1:
            raise ValueError("Fault anchor changed: " + name)
        yield name, source.replace(old, new), testbench
    call = "prefixsum(input.data, output.data);"
    if testbench.count(call) != 1:
        raise ValueError("Testbench call anchor changed")
    for guard in GUARDS:
        # Mutate a valid struct member, not an out-of-bounds array access.
        yield guard, source, testbench.replace(call, call + f"\n        {guard} = 0;")


def check(output):
    if not find_compiler():
        raise RuntimeError("Start from an x64 Native Tools prompt or provide GCC/Clang")
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "frozen-code"
    files = list((ROOT / "autohls").glob("*.py")) + [ROOT / name for name in (
        "scripts/check_prefixsum.py", "tests/test_prefixsum.py",
        "examples/prefixsum.cpp", "benchmarks/prefixsum_tb.cpp", "docs/PREFIXSUM_CONTRACT_20260927.md")]
    for path in files:
        destination = frozen / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    rows = []
    inputs = [(c.key, render_candidate(PREFIXSUM, c), PREFIXSUM.testbench.read_text(encoding="utf-8"), False)
              for c in configurations()]
    inputs += [(name, source, tb, True) for name, source, tb in fault_inputs()]
    for name, source, tb, fault in inputs:
        directory = output / ("faults" if fault else "candidates") / name
        directory.mkdir(parents=True)
        candidate, testbench = directory / "kernel.cpp", directory / "testbench.cpp"
        candidate.write_text(source, encoding="utf-8")
        testbench.write_text(tb, encoding="utf-8")
        result = verify_native(candidate, testbench, directory / "native")
        accepted = (result.get("compile_exit_code") == 0 and result.get("test_exit_code") == 1) if fault else (
            result["passed"] and (result["cases"], result["checks"], result.get("seed")) == (32, 16512, 20260927))
        row = {"id": name, "injected_fault": fault, "expected_outcome_observed": accepted, "native": result}
        (directory / "result.json").write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        rows.append(row)
        print(f"{name}: {'accepted' if accepted else 'FAILED'}", flush=True)
    summary = {"stage": "native-contract-preflight", "passed": all(r["expected_outcome_observed"] for r in rows),
               "candidates": 30, "faults": 12, "candidate_checks": sum(r["native"]["checks"] for r in rows if not r["injected_fault"]),
               "model_calls": 0, "hls_runs": 0, "rtl_verified": False, "board_verified": False, "results": rows}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    checksums = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(output.rglob("*")) if p.is_file() and p.suffix in {".cpp", ".py", ".md", ".json", ".log"}}
    (output / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    return summary["passed"]


if __name__ == "__main__":
    sys.exit(0 if check(Path(sys.argv[1]).resolve()) else 1)
