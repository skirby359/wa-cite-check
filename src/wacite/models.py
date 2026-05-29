"""Core data structures shared across the checker."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """Severity of a finding, used for grouping and exit codes."""

    ERROR = "error"      # likely wrong: not found, name/year/parallel mismatch
    WARNING = "warning"  # correct but risky: negative treatment
    INFO = "info"        # advisory: pin-page not verified, short-form linked


class FindingType(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    NAME_MISMATCH = "NAME_MISMATCH"
    YEAR_MISMATCH = "YEAR_MISMATCH"
    PARALLEL_MISMATCH = "PARALLEL_MISMATCH"
    NEGATIVE_TREATMENT = "NEGATIVE_TREATMENT"
    PIN_NOT_VERIFIED = "PIN_NOT_VERIFIED"


# Map each finding type to its default severity.
FINDING_SEVERITY: dict[FindingType, Severity] = {
    FindingType.NOT_FOUND: Severity.ERROR,
    FindingType.NAME_MISMATCH: Severity.ERROR,
    FindingType.YEAR_MISMATCH: Severity.ERROR,
    FindingType.PARALLEL_MISMATCH: Severity.ERROR,
    FindingType.NEGATIVE_TREATMENT: Severity.WARNING,
    FindingType.PIN_NOT_VERIFIED: Severity.INFO,
}


@dataclass
class ParsedCitation:
    """A citation found in a document, broken into components.

    For statutes/rules, only ``raw`` and ``subsection`` are typically set.
    For case reporter cites, the name/volume/reporter/page/year are populated.
    """

    raw: str                          # full matched citation text
    kind: str                         # "case" | "rcw" | "wac" | "const" | "rule"
    start: int = 0                    # char offset in source text
    end: int = 0
    # Case components
    name: str | None = None           # e.g. "State v. Smith"
    volume: str | None = None
    reporter: str | None = None
    page: str | None = None
    pincite: str | None = None        # e.g. "512" from "199 Wn.2d 1, 512"
    year: int | None = None
    parallel: list[str] = field(default_factory=list)  # other reporter cites in the same cite
    # Resolution target used for lookup (reporter cite for cases, full cite otherwise)
    lookup_key: str = ""
    # True when this citation was reconstructed from a short form (Id./supra)
    from_shortform: bool = False


@dataclass
class AuthorityRecord:
    """A row from the SQLite citation index."""

    authority_id: str
    authority_type: str
    canonical_cite: str
    display_title: str | None
    year: int | None
    court_name: str | None
    court_level: int | None
    authority_status: str
    parallel_cites: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """A single issue flagged against one citation."""

    citation: ParsedCitation
    finding_type: FindingType
    severity: Severity
    message: str
    authority: AuthorityRecord | None = None

    def to_dict(self) -> dict:
        cit = self.citation
        return {
            "type": self.finding_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "citation": cit.raw,
            "kind": cit.kind,
            "offset": cit.start,
            "from_shortform": cit.from_shortform,
            "authority_id": self.authority.authority_id if self.authority else None,
            "matched_title": self.authority.display_title if self.authority else None,
        }


@dataclass
class AuditReport:
    """Result of auditing one document."""

    source: str
    citations_found: int = 0
    citations_resolved: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.WARNING)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "citations_found": self.citations_found,
            "citations_resolved": self.citations_resolved,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }
