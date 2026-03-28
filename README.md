# Dance Video Stitcher

Video editor with audio-based clip alignment. Built on [FreeCut](https://github.com/walterlow/freecut).

Automatically aligns dance class video clips by audio cross-correlation, then lets you edit on a full-featured timeline.

## Quick Start

```bash
# One-click launch:
start.bat

# Or manually:
cd py-backend && python main.py    # Backend API (port 8765)
npm run dev                        # Editor UI (port 5173)
```

**Requirements:** Node.js 18+, Python 3.10+, ffmpeg in PATH, Chrome 113+

## Project Structure

```
video_stitch_editor/
├── start.bat                          # One-click launcher
├── py-backend/                        # Python audio alignment backend
│   ├── main.py                        # Backend entry point
│   └── backend/
│       ├── server.py                  # FastAPI routes
│       ├── audio_analysis.py          # Chroma cross-correlation engine
│       ├── video_processing.py        # ffmpeg wrappers
│       └── project_manager.py         # Project state (JSON)
├── src/features/audio-alignment/      # Custom feature module
│   ├── components/
│   │   ├── alignment-panel.tsx        # Align sidebar tab
│   │   └── converter-panel.tsx        # Convert sidebar tab
│   ├── services/
│   │   └── alignment-api.ts           # Backend API client
│   └── index.ts
├── src/features/editor/               # FreeCut editor (modified)
├── src/features/timeline/             # FreeCut timeline
└── ...                                # FreeCut core files
```

## Workflow

### Audio Alignment (custom feature)

1. Open editor → left sidebar **Media** tab → Import video clips
2. Switch to **Align** tab (waveform icon)
3. Optionally upload a reference song
4. Click **Auto-Align** → clips auto-placed on timeline at correct music positions
5. Each clip on its own track, overlapping regions visible
6. Use Split (S) to cut, delete unwanted parts, adjust positions
7. Export final video

### Video Converter (custom feature)

Left sidebar → **Convert** tab:
- Select file → choose format/resolution/quality → Convert → Download

## FreeCut Editor Quick Reference

| Action | How |
|--------|-----|
| **Import media** | Media tab → Import, or drag from file explorer |
| **Add to timeline** | Drag from Media panel to a Track |
| **Split/Cut** | Select clip → press **S** or click ✂️ |
| **Delete** | Select → **Delete** |
| **Trim edges** | Drag clip edges |
| **Move clip** | Drag clip on timeline |
| **Play/Pause** | **Space** |
| **Undo** | **Ctrl+Z** |
| **Add text** | Text tab in sidebar |
| **Add effects** | Effects tab in sidebar |
| **Transitions** | Overlap two clips, or drag from Transitions tab |
| **Speed change** | Select clip → Properties panel → Media → Speed |
| **Export** | Top-right **Export** button |

## Audio Alignment Algorithm

1. Extract audio from each video → mono WAV 22050Hz (ffmpeg)
2. Compute chroma features: STFT (window=4096, hop=2048) → 12 pitch classes → L2 normalize
3. Cross-correlate each clip's chroma against reference song's chroma
4. Peak of summed correlation = time offset; confidence = peak prominence
5. Refine with raw waveform correlation in ±2s window for ~1ms accuracy

**Why chroma**: captures harmonic pitch, ignores broadband noise (teacher voice, ambient sounds)

## Supported Formats

| Type | Formats |
|------|---------|
| Video input | MP4, MOV, AVI, MKV, WebM |
| Audio (reference) | MP3, WAV, FLAC, AAC, OGG |
| Video export | MP4, WebM, MOV, MKV (via FreeCut) |
| Converter output | MP4, MOV, MKV, WebM, AVI |

## Key Design Decisions

| Decision | Why |
|----------|-----|
| FreeCut as base | Full-featured browser editor with timeline, waveforms, playback — no need to build from scratch |
| Python backend | scipy/numpy for audio analysis, ffmpeg for video processing |
| Chroma not MFCC | Noise-robust for classroom recordings |
| Each clip on own track | Overlapping regions visible for manual cut decisions |
| Reference optional | Auto-uses longest clip if no song file |

## Obsidian Backup

Project documentation mirrored to:
`D:\Dropbox\應用程式\remotely-save\Obsidian_dropbox\03_Projects\Video-Stitch\`
