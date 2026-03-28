# Pitfalls & Lessons Learned

開發過程中踩過的坑，避免未來重複犯錯。

## 1. 不要從零手刻影片編輯器 UI

**問題**：最初嘗試用 vanilla JS + Canvas 手寫時間軸、波形、播放功能，反覆出錯（波形不顯示、播放無法同步、拖動不工作）。
**解法**：Fork FreeCut（成熟的開源瀏覽器影片編輯器），只加自訂功能模組。
**教訓**：影片編輯器 UI 的複雜度遠超預期，用現成方案省 90% 的時間。

## 2. wavesurfer.js 無法 decode .mp4 音頻

**問題**：wavesurfer.js 載入 .mp4 影片檔時，Web Audio API 對某些影片容器格式 decode 失敗，導致波形不顯示。
**解法**：需要後端先用 ffmpeg 提取純音頻（M4A/AAC），wavesurfer 載入音頻檔而非影片檔。
**教訓**：瀏覽器的 Web Audio API 對影片容器支援不穩定，音頻和影片要分開處理。

## 3. ffmpeg 不一定有 libmp3lame

**問題**：後端嘗試用 `libmp3lame` 編碼器輸出 MP3，但 ffmpeg 安裝版本沒有這個編碼器，轉檔靜默失敗。
**解法**：改用 AAC 編碼（`-c:a aac`），ffmpeg 內建支援，不需額外安裝。
**教訓**：不要假設 ffmpeg 有所有編碼器，用 `ffmpeg -encoders` 確認可用的。

## 4. FreeCut 的 sourceStart/sourceEnd 是 frame 數不是秒數

**問題**：建立 timeline item 時，`sourceStart`、`sourceEnd`、`sourceDuration` 設成秒數，導致 Split 後的 clip 無法播放。
**解法**：這些欄位必須是 **source-native FPS 的 frame 數**：`Math.round(seconds * sourceFps)`。`trimStart`/`trimEnd` 應設為 `0`。
**教訓**：FreeCut CLAUDE.md 有寫這個規則，修改前要先讀完。

## 5. 每次上傳都產生新 UUID 檔名

**問題**：前端每次 Auto-Align 都重新上傳檔案，後端為每個檔案加 UUID 前綴（`045c6aeb_原檔名.mp4`）。導致：
- 後端的 `reference_path` 指向舊上傳的路徑，跟新 clip 路徑不匹配
- `align_all_clips` 裡 `normpath` 比對失敗，reference clip 不被識別，offset 不為 0
**解法**：每次 analyze 都無條件重新從當前 clips 選最長的當 reference，不依賴舊 session 的 state。
**教訓**：有 UUID 前綴的檔案系統，路徑比對要特別小心。

## 6. 檔名匹配需要去掉 UUID 前綴

**問題**：後端回傳的 `filename` 帶 UUID 前綴（`045c6aeb_別君賦 08.mp4`），但 FreeCut media library 存的是原始檔名（`別君賦 08.mp4`），導致 `mediaItems.find()` 找不到匹配。
**解法**：前端用 `filename.replace(/^[a-f0-9]{8}_/, '')` 去掉前綴再比對。
**教訓**：任何跨系統的檔名比對都要考慮前綴/後綴差異。

## 7. FreeCut 的 insertTrack 不在 store 上

**問題**：嘗試用 `useTimelineStore.getState().insertTrack()` 程式化建立 track，但 `insertTrack` 是 hook 層的方法，不在 store 上。用 `as any` 強制呼叫導致靜默失敗。
**解法**：不自動建立 track，要求使用者先手動建好足夠的 tracks（點 + 按鈕）。
**教訓**：FreeCut 的 store facade 只暴露部分 actions，hook 層的方法不能從 store 直接呼叫。

## 8. removeItems + addItem 的時序問題

**問題**：Auto-Align 先 `removeItems()` 清空 timeline，再 `addItem()` 加新 clips。但如果 `addItem` 失敗（例如 track 不存在），結果是 timeline 完全空白。
**解法**：改為不刪除舊 items，只疊加新的。使用者需要自行清理。
**教訓**：破壞性操作（刪除）和建設性操作（新增）之間要確保新增一定成功，否則先新增再刪除。

## 9. CORS 必須設 allow_origins=["*"]

**問題**：FreeCut 跑在 localhost:5173，Python backend 跑在 localhost:8765，跨 port 就是跨域。
**解法**：FastAPI 的 CORSMiddleware 設 `allow_origins=["*"]`。
**教訓**：本地開發的雙 server 架構一定要處理 CORS。

## 10. Python 3.14 的套件相容性

**問題**：librosa 依賴 numba/llvmlite，不支援 Python 3.14。PySide6 也不支援。
**解法**：音頻分析只用 scipy + numpy（無 librosa），UI 用瀏覽器（無 PySide6）。
**教訓**：用最新版 Python 要先確認關鍵套件的相容性。
