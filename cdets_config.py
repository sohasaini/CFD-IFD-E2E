from __future__ import annotations

import os

CONSUMER_KEY = os.getenv("CDETS_CONSUMER_KEY", "").strip()
CONSUMER_SECRET = os.getenv("CDETS_CONSUMER_SECRET", "").strip()
