# FollowMe 規格價格每日更新

## 目的

`型號表.txt` 只用來判斷哪些型號名稱可以接受；FollowMe 還需要另一份會更新的規格與價格基準表：

- `data/followme_reference.json`

辨識流程會在每張照片分析時讀取這份 JSON，並把最新 FollowMe 規格價格表追加到 prompt。

主程式啟動時也會檢查這份表是否超過 24 小時；如果已過期，會先嘗試更新一次。若網路失敗，會改用現有本機資料繼續跑，不會讓整批照片中斷。

## 手動更新

```powershell
.\.venv\Scripts\python.exe tools\update_followme_reference.py
```

更新內容：

- FollowMe M5 32"
- FollowMe M7 32"
- FollowMe Pro M7 43"
- 各系列常見型號碼
- 規格組合
- 合理價格帶
- 每個來源的抓取狀態與原始候選價格

## 價格使用原則

- 現場照片上的清楚價牌永遠優先。
- 每日表只用來輔助 FollowMe 型號推導與抓出可疑結果。
- 網頁抓到的雜訊價格會放在 `raw_observed_twd`，不會直接給 prompt 使用。
- 給 prompt 與後端規則使用的是已過濾的 `observed_twd` 與 `range_twd`。

## 建議每天自動跑

搬到另一台 Windows 電腦後，可以用工作排程器每天跑一次：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath 'D:\00_程式\20260120_商化自動OCR圖片_HITL實驗'; .\.venv\Scripts\python.exe tools\update_followme_reference.py"
```

如果那台電腦沒有網路，工具仍可用內建基準資料產生表：

```powershell
.\.venv\Scripts\python.exe tools\update_followme_reference.py --offline
```

如果臨時不想在主程式啟動時上網更新，可以加：

```powershell
.\.venv\Scripts\python.exe samsung_ocr_batch_processor.py --no_followme_auto_update
```
