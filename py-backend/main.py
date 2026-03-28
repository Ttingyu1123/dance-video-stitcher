"""
Dance Video Stitcher - Backend API
Audio alignment engine for FreeCut editor.
"""

import sys
import os
import uvicorn

# Ensure py-backend/ is the working directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    print("=" * 50)
    print("  Dance Video Stitcher - Backend API")
    print("  Running on http://localhost:8765")
    print("  Press Ctrl+C to stop")
    print("=" * 50)

    uvicorn.run(
        "backend.server:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
    )
