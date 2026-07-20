"""Démo mémoire INTERACTIVE — tu tapes un message, tu le vois en BD puis en mémoire.

Boucle : tu écris un message → il est persisté → on affiche
  (a) la BASE en brut (tables messages + faits_semantiques, via sqlite3)
  (b) la MÉMOIRE assemblée (read().render() + inspect()).

Lancer (venv actif, depuis velmo-v2/) :
    python scripts/demo_memory_interactive.py
ou :
    uv run python scripts/demo_memory_interactive.py

Ligne vide pour quitter.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Base VISIBLE et repartie propre à chaque lancement (commente le unlink pour
# accumuler d'un run à l'autre et illustrer la persistance R2).
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_memory_interactive.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_PATH.unlink(missing_ok=True)
os.environ["VELMO_MEMORY_DB"] = str(DB_PATH)

from velmo.memory import MemoryManager  # noqa: E402  (import après avoir posé l'env)


def show_db(user_id: str) -> None:
    """Lit la base EN BRUT (sans passer par l'agent) → prouve que c'est écrit en dur."""
    con = sqlite3.connect(DB_PATH)
    print("\n  -- messages (mémoire court terme) --")
    for turn, role, content in con.execute(
        "SELECT turn_index, role, content FROM messages WHERE user_id=? ORDER BY turn_index",
        (user_id,),
    ):
        print(f"  {turn:>3} | {role:<9} | {content}")
    print("\n  -- faits_semantiques (mémoire long terme) --")
    rows = list(con.execute(
        "SELECT type, value, source, confidence FROM faits_semantiques WHERE user_id=? ORDER BY type",
        (user_id,),
    ))
    if not rows:
        print("  (aucun fait durable extrait de ce message)")
    for type_, value, source, confidence in rows:
        print(f"  {type_:<16} | {value:<28} | {source:<7} | {confidence}")
    con.close()


def main() -> None:
    user_id = input("user_id [C-demo] : ").strip() or "C-demo"
    mm = MemoryManager()  # une session de conversation pour toute la démo
    print("\nTape un message (ligne vide = quitter).")

    while True:
        msg = input(f"\n[{user_id}] > ").strip()
        if not msg:
            print("Fin de la démo. Base :", DB_PATH)
            break

        # 1. ENVOI : persiste le tour + extrait les faits durables
        mm.write(user_id, msg, "(réponse simulée)")

        # 2. LA BASE EN BRUT
        print("\n==================== EN BASE (brut SQLite) ====================")
        show_db(user_id)

        # 3. LA MÉMOIRE DE TRAVAIL (ce que verrait le LLM)
        print("\n==================== EN MÉMOIRE (render) =====================")
        print(mm.read(user_id, msg).render())


if __name__ == "__main__":
    main()
