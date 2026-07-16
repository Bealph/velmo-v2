"""Démo garde-fous — PREUVE PAR LOT sur les cas d'évaluation.

Passe chaque cas de eval/guardrail_cases.jsonl dans check_input/check_output et
affiche le verdict par cas + un bilan chiffré : blocage des hostiles, faux positifs,
PII en sortie. La 1re ligne (regex) seule est exercée ici — déterministe, sans LLM.

Lancer :
    uv run python scripts/demo_guardrails.py
"""

from __future__ import annotations

import json
from pathlib import Path

from velmo.guardrails import GuardrailEngine

CASES = Path(__file__).resolve().parent.parent / "eval" / "guardrail_cases.jsonl"


def load() -> list[dict]:
    text = CASES.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main() -> None:
    cases = load()
    engine = GuardrailEngine()  # 1re ligne déterministe (pas de LLM-juge branché)

    rows = []
    for c in cases:
        msg, where, expected = c["message"], c["where"], c["expected_action"]
        dec = engine.check_input(msg) if where == "input" else engine.check_output(msg)
        ok = dec.action == expected
        rows.append((c["id"], c["category"], where, expected, dec.action, dec.category or "-", ok))

    print(f"{'id':<22} {'cat. attendue':<15} {'ou':<7} {'attendu':<8} {'obtenu':<8} {'detectee':<16} ok")
    print("-" * 92)
    for id_, cat, where, exp, got, detected, ok in rows:
        print(f"{id_:<22} {cat:<15} {where:<7} {exp:<8} {got:<8} {detected:<16} {'OK' if ok else 'KO'}")

    hostiles = [r for r in rows if r[3] == "block"]
    legits = [r for r in rows if r[3] == "allow"]
    pii = [r for r in rows if r[1] == "pii"]
    blocked_hostiles = sum(1 for r in hostiles if r[4] == "block")
    fp = sum(1 for r in legits if r[4] == "block")
    pii_blocked = sum(1 for r in pii if r[4] == "block")
    fp_rate = (fp / len(legits) * 100) if legits else 0.0

    print("\n" + "=" * 52)
    print("BILAN")
    print("=" * 52)
    print(f"Hostiles bloques        : {blocked_hostiles}/{len(hostiles)}")
    print(f"  dont PII (sortie)      : {pii_blocked}/{len(pii)}")
    print(f"Faux positifs (legit.)  : {fp}/{len(legits)}  (taux {fp_rate:.1f} %, seuil <= 10 %)")
    print(f"Evenements journalises  : {len(engine.events)}")

    all_ok = blocked_hostiles == len(hostiles) and fp == 0
    print(f"\nVerdict : {'>>> TOUS LES CRITERES TENUS (100% bloques, 0 faux positif)' if all_ok else '>>> ECHEC'}")


if __name__ == "__main__":
    main()
