import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.inspect_anime import inspect, subtitle_details


class InspectAnimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def touch(self, relative_path: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        return path

    def scan_without_optional_parser(self) -> dict:
        with patch.dict(sys.modules, {"anitopy": None}):
            return inspect(self.root)

    def by_path(self, result: dict) -> dict[str, dict]:
        return {item["relative_path"]: item for item in result["files"]}

    def test_common_episode_and_extra_hints(self) -> None:
        extras = {
            "SP01.mkv": "sp",
            "NCOP01.mkv": "ncop",
            "NCED01.mkv": "nced",
            "OVA01.mkv": "ova",
            "OAD01.mkv": "oad",
            "Movie.mkv": "movie",
        }
        for filename in ("01.mkv", "01v2.mkv", "12.5.mkv", *extras):
            self.touch(filename)

        files = self.by_path(self.scan_without_optional_parser())
        for filename, episode in (("01.mkv", "01"), ("01v2.mkv", "01v2"), ("12.5.mkv", "12.5")):
            with self.subTest(filename=filename):
                self.assertEqual(files[filename]["parsed"]["episode"], episode)
                self.assertEqual(files[filename]["role"], "episode-candidate")

        for filename, role_hint in extras.items():
            with self.subTest(extra=filename):
                self.assertEqual(files[filename]["role"], "extra")
                self.assertEqual(files[filename]["role_hint"], role_hint)

    def test_subtitle_formats_languages_and_companion_keys(self) -> None:
        for filename in ("01.chs.ass", "01.cht.ass", "01.sub", "01.idx", "01.sup"):
            self.touch(filename)

        files = self.by_path(self.scan_without_optional_parser())
        self.assertEqual(files["01.chs.ass"]["subtitle"]["language_tag"], "zh-Hans")
        self.assertEqual(files["01.cht.ass"]["subtitle"]["language_tag"], "zh-Hant")
        self.assertEqual(files["01.sub"]["type"], "subtitle")
        self.assertEqual(files["01.idx"]["type"], "subtitle")
        self.assertEqual(files["01.sup"]["type"], "subtitle")
        self.assertEqual(files["01.sub"]["subtitle"]["match_key"], "01")
        self.assertEqual(files["01.idx"]["subtitle"]["match_key"], "01")

    def test_hardlink_identity_is_reported(self) -> None:
        if not hasattr(os, "link"):
            self.skipTest("hard links are not available")

        original = self.touch("original.mkv")
        hardlink = self.root / "hardlink.mkv"
        separate_copy = self.touch("separate-copy.mkv")
        try:
            os.link(original, hardlink)
        except OSError as error:
            self.skipTest(f"hard links unavailable on this filesystem: {error}")

        files = self.by_path(self.scan_without_optional_parser())
        first = files["original.mkv"]
        linked = files["hardlink.mkv"]
        copied = files["separate-copy.mkv"]
        self.assertEqual(first["device"], linked["device"])
        self.assertEqual(first["inode"], linked["inode"])
        self.assertEqual(first["nlink"], 2)
        self.assertEqual(linked["nlink"], 2)
        self.assertEqual(copied["nlink"], 1)
        self.assertNotEqual(first["inode"], copied["inode"])

    def test_opaque_names_round_trip_through_json(self) -> None:
        filename = "[Group] (A) 'single' \"double\" & $HOME ! `tick` - 空格\nnewline.mkv"
        self.touch(filename)

        result = self.scan_without_optional_parser()
        decoded = json.loads(json.dumps(result, ensure_ascii=False))
        files = self.by_path(decoded)
        self.assertIn(filename, files)
        self.assertEqual(files[filename]["type"], "video")

    def test_extended_subtitle_language_aliases(self) -> None:
        aliases = {
            "GB": ["zh-Hans"],
            "BIG5": ["zh-Hant"],
            "简": ["zh-Hans"],
            "繁": ["zh-Hant"],
            "简日": ["zh-Hans", "ja"],
            "繁日": ["zh-Hant", "ja"],
            "CHS&JPN": ["zh-Hans", "ja"],
            "CHT&JPN": ["zh-Hant", "ja"],
            "JPSC": ["ja", "zh-Hans"],
            "JPTC": ["ja", "zh-Hant"],
            "CHS_JP": ["zh-Hans", "ja"],
        }
        for alias, expected in aliases.items():
            with self.subTest(alias=alias):
                details = subtitle_details(f"01.{alias}", Path(f"01.{alias}.ass"))
                self.assertEqual(details["raw_language_tag"], alias)
                self.assertEqual(details["language_tags"], expected)
                self.assertEqual(details["match_key"], "01")

    def test_fonts_unknown_extension_and_audio_track(self) -> None:
        self.touch("Fonts/subtitle.ttf")
        self.touch("unknown.bin")
        self.touch("01 - Track Name.flac")

        files = self.by_path(self.scan_without_optional_parser())
        self.assertEqual(files["Fonts/subtitle.ttf"]["type"], "font")
        self.assertEqual(files["Fonts/subtitle.ttf"]["role_hint"], "font")
        self.assertEqual(files["unknown.bin"]["type"], "unknown")
        self.assertEqual(files["01 - Track Name.flac"]["type"], "audio")
        self.assertEqual(files["01 - Track Name.flac"]["role"], "unknown")

    def test_symlinks_are_reported_but_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)
            (outside / "not-scanned.mkv").write_bytes(b"outside")
            (self.root / "linked-directory").symlink_to(outside, target_is_directory=True)

            files = self.by_path(self.scan_without_optional_parser())

        self.assertIn("linked-directory", files)
        self.assertEqual(files["linked-directory"]["type"], "symlink")
        self.assertTrue(files["linked-directory"]["is_symlink"])
        self.assertNotIn("linked-directory/not-scanned.mkv", files)

    def test_fifo_and_unix_socket_are_reported_as_special(self) -> None:
        if not hasattr(os, "mkfifo") or not hasattr(socket, "AF_UNIX"):
            self.skipTest("FIFO and Unix-domain sockets are not available")

        os.mkfifo(self.root / "queue")
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(self.root / "local.sock"))
        try:
            files = self.by_path(self.scan_without_optional_parser())
        finally:
            listener.close()

        for path in ("queue", "local.sock"):
            with self.subTest(path=path):
                self.assertEqual(files[path]["type"], "special")
                self.assertEqual(files[path]["role"], "unknown")
                self.assertIsNone(files[path]["size"])


if __name__ == "__main__":
    unittest.main()
