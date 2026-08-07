"""Fixtures de test : base SQLite seedée, FAQ locale, agents — tout hors-ligne."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from velmo.agent import Agent
from velmo.db import fresh_sqlite_session
from velmo.guardrails import Decision, GuardrailEngine
from velmo.kb_store import LocalKB
from velmo.llm import EchoLLM
from velmo.memory import MemoryManager
from velmo.sampledata import seed

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"


def load_jsonl(name: str) -> list[dict]:
    text = (EVAL_DIR / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def seeded_session():
    session = fresh_sqlite_session()
    seed(session)
    return session


class AllowAllGuardrails:
    """Garde-fous neutralisés (agent dégradé pour le test de régression)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def check_input(self, message: str, **_) -> Decision:
        return Decision(allowed=True, action="allow")

    # `**_` : suit l'interface de GuardrailEngine, qui accepte désormais `llm_generated`
    # (le LLM-juge de sortie ne s'applique qu'au contenu généré par le modèle).
    def check_output(self, text: str, **_) -> Decision:
        return Decision(allowed=True, action="allow")


def build_reference_agent() -> Agent:
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(),
        guardrails=GuardrailEngine(),
        session=seeded_session(),
        kb=LocalKB(),
    )


def build_degraded_agent() -> Agent:
    return Agent(
        llm=EchoLLM(),
        memory=MemoryManager(),
        guardrails=AllowAllGuardrails(),
        session=seeded_session(),
        kb=LocalKB(),
    )


@pytest.fixture(scope="session", autouse=True)
def stockage_memoire_isole(tmp_path_factory):
    """Isole le stockage mémoire de la suite, et le protège d'une URL d'environnement.

    Deux effets, tous deux voulus :

      1. les tests n'écrivent plus dans `data/velmo_memory.sqlite`, le fichier de
         développement, qu'ils polluaient jusqu'ici ;
      2. une `VELMO_MEMORY_DB_URL` présente dans l'environnement est **neutralisée** le
         temps de la suite. Sans cette précaution, lancer les tests sur un poste
         configuré pour le déploiement les ferait écrire dans la base de PRODUCTION —
         la résolution du store retombant sur l'URL faute de chemin explicite.

    Portée **session** et non fonction : `test_cross_session_persistence` crée plusieurs
    MemoryManager qui doivent partager le même store, exactement comme en production.
    Une isolation par test casserait ce qu'on cherche justement à vérifier.
    """
    fichier = tmp_path_factory.mktemp("memoire") / "velmo_memory_test.sqlite"
    anciens = {
        "VELMO_MEMORY_DB": os.environ.get("VELMO_MEMORY_DB"),
        "VELMO_MEMORY_DB_URL": os.environ.get("VELMO_MEMORY_DB_URL"),
    }
    os.environ["VELMO_MEMORY_DB"] = str(fichier)
    os.environ.pop("VELMO_MEMORY_DB_URL", None)
    try:
        yield fichier
    finally:
        for nom, valeur in anciens.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur


@pytest.fixture
def db_session():
    session = seeded_session()
    yield session
    session.close()


@pytest.fixture
def kb() -> LocalKB:
    return LocalKB()


@pytest.fixture
def reference_agent() -> Agent:
    return build_reference_agent()


@pytest.fixture
def degraded_agent() -> Agent:
    return build_degraded_agent()
