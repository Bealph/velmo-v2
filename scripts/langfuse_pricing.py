"""Déclare le tarif de `gpt-5.6-terra` dans Langfuse (reproductible et documenté).

    uv run --extra obs --extra llm python scripts/langfuse_pricing.py

Pourquoi ce script : Langfuse calcule le coût à partir d'une table tarifaire par modèle.
`gpt-5.6-terra` n'y figurait pas → coût affiché 0, donc 100 % du coût d'un tour
attribué à tort au juge (`gpt-5.4-mini`, lui connu de Langfuse) et 0 % au modèle
principal. Sans ce fichier, le tarif ne vivrait que dans l'UI Langfuse — perdu si le
projet est recréé, et sa justification introuvable.

Dérivation du tarif (source : onglet Surveillance du déploiement Azure `gpt-5.6-terra`,
compteurs « Standard Global, par 1M tokens ») :

    Input  meter : 0,02118 € pour 10 180 tokens  → 0,02118 / 0,01018 =  2,08 € / 1M
    Output meter : 0,02022 € pour  1 670 tokens  → 0,02022 / 0,00167 = 12,11 € / 1M
    (contrôle : Input + Output + CacheWrite = 0,0679 ≈ 0,07 € = coût total affiché ✓)

Réserve assumée : ces prix sont en EUR ; Langfuse tarifie `gpt-5.4-mini` en USD. Le
total d'un projet mélange donc légèrement les deux devises. Comme le juge ne produit
que ~9 tokens de sortie, sa part est faible et l'écart de change (~8 %) est négligeable
sur le total. À convertir en USD si une cohérence stricte est requise.
"""

from __future__ import annotations

from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

import velmo  # noqa: E402  — initialise le client Langfuse (masqué)

MODEL_NAME = "gpt-5.6-terra"
MATCH_PATTERN = r"(?i)^gpt-5\.6-terra"   # matche gpt-5.6-terra-2026-07-09 et variantes de date
INPUT_PRICE = 2.0806e-6    # 2,08 € / 1M tokens
OUTPUT_PRICE = 1.2108e-5   # 12,11 € / 1M tokens


def main() -> None:
    from langfuse import get_client

    lf = get_client()
    if lf is None:
        raise SystemExit("Langfuse non configuré (LANGFUSE_PUBLIC_KEY absente).")

    # Idempotence : l'endpoint `models.list()` ne renvoie pas de façon fiable les modèles
    # custom fraîchement créés, on ne peut donc pas s'y fier pour tester l'existence. On
    # tente la création et on traite le 400 « already exists » comme un succès.
    try:
        m = lf.api.models.create(
            model_name=MODEL_NAME,
            match_pattern=MATCH_PATTERN,
            unit="TOKENS",
            input_price=INPUT_PRICE,
            output_price=OUTPUT_PRICE,
            start_date=datetime(2026, 1, 1),
        )
        print(f"Définition créée : {m.model_name} | in {m.input_price} | out {m.output_price} €/token")
        print("Note : le coût n'est calculé qu'à l'ingestion — seules les traces POSTÉRIEURES")
        print("à cette création seront valorisées (les anciennes gardent leur coût figé).")
    except Exception as exc:
        if "already exists" in str(exc).lower():
            print(f"Définition « {MODEL_NAME} » déjà présente — rien à faire.")
        else:
            raise


if __name__ == "__main__":
    main()
