"""Agent Velmo 2.0 : garde-fou d'entrée → mémoire → routage outils → garde-fou
de sortie → écriture mémoire.

Le routage et les outils sont fonctionnels (accès réel à la base). La mémoire,
les garde-fous de contenu et le MLOps sont les chantiers à construire ; ici ils
sont câblés via des composants par défaut (no-op).
"""

from __future__ import annotations

import re

from . import observability as obs
from . import tools
from .guardrails import GuardrailEngine
from .llm import LLM, get_llm
from .memory import MemoryManager

SYSTEM_PROMPT = (
    "Tu es l'assistant de support de Velmo, boutique de maillots de foot collector. "
    "Tu traites la gestion de commandes de niveau 1 avec courtoisie et précision.\n"
    "Règles impératives :\n"
    "- Quand des extraits FAQ te sont fournis, réponds UNIQUEMENT à partir d'eux et cite "
    "leur source (le nom du fichier).\n"
    "- N'invente JAMAIS une procédure, un délai, un montant ou une condition qui n'y "
    "figure pas. Si l'information manque, dis-le clairement et propose de transmettre "
    "la demande à un conseiller.\n"
    "- Utilise les informations mémorisées sur le client quand elles sont pertinentes."
)

DEFAULT_REFUSAL = (
    "Désolé, je ne peux pas traiter cette demande. Je reste à votre disposition "
    "pour vos commandes, livraisons, retours et la FAQ Velmo."
)

# Repli en cas de panne d'une dépendance externe : on reste poli et honnête, sans
# jamais exposer la cause technique au client (ni au journal côté client).
TECHNICAL_FALLBACK = (
    "Je rencontre momentanément un problème technique et ne peux pas traiter votre "
    "demande à l'instant. Pouvez-vous reformuler dans un instant ? Si cela persiste, "
    "je transmets votre demande à un conseiller."
)

ORDER_RE = re.compile(r"O-\d{4}-\d{4}")
SIZE_RE = re.compile(r"\b(XXL|XL|S|M|L)\b")
AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|euros?)")
_CONFIRM = ("je confirme", "confirme", "c'est confirmé", "oui je", "vas-y")

# Alias conviviaux -> référence produit.
_ALIASES = {
    "om 1993": "om-1993",
    "marseille 1993": "om-1993",
    "france 98": "france-1998",
    "france 1998": "france-1998",
    "united 99": "mu-1999-treble",
    "mu 1999": "mu-1999-treble",
    "manchester 1999": "mu-1999-treble",
    "bresil 1970": "brazil-1970",
    "brésil 1970": "brazil-1970",
}

_FAQ_KEYWORDS = (
    "frais de port", "frais de livraison", "délai", "delai", "politique de retour",
    "authenticit", "certificat", "paiement", "réassort", "reassort", "rétractation",
    "retractation", "entretien", "garantie", "remboursement sous", "conditions d'échange",
)


class Agent:
    """Assistant de support adossé aux outils métier et à la FAQ."""

    def __init__(
        self,
        llm: LLM,
        memory: MemoryManager,
        guardrails: GuardrailEngine,
        session=None,
        kb=None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.guardrails = guardrails
        self.session = session
        self.kb = kb
        # Trace du DERNIER tour : ce que l'agent a réellement fait (route empruntée,
        # sources FAQ utilisées, verdicts des garde-fous). Sert à l'observabilité —
        # panneau « coulisses » de l'interface, et base de l'export vers un collecteur
        # de traces. Sans ça, l'extérieur ne peut que deviner (et se tromper).
        self.last_trace: dict = {}
        # Action sensible en attente de confirmation, PAR CLIENT : {user_id: (label,
        # order_id, action)}. Indispensable car l'agent invite à répondre « je confirme »,
        # or ce message ne contient aucun numéro de commande — sans mémoire de l'action
        # en cours, la confirmation ne pouvait aboutir et l'agent promettait un geste
        # qu'il n'exécutait jamais. Cloisonné par user_id : la confirmation d'un client
        # ne peut pas déclencher l'action d'un autre (R3).
        self._pending: dict[str, tuple] = {}

    def respond(self, user_id: str, message: str) -> str:
        """Un tour complet. Le corps est instrumenté : chaque `obs.step` ENVELOPPE le
        travail qu'il mesure (un span posé après coup afficherait 0 ms)."""
        with obs.turn("velmo.respond", user_id=user_id,
                      session_id=getattr(self.memory, "session_id", "-"), input=message):
            answer = self._respond(user_id, message)
            obs.update(output=answer, metadata=self.last_trace)
            obs.score("route", self.last_trace.get("route", "?"))
            return answer

    def _respond(self, user_id: str, message: str) -> str:
        self.last_trace = {"route": "outil", "kb_sources": [], "input": "allow", "output": "allow"}

        with obs.step("garde-fou entrée", "guardrail", input=message):
            gate_in = self.guardrails.check_input(message, user_id=user_id)
            obs.update(output={"action": gate_in.action, "categorie": gate_in.category})
        if not gate_in.allowed:
            self.last_trace["input"] = f"block:{gate_in.category}"
            self.last_trace["route"] = "refus (entrée)"
            refusal = gate_in.refusal or DEFAULT_REFUSAL
            self.memory.write(user_id, message, refusal)
            return refusal

        # Le contexte mémoire est LU **et transmis** au routage : sans ça l'agent
        # disposait d'une mémoire parfaitement alimentée mais n'y accédait jamais pour
        # répondre (amnésie en conversation, révélée par le test de bout en bout).
        with obs.step("lecture mémoire", "retriever", input=message):
            context = self.memory.read(user_id, message)
            obs.update(output={"faits": context.facts, "tours_historique": len(context.history)})

        # FAIL-SAFE : une panne d'une dépendance externe (500 du fournisseur LLM, base
        # momentanément injoignable) ne doit JAMAIS tuer la conversation. Le LLM-juge est
        # déjà en fail-open ; l'appel principal ne l'était pas, et une erreur 500 d'Azure
        # remontait jusqu'en haut = session terminée. On dégrade poliment, on trace la
        # cause, et le client garde un interlocuteur.
        with obs.step("routage", "chain", input=message):
            try:
                answer = self._handle(user_id, message, context)
            except Exception as exc:  # noqa: BLE001 - on refuse de propager quoi que ce soit
                self.last_trace["route"] = "erreur technique"
                self.last_trace["error"] = f"{type(exc).__name__}: {exc}"
                answer = TECHNICAL_FALLBACK
            obs.update(output=answer, metadata={"route": self.last_trace["route"],
                                                "kb_sources": self.last_trace["kb_sources"]})

        # Seule la route « llm + rag » produit du texte écrit par le MODÈLE. Les routes
        # outil/faq/refus renvoient nos propres chaînes : inutile d'y dépêcher le LLM-juge
        # (~1 s par tour observée en trace). La 1re ligne regex, elle, s'applique toujours.
        genere_par_llm = self.last_trace["route"] == "llm + rag"
        with obs.step("garde-fou sortie", "guardrail", input=answer):
            gate_out = self.guardrails.check_output(
                answer, llm_generated=genere_par_llm, user_id=user_id
            )
            obs.update(output={"action": gate_out.action, "categorie": gate_out.category,
                               "juge_consulte": genere_par_llm})
        if not gate_out.allowed:
            self.last_trace["output"] = f"block:{gate_out.category}"
            self.last_trace["route"] = "refus (sortie)"
            answer = gate_out.refusal or DEFAULT_REFUSAL

        with obs.step("écriture mémoire", "span"):
            self.memory.write(user_id, message, answer)
        return answer

    # --- routage déterministe ------------------------------------------------

    def _handle(self, user_id: str, message: str, context=None) -> str:
        low = message.lower()
        order = ORDER_RE.search(message)
        order_id = order.group(0) if order else None
        confirmed = any(c in low for c in _CONFIRM)

        # « je confirme » SEUL : le message ne porte pas de numéro de commande, on
        # exécute donc l'action mise en attente au tour précédent pour CE client.
        if confirmed and order_id is None:
            pending = self._pending.pop(user_id, None)
            if pending is not None:
                label, pending_order, action = pending
                self.last_trace["route"] = "outil (confirmation)"
                return self._run_action(pending_order, action)

        if order_id and "annul" in low:
            return self._confirm_or_act(
                user_id, confirmed, "annuler", order_id,
                lambda: tools.cancel_order(self.session, order_id, user_id),
            )
        if order_id and "adresse" in low:
            return self._confirm_or_act(
                user_id, confirmed, "modifier l'adresse de", order_id,
                lambda: tools.update_shipping_address(
                    self.session, order_id, user_id, {"line1": "(à préciser)"}
                ),
            )
        if order_id and "taille" in low and any(w in low for w in ("chang", "modif", "tromp", "erreur")):
            size = SIZE_RE.search(message)
            new_size = size.group(1) if size else "M"
            return self._confirm_or_act(
                user_id, confirmed, f"changer la taille (vers {new_size}) de", order_id,
                lambda: tools.update_order_item(self.session, order_id, user_id, new_size),
            )
        if order_id and any(w in low for w in ("retour", "échange", "echange", "renvoyer")):
            return self._confirm_or_act(
                user_id, confirmed, "ouvrir un retour pour", order_id,
                lambda: tools.create_return(self.session, order_id, user_id, "Demande client"),
            )
        if order_id and "rembours" in low:
            amount_match = AMOUNT_RE.search(message)
            amount = float(amount_match.group(1).replace(",", ".")) if amount_match else 0.0
            return self._confirm_or_act(
                user_id, confirmed, f"rembourser {amount:.0f}€ sur", order_id,
                lambda: tools.trigger_refund(self.session, order_id, user_id, amount, "Demande client"),
            )

        if order_id and any(w in low for w in ("suivi", "colis", "livr", "transport", "track")):
            return self._format_tracking(tools.track_shipment(self.session, order_id, user_id))
        if order_id:
            return self._format_order(tools.get_order(self.session, order_id, user_id))

        if any(w in low for w in ("dispo", "stock", "reste", "en taille")):
            return self._handle_stock(message, low)

        if any(k in low for k in _FAQ_KEYWORDS):
            found = tools.search_kb(self.kb, message)
            self.last_trace["route"] = "faq (mots-clés)"
            self.last_trace["kb_sources"] = [h["source"] for h in found.get("results", [])[:3]]
            return self._format_kb(found)

        # Repli conversationnel = mémoire + RAG.
        #
        # Le routage par mots-clés ci-dessus ne peut pas couvrir toutes les paraphrases
        # (« comment retourner un maillot » ne matchait aucun mot-clé) : la question
        # partait alors au LLM SANS la base de connaissances, et il INVENTAIT une
        # procédure. On récupère donc systématiquement la FAQ ici et on ancre la réponse
        # dessus — le prompt système interdit d'inventer hors de ces extraits.
        parts: list[str] = []
        if context is not None:
            rendered = context.render()
            if rendered:
                parts.append(rendered)
        self.last_trace["route"] = "llm + rag"
        if self.kb is not None:
            hits = tools.search_kb(self.kb, message)
            if hits.get("found"):
                self.last_trace["kb_sources"] = [h["source"] for h in hits["results"][:3]]
                parts.append("\n".join(
                    f"[FAQ source={h['source']}] {h['snippet']}"
                    for h in hits["results"][:3]
                ))
        return self.llm.invoke(SYSTEM_PROMPT, "\n\n".join(parts), message)

    def _confirm_or_act(self, user_id: str, confirmed: bool, label: str, order_id: str, action) -> str:
        if not confirmed:
            # On MÉMORISE l'action : le « je confirme » du tour suivant n'aura pas de
            # numéro de commande. Une nouvelle demande écrase la précédente, donc on ne
            # peut pas confirmer par erreur une action abandonnée.
            self._pending[user_id] = (label, order_id, action)
            return (
                f"Pour {label} la commande {order_id}, pouvez-vous confirmer ? "
                "Répondez « je confirme »."
            )
        self._pending.pop(user_id, None)  # confirmation explicite : plus rien en attente
        return self._run_action(order_id, action)

    def _run_action(self, order_id: str, action) -> str:
        """Exécute l'action métier et met en mots son résultat (succès / escalade / erreur)."""
        result = action()
        if result.get("error"):
            return f"Je ne trouve pas la commande {order_id} à votre nom."
        if result.get("action") == "escalate":
            return (
                f"Cette demande sur la commande {order_id} dépasse ce que je peux faire seul "
                "(commande déjà partie ou montant trop élevé). Je transmets à un conseiller."
            )
        return f"C'est fait pour la commande {order_id} ({result.get('action')})."

    def _handle_stock(self, message: str, low: str) -> str:
        ref = self._find_ref(low)
        size = SIZE_RE.search(message)
        if not ref or not size:
            return "Pouvez-vous préciser la référence du maillot et la taille souhaitée ?"
        result = tools.check_stock(self.session, ref, size.group(1))
        if result.get("error"):
            return "Je ne connais pas cette référence dans notre catalogue."
        if result["available"]:
            return f"Le maillot {result['title']} en taille {result['size']} est disponible."
        return f"Le maillot {ref} en taille {result['size']} est indisponible (épuisé)."

    def _find_ref(self, low: str) -> str | None:
        for alias, ref in _ALIASES.items():
            if alias in low:
                return ref
        if self.session is not None:
            from .db import Product
            from .tools._common import select

            for (ref,) in self.session.execute(select(Product.ref)).all():
                if ref.lower() in low:
                    return ref
        return None

    @staticmethod
    def _format_order(result: dict) -> str:
        if result.get("error"):
            return "Je ne trouve pas cette commande à votre nom."
        return f"Votre commande {result['order_id']} est au statut « {result['status']} »."

    @staticmethod
    def _format_tracking(result: dict) -> str:
        if result.get("error"):
            return "Je ne trouve pas cette commande à votre nom."
        if not result.get("tracking_number"):
            return f"La commande {result['order_id']} n'est pas encore expédiée."
        return (
            f"Votre colis {result['tracking_number']} ({result['carrier']}) est attendu vers "
            f"{result['estimated_delivery']}."
        )

    @staticmethod
    def _format_kb(result: dict) -> str:
        if not result.get("found"):
            return "Je n'ai pas trouvé cette information dans notre FAQ."
        top = result["results"][0]
        return f"D'après notre FAQ ({top['source']}) : {top['snippet']}"


def build_default_agent(session=None, kb=None) -> Agent:
    """Assemble un agent avec composants par défaut, base et FAQ.

    Les garde-fous reçoivent le LLM-juge (2e ligne) s'il est configuré ; `get_moderator()`
    renvoie `None` en l'absence d'endpoint/clé → 1re ligne regex seule (déterministe).
    """
    from .db import session_factory
    from .guardrails.moderator import get_moderator
    from .kb_store import get_kb
    from .llm import enable_os_truststore

    # AVANT toute construction : le chargement du modele d'embeddings (get_kb) fait des
    # appels HTTPS vers huggingface.co. Derriere un antivirus/proxy qui inspecte le TLS,
    # sans le magasin de certificats de l'OS c'est CERTIFICATE_VERIFY_FAILED — et
    # sentence-transformers fabrique alors un modele de repli au pooling different, donc
    # des embeddings de requete incompatibles avec ceux de l'ingestion (recuperation
    # silencieusement degradee). Le truststore etait injecte dans get_llm(), appele APRES
    # get_kb() : trop tard.
    enable_os_truststore()

    if session is None:
        session = session_factory()()
    if kb is None:
        kb = get_kb()
    return Agent(
        llm=get_llm(),
        memory=MemoryManager(),
        guardrails=GuardrailEngine(moderator=get_moderator()),
        session=session,
        kb=kb,
    )
