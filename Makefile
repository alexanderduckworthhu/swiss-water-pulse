.PHONY: download process validate render encode all still preview test

download:
	uv run python -m src.cli download

process:
	uv run python -m src.cli process

validate:
	uv run python -m src.cli validate

render:
	uv run python -m src.cli render

encode:
	uv run python -m src.cli encode

still:
	uv run python -m src.cli still --date 2025-06-15

preview:
	uv run python -m src.cli preview --date 2025-06-15

draft:
	uv run python -m src.cli render --days 2025-06-01:2025-06-10 --draft

all: download process validate render encode

test:
	uv run python -m unittest discover -s tests -v
