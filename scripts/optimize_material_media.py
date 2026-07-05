#!/usr/bin/env python3
"""Optimize formatted material-pack media files."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKS = ROOT / "materials" / "private" / "packs"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".ogg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


class AudioJob(NamedTuple):
    source: Path
    target: Path


class VideoJob(NamedTuple):
    source: Path


def m4a_target(source: Path) -> Path:
    target = source.with_suffix(".m4a")
    if target == source:
        return source
    if not target.exists():
        return target
    index = 2
    while True:
        candidate = source.with_name(f"{source.stem}-{index}.m4a")
        if not candidate.exists():
            return candidate
        index += 1


def audio_jobs(packs_root: Path) -> list[AudioJob]:
    jobs: list[AudioJob] = []
    for source in sorted(packs_root.glob("*/audio/*")):
        if source.is_file() and source.suffix.lower() in AUDIO_EXTENSIONS:
            jobs.append(AudioJob(source=source, target=m4a_target(source)))
    return jobs


def video_jobs(packs_root: Path) -> list[VideoJob]:
    jobs: list[VideoJob] = []
    for source in sorted(packs_root.glob("*/video/*")):
        if source.is_file() and source.suffix.lower() in VIDEO_EXTENSIONS:
            jobs.append(VideoJob(source=source))
    return jobs


def rewrite_paths(value: object, path_map: dict[str, str]) -> None:
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str) and path in path_map:
            value["path"] = path_map[path]
        for child in value.values():
            rewrite_paths(child, path_map)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str) and child in path_map:
                value[index] = path_map[child]
            else:
                rewrite_paths(child, path_map)


def rewrite_pack_json(pack: Path, path_map: dict[str, str]) -> None:
    questions_path = pack / "questions.json"
    if questions_path.exists():
        data = json.loads(questions_path.read_text(encoding="utf-8"))
        rewrite_paths(data, path_map)
        questions_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    material_path = pack / "material.json"
    if material_path.exists():
        material = json.loads(material_path.read_text(encoding="utf-8"))
        files = material.get("files") or {}
        if isinstance(files.get("audio"), list):
            files["audio"] = [path_map.get(path, path) for path in files["audio"]]
        material_path.write_text(json.dumps(material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def convert_audio(job: AudioJob, bitrate: str, sample_rate: int) -> None:
    temp = job.target.with_name(f"{job.target.stem}.tmp{job.target.suffix}")
    if temp.exists():
        temp.unlink()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(job.source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "aac",
            "-b:a",
            bitrate,
            str(temp),
        ],
        check=True,
    )
    temp.replace(job.target)


def convert_video(job: VideoJob, crf: int, max_height: int, audio_bitrate: str) -> int:
    temp = job.source.with_name(f"{job.source.stem}.tmp{job.source.suffix}")
    if temp.exists():
        temp.unlink()
    scale_filter = f"scale='if(gt(ih,{max_height}),-2,iw)':'if(gt(ih,{max_height}),{max_height},ih)'"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(job.source),
            "-vf",
            scale_filter,
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            str(temp),
        ],
        check=True,
    )
    converted_size = temp.stat().st_size
    temp.replace(job.source)
    return converted_size


def optimize_audio(packs_root: Path, apply: bool, bitrate: str, sample_rate: int) -> tuple[int, int, int]:
    jobs = audio_jobs(packs_root)
    original_bytes = sum(job.source.stat().st_size for job in jobs)
    if not apply:
        return len(jobs), original_bytes, 0

    converted_bytes = 0
    by_pack: dict[Path, dict[str, str]] = {}
    for index, job in enumerate(jobs, 1):
        print(f"[{index}/{len(jobs)}] {job.source.relative_to(packs_root)} -> {job.target.name}", flush=True)
        convert_audio(job, bitrate, sample_rate)
        converted_bytes += job.target.stat().st_size
        pack = job.source.parents[1]
        by_pack.setdefault(pack, {})[job.source.relative_to(pack).as_posix()] = job.target.relative_to(pack).as_posix()
        job.source.unlink()

    for pack, path_map in by_pack.items():
        rewrite_pack_json(pack, path_map)
    return len(jobs), original_bytes, converted_bytes


def optimize_video(packs_root: Path, apply: bool, crf: int, max_height: int, audio_bitrate: str) -> tuple[int, int, int]:
    jobs = video_jobs(packs_root)
    original_bytes = sum(job.source.stat().st_size for job in jobs)
    if not apply:
        return len(jobs), original_bytes, 0

    converted_bytes = 0
    for index, job in enumerate(jobs, 1):
        print(f"[{index}/{len(jobs)}] {job.source.relative_to(packs_root)}", flush=True)
        converted_bytes += convert_video(job, crf, max_height, audio_bitrate)
    return len(jobs), original_bytes, converted_bytes


def print_summary(label: str, count: int, original: int, converted: int, apply: bool) -> None:
    if apply:
        saved = original - converted
        print(f"Optimized {count} {label} file(s)")
        print(f"Before: {original / 1024 / 1024:.1f} MB")
        print(f"After:  {converted / 1024 / 1024:.1f} MB")
        print(f"Saved:  {saved / 1024 / 1024:.1f} MB")
    else:
        print(f"Dry run: {count} {label} file(s) would be optimized")
        print(f"Current {label} size: {original / 1024 / 1024:.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs-root", type=Path, default=DEFAULT_PACKS)
    parser.add_argument("--apply", action="store_true", help="Actually transcode files and rewrite JSON paths")
    parser.add_argument("--media", choices=["audio", "video", "all"], default="audio")
    parser.add_argument("--bitrate", default="48k", help="AAC audio bitrate")
    parser.add_argument("--sample-rate", type=int, default=24000, help="Output sample rate")
    parser.add_argument("--video-crf", type=int, default=26, help="H.264 CRF for video optimization")
    parser.add_argument("--video-max-height", type=int, default=540, help="Scale videos taller than this height")
    parser.add_argument("--video-audio-bitrate", default="64k", help="AAC bitrate for video audio tracks")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg was not found on PATH")

    if args.media in {"audio", "all"}:
        count, original, converted = optimize_audio(args.packs_root, args.apply, args.bitrate, args.sample_rate)
        print_summary("audio", count, original, converted, args.apply)
    if args.media in {"video", "all"}:
        count, original, converted = optimize_video(
            args.packs_root,
            args.apply,
            args.video_crf,
            args.video_max_height,
            args.video_audio_bitrate,
        )
        print_summary("video", count, original, converted, args.apply)
    if not args.apply:
        print("Use --apply to transcode media and rewrite pack metadata where needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
