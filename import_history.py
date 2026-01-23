import json
import os
from skills.auto_curation import AutoCurator

def import_history():
    json_path = "project-1-at-2026-01-20-09-01-f1ed471e.json"
    if not os.path.exists(json_path):
        print(f"File not found: {json_path}")
        return

    curator = AutoCurator()
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    count = 0
    for item in data:
        original_fname = item.get('file_upload')
        if not original_fname: continue
        
        # Parse Annotations
        annotations = item.get('annotations', [])
        if not annotations: continue
        
        result_list = annotations[0].get('result', [])
        
        extracted = {
            "category": "無法分辨",
            "model": "",
            "price": "",
            "black_screen": False
        }
        
        for res in result_list:
            from_name = res.get('from_name')
            val = res.get('value', {})
            
            if from_name == 'category':
                choices = val.get('choices', [])
                if choices: extracted['category'] = choices[0]
            elif from_name == 'model':
                text = val.get('text', [])
                if text: extracted['model'] = text[0]
            elif from_name == 'price':
                text = val.get('text', [])
                if text: extracted['price'] = text[0]
            elif from_name == 'black_screen':
                choices = val.get('choices', [])
                if '黑屏' in choices: extracted['black_screen'] = True

        # Handle LS uuid prefix (8 chars + dash)
        if len(original_fname) > 9 and original_fname[8] == '-':
             potential_real_name = original_fname[9:]
             curator.add_correction(potential_real_name, extracted, rule_note="Imported from Project-1 History")
             print(f"Imported: {potential_real_name} -> {extracted}")
             count += 1
        else:
             curator.add_correction(original_fname, extracted, rule_note="Imported from Project-1 History (Raw)")
             count += 1

    print(f"Total imported: {count}")

if __name__ == "__main__":
    import_history()
