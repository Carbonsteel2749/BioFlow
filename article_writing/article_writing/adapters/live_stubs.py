"""Shared live-adapter errors. Concrete ports live in dedicated modules."""

from __future__ import annotations

__all__ = ["LiveAdapterNotReady"]


class LiveAdapterNotReady(RuntimeError):
    """Raised when a live port is selected but no usable source was given."""
