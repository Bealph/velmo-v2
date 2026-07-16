"""Porte qualité (le gate bloquant de la CI).

    python -m velmo.mlops.score results/scores.json --min-global 90

Sort en erreur (`exit 1`) si la note globale est sous le seuil OU si un plancher dur
est violé → le job CI échoue → avec la protection de branche, le merge est refusé =
« livraison bloquée » (D25/D27). Le seuil est passé en /100 (lisible) puis converti
en fraction, l'unité canonique interne.

Les planchers durs (FP=0, mémoire≥90 %, blocage≥95 %) sont les constantes D25 câblées
dans `enforce_threshold` — pas des flags : ce sont des invariants de conception, pas
des réglages d'exécution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import DeliveryBlocked, _load_scores, enforce_threshold


def main() -> None:
    parser = argparse.ArgumentParser(description="Porte qualité : bloque la livraison sous le seuil.")
    parser.add_argument("scores", type=Path, help="JSON produit par `python -m velmo.mlops.eval`")
    parser.add_argument("--min-global", type=float, default=90.0, help="Seuil global /100 (défaut 90).")
    args = parser.parse_args()

    scores = _load_scores(args.scores)
    try:
        enforce_threshold(scores, args.min_global / 100.0)
    except DeliveryBlocked as exc:
        print(f"PORTE QUALITE : {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"PORTE QUALITE : OK (global={scores.global_ * 100:.0f}/100, seuil {args.min_global:.0f})")


if __name__ == "__main__":
    main()
