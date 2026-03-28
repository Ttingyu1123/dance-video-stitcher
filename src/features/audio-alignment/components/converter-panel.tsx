/**
 * Video Converter Panel - format conversion, resize, quality adjustment.
 * Uses the Python backend's /api/convert endpoint.
 */
import { useState, useCallback, useRef } from 'react';
import { FileVideo, Download, RefreshCw } from 'lucide-react';

const API_BASE = 'http://localhost:8765';

export function ConverterPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [isConverting, setIsConverting] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState('');
  const [outputName, setOutputName] = useState('');
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Settings
  const [format, setFormat] = useState('mp4');
  const [resolution, setResolution] = useState('');
  const [scaleMode, setScaleMode] = useState('fit');
  const [quality, setQuality] = useState('medium');

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setFile(f);
      setDownloadUrl('');
      setError('');
    }
  }, []);

  const handleConvert = useCallback(async () => {
    if (!file) return;
    setIsConverting(true);
    setError('');
    setDownloadUrl('');

    const fd = new FormData();
    fd.append('file', file);
    fd.append('format', format);
    fd.append('quality', quality);
    fd.append('scale_mode', scaleMode);

    if (resolution) {
      const presets: Record<string, [number, number]> = {
        '1080p': [1920, 1080],
        '720p': [1280, 720],
        '480p': [854, 480],
      };
      const res = presets[resolution];
      if (res) {
        fd.append('width', res[0].toString());
        fd.append('height', res[1].toString());
      }
    }

    try {
      const res = await fetch(`${API_BASE}/api/convert`, { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Conversion failed');
      setDownloadUrl(`${API_BASE}${data.download_url}`);
      setOutputName(data.output_filename);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsConverting(false);
    }
  }, [file, format, resolution, scaleMode, quality]);

  const selectClass = 'w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs';
  const labelClass = 'text-[11px] text-muted-foreground';

  return (
    <div className="flex flex-col gap-3 p-3 h-full overflow-y-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Video Converter
      </h3>

      {/* File select */}
      <button
        onClick={() => fileInputRef.current?.click()}
        className="flex flex-col items-center gap-1.5 rounded-md border-2 border-dashed border-border p-4 text-center transition-colors hover:border-primary/50 cursor-pointer"
      >
        <FileVideo className="h-6 w-6 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">
          {file ? file.name : 'Select video file'}
        </span>
        {file && (
          <span className="text-[10px] text-muted-foreground">
            {(file.size / 1024 / 1024).toFixed(1)} MB
          </span>
        )}
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept="video/*,.mp4,.mov,.avi,.mkv,.webm"
        className="hidden"
        onChange={handleFileSelect}
      />

      {/* Settings */}
      <div className="flex flex-col gap-2">
        <div>
          <label className={labelClass}>Output Format</label>
          <select className={selectClass} value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="mp4">MP4</option>
            <option value="mov">MOV</option>
            <option value="mkv">MKV</option>
            <option value="webm">WebM</option>
            <option value="avi">AVI</option>
          </select>
        </div>

        <div>
          <label className={labelClass}>Resolution</label>
          <select className={selectClass} value={resolution} onChange={(e) => setResolution(e.target.value)}>
            <option value="">Original</option>
            <option value="1080p">1080p (1920x1080)</option>
            <option value="720p">720p (1280x720)</option>
            <option value="480p">480p (854x480)</option>
          </select>
        </div>

        {resolution && (
          <div>
            <label className={labelClass}>Scale Mode</label>
            <select className={selectClass} value={scaleMode} onChange={(e) => setScaleMode(e.target.value)}>
              <option value="fit">Fit (letterbox)</option>
              <option value="fill">Fill (crop)</option>
              <option value="stretch">Stretch</option>
            </select>
          </div>
        )}

        <div>
          <label className={labelClass}>Quality</label>
          <select className={selectClass} value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
      </div>

      {/* Convert button */}
      <button
        onClick={handleConvert}
        disabled={!file || isConverting}
        className="flex items-center justify-center gap-2 rounded-md bg-red-500 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {isConverting ? (
          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <FileVideo className="h-3.5 w-3.5" />
        )}
        {isConverting ? 'Converting...' : 'Convert'}
      </button>

      {/* Error */}
      {error && (
        <div className="rounded-md bg-red-500/10 p-2 text-xs text-red-400">{error}</div>
      )}

      {/* Download */}
      {downloadUrl && (
        <a
          href={downloadUrl}
          download={outputName}
          className="flex items-center justify-center gap-2 rounded-md bg-emerald-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-emerald-700"
        >
          <Download className="h-3.5 w-3.5" />
          Download {outputName}
        </a>
      )}
    </div>
  );
}
