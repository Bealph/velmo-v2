"""Les réglages sortis du code pour la mise en ligne.

Le brief de déploiement demande d'externaliser les seuils des garde-fous, et de ne pas
laisser une configuration s'établir par effet de bord. Ces tests verrouillent les deux
interrupteurs et le seuil, avec leurs valeurs par défaut — car un défaut qui change, c'est
un comportement qui change sans qu'on l'ait décidé.
"""

from __future__ import annotations

from velmo.guardrails.moderator import _moderator_active, get_moderator
from velmo.tools._common import _refund_cap


# --------------------------------------------------------------------------- #
# LLM-juge : l'activation doit être explicite
# --------------------------------------------------------------------------- #
def test_le_juge_reste_inactif_meme_avec_une_cle(monkeypatch):
    """**Le défaut corrigé.** Une clé présente n'active plus la 2e ligne.

    Avant l'interrupteur, `get_moderator()` construisait le juge dès qu'un endpoint et une
    clé existaient. Or ils sont indispensables en production : le juge devenait donc actif
    sans décision, et `version.yaml: moderator: false` n'y changeait rien puisque ce
    fichier ne pilote pas le comportement.
    """
    monkeypatch.setenv("AZURE_AI_INFERENCE_ENDPOINT", "https://exemple.invalid/openai/v1")
    monkeypatch.setenv("AZURE_AI_INFERENCE_API_KEY", "valeur-de-test")
    monkeypatch.delenv("VELMO_MODERATOR", raising=False)

    assert get_moderator() is None


def test_le_juge_reste_inactif_sans_endpoint_ni_cle(monkeypatch):
    """Interrupteur activé mais rien à appeler : on ne construit pas de juge bancal."""
    monkeypatch.setenv("VELMO_MODERATOR", "true")
    monkeypatch.delenv("AZURE_AI_INFERENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_INFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_JUDGE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_JUDGE_API_KEY", raising=False)

    assert get_moderator() is None


def test_valeurs_reconnues_par_l_interrupteur(monkeypatch):
    """Table de vérité de l'interrupteur, défaut inclus.

    Le défaut compte autant que le reste : c'est lui qui s'applique en ligne, la décision
    de déploiement étant de ne PAS renseigner la variable.
    """
    for valeur, attendu in [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("oui", True),
        (" true ", True),
        ("0", False), ("false", False), ("non", False), ("", False), ("peut-etre", False),
    ]:
        monkeypatch.setenv("VELMO_MODERATOR", valeur)
        assert _moderator_active() is attendu, f"valeur {valeur!r}"

    monkeypatch.delenv("VELMO_MODERATOR", raising=False)
    assert _moderator_active() is False, "absent doit valoir désactivé"


# --------------------------------------------------------------------------- #
# Seuil de remboursement : externalisé, mais sans changer le comportement connu
# --------------------------------------------------------------------------- #
def test_le_seuil_par_defaut_reste_cinquante(monkeypatch):
    """Sans variable, la valeur historique s'applique : rien ne change en local ni en CI."""
    monkeypatch.delenv("VELMO_REFUND_CAP", raising=False)

    assert _refund_cap() == 50.0


def test_le_seuil_est_lu_depuis_l_environnement(monkeypatch):
    """Le brief range les seuils de garde-fous parmi les valeurs à externaliser."""
    monkeypatch.setenv("VELMO_REFUND_CAP", "80")
    assert _refund_cap() == 80.0

    # Virgule décimale : un opérateur français saisira « 79,90 » sans y penser.
    monkeypatch.setenv("VELMO_REFUND_CAP", "79,90")
    assert _refund_cap() == 79.90


def test_un_seuil_illisible_ne_fait_pas_tomber_l_agent(monkeypatch):
    """Une saisie fautive laisse le comportement connu, elle ne casse pas le démarrage.

    Un paramètre d'application mal saisi ne doit pas rendre l'agent indisponible : il doit
    le laisser sur son seuil historique. L'inverse — lever à l'import — transformerait une
    faute de frappe dans le portail en panne totale.
    """
    for fautif in ["cinquante", "", "  ", "50 euros", "1e", "None"]:
        monkeypatch.setenv("VELMO_REFUND_CAP", fautif)
        assert _refund_cap() == 50.0, f"valeur {fautif!r}"
