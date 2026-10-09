SEED ?= 42
HOME_CLUB ?= ashenford
AWAY_CLUB ?= greywater

.PHONY: setup gen batch tune test lint fmt

setup:
	cd backend && uv sync

gen:
	cd backend && uv run pitchside-gen match --seed $(SEED) --home $(HOME_CLUB) --away $(AWAY_CLUB) --out ../data/matches

batch:
	cd backend && uv run pitchside-gen batch --seeds 1-20 --out ../data/matches/batch

# Try tuning constants without editing code, e.g. make tune ARGS="SHOT_BIAS=-0.03"
tune:
	cd backend && uv run python scripts/tune.py $(ARGS)

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .
