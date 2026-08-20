"""
Shared helpers/config used by every stale-resource check module.
"""

import os
from datetime import datetime, timezone

REGION = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-east-1"))
SNAPSHOT_AGE_DAYS = int(os.environ.get("SNAPSHOT_AGE_DAYS", "90"))
STOPPED_INSTANCE_AGE_DAYS = int(os.environ.get("STOPPED_INSTANCE_AGE_DAYS", "30"))


def days_ago(dt):
    """Return the number of whole days between now (UTC) and dt, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days
