from __future__ import annotations

import argparse
import hashlib
import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ChecksumInput:
    path: Path
    normalized_name: str
    identity: tuple[int, int, int, int]


def _is_reparse(info: os.stat_result | object) -> bool:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & marker)


def _normalized_basename(path: Path) -> str:
    name = path.name
    if (
        not name
        or name in {".", ".."}
        or name != name.rstrip(" .")
        or ":" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError("checksum input has an unsafe basename")
    return unicodedata.normalize("NFC", name).casefold()


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    # Windows can advance st_ctime_ns on the first open after an atomic replace
    # even when the file object and its bytes are unchanged. Device, inode, size,
    # and mtime remain stable across that publication boundary and are rechecked
    # both on the opened descriptor and after all hashing completes.
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    )


def _validated_inputs(files: Iterable[Path], output: Path) -> list[ChecksumInput]:
    candidates = [Path(path) for path in files]
    if not candidates:
        raise ValueError("at least one checksum input is required")

    validated: list[ChecksumInput] = []
    resolved_names: set[str] = set()
    identities: set[tuple[int, int]] = set()
    basenames: set[str] = set()
    for candidate in candidates:
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"checksum input is missing: {candidate}") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError("checksum input cannot be a link or reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("checksum input must be a regular file")
        resolved = candidate.resolve(strict=True)
        resolved_key = os.path.normcase(str(resolved))
        identity = (info.st_dev, info.st_ino)
        if resolved_key in resolved_names or identity in identities:
            raise ValueError("duplicate input path")
        resolved_names.add(resolved_key)
        identities.add(identity)

        basename = _normalized_basename(resolved)
        if basename in basenames:
            raise ValueError("duplicate basename in checksum inputs")
        basenames.add(basename)
        validated.append(
            ChecksumInput(
                path=resolved,
                normalized_name=basename,
                identity=_identity(info),
            )
        )

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    final = output.parent.resolve(strict=True) / output.name
    if final.exists() or final.is_symlink():
        output_info = final.lstat()
        if stat.S_ISLNK(output_info.st_mode) or _is_reparse(output_info):
            raise ValueError("checksum output cannot be a link or reparse point")
        if not stat.S_ISREG(output_info.st_mode):
            raise ValueError("checksum output must be a regular file")
        output_identity = (output_info.st_dev, output_info.st_ino)
    else:
        output_identity = None

    output_key = os.path.normcase(str(final.resolve(strict=False)))
    for item in validated:
        if (
            os.path.normcase(str(item.path)) == output_key
            or output_identity == item.identity[:2]
        ):
            raise ValueError("checksum output path appears among inputs")
    return sorted(validated, key=lambda item: (item.normalized_name, item.path.name))


def _sha256(item: ChecksumInput) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(item.path, flags)
    except OSError as exc:
        raise ValueError(f"checksum input changed before read: {item.path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or _identity(opened) != item.identity
        ):
            raise ValueError(f"checksum input changed before read: {item.path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)

    try:
        after = item.path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"checksum input changed during read: {item.path}") from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or _is_reparse(after)
        or _identity(after) != item.identity
    ):
        raise ValueError(f"checksum input changed during read: {item.path}")
    return digest.hexdigest()


def _verify_unchanged(item: ChecksumInput, phase: str) -> None:
    try:
        current = item.path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"checksum input changed {phase}: {item.path}") from exc
    if (
        stat.S_ISLNK(current.st_mode)
        or _is_reparse(current)
        or not stat.S_ISREG(current.st_mode)
        or _identity(current) != item.identity
    ):
        raise ValueError(f"checksum input changed {phase}: {item.path}")


def write_checksums(files: Iterable[Path], output: Path) -> None:
    output = Path(output)
    validated = _validated_inputs(files, output)
    final = output.parent.resolve(strict=True) / output.name
    lines = [f"{_sha256(item)}  {item.path.name}\n" for item in validated]
    for item in validated:
        _verify_unchanged(item, "after hashing")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final.name}.", suffix=".tmp", dir=final.parent
    )
    temporary = Path(temporary_name)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        for item in validated:
            _verify_unchanged(item, "before checksum publication")
        os.replace(temporary, final)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write deterministic SHA-256 release checksums")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    write_checksums(args.files, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
