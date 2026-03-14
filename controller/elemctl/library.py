"""Controller-owned program catalog and compiled artifact cache."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .program_metadata import extract_metadata

if TYPE_CHECKING:
    from elements.types import CompiledManifest


log = logging.getLogger(__name__)
_CACHE_VERSION = 1


@dataclass(frozen=True)
class ProgramEntry:
    program_id: str
    path: str
    source: str | None
    source_hash: str | None
    beat: float | None
    duration: float | None
    error: str | None


class ProgramLibrary:
    """Scan and cache a filesystem-backed catalog of known programs."""

    def __init__(self, animations_dir: str):
        self._animations_dir = os.path.expanduser(animations_dir)
        self._programs: dict[str, ProgramEntry] = {}
        self.rescan()

    def rescan(self) -> list[ProgramEntry]:
        programs: dict[str, ProgramEntry] = {}
        root = Path(self._animations_dir)

        if not root.exists():
            self._programs = programs
            return []
        if not root.is_dir():
            log.warning('program library path is not a directory: %s', root)
            self._programs = programs
            return []

        try:
            paths = sorted(root.glob('*.py'))
        except OSError as e:
            log.warning('cannot scan program library %s: %s', root, e)
            self._programs = programs
            return []

        for path in paths:
            program_id = path.stem
            resolved = str(path.resolve())
            try:
                source = path.read_text()
            except OSError as e:
                programs[program_id] = ProgramEntry(
                    program_id=program_id,
                    path=resolved,
                    source=None,
                    source_hash=None,
                    beat=None,
                    duration=None,
                    error=f'cannot read file: {e}',
                )
                continue

            source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
            try:
                beat, duration = extract_metadata(source, resolved)
            except ValueError as e:
                programs[program_id] = ProgramEntry(
                    program_id=program_id,
                    path=resolved,
                    source=source,
                    source_hash=source_hash,
                    beat=None,
                    duration=None,
                    error=str(e),
                )
                continue

            programs[program_id] = ProgramEntry(
                program_id=program_id,
                path=resolved,
                source=source,
                source_hash=source_hash,
                beat=beat,
                duration=duration,
                error=None,
            )

        self._programs = programs
        return self.list_programs()

    def list_programs(self) -> list[ProgramEntry]:
        return [self._programs[key] for key in sorted(self._programs)]

    def get(self, program_id: str) -> ProgramEntry | None:
        return self._programs.get(program_id)


class ArtifactCache:
    def __init__(self):
        self._cache: dict[tuple[str, str, int], CompiledManifest] = {}

    def get(self, source_hash: str, topology: str) -> CompiledManifest | None:
        return self._cache.get((source_hash, topology, _CACHE_VERSION))

    def put(self, source_hash: str, topology: str, manifest: CompiledManifest) -> None:
        self._cache[(source_hash, topology, _CACHE_VERSION)] = manifest
