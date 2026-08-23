"""HTTP routers for the LocalBib core (Backend-Modularisierung, #71).

Pilot routers (#79): stats, version, banner, license, appearance.
Per the import-linter contracts, a router module must never import from
another ``routers`` module — cross-router shared logic belongs in ``services``.
"""
