"""Data-source adapters and the connection manager.

Every source is reached through :class:`~backend.connectors.base.DataScoutAdapter`.
The interface is deliberately read-only: it exposes no method that could
create, modify or delete anything in a source system.
"""

from backend.connectors.base import (
    AdapterError,
    AdapterNotConfigured,
    ConnectionStatus,
    DataScoutAdapter,
)
from backend.connectors.data_scout import NvidiaInternalDataScoutAdapter
from backend.connectors.local_files import LocalFilesAdapter
from backend.connectors.registry import ConnectionManager, build_adapter
from backend.connectors.synthetic import SyntheticAdapter

__all__ = [
    "DataScoutAdapter",
    "AdapterError",
    "AdapterNotConfigured",
    "ConnectionStatus",
    "NvidiaInternalDataScoutAdapter",
    "LocalFilesAdapter",
    "SyntheticAdapter",
    "ConnectionManager",
    "build_adapter",
]
