"""Scheduler: selects mature checks and enqueues them.

Real scheduling (CheckSchedule polling with jitter) lands in T06. This package
currently exposes a minimal entrypoint (``app.scheduler.main``) so the
``scheduler`` compose service can start.
"""
