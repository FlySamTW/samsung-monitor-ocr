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
        self.config = config or {
            "max_size": 4096, 
            "contrast_factor": 1.2,
            "sharpness_factor": 1.5,
            "auto_orient": True,
            "detect_label_card": False  # FIXED: Default to False to keep PURE SOURCE
        }

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

    def process(self, image_path: str) -> dict:
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
                
                # 2. Label Card Detection (NEW)
                if self.config.get("detect_label_card"):
                    img_array = np.array(img)
                    cropped, bbox = self.detect_label_card(img_array)
                    
                    if cropped is not None:
                        img = Image.fromarray(cropped)
                        applied_transforms.append(f"label_crop_{bbox}")
                        log.info(f"[Crop] Extracted label card region")

                # 3. Resize if too large
                max_size = self.config.get("max_size")
                if max_size is not None and (img.width > max_size or img.height > max_size):
                    img.thumbnail((max_size, max_size))
                    applied_transforms.append(f"resize_to_{max_size}")

                # 4. Enhance Contrast
                factor = self.config.get("contrast_factor", 1.0)
                if factor != 1.0:
                    enhancer = ImageEnhance.Contrast(img)
                    img = enhancer.enhance(factor)
                    applied_transforms.append(f"contrast_{factor}")

                # 5. Enhance Sharpness
                factor = self.config.get("sharpness_factor", 1.0)
                if factor != 1.0:
                    enhancer = ImageEnhance.Sharpness(img)
                    img = enhancer.enhance(factor)
                    applied_transforms.append(f"sharpness_{factor}")

                # Encode to Base64
                buffered = io.BytesIO()
                img.convert("RGB").save(buffered, format="JPEG", quality=90)
                img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

                return {
                    "base64": img_b64,
                    "metadata": {
                        "original_size": original_size,
                        "processed_size": img.size,
                        "transforms": applied_transforms
                    }
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
