#!/usr/bin/env python3
"""Prepare audio for Adobe Podcast Enhance, then put the cleaned audio back.

Upload and download happen in the browser. This program never logs in.

  python3 enhance.py doctor
  python3 enhance.py install
  python3 enhance.py prepare FILE [--plan free|premium] [--max-mb 9] [--bitrate 192k]
  python3 enhance.py status WORK
  python3 enhance.py collect WORK [--files DOWNLOAD ...]
  python3 enhance.py replace WORK
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skills" / "adobe-podcast-enhance"
ENHANCE_URL = "https://podcast.adobe.com/en/enhance"
FEATURES_URL = "https://podcast.adobe.com/en/features"

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT
DOWNLOAD_AUDIO_EXT = AUDIO_EXT | {""}
SKIP_DOWNLOAD_EXT = {
    ".crdownload", ".download", ".part", ".dmg", ".pkg", ".zip", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".html", ".txt", ".csv",
    ".json", ".docx", ".xlsx", ".pptx", ".app", ".exe", ".iso", ".mp4",
    ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ds_store",
}
SKIP_DOWNLOAD_NAMES = {".ds_store", ".localized"}
PLAN_MAX_SECONDS = {"free": 29 * 60, "premium": 110 * 60}
PLAN_DAILY_SECONDS = {"free": 60 * 60, "premium": 4 * 60 * 60}
CODEC_SUFFIX = {
    "mp3": ".mp3",
    "flac": ".flac",
    "aac": ".m4a",
    "vorbis": ".ogg",
    "opus": ".opus",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_f32le": ".wav",
}
SILENCE_RE = re.compile(
    r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)"
)

# Skill folders that already exist on a machine. ~/.grok is intentionally absent:
# it holds auth state, and this install must not write there.
AGENT_HOMES = (
    Path.home() / ".claude",
    Path.home() / ".agents",
    Path.home() / ".gemini",
    Path.home() / ".config" / "opencode",
    Path.home() / ".codex",
    Path.home() / ".cursor",
)


class EnhanceError(Exception):
    pass


def say(line: str) -> None:
    print(line)


def need_python() -> None:
    if sys.version_info < (3, 9):
        raise EnhanceError("Python 3.9 or newer is required")


def need_bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise EnhanceError(
            f"{name} not found. Install ffmpeg (it includes ffprobe) and run doctor again"
        )
    return found


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        shown = " ".join(shlex.quote(c) for c in cmd[:8])
        detail = "\n".join(tail)
        raise EnhanceError(f"command failed ({proc.returncode}): {shown}\n{detail}")
    return proc


def parse_bitrate(text: str) -> str:
    raw = text.strip().lower()
    if raw.endswith("k"):
        raw = raw[:-1]
    try:
        value = int(raw)
    except ValueError as exc:
        raise EnhanceError(f"bitrate must look like 192k, got {text!r}") from exc
    if not 64 <= value <= 320:
        raise EnhanceError("bitrate must be between 64k and 320k")
    return f"{value}k"


def bitrate_kbps(text: str) -> int:
    return int(parse_bitrate(text)[:-1])


def seconds_for_budget(max_bytes: int, bitrate: str) -> float:
    bytes_per_sec = bitrate_kbps(bitrate) * 1000 / 8
    return (max_bytes * 0.90) / bytes_per_sec


def silence_midpoints(log: str) -> list[float]:
    points = []
    for end, dur in SILENCE_RE.findall(log):
        mid = float(end) - float(dur) / 2
        if mid > 0:
            points.append(mid)
    return points


def plan_cuts(
    duration: float,
    silences: list[float],
    max_part_s: float,
    min_part_s: float = 0.8,
) -> list[tuple[float, float, bool]]:
    """Return (start, end, hard_cut) spans covering [0, duration].

    A hard cut is a split that did not land on a silence.
    """
    if duration <= 0:
        raise EnhanceError("duration must be positive")
    if max_part_s <= min_part_s:
        raise EnhanceError(
            f"part limit {max_part_s:.2f}s is shorter than the {min_part_s:.1f}s minimum. "
            "Raise --max-mb or lower --bitrate"
        )
    if duration <= max_part_s:
        return [(0.0, duration, False)]

    spans: list[tuple[float, float, bool]] = []
    start = 0.0
    while duration - start > max_part_s + 1e-3:
        window_end = start + max_part_s
        candidates = [s for s in silences if start + min_part_s < s <= window_end + 1e-6]
        if candidates:
            cut, hard = candidates[-1], False
        else:
            cut, hard = window_end, True
        if cut <= start + 1e-3:
            raise EnhanceError("failed to advance while splitting audio")
        spans.append((start, cut, hard))
        start = cut
    if duration - start > 1e-3:
        spans.append((start, duration, False))
    return spans


def close_durations(durations: list[float], gap: float = 1.0) -> bool:
    ordered = sorted(durations)
    return any(b - a < gap for a, b in zip(ordered, ordered[1:]))


def pair(
    needs: dict[str, float],
    cands: dict[str, float],
    tol: float = 0.5,
) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Match stem -> path by closest duration. Each path is used once.

    A stem is ambiguous when two unused paths are both within tolerance and
    almost the same distance away. Ambiguous stems are not paired.
    """
    used: set[str] = set()
    pairs: dict[str, str] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for stem, dur in sorted(needs.items(), key=lambda kv: kv[1]):
        ranked = sorted((abs(cd - dur), path) for path, cd in cands.items() if path not in used)
        if not ranked or ranked[0][0] > tol:
            missing.append(stem)
            continue
        if len(ranked) > 1 and ranked[1][0] <= tol and (ranked[1][0] - ranked[0][0]) < 0.05:
            ambiguous.append(stem)
            continue
        pairs[stem] = ranked[0][1]
        used.add(ranked[0][1])
    leftover = [path for path in cands if path not in used]
    return pairs, missing, leftover, ambiguous


def attached_pic(stream: dict) -> bool:
    disp = stream.get("disposition") or {}
    if disp.get("attached_pic") in (1, True):
        return True
    return stream.get("codec_name") in {"mjpeg", "png"}


def classify(streams: list[dict]) -> tuple[list[dict], list[dict]]:
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    videos = [
        s for s in streams
        if s.get("codec_type") == "video" and not attached_pic(s)
    ]
    return audios, videos


def probe(path: Path) -> dict:
    proc = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height:stream_disposition=attached_pic",
        "-of", "json", str(path),
    ])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise EnhanceError(f"ffprobe returned no metadata for {path}") from exc
    streams = data.get("streams") or []
    duration = (data.get("format") or {}).get("duration")
    if not duration:
        for stream in streams:
            if stream.get("duration"):
                duration = stream["duration"]
                break
    if not duration:
        raise EnhanceError(f"no duration in {path}")
    audios, videos = classify(streams)
    video = videos[0] if videos else None
    return {
        "duration": float(duration),
        "has_video": video is not None,
        "audio_count": len(audios),
        "audio_codec": audios[0].get("codec_name") if audios else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "video_codec": video.get("codec_name") if video else None,
    }


def video_stream_md5(path: Path) -> str:
    proc = run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:v:0", "-c", "copy", "-f", "md5", "-",
    ])
    line = (proc.stdout or "").strip().splitlines()
    if not line or not line[-1].startswith("MD5="):
        raise EnhanceError(f"could not hash the video stream of {path}")
    return line[-1]


def same_bytes(a: Path, b: Path) -> bool:
    if a.resolve() == b.resolve():
        return True
    sa, sb = a.stat().st_size, b.stat().st_size
    if sa != sb:
        return False
    ha, hb = hashlib.sha256(), hashlib.sha256()
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ba, bb = fa.read(1024 * 1024), fb.read(1024 * 1024)
            if not ba and not bb:
                break
            ha.update(ba)
            hb.update(bb)
    return ha.digest() == hb.digest()


def load_manifest(work: Path) -> dict:
    path = work / "manifest.json"
    if not path.is_file():
        raise EnhanceError(f"no manifest.json in {work}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(work: Path, manifest: dict) -> None:
    path = work / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_work(path: Path) -> Path:
    p = path.expanduser().resolve()
    if p.is_file() and p.name == "manifest.json":
        p = p.parent
    if not (p / "manifest.json").is_file():
        raise EnhanceError(f"no manifest.json in {p}. Pass the work directory printed by prepare")
    return p


def detect_silences(source: Path) -> list[float]:
    proc = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-i", str(source), "-vn",
            "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise EnhanceError("silence detection failed\n" + "\n".join(tail))
    return silence_midpoints(proc.stderr or "")


def encode_mp3(
    source: Path,
    dest: Path,
    bitrate: str,
    start: float | None = None,
    duration: float | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(source), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, str(dest)]
    run(cmd)


def encode_span(source: Path, dest: Path, bitrate: str, start: float, end: float, full: float) -> None:
    if start <= 0.001 and abs(end - full) <= 0.001:
        encode_mp3(source, dest, bitrate)
    else:
        encode_mp3(source, dest, bitrate, start=start, duration=end - start)


def part_limit_seconds(duration: float, plan: str, max_seconds: float, max_mb: float, bitrate: str) -> float:
    limit = min(float(max_seconds), float(PLAN_MAX_SECONDS[plan]))
    if max_mb > 0:
        by_size = seconds_for_budget(int(max_mb * 1024 * 1024), bitrate)
        if duration > by_size and by_size < 5:
            raise EnhanceError(
                f"the size cap ({max_mb} MB at {bitrate}) leaves less than 5s per part. "
                "Raise --max-mb or lower --bitrate"
            )
        limit = min(limit, by_size)
    if limit < 1:
        raise EnhanceError("each part must be at least 1s. Raise --max-seconds or --max-mb")
    return limit


def partial_path(dest: Path) -> Path:
    # Keep the real suffix. "file.mp4.partial" makes ffmpeg refuse to pick a format.
    return dest.with_name(f".{dest.stem}.partial{dest.suffix}")


def build_parts(source: Path, upload: Path, full: float, limit: float, bitrate: str, max_mb: float) -> list[dict]:
    max_b = int(max_mb * 1024 * 1024) if max_mb > 0 else None
    silences: list[float] | None = None
    current = limit
    last_error = "could not fit every part under the size cap"
    for _attempt in range(4):
        for old in upload.glob("*.mp3"):
            old.unlink()
        if full <= current + 1e-3:
            spans = [(0.0, full, False)]
        else:
            if silences is None:
                silences = detect_silences(source)
            spans = plan_cuts(full, silences, current)
        parts = []
        for index, (start, end, hard) in enumerate(spans, start=1):
            dest = upload / f"part-{index:02d}.mp3"
            encode_span(source, dest, bitrate, start, end, full)
            info = probe(dest)
            parts.append({
                "index": index,
                "upload": f"upload/{dest.name}",
                "start_s": round(start, 3),
                "end_s": round(end, 3),
                "duration_s": round(info["duration"], 3),
                "bytes": dest.stat().st_size,
                "hard_cut": hard,
                "enhanced": None,
            })
        too_big = [p for p in parts if max_b and p["bytes"] > max_b]
        if not too_big:
            return parts
        worst = max(too_big, key=lambda p: p["bytes"] / max(p["duration_s"], 0.1))
        measured = worst["duration_s"] * (max_b / worst["bytes"]) * 0.90
        nxt = min(current * 0.8, measured)
        if nxt >= current - 0.05 or nxt < 5:
            last_error = (
                f"{worst['upload']} is {worst['bytes'] / 1e6:.1f} MB, over the {max_mb} MB cap, "
                "and splitting it further would make parts shorter than 5s"
            )
            break
        current = nxt
    raise EnhanceError(last_error)


def iter_inputs(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        if not path.exists():
            raise EnhanceError(f"not found: {path}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if not child.is_file() or child.name == "enhanced":
                    continue
                if child.suffix.lower() in MEDIA_EXT:
                    found.append(child)
        else:
            found.append(path)
    if not found:
        raise EnhanceError("no audio or video files to prepare")
    return found


def work_root_for(source: Path, out: Path | None) -> Path:
    if out:
        return out.expanduser().resolve()
    return source.parent / "enhanced"


def prepare_one(source: Path, out: Path | None, plan: str, max_seconds: float, max_mb: float, bitrate: str) -> dict:
    info = probe(source)
    if info["audio_count"] < 1:
        raise EnhanceError(f"no audio stream in {source}")
    root = work_root_for(source, out)
    work = root / source.stem
    manifest_path = work / "manifest.json"
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        prev_source = previous.get("source")
        if prev_source and Path(prev_source).resolve() != source.resolve():
            digest = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:6]
            work = root / f"{source.stem}-{digest}"
    previous_output = None
    if (work / "manifest.json").is_file():
        previous_output = json.loads((work / "manifest.json").read_text(encoding="utf-8")).get("output")
    upload = work / "upload"
    clean = work / "clean"
    upload.mkdir(parents=True, exist_ok=True)
    if clean.exists():
        shutil.rmtree(clean)
    limit = part_limit_seconds(info["duration"], plan, max_seconds, max_mb, bitrate)
    parts = build_parts(source, upload, info["duration"], limit, bitrate, max_mb)
    manifest = {
        "version": 1,
        "source": str(source.resolve()),
        "kind": "video" if info["has_video"] else "audio",
        "duration_s": round(info["duration"], 3),
        "audio_streams": info["audio_count"],
        "video_codec": info["video_codec"],
        "width": info["width"],
        "height": info["height"],
        "bitrate": bitrate,
        "plan": plan,
        "max_mb": max_mb,
        "prepared_at": time.time(),
        "parts": parts,
        "clean_audio": None,
        "output": None,
        "previous_output": previous_output,
    }
    save_manifest(work, manifest)
    return manifest | {"work": str(work)}


def print_prepare_report(manifests: list[dict]) -> None:
    total = 0.0
    for manifest in manifests:
        total += manifest["duration_s"]
        hard = sum(1 for p in manifest["parts"] if p["hard_cut"])
        say(f"prepared {manifest['source']}")
        say(f"work: {manifest['work']}")
        say(f"kind: {manifest['kind']}  duration: {manifest['duration_s']:.1f}s  parts: {len(manifest['parts'])}")
        if manifest["audio_streams"] > 1:
            say(
                f"warning: {manifest['audio_streams']} audio streams. "
                "replace keeps every non-audio stream and uses one enhanced track from the first"
            )
        if hard:
            indexes = ", ".join(f"part-{p['index']:02d}" for p in manifest["parts"] if p["hard_cut"])
            say(f"warning: {indexes} cut through a word. Listen at that point")
        if manifest.get("previous_output") and Path(manifest["previous_output"]).exists():
            say(f"note: previous output is still on disk: {manifest['previous_output']}")
    plan = manifests[0]["plan"]
    if total > PLAN_DAILY_SECONDS[plan]:
        hours = PLAN_DAILY_SECONDS[plan] / 3600
        say(
            f"warning: this batch is {total / 60:.0f} min. "
            f"The {plan} plan published on {FEATURES_URL} allows about {hours:.0f}h of Enhance per day"
        )
    say(f"upload each file to {ENHANCE_URL}")
    say("one file at a time. Wait for that file's Download before sending the next")
    for manifest in manifests:
        for part in manifest["parts"]:
            path = str(Path(manifest["work"]) / part["upload"])
            say(f"  {path}  {part['bytes'] / 1e6:.1f} MB  {part['duration_s']:.1f}s")
        quoted_work = shlex.quote(manifest["work"])
        tool = shlex.quote(str(ROOT / "enhance.py"))
        say(f"then: python3 {tool} collect {quoted_work} --files <download>")
        say(f"then: python3 {tool} replace {quoted_work}")


def audio_suffix(path: Path, info: dict) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        return ext
    return CODEC_SUFFIX.get(info.get("audio_codec") or "", ".mp3")


def iter_download_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    found = []
    for child in directory.iterdir():
        if not child.is_file():
            continue
        if child.name.lower() in SKIP_DOWNLOAD_NAMES:
            continue
        ext = child.suffix.lower()
        if ext in SKIP_DOWNLOAD_EXT:
            continue
        if ext not in DOWNLOAD_AUDIO_EXT and not child.name.startswith("."):
            continue
        if child.suffix.lower() in {".crdownload", ".download", ".part"}:
            continue
        found.append(child)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:80]


def exclude_paths(manifest: dict, work: Path) -> set[Path]:
    banned = {Path(manifest["source"]).resolve()}
    for part in manifest["parts"]:
        banned.add((work / part["upload"]).resolve())
        if part.get("enhanced"):
            banned.add((work / part["enhanced"]).resolve())
    if manifest.get("clean_audio"):
        banned.add((work / manifest["clean_audio"]).resolve())
    if manifest.get("output"):
        banned.add(Path(manifest["output"]).resolve())
    return banned


def scan_candidates(manifest: dict, work: Path, downloads: Path, any_age: bool) -> dict[str, float]:
    not_before = 0.0 if any_age else float(manifest["prepared_at"]) - 30
    banned = exclude_paths(manifest, work)
    dirs = []
    for directory in (downloads, work, Path(manifest["source"]).parent):
        resolved = directory.expanduser().resolve()
        if resolved not in dirs:
            dirs.append(resolved)
    cands: dict[str, float] = {}
    for directory in dirs:
        for path in iter_download_files(directory):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in banned or path.stat().st_mtime < not_before:
                continue
            if path.stat().st_size > 600 * 1024 * 1024:
                continue
            try:
                info = probe(path)
            except EnhanceError:
                continue
            if info["audio_count"] < 1 or info["has_video"]:
                continue
            cands[str(resolved)] = info["duration"]
    return cands


def pending_parts(manifest: dict, work: Path) -> list[dict]:
    pending = []
    for part in manifest["parts"]:
        enhanced = part.get("enhanced")
        if enhanced and (work / enhanced).is_file():
            continue
        part["enhanced"] = None
        pending.append(part)
    return pending


def assign_in_order(parts: list[dict], files: list[Path], tol: float) -> list[tuple[dict, Path]] | None:
    if len(files) != len(parts):
        return None
    ordered = sorted(parts, key=lambda p: p["index"])
    paired = []
    for part, path in zip(ordered, files):
        info = probe(path)
        if info["audio_count"] < 1:
            return None
        if abs(info["duration"] - part["duration_s"]) > tol:
            return None
        paired.append((part, path))
    return paired


def join_audio(paths: list[Path], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        if dest.resolve() != paths[0].resolve():
            shutil.copy2(paths[0], dest)
        return
    cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error"]
    for path in paths:
        cmd += ["-i", str(path)]
    inputs = "".join(f"[{i}:a]" for i in range(len(paths)))
    cmd += [
        "-filter_complex", f"{inputs}concat=n={len(paths)}:v=0:a=1[a]",
        "-map", "[a]", "-c:a", "flac", str(dest),
    ]
    run(cmd)


def collect(work: Path, downloads: Path, files: list[Path] | None, tol: float, any_age: bool) -> int:
    manifest = load_manifest(work)
    pending = pending_parts(manifest, work)
    if not pending:
        say("collect: every part already has a download")
    else:
        ordered = None
        if files:
            resolved = [p.expanduser().resolve() for p in files]
            for path in resolved:
                if not path.is_file():
                    raise EnhanceError(f"not found: {path}")
            ordered = assign_in_order(pending, resolved, tol)
            if ordered is None:
                if len(pending) > 1 and close_durations([p["duration_s"] for p in pending]):
                    raise EnhanceError(
                        "these parts are within 1s of each other and --files did not line up. "
                        "Pass the downloads in part order: part-01, part-02, ..."
                    )
                cands = {}
                for path in resolved:
                    info = probe(path)
                    if info["audio_count"] < 1:
                        raise EnhanceError(f"no audio in {path}")
                    cands[str(path)] = info["duration"]
            else:
                cands = {}
                say("collect: matched --files to parts in the order given")
        else:
            if len(pending) > 1 and close_durations([p["duration_s"] for p in pending]):
                raise EnhanceError(
                    "part durations are within 1s of each other. "
                    "Pass --files in part order (part-01, part-02, ...). Duration matching could swap them"
                )
            cands = scan_candidates(manifest, work, downloads, any_age)
            if not cands:
                raise EnhanceError(
                    f"no new audio in {downloads}. Download from {ENHANCE_URL} "
                    "and pass the files with --files"
                )

        if ordered is None:
            needs = {f"{p['index']:02d}": p["duration_s"] for p in pending}
            blocked: set[str] = set()
            uploads = {f"{p['index']:02d}": work / p["upload"] for p in pending}
            while True:
                available = {path: dur for path, dur in cands.items() if path not in blocked}
                pairs, _missing, _leftover, ambiguous = pair(needs, available, tol)
                identical = [
                    path for stem, path in pairs.items()
                    if same_bytes(Path(path), uploads[stem])
                ]
                if not identical:
                    break
                for path in identical:
                    say(f"ignored {path}: byte-identical to the upload")
                blocked.update(identical)
            if ambiguous:
                say("warning: two downloads are equally close in duration. Those parts were left unmatched")
            by_index = {p["index"]: p for p in pending}
            chosen = [(by_index[int(stem)], Path(path)) for stem, path in pairs.items()]
            ordered = chosen
            matched_index = {p["index"] for p, _path in ordered}
            for part in pending:
                if part["index"] not in matched_index:
                    say(f"no match for part-{part['index']:02d} ({part['duration_s']:.1f}s)")

        clean = work / "clean"
        clean.mkdir(parents=True, exist_ok=True)
        for part, src in ordered or []:
            info = probe(src)
            if same_bytes(src, work / part["upload"]):
                say(f"part-{part['index']:02d}: skipped a file that is byte-identical to the upload")
                continue
            dest = clean / f"part-{part['index']:02d}{audio_suffix(src, info)}"
            if dest.resolve() != src.resolve():
                shutil.copy2(src, dest)
            part["enhanced"] = str(dest.relative_to(work))
            say(f"part-{part['index']:02d}: {src} -> {dest}")

    ready = []
    for part in sorted(manifest["parts"], key=lambda p: p["index"]):
        rel = part.get("enhanced")
        if not rel or not (work / rel).is_file():
            ready = []
            break
        ready.append(work / rel)
    if len(ready) != len(manifest["parts"]):
        save_manifest(work, manifest)
        missing = [p for p in manifest["parts"] if not p.get("enhanced")]
        say(f"matched {len(manifest['parts']) - len(missing)}/{len(manifest['parts'])}")
        return 2

    if len(ready) == 1:
        manifest["clean_audio"] = str(ready[0].relative_to(work))
    else:
        joined = work / "clean" / "joined.flac"
        join_audio(ready, joined)
        manifest["clean_audio"] = "clean/joined.flac"
    clean_path = work / manifest["clean_audio"]
    clean_dur = probe(clean_path)["duration"]
    slack = 0.5 * len(manifest["parts"])
    if abs(clean_dur - manifest["duration_s"]) > slack:
        save_manifest(work, manifest)
        raise EnhanceError(
            f"cleaned audio is {clean_dur:.2f}s and the source is {manifest['duration_s']:.2f}s "
            f"(allowed gap {slack:.2f}s). The picture was not touched"
        )
    save_manifest(work, manifest)
    say(f"clean: {clean_path}  {clean_dur:.2f}s")
    say(f"next: python3 {shlex.quote(str(ROOT / 'enhance.py'))} replace {shlex.quote(str(work))}")
    return 0


def output_path(source: Path, kind: str, clean: Path) -> Path:
    if kind == "video":
        suffix = source.suffix if source.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv"} else ".mp4"
    else:
        suffix = clean.suffix if clean.suffix else ".mp3"
    return source.with_name(source.stem + "-enhanced" + suffix)


def replace(work: Path, aac_bitrate: str, force: bool) -> int:
    aac_bitrate = parse_bitrate(aac_bitrate)
    manifest = load_manifest(work)
    if not manifest.get("clean_audio"):
        raise EnhanceError("no cleaned audio yet. Run collect first")
    source = Path(manifest["source"])
    clean = work / manifest["clean_audio"]
    if not source.is_file():
        raise EnhanceError(f"source is gone: {source}")
    if not clean.is_file():
        raise EnhanceError(f"cleaned audio is gone: {clean}")
    dest = output_path(source, manifest["kind"], clean)
    if dest.resolve() == source.resolve():
        raise EnhanceError("refusing to overwrite the source")
    if dest.exists() and not force:
        raise EnhanceError(f"output already exists: {dest}. Pass --force to replace that file")

    if manifest["kind"] == "audio":
        partial = partial_path(dest)
        shutil.copy2(clean, partial)
        os.replace(partial, dest)
        out_dur = probe(dest)["duration"]
        slack = 0.5 * len(manifest["parts"])
        if abs(out_dur - manifest["duration_s"]) > slack:
            dest.unlink(missing_ok=True)
            raise EnhanceError(
                f"output duration {out_dur:.2f}s does not match source {manifest['duration_s']:.2f}s"
            )
        manifest["output"] = str(dest)
        save_manifest(work, manifest)
        say(f"duration: source {manifest['duration_s']:.3f}s  output {out_dur:.3f}s")
        say(f"output: {dest}")
        return 0

    before = video_stream_md5(source)
    partial = partial_path(dest)
    if partial.exists():
        partial.unlink()
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-v", "error",
        "-i", str(source), "-i", str(clean),
        "-map", "0", "-map", "-0:a", "-map", "1:a:0",
        "-c", "copy", "-c:a", "aac", "-b:a", aac_bitrate,
        "-map_metadata", "0", "-map_chapters", "0",
    ]
    if dest.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(partial))
    try:
        run(cmd)
        after = video_stream_md5(partial)
        if after != before:
            partial.unlink(missing_ok=True)
            raise EnhanceError("video stream changed. The original file was left untouched")
        out_info = probe(partial)
        slack = 0.5 * len(manifest["parts"])
        if abs(out_info["duration"] - manifest["duration_s"]) > slack:
            partial.unlink(missing_ok=True)
            raise EnhanceError(
                f"output duration {out_info['duration']:.2f}s does not match source "
                f"{manifest['duration_s']:.2f}s. The original file was left untouched"
            )
        os.replace(partial, dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    manifest["output"] = str(dest)
    save_manifest(work, manifest)
    say(f"duration: source {manifest['duration_s']:.3f}s  output {out_info['duration']:.3f}s")
    say(f"video: {out_info['video_codec']} {out_info['width']}x{out_info['height']}")
    say("video-stream: copied")
    say(f"audio: aac {aac_bitrate}")
    say(f"output: {dest}")
    return 0


def status(work: Path) -> int:
    manifest = load_manifest(work)
    say(f"source: {manifest['source']}")
    say(f"kind: {manifest['kind']}  duration: {manifest['duration_s']:.1f}s  plan: {manifest['plan']}")
    for part in manifest["parts"]:
        state = "downloaded" if part.get("enhanced") else "needs download"
        extra = "  hard-cut" if part.get("hard_cut") else ""
        say(
            f"part-{part['index']:02d}: {state}  {part['duration_s']:.1f}s  "
            f"{part['bytes'] / 1e6:.1f} MB{extra}  {work / part['upload']}"
        )
    say(f"clean: {work / manifest['clean_audio'] if manifest.get('clean_audio') else 'missing'}")
    say(f"output: {manifest.get('output') or 'missing'}")
    return 0


def link_skill(dest: Path, src: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        try:
            current = dest.resolve()
        except OSError:
            current = None
        if current == src.resolve():
            return "already"
        if current is None or not dest.exists():
            dest.unlink()
        else:
            return "skip-foreign"
    elif dest.exists():
        return "skip-exists"
    try:
        dest.symlink_to(src, target_is_directory=True)
    except OSError as exc:
        return f"failed: {exc}"
    return "linked"


def install() -> int:
    if not (SKILL_DIR / "SKILL.md").is_file():
        raise EnhanceError(f"skill is missing at {SKILL_DIR}")
    homes = [h for h in AGENT_HOMES if h.is_dir()]
    if not homes:
        say("no agent home found. Open this folder in the agent so it reads AGENTS.md")
        say(f"skill: {SKILL_DIR}")
        return 0
    for home in homes:
        result = link_skill(home / "skills" / "adobe-podcast-enhance", SKILL_DIR)
        say(f"{result}: {home / 'skills' / 'adobe-podcast-enhance'}")
    say("some agents read more than one of these folders and may list the skill twice. It is the same skill")
    say("this install does not write to ~/.grok")
    return 0


def doctor() -> int:
    need_python()
    say(f"python: {sys.version.split()[0]}")
    say(f"repo: {ROOT}")
    say(f"skill: {SKILL_DIR}")
    missing = False
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name)
        say(f"{name}: {found or 'MISSING'}")
        missing = missing or not found
    homes = [h for h in AGENT_HOMES if h.is_dir()]
    if not homes:
        say("agent homes: none detected. AGENTS.md in this repo is the entry point")
    for home in homes:
        link = home / "skills" / "adobe-podcast-enhance"
        if link.is_symlink() and link.resolve() == SKILL_DIR.resolve():
            say(f"link: {link}")
        elif link.exists():
            say(f"link: {link} (points somewhere else)")
        else:
            say(f"link: {link} (not installed)")
    say(f"adobe: {ENHANCE_URL}")
    say(f"limits published at {FEATURES_URL} (checked 2026-10-08)")
    return 2 if missing else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check python, ffmpeg, and skill links")
    sub.add_parser("install", help="link the skill into agent homes that already exist")

    prep = sub.add_parser("prepare", help="extract uploadable mp3 parts and write a manifest")
    prep.add_argument("inputs", nargs="+", type=Path)
    prep.add_argument("--out", type=Path, help="directory that will contain one work folder per file")
    prep.add_argument("--plan", choices=("free", "premium"), default="free")
    prep.add_argument("--max-seconds", type=float, default=None)
    prep.add_argument("--max-mb", type=float, default=9, help="split above this size. 0 disables the size split")
    prep.add_argument("--bitrate", default="192k")

    sub.add_parser("status", help="show which parts are still waiting for a download").add_argument("work", type=Path)

    collect_cmd = sub.add_parser("collect", help="match downloaded audio to parts and join them")
    collect_cmd.add_argument("work", type=Path)
    collect_cmd.add_argument(
        "--downloads", type=Path,
        default=os.environ.get("ADOBE_DOWNLOADS", str(Path.home() / "Downloads")),
    )
    collect_cmd.add_argument("--files", nargs="+", type=Path)
    collect_cmd.add_argument("--tolerance", type=float, default=0.5)
    collect_cmd.add_argument("--any-age", action="store_true", help="also consider downloads older than prepare")

    replace_cmd = sub.add_parser("replace", help="write the cleaned file beside the source")
    replace_cmd.add_argument("work", type=Path)
    replace_cmd.add_argument("--aac-bitrate", default="320k")
    replace_cmd.add_argument("--force", action="store_true")
    return parser


def dispatch(argv: list[str]) -> int:
    need_python()
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 2
    args = parser.parse_args(argv)
    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "install":
        return install()
    if args.cmd == "prepare":
        need_bin("ffmpeg")
        need_bin("ffprobe")
        bitrate = parse_bitrate(args.bitrate)
        max_seconds = args.max_seconds if args.max_seconds is not None else PLAN_MAX_SECONDS[args.plan]
        if args.max_mb < 0:
            raise EnhanceError("--max-mb must be 0 or greater")
        if max_seconds <= 0:
            raise EnhanceError("--max-seconds must be positive")
        manifests = []
        for source in iter_inputs(list(args.inputs)):
            manifests.append(prepare_one(source, args.out, args.plan, max_seconds, args.max_mb, bitrate))
        print_prepare_report(manifests)
        return 0
    if args.cmd == "status":
        return status(resolve_work(args.work))
    if args.cmd == "collect":
        need_bin("ffmpeg")
        need_bin("ffprobe")
        return collect(
            resolve_work(args.work),
            args.downloads.expanduser(),
            list(args.files) if args.files else None,
            args.tolerance,
            args.any_age,
        )
    if args.cmd == "replace":
        need_bin("ffmpeg")
        need_bin("ffprobe")
        return replace(resolve_work(args.work), args.aac_bitrate, args.force)
    raise EnhanceError(f"unknown command {args.cmd}")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        return dispatch(argv)
    except EnhanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
