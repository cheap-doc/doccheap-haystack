"""A Haystack component that reads identity documents with the doc.cheap API."""

from ._version import __version__
from .converter import DocCheapConverter

__all__ = ["DocCheapConverter", "__version__"]
