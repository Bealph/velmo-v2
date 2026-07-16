"""Mémoire de l'agent Velmo : contexte court terme et mémoire long terme.

Surface publique stable consommée par l'agent et la suite d'acceptance.
L'implémentation interne (court terme, long terme, orchestration) est à construire.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func

from .models import FaitSemantique, Message, memory_session_factory

Turn = tuple[str, str]  # (role, content)


@dataclass
class MemoryContext:
    """Contexte mémoire restitué pour une requête utilisateur."""

    history: list[Turn] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    episodic: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Sérialise le contexte en texte (injectable dans un prompt)."""
        parts: list[str] = []
        for role, content in self.history:
            parts.append(f"{role}: {content}")
        for key, value in self.facts.items():
            parts.append(f"fact:{key}={value}")
        parts.extend(self.episodic)
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Politique D18 : quels types n'ont qu'UNE valeur courante (→ REMPLACE) ?
# Tout type absent de ce set est considéré MULTI-valeur (→ INSERT nouvelle ligne).
# On énumère le mono (fermé, connu) ; défaut multi → on préfère accumuler qu'écraser.
# --------------------------------------------------------------------------- #
MONO_VALUE: set[str] = {
    "adresse", "code_postal", "pointure", "segment", "pref_tutoiement",
    "statut_compte", "langue", "canal_contact", "contrat", "litige_authenticite",
}


# --------------------------------------------------------------------------- #
# Table de règles d'extraction (CONSERVATRICE : au moindre doute, on n'écrit rien).
# Chaque règle : (type, motif compilé, extracteur de valeur -> str, verbatim strip()).
# --------------------------------------------------------------------------- #
def _grp(i: int):
    return lambda m: m.group(i).strip()


def _const(v: str):
    return lambda m: v


_RULES: list[tuple[str, re.Pattern[str], object]] = [
    ("commande", re.compile(r"\bO-\d{4}-\d{4}\b"), _grp(0)),                                   # multi
    ("adresse", re.compile(r"adresse(?:\s+de\s+livraison)?\s+est\s+([^.]+)", re.I), _grp(1)),  # mono
    ("code_postal", re.compile(r"code\s+postal\D{0,15}(\d{5})", re.I), _grp(1)),               # cue requis
    ("pointure", re.compile(r"\bpointure\s+(\d{2})\b", re.I), _grp(1)),                        # cue requis
    ("pref_tutoiement", re.compile(r"\btuto(?:i|y)\w*", re.I), _const("tutoie")),
    ("segment", re.compile(r"\bclient\s+pro\b", re.I), _const("pro")),
]

_MULTI_MATCH_TYPES = {"commande", "produit", "clubs"}  # on capture toutes les occurrences


class MemoryManager:
    """Orchestre la mémoire court terme et long terme, isolée par utilisateur."""

    # Fenêtre brute d'historique. 1 tour = 2 messages (user + assistant) → 60 = 30 tours :
    # on couvre R1 (« au moins 30 tours ») SANS dépendre d'un résumé pas encore construit.
    # Rappel de conception : aucune fenêtre fixe ne suffit seule pour une conversation
    # arbitrairement longue — l'info CRITIQUE survit parce qu'elle est extraite en FAIT
    # (réinjecté à chaque tour), pas parce qu'on garde les messages. Le résumé glissant
    # (Étape 5) est le vrai complément R4 à construire : il portera le milieu compressé et
    # permettra alors de réduire cette fenêtre sous le budget de tokens.
    RECENT_MESSAGES = 60  # = 30 tours

    def __init__(self, *, token_budget: int = 2000, db_path=None) -> None:
        self.token_budget = token_budget
        # Fabrique appelée ICI (pas au niveau module) : la fixture autouse qui pose
        # VELMO_MEMORY_DB doit être prise en compte à temps.
        # `db_path` explicite (défaut None → résolution habituelle env/chemin par défaut) :
        # l'évaluation MLOps donne à CHAQUE cas un store jetable et isolé, sans toucher
        # ni l'environnement global ni la mémoire de production (D-isolation #6).
        self._Session = memory_session_factory(db_path)
        self.session_id = str(uuid.uuid4())  # une session de conversation par instance

    # ------------------------------------------------------------------ #
    # Écriture : cœur D18, partagé par remember_fact et l'extraction de write
    # ------------------------------------------------------------------ #
    def _upsert_fact(self, session, user_id: str, type_: str, value: str,
                     source: str, confidence: str) -> None:
        value = value.strip()
        existing = (
            session.query(FaitSemantique)
            .filter_by(user_id=user_id, type=type_)
            .order_by(FaitSemantique.updated_at.desc())
            .all()
        )

        # NON : aucun fait (user, type) → INSERT
        if not existing:
            session.add(FaitSemantique(
                user_id=user_id, type=type_, value=value,
                source=source, confidence=confidence,
            ))
            return

        # OUI, même valeur → anti-doublon : on touche juste updated_at
        same = next((r for r in existing if r.value == value), None)
        if same is not None:
            same.updated_at = datetime.utcnow()
            return

        # OUI, valeur différente (contradiction)
        if type_ in MONO_VALUE:
            # REMPLACER la ligne la plus récente, sans laisser une déduction agent
            # écraser une déclaration client (client > agent).
            row = existing[0]
            if source == "client" or row.source != "client":
                row.value = value
                row.source = source
                row.confidence = confidence  # updated_at bouge seul (onupdate) car value change
        else:
            # MULTI-valeur → nouvelle ligne
            session.add(FaitSemantique(
                user_id=user_id, type=type_, value=value,
                source=source, confidence=confidence,
            ))

    def _extract_facts(self, text: str) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for type_, pattern, extract in _RULES:
            if type_ in _MULTI_MATCH_TYPES:
                for m in pattern.finditer(text):
                    found.append((type_, extract(m)))
            else:
                m = pattern.search(text)
                if m:
                    found.append((type_, extract(m)))
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for pair in found:
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
        return out

    def _facts_dict(self, session, user_id: str) -> dict[str, str]:
        """Faits d'un user en dict scalaire {type: value}. Multi-valeur → valeurs jointes."""
        rows = (
            session.query(FaitSemantique)
            .filter_by(user_id=user_id)                       # R3 : isolation
            .order_by(FaitSemantique.type, FaitSemantique.updated_at)
            .all()
        )
        by_type: dict[str, list[str]] = {}
        for r in rows:
            by_type.setdefault(r.type, []).append(r.value)
        return {t: (vals[0] if len(vals) == 1 else ", ".join(vals)) for t, vals in by_type.items()}

    # ------------------------------------------------------------------ #
    # Surface publique
    # ------------------------------------------------------------------ #
    def read(self, user_id: str, message: str) -> MemoryContext:
        """Reconstitue le contexte mémoire pertinent pour `message`."""
        # `message` réservé à la future recherche épisodique (vectoriel, non testé ici).
        with self._Session() as s:
            facts = self._facts_dict(s, user_id)
            msgs = (
                s.query(Message)
                .filter_by(user_id=user_id, session_id=self.session_id)
                .order_by(Message.turn_index.desc())
                .limit(self.RECENT_MESSAGES)
                .all()
            )
            history = [(m.role, m.content) for m in reversed(msgs)]
        return MemoryContext(history=history, facts=facts, episodic=[])

    def write(self, user_id: str, user_message: str, assistant_message: str) -> None:
        """Met à jour la mémoire à partir d'un échange."""
        with self._Session() as s:
            last = (
                s.query(func.max(Message.turn_index))
                .filter_by(user_id=user_id, session_id=self.session_id)
                .scalar()
            )
            start = 0 if last is None else last + 1
            s.add(Message(user_id=user_id, session_id=self.session_id,
                          turn_index=start, role="user", content=user_message))
            s.add(Message(user_id=user_id, session_id=self.session_id,
                          turn_index=start + 1, role="assistant", content=assistant_message))

            for type_, value in self._extract_facts(user_message):
                self._upsert_fact(s, user_id, type_, value, source="client", confidence="eleve")

            s.commit()

    def remember_fact(self, user_id: str, key: str, value: str) -> None:
        """Persiste un fait durable sur l'utilisateur."""
        with self._Session() as s:
            self._upsert_fact(s, user_id, key, value, source="client", confidence="eleve")
            s.commit()

    def forget(self, user_id: str, target: str) -> int:
        """Supprime les souvenirs correspondant à `target`. Renvoie le nombre supprimé.

        R5 — on supprime une INFORMATION, pas une ligne : la valeur est aussi masquée
        dans le texte brut des messages, sinon elle ressortirait via l'historique.
        """
        with self._Session() as s:
            # 1) valeurs du fait AVANT suppression
            rows = s.query(FaitSemantique).filter_by(user_id=user_id, type=target).all()
            values = [r.value for r in rows if r.value]

            # 2) masquer ces valeurs dans TOUS les messages du user
            if values:
                for m in s.query(Message).filter_by(user_id=user_id).all():
                    new_content = m.content
                    for v in values:
                        new_content = new_content.replace(v, "[supprimé]")
                    if new_content != m.content:
                        m.content = new_content

            # 3) DELETE les faits → compte supprimé
            removed = (
                s.query(FaitSemantique)
                .filter_by(user_id=user_id, type=target)
                .delete(synchronize_session=False)
            )
            s.commit()
        return removed

    def inspect(self, user_id: str) -> dict:
        """Renvoie l'état mémoire d'un utilisateur (faits + souvenirs épisodiques)."""
        with self._Session() as s:
            facts = self._facts_dict(s, user_id)
        # episodic : vectoriel non implémenté (conçu, non testé)
        return {"facts": facts, "episodic": []}


__all__ = ["MemoryManager", "MemoryContext", "MONO_VALUE"]