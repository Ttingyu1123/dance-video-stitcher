"""
FastAPI server for Dance Video Stitcher.
Provides REST API for upload, analysis, timeline editing, preview, and rendering.
"""

import os
import uuid
import asyncio
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from . import audio_analysis as aa
from . import video_processing as vp
from .project_manager import ProjectManager

# ─── Paths ───
BASE_DIR = Path(__file__).resolve().parent  # py-backend/
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR = TEMP_DIR / "uploads"

for d in [TEMP_DIR, OUTPUT_DIR, UPLOAD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── App Setup ───
app = FastAPI(title="Dance Video Stitcher")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── State ───
pm = ProjectManager()


# ─── Pages ───

@app.get("/")
async def index():
    return {"status": "Dance Video Stitcher API running"}


# ─── Upload API ───

@app.post("/api/upload/clips")
async def upload_clips(files: list[UploadFile] = File(...)):
    """Upload one or more video clips."""
    uploaded = []
    for f in files:
        if not aa.is_supported_video(f.filename) and not aa.is_supported_audio(f.filename):
            continue
        file_id = f"{uuid.uuid4().hex[:8]}_{f.filename}"
        file_path = UPLOAD_DIR / file_id
        content = await f.read()
        with open(file_path, "wb") as out:
            out.write(content)

        info = vp.get_video_info(str(file_path))
        thumb = vp.extract_thumbnail_base64(str(file_path), min(1.0, info["duration"] / 2))

        uploaded.append({
            "file_id": file_id,
            "filename": f.filename,
            "path": str(file_path),
            "duration": info["duration"],
            "width": info["width"],
            "height": info["height"],
            "fps": info["fps"],
            "thumbnail": thumb,
        })
    return {"clips": uploaded}


@app.post("/api/upload/reference")
async def upload_reference(file: UploadFile = File(...)):
    """Upload the reference song."""
    if not aa.is_supported_audio(file.filename) and not aa.is_supported_video(file.filename):
        raise HTTPException(400, f"Unsupported format: {file.filename}")

    file_id = f"ref_{uuid.uuid4().hex[:8]}_{file.filename}"
    file_path = UPLOAD_DIR / file_id
    content = await file.read()
    with open(file_path, "wb") as out:
        out.write(content)

    duration = vp.get_duration(str(file_path))
    pm.set_reference(str(file_path), duration)

    return {
        "file_id": file_id,
        "filename": file.filename,
        "path": str(file_path),
        "duration": duration,
    }


# ─── Analysis API ───

@app.post("/api/analyze")
async def analyze_clips(clip_paths: list[str]):
    """
    Run audio alignment analysis.
    Expects JSON body: ["path1.mp4", "path2.mp4", ...]
    If no reference song uploaded, auto-uses the longest clip as reference.
    """
    if not clip_paths:
        raise HTTPException(400, "No clips to analyze")

    # Always pick the longest clip from current batch as reference,
    # unless user explicitly uploaded a reference song (filename starts with "ref_")
    ref_path = pm.state.reference_path
    has_dedicated_ref = (
        ref_path
        and os.path.exists(ref_path)
        and os.path.basename(ref_path).startswith("ref_")
    )

    if not has_dedicated_ref:
        # Auto-select longest clip as reference
        longest_path = None
        longest_dur = 0
        for p in clip_paths:
            try:
                dur = vp.get_duration(p)
                if dur > longest_dur:
                    longest_dur = dur
                    longest_path = p
            except Exception:
                continue
        if not longest_path:
            raise HTTPException(400, "Cannot determine reference from clips")
        ref_path = longest_path
        pm.set_reference(ref_path, longest_dur)

    # Run analysis in thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    alignments = await loop.run_in_executor(
        None,
        aa.align_all_clips,
        clip_paths,
        ref_path,
        str(TEMP_DIR),
    )

    pm.set_clips(alignments)

    return {
        "clips": [a.to_dict() for a in alignments],
        "reference_duration": pm.state.reference_duration,
    }


@app.post("/api/add-to-timeline")
async def add_clips_to_timeline(clip_paths: list[str]):
    """
    Add clips directly to timeline without analysis (for manual positioning).
    Clips are placed sequentially with no overlap.
    """
    if not clip_paths:
        raise HTTPException(400, "No clips to add")

    # If no reference, use longest clip
    if not pm.state.reference_path:
        longest_path = None
        longest_dur = 0
        for p in clip_paths:
            try:
                dur = vp.get_duration(p)
                if dur > longest_dur:
                    longest_dur = dur
                    longest_path = p
            except Exception:
                continue
        if longest_path:
            pm.set_reference(longest_path, longest_dur)

    offset = 0.0
    alignments = []
    for idx, path in enumerate(clip_paths):
        duration = vp.get_duration(path)
        alignment = aa.ClipAlignment(
            clip_id=f"clip_{idx:03d}",
            filename=os.path.basename(path),
            file_path=path,
            offset_sec=round(offset, 3),
            duration_sec=round(duration, 3),
            confidence=0.0,  # not analyzed yet
        )
        alignments.append(alignment)
        offset += duration  # place sequentially

    pm.set_clips(alignments)

    return {
        "clips": [a.to_dict() for a in alignments],
        "reference_duration": pm.state.reference_duration,
    }


@app.post("/api/refine")
async def refine_clips(data: dict):
    """
    Refine clip positions based on manual placement.
    Expects JSON: { "clips": [{"file_path": "...", "hint_offset": 12.5}, ...], "search_window": 10 }
    Searches within ±search_window seconds of each clip's current position.
    """
    if not pm.state.reference_path:
        raise HTTPException(400, "No reference song set")

    clips_data = data.get("clips", [])
    search_window = data.get("search_window", 10.0)

    if not clips_data:
        raise HTTPException(400, "No clips to refine")

    clip_paths = [c["file_path"] for c in clips_data]
    hint_offsets = [c["hint_offset"] for c in clips_data]

    loop = asyncio.get_event_loop()
    alignments = await loop.run_in_executor(
        None,
        aa.refine_all_clips,
        clip_paths,
        pm.state.reference_path,
        hint_offsets,
        str(TEMP_DIR),
        search_window,
    )

    pm.set_clips(alignments)

    return {
        "clips": [a.to_dict() for a in alignments],
        "reference_duration": pm.state.reference_duration,
    }


# ─── Timeline Editing API ───

@app.put("/api/clips/{clip_id}")
async def update_clip(clip_id: str, updates: dict):
    """Update clip properties (offset_sec, trim_start, trim_end, speed)."""
    allowed_fields = {"offset_sec", "trim_start", "trim_end", "speed"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}

    clip = pm.update_clip(clip_id, filtered)
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    return clip.to_dict()


@app.delete("/api/clips/{clip_id}")
async def delete_clip(clip_id: str):
    """Remove a clip from the project."""
    if not pm.remove_clip(clip_id):
        raise HTTPException(404, f"Clip {clip_id} not found")
    return {"status": "ok"}


@app.get("/api/clips")
async def get_clips():
    """Get all clips in current order."""
    return {
        "clips": [c.to_dict() for c in pm.get_ordered_clips()],
        "reference_duration": pm.state.reference_duration,
        "reference_path": pm.state.reference_path,
    }


# ─── Audio Playback ───

@app.get("/api/audio/reference")
async def get_reference_audio():
    """Serve the reference audio as MP3 for browser playback."""
    if not pm.state.reference_path:
        raise HTTPException(400, "No reference song uploaded")

    ref_path = pm.state.reference_path
    ext = os.path.splitext(ref_path)[1].lower()

    # If already mp3, serve directly
    if ext == ".mp3":
        return FileResponse(ref_path, media_type="audio/mpeg")

    # Convert to m4a/aac (works for .wav, .flac, .ogg, .mp4, .mov, etc.)
    m4a_path = str(TEMP_DIR / "ref_playback.m4a")
    need_convert = not os.path.exists(m4a_path)
    if not need_convert:
        need_convert = os.path.getmtime(ref_path) > os.path.getmtime(m4a_path)

    if need_convert:
        import subprocess
        result = subprocess.run([
            "ffmpeg", "-y", "-i", ref_path,
            "-vn", "-c:a", "aac", "-b:a", "128k",
            "-loglevel", "error", m4a_path
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(500, f"Audio conversion failed: {result.stderr}")

    return FileResponse(m4a_path, media_type="audio/mp4")


@app.get("/api/audio/clip/{clip_id}")
async def get_clip_audio_aac(clip_id: str):
    """Serve a clip's audio as M4A/AAC (extracted from video) for wavesurfer."""
    clip = pm.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    m4a_path = str(TEMP_DIR / f"{clip_id}_audio.m4a")
    need_convert = not os.path.exists(m4a_path)
    if not need_convert:
        need_convert = os.path.getmtime(clip.file_path) > os.path.getmtime(m4a_path)

    if need_convert:
        import subprocess
        result = subprocess.run([
            "ffmpeg", "-y", "-i", clip.file_path,
            "-vn", "-c:a", "aac", "-b:a", "128k",
            "-loglevel", "error", m4a_path
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(500, f"Audio extraction failed: {result.stderr}")

    return FileResponse(m4a_path, media_type="audio/mp4")


@app.get("/api/video/clip/{clip_id}")
async def get_clip_video(clip_id: str):
    """Serve a clip's video file for the <video> player."""
    clip = pm.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")
    ext = os.path.splitext(clip.file_path)[1].lower()
    media_types = {
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    return FileResponse(
        clip.file_path,
        media_type=media_types.get(ext, "video/mp4"),
    )


@app.get("/api/video/clip-by-file/{file_id}")
async def get_clip_video_by_file(file_id: str):
    """Serve uploaded clip by file_id (for preview before analysis)."""
    file_path = UPLOAD_DIR / file_id
    if not file_path.exists():
        raise HTTPException(404, f"File {file_id} not found")
    ext = os.path.splitext(str(file_path))[1].lower()
    media_types = {
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    return FileResponse(
        str(file_path),
        media_type=media_types.get(ext, "video/mp4"),
    )


# ─── Frame / Waveform API ───

@app.get("/api/frame/{clip_id}")
async def get_frame(clip_id: str, time: float = 0.0):
    """Get a frame from a clip at the given time (relative to clip start)."""
    clip = pm.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    loop = asyncio.get_event_loop()
    frame_b64 = await loop.run_in_executor(
        None, vp.extract_frame_base64, clip.file_path, time
    )
    return {"frame": frame_b64}


@app.get("/api/filmstrip/{clip_id}")
async def get_filmstrip(clip_id: str, count: int = 10, height: int = 60):
    """
    Get a filmstrip (multiple evenly-spaced thumbnail frames) for a clip.
    Returns list of base64 JPEG frames for timeline visualization.
    """
    clip = pm.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    duration = clip.duration_sec
    if duration <= 0:
        return {"frames": [], "duration": 0}

    # Calculate aspect-ratio-preserving width from height
    width = int(height * 16 / 9)  # assume 16:9, will be corrected by ffmpeg scale=-1:h

    # Extract frames at evenly spaced intervals
    interval = duration / max(count, 1)
    times = [interval * i + interval / 2 for i in range(count)]

    loop = asyncio.get_event_loop()
    frames = []
    for t in times:
        t = min(t, duration - 0.1)
        try:
            frame_b64 = await loop.run_in_executor(
                None, vp.extract_thumbnail_base64, clip.file_path, t, width
            )
            frames.append(frame_b64)
        except Exception:
            frames.append(None)

    return {"frames": frames, "duration": duration, "interval": interval}


@app.get("/api/waveform/reference")
async def get_reference_waveform(points: int = 2000):
    """Get reference song waveform data for timeline visualization."""
    if not pm.state.reference_path:
        raise HTTPException(400, "No reference song uploaded")

    loop = asyncio.get_event_loop()
    waveform = await loop.run_in_executor(
        None, aa.get_waveform_from_file,
        pm.state.reference_path, str(TEMP_DIR), points
    )
    return {"waveform": waveform, "duration": pm.state.reference_duration}


@app.get("/api/waveform/{clip_id}")
async def get_clip_waveform(clip_id: str, points: int = 500):
    """Get clip waveform data."""
    clip = pm.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, f"Clip {clip_id} not found")

    loop = asyncio.get_event_loop()
    waveform = await loop.run_in_executor(
        None, aa.get_waveform_from_file,
        clip.file_path, str(TEMP_DIR), points
    )
    return {"waveform": waveform, "duration": clip.duration_sec}


# ─── Render API ───

@app.post("/api/render")
async def render_final(
    crossfade_sec: float = 0.0,
    resolution: str | None = None,
    quality: str = "medium",
):
    """
    Render the final stitched video based on current clip configuration.
    """
    clips = pm.get_ordered_clips()
    if not clips:
        raise HTTPException(400, "No clips to render")

    pm.state.crossfade_sec = crossfade_sec
    pm.state.output_resolution = resolution
    pm.state.output_quality = quality

    # Trim each clip according to user settings
    trimmed_paths = []
    loop = asyncio.get_event_loop()

    for i, clip in enumerate(clips):
        trim_start = clip.trim_start
        trim_end = clip.trim_end if clip.trim_end is not None else clip.duration_sec
        out_path = str(TEMP_DIR / f"trimmed_{i:03d}.mp4")

        await loop.run_in_executor(
            None, vp.trim_clip,
            clip.file_path, trim_start, trim_end, out_path, clip.speed
        )
        trimmed_paths.append(out_path)

    # Concatenate
    output_path = str(OUTPUT_DIR / "stitched_output.mp4")
    await loop.run_in_executor(
        None, vp.concat_clips,
        trimmed_paths, output_path, crossfade_sec
    )

    # Optional resize
    if resolution and resolution in vp.RESOLUTION_PRESETS:
        w, h = vp.RESOLUTION_PRESETS[resolution]
        resized_path = str(OUTPUT_DIR / "stitched_output_final.mp4")
        await loop.run_in_executor(
            None, vp.convert_format,
            output_path, resized_path, w, h, "fit", quality
        )
        output_path = resized_path

    # Auto-save project
    try:
        save_path = pm.auto_save_path()
        if save_path:
            pm.save(save_path)
    except Exception:
        pass  # non-critical

    return {
        "status": "complete",
        "output_path": output_path,
        "download_url": f"/api/download/{os.path.basename(output_path)}",
    }


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Download rendered output file."""
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        str(file_path),
        media_type="video/mp4",
        filename=filename,
    )


# ─── Project Save/Load API ───

@app.post("/api/project/save")
async def save_project(path: str | None = None):
    """Save current project state."""
    save_path = path or pm.auto_save_path()
    if not save_path:
        save_path = str(OUTPUT_DIR / ".dance_project.json")

    pm.save(save_path)
    return {"status": "saved", "path": save_path}


@app.post("/api/project/load")
async def load_project(path: str):
    """Load a project from JSON file."""
    if not os.path.exists(path):
        raise HTTPException(404, f"Project file not found: {path}")

    state = pm.load(path)
    return {
        "project_name": state.project_name,
        "reference_path": state.reference_path,
        "reference_duration": state.reference_duration,
        "clips": [c.to_dict() for c in state.clips],
        "crossfade_sec": state.crossfade_sec,
    }


# ─── Converter API ───

@app.post("/api/convert")
async def convert_video(
    file: UploadFile = File(...),
    format: str = Form("mp4"),
    width: int | None = Form(None),
    height: int | None = Form(None),
    scale_mode: str = Form("fit"),
    quality: str = Form("medium"),
):
    """Convert a video file: format, resolution, quality."""
    # Save uploaded file
    file_id = f"conv_{uuid.uuid4().hex[:8]}_{file.filename}"
    input_path = UPLOAD_DIR / file_id
    content = await file.read()
    with open(input_path, "wb") as out:
        out.write(content)

    # Generate output filename
    base_name = os.path.splitext(file.filename)[0]
    output_filename = f"{base_name}_converted.{format}"
    output_path = str(OUTPUT_DIR / output_filename)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, vp.convert_format,
        str(input_path), output_path,
        width, height, scale_mode, quality
    )

    return {
        "status": "complete",
        "output_filename": output_filename,
        "download_url": f"/api/download/{output_filename}",
    }
