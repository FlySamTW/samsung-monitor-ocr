#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import base64
from openai import OpenAI
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Import local modules
from skills.batch_orchestrator import BatchOrchestrator
from skills.prompt_versioning import PromptManager
from skills.two_stage_ocr import TwoStageOCRProcessor

# --- Flask App ---
flask_app = Flask(__name__)
CORS(flask_app)
orchestrator: BatchOrchestrator = None
api_client: OpenAI = None
model_name_global = ""
two_stage_processor: TwoStageOCRProcessor = None

# --- LLM Processor Function (The "Brain" passed to Orchestrator) ---
def process_single_image(fname, image_b64, prompt_mgr: PromptManager, auto_curator):
    """
    使用兩階段 OCR 處理器處理圖片
    """
    global two_stage_processor, orchestrator
    
    try:
        # 創建臨時圖片檔案
        import tempfile, os
        from PIL import Image
        import io
        
        img_data = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_data))
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
            temp_path = temp_file.name
            img.save(temp_path, format='JPEG', quality=95)
        
        try:
            # 使用兩階段 OCR 處理器
            result = two_stage_processor.process_image(temp_path, fname, orchestrator)
            
            # 提取 OCR 結果
            ocr_data = result.get('ocr_result', {})
            
            return {
                "category": ocr_data.get("category", "不合格-照片不清楚"),
                "model": ocr_data.get("model", ""),
                "price": ocr_data.get("price", ""),
                "black_screen": ocr_data.get("black_screen", False),
                "thinking": ocr_data.get("thinking", ""),
                "confidence": result.get('stage2', {}).get('confidence', 0.0)
            }
            
        finally:
            # 清理臨時檔案
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                
    except Exception as e:
        if orchestrator:
            orchestrator.log_system(f"❌ 處理失敗: {e}")
        
        return {
            "category": "不合格-照片不清楚",
            "model": "",
            "price": "",
            "black_screen": False,
            "thinking": f"處理錯誤: {e}",
            "confidence": 0.0
        }

# --- Flask API Routes ---

@flask_app.route('/api/status', methods=['GET'])
def get_status():
    """獲取系統狀態"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    return jsonify(orchestrator.get_status())

@flask_app.route('/api/logs', methods=['GET'])
def get_logs():
    """獲取系統日誌"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    # 使用分頁參數
    last = request.args.get('last', '0')
    lines = request.args.get('lines', '50')
    
    try:
        last_id = int(last)
        max_lines = int(lines)
        return jsonify(orchestrator.get_logs_since(last_id, max_lines))
    except (ValueError, TypeError):
        return jsonify({"error": "無效的參數"}), 400

@flask_app.route('/api/start_batch', methods=['POST'])
def start_batch():
    """開始批次處理"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    try:
        if orchestrator.is_running():
            return jsonify({"error": "批次處理已在執行中"}), 400
        
        # 開始批次處理
        thread_id = orchestrator.start_batch()
        return jsonify({"status": "started", "thread_id": thread_id})
        
    except Exception as e:
        return jsonify({"error": f"啟動失敗: {str(e)}"}), 500

@flask_app.route('/api/pause', methods=['POST'])
def pause_batch():
    """暫停批次處理"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    orchestrator.pause()
    return jsonify({"status": "paused"})

@flask_app.route('/api/resume', methods=['POST'])
def resume_batch():
    """恢復批次處理"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    orchestrator.resume()
    return jsonify({"status": "resumed"})

@flask_app.route('/api/stop', methods=['POST'])
def stop_batch():
    """停止批次處理"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    orchestrator.stop()
    return jsonify({"status": "stopped"})

@flask_app.route('/api/results', methods=['GET'])
def get_results():
    """獲取處理結果"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    return jsonify(orchestrator.get_results())

@flask_app.route('/api/photos/<filename>', methods=['GET'])
def serve_photo(filename):
    """提供圖片檔案"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    return send_from_directory(orchestrator.config["image_dir"], filename)

@flask_app.route('/dashboard/optimized')
def serve_optimized_dashboard():
    """提供優化後的控制台界面"""
    return send_from_directory('.', 'dashboard_optimized.html')

@flask_app.route('/api/manual_correction', methods=['POST'])
def manual_correction():
    """接收人工修正並加入學習系統"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    try:
        data = request.get_json()
        filename = data.get('filename')
        correction_data = data.get('correction')
        
        if not filename or not correction_data:
            return jsonify({"error": "缺少必要參數"}), 400
        
        # 添加到修正學習系統
        orchestrator.auto_curator.add_correction(filename, correction_data)
        
        # 記錄到日誌
        orchestrator.log_system(f"✅ 人工修正: {filename} -> {correction_data}")
        
        # 重新加入處理佇列
        if filename not in orchestrator.retry_queue:
            orchestrator.retry_queue.append(filename)
            orchestrator.log_system(f"🔄 重新排程: {filename}")
        
        return jsonify({
            "status": "success", 
            "message": "修正已提交並加入學習",
            "filename": filename,
            "correction": correction_data
        })
        
    except Exception as e:
        return jsonify({"error": f"修正失敗: {str(e)}"}), 500

# --- Main ---
def main():
    global orchestrator, api_client, model_name_global, two_stage_processor
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="photos", help="Image directory")
    parser.add_argument("--api_base", default="http://localhost:1234/v1", help="LM Studio/OpenAI Base URL")
    parser.add_argument("--api_key", default="lm-studio", help="API Key")
    parser.add_argument("--model", default="zai-org/glm-4.6v-flash", help="Model Name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files")
    args = parser.parse_args()

    model_name_global = args.model
    api_client = OpenAI(base_url=args.api_base, api_key=args.api_key)

    # 初始化兩階段 OCR 處理器
    two_stage_processor = TwoStageOCRProcessor(api_client, model_name_global)

    # Config for Orchestrator
    config = {
        "image_dir": args.dir,
        "output_dir": ".", # Root for csvs
        "output_file": "final_results_v4.csv", # Legacy
        "assets_dir": "assets",
        "persist_file": "dynamic_data.json",
        "model_list_file": "型號表.txt",
        "clean_config": str(args)
    }

    orchestrator = BatchOrchestrator(config)
    orchestrator.set_processor_function(process_single_image)
    
    print(f"🚀 三星 OCR 批次處理系統啟動!")
    print(f"📁 圖片目錄: {args.dir}")
    print(f"🧠 模型: {args.model}")
    print(f"🌐 API Base: {args.api_base}")
    print(f"📊 優化界面: http://localhost:5000/dashboard/optimized")
    print()

    flask_app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

if __name__ == "__main__":
    main()