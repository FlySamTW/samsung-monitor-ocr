# Qwen3-VL OCR 最佳實踐指南

> 本文檔記錄了在 Samsung 螢幕 OCR 專案中，使用 Qwen3-VL-4B 模型的寶貴經驗。
>
> 最後更新：2026-02-02

---

## 1. 模型參數設定

### 官方建議 vs OCR 優化

| 參數               | 官方建議 | OCR 任務優化 | 說明                                                             |
| ------------------ | -------- | ------------ | ---------------------------------------------------------------- |
| `temperature`      | 0.7      | **0.1**      | 官方 0.7 適合創意任務，但 OCR 需要精確讀取，必須用低溫度避免幻覺 |
| `top_p`            | 0.8      | 0.8          | 保持官方建議                                                     |
| `top_k`            | 20       | 20           | 保持官方建議                                                     |
| `presence_penalty` | 1.5      | 1.5          | 避免重複輸出                                                     |
| `max_tokens`       | -        | 512          | OCR 任務不需要太長輸出                                           |

### 關鍵發現

```
temperature=0.7 會導致：
- 把 13,291 讀成 13.29（誤認為美金格式）
- 把 $13,291 讀成 $13.29
- 憑空編造價格

temperature=0.1 效果：
- 5/5 次正確讀取 13291
- 穩定輸出型號 S32DM703UC
```

---

## 2. 圖片處理原則

### ❌ 錯誤做法：重新編碼

```python
# 這會損失畫質！
buffered = io.BytesIO()
img.convert("RGB").save(buffered, format="JPEG", quality=90)
img_b64 = base64.b64encode(buffered.getvalue()).decode()
```

### ✅ 正確做法：直接讀取原始 bytes

```python
# 直接讀取原始檔案，不重新壓縮
with open(image_path, 'rb') as f:
    img_b64 = base64.b64encode(f.read()).decode()
```

### 為什麼需要 Base64？

- LM Studio 使用 OpenAI 兼容 API
- API 協議要求圖片以 `data:image/jpeg;base64,xxx` 格式傳遞
- 這是 API 協議要求，不是模型要求
- **重點：轉 base64 ≠ 重新編碼圖片**

### 圖片尺寸

- Qwen3-VL 支援高解析度輸入（原生支援動態解析度）
- 不需要縮圖！4000x2252 的原圖可以直接送入
- 只有在記憶體不足時才考慮縮圖

---

## 3. Prompt 設計原則

### 核心原則：讓模型「慢慢讀」

```markdown
⚠️ **慢慢看，仔細讀！**

- 型號的**每一個字母**都很重要（S27D392GAC 不是 S27D392GRC）
- 價格的**每一個數字**都很重要（13,291 不是 13.29 也不是 1329）
```

### 具體讀取方法

```markdown
### 型號辨識（一個字一個字讀）

- **逐字母讀取**，S-3-2-D-M-7-0-3-U-C，不要跳過任何字元！

### 價格辨識（一個數字一個數字讀）

**讀數字的方法：** 從左到右，一→三→二→九→一 = 13291（不是 13.29！）
```

### 避免幻覺的關鍵語句

```markdown
⚠️ **嚴禁憑空編造價格！** 必須在標籤上**親眼看到**數字才能填寫！
如果標籤上**沒有明確的價格數字**，price 欄位填 null！
```

### Few-shot 範例的陷阱

❌ **致命錯誤：範例中包含具體價格數字**

```
這台是 FollowMe，白色支架很明顯，標籤寫 12,900，螢幕正常。
{"price": "12900"}
```

模型會學到「FollowMe = 12,900」的錯誤關聯，然後套用到所有 FollowMe 照片！

❌ **致命錯誤：範例中包含具體型號**

```
{"model": "S27CG552EC"}
```

模型在看不清楚時，會因為恐慌而直接填入這個「標準答案」，導致嚴重幻覺！

❌ **同樣致命：格式說明中包含具體數字**

```
價格格式：`2,390`、`4,990`、`12,990`
```

模型會把這些數字當成「合理答案」來用！

✅ **正確做法：使用佔位符**

```
這台是 FollowMe，白色支架很明顯，但標籤上**完全沒有價格數字**。
這台是 FollowMe，白色支架很明顯，但標籤上**完全沒有價格數字**。
{"model": "Sxx-xxxxxxx (從標籤讀取)", "price": null}

價格格式：`X,XXX` 或 `XX,XXX`
```

---

## 4. 常見問題與解決方案

### 問題 1：把價格讀成小數（13291 → 13.29）

**原因**：temperature 太高，模型「猜測」這是美金格式  
**解決**：降低 temperature 到 0.1

### 問題 2：模型憑空編造價格

**原因**：

1. Prompt 範例中的價格被模型「記住」
2. 沒有明確禁止編造

**解決**：

1. 修改範例，不使用固定價格
2. 加入「嚴禁憑空編造」的警告

### 問題 3：圖片細節丟失

**原因**：不必要的重新編碼（quality=90）  
**解決**：直接讀取原始檔案 bytes

### 問題 4：輸出不穩定

**測試方法**：同一張圖跑 5 次，觀察結果一致性

| 設定            | 5 次測試成功率 |
| --------------- | -------------- |
| temperature=0.7 | 40%（2/5）     |
| temperature=0.1 | 100%（5/5）    |

### 問題 5：邏輯死鎖 (Logic Deadlock)

**症狀**：模型像跳針一樣不斷重複同一段話（如 "但這張照片..." 重複 10 次）。
**原因**：Prompt 規則衝突（例如：優先找 FollowMe vs 價格不符知識庫），導致模型陷入判定迴圈。
**解決**：

1. **Watchdog 機制**：程式端偵測重複語句 (>=2次)，採取零容忍策略，強制中斷並重試。
2. **重試策略**：通常第二次 random seed 不同就會通過。

---

## 5. API 呼叫範例

```python
import requests
import base64

# 直接讀取原始圖片（不重新編碼）
with open('image.jpg', 'rb') as f:
    img_b64 = base64.b64encode(f.read()).decode()

# 讀取 prompt
with open('samsung_ocr_prompt.txt', 'r', encoding='utf-8') as f:
    prompt = f.read()

response = requests.post(
    'http://192.168.0.234:1234/v1/chat/completions',
    json={
        'model': 'qwen3-vl-4b',  # 注意：小寫
        'messages': [
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}},
                {'type': 'text', 'text': '請辨識這張照片'}
            ]}
        ],
        # OCR 優化參數
        'temperature': 0.1,      # 關鍵！低溫度避免幻覺
        'top_p': 0.8,
        'top_k': 20,
        'presence_penalty': 1.5,
        'max_tokens': 512
    },
    timeout=120
)

print(response.json()['choices'][0]['message']['content'])
```

---

## 6. 參考資源

- [Qwen3-VL GitHub](https://github.com/QwenLM/Qwen3-VL)
- [Qwen3-VL-4B-Instruct HuggingFace](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct)
- 官方建議參數（VL 模式）：
  ```bash
  export temperature=0.7
  export top_p=0.8
  export top_k=20
  export presence_penalty=1.5
  ```

---

## 7. 版本歷史

| 版本 | 日期       | 變更                             |
| ---- | ---------- | -------------------------------- |
| v2.5 | 2026-02-02 | 強調逐字讀取、修復圖片重編碼問題 |
| v2.4 | 2026-02-02 | 加入防幻覺規則                   |
| v2.3 | 2026-01-27 | 基礎版本                         |

---

## 8. 核心教訓總結

1. **OCR ≠ 創意生成**：temperature 必須低（0.1）
2. **原圖 ≠ 重編碼**：直接讀 bytes，不要用 PIL 重新存檔
3. **範例會被學習**：Few-shot 中的數值會影響模型輸出
4. **慢就是快**：讓模型「一個字一個字讀」比「快速掃描」準確
5. **測試要多次**：單次測試不可靠，至少跑 5 次確認穩定性
