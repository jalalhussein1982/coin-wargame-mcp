"""Summaries over benchmark JSONL rows."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def load(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def summarize(path: Path) -> str:
    """Markdown table: one row per (model, side) with wins, score, approach, illegal attempts, tokens, time."""
    rows = load(path)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["side"])].append(r)
    lines = [
        "| model | side | games | wins | draws | avg turns | avg performance_score | illegal attempts | approach | tokens | seconds |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for (model, side), rs in sorted(groups.items()):
        n = len(rs)
        wins = sum(1 for r in rs if r["winner"] == side)
        draws = sum(1 for r in rs if r["winner"] is None)
        turns = sum(r["turns"] for r in rs) / n
        score = sum(r["performance_score"] or 0 for r in rs) / n
        illegal = sum(r["illegal_attempts"] for r in rs)
        approaches = defaultdict(int)
        for r in rs:
            approaches[r["approach"]] += 1
        approach = ", ".join(f"{k} x{v}" for k, v in sorted(approaches.items(), key=lambda kv: -kv[1]))
        tokens = sum(r["tokens"]["input"] + r["tokens"]["output"] for r in rs)
        secs = sum(r["seconds"] for r in rs)
        lines.append(f"| {model} | {side} | {n} | {wins} | {draws} | {turns:.1f} | {score:.1f} | {illegal} | {approach} | {tokens} | {secs:.1f} |")
    return "\n".join(lines)
