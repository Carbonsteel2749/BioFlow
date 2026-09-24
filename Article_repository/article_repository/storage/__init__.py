"""Storage and persistence abstractions."""
# __init__.py
from .metadata_store import MetadataStore, LiteratureMetadata
from .repository import LiteratureRepository
from .indexes import create_all_indexes

__all__ = ["MetadataStore", "LiteratureMetadata", "LiteratureRepository", "create_all_indexes"]
