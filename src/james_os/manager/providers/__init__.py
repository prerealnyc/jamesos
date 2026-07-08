"""The D8 provider layer, ported from bm2.0 backend/app/adapters/.

Every external dependency sits behind a Protocol in base.py; mocks.py is the
deterministic keyless suite (backed by fixtures.py) and live.py the real
vendors, wired per-key with mock fallback so go-live is incremental. Agents
depend on the interfaces only — vendor swaps are config changes.
"""

from .base import Providers, get_providers

__all__ = ["Providers", "get_providers"]
