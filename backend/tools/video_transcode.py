# -*- coding: utf-8 -*-
"""
Video transcoding helpers for compatibility-sensitive external tools.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg


def transcode_video_for_vsf(input_path: str | Path, output_path: str | Path) -> str:
    """
    Transcode a video into a conservative MP4 format for VideoSubFinder/OpenCV.

    Some source files can be decoded by the local OpenCV package but fail inside
    VideoSubFinder's bundled decoder stack. A H.264/yuv420p MP4 is broadly
    accepted by FFmpeg and OpenCV based readers, while preserving the original
    timeline without forcing a new frame rate.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    attempts = [
        (
            "H.264",
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ],
        ),
        (
            "MPEG-4",
            [
                "-c:v",
                "mpeg4",
                "-q:v",
                "2",
                "-pix_fmt",
                "yuv420p",
            ],
        ),
    ]

    errors: list[str] = []
    for label, video_args in attempts:
        _remove_incomplete_output(output_path)
        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            *video_args,
            str(output_path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return str(output_path)
        errors.append(f"{label}: {_summarize_ffmpeg_error(result.stderr)}")

    _remove_incomplete_output(output_path)
    raise RuntimeError("FFmpeg transcode failed before VideoSubFinder. " + " | ".join(errors))


def _summarize_ffmpeg_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "no error output"
    return " ".join(lines[-3:])


def _remove_incomplete_output(output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()
