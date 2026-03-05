import sys
import os

# Adjust path
sys.path.append(os.getcwd())

try:
    print("Attempting to import samsung_ocr_batch_processor...")
    # This might fail if the file is not in PYTHONPATH or has errors
    from samsung_ocr_batch_processor import process_single_image
    print("Success! Function Imported")
    print(process_single_image)
except ImportError as e:
    print("Import Failed: " + str(e))
except Exception as e:
    print("Crash during import: " + str(e))
