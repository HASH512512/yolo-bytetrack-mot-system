"""Generate Markdown and JSON ablation reproduction tables (synthetic diagnostic only)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

from evaluation.ablation_experiments import (
    AblationVariantResult,
    run_ablation_suite,
)

SYNTHETIC_WARNING_NOTE = (
    "[Synthetic Benchmark - Algorithm Verification Only]"
)


def _format_diou_vdc_oru(r: AblationVariantResult) -> str:
    flags = (r.use_diou, r.use_vdc, r.use_oru)
    if all(flags):
        return "Y"
    if not any(flags):
        return "N"
    return "/".join("Y" if f else "N" for f in flags)


def format_markdown_table(results: List[AblationVariantResult]) -> str:
    """Format ablation results as a GitHub-flavored Markdown table."""
    lines = [
        f"> **{SYNTHETIC_WARNING_NOTE}**",
        "",
        "| Variant | ClassAware | NSA | DIoU/VDC/ORU | LazyApp | MOTA | IDF1 | IDSW | Frag | Track FPS |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for r in results:
        m = r.metrics
        lines.append(
            f"| {r.name} | {'Y' if r.use_class_aware else 'N'} | "
            f"{'Y' if r.use_nsa else 'N'} | "
            f"{_format_diou_vdc_oru(r)} | "
            f"{'Y' if r.use_lazy_appearance else 'N'} | "
            f"{m.mota:.4f} | {m.idf1:.4f} | {m.id_switches} | "
            f"{m.fragmentations} | {r.fps:.1f} |"
        )
    return "\n".join(lines)


def generate_reproduction_table(
    output_dir: Optional[str | Path] = None,
    num_frames: int = 150,
    num_targets: int = 8,
    seed: int = 42,
) -> List[AblationVariantResult]:
    """Run synthetic diagnostic ablation experiments and optionally save Markdown + JSON."""
    results = run_ablation_suite(
        num_frames=num_frames, num_targets=num_targets, seed=seed
    )
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "ablation_table.md").write_text(
            format_markdown_table(results) + "\n", encoding="utf-8"
        )
        (out / "ablation_results.json").write_text(
            json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run synthetic diagnostic ByteTrack ablation experiments and generate table."
    )
    parser.add_argument("--output-dir", type=str, default="evaluation/outputs")
    parser.add_argument("--num-frames", type=int, default=150)
    parser.add_argument("--num-targets", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    results = generate_reproduction_table(
        output_dir=args.output_dir,
        num_frames=args.num_frames,
        num_targets=args.num_targets,
        seed=args.seed,
    )
    print(format_markdown_table(results))


if __name__ == "__main__":
    main()
