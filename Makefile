.PHONY: test install crawl posts clean

# Run tests first by default.
LIMIT ?= 10
PAGE ?= ""

test: install
test: install
	uv run pytest

install:
	uv sync --dev

crawl: test
	uv run crawler crawl "$(PAGE)"

posts:
	uv run crawler posts --limit $(LIMIT)

clean:
	rm -rf .pytest_cache data/fixtures
	find . -name "__pycache__" -type d -exec rm -rf {} +
