# -*- coding: utf-8 -*-
"""
Video metadata helpers.

OpenCV may report zero width/height for some containers/codecs even after the
video opens successfully. These helpers fall back to a decoded frame when
metadata properties are incomplete.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import cv2


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def has_valid_dimensions(self) -> bool:
        return self.width > 0 and self.height > 0

    @property
    def has_valid_timeline(self) -> bool:
        return self.fps > 0 and self.frame_count > 0


def read_video_metadata(
    video_path: str | Path | None = None,
    *,
    cap: Optional[Any] = None,
    sample_frame: Optional[Any] = None,
) -> VideoMetadata:
    """
    Read video metadata and repair missing dimensions from a decoded frame.

    If ``cap`` is provided, the caller owns it. The current frame position is
    restored after probing.
    """
    owns_cap = cap is None
    if cap is None:
        if video_path is None:
            raise ValueError("video_path or cap is required")
        cap = cv2.VideoCapture(str(video_path))

    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if sample_frame is not None:
            height, width = _dimensions_from_frame(sample_frame, width, height)
        elif width <= 0 or height <= 0 or frame_count <= 0:
            current_pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
            ret, frame = cap.read()
            if ret:
                height, width = _dimensions_from_frame(frame, width, height)
            if current_pos is not None and current_pos >= 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, current_pos)
            else:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        return VideoMetadata(width=width, height=height, fps=fps, frame_count=frame_count)
    finally:
        if owns_cap:
            cap.release()


def _dimensions_from_frame(frame: Any, width: int, height: int) -> tuple[int, int]:
    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
        return height, width
    frame_height, frame_width = frame.shape[:2]
    return (height if height > 0 else int(frame_height), width if width > 0 else int(frame_width))
