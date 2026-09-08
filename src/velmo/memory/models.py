"""Brique 1 — Schéma & store de la mémoire (Velmo 2.0).

Traduction en code de l'Étape 3.2 du dossier `01-memoire.md` :
- table relationnelle `faits_semantiques` (mémoire sémantique : faits durables du client) ;
- table relationnelle `messages`         (mémoire court terme : le fil de la conversation).

Cette Base est **indépendante** de la `db.Base` applicative : la mémoire a son propre
cycle de vie et son propre fichier SQLite, isolés du reste de l'application.

Python 3.11 (contrainte projet) : `datetime.utcnow` y est parfaitement valide.
On reste en datetime **naïf UTC** (comme db.py) : sur SQLite, le type DateTime perd
le fuseau au stockage — mélanger aware et naïf ferait échouer la comparaison D18.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import String, Integer, DateTime, Index, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


# --------------------------------------------------------------------------- #
# Base dédiée à la mémoire (volontairement séparée de db.Base)
# --------------------------------------------------------------------------- #
class MemoryBase(DeclarativeBase):
    """Base déclarative propre à la mémoire, sans lien avec db.Base."""
    pass


# --------------------------------------------------------------------------- #
# Mémoire SÉMANTIQUE — faits durables du client
# Cf. 01-memoire.md §3.2.a. Clé métier = (user_id, type) ; `id` est purement technique.
# --------------------------------------------------------------------------- #
class FaitSemantique(MemoryBase):
    __tablename__ = "faits_semantiques"

    # Index composite NON unique : accélère le lookup (user_id, type) et, par préfixe
    # gauche, l'inspection R6 « tous les faits d'un user ». Surtout PAS unique : les types
    # multi-valeur (commande, produit) ont plusieurs lignes pour un même (user_id, type).
    # L'upsert mono-valeur (D18) est une règle applicative, pas une contrainte DB.
    __table_args__ = (
        Index("ix_faits_user_type", "user_id", "type"),
    )

    # PK technique auto-incrémentée : personne ne référence ce fait par son id
    # (l'upsert D18, l'oubli R5 et l'isolation R3 passent par (user_id, type)).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[str] = mapped_column(String, nullable=False)   # R3 · isolation
    type: Mapped[str] = mapped_column(String, nullable=False)      # R5 · axe d'oubli
    value: Mapped[str] = mapped_column(String, nullable=False)     # R2/R6 · valeur retenue

    # Cycle de vie du fait (D4/D7) : "actif" par défaut, "ouvert"/"resolu" pour un litige.
    statut: Mapped[str] = mapped_column(String, nullable=False, default="actif")

    source: Mapped[str] = mapped_column(String, nullable=False)      # R6 · "client" (fort) > "agent"
    confidence: Mapped[str] = mapped_column(String, nullable=False)  # "eleve" (règle) / "moyen" (LLM)

    # Temps réel (naïf UTC) : D18 « garder la plus récente » s'appuie sur updated_at,
    # qui bouge tout seul à chaque UPDATE grâce à onupdate.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)                          # R6
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)  # R6/D18

    def __repr__(self) -> str:  # pragma: no cover - confort de debug/inspection (R6)
        return (
            f"FaitSemantique(id={self.id!r}, user_id={self.user_id!r}, "
            f"type={self.type!r}, value={self.value!r}, statut={self.statut!r}, "
            f"source={self.source!r}, confidence={self.confidence!r})"
        )


# --------------------------------------------------------------------------- #
# Mémoire COURT TERME — le fil de la conversation (source de vérité relationnelle)
# Cf. 01-memoire.md §3.2.b.
# --------------------------------------------------------------------------- #
class Message(MemoryBase):
    __tablename__ = "messages"

    # On recharge les derniers tours d'une session filtrés par (user_id, session_id),
    # ordonnés par turn_index → index composite non unique dédié.
    __table_args__ = (
        Index("ix_messages_user_session", "user_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[str] = mapped_column(String, nullable=False)     # R3 · propriétaire
    session_id: Mapped[str] = mapped_column(String, nullable=False)  # R2 · frontière de session
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False) # R1 · ordre des tours

    role: Mapped[str] = mapped_column(String, nullable=False)     # "user" / "assistant"
    content: Mapped[str] = mapped_column(String, nullable=False)  # texte du message

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Message(id={self.id!r}, user_id={self.user_id!r}, "
            f"session_id={self.session_id!r}, turn_index={self.turn_index!r}, role={self.role!r})"
        )


# --------------------------------------------------------------------------- #
# Le store persistant — fabrique de sessions SQLite
# --------------------------------------------------------------------------- #
def _default_db_path() -> Path:
    """Chemin par défaut du fichier de mémoire.

    Résolution en cascade :
      1. variable d'env VELMO_MEMORY_DB  → laisse la CI/les tests pointer un fichier jetable ;
      2. sinon, <racine_du_repo>/data/velmo_memory.sqlite → chemin DÉTERMINISTE, donc
         partagé entre deux MemoryManager() lancés depuis des cwd différents.

    La racine est ancrée sur __file__ (src/velmo/memory/models.py → parents[3]) et non
    sur cwd(), pour garantir ce partage quel que soit le dossier de lancement.
    """
    env = os.getenv("VELMO_MEMORY_DB")
    if env:
        return Path(env)
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "data" / "velmo_memory.sqlite"


def _memory_db_url() -> str | None:
    """URL de connexion à un stockage mémoire EXTERNE au système de fichiers.

    Introduite pour la mise en ligne. La mémoire long terme doit survivre au changement
    de machine (exigence R2), ce qu'un fichier local ne permet pas — c'est précisément le
    « fichier perdu au changement de poste » que le brief de déploiement demande de
    remplacer.

    Un fichier ne conviendrait de toute façon pas en ligne : sur App Service, le système
    de fichiers de l'application est un partage réseau, sur lequel il est impossible
    d'acquérir un verrou exclusif. La documentation Microsoft écarte explicitement les
    bases de données fichier pour cette raison, et recommande une base managée.
    """
    return os.getenv("VELMO_MEMORY_DB_URL") or None


def memory_session_factory(
    path: str | os.PathLike[str] | None = None,
    *,
    url: str | None = None,
) -> sessionmaker:
    """Prépare le stockage mémoire (fichier ou base externe) et renvoie un sessionmaker.

    Résolution, du plus explicite au plus implicite :

      1. `path` — un chemin passé par l'appelant l'emporte sur TOUT, y compris sur une
         URL présente dans l'environnement. **C'est un invariant de sécurité, pas une
         commodité** : l'évaluation MLOps donne à chaque cas un fichier jetable
         (`mlops/__init__.py`, `db_path=tempfile.mktemp(...)`) pour les isoler. Si une
         URL d'environnement primait, chaque cas d'évaluation écrirait dans la base de
         PRODUCTION — et l'isolation D-isolation #6 tomberait en silence.
      2. `url` — URL passée par l'appelant.
      3. env `VELMO_MEMORY_DB_URL` — le mode en ligne (base managée).
      4. env `VELMO_MEMORY_DB`, sinon `data/velmo_memory.sqlite` — le mode fichier,
         inchangé, qui reste celui du développement local et des tests.

    Args:
        path: chemin explicite d'un fichier SQLite. Prioritaire (voir 1).
        url:  URL de connexion SQLAlchemy explicite.

    Returns:
        Un sessionmaker lié au moteur. expire_on_commit=False : on garde les attributs
        lisibles après commit / hors session (le memory manager relit un fait juste
        après l'avoir écrit) → pas de DetachedInstanceError.
    """
    if path is not None:
        resolved_url = None  # un chemin explicite l'emporte : voir le point 1 ci-dessus
    else:
        resolved_url = url or _memory_db_url()

    if resolved_url:
        # pool_pre_ping : une base managée ferme les connexions restées inactives. Sans
        # cette vérification, le premier message reçu après une accalmie échouerait sur
        # une connexion morte — un défaut qui n'apparaît jamais en développement.
        engine = create_engine(resolved_url, pool_pre_ping=True)
    else:
        db_path = Path(path) if path is not None else _default_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)  # crée data/ au besoin
        engine = create_engine(f"sqlite:///{db_path}")

    # Les tables de la mémoire sont créées ici, et non par Alembic : Alembic gère la base
    # MÉTIER (`DB_URL`), qui est une base distincte. Le schéma mémoire reste donc porté
    # par le modèle, ce qui vaut aussi bien pour un fichier que pour une base managée.
    MemoryBase.metadata.create_all(engine)

    return sessionmaker(bind=engine, expire_on_commit=False)