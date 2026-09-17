"""Reading CHANGELOG.md so the app can show it itself.

A release history is only useful where people will actually see it, which is
inside the app rather than in a text file next to the .exe. The markdown is
parsed into plain structures here and rendered by the Settings panel, so there
is exactly one copy of the content and it cannot drift.

Only the small subset of markdown the changelog actually uses is understood:
``## version — title`` headings, ``### Section`` headings, ``- bullets`` with
hanging indentation, and paragraphs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import paths

log = logging.getLogger(__name__)

#: Em dash, en dash or hyphen - whichever separates version from title.
_SEPARATORS = ("—", "–", " - ")


@dataclass
class Section:
    heading: str
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"heading": self.heading, "items": self.items}


@dataclass
class Release:
    version: str
    title: str = ""
    intro: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "title": self.title,
            "intro": self.intro,
            "sections": [s.to_dict() for s in self.sections],
        }


def _split_heading(heading: str) -> tuple[str, str]:
    for separator in _SEPARATORS:
        if separator in heading:
            version, _, title = heading.partition(separator)
            return version.strip(), title.strip()
    return heading.strip(), ""


def parse(text: str) -> list[Release]:
    releases: list[Release] = []
    release: Release | None = None
    section: Section | None = None
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph and release is not None:
            release.intro.append(" ".join(paragraph))
        paragraph = []

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("## "):
            flush_paragraph()
            version, title = _split_heading(stripped[3:])
            release = Release(version=version, title=title)
            releases.append(release)
            section = None
            continue

        if release is None:
            continue  # the file's own preamble, before the first release

        if stripped.startswith("### "):
            flush_paragraph()
            section = Section(heading=stripped[4:].strip())
            release.sections.append(section)
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            target = section.items if section is not None else release.intro
            target.append(stripped[2:].strip())
            continue

        if not stripped or stripped.startswith(("---", "#", "|")):
            flush_paragraph()
            continue

        # An indented line continues the bullet above it; anything else starts
        # or continues a paragraph.
        if line.startswith("  ") and section is not None and section.items:
            section.items[-1] += " " + stripped
        elif line.startswith("  ") and section is None and release.intro and paragraph == []:
            release.intro[-1] += " " + stripped
        else:
            paragraph.append(stripped)

    flush_paragraph()
    return releases


def load() -> list[dict]:
    """The parsed changelog, or an empty list if it is not available."""
    try:
        text = paths.CHANGELOG_FILE.read_text(encoding="utf-8-sig")
    except OSError as exc:
        log.warning("Could not read the changelog: %s", exc)
        return []
    return [release.to_dict() for release in parse(text)]
