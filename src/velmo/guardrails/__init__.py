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

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

# Journal des blocages, en plus de la liste `events` en mémoire.
#
# `events` sert à l'interface et aux tests, mais elle disparaît au redémarrage et
# n'apparaît nulle part côté hébergeur. Or un blocage doit être CONSTATABLE en
# exploitation, pas seulement dans le processus qui l'a produit.
#
# Niveau WARNING, et ce n'est pas un choix esthétique : sans aucune configuration de
# journalisation, Python n'émet que les messages de niveau WARNING et au-delà, via son
# gestionnaire de dernier recours (vers `stderr`). Un `INFO` serait donc silencieux sur un
# hébergeur qui ne configure rien. Une bibliothèque n'ayant pas à imposer une
# configuration globale au programme qui l'utilise, on s'aligne sur le niveau qui sort
# tout seul — et un blocage est de toute façon un événement anormal, donc à sa place ici.
_LOGGER = logging.getLogger("velmo.guardrails")

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

# Le LLM-juge en SORTIE est restreint aux MÊMES catégories que la 1re ligne (+ pii) :
# ce sont les seuls dangers reels d'une REPONSE. Sans cette restriction, le juge —
# qui recoit le texte prefixe « MESSAGE : », donc evalue une reponse avec la semantique
# d'une requete client — classait en `prompt_injection` une reponse parfaitement
# legitime qui mentionnait ses propres extraits FAQ (« je n'ai pas l'information dans
# les extraits fournis (authenticite.md...) »), et refusait le client.
# `prompt_injection` et `out_of_scope` n'ont aucun sens en sortie : l'agent ne peut pas
# s'auto-injecter, et un mot hors-perimetre peut legitimement figurer dans une reponse.
_OUTPUT_MODERATOR_CATEGORIES = {"secret_leak", "hate", "violence", "sexual", "pii"}

# PII à formats fixes — testés sur le texte ORIGINAL (l'IBAN est sensible à la casse).
# Carte : 4 blocs de 4 chiffres (16) → « O-2024-0101 » (8 chiffres) ne matche pas.
_CARD = re.compile(r"\b\d{4}(?:[ -]?\d{4}){3}\b")
# IBAN : 2 lettres pays + 2 chiffres de controle + 11 a 30 alphanumeriques (espaces
# de groupage tolerés) => longueur totale 15 a 34, la plage REELLE d'un IBAN.
# Le motif precedent ({2,4} repete 2 a 8 fois) acceptait des l 8 caracteres et bloquait
# donc les references produit en majuscules — « OM1993 FINALE », « BR1970 PELE »,
# « CT7788 ABCD » — soit un faux positif sur le vocabulaire meme de la boutique
# (observe en production : une question legitime sur une taille refusee au client).
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
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
    def _log(self, where: str, category: str, method: str, user_id: str | None = None) -> None:
        self.events.append({
            "where": where,          # "input" | "output"
            "category": category,
            "action": "block",
            "method": method,        # "rules" | "moderator"
            "user_id": user_id,      # rattache le blocage à une conversation
            "excerpt": "[masqué]" if category in ("pii", "secret_leak") else None,
        })

        # Le message d'origine n'est JAMAIS journalisé : seuls des métadonnées de
        # catégorie. C'est ce qui permet de tracer un blocage `pii` ou `secret_leak` sans
        # recopier dans le journal ce qu'on vient précisément de refuser de laisser passer.
        _LOGGER.warning(
            "blocage where=%s category=%s method=%s user=%s",
            where, category, method, user_id or "inconnu",
        )

    def _block(self, where: str, category: str, method: str,
               user_id: str | None = None) -> Decision:
        self._log(where, category, method, user_id)
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
    def check_input(self, message: str, *, user_id: str | None = None,
                    **_: object) -> Decision:
        norm = _normalize(message)

        # 1re ligne : cascade regex par sévérité, premier match = blocage.
        for category, patterns in _INPUT_DETECTORS:
            if any(p.search(norm) for p in patterns):
                return self._block("input", category, "rules", user_id)

        # 2e ligne : LLM-juge, seulement si injecté et si la 1re ligne a laissé passer.
        if self.moderator is not None:
            category = self.moderator(message)
            if category:
                return self._block("input", category, "moderator", user_id)

        return Decision(allowed=True, action="allow")

    # ------------------------------------------------------------------ #
    # Garde-fou de SORTIE
    # ------------------------------------------------------------------ #
    def check_output(self, text: str, *, llm_generated: bool = True,
                     user_id: str | None = None, **_: object) -> Decision:
        """Contrôle une réponse avant envoi au client.

        `llm_generated=False` saute la 2e ligne (LLM-juge) : quand la réponse vient d'un
        OUTIL métier ou d'un extrait de FAQ, c'est un texte que NOUS avons écrit, pas une
        production du modèle — le faire juger par un LLM coûtait ~1 s et un appel par tour
        pour un risque nul (observé en trace : la fiche `frais-de-port.md` envoyée au juge).
        La 1re ligne regex, elle, s'applique TOUJOURS : elle est gratuite et reste utile si
        un outil venait à renvoyer une donnée sensible.
        """
        norm = _normalize(text)

        # PII à formats fixes — IBAN AVANT carte (l'IBAN contient 16 chiffres en blocs
        # qui déclencheraient à tort la regex carte → mauvaise catégorie dans le log).
        if _IBAN.search(text) or _CARD.search(text) or _PASSWORD.search(norm):
            return self._block("output", "pii", "rules", user_id)

        # Secret / toxicité qui auraient dérivé dans la réponse (défense en profondeur).
        for category, patterns in _OUTPUT_TEXT_DETECTORS:
            if any(p.search(norm) for p in patterns):
                return self._block("output", category, "rules", user_id)

        # 2e ligne : LLM-juge sur la sortie (fuite reformulée, dérive sémantique),
        # RESTREINT aux catégories qui ont un sens pour une réponse (cf. ci-dessus)
        # et au contenu réellement GÉNÉRÉ par le modèle.
        if llm_generated and self.moderator is not None:
            category = self.moderator(text)
            if category in _OUTPUT_MODERATOR_CATEGORIES:
                return self._block("output", category, "moderator", user_id)

        return Decision(allowed=True, action="allow")
