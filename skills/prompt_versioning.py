import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

log = logging.getLogger("rich")

class PromptManager:
    """
    Manages prompt bundles (System Prompt + User Prompt + Few-Shot Examples).
    Ensures version control and reproducibility.
    """
    def __init__(self, assets_dir: str):
        self.assets_dir = assets_dir
        self.bundles_dir = os.path.join(assets_dir, "prompt_bundles")
        os.makedirs(self.bundles_dir, exist_ok=True)
        self.current_bundle_id = None
        self.current_bundle_data = {}

    def load_active_bundle(self, bundle_id: str = "latest"):
        """Loads a specific prompt bundle or the latest one."""
        try:
            target_file = None
            if bundle_id == "latest":
                # Find the most recently created json file
                files = [f for f in os.listdir(self.bundles_dir) if f.endswith(".json")]
                if not files:
                    self._create_default_bundle()
                    files = [f for f in os.listdir(self.bundles_dir) if f.endswith(".json")]
                
                # Sort by timestamp in filename (assuming format prompt_vYYYYMMDD_HHMMSS.json)
                files.sort(reverse=True)
                target_file = files[0]
            else:
                target_file = f"{bundle_id}.json"

            full_path = os.path.join(self.bundles_dir, target_file)
            if not os.path.exists(full_path):
                raise FileNotFoundError(f"Prompt bundle not found: {full_path}")

            with open(full_path, "r", encoding="utf-8") as f:
                self.current_bundle_data = json.load(f)
                self.current_bundle_id = self.current_bundle_data.get("version_id", target_file.replace(".json", ""))
                
            log.info(f"Loaded Prompt Bundle: {self.current_bundle_id}")
            return self.current_bundle_data

        except Exception as e:
            log.error(f"Failed to load prompt bundle: {e}")
            return self._create_default_bundle()

    def _create_default_bundle(self):
        """Creates a default prompt bundle if none exists."""
        version_id = f"prompt_v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        default_data = {
            "version_id": version_id,
            "created_at": datetime.now().isoformat(),
            "system_prompt": "你是一個專業的三星門市照片審核員。...",
            "user_prompt_template": "請分析這張圖片...",
            "few_shot_config": {
                "source": "dynamic",
                "k": 3
            },
            "parameters": {
                "temperature": 0.1,
                "top_p": 0.9
            }
        }
        self.save_bundle(default_data, version_id)
        return default_data

    def save_bundle(self, data: Dict[str, Any], version_id: Optional[str] = None):
        """Saves a new prompt bundle version."""
        if not version_id:
            version_id = f"prompt_v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        data["version_id"] = version_id
        file_path = os.path.join(self.bundles_dir, f"{version_id}.json")
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        log.info(f"Saved Prompt Bundle: {version_id}")
        self.current_bundle_id = version_id
        self.current_bundle_data = data
        return version_id

    def get_system_prompt(self) -> str:
        return self.current_bundle_data.get("system_prompt", "")

    def get_user_prompt_template(self) -> str:
        # [v18.83] Hotfix: Always read from the live text file to ensure prompt updates apply immediately!
        # This bypasses the JSON bundle cache which was causing stale prompts.
        prompt_txt_path = os.path.join(self.assets_dir, "..", "samsung_ocr_prompt.txt")
        try:
            with open(prompt_txt_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            log.warning(f"Failed to read live prompt file: {e}. Falling back to bundle.")
            return self.current_bundle_data.get("user_prompt_template", "")

    def get_prompt_bundle(self) -> Dict[str, Any]:
        """Returns the current prompt bundle, loading if necessary."""
        if not self.current_bundle_data:
            return self.load_active_bundle()
        return self.current_bundle_data
