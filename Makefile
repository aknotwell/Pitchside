SEED ?= 42
HOME_CLUB ?= ashenford
AWAY_CLUB ?= greywater

.PHONY: setup gen batch tune live viewer test lint fmt

setup:
	cd backend && uv sync

gen:
	cd backend && uv run pitchside-gen match --seed $(SEED) --home $(HOME_CLUB) --away $(AWAY_CLUB) --out ../data/matches

batch:
	cd backend && uv run pitchside-gen batch --seeds 1-20 --out ../data/matches/batch

# Try tuning constants without editing code, e.g. make tune ARGS="SHOT_BIAS=-0.03"
tune:
	cd backend && uv run python scripts/tune.py $(ARGS)

# Stream live matches over WebSocket at ws://127.0.0.1:8000/ws/match
live:
	cd backend && uv run pitchside-live

# Live pitch viewer at http://localhost:5173 (run `make live` in another terminal)
viewer:
	cd frontend && ([ -d node_modules ] || npm ci) && npm run dev

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .
