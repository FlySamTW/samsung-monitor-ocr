import os
import io
import base64
import logging
import numpy as np
import cv2
from PIL import Image, ImageEnhance, ImageOps

log = logging.getLogger("rich")

class ImageProcessor:
    """
    Handles image preprocessing to improve OCR accuracy.
    Features: Resize, Contrast Enhancement, Sharpness, Grayscale conversion, Label Card Detection.
    """
    def __init__(self, config: dict = None):
        defaults = {
            "max_size": 4096,
            "crop_max_size": 4096,
            "crop_jpeg_quality": 100,
            "max_dimensions": None,
            "contrast_factor": 1.2,
            "sharpness_factor": 1.5,
            "auto_orient": True,
            "detect_label_card": True,  # [v18.25] Enable by default for Dual Vision
            "auto_high_res_crops": True,
            "bottom_label_strip": False,
            "bottom_center_zoom": False,
            "evidence_attempt": 1,
            "scene_tile_max": 3,
        }
        self.config = defaults
        if config:
            self.config.update(config)

    def _resize_box_for(self, img: Image.Image):
        max_dimensions = self.config.get("max_dimensions")
        if max_dimensions:
            width, height = max_dimensions
            max_edge = max(int(width), int(height))
            return max_edge, max_edge

        max_size = self.config.get("max_size")
        if max_size is None:
            return None
        max_size = int(max_size)
        return max_size, max_size

    def _resize_if_needed(
        self,
        img: Image.Image,
        applied_transforms: list,
        label: str,
    ):
        box = self._resize_box_for(img)
        if not box:
            return img, False
        target_edge = max(box)
        if max(img.width, img.height) <= target_edge:
            return img, False

        resized = img.copy()
        resized.thumbnail(box)
        applied_transforms.append(f"{label}_resize_long_edge_{target_edge}_to_{resized.width}x{resized.height}")
        return resized, True

    def _resize_crop_if_needed(self, img: Image.Image, applied_transforms: list, label: str):
        """Bound supplementary evidence independently from the low-res full scene."""
        crop_limit = self.config.get("crop_max_size")
        if crop_limit is None or max(img.size) <= int(crop_limit):
            return img
        resized = img.copy()
        resized.thumbnail((int(crop_limit), int(crop_limit)))
        applied_transforms.append(f"{label}_resize_long_edge_{int(crop_limit)}_to_{resized.width}x{resized.height}")
        return resized

    def _encode_crop(self, img: Image.Image) -> str:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="JPEG", quality=int(self.config.get("crop_jpeg_quality", 100)))
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _scene_tile_specs(self, width: int, height: int, attempt: int):
        """Return bounded full-height subject/ownership tiles by evidence pass."""
        if attempt <= 1:
            specs = (("scene_center", 0.26, 0.74),)
        elif attempt == 2:
            specs = (("scene_left", 0.00, 0.60), ("scene_center", 0.26, 0.74), ("scene_right", 0.40, 1.00))
        elif attempt >= 3:
            specs = (("scene_left", 0.00, 0.48), ("scene_center", 0.26, 0.74), ("scene_right", 0.52, 1.00))
        else:
            return []
        return [(label, (int(width * x1), 0, int(width * (x2 - x1)), height)) for label, x1, x2 in specs]

    def crop_bottom_label_strip(self, img_array: np.ndarray) -> tuple:
        """
        Crop the lower shelf/label band for difficult retail photos.
        This is deterministic and intended for rerun workflows, not manual slicing.
        """
        try:
            h, w = img_array.shape[:2]
            y1 = int(h * 0.58)
            y2 = int(h * 0.95)
            cropped = img_array[y1:y2, 0:w]
            return cropped, (0, y1, w, y2 - y1)
        except Exception as e:
            log.warning(f"Bottom label strip crop failed: {e}")
            return None, None

    def crop_bottom_center_zoom(self, img_array: np.ndarray) -> tuple:
        """
        Crop and enlarge the lower-center price card zone.
        Useful when the main product label sits behind the shelf rail.
        """
        try:
            h, w = img_array.shape[:2]
            x1 = int(w * 0.32)
            x2 = int(w * 0.72)
            y1 = int(h * 0.62)
            y2 = int(h * 0.94)
            cropped = img_array[y1:y2, x1:x2]
            zoomed = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            return zoomed, (x1, y1, x2 - x1, y2 - y1)
        except Exception as e:
            log.warning(f"Bottom center zoom crop failed: {e}")
            return None, None

    def detect_label_card(self, img_array: np.ndarray) -> tuple:
        """
        Detect label card region using edge detection and contour analysis.
        Works with labels of any color (white, yellow, blue, etc.)
        Returns: (cropped_region, bbox) or (None, None) if not found
        """
        try:
            # Convert to grayscale
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            
            # Apply Gaussian blur to reduce noise
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            
            # Use Canny edge detection
            edges = cv2.Canny(blurred, 50, 150)
            
            # Dilate edges to connect nearby contours
            kernel = np.ones((3, 3), np.uint8)
            dilated = cv2.dilate(edges, kernel, iterations=2)
            
            # Find contours
            contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            if not contours:
                return None, None
            
            # Find the best rectangular contour (likely the label card)
            best_contour = None
            best_score = 0
            img_area = img_array.shape[0] * img_array.shape[1]
            
            for cnt in contours:
                area = cv2.contourArea(cnt)
                
                # Filter: must be between 1% and 50% of image area
                if area < (img_area * 0.01) or area > (img_area * 0.5):
                    continue
                
                # Get bounding rectangle
                x, y, w, h = cv2.boundingRect(cnt)
                
                # Calculate aspect ratio (labels are usually wider than tall)
                aspect_ratio = w / float(h) if h > 0 else 0
                
                # Calculate rectangularity (how close to a perfect rectangle)
                rect_area = w * h
                rectangularity = area / rect_area if rect_area > 0 else 0
                
                # Score based on:
                # - Rectangularity (prefer rectangular shapes)
                # - Aspect ratio (prefer horizontal rectangles, 1.2 to 4.0)
                # - Size (prefer medium-sized regions)
                score = 0
                
                if rectangularity > 0.7:  # Must be fairly rectangular
                    score += rectangularity * 50
                    
                if 1.2 <= aspect_ratio <= 4.0:  # Horizontal rectangle
                    score += 30
                elif 0.25 <= aspect_ratio < 1.2:  # Vertical or square
                    score += 10
                    
                # Prefer medium-sized regions (5-30% of image)
                size_ratio = area / img_area
                if 0.05 <= size_ratio <= 0.3:
                    score += 20
                
                if score > best_score:
                    best_score = score
                    best_contour = cnt
            
            if best_contour is None or best_score < 50:  # Minimum score threshold
                log.info(f"[LabelDetect] No suitable label found (best score: {best_score:.1f})")
                return None, None
            
            # Get bounding box
            x, y, w, h = cv2.boundingRect(best_contour)
            
            # Add padding (5%)
            pad_x = int(w * 0.05)
            pad_y = int(h * 0.05)
            x = max(0, x - pad_x)
            y = max(0, y - pad_y)
            w = min(img_array.shape[1] - x, w + 2 * pad_x)
            h = min(img_array.shape[0] - y, h + 2 * pad_y)
            
            # Crop
            cropped = img_array[y:y+h, x:x+w]
            
            log.info(f"[LabelDetect] Found label at ({x},{y}) size {w}x{h}, score={best_score:.1f}")
            return cropped, (x, y, w, h)
            
        except Exception as e:
            log.warning(f"Label card detection failed: {e}")
            return None, None

    def process(self, image_path: str, evidence_attempt: int = None) -> dict:
        """
        Reads and processes an image.
        Returns a dict containing:
        - 'base64': processed image base64 string
        - 'metadata': original size, new size, applied transforms
        """
        try:
            with Image.open(image_path) as img:
                applied_transforms = []
                
                # 1. Auto Orientation (EXIF fix)
                if self.config.get("auto_orient"):
                    img = ImageOps.exif_transpose(img)
                    applied_transforms.append("exif_transpose")

                original_size = img.size
                
                # 2. Label Card Detection (NEW [v18.25])
                attempt = int(evidence_attempt if evidence_attempt is not None else self.config.get("evidence_attempt", 1) or 1)
                label_b64 = None
                bottom_label_b64 = None
                bottom_center_b64 = None
                scene_tiles = []
                if self.config.get("detect_label_card"):
                    img_array = np.array(img)
                    cropped, bbox = self.detect_label_card(img_array)
                    
                    if cropped is not None:
                        # Encode Label to Base64
                        label_img = Image.fromarray(cropped)
                        label_img = self._resize_crop_if_needed(label_img, applied_transforms, "label")
                        label_b64 = self._encode_crop(label_img)
                        applied_transforms.append(f"label_crop_found_{bbox}")
                        log.info(f"[Crop] Extracted high-res label card")

                    large_scene = max(img.size) > max(self._resize_box_for(img) or (0,))
                    if self.config.get("bottom_label_strip") or (self.config.get("auto_high_res_crops") and large_scene):
                        bottom_cropped, bottom_bbox = self.crop_bottom_label_strip(img_array)
                        if bottom_cropped is not None:
                            bottom_img = Image.fromarray(bottom_cropped)
                            bottom_img = self._resize_crop_if_needed(bottom_img, applied_transforms, "bottom_label")
                            bottom_label_b64 = self._encode_crop(bottom_img)
                            applied_transforms.append(f"bottom_label_strip_{bottom_bbox}")

                    if attempt <= 1 and (self.config.get("bottom_center_zoom") or (self.config.get("auto_high_res_crops") and large_scene)):
                        center_cropped, center_bbox = self.crop_bottom_center_zoom(img_array)
                        if center_cropped is not None:
                            center_img = Image.fromarray(center_cropped)
                            center_img = self._resize_crop_if_needed(center_img, applied_transforms, "bottom_center")
                            bottom_center_b64 = self._encode_crop(center_img)
                            applied_transforms.append(f"bottom_center_zoom_{center_bbox}")

                    if large_scene:
                        for tile_label, (x, y, w, h) in self._scene_tile_specs(img.width, img.height, attempt)[:int(self.config.get("scene_tile_max", 3))]:
                            tile = Image.fromarray(img_array[y:y + h, x:x + w])
                            tile = self._resize_crop_if_needed(tile, applied_transforms, tile_label)
                            scene_tiles.append({
                                "label": tile_label,
                                "bbox": (x, y, w, h),
                                "base64": self._encode_crop(tile),
                                "size": tile.size,
                                "attempt": attempt,
                            })
                            applied_transforms.append(f"{tile_label}_bbox_{(x, y, w, h)}_attempt_{attempt}")

                # 3. Resize Full Image if too large (KEEP as context)
                full_img = img.copy()
                full_img, needs_reencode = self._resize_if_needed(full_img, applied_transforms, "resize")
                
                # [v18.65] 只在必要時重新編碼，避免畫質損失
                if needs_reencode:
                    # 縮圖後需要重新編碼
                    buffered = io.BytesIO()
                    full_img.convert("RGB").save(buffered, format="JPEG", quality=95)
                    full_img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                else:
                    # 直接讀取原始檔案的 bytes，不重新編碼！
                    with open(image_path, 'rb') as f:
                        full_img_b64 = base64.b64encode(f.read()).decode('utf-8')
                    applied_transforms.append("raw_bytes_no_reencode")

                return {
                    "base64": full_img_b64,
                    "label_base64": label_b64,
                    "bottom_label_base64": bottom_label_b64,
                    "bottom_center_base64": bottom_center_b64,
                    "metadata": {
                        "original_size": original_size,
                        "processed_size": full_img.size,
                        "label_found": label_b64 is not None,
                        "bottom_label_strip": bottom_label_b64 is not None,
                        "bottom_center_zoom": bottom_center_b64 is not None,
                        "scene_tiles": [{k: v for k, v in tile.items() if k != "base64"} for tile in scene_tiles],
                        "evidence_attempt": attempt,
                        "source_path": str(image_path),
                        "transforms": applied_transforms
                    },
                    "scene_tiles": scene_tiles,
                }

        except Exception as e:
            # [v11.96 Silence Terminal Spam]
            # Don't log "cannot identify image file" to console via log.error/warning
            # because it blocks IO during batch processing of corrupted files.
            err_str = str(e)
            if "cannot identify image" in err_str:
                # Silent fail for known corruption
                pass 
            else:
                log.warning(f"Image processing failed for {image_path}: {e}")
            return None

    def create_thumbnail(self, image_path: str, max_size: int = 400) -> str:
        """Creates a small thumbnail for UI display."""
        try:
            with Image.open(image_path) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((max_size, max_size))
                buffered = io.BytesIO()
                img.convert("RGB").save(buffered, format="JPEG", quality=70)
                return base64.b64encode(buffered.getvalue()).decode('utf-8')
        except Exception:
            return ""
