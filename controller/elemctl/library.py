"""Controller-owned program catalog and compiled artifact cache."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .program_metadata import extract_metadata

if TYPE_CHECKING:
    from elements.types import CompiledManifest


log = logging.getLogger(__name__)
_CACHE_VERSION = 1
_PROGRAM_ID_RE = re.compile(r'^[A-Za-z0-9._-]+$')


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
            try:
                source = path.read_text()
            except OSError as e:
                programs[program_id] = self._entry_from_read_error(path, e)
                continue

            programs[program_id] = self._entry_from_source(program_id, path, source)

        self._programs = programs
        return self.list_programs()

    def list_programs(self) -> list[ProgramEntry]:
        return [self._programs[key] for key in sorted(self._programs)]

    def get(self, program_id: str) -> ProgramEntry | None:
        return self._programs.get(program_id)

    def publish(self, program_id: str, source: str) -> ProgramEntry:
        self._validate_program_id(program_id)
        if not isinstance(source, str):
            raise ValueError("'source' must be a string")

        root = Path(self._animations_dir)
        if root.exists() and not root.is_dir():
            raise ValueError(f'program library path is not a directory: {root}')
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(f'cannot create program library directory: {e}') from e

        path = root / f'{program_id}.py'
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=root,
                prefix=f'.{program_id}.',
                suffix='.tmp',
                delete=False,
            ) as tmp_fp:
                tmp_fp.write(source)
                tmp_path = Path(tmp_fp.name)
            os.replace(tmp_path, path)
        except OSError as e:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise ValueError(f'cannot write program {program_id}: {e}') from e

        entry = self._entry_from_source(program_id, path, source)
        self._programs[program_id] = entry
        return entry

    @staticmethod
    def _validate_program_id(program_id: str) -> None:
        if not isinstance(program_id, str) or not program_id:
            raise ValueError("'program_id' is required")
        if not _PROGRAM_ID_RE.fullmatch(program_id):
            raise ValueError(
                "program_id may only contain letters, numbers, ., _, and -"
            )

    @staticmethod
    def _entry_from_read_error(path: Path, error: OSError) -> ProgramEntry:
        return ProgramEntry(
            program_id=path.stem,
            path=str(path.resolve()),
            source=None,
            source_hash=None,
            beat=None,
            duration=None,
            error=f'cannot read file: {error}',
        )

    @staticmethod
    def _entry_from_source(program_id: str, path: Path, source: str) -> ProgramEntry:
        resolved = str(path.resolve())
        source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
        try:
            beat, duration = extract_metadata(source, resolved)
        except ValueError as e:
            return ProgramEntry(
                program_id=program_id,
                path=resolved,
                source=source,
                source_hash=source_hash,
                beat=None,
                duration=None,
                error=str(e),
            )

        return ProgramEntry(
            program_id=program_id,
            path=resolved,
            source=source,
            source_hash=source_hash,
            beat=beat,
            duration=duration,
            error=None,
        )


class ArtifactCache:
    def __init__(self):
        self._cache: dict[tuple[str, str, int], CompiledManifest] = {}

    def get(self, source_hash: str, topology: str) -> CompiledManifest | None:
        return self._cache.get((source_hash, topology, _CACHE_VERSION))

    def put(self, source_hash: str, topology: str, manifest: CompiledManifest) -> None:
        self._cache[(source_hash, topology, _CACHE_VERSION)] = manifest
