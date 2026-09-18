"""Broker adapters. Each exposes detect(path) -> bool and parse(path) -> [Holding].

Detection sniffs file content, never the filename, so exports can be dropped in
under whatever name the broker gave them.
"""
from . import generic, ing

#: Broker-specific adapters, tried in order.
SPECIFIC = [ing]

#: Tried only once every specific adapter has declined. A generic reader
#: recognises files a dedicated adapter parses better, so it must never
#: compete with one.
FALLBACK = [generic]

ADAPTERS = SPECIFIC + FALLBACK


def detect(path):
    """Return the adapter that recognises this file, or None."""
    for a in SPECIFIC + FALLBACK:
        try:
            if a.detect(path):
                return a
        except Exception:
            continue
    return None
