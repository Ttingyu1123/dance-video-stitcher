/**
 * Audio Alignment Panel - sidebar tab for aligning clips by audio.
 *
 * Flow:
 * 1. User imports videos into FreeCut media library (normal flow)
 * 2. User optionally uploads a reference song here
 * 3. Click "Auto-Align" → sends files to Python backend → gets offsets
 * 4. Clips are placed on the timeline at the correct positions
 */
import { useState, useCallback, useEffect } from 'react';
import { Music, Wand2, RefreshCw, AlertCircle, CheckCircle2 } from 'lucide-react';
import * as api from '../services/alignment-api';

const METHOD_LABELS: Record<string, string> = {
  chroma: 'Chroma',
  mfcc: 'MFCC',
  fingerprint: 'Fingerprint',
  reference: 'Reference',
  refine: 'Refined',
};

interface AlignmentPanelProps {
  onAlignComplete: (results: api.AlignmentResult[], refDuration: number) => void | Promise<void>;
  mediaFiles: Array<{ id: string; name: string; file?: File; blobUrl?: string }>;
}

export function AlignmentPanel({ onAlignComplete, mediaFiles }: AlignmentPanelProps) {
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [referenceName, setReferenceName] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [results, setResults] = useState<api.AlignmentResult[] | null>(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');

  // Check backend health on mount
  useEffect(() => {
    api.checkBackendHealth().then(setBackendOnline);
  }, []);

  // Upload reference song
  const handleRefUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setReferenceName(file.name);
    setError('');

    try {
      setStatus('Uploading reference...');
      await api.uploadReference(file);
      setStatus('Reference uploaded');
    } catch (err: any) {
      setError(`Reference upload failed: ${err.message}`);
      setStatus('');
    }
  }, []);

  // Upload media files to backend and run alignment
  const handleAutoAlign = useCallback(async () => {
    if (mediaFiles.length === 0) {
      setError('Import video clips in the Media tab first');
      return;
    }

    setIsUploading(true);
    setIsAnalyzing(false);
    setError('');
    setResults(null);

    try {
      // Step 1: Upload clips to Python backend
      setStatus(`Uploading ${mediaFiles.length} clips...`);
      const filesToUpload: File[] = [];

      for (const mf of mediaFiles) {
        if (mf.file) {
          filesToUpload.push(mf.file);
        } else if (mf.blobUrl) {
          // Fetch blob from URL and create File
          const res = await fetch(mf.blobUrl);
          const blob = await res.blob();
          filesToUpload.push(new File([blob], mf.name, { type: blob.type }));
        }
      }

      if (filesToUpload.length === 0) {
        setError('No files available to upload. Try re-importing clips.');
        setIsUploading(false);
        return;
      }

      const uploadResult = await api.uploadClips(filesToUpload);
      const paths = uploadResult.clips.map((c) => c.path);
      setIsUploading(false);

      // Step 2: Run analysis
      setIsAnalyzing(true);
      setStatus('Analyzing audio alignment...');

      const analysisResult = await api.analyzeClips(paths);
      // Debug: log raw results from backend
      console.log('[AudioAlign] Raw results:', JSON.stringify(analysisResult.clips.map(c => ({
        name: c.filename, offset: c.offset_sec, duration: c.duration_sec, confidence: c.confidence
      })), null, 2));
      setResults(analysisResult.clips);
      setStatus(`Aligned ${analysisResult.clips.length} clips`);

      onAlignComplete(analysisResult.clips, analysisResult.reference_duration);
    } catch (err: any) {
      setError(err.message);
      setStatus('');
    } finally {
      setIsUploading(false);
      setIsAnalyzing(false);
    }
  }, [mediaFiles, onAlignComplete]);

  // Refine positions
  const handleRefine = useCallback(async () => {
    if (!results) return;
    setIsAnalyzing(true);
    setError('');

    try {
      setStatus('Refining positions...');
      const clipsData = results.map((r) => ({
        file_path: r.file_path,
        hint_offset: r.offset_sec,
      }));

      const refined = await api.refineClips(clipsData, 10);
      setResults(refined.clips);
      setStatus('Positions refined');
      onAlignComplete(refined.clips, refined.reference_duration);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsAnalyzing(false);
    }
  }, [results, onAlignComplete]);

  const isWorking = isUploading || isAnalyzing;

  return (
    <div className="flex flex-col gap-3 p-3 h-full overflow-y-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Audio Alignment
      </h3>

      {/* Backend status */}
      <div className="flex items-center gap-2 text-xs">
        {backendOnline === null ? (
          <span className="text-muted-foreground">Checking backend...</span>
        ) : backendOnline ? (
          <>
            <CheckCircle2 className="h-3 w-3 text-emerald-500" />
            <span className="text-emerald-500">Backend connected</span>
          </>
        ) : (
          <>
            <AlertCircle className="h-3 w-3 text-red-400" />
            <span className="text-red-400">
              Backend offline — run{' '}
              <code className="rounded bg-muted px-1 text-[10px]">python main.py</code>
            </span>
          </>
        )}
      </div>

      {/* Reference song upload */}
      <div className="rounded-md border border-border p-2">
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <Music className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">
            {referenceName || 'Reference song (optional)'}
          </span>
          <input
            type="file"
            accept="audio/*,.mp3,.wav,.flac,.aac,.ogg"
            className="hidden"
            onChange={handleRefUpload}
          />
        </label>
      </div>

      {/* Media file count */}
      <div className="text-xs text-muted-foreground">
        {mediaFiles.length} video clip{mediaFiles.length !== 1 ? 's' : ''} in media library
      </div>

      {/* Action buttons */}
      <button
        onClick={handleAutoAlign}
        disabled={isWorking || !backendOnline || mediaFiles.length === 0}
        className="flex items-center justify-center gap-2 rounded-md bg-red-500 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {isWorking ? (
          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Wand2 className="h-3.5 w-3.5" />
        )}
        {isUploading ? 'Uploading...' : isAnalyzing ? 'Analyzing...' : 'Auto-Align'}
      </button>

      {results && (
        <button
          onClick={handleRefine}
          disabled={isWorking}
          className="flex items-center justify-center gap-2 rounded-md border border-border px-3 py-1.5 text-xs transition-colors hover:bg-accent disabled:opacity-40"
        >
          <RefreshCw className="h-3 w-3" />
          Refine Positions
        </button>
      )}

      {/* Status */}
      {status && !error && (
        <div className="text-xs text-muted-foreground">{status}</div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-md bg-red-500/10 p-2 text-xs text-red-400">{error}</div>
      )}

      {/* Results */}
      {results && (
        <div className="flex flex-col gap-1.5 mt-1">
          <h4 className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Alignment Results
          </h4>
          {results.map((r) => {
            const confColor =
              r.confidence > 0.7
                ? 'text-emerald-400'
                : r.confidence > 0.4
                ? 'text-yellow-400'
                : 'text-red-400';
            const methodLabel = r.method ? METHOD_LABELS[r.method] ?? r.method : '';
            return (
              <div
                key={r.clip_id}
                className="rounded border border-border px-2 py-1.5"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] truncate">{r.filename}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {formatTime(r.offset_sec)} — {formatTime(r.offset_sec + r.duration_sec)}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-0.5">
                    <span className={`text-[10px] font-mono ${confColor}`}>
                      {Math.round(r.confidence * 100)}%
                    </span>
                    {methodLabel && (
                      <span className="text-[9px] font-mono text-muted-foreground">
                        {methodLabel}
                      </span>
                    )}
                  </div>
                </div>
                {/* Method comparison bar */}
                {r.method_details && r.method_details.length > 1 && (
                  <div className="flex gap-1 mt-1">
                    {r.method_details
                      .sort((a, b) => b.confidence - a.confidence)
                      .map((d) => {
                        const isWinner = d.method === r.method;
                        const barColor = isWinner ? 'bg-emerald-500' : 'bg-muted-foreground/30';
                        return (
                          <div
                            key={d.method}
                            className="flex-1 flex flex-col items-center gap-0.5"
                            title={`${d.method}: ${Math.round(d.confidence * 100)}% @ ${formatTime(d.offset_sec)}`}
                          >
                            <div className="w-full h-1 rounded-full bg-muted overflow-hidden">
                              <div
                                className={`h-full rounded-full ${barColor}`}
                                style={{ width: `${Math.max(5, d.confidence * 100)}%` }}
                              />
                            </div>
                            <span className={`text-[8px] ${isWinner ? 'text-emerald-400 font-medium' : 'text-muted-foreground'}`}>
                              {METHOD_LABELS[d.method] ?? d.method}
                            </span>
                          </div>
                        );
                      })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}
