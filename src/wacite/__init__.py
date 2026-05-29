"""wa-cite-check: a standalone Washington legal citation checker.

Phase 1 (this version) is a format/accuracy audit: it extracts every citation
from a motion (DOCX/PDF/text), confirms each exists in the wa-legal-ai corpus,
and cross-checks the case name, year, parallel cite, and authority status.

All checking runs offline against a portable SQLite index built once from the
corpus PostgreSQL database. See README.md.
"""

__version__ = "0.1.0"
