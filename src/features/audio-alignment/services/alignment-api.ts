/**
 * Client for the Python audio alignment backend (localhost:8765)
 */

const API_BASE = 'http://localhost:8765';

export interface MethodDetail {
  method: string;
  offset_sec: number;
  confidence: number;
  detail: string;
}

export interface AlignmentResult {
  clip_id: string;
  filename: string;
  file_path: string;
  offset_sec: number;
  duration_sec: number;
  confidence: number;
  trim_start: number;
  trim_end: number | null;
  speed: number;
  method: string;
  method_details: MethodDetail[];
}

export interface AnalyzeResponse {
  clips: AlignmentResult[];
  reference_duration: number;
}

/**
 * Upload video clips to the alignment backend.
 */
export async function uploadClips(files: File[]): Promise<{ clips: Array<{ file_id: string; filename: string; path: string; duration: number }> }> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const res = await fetch(`${API_BASE}/api/upload/clips`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Upload a reference song to the alignment backend.
 */
export async function uploadReference(file: File): Promise<{ path: string; duration: number }> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${API_BASE}/api/upload/reference`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Run full auto-alignment analysis.
 */
export async function analyzeClips(clipPaths: string[]): Promise<AnalyzeResponse> {
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(clipPaths),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Refine alignment around manual positions.
 */
export async function refineClips(
  clips: Array<{ file_path: string; hint_offset: number }>,
  searchWindow: number,
): Promise<AnalyzeResponse> {
  const res = await fetch(`${API_BASE}/api/refine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clips, search_window: searchWindow }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Check if the Python backend is reachable.
 */
export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/clips`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}
