"""Only the reviewed, hash-pinned Markdown corpus can become active evidence."""

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files

from canopy.policy import POLICY_VERSION

CHUNKER_VERSION = "markdown-sections-v1"


class CorpusInvalid(RuntimeError):
    pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Section:
    ordinal: int
    anchor: str
    heading: str
    text: str


@dataclass(frozen=True)
class Source:
    key: str
    file: str
    version: str
    sha256: str
    title: str
    sections: tuple[Section, ...]


def chunk_markdown(markdown: str) -> tuple[str, tuple[Section, ...]]:
    """One bounded section per chunk; preserve exceptions and citations together.

    Reject oversize sections instead of silently truncating or separating qualifications.
    Larger document ingestion is deliberately deferred.
    """
    lines = markdown.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise CorpusInvalid("Policy Markdown requires a title")
    title = lines[0][2:].strip()
    sections = []
    blocks = re.split(r"^## ", "\n".join(lines[1:]), flags=re.MULTILINE)
    if blocks[0].strip():
        raise CorpusInvalid("Place all policy text inside a named section")
    for block in blocks[1:]:
        heading, _, body = block.partition("\n")
        anchor = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
        text = f"# {title}\n\n## {heading.strip()}\n\n{body.strip()}"
        if not body.strip() or len(heading) > 200 or len(text.encode("utf-8")) > 2000:
            raise CorpusInvalid("Policy sections must contain 1–2000 UTF-8 bytes")
        if not anchor or any(section.anchor == anchor for section in sections):
            raise CorpusInvalid("Policy section anchors must be unique")
        sections.append(Section(len(sections), anchor, heading.strip(), text))
    if not title or len(title) > 200 or not 1 <= len(sections) <= 16:
        raise CorpusInvalid("Policy Markdown requires a title and sections")
    return title, tuple(sections)


def _load_corpus() -> tuple[Source, ...]:
    root = files("canopy").joinpath("corpus")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["rules_version"] != POLICY_VERSION
        or manifest["chunker_version"] != CHUNKER_VERSION
    ):
        raise CorpusInvalid("Corpus and deterministic application versions do not match")
    sources = []
    for entry in manifest["sources"]:
        if not re.fullmatch(r"[a-z]+-v[0-9.]+\.md", entry["file"]):
            raise CorpusInvalid("Invalid policy source path")
        raw = root.joinpath(entry["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise CorpusInvalid("Policy source differs from its approved manifest")
        title, sections = chunk_markdown(raw.decode("utf-8"))
        sources.append(Source(**entry, title=title, sections=sections))
    if not sources or len({source.key for source in sources}) != len(sources):
        raise CorpusInvalid("Corpus requires unique source keys")
    return tuple(sources)


def load_corpus() -> tuple[Source, ...]:
    try:
        return _load_corpus()
    except (OSError, UnicodeError, KeyError, TypeError, ValueError):
        raise CorpusInvalid("Approved corpus files or manifest are invalid") from None
