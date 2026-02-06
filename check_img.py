import os
import base64
from PIL import Image

img_path = '商化照片-202512/M-南投縣-南投市-Q哥-南投家福-200.jpg'

# 原始檔案
size = os.path.getsize(img_path)
print(f'原始檔案大小: {size:,} bytes ({size/1024:.1f} KB)')

# Base64
with open(img_path, 'rb') as f:
    raw = f.read()
    b64 = base64.b64encode(raw).decode()
print(f'Base64 長度: {len(b64):,} chars')

# 圖片尺寸
img = Image.open(img_path)
print(f'圖片尺寸: {img.size[0]}x{img.size[1]}')
