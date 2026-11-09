"""Regenerate all RQ2 figures from an archived A6 summary CSV."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def regenerate(summary_csv: str | Path, output_dir: str | Path) -> list[Path]:
    summary_csv = Path(summary_csv)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with summary_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["profile_id"]].append(row)

    latency_path = output_dir / "rq2-p95-latency.png"
    plt.figure(figsize=(8.5, 4.8))
    for profile_id, profile_rows in sorted(grouped.items()):
        ordered = sorted(profile_rows, key=lambda row: int(row["client_count"]))
        plt.plot(
            [int(row["client_count"]) for row in ordered],
            [float(row["p95_latency_ms"]) for row in ordered],
            marker="o",
            label=profile_id,
        )
    plt.xlabel("Competing clients")
    plt.ylabel("P95 end-to-end latency (ms)")
    plt.title("RQ2: distributed CP²N² latency under load and faults")
    plt.xticks(sorted({int(row["client_count"]) for row in rows}))
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(latency_path, dpi=200)
    plt.close()

    success_path = output_dir / "rq2-success-rate.png"
    plt.figure(figsize=(8.5, 4.8))
    for profile_id, profile_rows in sorted(grouped.items()):
        ordered = sorted(profile_rows, key=lambda row: int(row["client_count"]))
        plt.plot(
            [int(row["client_count"]) for row in ordered],
            [float(row["success_rate"]) for row in ordered],
            marker="o",
            label=profile_id,
        )
    plt.xlabel("Competing clients")
    plt.ylabel("Successful requests / submitted requests")
    plt.ylim(-0.02, 1.02)
    plt.title("RQ2: completion rate under contention and faults")
    plt.xticks(sorted({int(row["client_count"]) for row in rows}))
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(success_path, dpi=200)
    plt.close()
    return [latency_path, success_path]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary_csv")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    for path in regenerate(args.summary_csv, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
