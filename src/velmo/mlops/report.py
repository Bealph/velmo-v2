"""Génération du rapport lisible `mlops/report.md` (les 5 signaux du brief).

    python -m velmo.mlops.report results/scores.json --out mlops/report.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import _load_scores, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Écrit le rapport de suivi Markdown.")
    parser.add_argument("scores", type=Path, help="JSON produit par `python -m velmo.mlops.eval`")
    parser.add_argument("--out", type=Path, default=Path("mlops/report.md"))
    parser.add_argument("--min-global", type=float, default=90.0)
    args = parser.parse_args()

    scores = _load_scores(args.scores)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_report(scores, args.out, args.min_global / 100.0)
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
