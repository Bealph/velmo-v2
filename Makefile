.PHONY: install up down migrate seed seed-kb chat chat-ui eval ci test fmt lint typecheck

# Extras necessaires a l'execution (openai/langchain + streamlit). Sans eux,
# `uv run` resynchronise l'env et les retire => ModuleNotFoundError.
# `vector` est exclu : il tire PyTorch (~2.5 Go), voir `make install-vector`.
RUN := uv run --extra llm --extra demo

install:
	uv sync --extra llm --extra demo

install-vector:
	uv sync --all-extras

up:
	docker compose up -d

down:
	docker compose down

migrate:
	$(RUN) alembic upgrade head

seed:
	$(RUN) python scripts/seed.py

seed-kb:
	$(RUN) python scripts/seed_kb.py

chat:
	$(RUN) python -m velmo.cli

# Interface web : chat client + panneau « coulisses » (memoire, garde-fous, sources RAG).
# `python -m streamlit` contourne un streamlit.exe bloque par l'antivirus.
chat-ui:
	$(RUN) python -m streamlit run scripts/chat_app.py

eval:
	$(RUN) python -m velmo.mlops.score

ci: test

test:
	$(RUN) pytest tests/ -v

fmt:
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

lint:
	$(RUN) ruff check .

typecheck:
	$(RUN) mypy src
