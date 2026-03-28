"""
Multi-method audio alignment engine.

Provides three independent alignment algorithms that run in parallel.
The ensemble picks the result with the highest confidence.

Methods:
  1. Chroma cross-correlation  (pitch-class energy — good for music)
  2. MFCC cross-correlation    (timbral envelope — good for speech/ambient)
  3. Spectral peak fingerprint (Shazam-style — good for exact matches)
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import stft, fftconvolve

# ─── Constants ──────────────────────────────────────────────────────────────

SAMPLE_RATE = 22050
STFT_WINDOW = 4096
STFT_HOP = 2048

# MFCC
N_MELS = 40
N_MFCC = 13

# Fingerprint
FP_FAN_VALUE = 5        # pairs per anchor
FP_MIN_TIME_DELTA = 0   # frames
FP_MAX_TIME_DELTA = 100  # frames
FP_FREQ_BITS = 10
FP_DELTA_BITS = 10


# ─── Result container ──────────────────────────────────────────────────────

@dataclass
class MethodResult:
    method: str          # "chroma" | "mfcc" | "fingerprint"
    offset_sec: float
    confidence: float
    detail: str = ""     # human-readable note


# ─── 1. Chroma cross-correlation ───────────────────────────────────────────

def _compute_chroma(audio: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """12-bin chroma features from STFT."""
    freqs, _times, Zxx = stft(
        audio, fs=sr, nperseg=STFT_WINDOW,
        noverlap=STFT_WINDOW - STFT_HOP,
    )
    magnitude = np.abs(Zxx)
    chroma = np.zeros((12, magnitude.shape[1]), dtype=np.float32)

    for i, freq in enumerate(freqs):
        if freq < 50 or freq > 8000:
            continue
        pitch_class = int(round(12 * np.log2(freq / 440.0))) % 12
        chroma[pitch_class] += magnitude[i]

    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    return chroma / norms


def _chroma_correlate(
    clip_chroma: np.ndarray, ref_chroma: np.ndarray
) -> tuple[int, float]:
    n_ref = ref_chroma.shape[1]
    n_clip = clip_chroma.shape[1]
    if n_clip > n_ref:
        clip_chroma = clip_chroma[:, :n_ref]
        n_clip = n_ref

    correlation = np.zeros(n_ref + n_clip - 1, dtype=np.float64)
    for i in range(12):
        correlation += fftconvolve(ref_chroma[i], clip_chroma[i, ::-1], mode="full")

    # correlation[k] corresponds to offset (k - n_clip + 1) in STFT frames.
    # Valid range: clip fully within reference → k from (n_clip-1) to (n_ref-1)
    valid_start = n_clip - 1
    valid_end = n_ref
    valid = correlation[valid_start:valid_end]
    if len(valid) == 0:
        return 0, 0.0

    peak_idx = int(np.argmax(valid))
    peak_val = valid[peak_idx]
    mean_c, std_c = np.mean(valid), np.std(valid)
    confidence = min(1.0, (peak_val - mean_c) / (4 * std_c)) if std_c > 0 else 0.0
    # peak_idx is relative to valid_start, which maps to offset 0
    return peak_idx, max(0.0, confidence)


def align_chroma(
    clip_audio: np.ndarray, ref_audio: np.ndarray, sr: int = SAMPLE_RATE
) -> MethodResult:
    """Chroma cross-correlation alignment."""
    clip_chroma = _compute_chroma(clip_audio, sr)
    ref_chroma = _compute_chroma(ref_audio, sr)
    if clip_chroma.shape[1] == 0 or ref_chroma.shape[1] == 0:
        return MethodResult("chroma", 0.0, 0.0, "empty chroma")

    offset_frames, confidence = _chroma_correlate(clip_chroma, ref_chroma)
    offset_sec = offset_frames * STFT_HOP / sr
    return MethodResult("chroma", round(offset_sec, 3), round(confidence, 3))


# ─── 2. MFCC cross-correlation ────────────────────────────────────────────

def _hz_to_mel(hz: float) -> float:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(sr: int, n_fft: int, n_mels: int = N_MELS) -> np.ndarray:
    """Build a Mel filterbank matrix (n_mels x n_fft//2+1)."""
    fmax = sr / 2.0
    mel_low = _hz_to_mel(0)
    mel_high = _hz_to_mel(fmax)
    mel_points = np.linspace(mel_low, mel_high, n_mels + 2)
    hz_points = np.array([_mel_to_hz(m) for m in mel_points])
    bin_points = np.floor((n_fft + 1) * hz_points / sr).astype(int)

    n_freqs = n_fft // 2 + 1
    filterbank = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for m in range(n_mels):
        left, center, right = bin_points[m], bin_points[m + 1], bin_points[m + 2]
        for k in range(left, center):
            if center > left:
                filterbank[m, k] = (k - left) / (center - left)
        for k in range(center, right):
            if right > center:
                filterbank[m, k] = (right - k) / (right - center)
    return filterbank


def _compute_mfcc(
    audio: np.ndarray, sr: int = SAMPLE_RATE, n_mfcc: int = N_MFCC
) -> np.ndarray:
    """Compute MFCC features: (n_mfcc, T) matrix."""
    freqs, _times, Zxx = stft(
        audio, fs=sr, nperseg=STFT_WINDOW,
        noverlap=STFT_WINDOW - STFT_HOP,
    )
    power = np.abs(Zxx) ** 2

    mel_fb = _mel_filterbank(sr, STFT_WINDOW, N_MELS)
    mel_spec = mel_fb @ power  # (n_mels, T)
    mel_spec = np.maximum(mel_spec, 1e-10)
    log_mel = np.log(mel_spec)

    # DCT-II to get MFCCs
    n_mels_actual = log_mel.shape[0]
    dct_matrix = np.zeros((n_mfcc, n_mels_actual), dtype=np.float32)
    for k in range(n_mfcc):
        for n in range(n_mels_actual):
            dct_matrix[k, n] = np.cos(np.pi * k * (2 * n + 1) / (2 * n_mels_actual))
    mfcc = dct_matrix @ log_mel  # (n_mfcc, T)

    # Normalize per-coefficient across time
    mean = mfcc.mean(axis=1, keepdims=True)
    std = mfcc.std(axis=1, keepdims=True)
    std = np.maximum(std, 1e-8)
    return (mfcc - mean) / std


def align_mfcc(
    clip_audio: np.ndarray, ref_audio: np.ndarray, sr: int = SAMPLE_RATE
) -> MethodResult:
    """MFCC cross-correlation alignment."""
    clip_mfcc = _compute_mfcc(clip_audio, sr)
    ref_mfcc = _compute_mfcc(ref_audio, sr)

    n_coeff = min(clip_mfcc.shape[0], ref_mfcc.shape[0])
    n_ref = ref_mfcc.shape[1]
    n_clip = clip_mfcc.shape[1]

    if n_clip == 0 or n_ref == 0:
        return MethodResult("mfcc", 0.0, 0.0, "empty MFCC")

    if n_clip > n_ref:
        clip_mfcc = clip_mfcc[:, :n_ref]
        n_clip = n_ref

    correlation = np.zeros(n_ref + n_clip - 1, dtype=np.float64)
    for i in range(n_coeff):
        correlation += fftconvolve(ref_mfcc[i], clip_mfcc[i, ::-1], mode="full")

    # correlation[k] corresponds to offset (k - n_clip + 1) in STFT frames
    valid_start = n_clip - 1
    valid_end = n_ref
    valid = correlation[valid_start:valid_end]
    if len(valid) == 0:
        return MethodResult("mfcc", 0.0, 0.0, "no valid range")

    peak_idx = int(np.argmax(valid))
    peak_val = valid[peak_idx]
    mean_c, std_c = np.mean(valid), np.std(valid)
    confidence = min(1.0, (peak_val - mean_c) / (4 * std_c)) if std_c > 0 else 0.0

    offset_sec = peak_idx * STFT_HOP / sr
    return MethodResult("mfcc", round(offset_sec, 3), round(max(0.0, confidence), 3))


# ─── 3. Spectral peak fingerprint ─────────────────────────────────────────

def _get_spectrogram_peaks(
    audio: np.ndarray, sr: int = SAMPLE_RATE, threshold_ratio: float = 0.3
) -> list[tuple[int, int]]:
    """
    Extract spectral peaks from audio.
    Returns list of (time_frame, freq_bin) tuples.
    """
    freqs, _times, Zxx = stft(
        audio, fs=sr, nperseg=STFT_WINDOW,
        noverlap=STFT_WINDOW - STFT_HOP,
    )
    magnitude = np.abs(Zxx)

    # Local maximum detection: compare each point to its 4-connected neighbors
    peaks = []
    rows, cols = magnitude.shape
    threshold = magnitude.max() * threshold_ratio * 0.1  # low threshold to get enough peaks

    for t in range(1, cols - 1):
        for f in range(1, rows - 1):
            val = magnitude[f, t]
            if val < threshold:
                continue
            if (val > magnitude[f - 1, t] and
                val > magnitude[f + 1, t] and
                val > magnitude[f, t - 1] and
                val > magnitude[f, t + 1]):
                peaks.append((t, f))

    return peaks


def _generate_fingerprints(
    peaks: list[tuple[int, int]]
) -> dict[int, list[int]]:
    """
    Generate combinatorial fingerprint hashes from peaks.
    Returns {hash_value: [time_offset, ...]}
    """
    peaks_sorted = sorted(peaks, key=lambda p: (p[0], p[1]))
    fingerprints: dict[int, list[int]] = {}

    for i, (t1, f1) in enumerate(peaks_sorted):
        targets = []
        for j in range(i + 1, len(peaks_sorted)):
            t2, f2 = peaks_sorted[j]
            dt = t2 - t1
            if dt < FP_MIN_TIME_DELTA:
                continue
            if dt > FP_MAX_TIME_DELTA:
                break
            targets.append((t2, f2))
            if len(targets) >= FP_FAN_VALUE:
                break

        for t2, f2 in targets:
            dt = t2 - t1
            # Pack into a single hash: f1 | f2 | dt
            f1_clip = f1 & ((1 << FP_FREQ_BITS) - 1)
            f2_clip = f2 & ((1 << FP_FREQ_BITS) - 1)
            dt_clip = dt & ((1 << FP_DELTA_BITS) - 1)
            h = (f1_clip << (FP_FREQ_BITS + FP_DELTA_BITS)) | (f2_clip << FP_DELTA_BITS) | dt_clip
            fingerprints.setdefault(h, []).append(t1)

    return fingerprints


def align_fingerprint(
    clip_audio: np.ndarray, ref_audio: np.ndarray, sr: int = SAMPLE_RATE
) -> MethodResult:
    """Shazam-style spectral fingerprint alignment."""
    clip_peaks = _get_spectrogram_peaks(clip_audio, sr)
    ref_peaks = _get_spectrogram_peaks(ref_audio, sr)

    if len(clip_peaks) < 10 or len(ref_peaks) < 10:
        return MethodResult("fingerprint", 0.0, 0.0, "too few peaks")

    clip_fp = _generate_fingerprints(clip_peaks)
    ref_fp = _generate_fingerprints(ref_peaks)

    # Match: for each shared hash, compute time offset = ref_time - clip_time
    offset_counts: dict[int, int] = {}
    total_matches = 0

    for h, clip_times in clip_fp.items():
        if h not in ref_fp:
            continue
        ref_times = ref_fp[h]
        for ct in clip_times:
            for rt in ref_times:
                delta = rt - ct  # offset in STFT frames
                offset_counts[delta] = offset_counts.get(delta, 0) + 1
                total_matches += 1

    if not offset_counts:
        return MethodResult("fingerprint", 0.0, 0.0, "no matching hashes")

    # Best offset = most common delta
    best_offset = max(offset_counts, key=offset_counts.get)  # type: ignore[arg-type]
    best_count = offset_counts[best_offset]

    # Confidence: fraction of matches at the peak offset vs total
    # Also consider absolute count
    if total_matches > 0:
        peak_ratio = best_count / total_matches
    else:
        peak_ratio = 0.0

    # Scale confidence: need at least 10 agreeing matches for reasonable confidence
    count_factor = min(1.0, best_count / 50.0)
    confidence = peak_ratio * 0.6 + count_factor * 0.4

    offset_sec = best_offset * STFT_HOP / sr
    return MethodResult(
        "fingerprint",
        round(max(0.0, offset_sec), 3),
        round(min(1.0, max(0.0, confidence)), 3),
        f"{best_count} matches at peak, {total_matches} total",
    )


# ─── Waveform refinement ──────────────────────────────────────────────────

def refine_with_waveform(
    clip_audio: np.ndarray,
    ref_audio: np.ndarray,
    coarse_offset_sec: float,
    window_sec: float = 2.0,
    sr: int = SAMPLE_RATE,
) -> float:
    """Refine coarse offset using raw waveform cross-correlation in a narrow window."""
    coarse_sample = int(coarse_offset_sec * sr)
    window_samples = int(window_sec * sr)

    ref_start = max(0, coarse_sample - window_samples)
    ref_end = min(len(ref_audio), coarse_sample + len(clip_audio) + window_samples)
    ref_segment = ref_audio[ref_start:ref_end]

    clip_segment = clip_audio[:min(len(clip_audio), 5 * sr)]
    if len(clip_segment) > len(ref_segment):
        return coarse_offset_sec

    correlation = fftconvolve(ref_segment, clip_segment[::-1], mode="valid")
    if len(correlation) == 0:
        return coarse_offset_sec

    peak_idx = int(np.argmax(correlation))
    return (ref_start + peak_idx) / sr


# ─── Ensemble orchestrator ─────────────────────────────────────────────────

def align_ensemble(
    clip_audio: np.ndarray,
    ref_audio: np.ndarray,
    sr: int = SAMPLE_RATE,
    methods: Optional[list[str]] = None,
) -> tuple[MethodResult, list[MethodResult]]:
    """
    Run multiple alignment methods in parallel, return (best, all_results).

    Args:
        clip_audio: clip audio as float32 ndarray
        ref_audio:  reference audio as float32 ndarray
        sr:         sample rate
        methods:    subset of ["chroma", "mfcc", "fingerprint"], or None for all

    Returns:
        (best_result, all_results) — best has waveform-refined offset
    """
    available = {
        "chroma": align_chroma,
        "mfcc": align_mfcc,
        "fingerprint": align_fingerprint,
    }

    if methods is None:
        methods = list(available.keys())

    all_results: list[MethodResult] = []

    # Run methods in parallel threads (they're CPU-bound but release GIL during numpy ops)
    with ThreadPoolExecutor(max_workers=len(methods)) as pool:
        futures = {
            pool.submit(available[m], clip_audio, ref_audio, sr): m
            for m in methods if m in available
        }
        for future in as_completed(futures):
            method_name = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                all_results.append(
                    MethodResult(method_name, 0.0, 0.0, f"error: {e}")
                )

    if not all_results:
        return MethodResult("none", 0.0, 0.0, "no methods ran"), []

    # Pick best using consensus + confidence
    best = _pick_best(all_results)

    # Refine the best result with waveform correlation
    if best.confidence > 0:
        refined_sec = refine_with_waveform(clip_audio, ref_audio, best.offset_sec)
        best = MethodResult(
            best.method,
            round(refined_sec, 3),
            best.confidence,
            best.detail,
        )

    return best, all_results


def _pick_best(results: list[MethodResult], tolerance_sec: float = 1.5) -> MethodResult:
    """
    Pick the best result using consensus + confidence.

    Strategy:
    1. If 2+ methods agree on a similar offset (within tolerance), use
       the highest-confidence result from that consensus group.
    2. Otherwise, fall back to the single highest-confidence result.
    """
    if len(results) <= 1:
        return max(results, key=lambda r: r.confidence)

    # Find consensus groups: cluster results by offset proximity
    sorted_results = sorted(results, key=lambda r: r.offset_sec)
    groups: list[list[MethodResult]] = []

    for r in sorted_results:
        placed = False
        for group in groups:
            # Check if this result is close to any member of the group
            if any(abs(r.offset_sec - g.offset_sec) <= tolerance_sec for g in group):
                group.append(r)
                placed = True
                break
        if not placed:
            groups.append([r])

    # Find the best group: prefer groups with 2+ members, then by max confidence
    multi_member_groups = [g for g in groups if len(g) >= 2]

    if multi_member_groups:
        # Among groups with consensus, pick the one with highest max confidence
        best_group = max(
            multi_member_groups,
            key=lambda g: max(r.confidence for r in g),
        )
        return max(best_group, key=lambda r: r.confidence)

    # No consensus — fall back to highest confidence
    return max(results, key=lambda r: r.confidence)
