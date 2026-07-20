"""Interface d'échange Velmo 2.0 — le chat client, avec ses coulisses.

Lancer (depuis velmo-v2/) :
    uv run python -m streamlit run scripts/chat_app.py

(On passe par `python -m streamlit` pour contourner un streamlit.exe bloqué par
l'antivirus sur cette machine ; `uv run` cible l'env projet.)

Contrairement aux démos `demo_memory_app.py` / `demo_guardrails_app.py` qui isolent
UN chantier, cette interface fait tourner l'AGENT COMPLET : mémoire durable, garde-fous
d'entrée/sortie, outils métier sur Postgres et FAQ en recherche sémantique.

Deux zones :
- GAUCHE  : la conversation telle que la verrait un client.
- DROITE  : les « coulisses » du dernier tour — ce que l'agent a RÉELLEMENT fait
            (route empruntée, verdicts des garde-fous, sources FAQ citées, mémoire,
            latence). Les données viennent de `agent.last_trace` : aucune devinette.

Le sélecteur de client n'est pas décoratif : l'exigence R3 impose que l'identité vienne
de la SESSION AUTHENTIFIÉE, jamais du message. Changer de client ici, c'est changer
d'utilisateur authentifié — et l'on voit aussitôt l'isolation (mémoire et commandes
d'un client restent invisibles aux autres).
"""

from __future__ import annotations

import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import streamlit as st  # noqa: E402

from velmo.agent import build_default_agent  # noqa: E402

st.set_page_config(page_title="Velmo 2.0 — Support", page_icon="👕", layout="wide")


# --------------------------------------------------------------------------- #
# Agent : construit UNE fois et conservé entre les reruns Streamlit.
# Le reconstruire à chaque interaction relancerait la connexion Postgres et le
# chargement du modèle d'embeddings (plusieurs secondes) à chaque message.
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Démarrage de l'agent (base, FAQ, modèle)…")
def get_agent():
    return build_default_agent()


@st.cache_data(show_spinner=False)
def list_customers() -> list[tuple[str, str]]:
    """(id, nom) des clients en base — la liste des identités « authentifiables »."""
    from velmo.db import Customer
    from velmo.tools._common import select

    rows = get_agent().session.execute(select(Customer.id, Customer.name)).all()
    return [(r[0], r[1]) for r in rows]


agent = get_agent()
customers = list_customers()

# =========================================================================== #
# SIDEBAR — identité (= session authentifiée) et contrôles
# =========================================================================== #
with st.sidebar:
    st.header("🎛️ Session")
    labels = {cid: f"{name} ({cid})" for cid, name in customers} or {"C-marc-dubois": "C-marc-dubois"}
    ids = list(labels)
    user_id = st.selectbox(
        "Client authentifié", ids, format_func=lambda c: labels[c],
        help="Simule la session authentifiée. R3 : l'identité ne vient JAMAIS du message.",
    )

    # Changer de client = nouvelle conversation : on ne mélange pas deux fils.
    if st.session_state.get("user_id") != user_id:
        st.session_state.user_id = user_id
        st.session_state.messages = []
        st.session_state.trace = None

    if st.button("🗑️ Nouvelle conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.trace = None
        st.rerun()

    st.divider()
    st.caption(
        f"LLM : `{type(agent.llm).__name__}`  \n"
        f"FAQ : `{type(agent.kb).__name__}`  \n"
        f"Garde-fou 2ᵉ ligne : "
        f"`{'actif' if getattr(agent.guardrails, 'moderator', None) else 'inactif'}`"
    )

st.session_state.setdefault("messages", [])
st.session_state.setdefault("trace", None)

# =========================================================================== #
# CORPS — chat (gauche) + coulisses (droite)
# =========================================================================== #
chat_col, back_col = st.columns([3, 2], gap="large")

with chat_col:
    st.subheader("💬 Support Velmo")
    for role, content in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(content)

with back_col:
    st.subheader("🔍 Coulisses du dernier tour")
    trace = st.session_state.trace
    if trace is None:
        st.info("Envoyez un message : le détail de ce que fait l'agent s'affichera ici.")
    else:
        gi, go = trace["input"], trace["output"]
        c1, c2 = st.columns(2)
        c1.metric("Garde-fou entrée", "✅ passé" if gi == "allow" else "🚫 bloqué")
        c2.metric("Garde-fou sortie", "✅ passé" if go == "allow" else "🚫 bloqué")
        for label, verdict in (("entrée", gi), ("sortie", go)):
            if verdict != "allow":
                st.error(f"Blocage en {label} — catégorie **{verdict.split(':', 1)[1]}**")

        st.caption(f"Route : **{trace['route']}** · latence **{trace['latency_ms']:.0f} ms**")

        with st.expander("📚 Sources FAQ citées", expanded=bool(trace["kb_sources"])):
            if trace["kb_sources"]:
                for src in trace["kb_sources"]:
                    st.markdown(f"- `{src}`")
            else:
                st.caption("Aucune — ce tour n'a pas consulté la FAQ (outil métier ou refus).")

        with st.expander("🧠 Mémoire durable du client", expanded=True):
            facts = trace["facts"]
            if facts:
                st.table({"type": list(facts), "valeur": list(facts.values())})
            else:
                st.caption("Aucun fait mémorisé pour ce client.")
            st.caption(f"Isolée par `user_id` = `{trace['user_id']}` (R3).")

# --------------------------------------------------------------------------- #
# Saisie : un tour = respond() + capture de ce qui s'est passé.
# --------------------------------------------------------------------------- #
if prompt := st.chat_input("Votre question…"):
    st.session_state.messages.append(("user", prompt))

    before = len(agent.guardrails.events)  # delta = blocages survenus pendant ce tour
    start = time.perf_counter()
    answer = agent.respond(user_id, prompt)
    latency_ms = (time.perf_counter() - start) * 1000

    trace = dict(agent.last_trace)
    trace["latency_ms"] = latency_ms
    trace["user_id"] = user_id
    trace["facts"] = agent.memory.inspect(user_id).get("facts", {})
    trace["new_events"] = agent.guardrails.events[before:]

    st.session_state.messages.append(("assistant", answer))
    st.session_state.trace = trace
    st.rerun()
