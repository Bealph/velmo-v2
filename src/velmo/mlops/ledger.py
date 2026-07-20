"""Ledger des notes : historique append-only `mlops/scores.jsonl` (D26/D31).

    python -m velmo.mlops.ledger append results/scores.json --version v2.0.0 --commit <sha>

Une ligne par version → notes comparables d'une version à l'autre (le brief), et la CI
peut détecter une chute vs la version précédente. Sur un tag, la CI committe cette ligne
(commit-back) : git devient la source de vérité de l'historique des notes.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from . import _SCORES_FILE, _load_scores, scores_to_dict


def main() -> None:
    parser = argparse.ArgumentParser(description="Ajoute une ligne de note au ledger d'historique.")
    parser.add_argument("action", choices=["append"])
    parser.add_argument("scores", help="JSON produit par `python -m velmo.mlops.eval`")
    parser.add_argument("--version", required=True, help="Identifiant sémantique de la version (ex. v2.0.0).")
    parser.add_argument("--commit", required=True, help="Hash de commit git de la version.")
    args = parser.parse_args()

    scores = _load_scores(args.scores)
    entry = scores_to_dict(
        scores,
        version=args.version,
        commit=args.commit,
        date=datetime.now().isoformat(timespec="seconds"),
    )
    _SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _SCORES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"ledger += {args.version}  (global={scores.global_ * 100:.0f}/100)")


if __name__ == "__main__":
    main()
