#!/usr/bin/env python3
"""Batch-evaluate retrieval accuracy on several Zizhi Tongjian Zhouji samples."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.zhouji_eval_samples import ZHOUJI_30_SAMPLES
from zztj_agent import retrieve


@dataclass(frozen=True)
class Sample:
    label: str
    query: str
    expected_any: tuple[str, ...]


ADAPTED_SAMPLES = (
    Sample(
        label="三家分晋",
        query="威烈王二十三年，初命晉大夫魏斯、趙籍、韓虔為諸侯。",
        expected_any=("史記_卷039", "史記_卷044"),
    ),
    Sample(
        label="求九鼎",
        query="顯王三十三年，秦興師臨周而求九鼎，周君患之，以告顏率。",
        expected_any=("戰國策_東周",),
    ),
    Sample(
        label="晋阳之围",
        query="知伯帥韓、魏而攻趙，決晉水以灌晉陽，城不浸者三版。",
        expected_any=("史記_卷044",),
    ),
    Sample(
        label="胡服骑射",
        query="趙武靈王北略中山地，遂胡服騎射。",
        expected_any=("史記_卷043",),
    ),
    Sample(
        label="河西献地",
        query="魏惠王兵數破於齊秦，國內空，日以削，乃使割河西之地獻於秦以和。",
        expected_any=("史記_卷068",),
    ),
)


SOURCE_ALIGNED_SAMPLES = (
    Sample(
        label="封三晋（原句）",
        query="周威烈王賜趙、韓、魏皆命爲諸侯。",
        expected_any=("史記_卷039",),
    ),
    Sample(
        label="求九鼎（原句）",
        query="欲興兵臨周而求九鼎，周之君臣，內自盡計。",
        expected_any=("戰國策_東周",),
    ),
    Sample(
        label="晋阳灌城（原句）",
        query="又率韓、魏之兵以圍趙襄子於晉陽，決晉水以灌晉陽之城，不湛者三版。",
        expected_any=("史記_卷044",),
    ),
    Sample(
        label="胡服骑射（原句）",
        query="今吾將胡服騎射以教百姓，而世必議寡人，柰何。",
        expected_any=("史記_卷043",),
    ),
    Sample(
        label="河西献地（原句）",
        query="魏惠王兵數破於齊秦，國內空，日以削，恐，乃使使割河西之地獻於秦以和。",
        expected_any=("史記_卷068",),
    ),
)

ZHOUJI_30 = tuple(Sample(**item) for item in ZHOUJI_30_SAMPLES)


def _is_expected_hit(result: dict, expected_any: tuple[str, ...]) -> bool:
    haystack = f"{result.get('title', '')} {result.get('file', '')}"
    return any(token in haystack for token in expected_any)


def _first_hit_rank(results: list[dict], expected_any: tuple[str, ...]) -> int | None:
    for idx, result in enumerate(results, 1):
        if _is_expected_hit(result, expected_any):
            return idx
    return None


def _rr_and_loss(hit_rank: int | None) -> tuple[float, float]:
    if hit_rank is None:
        return 0.0, 1.0
    rr = 1.0 / hit_rank
    return rr, 1.0 - rr


def run_suite(name: str, samples: tuple[Sample, ...], top_k: int) -> None:
    print(f"\n=== {name} ===")
    top1_hits = 0
    top3_hits = 0
    mrr_sum = 0.0
    loss_sum = 0.0
    worst_cases = []

    for sample in samples:
        results = retrieve(sample.query, top_k=top_k)
        hit_rank = _first_hit_rank(results, sample.expected_any)
        top1 = hit_rank == 1
        top3 = hit_rank is not None and hit_rank <= min(3, top_k)
        rr, loss = _rr_and_loss(hit_rank)
        top1_hits += int(top1)
        top3_hits += int(top3)
        mrr_sum += rr
        loss_sum += loss
        worst_cases.append((loss, sample.label, hit_rank, results[: min(3, len(results))]))

        print(f"\n[{sample.label}]")
        print(f"query: {sample.query}")
        print(f"expected: {', '.join(sample.expected_any)}")
        print(
            f"hit_rank: {hit_rank if hit_rank is not None else 'MISS'} | "
            f"rr={rr:.3f} | loss={loss:.3f}"
        )
        for idx, result in enumerate(results, 1):
            marker = "OK" if _is_expected_hit(result, sample.expected_any) else ".."
            print(
                f"  {idx}. {marker} {result['title']} | {result['file']} | "
                f"score={result['score']:.4f}"
            )

    total = len(samples)
    worst_cases.sort(key=lambda item: (-item[0], item[1]))
    print(
        f"\nsummary: top1={top1_hits}/{total} ({top1_hits / total:.0%}), "
        f"top3={top3_hits}/{total} ({top3_hits / total:.0%}), "
        f"mrr={mrr_sum / total:.3f}, avg_loss={loss_sum / total:.3f}"
    )
    print("\nworst_cases:")
    for loss, label, hit_rank, results in worst_cases[:5]:
        first = results[0] if results else {}
        print(
            f"  - {label}: hit_rank={hit_rank if hit_rank is not None else 'MISS'}, "
            f"loss={loss:.3f}, top1={first.get('title', 'N/A')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("adapted", "aligned", "zhouji30", "all"),
        default="zhouji30",
        help="Which sample suite to run.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="How many retrieval results to print for each sample.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.suite in ("adapted", "all"):
        run_suite("周纪改写样本", ADAPTED_SAMPLES, top_k=args.top_k)
    if args.suite in ("aligned", "all"):
        run_suite("原句对齐样本", SOURCE_ALIGNED_SAMPLES, top_k=args.top_k)
    if args.suite in ("zhouji30", "all"):
        run_suite("周纪三十样本", ZHOUJI_30, top_k=args.top_k)


if __name__ == "__main__":
    main()
