"""Où la mémoire est stockée : fichier local ou base externe, et qui l'emporte.

La mise en ligne exige que la mémoire long terme survive au changement de machine (R2),
donc qu'elle sorte du système de fichiers. `memory_session_factory` accepte pour cela une
URL de connexion. Ces tests verrouillent la règle de résolution — et surtout l'invariant
de sécurité qui la rend sans danger.

Les URL utilisées ici sont des URL SQLite (`sqlite:///…`) : le mécanisme testé est celui
de la résolution, identique quel que soit le moteur derrière. Aucun test n'a besoin d'une
base distante, et la suite reste donc hors-ligne et déterministe.
"""

from __future__ import annotations

import os

from velmo.memory.models import FaitSemantique, memory_session_factory


def _url_du_moteur(Session) -> str:
    """URL réellement branchée sur le sessionmaker."""
    return str(Session.kw["bind"].url)


def test_url_d_environnement_utilisee(tmp_path, monkeypatch):
    """Une URL dans l'environnement fait sortir la mémoire du mode fichier local."""
    cible = tmp_path / "en_ligne.sqlite"
    monkeypatch.setenv("VELMO_MEMORY_DB_URL", f"sqlite:///{cible}")

    Session = memory_session_factory()

    assert str(cible) in _url_du_moteur(Session)


def test_url_explicite_prime_sur_l_environnement(tmp_path, monkeypatch):
    """Une URL passée par l'appelant l'emporte sur celle de l'environnement."""
    monkeypatch.setenv("VELMO_MEMORY_DB_URL", f"sqlite:///{tmp_path / 'env.sqlite'}")
    demandee = tmp_path / "demandee.sqlite"

    Session = memory_session_factory(url=f"sqlite:///{demandee}")

    assert str(demandee) in _url_du_moteur(Session)


def test_un_chemin_explicite_prime_sur_une_url_d_environnement(tmp_path, monkeypatch):
    """**Invariant de sécurité.** Un chemin explicite ignore toute URL d'environnement.

    C'est ce qui protège l'évaluation MLOps : elle donne à chaque cas un fichier jetable
    (`mlops.fresh_memory`, `db_path=tempfile.mktemp(...)`) pour les isoler les uns des
    autres. Si l'URL d'environnement l'emportait, chaque cas d'évaluation écrirait dans
    la base de production — et l'isolation tomberait sans qu'aucune erreur ne le signale.
    """
    poison = tmp_path / "production.sqlite"
    monkeypatch.setenv("VELMO_MEMORY_DB_URL", f"sqlite:///{poison}")
    jetable = tmp_path / "jetable.sqlite"

    Session = memory_session_factory(jetable)

    assert str(jetable) in _url_du_moteur(Session)
    assert "production" not in _url_du_moteur(Session)
    # La base « de production » n'a même pas été touchée : aucun fichier créé.
    assert not poison.exists()


def test_mode_fichier_inchange_sans_url(tmp_path, monkeypatch):
    """Sans URL, le comportement historique est conservé : chemin d'environnement."""
    monkeypatch.delenv("VELMO_MEMORY_DB_URL", raising=False)
    fichier = tmp_path / "local.sqlite"
    monkeypatch.setenv("VELMO_MEMORY_DB", str(fichier))

    Session = memory_session_factory()

    url = _url_du_moteur(Session)
    assert url.startswith("sqlite:")
    assert str(fichier) in url


def test_le_schema_est_cree_sur_la_cible_de_l_url(tmp_path, monkeypatch):
    """Un fait écrit via une URL est relisible : les tables ont bien été créées.

    Vérifie que `create_all` s'applique aussi au mode URL — sans quoi la première écriture
    en ligne échouerait sur une table absente.
    """
    monkeypatch.setenv("VELMO_MEMORY_DB_URL", f"sqlite:///{tmp_path / 'cible.sqlite'}")
    Session = memory_session_factory()

    with Session() as session:
        session.add(
            FaitSemantique(
                user_id="C-test-url",
                type="pointure",
                value="taille L",
                source="client",
                confidence="eleve",
            )
        )
        session.commit()

    with Session() as session:
        faits = session.query(FaitSemantique).filter_by(user_id="C-test-url").all()

    assert [f.value for f in faits] == ["taille L"]


def test_la_suite_n_ecrit_pas_dans_la_memoire_de_developpement():
    """Garde-fou sur la fixture d'isolement : la suite ne touche pas `data/`.

    Sans cette vérification, une régression de la fixture repasserait silencieusement les
    tests sur le fichier de développement — et polluerait la mémoire locale réelle.
    """
    chemin = os.environ.get("VELMO_MEMORY_DB", "")
    assert chemin, "la fixture d'isolement doit poser VELMO_MEMORY_DB"
    assert "data" not in os.path.normpath(chemin).split(os.sep)[-2:]
    assert "VELMO_MEMORY_DB_URL" not in os.environ
