"""CLI d'évaluation : construit un agent offline déterministe, exécute les 3 suites
et écrit les notes en JSON.

    python -m velmo.mlops.eval --out results/scores.json

Agent offline (SQLite seedé + EchoLLM + KB locale + garde-fous réels) : la CI est
reproductible sans Postgres ni clé LLM — c'est le socle du déterminisme (D25).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from velmo.agent import Agent
from velmo.db import fresh_sqlite_session
from velmo.guardrails import GuardrailEngine
from velmo.kb_store import LocalKB
from velmo.llm import EchoLLM
from velmo.memory import MemoryManager
from velmo.sampledata import seed

from . import current_version, run_eval, scores_to_dict


def build_eval_agent() -> Agent:
    """Agent de référence pour l'éval : tout hors-ligne, déterministe et seedé."""
    session = fresh_sqlite_session()
    seed(session)
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(),
        guardrails=GuardrailEngine(),
        session=session,
        kb=LocalKB(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Évaluation qualité Velmo (3 suites → notes).")
    parser.add_argument("--out", type=Path, default=Path("results/scores.json"))
    args = parser.parse_args()

    scores = run_eval(build_eval_agent())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = scores_to_dict(scores, version=current_version())
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"eval OK -> {args.out}  (global={scores.global_ * 100:.0f}/100)")


if __name__ == "__main__":
    main()
