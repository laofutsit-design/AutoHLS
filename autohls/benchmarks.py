"""Reviewed benchmark contracts and bounded, reproducible pragma transforms.

Only the bundled kernels are supported here. This is intentionally not a C++
parser: unfamiliar code must receive its own reviewed contract and testbench.
"""

from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Benchmark:
    name: str
    loop: str
    arrays: tuple[tuple[str, int], ...]

    @property
    def source(self) -> Path:
        return ROOT / "examples" / f"{self.name}.cpp"

    @property
    def testbench(self) -> Path:
        return ROOT / "benchmarks" / f"{self.name}_tb.cpp"


BENCHMARKS = {
    "matmul": Benchmark("matmul", "DOT", (("a", 2), ("b", 1))),
    "fir": Benchmark("fir", "TAP", (("input", 1), ("coeff", 1))),
    "conv2d": Benchmark("conv2d", "KERNEL_X", (("input", 2), ("weights", 2))),
}

# Explicit opt-in only: keep `research all` and development-suite defaults intact.
VALIDATION_BENCHMARKS = {
    "prefixsum": Benchmark("prefixsum", "SCAN", (("input", 1),)),
}


@dataclass(frozen=True)
class Configuration:
    pipeline_ii: int = 0
    unroll: int = 1
    partition: int = 1

    def __post_init__(self) -> None:
        for name, allowed in (("pipeline_ii", (0, 1, 2)), ("unroll", (1, 2, 4, 8)), ("partition", (1, 2, 4, 8))):
            value = getattr(self, name)
            if type(value) is not int or value not in allowed:
                raise ValueError(f"Invalid {name}: {value}")
        if self.partition > self.unroll:
            raise ValueError("Partition exceeds this experiment's bounded search space")

    @property
    def key(self) -> str:
        return f"p{self.pipeline_ii}-u{self.unroll}-a{self.partition}"


def configurations() -> list[Configuration]:
    return [Configuration(ii, unroll, partition)
            for ii, unroll, partition in product((0, 1, 2), (1, 2, 4, 8), (1, 2, 4, 8))
            if partition <= unroll]


def render_candidate(benchmark: Benchmark, config: Configuration) -> str:
    source = benchmark.source.read_text(encoding="utf-8")
    pragmas = []
    if config.pipeline_ii:
        pragmas.append(f"#pragma HLS PIPELINE II={config.pipeline_ii}")
    if config.unroll > 1:
        pragmas.append(f"#pragma HLS UNROLL factor={config.unroll}")
    pattern = rf"(?m)^{re.escape(benchmark.loop)}:\s*\n\s*for\s*\([^\n]*\)\s*\{{"
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError(f"Expected one labeled loop: {benchmark.name}/{benchmark.loop}")
    if pragmas:
        end = matches[0].end()
        source = source[:end] + "\n" + "\n".join(pragmas) + source[end:]
    if config.partition > 1:
        function = re.search(rf"\bvoid\s+{benchmark.name}\s*\([^;{{}}]*\)\s*\{{", source)
        if function is None:
            raise ValueError(f"Top function not found: {benchmark.name}")
        for name, dimension in benchmark.arrays:
            if not re.search(rf"\b{name}\s*\[", function.group()):
                raise ValueError(f"Array contract no longer matches: {name}")
        partitions = [f"#pragma HLS ARRAY_PARTITION variable={name} cyclic factor={config.partition} dim={dim}"
                      for name, dim in benchmark.arrays]
        source = source[:function.end()] + "\n" + "\n".join(partitions) + source[function.end():]
    return source


def config_record(config: Configuration) -> dict:
    return {"id": config.key, **asdict(config)}
