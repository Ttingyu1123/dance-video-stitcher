"""
Video processing module for Dance Video Stitcher.
Handles ffmpeg operations: thumbnails, frame extraction, trimming,
concatenation, format conversion, and resizing.
"""

import subprocess
import json
import os
import base64
from dataclasses import dataclass


def get_duration(file_path: str) -> float:
    """Get duration of a video/audio file in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def get_video_info(file_path: str) -> dict:
    """Get video info (width, height, duration, codec)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,r_frame_rate",
        "-show_entries", "format=duration,size",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    info = json.loads(result.stdout)

    stream = info.get("streams", [{}])[0]
    fmt = info.get("format", {})

    # Parse frame rate fraction
    fps_str = stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) > 0 else 30.0
    else:
        fps = float(fps_str)

    return {
        "width": stream.get("width", 0),
        "height": stream.get("height", 0),
        "codec": stream.get("codec_name", "unknown"),
        "fps": round(fps, 2),
        "duration": float(fmt.get("duration", 0)),
        "size_bytes": int(fmt.get("size", 0)),
    }


def extract_thumbnail(file_path: str, time_sec: float, output_path: str, width: int = 320) -> str:
    """Extract a thumbnail from video at given time. Returns output path."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", file_path,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-loglevel", "error",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Thumbnail extraction failed: {result.stderr}")
    return output_path


def extract_thumbnail_base64(file_path: str, time_sec: float, width: int = 320) -> str:
    """Extract thumbnail and return as base64 JPEG string."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", file_path,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-f", "image2",
        "-c:v", "mjpeg",
        "-loglevel", "error",
        "pipe:1"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"Thumbnail extraction failed: {result.stderr.decode()}")
    return base64.b64encode(result.stdout).decode("ascii")


def extract_frame_base64(file_path: str, time_sec: float, width: int = 640) -> str:
    """Extract a single frame at given time, return as base64 JPEG."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", file_path,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-f", "image2",
        "-c:v", "mjpeg",
        "-loglevel", "error",
        "pipe:1"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"Frame extraction failed: {result.stderr.decode()}")
    return base64.b64encode(result.stdout).decode("ascii")


def trim_clip(
    input_path: str,
    start_sec: float,
    end_sec: float,
    output_path: str,
    speed: float = 1.0,
) -> str:
    """
    Trim a clip from start to end seconds.
    Optionally apply speed change.
    """
    duration = end_sec - start_sec

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration),
    ]

    if speed != 1.0:
        # Video speed via setpts, audio via atempo
        video_filter = f"setpts={1.0/speed}*PTS"
        # atempo only supports 0.5 to 100.0, chain for extreme values
        audio_filters = _build_atempo_chain(speed)
        cmd += ["-vf", video_filter, "-af", audio_filters]

    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-loglevel", "error",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Trim failed: {result.stderr}")
    return output_path


def _build_atempo_chain(speed: float) -> str:
    """Build ffmpeg atempo filter chain for a given speed multiplier."""
    # atempo supports 0.5 to 100.0
    filters = []
    remaining = speed
    while remaining > 100.0:
        filters.append("atempo=100.0")
        remaining /= 100.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def concat_clips(
    clip_paths: list[str],
    output_path: str,
    crossfade_sec: float = 0.0,
) -> str:
    """
    Concatenate trimmed clips in order.
    If crossfade_sec > 0, apply video and audio crossfade between clips.
    """
    if not clip_paths:
        raise ValueError("No clips to concatenate")

    if len(clip_paths) == 1:
        # Just copy
        cmd = [
            "ffmpeg", "-y", "-i", clip_paths[0],
            "-c", "copy", "-loglevel", "error",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Copy failed: {result.stderr}")
        return output_path

    if crossfade_sec <= 0:
        return _concat_no_crossfade(clip_paths, output_path)
    else:
        return _concat_with_crossfade(clip_paths, output_path, crossfade_sec)


def _concat_no_crossfade(clip_paths: list[str], output_path: str) -> str:
    """Concatenate clips using concat demuxer (no re-encoding if possible)."""
    # Create concat file list
    list_path = output_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for path in clip_paths:
            # Escape single quotes in path
            safe_path = path.replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-loglevel", "error",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Cleanup concat list
    try:
        os.remove(list_path)
    except OSError:
        pass

    if result.returncode != 0:
        raise RuntimeError(f"Concat failed: {result.stderr}")
    return output_path


def _concat_with_crossfade(
    clip_paths: list[str],
    output_path: str,
    crossfade_sec: float,
) -> str:
    """Concatenate clips with crossfade using filter_complex, two at a time."""
    if len(clip_paths) <= 1:
        return _concat_no_crossfade(clip_paths, output_path)

    # Process pairs sequentially to avoid complex filter graphs
    current = clip_paths[0]
    temp_dir = os.path.dirname(output_path)

    for i in range(1, len(clip_paths)):
        next_clip = clip_paths[i]
        is_last = (i == len(clip_paths) - 1)
        out = output_path if is_last else os.path.join(temp_dir, f"_xfade_temp_{i}.mp4")

        dur = get_duration(current)
        offset = max(0, dur - crossfade_sec)

        cmd = [
            "ffmpeg", "-y",
            "-i", current,
            "-i", next_clip,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={crossfade_sec}:offset={offset}[vout];"
            f"[0:a][1:a]acrossfade=d={crossfade_sec}[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac",
            "-loglevel", "error",
            out
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Crossfade failed at clip {i}: {result.stderr}")

        # Clean up previous temp
        if not is_last and i > 1:
            try:
                os.remove(current)
            except OSError:
                pass
        current = out

    return output_path


# ─── Format Converter Functions ───


def convert_format(
    input_path: str,
    output_path: str,
    width: int | None = None,
    height: int | None = None,
    scale_mode: str = "fit",  # fit (letterbox), fill (crop), stretch
    quality: str = "medium",  # high, medium, low
) -> str:
    """
    Convert video format with optional resize.
    scale_mode: 'fit' adds black bars, 'fill' crops, 'stretch' distorts
    quality: 'high' (CRF 18), 'medium' (CRF 23), 'low' (CRF 28)
    """
    crf_map = {"high": "18", "medium": "23", "low": "28"}
    crf = crf_map.get(quality, "23")

    cmd = ["ffmpeg", "-y", "-i", input_path]

    if width and height:
        vf = _build_scale_filter(width, height, scale_mode)
        cmd += ["-vf", vf]
    elif width:
        cmd += ["-vf", f"scale={width}:-2"]
    elif height:
        cmd += ["-vf", f"scale=-2:{height}"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", crf,
        "-c:a", "aac",
        "-b:a", "192k",
        "-loglevel", "error",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Conversion failed: {result.stderr}")
    return output_path


def _build_scale_filter(width: int, height: int, mode: str) -> str:
    """Build ffmpeg scale/pad/crop filter for different resize modes."""
    if mode == "fit":
        # Scale to fit within dimensions, add black bars
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )
    elif mode == "fill":
        # Scale to fill dimensions, crop excess
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    else:
        # Stretch to exact dimensions
        return f"scale={width}:{height}"


RESOLUTION_PRESETS = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
}
