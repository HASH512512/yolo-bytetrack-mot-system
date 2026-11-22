"""Ablation experiment harness and MOT evaluation metrics."""

from evaluation.ablation_experiments import (
    AblationVariantResult,
    MOTMetrics,
    evaluate_mot,
    generate_occlusion_benchmark,
    load_mot_gt,
    run_ablation_suite,
)
from evaluation.make_reproduction_table import (
    format_markdown_table,
    generate_reproduction_table,
)

__all__ = [
    "AblationVariantResult",
    "MOTMetrics",
    "evaluate_mot",
    "generate_occlusion_benchmark",
    "load_mot_gt",
    "run_ablation_suite",
    "format_markdown_table",
    "generate_reproduction_table",
]
