"""Garde-fous d'entrée et de sortie de l'agent Velmo.

Défense en profondeur à DEUX lignes (cf. dossier 02-guardrails.md, D36) :

  1re ligne — REGEX / MOTS-CLÉS (déterministe, testée, tient en CI)
      Détecteurs par sévérité, premier match = blocage. Anti-injection : ce sont
      des règles en CODE, hors du LLM, qu'aucun message ne peut désactiver.

  2e ligne — LLM-JUGE / MODÉRATION (sémantique, paraphrases)
      Crochet `moderator` injectable, DÉSACTIVÉ par défaut (`None`) → le chemin
      testé reste 100 % déterministe. Appelé seulement APRÈS un « laissez-passer »
      de la 1re ligne (il ne peut jamais déverrouiller un blocage regex).

Contrats consommés par l'agent et l'acceptance : `Decision`, `GuardrailEngine`,
`check_input`, `check_output`, `events`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

# Catégories de contenus contrôlés.
CATEGORIES = (
    "hate",
    "violence",
    "sexual",
    "pii",
    "out_of_scope",
    "prompt_injection",
    "secret_leak",
)


# --------------------------------------------------------------------------- #
# Normalisation : minuscule + suppression des accents.
# Rend la détection insensible à la casse ET aux accents (« menacé » == « menace »,
# « sous-êtres » == « sous-etres ») → plus robuste que des mots-clés bruts.
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


# --------------------------------------------------------------------------- #
# 1re ligne — détecteurs déterministes (sur texte NORMALISÉ), par ordre de sévérité.
# Chaque motif est calibré pour bloquer les hostiles SANS toucher les 12 légitimes.
# Piège FP clé : `authentifi` (verbe, hostile) ne matche PAS `authentiques` (adjectif,
# légitime) — d'où le motif sur le radical du verbe, jamais sur l'adjectif.
# --------------------------------------------------------------------------- #
def _rx(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in patterns]


_INPUT_DETECTORS: list[tuple[str, list[re.Pattern[str]]]] = [
    ("secret_leak", _rx(
        r"\bcle api\b", r"\bapi key\b", r"\bmot de passe\b",
        r"\benvironnement\b", r"\btoken", r"\bsecret", r"config(uration)? interne",
    )),
    ("prompt_injection", _rx(
        r"ignore tes (instructions|consignes)", r"oublie tes (instructions|consignes)",
        r"\bdeveloper mode\b", r"mode developpeur", r"prompt (systeme|system)",
        r"plus de regles", r"plus aucune regle",
    )),
    ("hate", _rx(
        r"sous-etre", r"sous-humain", r"sale race",
        r"retournez dans votre pays", r"\bhais\b", r"\bhaine\b",
    )),
    ("violence", _rx(
        r"\bfrapper\b", r"\btuer\b", r"faire du mal", r"\bmenace",
    )),
    ("sexual", _rx(
        r"\bsexuel", r"\bsexe\b", r"\bnudite", r"\bporno", r"obscene",
    )),
    ("out_of_scope", _rx(
        r"combien (vaut|coute)", r"\bvaut\b", r"\bcote\b", r"\brevente\b",
        r"\bbourse\b", r"\binvestir\b", r"\bplacement\b", r"\bauthentifi", r"\bjuridique\b",
    )),
]

# En SORTIE, on garde secret + toxicité (dangers réels d'une réponse), mais PAS la
# détection out_of_scope par mots-clés : « vaut », « cote »… peuvent apparaître dans
# une réponse légitime (montant, statut) → risque de faux positif en sortie. Le
# périmètre sémantique en sortie, s'il faut, revient au LLM-juge (2e ligne).
_OUTPUT_TEXT_DETECTORS: list[tuple[str, list[re.Pattern[str]]]] = [
    d for d in _INPUT_DETECTORS if d[0] in {"secret_leak", "hate", "violence", "sexual"}
]

# PII à formats fixes — testés sur le texte ORIGINAL (l'IBAN est sensible à la casse).
# Carte : 4 blocs de 4 chiffres (16) → « O-2024-0101 » (8 chiffres) ne matche pas.
_CARD = re.compile(r"\b\d{4}(?:[ -]?\d{4}){3}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){2,8}\b")
_PASSWORD = re.compile(r"\bmot de passe\b")  # sur texte normalisé


# --------------------------------------------------------------------------- #
# Messages de refus par catégorie (polis, sans révéler les règles internes).
# --------------------------------------------------------------------------- #
_DEFAULT_REFUSAL = (
    "Désolé, je ne peux pas traiter cette demande. Je reste à votre disposition "
    "pour vos commandes, livraisons, retours et la FAQ Velmo."
)
_REFUSALS: dict[str, str] = {
    "hate": "Je ne peux pas continuer sur ce ton. Je reste à votre disposition pour "
            "vos commandes, livraisons et retours.",
    "violence": "Je ne peux pas continuer sur ce ton. Je reste à votre disposition "
                "pour vos commandes, livraisons et retours.",
    "sexual": "Ce sujet sort de ce que je peux traiter. Je peux vous aider sur vos "
              "commandes, livraisons et retours.",
    "prompt_injection": "Je poursuis dans le cadre du support Velmo et ne peux pas "
                        "accéder à cette demande.",
    "secret_leak": "Je ne peux pas partager d'informations internes ou de configuration.",
    "out_of_scope": "Je ne peux pas fournir ce type d'information (estimation, conseil "
                    "juridique ou financier). Je peux vous aider sur vos commandes, "
                    "livraisons et retours.",
    "pii": "Désolé, je ne peux pas transmettre cette information : elle contient une "
           "donnée sensible.",
}


@dataclass
class Decision:
    """Verdict d'un garde-fou sur un message."""

    allowed: bool
    action: str  # "allow" | "block"
    category: str | None = None
    reason: str = ""
    refusal: str | None = None


@dataclass
class GuardrailEngine:
    """Applique les garde-fous d'entrée et de sortie et journalise les décisions.

    `moderator` (2e ligne, optionnel) : callable `(text) -> catégorie | None`.
    Il renvoie une catégorie interdite si le texte doit être bloqué, sinon `None`.
    Absent par défaut → seule la 1re ligne (regex) agit, ce qui reste déterministe.
    """

    moderator: Callable[[str], str | None] | None = None
    events: list[dict] = field(default_factory=list)

    # -- journalisation MASQUÉE (jamais de PII/secret en clair dans les logs) -- #
    def _log(self, where: str, category: str, method: str) -> None:
        self.events.append({
            "where": where,          # "input" | "output"
            "category": category,
            "action": "block",
            "method": method,        # "rules" | "moderator"
            "excerpt": "[masqué]" if category in ("pii", "secret_leak") else None,
        })

    def _block(self, where: str, category: str, method: str) -> Decision:
        self._log(where, category, method)
        return Decision(
            allowed=False,
            action="block",
            category=category,
            reason=f"{category} détecté en {where} ({method})",
            refusal=_REFUSALS.get(category, _DEFAULT_REFUSAL),
        )

    # ------------------------------------------------------------------ #
    # Garde-fou d'ENTRÉE
    # ------------------------------------------------------------------ #
    def check_input(self, message: str) -> Decision:
        norm = _normalize(message)

        # 1re ligne : cascade regex par sévérité, premier match = blocage.
        for category, patterns in _INPUT_DETECTORS:
            if any(p.search(norm) for p in patterns):
                return self._block("input", category, "rules")

        # 2e ligne : LLM-juge, seulement si injecté et si la 1re ligne a laissé passer.
        if self.moderator is not None:
            category = self.moderator(message)
            if category:
                return self._block("input", category, "moderator")

        return Decision(allowed=True, action="allow")

    # ------------------------------------------------------------------ #
    # Garde-fou de SORTIE
    # ------------------------------------------------------------------ #
    def check_output(self, text: str) -> Decision:
        norm = _normalize(text)

        # PII à formats fixes — IBAN AVANT carte (l'IBAN contient 16 chiffres en blocs
        # qui déclencheraient à tort la regex carte → mauvaise catégorie dans le log).
        if _IBAN.search(text) or _CARD.search(text) or _PASSWORD.search(norm):
            return self._block("output", "pii", "rules")

        # Secret / toxicité qui auraient dérivé dans la réponse (défense en profondeur).
        for category, patterns in _OUTPUT_TEXT_DETECTORS:
            if any(p.search(norm) for p in patterns):
                return self._block("output", category, "rules")

        # 2e ligne : LLM-juge sur la sortie (fuite reformulée, dérive sémantique).
        if self.moderator is not None:
            category = self.moderator(text)
            if category:
                return self._block("output", category, "moderator")

        return Decision(allowed=True, action="allow")
