"""Broker adapters. Each exposes detect(path) -> bool and parse(path) -> [Holding].

Detection sniffs file content, never the filename, so exports can be dropped in
under whatever name the broker gave them.
"""
from . import ing

ADAPTERS = [ing]


def detect(path):
    """Return the adapter that recognises this file, or None."""
    for a in ADAPTERS:
        try:
            if a.detect(path):
                return a
        except Exception:
            continue
    return None
