"""Démo mémoire Velmo 2.0 — le parcours d'un message.

Objectif (soutenance) : montrer, concrètement, qu'un message
  1. ENVOYÉ à la mémoire
  2. ATTERRIT en dur dans la base relationnelle (tables messages + faits_semantiques)
  3. RESSORT assemblé par la mémoire de travail (read().render()) + inspectable (R6)

Tout est hors-ligne (SQLite fichier, aucune dépendance externe). Lancer :

    uv run python scripts/demo_memory.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# --- 0. Base VISIBLE (pas un tmp) pour pouvoir l'inspecter à la main ---------- #
# On pointe VELMO_MEMORY_DB AVANT de construire le MemoryManager : la fabrique
# lit cette variable au moment de __init__ (c'est tout l'intérêt de l'avoir mise
# dans le constructeur et pas au niveau module).
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_memory.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_PATH.unlink(missing_ok=True)  # on repart d'une base propre à chaque démo
os.environ["VELMO_MEMORY_DB"] = str(DB_PATH)

from velmo.memory import MemoryManager  # noqa: E402  (import après avoir posé l'env)

USER = "C-demo-marc"


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# --------------------------------------------------------------------------- #
# 1. ENVOYER un message (write = persiste le tour + extrait les faits durables)
# --------------------------------------------------------------------------- #
rule("1. ENVOI D'UN MESSAGE")
mm = MemoryManager()
message = (
    "Bonjour, je suis client pro. Ma commande O-2024-0101 est prioritaire. "
    "Mon adresse de livraison est 10 rue de la Paix. "
    "Mon code postal est le 75002. Tu peux me tutoyer."
)
reponse = "C'est noté, je m'en occupe tout de suite."
print(f"[{USER}] client  : {message}")
print(f"[{USER}] velmo   : {reponse}")
mm.write(USER, message, reponse)
print(f"\n-> écrit dans : {DB_PATH}")

# --------------------------------------------------------------------------- #
# 2. CONSULTER LA BASE EN BRUT (via sqlite3 : on prouve que c'est bien en dur)
# --------------------------------------------------------------------------- #
rule("2. LA BASE RELATIONNELLE (lecture brute SQLite, sans passer par l'agent)")
con = sqlite3.connect(DB_PATH)

print("\n-- Table `messages` (mémoire court terme, fil de conv.) --")
print(f"{'turn':>4} | {'role':<9} | content")
for turn, role, content in con.execute(
    "SELECT turn_index, role, content FROM messages WHERE user_id=? ORDER BY turn_index",
    (USER,),
):
    print(f"{turn:>4} | {role:<9} | {content}")

print("\n-- Table `faits_semantiques` (mémoire long terme, faits durables) --")
print(f"{'type':<16} | {'value':<28} | {'source':<7} | confidence")
for type_, value, source, confidence in con.execute(
    "SELECT type, value, source, confidence FROM faits_semantiques WHERE user_id=? ORDER BY type",
    (USER,),
):
    print(f"{type_:<16} | {value:<28} | {source:<7} | {confidence}")
con.close()

# --------------------------------------------------------------------------- #
# 3. CONSULTER LA MÉMOIRE (via l'API MemoryManager : mémoire de travail + R6)
# --------------------------------------------------------------------------- #
rule("3. LA MÉMOIRE DE TRAVAIL (assemblée par MemoryManager pour le LLM)")
print(mm.read(USER, "De quoi te souviens-tu ?").render())

rule("3bis. INSPECTION (R6 — traçabilité : ce que l'agent a retenu)")
print(mm.inspect(USER))

print("\nDémo terminée. Base inspectable :", DB_PATH)
