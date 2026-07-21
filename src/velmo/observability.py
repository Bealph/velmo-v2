"""Observabilité optionnelle (Langfuse) — entièrement no-op si non configurée.

Principe (D42) : l'observabilité ne doit JAMAIS se trouver sur le chemin critique.
- Pas de `LANGFUSE_PUBLIC_KEY`, ou SDK absent → tous les appels ici sont des no-op.
- Une panne du collecteur ne doit pas casser une conversation : les erreurs de mise
  en place sont avalées, on continue sans tracer.

Leçon apprise en instrumentant : **un span ne mesure QUE ce qu'il enveloppe**. Des
spans créés après coup affichaient 0 ms et se plaçaient à la fin de la trace. D'où
des gestionnaires de contexte, qui obligent à entourer le travail réel.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

# --------------------------------------------------------------------------- #
# Masquage PII avant envoi vers Langfuse Cloud (exigence de gouvernance D24).
#
# Les traces partent vers un service TIERS : aucune donnée personnelle ne doit en
# clair y figurer. On expurge les identifiants sensibles des input/output AVANT
# l'envoi. Ce qui reste (route, catégories de garde-fous, sources FAQ, user_id
# pseudonyme) est nécessaire au diagnostic et non sensible → conservé.
#
# Limite assumée : c'est du masquage par MOTIF (déterministe, gratuit, hors LLM).
# Une adresse en texte libre (« 12 rue des Lilas ») n'a pas de format fixe et
# n'est donc pas captée ici ; la détecter demanderait un LLM, qu'on refuse sur ce
# chemin (coût, latence, et… envoyer la PII au LLM pour la masquer serait absurde).
# --------------------------------------------------------------------------- #
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"), "[IBAN]"),
    (re.compile(r"\b\d{4}(?:[ -]?\d{4}){3}\b"), "[CARTE]"),
    (re.compile(r"\bO-\d{4}-\d{4}\b"), "[COMMANDE]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (re.compile(r"\b0[1-9](?:[ .]?\d{2}){4}\b"), "[TEL]"),
]


def _redact(value: Any) -> Any:
    """Expurge récursivement les motifs PII des chaînes, en préservant la structure."""
    if isinstance(value, str):
        for pattern, repl in _PII_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _mask(*, data: Any, **_: Any) -> Any:
    """Fonction de masquage passée à Langfuse. Ne DOIT jamais lever (sinon fuite)."""
    try:
        return _redact(data)
    except Exception:
        return "[masquage impossible]"


# Client masqué construit UNE SEULE FOIS, et AVANT tout autre usage du SDK. Le masque
# vit dans la ressource partagée keyée par public_key ; le premier client construit la
# fixe. Si un client NON masqué (ex. le wrapper langfuse.openai via get_client) se créait
# avant, le masque ne s'appliquerait jamais. D'où l'appel à init() dès l'import du paquet.
_masked_client: Any = None
_init_done: bool = False


def init() -> Any:
    """Initialise le client Langfuse masqué. Idempotent, no-op sans clé ni SDK."""
    global _masked_client, _init_done
    if _init_done:
        return _masked_client
    _init_done = True
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse import Langfuse

        _masked_client = Langfuse(mask=_mask)
    except Exception:
        _masked_client = None
    return _masked_client


# URL de la DERNIÈRE trace ouverte. `get_trace_url()` n'est valable qu'à l'intérieur du
# contexte de trace ; comme `Agent.respond` ouvre et referme ce contexte lui-même, un
# appelant (script, interface) n'a aucun moyen de la récupérer après coup. On la retient.
_last_trace_url: str | None = None


def last_trace_url() -> str | None:
    """URL Langfuse du dernier tour tracé, ou `None` si l'observabilité est inactive."""
    return _last_trace_url


# Environnement courant des traces. Les tours joués par l'ÉVALUATION (hors-ligne,
# ~20 ms, sans appel réseau) se mélangeaient aux vraies conversations (~5 000 ms) :
# toute p50 calculée dans Langfuse en devenait faussement optimiste. Langfuse sépare
# nativement par `environment` → on marque l'éval, la production reste mesurable.
_environment: str = "production"


@contextmanager
def environment(name: str) -> Iterator[None]:
    """Marque toutes les traces ouvertes dans ce contexte comme appartenant à `name`."""
    global _environment
    precedent = _environment
    _environment = name
    try:
        yield
    finally:
        _environment = precedent


def _client():
    """Client Langfuse MASQUÉ si configuré, sinon `None` (jamais d'exception).

    On renvoie l'instance masquée d'`init()` (et non `get_client()`) : garantit que
    toute trace ouverte par ce module passe par le masque, sans dépendre de l'ordre
    d'initialisation. Le wrapper `langfuse.openai`, lui, appelle `get_client()` — il
    reçoit un wrapper de la MÊME ressource masquée, à condition qu'`init()` ait été
    appelé en premier (fait à l'import du paquet, cf. velmo/__init__)."""
    return init()


def enabled() -> bool:
    """Vrai si les traces partent réellement vers un collecteur."""
    return _client() is not None


@contextmanager
def turn(name: str, *, user_id: str, session_id: str, input: Any) -> Iterator[None]:
    """Trace UN tour de conversation, racine de la trace.

    `user_id` et `session_id` sont propagés à tous les spans enfants : sans eux,
    impossible de filtrer les traces par client ni de suivre une conversation.
    """
    lf = _client()
    contexts = None
    if lf is not None:
        try:
            from langfuse import propagate_attributes

            contexts = (
                lf.start_as_current_observation(name=name, as_type="agent", input=input),
                propagate_attributes(user_id=user_id, session_id=session_id,
                                     environment=_environment),
            )
        except Exception:
            contexts = None          # mise en place impossible → on trace pas, on continue

    if contexts is None:
        yield
        return

    global _last_trace_url
    observation, attributes = contexts
    with observation, attributes:    # les erreurs du CORPS remontent normalement
        try:
            _last_trace_url = lf.get_trace_url()
        except Exception:
            _last_trace_url = None
        yield


@contextmanager
def step(name: str, as_type: str = "span", **kwargs: Any) -> Iterator[None]:
    """Trace une étape À L'INTÉRIEUR d'un tour (garde-fou, mémoire, routage, RAG).

    `as_type` accepte les types que Langfuse reconnaît et affiche spécifiquement :
    `guardrail`, `retriever`, `tool`, `generation`… — ce qui rend la trace lisible
    sans avoir à déchiffrer des noms de spans.
    """
    lf = _client()
    observation = None
    if lf is not None:
        try:
            observation = lf.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        except Exception:
            observation = None

    if observation is None:
        yield
        return
    with observation:
        yield


def update(**kwargs: Any) -> None:
    """Complète le span courant (sortie, métadonnées) une fois le travail terminé."""
    lf = _client()
    if lf is None:
        return
    try:
        lf.update_current_span(**kwargs)
    except Exception:
        pass


def score(name: str, value: Any, *, comment: str | None = None) -> None:
    """Attache une note à la trace courante — le pont avec l'évaluation (chantier 3)."""
    lf = _client()
    if lf is None:
        return
    try:
        data_type = "NUMERIC" if isinstance(value, (int, float)) else "CATEGORICAL"
        lf.score_current_trace(name=name, value=value, data_type=data_type, comment=comment)
    except Exception:
        pass


def eval_run(version: str, scores: dict) -> None:
    """Publie une exécution d'évaluation comme trace + notes (pont chantier 3 ↔ observabilité).

    Chaque `run_eval` devient une trace portant les 8 signaux en `scores` Langfuse :
    on peut alors suivre l'évolution des notes VERSION APRÈS VERSION dans le même
    outil que la latence et le coût, au lieu de deux histoires séparées
    (`scores.jsonl` d'un côté, traces de l'autre).

    No-op sans Langfuse : la porte qualité de la CI reste totalement indépendante.
    """
    lf = _client()
    if lf is None:
        return
    try:
        from langfuse import propagate_attributes

        with lf.start_as_current_observation(
            name="velmo.eval", as_type="evaluator", input={"version": version}
        ):
            with propagate_attributes(session_id=f"eval-{version}", tags=["eval"],
                                      environment="eval"):
                for nom, valeur in scores.items():
                    if isinstance(valeur, (int, float)):
                        lf.score_current_trace(name=nom, value=float(valeur), data_type="NUMERIC")
                lf.update_current_span(output=scores)
        lf.flush()
    except Exception:
        pass


def flush() -> None:
    """Force l'envoi. L'export est asynchrone : sans flush, un process court n'envoie RIEN."""
    lf = _client()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception:
        pass
