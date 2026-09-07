"""Local web viewer for crawler-social.

This package is deliberately separate from the crawler: importing the
crawler CLI must never pull in FastAPI, and the server must never import
the crawler's write path.
"""
