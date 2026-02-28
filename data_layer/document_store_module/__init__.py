"""document_store_module package.

Provides BaseDocumentStore (ABC) and LocalDocumentStore (default local filesystem implementation).
"""

from .core.base import BaseDocumentStore
from .core.impl import LocalDocumentStore

__all__ = ["BaseDocumentStore", "LocalDocumentStore"]
