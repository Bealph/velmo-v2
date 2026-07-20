"""Premier contact opérationnel avec Langfuse : une conversation Velmo tracée."""
from dotenv import load_dotenv
load_dotenv(".env")

from langfuse import get_client
from velmo.agent import build_default_agent

lf = get_client()
print("connexion Langfuse :", "OK" if lf.auth_check() else "ECHEC")

agent = build_default_agent()
user_id, question = "C-marc-dubois", "Comment retourner un maillot qui ne me va pas ?"

with lf.start_as_current_observation(
    name="velmo.respond", as_type="agent",
    input={"user_id": user_id, "message": question},
):
    answer = agent.respond(user_id, question)
    t = agent.last_trace

    # Un span par étape : c'est ce découpage qui rend la trace lisible
    with lf.start_as_current_observation(
        name="garde-fous", as_type="guardrail",
        input=question, output={"entree": t["input"], "sortie": t["output"]},
    ):
        pass

    with lf.start_as_current_observation(
        name="recherche-faq", as_type="retriever",
        input=question, output=t["kb_sources"],
    ):
        pass

    lf.update_current_span(output=answer, metadata={"route": t["route"]})
    lf.score_current_trace(name="route", value=t["route"], data_type="CATEGORICAL")
    print("\nTRACE :", lf.get_trace_url())

lf.flush()   # indispensable : l'envoi est asynchrone et bufferisé
print("réponse :", answer[:120])
