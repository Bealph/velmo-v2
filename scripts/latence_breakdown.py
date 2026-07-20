"""Deux tours consecutifs : separe le demarrage a froid du regime etabli,
et decoupe l'etape 3 (recherche FAQ vs appel LLM)."""
import time
from dotenv import load_dotenv
load_dotenv(".env")
from velmo.agent import build_default_agent, SYSTEM_PROMPT
from velmo import tools

agent = build_default_agent()
uid, q = "C-marc-dubois", "Comment retourner un maillot qui ne me va pas ?"

def chrono(label, fn):
    t0 = time.perf_counter(); r = fn()
    print(f"  {label:26} {(time.perf_counter()-t0)*1000:8.0f} ms"); return r

for tour in (1, 2):
    print(f"\n--- TOUR {tour} ---")
    chrono("garde-fou ENTREE", lambda: agent.guardrails.check_input(q))
    hits = chrono("recherche FAQ (RAG)", lambda: tools.search_kb(agent.kb, q))
    ctx = agent.memory.read(uid, q)
    parts = [ctx.render()] + [f"[FAQ source={h['source']}] {h['snippet']}" for h in hits.get("results", [])[:3]]
    ans = chrono("appel LLM principal", lambda: agent.llm.invoke(SYSTEM_PROMPT, "\n\n".join(parts), q))
    chrono("garde-fou SORTIE", lambda: agent.guardrails.check_output(ans))
