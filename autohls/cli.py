"""Command-line entry points for scripted AutoHLS evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import DEVICE, explore
from .vitis import run_synthesis
from .benchmarks import BENCHMARKS, VALIDATION_BENCHMARKS
from .experiments import run_experiment
from .planner import COVERAGE_POLICY


def _write_result(result: dict, output: str | None) -> None:
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
        print(f"报告已写入: {destination.resolve()}")
    else:
        print(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(prog="autohls", description="天枢智构 AutoHLS 命令行工具")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("explore", help="运行确定性设计空间探索")
    demo.add_argument("source", help="C/C++ 源文件")
    demo.add_argument("--top", help="顶层函数；省略时自动识别")
    demo.add_argument("--goal", choices=("balanced", "latency", "resource"), default="balanced")
    demo.add_argument("--output", "-o", help="JSON 报告输出路径")

    real = commands.add_parser("synthesize", help="调用 Vivado/Vitis HLS 运行一次真实基线综合")
    real.add_argument("source", help="C/C++ 源文件")
    real.add_argument("--top", required=True, help="顶层函数")
    real.add_argument("--part", default=DEVICE["part"], help="AMD 器件型号")
    real.add_argument("--clock", type=float, default=DEVICE["clock_ns"], help="目标时钟周期（ns）")
    real.add_argument("--output-dir", default="artifacts/vitis", help="综合运行目录")
    real.add_argument("--report", help="将解析后的 JSON 报告写入该路径")

    research = commands.add_parser("research", help="真实 C++ 验证 / 带证据的 HLS 搜索")
    research.add_argument("benchmark", choices=(*BENCHMARKS, *VALIDATION_BENCHMARKS, "all"))
    research.add_argument("--backend", choices=("native", "hls"), default="native")
    research.add_argument("--planner", choices=("exhaustive", "random", "ollama"), default="exhaustive")
    research.add_argument("--budget", type=int, default=8, help="候选评估次数，包含基线，最多30")
    research.add_argument("--goal", choices=("balanced", "latency", "resource"), default="balanced")
    research.add_argument("--seed", type=int, default=0)
    research.add_argument("--model", default="", help="已安装的本地 Ollama 模型名称")
    research.add_argument("--no-feedback", action="store_true", help="消融实验：不给模型历史综合反馈")
    research.add_argument("--candidate-order-seed", type=int, help="可选诊断：重排模型候选呈现顺序，不改变空间或生成seed；每步使用此值+评估序号")
    research.add_argument("--coverage-policy", choices=("none", COVERAGE_POLICY), default="none",
                          help="可选共同调度：前三次尝试覆盖基线/II=1/II=2，之后自由选择；随机/模型均支持，至少3次预算")
    research.add_argument("--cosim", action="store_true", help="对每个候选运行 RTL 协同仿真")
    research.add_argument("--clock", type=float, default=DEVICE["clock_ns"])
    research.add_argument("--limits", help="资源约束 JSON 文件，含 lut/ff/dsp/bram；bram 单位为18K")
    research.add_argument("--output-dir", default="artifacts/research")

    args = parser.parse_args()
    if args.command == "research":
        limits = json.loads(Path(args.limits).read_text(encoding="utf-8")) if args.limits else None
        names = BENCHMARKS if args.benchmark == "all" else [args.benchmark]
        failed = False
        for name in names:
            try:
                directory = run_experiment(name, args.output_dir, backend=args.backend, planner=args.planner,
                                           budget=args.budget, goal=args.goal, seed=args.seed, model=args.model,
                                           feedback=not args.no_feedback, clock_ns=args.clock, cosim=args.cosim, limits=limits,
                                           candidate_order_seed=args.candidate_order_seed, coverage_policy=args.coverage_policy)
            except (RuntimeError, ValueError) as exc:
                parser.exit(2, f"{exc}\n")
            print(f"Evidence: {directory}")
            summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            failed |= not summary["completed_budget"] or summary["correctness_passed"] != summary["evaluated"]
            if args.backend == "hls":
                failed |= not summary["feasible_synthesized"]
        if failed:
            parser.exit(1, "Experiment has failures or missing hardware evidence; inspect summary.json\n")
        return
    source = Path(args.source)
    if args.command == "explore":
        result = explore(source.read_text(encoding="utf-8"), top=args.top, goal=args.goal)
        _write_result(result, args.output)
    else:
        result = run_synthesis(source, args.top, args.output_dir, args.part, args.clock)
        _write_result(result, args.report)


if __name__ == "__main__":
    main()
