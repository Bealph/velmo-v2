"""Smoke-test Azure — vérifie que get_llm() se connecte et répond.

N'affiche JAMAIS la clé ni l'endpoint complet : seulement des booléens + la réponse
du modèle. Lancer :
    uv run python scripts/smoke_llm.py
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # charge .env AVANT get_llm() (qui lit os.getenv au moment de l'appel)

from velmo.llm import EchoLLM, get_llm  # noqa: E402

print("endpoint configuré :", bool(os.getenv("AZURE_AI_INFERENCE_ENDPOINT")))
print("clé configurée     :", bool(os.getenv("AZURE_AI_INFERENCE_API_KEY")))
print("modèle             :", os.getenv("AZURE_AI_INFERENCE_MODEL") or "(défaut Kimi-K2.6)")

llm = get_llm()
print("type de client     :", type(llm).__name__)

if isinstance(llm, EchoLLM):
    print("-> EchoLLM (offline) : endpoint absent, aucun appel reseau. Rien a tester.")
else:
    print("-> appel reel a Azure en cours...")
    reply = llm.invoke(
        "Tu es un assistant de test. Reponds en une seule phrase courte.",
        "",
        "Reponds exactement : Connexion Azure OK.",
    )
    print("reponse du modele  :", reply)
    print("[OK] Connexion Azure fonctionnelle." if reply else "[!] Reponse vide.")
