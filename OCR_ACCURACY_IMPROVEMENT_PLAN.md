# Vision OCR 精度提升計畫 - S27/S32 混淆問題

## 📊 問題診斷

**案例：546.jpg**
- OCR 結果：`S32CG552EC / 7490`
- 可能正確：`S27CG552EC / 7490`（從價格和上下文推斷）
- **根本原因：數字 7 vs 3 混淆**

## 🔍 Vision OCR 常見混淆字元

| 容易混淆 | 原因 | 頻率 |
|---------|------|------|
| **2 vs 7** | 手寫風格、字體傾斜 | ⭐⭐⭐⭐⭐ |
| **3 vs 8** | 部分模糊、光線問題 | ⭐⭐⭐⭐ |
| **0 vs O** | 字體相似 | ⭐⭐⭐⭐ |
| **5 vs S** | 字母數字混淆 | ⭐⭐⭐ |
| **6 vs G** | 字形相似 | ⭐⭐⭐ |
| **1 vs I** | 細長字符 | ⭐⭐ |
| **B vs 8** | 閉合弧形 | ⭐⭐ |

## 🎯 業界最佳實踐（Multi-Pass OCR）

### 方案 A：多重採樣策略（推薦）

```
原圖 → [Crop 1] → OCR → 結果 A
     ↓ [Crop 2] → OCR → 結果 B  → 多數投票
     ↓ [Crop 3] → OCR → 結果 C
```

**實作方式**：
1. 針對標籤區域進行 3 次不同角度/對比度的裁剪
2. 每次送給 Vision Model
3. 用投票機制決定最終結果

### 方案 B：Confidence-Based 驗證

```python
# Prompt 增強：要求模型輸出信心度
{
  "model": "S27CG552EC",
  "confidence": "high",  // 新增
  "ambiguous_chars": []  // 新增：標注不確定的字元
}
```

### 方案 C：Context-Based 後處理（最實用）

**核心思想**：利用價格、尺寸邏輯進行交叉驗證

```
型號檢測: S32CG552EC
價格檢測: 4990

❌ 矛盾！S32 系列價格通常 > 5500
✅ 修正為: S27CG552EC（27吋系列 4000-5000 價格合理）
```

### 方案 D：Prompt 強化（立即可用）

**現有問題**：
- ✅ Prompt 已有「3 vs 4」提醒
- ❌ **缺少「2 vs 7」、「5 vs S」提醒**
- ❌ 缺少尺寸-價格交叉驗證規則

## 🚀 立即改進方案（Prompt Enhancement）

### 改進 1：增強字元辨識提醒

```markdown
4. **超級容易混淆的字元（按頻率排序）**：
   - **🔥 數字 2 vs 7**：S**27**CG552EC vs S**32**CG552EC（最高頻錯誤！）
   - **數字 3 vs 4**：S27D**3**00GAC vs S27D**4**00GAC
   - **數字 3 vs 8**：S**3**2 vs S**8**2
   - **數字 0 vs 字母 O**
   - **數字 5 vs 字母 S**  
   - **數字 6 vs 字母 G**
   - **數字 7 vs B**：S27**B**610EQC，不是 S27**7**33！
   - **AC vs NC vs GNC vs EQC**：結尾要仔細分辨
```

### 改進 2：尺寸-價格邏輯驗證

```markdown
6. **🔥 尺寸-價格邏輯驗證**（超重要！）：

   **三星螢幕尺寸-價格對照表**：
   - S24xxx 系列（24吋）：NT$ 2,000 ~ 3,500
   - S27xxx 系列（27吋）：NT$ 3,000 ~ 6,000
   - S32xxx 系列（32吋）：NT$ 5,500 ~ 9,000
   - S43xxx 系列（43吋）：NT$ 9,000+
   
   **⚠️ 交叉驗證規則**：
   - 如果型號是 S32xxx 但價格 < 5000 → 很可能是 S27xxx（2 vs 7 誤判）
   - 如果型號是 S27xxx 但價格 > 7000 → 很可能是 S32xxx（7 vs 2 誤判）
   - 如果型號是 S24xxx 但價格 > 4000 → 檢查是否為 S27xxx
   
   **範例**：
   - ❌ S32CG552EC / 4990 → 矛盾！32吋不可能這價格
   - ✅ 修正為 S27CG552EC / 4990（27吋價格合理）
```

### 改進 3：增加思考檢查點

```markdown
## 輸出前自我檢查（必須執行）

在輸出 JSON 前，請自問：

1. **型號長度正確嗎？**  
   - 三星型號通常 10-11 字元（如 S27CG552EC）
   - 如果只有 5-6 字元（如 S27733）很可能錯誤

2. **尺寸與價格邏輯合理嗎？**
   - S24xxx + 價格 > 4000 → 可能誤判
   - S27xxx + 價格 < 3000 → 可能誤判
   - S32xxx + 價格 < 5500 → 很可能誤判（檢查 2 vs 7）
   
3. **相似字元是否再次確認？**
   - 型號中的 2 vs 7 → 重新仔細看一次
   - 型號中的 3 vs 4 → 配合價格驗證
   - 型號中的 0 vs O → 確認上下文
```

## 📝 Prompt 修改建議

**位置：samsung_ocr_prompt.txt 第 60-80 行**

**修改重點**：
1. 在現有「超級容易混淆的字元」清單最前面加上 **「數字 2 vs 7」**（標記為最高頻）
2. 在「型號價格對應驗證」章節後面新增「尺寸-價格邏輯驗證」
3. 在輸出格式前新增「輸出前自我檢查」章節

## 🔬 進階方案（需開發）

### A. Python 後處理層（推薦！）

在 `field_extraction.py` 中新增驗證函數：

```python
def validate_model_price_logic(model: str, price: int) -> dict:
    """
    交叉驗證型號與價格的邏輯一致性
    
    Returns:
        {
            "is_valid": bool,
            "suggested_fix": str,
            "confidence": float,
            "reason": str
        }
    """
    import re
    
    # 提取尺寸
    size_match = re.search(r'S(\d{2})', model)
    if not size_match:
        return {"is_valid": True, "suggested_fix": None}
    
    size = int(size_match.group(1))
    
    # 價格範圍
    price_ranges = {
        24: (2000, 3500),
        27: (3000, 6000),
        32: (5500, 9000),
        43: (9000, 20000)
    }
    
    if size in price_ranges:
        min_price, max_price = price_ranges[size]
        
        if not (min_price <= price <= max_price):
            # 價格不合理，嘗試推測正確尺寸
            for candidate_size, (min_p, max_p) in price_ranges.items():
                if min_p <= price <= max_p:
                    suggested_model = model.replace(f"S{size}", f"S{candidate_size}")
                    return {
                        "is_valid": False,
                        "suggested_fix": suggested_model,
                        "confidence": 0.8,
                        "reason": f"價格 {price} 不符合 S{size} 範圍 ({min_price}-{max_price})，建議改為 S{candidate_size}"
                    }
    
    return {"is_valid": True, "suggested_fix": None}
```

**整合位置**：`samsung_ocr_batch_processor.py` 的 `process_single_image()` 函數中，在型號提取後立即驗證。

### B. Few-Shot Examples 強化

在 `few_shot_examples.json` 中新增錯誤案例：

```json
{
  "image_desc": "27吋曲面螢幕，標籤寫 S__CG552EC，數字 2/7 模糊",
  "correct_reasoning": "價格 4990 屬於 27吋範圍（3000-6000），不是 32吋（>5500），所以是 S27CG552EC",
  "wrong_output": {"model": "S32CG552EC", "price": "4990"},
  "correct_output": {"model": "S27CG552EC", "price": "4990"}
}
```

### C. Vision Model 參數調整

如果使用 API 支援：
```python
# 增加 temperature 降低隨機性
# 增加 max_tokens 讓模型有更多思考空間
ocr_params = {
    "temperature": 0.1,  # 更確定性
    "top_p": 0.9,
    "max_tokens": 500,   # 允許更詳細的思考
}
```

## 📊 效果評估指標

實施改進後，追蹤以下指標：

| 指標 | 目標 | 測量方式 |
|------|------|---------|
| 2/7 混淆率 | < 2% | 人工抽查 100 張含 S27/S32 的圖 |
| 3/4 混淆率 | < 1% | 檢查 S27D300/S27D400 識別準確率 |
| 尺寸-價格矛盾率 | 0% | 自動檢查所有結果 |
| 整體型號準確率 | > 98% | 與人工標注對比 |

## 🎯 推薦實施順序

1. **立即**：修改 Prompt（方案 1、2、3）→ 30 分鐘
2. **短期**：Python 後處理層（方案 A）→ 2 小時
3. **中期**：Few-Shot 強化（方案 B）→ 1 天
4. **長期**：Multi-Pass OCR（需要架構調整）→ 1 週

---

**總結**：546.jpg 問題根源是 **2 vs 7 混淆**，最快的改進是**增強 Prompt 並加入尺寸-價格交叉驗證**。
