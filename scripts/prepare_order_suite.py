"""Freeze the proposed 39-job comparison; this module never starts experiments."""
from pathlib import Path

from scripts import model_suite, prepare_model_suite
from scripts.model_suite import command


def protocol():
    plan = model_suite.protocol()
    plan.update(experiment="candidate-order-comparison-v1", candidate_order_comparison=True,
                candidate_order_policy="remaining-shuffle-v1",
                candidate_order_seed_rule="base + evaluation_index", jobs=[])
    schedule = [("random", "none", 0, 0),
                ("no-feedback", "canonical", 0, 0), ("no-feedback", "shuffled", 0, 0),
                ("feedback", "shuffled", 0, 0), ("feedback", "canonical", 0, 0),
                ("feedback", "canonical", 0, 1), ("feedback", "shuffled", 0, 1),
                ("no-feedback", "shuffled", 0, 1), ("no-feedback", "canonical", 0, 1)]
    schedule += [("random", "none", seed, 0) for seed in range(1, 5)]
    for benchmark in ("matmul", "fir", "conv2d"):
        for group, condition, seed, repeat in schedule:
            plan["jobs"].append({"id": f"{benchmark}-{group}-{condition}-s{seed}-r{repeat}",
                "benchmark": benchmark, "group": group, "order_condition": condition,
                "seed": seed, "repeat": repeat,
                "candidate_order_seed": 20260927 if condition == "shuffled" else None})
    return plan


if __name__ == "__main__":
    prepare_model_suite.main(Path("artifacts/model-order-suite-20260927/preparation-v1"), protocol(),
                             "docs/MODEL_ORDER_SUITE_PLAN_20260927.md")
