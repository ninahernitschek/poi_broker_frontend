"""
Flask extensions initialization.

This module creates and exports shared extension instances 
without an app instance to avoid circular imports.
They are configured in the app factory (create_app).
"""

from .huey_config import create_huey

# Main Huey instance - created based on HUEY_BACKEND environment variable
# Development (default): MemoryHuey with immediate=True (synchronous)
# Production: SqliteHuey with persistent queue
# NOTE: this runs at import time, before any Flask app exists. With
# HUEY_BACKEND=sqlite, HUEY_SQLITE_PATH must be set explicitly — there is no
# instance-folder fallback outside an app context, by design (a silent
# cwd-derived fallback would let web and worker each create their own huey.db
# and silently drop tasks). Missing path fails fast with RuntimeError.
huey = create_huey()
