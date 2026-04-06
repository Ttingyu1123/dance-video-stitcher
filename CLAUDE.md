# Dance Video Stitcher (based on FreeCut)

Video editor with audio-based clip alignment. Fork of FreeCut with custom `audio-alignment` feature module.

## Quick Start

```bash
start.bat                                  # One-click: backend (8765) + frontend (5173)
cd py-backend && python main.py            # Backend only
npm run dev                                # Frontend only (port 5173)
npm run build | lint | test | test:run | routes
```

## Architecture

Browser-based multi-track video editor. React 19 + TypeScript + Vite + Zustand + WebGPU.

```
src/
├── features/         # Self-contained modules: editor, timeline, preview, player,
│                     # composition-runtime, export, effects, keyframes, media-library,
│                     # project-bundle, projects, settings, audio-alignment (custom)
├── domain/           # Framework-agnostic logic (transitions engine/registry/renderers)
├── infrastructure/   # Browser/storage/GPU adapters (use these, not lib/ directly)
├── lib/              # Core libs: gpu-effects, gpu-transitions, gpu-compositor, migrations
├── shared/           # Cross-cutting: Zustand stores, logging, utils
├── components/ui/    # shadcn/ui (Radix primitives)
├── routes/           # TanStack Router (auto-generated routeTree.gen.ts — don't edit)
└── config/hotkeys.ts
```

## Custom Features (added on top of FreeCut)

### Audio Alignment (Align tab)
- **Feature module**: `src/features/audio-alignment/components/alignment-panel.tsx`
- **Backend API client**: `src/features/audio-alignment/services/alignment-api.ts`
- **Integration point**: `src/features/editor/components/media-sidebar.tsx` — `handleAlignComplete` callback
- **Python backend**: `py-backend/backend/audio_analysis.py` — STFT → 12 pitch classes → fftconvolve → waveform refinement
- **API base**: `http://localhost:8765` (CORS enabled)

### Video Converter (Convert tab)
- **Component**: `src/features/audio-alignment/components/converter-panel.tsx`
- **Backend endpoint**: `POST /api/convert` — ffmpeg format/resize/quality conversion

### Key Integration Details
- Sidebar tabs: `activeTab` union type in `src/shared/state/editor/types.ts`; add `{ id, icon, label }` + content `<div>` in `media-sidebar.tsx`
- `sourceStart`/`sourceEnd`/`sourceDuration` are **source-native FPS frames**, NOT seconds (PITFALLS.md #4)
- Backend filenames have UUID prefix (`045c6aeb_file.mp4`) — strip with `/^[a-f0-9]{8}_/` before matching media library
- `insertTrack` is a hook method, NOT on the store — don't call from `getState()` (PITFALLS.md #7)

See `PITFALLS.md` for 10 documented issues and solutions.

## Key Patterns

- **State**: Zustand stores + Zundo for undo/redo
- **Timeline store**: `useTimelineStore` is a facade over domain stores (`items-store`, `transitions-store`, `keyframes-store`, `markers-store`, `timeline-settings-store`, `timeline-command-store`). Components use the facade; action code accesses domain stores via `.getState()` directly
- **Timeline mutations**: Use `execute()` wrapper from `shared.ts` in `features/timeline/stores/actions/*.ts` for undo/redo. Never mutate stores directly
- **Timeline item types**: `TimelineItem` discriminated union on `type`: `video | audio | text | image | shape | adjustment | composition` — GIFs use `image`. Types in `src/types/timeline.ts`
- **Item positioning**: Remotion convention — `from` (start frame in project FPS) + `durationInFrames`
- **Compositions**: Pre-comps have `compositions-store.ts` + `composition-navigation-store.ts`. 1-level nesting only
- **Migrations**: `lib/migrations/` — increment `CURRENT_SCHEMA_VERSION` in `types.ts` when adding migrations
- **Feature boundaries**: Import from `@/infrastructure/` facades, not `@/lib/*` directly. Cross-feature imports go through `deps/` adapters. Enforced by pre-push hook (`check:boundaries`, `check:legacy-lib-imports`)
- **Routing**: TanStack Router — run `npm run routes` after adding/changing route files
- **Path alias**: `@/*` → `src/*`
- **Media processing**: Mediabunny (decode), WebCodecs (export), Web Workers for heavy ops
- **Storage**: IndexedDB via `idb` (`lib/storage/`)

## Code Style

- Strict TypeScript (`noUnusedLocals`, `noUnusedParameters`, `noUncheckedIndexedAccess`)
- `no-console` rule — use `createLogger` from `src/shared/logging/logger.ts`; never raw `console.*`
- **Logging**: Wide event pattern for multi-step ops — `log.startEvent(name, opId)` accumulates context, emits one structured event via `.success()` / `.failure()`. Use `createOperationId()` for correlation
- `@typescript-eslint/no-explicit-any` warned
- `lib/logger.ts` uses only `function` declarations (no `class`/`const` at module scope) to avoid temporal dead zone errors in production chunk ordering

## Testing

- Vitest + jsdom + @testing-library/react; tests next to source as `*.test.ts` / `*.test.tsx`
- `src/test/setup.ts` mocks ImageData, WebGPU APIs (`navigator.gpu`), GPU constants — required for WebGPU tests

## Environment & Git

- `VITE_SHOW_DEBUG_PANEL=false` — hides debug panel (shown by default in dev)
- `main` — production; `develop` — active development; PR target: `main`
- Conventional commits: `type(scope): description` (e.g. `fix(timeline):`, `feat(export):`)

## Obsidian Backup

Docs mirrored to: `D:\Dropbox\應用程式\remotely-save\Obsidian_dropbox\03_Projects\Video-Stitch\`
Update Obsidian copy when changing CLAUDE.md or README.md.

## Gotchas

**GPU & Rendering**
- All effects are GPU-only (WebGPU shaders, `type: 'gpu-effect'`). Legacy CSS/glitch/halftone/LUT types removed in v6 migration. Specialized UI for `gpu-curves` and `gpu-color-wheels`; others use `GpuEffectPanel`
- All 13 transitions are GPU-only via `lib/gpu-transitions/`. Each renderer has `gpuTransitionId` + `renderCanvas()` Canvas 2D fallback. `calculateStyles()` is dead code. Use `Math.round()` on canvas `drawImage` offsets to avoid sub-pixel artifacts
- After clip edits changing position/duration, call `applyTransitionRepairs(changedClipIds)` from `shared.ts`
- GPU pipeline caching: `EffectsPipeline.requestCachedDevice()` caches adapter + device globally; preview component warms it on mount
- Progressive downscaling: halve dimensions repeatedly (never one large jump) to avoid moire/aliasing with high-frequency effects
- When updating multiple GPU effect params atomically, use `onParamsBatchChange`/`onParamsBatchLiveChange` — calling `onParamChange` twice reads stale state
- `StableVideoSequence`'s `areGroupPropsEqual` whitelists item properties for React.memo — add new visual `TimelineItem` properties there or risk stale playback renders

**Playback & Scrub**
- Fast scrub: prewarm frames use WASM decode (40-80ms) and block priority frames. Skip prewarm during playback (`isPlaying` check); priority frames use DOM video zero-copy (~1ms). `backgroundPreseek` fires on large jumps (>3s)
- Render loop concurrency: `pumpRenderLoop` uses single-mutex (`scrubRenderInFlightRef`). `scrubRenderGenerationRef` bumped ONLY on playback-start force-clear. Never bump generation on sequential scrub frames — causes unbounded concurrent pumps
- Transition participant video hold: incoming clip's DOM video paused by premount logic, marked `data-transition-hold="1"`, `.play()` called so canvas renderer gets frames. Cleared in `clearTransitionPlaybackSession`
- Transition prearm covers all types — `forceFastScrubOverlay` uses `getPlayingAnyTransitionPrewarmStartFrame` (not complex-only); also checks `getTransitionWindowForFrame` for playback starting inside an active transition
- Reuse rendered frames: use `usePlaybackStore.getState().captureCanvasSource()` first; fall back to `renderSingleFrame()` only when preview unavailable

**Timeline & Tracks**
- Track groups are 1-level only. Gate behavior (mute/visible/locked) propagates via `resolveEffectiveTrackStates()` in `group-utils.ts`
- Group tracks (`isGroup: true`) are headers only — never place items on them; filter when searching for candidate tracks
- Track `order`: lower value = visually higher. New tracks at `minOrder - 1`. Pre-comp dissolve expands upward from bottom-most selected track
- `_splitItem()` returns `{ leftItem, rightItem } | null` — capture return; original item ID is stale after split
- Timeline has its own `keydown` listener in `timeline.tsx` — child panel handlers must `stopPropagation()` and check `e.defaultPrevented`

**Keyboard & UI**
- Browser shortcut conflicts (e.g. Ctrl+E): use `eventListenerOptions: { capture: true }` on the hotkey
- `HOTKEY_OPTIONS` has `preventDefault: true`. For panel-scoped shortcuts, use `onKeyDown` on element with `tabIndex={-1}` + focus-on-hover + `stopPropagation()`, not global `useHotkeys` with guards
- Inline edit cancel (Escape) triggers blur on unmount — use ref guard to prevent `onBlur` from committing cancelled value

**Build & Config**
- `routeTree.gen.ts` is auto-generated — don't edit manually
- `*.mp4` files are gitignored
- Vite pre-bundles `lucide-react` (avoids analyzing 1500+ icons) — don't remove from `optimizeDeps`
- Build uses manual chunk splitting — check `vite.config.ts` when adding large dependencies
- Feature modules use `index.ts` barrel files to define public API surface

**Debug**
- `window.__DEBUG__` (DEV-only, tree-shaken in prod): `stores()`, `getTransitions()`, `getTracks()`, `getMediaLibrary()`, `jitter()`, `previewPerf()`, `transitionTrace()`, `prewarmCache()`, `filmstripMetrics()`, `seekTo`, `play`, `pause`. All use lazy `await import()`

## Available CLI Tools (OpenCLI)

Run `opencli list` to discover all available CLI tools. Use `opencli <command> -f json` for structured output.
Requires: `npm install -g @jackwener/opencli`. Public commands work without Chrome Extension.
