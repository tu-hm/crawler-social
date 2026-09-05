.PHONY: test install login session crawl chrome-cdp posts clean

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

clean:
	rm -rf .pytest_cache data/fixtures
	find . -name "__pycache__" -type d -exec rm -rf {} +
