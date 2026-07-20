"""Ingestion de la FAQ Velmo (kb/docs/*.md) dans Chroma.

Usage : uv run python scripts/seed_kb.py
Nécessite l'extra `vector` (chromadb + sentence-transformers) et un service Chroma.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

KB_DOCS_DIR = Path(__file__).resolve().parent.parent / "kb" / "docs"


def main() -> None:
    # Même source de vérité que l'agent : l'hôte/port viennent de CHROMA_URL
    # (avant, ce script lisait CHROMA_HOST/CHROMA_PORT — une seconde façon de
    # configurer le même service, donc une seconde façon de se tromper).
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    # Le téléchargement du modèle d'embeddings passe par HTTPS vers huggingface.co.
    # Derrière un antivirus/proxy qui inspecte le TLS, le CA racine est dans le magasin
    # Windows mais absent du bundle certifi -> CERTIFICATE_VERIFY_FAILED. Même remède
    # que pour le client LLM : faire confiance au magasin de l'OS.
    from velmo.llm import enable_os_truststore

    enable_os_truststore()

    from velmo.kb_store import chroma_collection, chroma_endpoint

    host, port, _ = chroma_endpoint()
    print(f"Chroma : {host}:{port}")
    collection = chroma_collection()

    docs, ids, metas = [], [], []
    for path in sorted(KB_DOCS_DIR.glob("*.md")):
        docs.append(path.read_text(encoding="utf-8"))
        ids.append(path.stem)
        metas.append({"source": path.name})

    collection.upsert(documents=docs, ids=ids, metadatas=metas)
    print(f"FAQ ingérée dans Chroma : {len(docs)} documents.")


if __name__ == "__main__":
    main()
