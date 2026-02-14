"""Configuration test fixtures.

The original get_settings cache fixture was removed when the placeholder
config.py was replaced by _settings.py (Phase 1). Settings instances are
now created directly in tests — no LRU cache to manage.
"""
