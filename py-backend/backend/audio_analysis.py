"""
Audio analysis module for Dance Video Stitcher.
Extracts audio, computes chroma features, and aligns clips to a reference song
using multi-method ensemble (chroma + MFCC + fingerprint).
"""

import subprocess
import json
import os
import tempfile
from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np
from scipy.signal import stft, fftconvolve
from scipy.io import wavfile

from .alignment_methods import align_ensemble, MethodResult


@dataclass
class ClipAlignment:
    clip_id: str
    filename: str
    file_path: str
    offset_sec: float        # where this clip starts in the reference song
    duration_sec: float      # total clip duration
    confidence: float        # 0-1, how confident the alignment is
    trim_start: float = 0.0  # user-adjustable in-point (seconds from clip start)
    trim_end: Optional[float] = None  # user-adjustable out-point (None = full length)
    speed: float = 1.0       # playback speed multiplier
    method: str = ""         # which alignment method won ("chroma", "mfcc", "fingerprint")
    method_details: list = field(default_factory=list)  # all method results for debugging

    def to_dict(self):
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "ClipAlignment":
        # Handle legacy data without method fields
        d.setdefault("method", "")
        d.setdefault("method_details", [])
        return ClipAlignment(**d)


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg"}
SAMPLE_RATE = 22050
STFT_WINDOW = 4096
STFT_HOP = 2048


def is_supported_video(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_VIDEO_EXTENSIONS


def is_supported_audio(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_AUDIO_EXTENSIONS


def extract_audio(input_path: str, output_wav: str) -> str:
    """Extract audio from video/audio file to mono WAV at 22050Hz."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ac", "1",           # mono
        "-ar", str(SAMPLE_RATE),
        "-f", "wav",
        "-loglevel", "error",
        output_wav
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")
    return output_wav


def load_audio(wav_path: str) -> np.ndarray:
    """Load WAV file as float32 numpy array, normalized to [-1, 1]."""
    sr, data = wavfile.read(wav_path)
    if sr != SAMPLE_RATE:
        raise ValueError(f"Expected sample rate {SAMPLE_RATE}, got {sr}")

    # Convert to float32
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0
    elif data.dtype == np.float64:
        data = data.astype(np.float32)

    # If stereo, take first channel
    if data.ndim > 1:
        data = data[:, 0]

    return data


def compute_chroma(audio: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """
    Compute 12-bin chroma features from audio.
    Returns a (12, T) matrix where T is the number of STFT frames.
    """
    # Compute STFT
    freqs, times, Zxx = stft(
        audio, fs=sr,
        nperseg=STFT_WINDOW,
        noverlap=STFT_WINDOW - STFT_HOP,
    )
    magnitude = np.abs(Zxx)

    # Map frequency bins to chroma (12 pitch classes)
    # Only use bins with freq > 50 Hz to avoid noise
    chroma = np.zeros((12, magnitude.shape[1]), dtype=np.float32)

    for i, freq in enumerate(freqs):
        if freq < 50 or freq > 8000:
            continue
        # Map frequency to pitch class: C=0, C#=1, ..., B=11
        pitch_class = int(round(12 * np.log2(freq / 440.0))) % 12
        chroma[pitch_class] += magnitude[i]

    # L2 normalize each frame
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    norms = np.maximum(norms, 1e-8)  # avoid division by zero
    chroma = chroma / norms

    return chroma


def cross_correlate_chroma(
    clip_chroma: np.ndarray,
    ref_chroma: np.ndarray,
) -> tuple[int, float]:
    """
    Cross-correlate clip chroma against reference chroma.
    Returns (offset_frames, confidence) where offset_frames is the best
    alignment position in the reference.
    """
    n_ref = ref_chroma.shape[1]
    n_clip = clip_chroma.shape[1]

    if n_clip > n_ref:
        # Clip is longer than reference - unusual but handle it
        clip_chroma = clip_chroma[:, :n_ref]
        n_clip = n_ref

    # Sum cross-correlation across all 12 chroma bins
    correlation = np.zeros(n_ref + n_clip - 1, dtype=np.float64)
    for i in range(12):
        corr = fftconvolve(ref_chroma[i], clip_chroma[i, ::-1], mode="full")
        correlation += corr

    # The peak in correlation corresponds to the best alignment
    # Only consider valid offsets (clip fully within reference)
    valid_start = 0
    valid_end = n_ref
    valid_correlation = correlation[valid_start:valid_end]

    peak_idx = int(np.argmax(valid_correlation))
    peak_value = valid_correlation[peak_idx]

    # Confidence: peak relative to mean and std
    mean_corr = np.mean(valid_correlation)
    std_corr = np.std(valid_correlation)
    if std_corr > 0:
        confidence = min(1.0, (peak_value - mean_corr) / (4 * std_corr))
    else:
        confidence = 0.0

    return peak_idx, max(0.0, confidence)


def refine_alignment(
    clip_audio: np.ndarray,
    ref_audio: np.ndarray,
    coarse_offset_sec: float,
    window_sec: float = 2.0,
    sr: int = SAMPLE_RATE,
) -> float:
    """
    Refine coarse alignment using raw waveform cross-correlation
    within a narrow window around the coarse offset.
    Returns refined offset in seconds.
    """
    coarse_sample = int(coarse_offset_sec * sr)
    window_samples = int(window_sec * sr)

    # Extract reference segment around coarse offset
    ref_start = max(0, coarse_sample - window_samples)
    ref_end = min(len(ref_audio), coarse_sample + len(clip_audio) + window_samples)
    ref_segment = ref_audio[ref_start:ref_end]

    # Use a short portion of the clip for refinement (first 5 seconds)
    clip_segment = clip_audio[:min(len(clip_audio), 5 * sr)]

    if len(clip_segment) > len(ref_segment):
        return coarse_offset_sec

    # Cross-correlate
    correlation = fftconvolve(ref_segment, clip_segment[::-1], mode="valid")
    if len(correlation) == 0:
        return coarse_offset_sec

    peak_idx = int(np.argmax(correlation))
    refined_sample = ref_start + peak_idx
    refined_sec = refined_sample / sr

    return refined_sec


def refine_clip_at_position(
    clip_audio: np.ndarray,
    ref_audio: np.ndarray,
    hint_offset_sec: float,
    search_window_sec: float = 10.0,
    sr: int = SAMPLE_RATE,
) -> tuple[float, float]:
    """
    Refine a clip's position using audio correlation, searching only within
    a window around the user's manually placed hint position.
    Returns (refined_offset_sec, confidence).
    """
    # Step 1: Chroma-based search in the local window
    hint_sample = int(hint_offset_sec * sr)
    window_samples = int(search_window_sec * sr)

    ref_start = max(0, hint_sample - window_samples)
    ref_end = min(len(ref_audio), hint_sample + len(clip_audio) + window_samples)
    ref_segment = ref_audio[ref_start:ref_end]

    if len(ref_segment) < sr:  # too short
        return hint_offset_sec, 0.0

    ref_chroma = compute_chroma(ref_segment, sr)
    clip_chroma = compute_chroma(clip_audio, sr)

    if clip_chroma.shape[1] == 0 or ref_chroma.shape[1] == 0:
        return hint_offset_sec, 0.0

    offset_frames, confidence = cross_correlate_chroma(clip_chroma, ref_chroma)
    coarse_offset_sec = ref_start / sr + frames_to_seconds(offset_frames)

    # Step 2: Fine-tune with waveform correlation
    refined_sec = refine_alignment(clip_audio, ref_audio, coarse_offset_sec, window_sec=2.0)

    return round(refined_sec, 3), round(confidence, 3)


def refine_all_clips(
    clip_paths: list[str],
    ref_path: str,
    hint_offsets: list[float],
    temp_dir: str,
    search_window_sec: float = 10.0,
) -> list[ClipAlignment]:
    """
    Refine alignment for clips that have been manually positioned.
    Uses each clip's hint_offset as the center of a local search window.
    """
    ref_wav = os.path.join(temp_dir, "ref_audio.wav")
    extract_audio(ref_path, ref_wav)
    ref_audio = load_audio(ref_wav)

    alignments = []

    for idx, (clip_path, hint_offset) in enumerate(zip(clip_paths, hint_offsets)):
        clip_id = f"clip_{idx:03d}"
        filename = os.path.basename(clip_path)

        clip_wav = os.path.join(temp_dir, f"{clip_id}_audio.wav")
        extract_audio(clip_path, clip_wav)
        clip_audio = load_audio(clip_wav)

        refined_sec, confidence = refine_clip_at_position(
            clip_audio, ref_audio, hint_offset, search_window_sec
        )
        duration_sec = get_audio_duration(clip_audio)

        alignment = ClipAlignment(
            clip_id=clip_id,
            filename=filename,
            file_path=clip_path,
            offset_sec=refined_sec,
            duration_sec=round(duration_sec, 3),
            confidence=confidence,
            method="refine",
            method_details=[],
        )
        alignments.append(alignment)

    alignments.sort(key=lambda a: a.offset_sec)
    for idx, alignment in enumerate(alignments):
        alignment.clip_id = f"clip_{idx:03d}"

    return alignments


def frames_to_seconds(frames: int) -> float:
    """Convert STFT frames to seconds."""
    return frames * STFT_HOP / SAMPLE_RATE


def get_audio_duration(audio: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Get duration of audio array in seconds."""
    return len(audio) / sr


def align_all_clips(
    clip_paths: list[str],
    ref_path: str,
    temp_dir: str,
) -> list[ClipAlignment]:
    """
    Full alignment pipeline:
    1. Extract audio from reference and all clips
    2. Compute chroma features
    3. Cross-correlate each clip against reference (coarse)
    4. Refine alignment with waveform correlation (fine)
    5. Run ensemble methods in parallel for comparison
    6. Return sorted list of ClipAlignment objects
    """
    # Extract and load reference audio
    ref_wav = os.path.join(temp_dir, "ref_audio.wav")
    extract_audio(ref_path, ref_wav)
    ref_audio = load_audio(ref_wav)
    ref_chroma = compute_chroma(ref_audio)

    alignments = []

    # Normalize ref_path for comparison
    ref_path_norm = os.path.normpath(ref_path)

    for idx, clip_path in enumerate(clip_paths):
        clip_id = f"clip_{idx:03d}"
        filename = os.path.basename(clip_path)

        # If this clip IS the reference, offset = 0, confidence = 1.0
        if os.path.normpath(clip_path) == ref_path_norm:
            duration_sec = get_audio_duration(ref_audio)
            alignment = ClipAlignment(
                clip_id=clip_id,
                filename=filename,
                file_path=clip_path,
                offset_sec=0.0,
                duration_sec=round(duration_sec, 3),
                confidence=1.0,
                method="reference",
                method_details=[],
            )
            alignments.append(alignment)
            continue

        # Extract audio
        clip_wav = os.path.join(temp_dir, f"{clip_id}_audio.wav")
        extract_audio(clip_path, clip_wav)
        clip_audio = load_audio(clip_wav)

        # --- Primary: chroma cross-correlation + waveform refinement ---
        clip_chroma = compute_chroma(clip_audio)
        offset_frames, chroma_confidence = cross_correlate_chroma(clip_chroma, ref_chroma)
        coarse_offset_sec = frames_to_seconds(offset_frames)
        refined_offset_sec = refine_alignment(clip_audio, ref_audio, coarse_offset_sec)

        # --- Secondary: ensemble methods for comparison ---
        method_details = [
            {"method": "chroma", "offset_sec": round(refined_offset_sec, 3),
             "confidence": round(chroma_confidence, 3), "detail": "primary (chroma+refine)"}
        ]
        try:
            _best, all_results = align_ensemble(clip_audio, ref_audio, SAMPLE_RATE)
            for r in all_results:
                method_details.append({
                    "method": r.method, "offset_sec": r.offset_sec,
                    "confidence": r.confidence, "detail": r.detail,
                })
        except Exception:
            pass  # ensemble is optional, don't break alignment

        duration_sec = get_audio_duration(clip_audio)

        alignment = ClipAlignment(
            clip_id=clip_id,
            filename=filename,
            file_path=clip_path,
            offset_sec=round(refined_offset_sec, 3),
            duration_sec=round(duration_sec, 3),
            confidence=round(chroma_confidence, 3),
            method="chroma",
            method_details=method_details,
        )
        alignments.append(alignment)

    # Sort by offset
    alignments.sort(key=lambda a: a.offset_sec)

    # Re-assign clip_ids after sorting
    for idx, alignment in enumerate(alignments):
        alignment.clip_id = f"clip_{idx:03d}"

    return alignments


def get_waveform_data(wav_path: str, num_points: int = 1000) -> list[float]:
    """
    Get downsampled waveform data for visualization.
    Returns a list of peak amplitude values.
    """
    audio = load_audio(wav_path)
    chunk_size = max(1, len(audio) // num_points)
    waveform = []

    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i + chunk_size]
        # Use peak amplitude for each chunk
        waveform.append(float(np.max(np.abs(chunk))))

    return waveform[:num_points]


def get_waveform_from_file(
    input_path: str,
    temp_dir: str,
    num_points: int = 1000,
) -> list[float]:
    """Extract audio and return waveform data for any supported file."""
    basename = os.path.splitext(os.path.basename(input_path))[0]
    wav_path = os.path.join(temp_dir, f"{basename}_waveform.wav")
    extract_audio(input_path, wav_path)
    return get_waveform_data(wav_path, num_points)
