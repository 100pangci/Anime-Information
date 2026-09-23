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
SUBTITLE_EXTENSIONS = {".ass", ".idx", ".smi", ".srt", ".ssa", ".sub", ".vtt"}
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
DISC_EXTENSIONS = {".bin", ".cue", ".dvd", ".iso", ".mdf", ".nrg"}

EXTRA_TOKEN = re.compile(
    r"(?i)(?<![a-z0-9])(?:ncop|nced|ova|oad|pv|cm|cd)(?:\s*\d+)?(?![a-z0-9])|"
    r"(?<![a-z0-9])(?:extras?|movie|bonus|interview|menu|ost|soundtrack|"
    r"scans?)(?![a-z0-9])"
)
SPECIAL_TOKEN = re.compile(r"(?i)(?<![a-z0-9])(?:sp\s*\d*|special)(?![a-z0-9])")
LANGUAGE_SUFFIX = re.compile(
    r"(?i)(?:[._\s-]+|\[)(chs|cht|sc|tc|zh[-_]?(?:hans|hant|cn|tw)|"
    r"jpn?|en|eng)(?:\])?$"
)
LANGUAGE_NAMES = {
    "chs": "zh-Hans",
    "sc": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh_hans": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh_cn": "zh-Hans",
    "cht": "zh-Hant",
    "tc": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh_hant": "zh-Hant",
    "zh-tw": "zh-Hant",
    "zh_tw": "zh-Hant",
    "jp": "ja",
    "jpn": "ja",
    "en": "en",
    "eng": "en",
}


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


def subtitle_details(stem: str, relative_path: Path) -> dict[str, str | None]:
    match = LANGUAGE_SUFFIX.search(stem)
    language_tag: str | None = None
    base_stem = stem
    if match:
        raw_tag = match.group(1).replace("_", "-").casefold()
        language_tag = LANGUAGE_NAMES.get(raw_tag)
        base_stem = stem[: match.start()].rstrip("._ -[")

    parent = relative_path.parent.as_posix()
    match_key = f"{parent}/{base_stem}" if parent != "." else base_stem
    return {
        "language_tag": language_tag,
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


def classify_role(kind: str, relative_path: Path, parsed: dict[str, Any]) -> str:
    path_text = "/".join(relative_path.parts)
    if kind == "font" or any(part.casefold() == "fonts" for part in relative_path.parts):
        return "font"
    if any(part.casefold() in {"scans", "scan", "booklet"} for part in relative_path.parts):
        return "scan"
    if EXTRA_TOKEN.search(path_text) or SPECIAL_TOKEN.search(path_text):
        return "extra"
    if kind in {"video", "subtitle", "audio"} and parsed.get("episode"):
        return "episode-candidate"
    return "unknown"


def walk_entries(
    root: Path,
    errors: list[dict[str, Any]],
    skipped: list[dict[str, str]],
) -> Iterator[tuple[Path, bool]]:
    """Yield files and symlinks without following symlinks or descending mounts."""
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
                    yield path, True
                elif entry.is_dir(follow_symlinks=False):
                    if entry.stat(follow_symlinks=False).st_dev == root_device:
                        subdirectories.append(path)
                    else:
                        skipped.append({"path": str(path), "reason": "mount-boundary"})
                elif entry.is_file(follow_symlinks=False):
                    yield path, False
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
    for path, is_symlink in walk_entries(root, errors, skipped):
        relative_path = path.relative_to(root)
        extension = path.suffix.casefold()
        kind = "symlink" if is_symlink else classify_extension(extension)
        try:
            details = path.lstat()
            size: int | None = details.st_size if stat.S_ISREG(details.st_mode) else None
        except OSError as error:
            size = None
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
        if subtitle and subtitle["language_tag"]:
            match = LANGUAGE_SUFFIX.search(stem)
            if match:
                parse_stem = stem[: match.start()].rstrip("._ -[")
        parsed = parse_filename(parse_stem) if kind in {"video", "subtitle", "audio"} else None
        role = classify_role(kind, relative_path, parsed or {})

        item: dict[str, Any] = {
            "path": str(path),
            "relative_path": relative_path.as_posix(),
            "type": kind,
            "extension": extension,
            "size": size,
            "is_symlink": is_symlink,
            "role": role,
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
