"""
Project manager for Dance Video Stitcher.
Handles project state: clip list, alignment results, trim settings.
Supports save/load to JSON for project persistence.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from .audio_analysis import ClipAlignment


@dataclass
class ProjectState:
    """Represents the complete state of a stitching project."""
    project_name: str = "Untitled Dance"
    reference_path: Optional[str] = None
    reference_duration: float = 0.0
    clips: list[ClipAlignment] = field(default_factory=list)
    crossfade_sec: float = 0.0
    output_resolution: Optional[str] = None  # e.g. "1080p" or None for source
    output_quality: str = "medium"

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "reference_path": self.reference_path,
            "reference_duration": self.reference_duration,
            "clips": [c.to_dict() for c in self.clips],
            "crossfade_sec": self.crossfade_sec,
            "output_resolution": self.output_resolution,
            "output_quality": self.output_quality,
        }

    @staticmethod
    def from_dict(d: dict) -> "ProjectState":
        clips = [ClipAlignment.from_dict(c) for c in d.get("clips", [])]
        return ProjectState(
            project_name=d.get("project_name", "Untitled Dance"),
            reference_path=d.get("reference_path"),
            reference_duration=d.get("reference_duration", 0.0),
            clips=clips,
            crossfade_sec=d.get("crossfade_sec", 0.0),
            output_resolution=d.get("output_resolution"),
            output_quality=d.get("output_quality", "medium"),
        )


class ProjectManager:
    """Manages the current project state with save/load capabilities."""

    def __init__(self):
        self.state = ProjectState()
        self._save_path: Optional[str] = None

    def new_project(self, name: str = "Untitled Dance") -> ProjectState:
        self.state = ProjectState(project_name=name)
        self._save_path = None
        return self.state

    def set_reference(self, path: str, duration: float):
        self.state.reference_path = path
        self.state.reference_duration = duration

    def set_clips(self, clips: list[ClipAlignment]):
        self.state.clips = clips

    def update_clip(self, clip_id: str, updates: dict) -> Optional[ClipAlignment]:
        """Update a specific clip's properties (offset, trim, speed, etc.)."""
        for clip in self.state.clips:
            if clip.clip_id == clip_id:
                for key, value in updates.items():
                    if hasattr(clip, key):
                        setattr(clip, key, value)
                return clip
        return None

    def remove_clip(self, clip_id: str) -> bool:
        original_len = len(self.state.clips)
        self.state.clips = [c for c in self.state.clips if c.clip_id != clip_id]
        return len(self.state.clips) < original_len

    def get_clip(self, clip_id: str) -> Optional[ClipAlignment]:
        for clip in self.state.clips:
            if clip.clip_id == clip_id:
                return clip
        return None

    def get_ordered_clips(self) -> list[ClipAlignment]:
        """Return clips sorted by their offset position."""
        return sorted(self.state.clips, key=lambda c: c.offset_sec)

    def save(self, path: Optional[str] = None) -> str:
        """Save project state to JSON file."""
        save_path = path or self._save_path
        if not save_path:
            raise ValueError("No save path specified")

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self.state.to_dict(), f, indent=2, ensure_ascii=False)

        self._save_path = save_path
        return save_path

    def load(self, path: str) -> ProjectState:
        """Load project state from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.state = ProjectState.from_dict(data)
        self._save_path = path
        return self.state

    def auto_save_path(self) -> Optional[str]:
        """Generate auto-save path based on reference file location."""
        if self.state.reference_path:
            ref_dir = os.path.dirname(self.state.reference_path)
            return os.path.join(ref_dir, ".dance_project.json")
        return None
