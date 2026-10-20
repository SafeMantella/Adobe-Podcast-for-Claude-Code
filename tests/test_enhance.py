import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enhance


class Pure(unittest.TestCase):
    def test_silence_midpoints(self):
        log = "silence_end: 3.000 | silence_duration: 1.000\n"
        self.assertEqual(enhance.silence_midpoints(log), [2.5])

    def test_plan_cuts_prefers_silence_and_covers_the_file(self):
        spans = enhance.plan_cuts(10, [3.5, 7.2], 4)
        self.assertEqual([(round(a, 3), round(b, 3), h) for a, b, h in spans], [
            (0.0, 3.5, False),
            (3.5, 7.2, False),
            (7.2, 10.0, False),
        ])
        self._covers(spans, 10)

    def test_plan_cuts_hard_cuts_when_no_silence_fits(self):
        spans = enhance.plan_cuts(10, [0.2], 4)
        self.assertEqual(spans[0], (0.0, 4.0, True))
        self.assertEqual(spans[1][2], True)
        self._covers(spans, 10)
        self.assertLessEqual(spans[0][1] - spans[0][0], 4)

    def test_plan_cuts_keeps_a_short_file_whole(self):
        self.assertEqual(enhance.plan_cuts(3, [1.5], 4), [(0.0, 3.0, False)])

    def test_pair_matches_closest_duration_once(self):
        pairs, missing, leftover, ambiguous = enhance.pair(
            {"a": 60.0, "b": 120.0},
            {"/d/t1": 120.1, "/d/t2": 59.9, "/d/t3": 300.0},
        )
        self.assertEqual(pairs, {"a": "/d/t2", "b": "/d/t1"})
        self.assertEqual(missing, [])
        self.assertEqual(leftover, ["/d/t3"])
        self.assertEqual(ambiguous, [])

    def test_pair_reports_missing_and_does_not_reuse(self):
        pairs, missing, _, _ = enhance.pair({"a": 60.0}, {"/d/t1": 90.0})
        self.assertEqual((pairs, missing), ({}, ["a"]))
        pairs, missing, _, _ = enhance.pair({"a": 60.0, "b": 60.2}, {"/d/t1": 60.1})
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(missing), 1)

    def test_pair_refuses_two_equally_close_files(self):
        _, _, _, ambiguous = enhance.pair({"a": 60.0}, {"/d/t1": 60.20, "/d/t2": 60.22})
        self.assertEqual(ambiguous, ["a"])

    def test_close_durations(self):
        self.assertTrue(enhance.close_durations([60.0, 60.4]))
        self.assertFalse(enhance.close_durations([60.0, 62.0]))

    def test_classify_ignores_a_cover_image(self):
        streams = [
            {"codec_type": "audio", "codec_name": "mp3"},
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ]
        audios, videos = enhance.classify(streams)
        self.assertEqual(len(audios), 1)
        self.assertEqual(videos, [])

    def test_parse_bitrate(self):
        self.assertEqual(enhance.parse_bitrate("192k"), "192k")
        self.assertEqual(enhance.parse_bitrate("128"), "128k")
        with self.assertRaises(enhance.EnhanceError):
            enhance.parse_bitrate("nope")

    def test_output_path_never_is_the_source(self):
        source = Path("/tmp/take.mp4")
        dest = enhance.output_path(source, "video", Path("/tmp/clean.mp3"))
        self.assertEqual(dest.name, "take-enhanced.mp4")
        self.assertNotEqual(dest, source)

    def test_link_skill_is_idempotent_and_does_not_replace_a_foreign_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            dest = root / "skills" / "adobe-podcast-enhance"
            self.assertEqual(enhance.link_skill(dest, src), "linked")
            self.assertEqual(enhance.link_skill(dest, src), "already")
            other = root / "other"
            other.mkdir()
            foreign = root / "foreign"
            foreign.symlink_to(other, target_is_directory=True)
            self.assertEqual(enhance.link_skill(foreign, src), "skip-foreign")

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as caught:
            enhance.main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def _covers(self, spans, duration):
        self.assertAlmostEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[-1][1], duration)
        for left, right in zip(spans, spans[1:]):
            self.assertAlmostEqual(left[1], right[0])
            self.assertGreater(left[1], left[0])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class Pipeline(unittest.TestCase):
    def test_video_roundtrip_copies_the_picture_and_leaves_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "take.mp4"
            subprocess.check_call([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=160x120:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(source),
            ])
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            out = root / "work"
            self.assertEqual(enhance.main([
                "prepare", str(source), "--out", str(out), "--max-mb", "0", "--max-seconds", "30",
            ]), 0)
            work = out / "take"
            upload = work / "upload" / "part-01.mp3"
            self.assertTrue(upload.is_file())
            download = root / "download.mp3"
            subprocess.check_call([
                "ffmpeg", "-y", "-v", "error", "-i", str(upload),
                "-c:a", "libmp3lame", "-b:a", "160k", str(download),
            ])
            empty = root / "empty-downloads"
            empty.mkdir()
            self.assertEqual(enhance.main([
                "collect", str(work), "--downloads", str(empty), "--files", str(download),
            ]), 0)
            self.assertEqual(enhance.main(["replace", str(work)]), 0)
            dest = root / "take-enhanced.mp4"
            self.assertTrue(dest.is_file())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            self.assertEqual(enhance.video_stream_md5(source), enhance.video_stream_md5(dest))
            self.assertEqual(enhance.main(["replace", str(work)]), 2)
            self.assertEqual(enhance.main(["replace", str(work), "--out-dir", str(root / "edit")]), 0)
            self.assertEqual(enhance.video_stream_md5(source), enhance.video_stream_md5(root / "edit" / "take-enhanced.mp4"))

    def test_split_on_silence_then_join_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "talk.wav"
            subprocess.check_call([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
                "-af", "volume=0:enable='between(t,2,3)+between(t,5,6)'",
                str(source),
            ])
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            out = root / "work"
            self.assertEqual(enhance.main([
                "prepare", str(source), "--out", str(out),
                "--max-mb", "0", "--max-seconds", "3",
            ]), 0)
            work = out / "talk"
            manifest = enhance.load_manifest(work)
            self.assertGreaterEqual(len(manifest["parts"]), 2)
            self.assertAlmostEqual(manifest["parts"][0]["start_s"], 0.0)
            self.assertAlmostEqual(manifest["parts"][-1]["end_s"], manifest["duration_s"], places=2)
            self.assertTrue(any(not part["hard_cut"] for part in manifest["parts"]))
            for part in manifest["parts"]:
                self.assertLessEqual(part["end_s"] - part["start_s"], 3.05)
            downloads = []
            for part in manifest["parts"]:
                upload = work / part["upload"]
                dest = root / f"dl-{part['index']:02d}.mp3"
                subprocess.check_call([
                    "ffmpeg", "-y", "-v", "error", "-i", str(upload),
                    "-c:a", "libmp3lame", "-b:a", "128k", str(dest),
                ])
                downloads.append(str(dest))
            self.assertEqual(enhance.main(["collect", str(work), "--files", *downloads]), 0)
            self.assertEqual(enhance.main(["replace", str(work)]), 0)
            self.assertTrue((root / "talk-enhanced.flac").is_file())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_collect_rejects_the_upload_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "note.wav"
            subprocess.check_call([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=330:duration=1", str(source),
            ])
            out = root / "work"
            self.assertEqual(enhance.main([
                "prepare", str(source), "--out", str(out), "--max-mb", "0",
            ]), 0)
            work = out / "note"
            upload = work / "upload" / "part-01.mp3"
            self.assertEqual(enhance.main(["collect", str(work), "--files", str(upload)]), 2)
            manifest = enhance.load_manifest(work)
            self.assertIsNone(manifest["parts"][0]["enhanced"])

    def test_prepare_rejects_video_with_no_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "silent.mp4"
            subprocess.check_call([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "color=c=black:s=160x120:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ])
            self.assertEqual(enhance.main([
                "prepare", str(source), "--out", str(root / "work"), "--max-mb", "0",
            ]), 2)


if __name__ == "__main__":
    unittest.main()
