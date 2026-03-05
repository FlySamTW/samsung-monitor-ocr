import os
import sys
import time
import json
from datetime import datetime

# Adjust path to find skills
sys.path.append(os.getcwd())

try:
    from skills.batch_orchestrator import BatchOrchestrator
except ImportError:
    sys.path.append(os.path.join(os.getcwd(), 'skills'))
    from batch_orchestrator import BatchOrchestrator

# Import real processor
try:
    import samsung_ocr_batch_processor
    from samsung_ocr_batch_processor import process_single_image
    from openai import OpenAI
    
    # Initialize Global Client manualy
    print("Setting up global API Client...")
    client = OpenAI(base_url="http://127.0.0.1:1234/v1", api_key="lm-studio", timeout=60, max_retries=1)
    
    # INJECT into module scope
    samsung_ocr_batch_processor.api_client = client
    samsung_ocr_batch_processor.model_name_global = "qwen/qwen3-vl-4b" # From config
    
    print("✅ Injected api_client and model_name_global")

except ImportError:
    print("Failed to import real processor!")
    sys.exit(1)

def test_rerun():
    target_dir = r"D:\00_程式\20260120_商化自動OCR圖片\商化照片-202601"
    # Find a valid file
    files = [f for f in os.listdir(target_dir) if f.lower().endswith('.jpg')]
    if not files:
        print("No images found for testing.")
        return
    
    test_file = files[0] # Test the first file
    # test_file = "M-南投縣-南投市-SF-南投-664.jpg" # Optional: Force specific file
    print(f"🧪 Testing REAL Rerun Logic on file: {test_file}")

    orchestrator = BatchOrchestrator({
        "image_dir": target_dir,
        "output_dir": "output",
        "assets_dir": "assets",
        "model_list_file": "型號表.txt"
    })
    
    # 1. Setup REAL Processor
    orchestrator.set_processor_function(process_single_image)
    
    # INJECT Orchestrator into module global scope for logging
    samsung_ocr_batch_processor.orchestrator = orchestrator
    print("✅ Injected orchestrator into module global")
    
    # 2. Force Rerun
    print("1️⃣ Calling force_rerun...")
    orchestrator.force_rerun(test_file)

    print(f"Priority Queue: {orchestrator.priority_queue}")
    
    # 3. Start Batch (simulating API)
    print("2️⃣ Starting Batch...")
    orchestrator.start_batch(restart=False)
    
    # 4. Wait for processing
    print("⏳ Waiting for processing loop...")
    max_wait = 60
    start = time.time()
    while orchestrator.is_running:
        if time.time() - start > max_wait:
            print("❌ Timeout waiting for batch to finish.")
            orchestrator.stop_batch()
            break
        time.sleep(1)
        
    print("3️⃣ Batch finished or stopped.")
    
    # 5. Check Results
    print("4️⃣ Verifying Result...")
    
    # Check if a new session file was created
    session_files = [f for f in os.listdir(target_dir) if f.endswith('OCR成功.json')]
    # Sort by time
    session_files.sort(key=lambda x: os.path.getmtime(os.path.join(target_dir, x)))
    last_file = session_files[-1]
    print(f"Latest Session File: {last_file}")
    
    # Read it
    with open(os.path.join(target_dir, last_file), 'r', encoding='utf-8') as f:
        data = json.load(f)
        found = False
        for item in data:
            # Check LS format
            img_path = item.get('data', {}).get('image', '')
            if os.path.basename(img_path) == test_file:
                # Check for our mock data
                model_val = "Unknown"
                # Parse LS annotations to find model
                try:
                    for res in item['annotations'][0]['result']:
                        if res['from_name'] == 'model':
                            model_val = res['value']['text'][0]
                except: pass
                
                print(f"✅ Found record in JSON! Model: {model_val}")
                if model_val == "DEBUG_MODEL_v1":
                    print("SUCCESS: Logic is working and data was saved.")
                else:
                    print("FAILURE: Record found but data is OLD (not matching mock).")
                found = True
                break
        
        if not found:
            print(f"❌ Record NOT found in {last_file}")

    # Check get_all_records
    print("5️⃣ Verifying get_all_records()...")
    all_records = orchestrator.get_all_records()
    final_rec = None
    for r in all_records:
        if r.get('file_name') == test_file:
            final_rec = r
            break
            
    if final_rec:
        print(f"✅ Record found in memory map. Model: {final_rec.get('model')}")
    else:
        print("❌ Record NOT found in memory map.")

if __name__ == "__main__":
    test_rerun()
