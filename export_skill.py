import json
import os
import argparse
from datetime import datetime

# 標準 System Prompt 模板 (繁體中文版)
BASE_SYSTEM_PROMPT = """
# Role: Samsung Store OCR Specialist

## 任務
你是一個專業的三星門市照片審核員。你的母語是**台灣繁體中文**。
你的任務是從照片中辨識三星產品的**型號 (Model)** 與 **價格 (Price)**，並判斷照片類型。

## 核心定義
1. **單機照 (Single Device)**：
   - 只要不符合「遠景照」定義，一律視為單機。
   - 包含：有標價卡、清楚型號、或雖模糊但聚焦於單一產品的。
2. **遠景照 (Wide Shot)**：
   - 必須 **同時滿足**：(a) 找不到標價卡 (b) 看不到型號字串 (c) 畫面超過 3 台以上完整螢幕。
   - 若有任何疑慮，請優先歸類為「單機」並嘗試辨識。

## 觀察重點
- **價格**：通常是標籤上**字體最大**的數字，位於型號正下方。
- **型號**：通常是英數混合 (如 S24D300)。

## 輸出格式
思考過程 (Thinking Process) 必須強制使用**繁體中文**。
<think>
觀察：(詳細描述你的觀察)
推論：(詳細推論)
</think>
Tool Call: submit_ocr_result(category=..., model=..., price=...)
"""

def export_to_skill(json_path, output_path):
    print(f"Reading dynamic data from: {json_path}")
    
    if not os.path.exists(json_path):
        print("Error: dynamic_data.json not found.")
        return

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        examples = data.get('dynamic_examples', [])
        rules = data.get('feedback_rules', [])
        
        print(f"Found {len(examples)} examples and {len(rules)} user rules.")
        
        # 構建 Skill 內容
        skill_content = f"""---
name: Samsung_OCR_Mastery
description: 專用於三星商化照片辨識的高階技能包。內建 {len(examples)} 筆用戶修正案例與 {len(rules)} 條核心規則。
triggers:
  - "辨識三星照片"
  - "OCR 商化"
---

{BASE_SYSTEM_PROMPT}

## ⚡ 用戶反饋規則 (Immutable Rules)
這些規則來自真實用戶回饋，擁有最高優先權：
"""
        for i, rule in enumerate(rules):
            skill_content += f"{i+1}. {rule}\n"
            
        skill_content += "\n## 📚 實戰經驗庫 (Learned Few-Shot)\n以下案例曾經被誤判，請參考正確答案：\n\n"
        
        # 將 Examples 轉為 Few-Shot 格式
        # 由於原始圖片路徑可能變動，這裡使用 "Text-Only Description" 
        for img_path, res in examples:
            fname = os.path.basename(img_path)
            # 嘗試生成一個虛擬的 User Input (僅描述)
            skill_content += f"### Case: {fname}\n"
            skill_content += f"**User**: (圖片: {fname})\n"
            skill_content += f"**Assistant**: [思考] 根據過往修正經驗，這張圖應識別為:\n"
            skill_content += f"Tool Call: submit_ocr_result(category='{res.get('category')}', model='{res.get('model')}', price='{res.get('price')}')\n\n"
            
        # 寫入檔案
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(skill_content)
            
        print(f"Successfully exported skill to: {output_path}")
        print("這份 Skill 現在包含了所有累積的智慧，可被其他 Agent 或未來的批次作業直接調用。")

    except Exception as e:
        print(f"Export failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="dynamic_data.json")
    parser.add_argument("--output", default="SAMSUNG_OCR_EXPERIENCE_SKILL.md")
    args = parser.parse_args()
    
    export_to_skill(args.input, args.output)
