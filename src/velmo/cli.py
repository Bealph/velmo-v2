"""REPL de conversation Velmo 2.0 (commandes, dispo, FAQ) — démarre après seed."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .agent import build_default_agent


def _force_utf8_output() -> None:
    """Rend la sortie console tolerante a tout caractere renvoye par le LLM.

    Sur Windows la console est en cp1252 : un simple emoji dans une reponse du modele
    (frequent) levait UnicodeEncodeError et TUAIT la session de chat. On force l'UTF-8
    avec repli `replace` : plus aucun caractere ne peut interrompre la conversation.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # flux redirige/non reconfigurable : on n'insiste pas
                pass


def main() -> None:
    _force_utf8_output()
    load_dotenv()
    parser = argparse.ArgumentParser(description="Chat support Velmo 2.0")
    parser.add_argument("--user", default="C-marc-dubois", help="Identifiant client authentifié")
    args = parser.parse_args()

    agent = build_default_agent()
    print(f"Velmo 2.0 prêt (client {args.user}). Posez votre question (Ctrl+C pour quitter).")
    while True:
        try:
            message = input("\nVous : ").strip()
            if not message:
                continue
            print(f"\nVelmo : {agent.respond(args.user, message)}")
        except (KeyboardInterrupt, EOFError):
            print("\nÀ bientôt !")
            break


if __name__ == "__main__":
    main()
