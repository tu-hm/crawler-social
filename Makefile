.PHONY: test install login session crawl full chrome-cdp posts comments serve serve-check test-server vendor vendor-verify clean

# Run tests first by default.
LIMIT ?= 10
PAGE ?= ""
TARGETS ?= example.txt

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

# Whole posts -- body, exact publication time, comments and replies -- for
# every URL in TARGETS. One page load per post, so it is the slow one.
#   make full                      # every target in example.txt
#   make full TARGETS=mine.txt LIMIT=5
full: test
	uv run crawler crawl --targets "$(TARGETS)" --full --limit $(LIMIT)

posts:
	uv run crawler posts --limit $(LIMIT)

comments:
	uv run crawler comments --limit $(LIMIT)

# Local, read-only web viewer on http://127.0.0.1:8765
serve:
	uv run crawler serve

# Every registered route is smoke-tested, so a new route cannot ship
# without one.
test-server:
	uv run pytest tests/test_smoke_routes.py

# Start the viewer only after the whole suite is green.
serve-check: test
	uv run crawler serve

# Vendored front-end libraries. Committed to the repo, so a clone plus
# `uv sync` works offline with no Node installed. These targets
# exist to re-fetch and diff, never as a build step.
HTMX_VERSION ?= 2.0.6
ALPINE_CSP_VERSION ?= 3.14.9
VENDOR := src/crawler_social/server/static/vendor

vendor:
	curl -fsSL -o $(VENDOR)/htmx.min.js \
	  https://unpkg.com/htmx.org@$(HTMX_VERSION)/dist/htmx.min.js
	curl -fsSL -o $(VENDOR)/alpine-csp.min.js \
	  https://cdn.jsdelivr.net/npm/@alpinejs/csp@$(ALPINE_CSP_VERSION)/dist/cdn.min.js
	cd $(VENDOR) && shasum -a 256 htmx.min.js alpine-csp.min.js > SHASUMS256
	@grep -q 'CSP-friendly build' $(VENDOR)/alpine-csp.min.js \
	  || { echo "ERROR: alpine-csp.min.js is not the CSP build"; exit 1; }
	@echo "pinned htmx $(HTMX_VERSION), alpine-csp $(ALPINE_CSP_VERSION)"

vendor-verify:
	cd $(VENDOR) && shasum -a 256 --check SHASUMS256

clean:
	rm -rf .pytest_cache data/fixtures
	find . -name "__pycache__" -type d -exec rm -rf {} +
