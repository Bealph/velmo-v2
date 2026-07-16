"""Démo Streamlit — mémoire COURT TERME vs LONG TERME (Velmo 2.0).

Lancer (depuis velmo-v2/) :
    uv run python -m streamlit run scripts/demo_memory_app.py

(On passe par `python -m streamlit` pour contourner un streamlit.exe bloqué par
l'antivirus sur cette machine ; et `uv run` cible l'env projet où streamlit est installé.)

Ce que ça montre en direct :
- COURT TERME (zone principale) : le fil de la session courante. Se VIDE à « Nouvelle session ».
- LONG TERME (sidebar) : les faits durables du client. PERSISTENT entre sessions (R2),
  isolés par client (R3). Oubli par type (R5) + inspection (R6).

Le geste clé de la démo : envoyer des faits → cliquer « Nouvelle session » → le fil
disparaît (court terme) MAIS les faits restent (long terme). C'est la distinction, en un clic.
"""

from __future__ import annotations

import os
from pathlib import Path

# Base persistante et STABLE (pour démontrer la persistance R2, même après redémarrage
# de l'appli). setdefault : on respecte VELMO_MEMORY_DB s'il est déjà posé.
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_streamlit.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("VELMO_MEMORY_DB", str(DB_PATH))

import streamlit as st  # noqa: E402

from velmo.memory import MemoryManager  # noqa: E402

st.set_page_config(page_title="Velmo — Démo mémoire", page_icon="🧠", layout="wide")

# --- État Streamlit : on garde le MemoryManager (donc le session_id) stable entre
# deux reruns. « Nouvelle session » en recrée un → nouveau session_id → court terme vidé.
if "mm" not in st.session_state:
    st.session_state.mm = MemoryManager()
mm: MemoryManager = st.session_state.mm

# =========================================================================== #
# SIDEBAR — contrôles + mémoire LONG TERME
# =========================================================================== #
with st.sidebar:
    st.header("🎛️ Contrôles")
    user_id = st.text_input("Client (user_id)", value="C-demo-marc",
                            help="Change de client pour démontrer l'isolation R3")

    c1, c2 = st.columns(2)
    if c1.button("🔄 Nouvelle session", use_container_width=True,
                 help="Nouveau session_id : le COURT TERME se vide, le LONG TERME persiste"):
        st.session_state.mm = MemoryManager()
        st.rerun()
    if c2.button("🗑️ Reset base", use_container_width=True,
                 help="Efface TOUTE la mémoire (tous clients confondus)"):
        DB_PATH.unlink(missing_ok=True)
        st.session_state.mm = MemoryManager()
        st.rerun()

    st.caption(f"session courante : `{mm.session_id[:8]}…`")
    st.caption(f"base : `{DB_PATH.name}`")

    st.divider()
    st.header("🧠 Mémoire LONG TERME")
    st.caption(f"Faits durables de **{user_id}** — persistent entre sessions (R2), isolés (R3)")

    facts = mm.inspect(user_id)["facts"]  # {type: value} (R6 : inspection)
    if facts:
        st.table([{"type": t, "valeur": v} for t, v in facts.items()])
    else:
        st.info("Aucun fait durable pour ce client.")

    if facts:
        st.subheader("🧹 Droit à l'oubli (R5)")
        target = st.selectbox("Oublier le type :", sorted(facts.keys()))
        if st.button(f"Oublier « {target} »", use_container_width=True):
            n = mm.forget(user_id, target)
            st.session_state.flash = (
                f"🧹 {n} fait(s) « {target} » supprimé(s) — et masqué(s) dans l'historique (R5)."
            )
            st.rerun()

    with st.expander("ℹ️ Quelle exigence chaque zone démontre ?"):
        st.markdown(
            "- **Fil de conversation** → court terme (R1)\n"
            "- **Faits qui restent après *Nouvelle session*** → long terme (R2)\n"
            "- **Changer de client → mémoire vide** → isolation (R3)\n"
            "- **Oublier un type** → droit à l'oubli (R5)\n"
            "- **Table des faits** → traçabilité / inspection (R6)"
        )

# =========================================================================== #
# ZONE PRINCIPALE — mémoire COURT TERME (le fil de conversation)
# =========================================================================== #
st.title("🧠 Mémoire court terme vs long terme")
st.caption(
    "Tape un message. Le **court terme** (ce fil) se vide à *Nouvelle session* ; "
    "le **long terme** (sidebar) persiste. Essaie : envoie des faits "
    "(« Ma commande O-2024-0101, je suis client pro, tu peux me tutoyer »), "
    "puis clique *Nouvelle session* → le fil disparaît, les faits restent."
)

# Message flash (après oubli / fait retenu)
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

# Historique COURT TERME de la session courante (filtré user_id + session_id)
ctx = mm.read(user_id, "")
for role, content in ctx.history:
    with st.chat_message("user" if role == "user" else "assistant"):
        st.write(content)

# Saisie d'un message
if msg := st.chat_input("Ton message…"):
    before = set(mm.inspect(user_id)["facts"].items())
    mm.write(user_id, msg, "C'est noté.")  # persiste le tour + extrait les faits durables
    after = set(mm.inspect(user_id)["facts"].items())
    new = dict(after - before)
    if new:
        st.session_state.flash = "🧠 Nouveau fait durable retenu : " + ", ".join(
            f"{k}={v}" for k, v in new.items()
        )
    st.rerun()
