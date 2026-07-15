from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


ARCHIVE_PREFIX = "agent-call-governor/"
INCLUDE_DIRECTORIES = frozenset(
    {".codex-plugin", ".agents", "hooks", "skills", "schemas", "docs"}
)
INCLUDE_ROOT_FILES = frozenset(
    {
        "README.md",
        "README.ko.md",
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "install.ps1",
        "install.sh",
    }
)
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".superpowers",
        "superpowers",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "dist",
        "build",
    }
)
PRIVATE_SUFFIXES = (
    ".env",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".jsonl",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".token",
    ".secret",
    ".credentials",
    ".private",
    "-wal",
    "-shm",
)
WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


@dataclass(frozen=True)
class ArchiveInput:
    relative: PurePosixPath
    path: Path
    data: bytes
    mode: int


@dataclass(frozen=True)
class GitIndexEntry:
    path: str
    object_id: str


def _is_reparse(info: os.stat_result | object) -> bool:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & marker)


def _validate_tracked_name(name: str) -> tuple[PurePosixPath, str]:
    if not isinstance(name, str) or not name:
        raise ValueError("tracked release path must be a nonempty string")
    if "\\" in name or ":" in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError("unsafe tracked path syntax")
    if name.startswith("/"):
        raise ValueError("absolute tracked path is unsafe")

    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("tracked path traversal or noncanonical component")
    for part in parts:
        if part != part.rstrip(" ."):
            raise ValueError("tracked path has a Windows-ambiguous component")
        device_name = part.split(".", 1)[0].casefold()
        if device_name in WINDOWS_RESERVED:
            raise ValueError("tracked path has a reserved Windows component")

    pure = PurePosixPath(*parts)
    if pure.is_absolute() or pure.as_posix() != name:
        raise ValueError("tracked path is not canonical POSIX relative form")
    normalized = unicodedata.normalize("NFC", name).casefold()
    return pure, normalized


def _is_allowed(relative: PurePosixPath) -> bool:
    return (
        len(relative.parts) == 1 and relative.name in INCLUDE_ROOT_FILES
    ) or relative.parts[0] in INCLUDE_DIRECTORIES


def _is_excluded(relative: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in parts):
        return True
    name = relative.name.casefold()
    if name == ".env" or name.startswith(".env."):
        return True
    return name.endswith(PRIVATE_SUFFIXES)


def _git_tracked_names(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--"],
        shell=False,
        check=True,
        capture_output=True,
    )
    payload = result.stdout
    if payload and not payload.endswith(b"\0"):
        raise ValueError("git ls-files returned a non-NUL-terminated record")
    try:
        return [part.decode("utf-8") for part in payload.split(b"\0") if part]
    except UnicodeDecodeError as exc:
        raise ValueError("git tracked path is not valid UTF-8") from exc


def _git_index_snapshot(root: Path) -> list[GitIndexEntry]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "-z", "--"],
        shell=False,
        check=True,
        capture_output=True,
    )
    payload = result.stdout
    if payload and not payload.endswith(b"\0"):
        raise ValueError("git ls-files --stage returned a non-NUL-terminated record")

    entries: list[GitIndexEntry] = []
    seen_paths: set[str] = set()
    for record in (part for part in payload.split(b"\0") if part):
        try:
            header, encoded_path = record.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
            path = encoded_path.decode("utf-8")
            object_text = object_id.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("git index entry has an invalid release shape") from exc
        if stage != b"0":
            raise ValueError("unmerged git index entries cannot be released")
        if mode not in {b"100644", b"100755"}:
            raise ValueError("git release input must be a regular blob")
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", object_text) is None:
            raise ValueError("git index entry has an invalid object id")
        if path in seen_paths:
            raise ValueError("duplicate path in git index snapshot")
        seen_paths.add(path)
        entries.append(GitIndexEntry(path=path, object_id=object_text))
    return entries


def _git_blob_bytes(root: Path, object_id: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", object_id],
        shell=False,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _read_stable_regular_file(path: Path, expected: os.stat_result) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse(opened):
            raise ValueError("release input must remain a regular file")
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("release input changed during validation")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)

    after = path.lstat()
    identity_before = (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_after != identity_before or stat.S_ISLNK(after.st_mode) or _is_reparse(after):
        raise ValueError("release input changed during read")
    return b"".join(chunks)


def _collect_archive_inputs(
    root: Path,
    *,
    tracked_names: Iterable[str] | None = None,
) -> list[ArchiveInput]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("release root must be a directory")
    from_git_index = tracked_names is None
    if from_git_index:
        names = _git_tracked_names(root)
        snapshot = _git_index_snapshot(root)
        if [entry.path for entry in snapshot] != names:
            raise ValueError("git index changed while capturing the release snapshot")
        object_ids = {entry.path: entry.object_id for entry in snapshot}
    else:
        names = list(tracked_names)
        object_ids = {}

    validated: list[PurePosixPath] = []
    normalized_names: set[str] = set()
    for name in names:
        relative, normalized = _validate_tracked_name(name)
        if normalized in normalized_names:
            raise ValueError("duplicate normalized tracked path")
        normalized_names.add(normalized)
        validated.append(relative)

    entries: list[ArchiveInput] = []
    for relative in validated:
        if not _is_allowed(relative) or _is_excluded(relative):
            continue

        current = root
        leaf: os.stat_result | None = None
        for index, part in enumerate(relative.parts):
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError as exc:
                raise ValueError(f"tracked release input is missing: {relative}") from exc
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise ValueError("release input cannot be a link or reparse point")
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise ValueError("release input parent must be a directory")
            leaf = info

        assert leaf is not None
        if not stat.S_ISREG(leaf.st_mode):
            raise ValueError("release input must be a regular file")
        resolved = current.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("release input escapes repository root") from exc

        worktree_data = _read_stable_regular_file(current, leaf)
        data = (
            _git_blob_bytes(root, object_ids[relative.as_posix()])
            if from_git_index
            else worktree_data
        )
        mode = 0o755 if relative.as_posix() == "install.sh" else 0o644
        entries.append(ArchiveInput(relative=relative, path=current, data=data, mode=mode))

    if not entries:
        raise ValueError("release archive has no eligible tracked files")
    return sorted(entries, key=lambda entry: entry.relative.as_posix())


def collect_release_files(
    root: Path,
    *,
    tracked_names: Iterable[str] | None = None,
) -> list[Path]:
    return [entry.path for entry in _collect_archive_inputs(root, tracked_names=tracked_names)]


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return left.resolve(strict=False) == right.resolve(strict=False)


def build_archive(root: Path, output: Path) -> None:
    root = root.resolve(strict=True)
    entries = _collect_archive_inputs(root)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    final = output.parent.resolve(strict=True) / output.name
    if not final.name or final.name in {".", ".."}:
        raise ValueError("archive output filename is invalid")
    if final.exists() or final.is_symlink():
        info = final.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError("archive output cannot be a link or reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("archive output must be a regular file")
    if any(_same_file(final, entry.path) for entry in entries):
        raise ValueError("archive output cannot replace a release input")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final.name}.", suffix=".tmp", dir=final.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            for entry in entries:
                info = zipfile.ZipInfo(
                    ARCHIVE_PREFIX + entry.relative.as_posix(),
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.create_system = 3
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = (stat.S_IFREG | entry.mode) << 16
                info.extra = b""
                info.comment = b""
                archive.writestr(info, entry.data)
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a tracked-file Codex plugin archive")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_archive(args.root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
