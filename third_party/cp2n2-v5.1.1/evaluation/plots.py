"""Simple plotting utilities for prototype evaluation."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt


def save_bar_chart(
    labels: list[str],
    values: list[float],
    title: str,
    ylabel: str,
    output_path: str | Path,
) -> None:
    """Save a simple bar chart."""
    output_path = Path(output_path)
    plt.figure(figsize=(8, 4.5))
    plt.bar(labels, values)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()


def save_grouped_bar_chart(
    labels: list[str],
    series_a: list[float],
    series_b: list[float],
    series_a_label: str,
    series_b_label: str,
    title: str,
    ylabel: str,
    output_path: str | Path,
) -> None:
    """Save a grouped bar chart with two series."""
    output_path = Path(output_path)

    x = list(range(len(labels)))
    width = 0.38

    plt.figure(figsize=(9, 4.8))
    plt.bar([value - width / 2 for value in x], series_a, width=width, label=series_a_label)
    plt.bar([value + width / 2 for value in x], series_b, width=width, label=series_b_label)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(x, labels, rotation=20, ha="right")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()
