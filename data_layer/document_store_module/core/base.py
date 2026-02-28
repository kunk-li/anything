from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, List


class BaseDocumentStore(ABC):
    """Document store abstract base class.

    This module stores *parsed* document content (already extracted by a parser module).
    It does not parse raw files.
    """

    @abstractmethod
    def create_document(self, content: str, file_name: str, file_type: str, content_hash: str) -> Dict[str, str]:
        """Create a standardized document dict (generate doc_id) without persisting to storage."""
        raise NotImplementedError

    @abstractmethod
    def save_document(self, document: Dict[str, str]) -> bool:
        """Persist a document and its info file."""
        raise NotImplementedError

    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, str]]:
        """Read a document (content + metadata) by doc_id and update last_access_time."""
        raise NotImplementedError

    @abstractmethod
    def update_document(self, doc_id: str, new_content: str, new_content_hash: str) -> bool:
        """Update stored content and metadata by doc_id."""
        raise NotImplementedError

    @abstractmethod
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document file and its info file by doc_id."""
        raise NotImplementedError

    @abstractmethod
    def check_duplicate_file(
        self,
        content_hash: str,
        file_name: str,
        file_type: str,
        content_length: int,
        force_save: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Check whether an upload is duplicate using hash + key fields."""
        raise NotImplementedError

    @abstractmethod
    def identify_zombie_files(self, threshold_days: int) -> List[dict]:
        """Identify zombie files in storage."""
        raise NotImplementedError

    @abstractmethod
    def clean_zombie_files(self, threshold_days: int, backup: bool = False) -> Dict[str, int]:
        """Clean zombie files and optionally back them up."""
        raise NotImplementedError

    @abstractmethod
    def read_info_file(self, doc_id: str) -> Optional[Dict[str, str]]:
        """Read info JSON by doc_id."""
        raise NotImplementedError

    @abstractmethod
    def write_info_file(self, document: Dict[str, str]) -> bool:
        """Write info JSON for the given standardized document dict."""
        raise NotImplementedError
