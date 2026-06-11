"""Sourcing fan-out (WAT Stage 6c parallel_sourcing_fanout).

Parallel in FINDING, serial in SUBMITTING. Three sourcers triage disjoint pools
concurrently and return ranked, pre-vetted candidates; the coordinator merges,
dedupes and ranks them; the agent worker then drains the list serially through
the single-session browser.
"""
