"""Velmo 2.0 — agent de support reconstruit (mémoire, garde-fous, MLOps)."""

__version__ = "2.0.0"

# --------------------------------------------------------------------------- #
# Confiance au magasin de certificats de l'OS, posée UNE SEULE FOIS à l'import.
#
# Derrière un antivirus / proxy qui inspecte le TLS, le CA racine est présent dans le
# magasin Windows mais absent du bundle `certifi` → CERTIFICATE_VERIFY_FAILED sur TOUT
# appel HTTPS : Azure (LLM), huggingface.co (modèle d'embeddings), cloud.langfuse.com.
#
# L'injection doit précéder la création de n'importe quel client HTTPS. La répartir
# dans chaque point d'entrée l'a fait oublier trois fois de suite (seed_kb, get_kb,
# client Langfuse) : on la centralise donc ici, où tout passe forcément.
#
# No-op sûr si `truststore` n'est pas installé (extra `llm` absent), et sans effet
# hors inspection TLS.
# --------------------------------------------------------------------------- #
from .llm import enable_os_truststore as _enable_os_truststore

_enable_os_truststore()

# Observabilité : on construit le client Langfuse MASQUÉ ici, à l'import du paquet, donc
# AVANT tout autre usage du SDK (notamment le wrapper langfuse.openai de get_llm). Le
# premier client construit fixe le masque de la ressource partagée ; un client non masqué
# créé avant rendrait le masquage PII (D24) inopérant. No-op sans LANGFUSE_PUBLIC_KEY.
from . import observability as _observability

_observability.init()
