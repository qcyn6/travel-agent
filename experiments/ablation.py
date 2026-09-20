"""Semantic/keyword retrieval ablation experiment entry points."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Iterable

from experiments.cases import ExperimentCase, make_experiment_cases
from experiments.runner import ExperimentResult, run_matrix, write_results
from experiments.variants import RETRIEVAL_ABLATION_VARIANTS, ExperimentVariant


async def run_retrieval_ablation(
    cases: Iterable[ExperimentCase] | None = None,
    variants: Iterable[ExperimentVariant] | None = None,
) -> list[ExperimentResult]:
    """Run semantic-only, keyword-only and hybrid retrieval variants."""

    return await run_matrix(
        cases or make_experiment_cases(),
        variants or RETRIEVAL_ABLATION_VARIANTS,
    )


def write_retrieval_ablation_results(
    results: Iterable[ExperimentResult], output_dir: str | Path
) -> dict[str, Path]:
    """Write ablation metrics beside the normal scheduling experiment files."""

    return write_results(results, output_dir, file_stem="retrieval_ablation")


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 Semantic/Keyword 检索权重消融实验")
    parser.add_argument(
        "--output",
        default="experiments/outputs",
        help="实验结果输出目录",
    )
    args = parser.parse_args()
    results = asyncio.run(run_retrieval_ablation())
    paths = write_retrieval_ablation_results(results, args.output)
    print(f"完成 {len(results)} 条检索消融实验记录")
    for path in paths.values():
        print(path)


if __name__ == "__main__":
    main()


__all__ = ["main", "run_retrieval_ablation", "write_retrieval_ablation_results"]
