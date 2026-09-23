#!/usr/bin/env python3
"""Read-only anime directory inventory; emits JSON to stdout."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Iterator


VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv",
    ".mov", ".mp4", ".mpeg", ".mpg", ".ogv", ".rm", ".ts", ".vob",
    ".webm", ".wmv",
}
SUBTITLE_EXTENSIONS = {".ass", ".idx", ".smi", ".srt", ".ssa", ".sub", ".sup", ".vtt"}
FONT_EXTENSIONS = {".otf", ".ttc", ".ttf", ".woff", ".woff2"}
IMAGE_EXTENSIONS = {
    ".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".webp",
}
AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".alac", ".ape", ".flac", ".m4a", ".mka", ".mp3",
    ".ogg", ".opus", ".wav", ".wma",
}
ARCHIVE_EXTENSIONS = {
    ".7z", ".bz2", ".cb7", ".cbr", ".cbz", ".gz", ".lz", ".rar",
    ".tar", ".xz", ".zip",
}
DISC_EXTENSIONS = {".cue", ".dvd", ".iso", ".mdf", ".nrg"}

EXTRA_HINTS = (
    ("ncop", re.compile(r"(?i)(?<![a-z0-9])ncop(?:\s*\d+)?(?![a-z0-9])")),
    ("nced", re.compile(r"(?i)(?<![a-z0-9])nced(?:\s*\d+)?(?![a-z0-9])")),
    ("ova", re.compile(r"(?i)(?<![a-z0-9])ova(?:\s*\d+)?(?![a-z0-9])")),
    ("oad", re.compile(r"(?i)(?<![a-z0-9])oad(?:\s*\d+)?(?![a-z0-9])")),
    ("movie", re.compile(r"(?i)(?<![a-z0-9])movie(?![a-z0-9])")),
    ("sp", re.compile(r"(?i)(?<![a-z0-9])sp\s*\d*(?![a-z0-9])")),
    ("pv", re.compile(r"(?i)(?<![a-z0-9])pv(?:\s*\d+)?(?![a-z0-9])")),
    ("cm", re.compile(r"(?i)(?<![a-z0-9])cm(?:\s*\d+)?(?![a-z0-9])")),
    ("ost", re.compile(r"(?i)(?<![a-z0-9])(?:ost|soundtrack)(?![a-z0-9])")),
    ("cd", re.compile(r"(?i)(?<![a-z0-9])cd\s*\d*(?![a-z0-9])")),
    ("bonus", re.compile(r"(?i)(?<![a-z0-9])bonus(?![a-z0-9])")),
    ("interview", re.compile(r"(?i)(?<![a-z0-9])interview(?![a-z0-9])")),
    ("menu", re.compile(r"(?i)(?<![a-z0-9])menu(?![a-z0-9])")),
)
GENERIC_EXTRA_TOKEN = re.compile(r"(?i)(?<![a-z0-9])extras?(?![a-z0-9])")
LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "chs": ("zh-Hans",),
    "sc": ("zh-Hans",),
    "gb": ("zh-Hans",),
    "简": ("zh-Hans",),
    "zh-hans": ("zh-Hans",),
    "zh_hans": ("zh-Hans",),
    "zh-cn": ("zh-Hans",),
    "zh_cn": ("zh-Hans",),
    "cht": ("zh-Hant",),
    "tc": ("zh-Hant",),
    "big5": ("zh-Hant",),
    "繁": ("zh-Hant",),
    "zh-hant": ("zh-Hant",),
    "zh_hant": ("zh-Hant",),
    "zh-tw": ("zh-Hant",),
    "zh_tw": ("zh-Hant",),
    "jp": ("ja",),
    "jpn": ("ja",),
    "en": ("en",),
    "eng": ("en",),
    "简日": ("zh-Hans", "ja"),
    "繁日": ("zh-Hant", "ja"),
    "chs&jpn": ("zh-Hans", "ja"),
    "cht&jpn": ("zh-Hant", "ja"),
    "jpsc": ("ja", "zh-Hans"),
    "jptc": ("ja", "zh-Hant"),
    "chs_jp": ("zh-Hans", "ja"),
}
LANGUAGE_SUFFIX = re.compile(
    r"(?i)(?:[._\s-]+|\[)("
    + "|".join(re.escape(alias) for alias in sorted(LANGUAGE_ALIASES, key=len, reverse=True))
    + r")(?:\])?$"
)


def classify_extension(extension: str) -> str:
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if extension in FONT_EXTENSIONS:
        return "font"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive"
    if extension in DISC_EXTENSIONS:
        return "disc-image"
    return "unknown"


def subtitle_details(stem: str, relative_path: Path) -> dict[str, Any]:
    match = LANGUAGE_SUFFIX.search(stem)
    raw_language_tag: str | None = None
    language_tags: tuple[str, ...] = ()
    base_stem = stem
    if match:
        raw_language_tag = match.group(1)
        language_tags = LANGUAGE_ALIASES.get(raw_language_tag.casefold(), ())
        base_stem = stem[: match.start()].rstrip("._ -[")

    parent = relative_path.parent.as_posix()
    match_key = f"{parent}/{base_stem}" if parent != "." else base_stem
    return {
        "raw_language_tag": raw_language_tag,
        "language_tags": list(language_tags),
        "language_tag": language_tags[0] if len(language_tags) == 1 else None,
        "match_key": match_key.casefold(),
    }


def parse_filename(stem: str) -> dict[str, Any]:
    """Use Anitopy when present; otherwise extract only conservative hints."""
    try:
        import anitopy  # type: ignore[import-not-found]

        result = anitopy.parse(stem)
        return {
            "engine": "anitopy",
            "title": result.get("anime_title") or None,
            "episode": result.get("episode_number") or None,
            "group": result.get("release_group") or None,
            "resolution": result.get("video_resolution") or None,
            "source": result.get("video_source") or None,
            "codec": result.get("video_term") or None,
            "confidence": "parser output; verify with context",
        }
    except ImportError:
        pass
    except Exception as error:  # Parser quirks must not abort an inventory.
        return {
            "engine": "anitopy",
            "title": None,
            "episode": None,
            "group": None,
            "confidence": "parse error",
            "error": type(error).__name__,
        }

    group_match = re.match(r"^\[([^\]]{1,80})\]", stem)
    group = group_match.group(1).strip() if group_match else None
    episode: str | None = None
    episode_patterns = (
        r"(?i)(?<![a-z0-9])S\d{1,2}E\d{1,3}(?:\.\d+)?(?:v\d+)?(?![a-z0-9])",
        r"(?i)(?<![a-z0-9])EP\s*\d{1,3}(?:\.\d+)?(?:v\d+)?(?![a-z0-9])",
        r"(?<!\d)\[\s*(\d{1,3}(?:\.\d+)?(?:v\d+)?)\s*\]",
        r"(?<![\w])[-–]\s*(\d{1,3}(?:\.\d+)?(?:v\d+)?)(?=\s|\[|\(|$)",
        r"(?i)^(\d{1,3}(?:\.\d+)?(?:v\d+)?)$",
    )
    for pattern in episode_patterns:
        match = re.search(pattern, stem)
        if match:
            episode = match.group(1) if match.lastindex else match.group(0)
            episode = re.sub(r"(?i)^EP\s*", "", episode)
            break

    return {
        "engine": "conservative-regex",
        "title": None,
        "episode": episode,
        "group": group,
        "confidence": "low; confirm from directory and neighboring files",
    }


def classify_role(
    kind: str, relative_path: Path, parsed: dict[str, Any]
) -> tuple[str, str | None]:
    if kind in {"special", "symlink"}:
        return "unknown", None
    path_text = "/".join(relative_path.parts)
    if kind == "font" or any(part.casefold() == "fonts" for part in relative_path.parts):
        return "font", "font"
    if any(part.casefold() in {"scans", "scan", "booklet"} for part in relative_path.parts):
        return "scan", "scan"
    for hint, pattern in EXTRA_HINTS:
        if pattern.search(path_text):
            return "extra", hint
    if GENERIC_EXTRA_TOKEN.search(path_text):
        return "extra", "extra"
    if kind in {"video", "subtitle"} and parsed.get("episode"):
        return "episode-candidate", None
    return "unknown", None


def walk_entries(
    root: Path,
    errors: list[dict[str, Any]],
    skipped: list[dict[str, str]],
) -> Iterator[tuple[Path, str]]:
    """Yield filesystem entries without following symlinks or descending mounts."""
    root_device = root.stat().st_dev
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda item: item.name.casefold())
        except OSError as error:
            errors.append(
                {
                    "path": str(directory),
                    "code": error.errno,
                    "message": error.strerror or type(error).__name__,
                }
            )
            continue

        subdirectories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    yield path, "symlink"
                elif entry.is_dir(follow_symlinks=False):
                    if entry.stat(follow_symlinks=False).st_dev == root_device:
                        subdirectories.append(path)
                    else:
                        skipped.append({"path": str(path), "reason": "mount-boundary"})
                elif entry.is_file(follow_symlinks=False):
                    yield path, "file"
                else:
                    yield path, "special"
            except OSError as error:
                errors.append(
                    {
                        "path": str(path),
                        "code": error.errno,
                        "message": error.strerror or type(error).__name__,
                    }
                )
        pending.extend(reversed(subdirectories))


def inspect(root: Path) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    for path, entry_kind in walk_entries(root, errors, skipped):
        relative_path = path.relative_to(root)
        extension = path.suffix.casefold()
        kind = entry_kind if entry_kind != "file" else classify_extension(extension)
        is_symlink = entry_kind == "symlink"
        try:
            details = path.lstat()
            is_regular_file = stat.S_ISREG(details.st_mode)
            size: int | None = details.st_size if is_regular_file else None
            device: int | None = details.st_dev if is_regular_file else None
            inode: int | None = details.st_ino if is_regular_file else None
            nlink: int | None = details.st_nlink if is_regular_file else None
        except OSError as error:
            size = None
            device = None
            inode = None
            nlink = None
            errors.append(
                {
                    "path": str(path),
                    "code": error.errno,
                    "message": error.strerror or type(error).__name__,
                }
            )

        stem = path.name[: -len(path.suffix)] if path.suffix else path.name
        subtitle = subtitle_details(stem, relative_path) if kind == "subtitle" else None
        parse_stem = stem
        if subtitle and subtitle["raw_language_tag"]:
            match = LANGUAGE_SUFFIX.search(stem)
            if match:
                parse_stem = stem[: match.start()].rstrip("._ -[")
        parsed = parse_filename(parse_stem) if kind in {"video", "subtitle", "audio"} else None
        role, role_hint = classify_role(kind, relative_path, parsed or {})

        item: dict[str, Any] = {
            "path": str(path),
            "relative_path": relative_path.as_posix(),
            "type": kind,
            "extension": extension,
            "size": size,
            "device": device,
            "inode": inode,
            "nlink": nlink,
            "is_symlink": is_symlink,
            "role": role,
            "role_hint": role_hint,
            "parsed": parsed,
        }
        if subtitle is not None:
            item["subtitle"] = subtitle
        files.append(item)

    files.sort(key=lambda item: item["relative_path"].casefold())
    return {
        "schema_version": 1,
        "root": str(root),
        "files": files,
        "errors": errors,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only inventory of anime files; emits JSON to stdout."
    )
    parser.add_argument("directory", type=Path, help="directory to inspect")
    args = parser.parse_args()

    root = args.directory.expanduser().absolute()
    if not root.exists():
        parser.error(f"directory does not exist: {root}")
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    try:
        result = inspect(root)
    except KeyboardInterrupt:
        print("scan interrupted", file=sys.stderr)
        return 130
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
