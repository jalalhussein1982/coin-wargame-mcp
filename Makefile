.PHONY: install test stdio http inspector bench clean

GAMES ?= 3
SEED ?= 1
RED ?= stub
GOV ?= stub
MODEL ?= claude-sonnet-5-5
PORT ?= 8000

install:
	git submodule update --init
	uv sync --all-extras

test:
	uv run pytest -q

stdio:
	uv run python -m coin_wargame_mcp.server

http:
	uv run python -m coin_wargame_mcp.server --http --no-auth --port $(PORT)

inspector:
	uv run mcp dev coin_wargame_mcp/server.py

bench:
	uv run python -m bench --games $(GAMES) --seed $(SEED) --red $(RED) --gov $(GOV) --model $(MODEL)

clean:
	rm -rf .pytest_cache .hypothesis coin_wargame_mcp/__pycache__ tests/__pycache__ bench/__pycache__ games.db
