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


def get_llm() -> LLM:
    """Construit le client Azure (OpenAI-compatible) si configuré, sinon `EchoLLM`."""
    endpoint = os.getenv("AZURE_AI_INFERENCE_ENDPOINT")
    if not endpoint:
        return EchoLLM()

    from openai import OpenAI

    client = OpenAI(base_url=endpoint, api_key=os.environ["AZURE_AI_INFERENCE_API_KEY"])
    model = os.environ.get("AZURE_AI_INFERENCE_MODEL", "grok-4.3")
    return AzureLLM(client, model)
