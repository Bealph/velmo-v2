"""Clients LLM : Azure AI Inference (Kimi-K2.6) et repli local hors-ligne.

L'import du SDK Azure est différé pour que le harness démarre et que les tests
tournent sans dépendre du SDK ni d'un endpoint joignable.
"""

from __future__ import annotations

import os
from typing import Protocol


class LLM(Protocol):
    """Interface minimale d'un client de complétion."""

    def invoke(self, system: str, context: str, message: str) -> str: ...


class EchoLLM:
    """Repli déterministe et hors-ligne : renvoie un accusé de réception.

    Permet au harness de conversation de démarrer sans identifiants Azure.
    """

    def invoke(self, system: str, context: str, message: str) -> str:
        return f"[velmo] J'ai bien reçu : {message}"


class AzureLLM:
    """Adapte un client OpenAI-compatible (Azure AI Foundry) à l'interface `LLM`.

    L'endpoint Azure expose une API OpenAI-compatible (`/openai/v1`) → on utilise
    directement le client `openai.OpenAI(base_url=...)`, comme l'exemple officiel de
    la ressource.
    """

    def __init__(self, client, model: str) -> None:
        self._client = client
        self._model = model

    def invoke(self, system: str, context: str, message: str) -> str:
        messages = [{"role": "system", "content": system}]
        if context:
            messages.append({"role": "system", "content": f"Mémoire:\n{context}"})
        messages.append({"role": "user", "content": message})
        resp = self._client.chat.completions.create(model=self._model, messages=messages)
        return resp.choices[0].message.content


def enable_os_truststore() -> None:
    """Fait confiance au magasin de certificats de l'OS.

    Utile derrière un antivirus / proxy qui inspecte le HTTPS avec son propre CA racine :
    ce CA est présent dans le magasin Windows mais absent du bundle certifi qu'utilise
    httpx → sans ça les appels échouent en CERTIFICATE_VERIFY_FAILED. No-op (sûr) si
    `truststore` n'est pas installé (on garde alors certifi, correct hors inspection TLS).
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


def get_llm() -> LLM:
    """Construit le client Azure (OpenAI-compatible) si configuré, sinon `EchoLLM`."""
    endpoint = os.getenv("AZURE_AI_INFERENCE_ENDPOINT")
    if not endpoint:
        return EchoLLM()

    enable_os_truststore()

    # Observabilité OPTIONNELLE : si Langfuse est configuré, on passe par SON client
    # OpenAI, qui capture automatiquement chaque appel comme une `generation` (modèle,
    # tokens, latence, coût). Sinon, client standard — la CI reste ainsi hors-ligne et
    # déterministe, sans aucune dépendance à un service tiers (décision d'architecture :
    # l'observabilité ne doit jamais se trouver sur le chemin critique de la porte qualité).
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        from langfuse.openai import OpenAI
    else:
        from openai import OpenAI

    client = OpenAI(base_url=endpoint, api_key=os.environ["AZURE_AI_INFERENCE_API_KEY"])
    # Défaut aligné sur un modèle réellement déployé (grok-4.3 ne l'est plus).
    model = os.environ.get("AZURE_AI_INFERENCE_MODEL", "gpt-5.6-terra")
    return AzureLLM(client, model)
