"""Dramatiq worker actors (expiry, ssl, vt, healthcheck, alerter, digest).

The real actor/broker wiring lands in T06. This package currently exposes a
minimal entrypoint (``app.workers.main``) so the ``worker`` compose service can
start and stay healthy against Redis.
"""
