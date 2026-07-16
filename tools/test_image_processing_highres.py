import base64, io, tempfile, unittest
from pathlib import Path
from PIL import Image
from skills.image_processing import ImageProcessor

class HighResolutionEvidenceTests(unittest.TestCase):
    def test_existing_message_assembly_consumes_supplemental_crops(self):
        source = (Path(__file__).parents[1] / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        self.assertIn("label_b64 = processed_image.get('label_base64')", source)
        self.assertIn("bottom_label_b64 = processed_image.get('bottom_label_base64')", source)
        self.assertIn("bottom_center_b64 = processed_image.get('bottom_center_base64')", source)
        self.assertIn('user_images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{full_image_b64}"}})', source)
        self.assertIn('data:image/jpeg;base64,{bottom_label_b64}', source)
        self.assertIn('data:image/jpeg;base64,{bottom_center_b64}', source)
        self.assertIn("messages = build_ocr_messages(system_prompt, user_content, ocr_attempt, previous_results)", source)

    def test_crop_is_from_original_and_exceeds_full_scene_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.jpg"
            Image.new("RGB", (5000, 3000), (20, 80, 140)).save(path, quality=100)
            result = ImageProcessor({"max_dimensions": (2560, 1440), "detect_label_card": True}).process(str(path))
            crop = Image.open(io.BytesIO(base64.b64decode(result["bottom_center_base64"])))
            full = Image.open(io.BytesIO(base64.b64decode(result["base64"])))
            self.assertEqual(full.size, (2560, 1536))
            self.assertEqual(crop.size, (4000, 1920))
            self.assertIn("resize_long_edge_2560", " ".join(result["metadata"]["transforms"]))
            self.assertLessEqual(sum(bool(result.get(key)) for key in ("label_base64", "bottom_label_base64", "bottom_center_base64")), 3)

    def test_normal_sized_photo_keeps_existing_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "normal.jpg"
            Image.new("RGB", (1200, 800), (1, 2, 3)).save(path, quality=95)
            result = ImageProcessor({"max_dimensions": (2560, 1440), "detect_label_card": False}).process(str(path))
            self.assertEqual(result["metadata"]["processed_size"], (1200, 800))
            self.assertIsNone(result["bottom_label_base64"])
            self.assertIsNone(result["bottom_center_base64"])

    def test_retry_tiles_cover_left_center_and_right_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.jpg"
            Image.new("RGB", (4032, 2268), (30, 40, 50)).save(path, quality=100)
            processor = ImageProcessor({"max_dimensions": (2560, 1440), "detect_label_card": True})
            first = processor.process(str(path), evidence_attempt=1)
            second = processor.process(str(path), evidence_attempt=2)
            third = processor.process(str(path), evidence_attempt=3)
            self.assertIsNotNone(first["bottom_center_base64"])
            self.assertEqual([x["label"] for x in first["scene_tiles"]], ["scene_center"])
            self.assertEqual([x["label"] for x in second["scene_tiles"]], ["scene_left", "scene_center", "scene_right"])
            self.assertEqual([x["label"] for x in third["scene_tiles"]], ["scene_left", "scene_center", "scene_right"])
            self.assertIsNone(second["bottom_center_base64"])
            self.assertIsNone(third["bottom_center_base64"])
            self.assertEqual(first["scene_tiles"][0]["bbox"], (1048, 0, 1935, 2268))
            self.assertEqual(second["scene_tiles"][1]["bbox"], (1048, 0, 1935, 2268))
            self.assertEqual(second["scene_tiles"][2]["bbox"], (1612, 0, 2419, 2268))
            self.assertEqual(third["scene_tiles"][2]["bbox"], (2096, 0, 1935, 2268))
            self.assertLessEqual(len(second["scene_tiles"]), 3)
            self.assertLessEqual(len(third["scene_tiles"]), 3)
            for tile in first["scene_tiles"] + second["scene_tiles"] + third["scene_tiles"]:
                self.assertEqual(tile["size"][1], 2268)
                self.assertNotIn("base64", third["metadata"]["scene_tiles"][0])

if __name__ == "__main__": unittest.main()
