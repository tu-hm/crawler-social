.PHONY: test install login session crawl chrome-cdp posts serve serve-check test-server clean

# Run tests first by default.
LIMIT ?= 10
PAGE ?= ""

test: install
	uv run pytest

install:
	uv sync --dev

# Sign in by hand, once, before the first crawl.
login:
	uv run crawler login

# Report whether the profile still holds a live session.
session:
	uv run crawler session

# Start your own Chrome for CRAWLER_ATTACH_MODE=cdp, then log in in that
# window and leave it open while crawls run.
PROFILE ?= $(HOME)/.local/state/crawler-social/chrome/default
PORT ?= 9222
chrome-cdp:
	google-chrome --remote-debugging-port=$(PORT) --user-data-dir="$(PROFILE)" &

crawl: test
	uv run crawler crawl "$(PAGE)"

posts:
	uv run crawler posts --limit $(LIMIT)

# Local, read-only web viewer on http://127.0.0.1:8765
serve:
	uv run crawler serve

# The verification gate (plans/v2/10): every registered route smoke-tested,
# so a new route cannot ship without one.
test-server:
	uv run pytest tests/test_smoke_routes.py

# Start the viewer only after the whole suite is green.
serve-check: test
	uv run crawler serve

clean:
	rm -rf .pytest_cache data/fixtures
	find . -name "__pycache__" -type d -exec rm -rf {} +
