"""Pose UNE question a Velmo depuis le terminal et affiche l'URL de sa trace Langfuse.

    uv run --extra obs --extra llm --extra demo python scripts/ask.py "Comment retourner un maillot ?"
    uv run ... python scripts/ask.py --user C-sophie-martin "Ma commande O-2024-0107 ?"

Interet : un tour complet, non interactif, avec le lien direct vers sa trace — parfait
pour demontrer l'observabilite sans naviguer dans l'interface Langfuse.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from velmo import observability as obs  # noqa: E402
from velmo.agent import build_default_agent  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Une question a Velmo, une trace Langfuse.")
    parser.add_argument("question", nargs="+", help="La question a poser")
    parser.add_argument("--user", default="C-marc-dubois", help="Client authentifie (R3)")
    args = parser.parse_args()
    question = " ".join(args.question)

    agent = build_default_agent()
    start = time.perf_counter()
    answer = agent.respond(args.user, question)
    latence_ms = (time.perf_counter() - start) * 1000

    trace = agent.last_trace
    print(f"\n\033[1mVous\033[0m  ({args.user}) : {question}")
    print(f"\n\033[1mVelmo\033[0m : {answer}\n")
    print(f"  route      : {trace['route']}")
    print(f"  garde-fous : entree={trace['input']} · sortie={trace['output']}")
    print(f"  sources    : {trace['kb_sources'] or '—'}")
    print(f"  latence    : {latence_ms:.0f} ms")

    # L'export est asynchrone : sans flush, un script court se termine AVANT l'envoi.
    obs.flush()
    url = obs.last_trace_url()
    print(f"\n  TRACE : {url}" if url else
          "\n  (observabilite inactive : renseigner LANGFUSE_PUBLIC_KEY dans .env)")


if __name__ == "__main__":
    main()
