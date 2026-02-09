# -*- coding: utf-8 -*-
"""
官方價格抓取與驗證系統 [v18.74]
- 使用三星產品詳情頁 API 抓取價格
- 商業型號 → 完整型號轉換 (S24F332EAC → LS24F332EACXZW)
- 本地快取到「即時價格表.txt」（方便手動更新）
- 自動發現新型號並加入「型號表.txt」
- [v18.74] 取官網最低價（實際售價），而非原價
- [v18.73] 嚴格驗證型號匹配，避免誤抓其他型號價格
"""

import os
import re
import time
import json
import requests
from typing import Optional, Tuple, Dict, Callable
from rich.console import Console

console = Console()

# 三星產品頁 URL 模板 (使用完整型號)
SAMSUNG_PRODUCT_API = "https://www.samsung.com/tw/api/v1/product/{model_id}/spec"
SAMSUNG_SEARCH_API = "https://www.samsung.com/tw/searchProduct"

# 全域日誌回調函數
_log_callback: Optional[Callable[[str], None]] = None

def set_price_log_callback(callback: Callable[[str], None]):
    """設定價格查詢日誌的回調函數（用於儀錶板顯示）"""
    global _log_callback
    _log_callback = callback

def _log_price_status(message: str):
    """同時輸出到 console 和回調"""
    console.print(message)
    if _log_callback:
        # 移除 rich 格式標記
        clean_msg = re.sub(r'\[.*?\]', '', message)
        _log_callback(clean_msg)

class OfficialPriceManager:
    """官方價格管理器"""
    
    def __init__(self, cache_file: str = "即時價格表.txt", model_list_file: str = "型號表.txt"):
        self.cache_file = cache_file
        self.model_list_file = model_list_file
        self.price_cache: Dict[str, int] = {}
        self.session_fetched: set = set()  # 本次執行期間已嘗試抓取的型號
        self.discontinued_models: set = set()  # [v18.71] 本次執行期間確認停產的型號
        # 不在 init 時載入，等 clear_and_init 被呼叫
    
    def clear_and_init(self):
        """清空「即時價格表.txt」並初始化（每次啟動伺服器時呼叫）"""
        try:
            # 清空即時價格表，只保留標題
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                f.write("# Samsung 官方價格快取 (伺服器啟動時自動清空)\n")
                f.write("# 格式: 型號|價格|來源|時間\n")
                f.write("# 你可以即時編輯此檔案來修正價格\n")
                f.write("# ========================================\n")
            self.price_cache.clear()
            self.session_fetched.clear()
            self.discontinued_models.clear()  # [v18.71] 清空停產記錄
            console.print("[cyan]🔄 已清空即時價格表，準備從官網抓取最新價格[/cyan]")
        except Exception as e:
            console.print(f"[yellow]⚠️ 清空即時價格表失敗: {e}[/yellow]")
    
    def _load_from_txt(self):
        """即時從 TXT 讀取（用戶可能已手動修改）"""
        self.price_cache.clear()
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        parts = line.split('|')
                        if len(parts) >= 2:
                            model = parts[0].strip().upper()
                            try:
                                price = int(parts[1].strip().replace(',', ''))
                                self.price_cache[model] = price
                            except ValueError:
                                continue
            except Exception as e:
                console.print(f"[yellow]⚠️ 讀取價格快取失敗: {e}[/yellow]")
    
    def _load_model_list(self) -> set:
        """載入型號表內容"""
        models = set()
        if os.path.exists(self.model_list_file):
            try:
                with open(self.model_list_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            models.add(line.upper())
            except Exception as e:
                console.print(f"[yellow]⚠️ 讀取型號表失敗: {e}[/yellow]")
        return models
    
    def _is_samsung_model_format(self, model: str) -> bool:
        """
        判斷是否為三星型號格式
        S + 數字開頭，例如 S24F332EAC, S27DG302EC
        """
        if not model:
            return False
        model = model.upper().strip()
        # 匹配 S + 兩位數字 + 任意字母數字
        return bool(re.match(r'^S\d{2}[A-Z0-9]+$', model))
    
    def verify_and_add_model(self, model: str) -> bool:
        """
        驗證型號是否存在於三星官網，若存在則加入型號表
        
        Returns:
            True: 型號有效且已加入（或已存在）
            False: 無法驗證或型號無效
        """
        if not model:
            return False
        
        model_clean = model.upper().strip()
        
        # 1. 先檢查是否已在型號表
        existing_models = self._load_model_list()
        if model_clean in existing_models:
            return True  # 已存在，無需處理
        
        # 2. 檢查是否為三星型號格式
        if not self._is_samsung_model_format(model_clean):
            return False
        
        # 3. 嘗試從官網驗證
        _log_price_status(f"[cyan]🔍 發現新型號 {model_clean}，正在官網驗證...[/cyan]")
        
        price = self._fetch_from_product_page(model_clean)
        if price:
            # 4. 驗證成功，加入型號表
            try:
                with open(self.model_list_file, 'a', encoding='utf-8') as f:
                    f.write(f"\n{model_clean}")
                _log_price_status(f"[green]✅ 已將 {model_clean} 加入型號表.txt (官方價格: NT${price:,})[/green]")
                
                # 同時也寫入即時價格表
                self._save_to_cache(model_clean, price, "auto-discover")
                return True
            except Exception as e:
                _log_price_status(f"[yellow]⚠️ 寫入型號表失敗: {e}[/yellow]")
                return False
        else:
            _log_price_status(f"[yellow]⚠️ {model_clean} 官網查無此型號 (可能已停產)[/yellow]")
            return False
    
    def _save_to_cache(self, model: str, price: int, source: str = ""):
        """儲存到本地快取"""
        try:
            with open(self.cache_file, 'a', encoding='utf-8') as f:
                timestamp = time.strftime("%Y-%m-%d %H:%M")
                f.write(f"{model}|{price}|{source}|{timestamp}\n")
            self.price_cache[model] = price
            _log_price_status(f"[green]💾 已寫入即時價格表: {model} = NT${price:,}[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️ 儲存價格快取失敗: {e}[/yellow]")
    
    def _to_full_model(self, model: str) -> str:
        """
        商業型號 → 完整型號
        S24F332EAC → LS24F332EACXZW
        S24DG302EC → LS24DG302ECXZW
        
        規則：
        - 加上 L 前綴
        - 加上 XZW 後綴 (不是 CXZW！因為型號本身可能已有 C 結尾)
        """
        model_clean = model.upper().strip()
        
        # 如果已經是完整型號
        if model_clean.startswith('L') and model_clean.endswith('XZW'):
            return model_clean
        
        # 加上 L 前綴
        if not model_clean.startswith('L'):
            model_clean = 'L' + model_clean
        
        # 加上 XZW 後綴
        if not model_clean.endswith('XZW'):
            model_clean = model_clean + 'XZW'
        
        return model_clean
    
    def _fetch_from_all_monitors(self, model: str) -> Optional[int]:
        """
        從三星全產品頁面抓取價格
        使用 all-monitors 頁面，一次載入所有顯示器
        """
        url = "https://www.samsung.com/tw/monitors/all-monitors/"
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
                'Referer': 'https://www.samsung.com/tw/',
            }
            
            model_clean = model.upper().strip()
            full_model = self._to_full_model(model_clean)
            
            console.print(f"[dim]🌐 正在搜尋 {model_clean} (完整型號: {full_model})...[/dim]")
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            html = response.text
            
            # 嘗試多種匹配模式
            # 模式1: 完整型號後跟價格 (LS24F332EACXZW ... NT$2,390)
            patterns = [
                # 直接在HTML中找完整型號和價格
                rf'{full_model}.*?NT\$\s*([\d,]+)',
                # 找商業型號
                rf'{model_clean}.*?NT\$\s*([\d,]+)',
                # 找部分型號 (前8字元)
                rf'{model_clean[:8]}[A-Z0-9]*.*?NT\$\s*([\d,]+)',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                if matches:
                    price_str = matches[0].replace(',', '')
                    price = int(price_str)
                    if 1000 < price < 100000:
                        console.print(f"[green]✓ 找到 {model_clean} 價格: NT${price:,}[/green]")
                        return price
            
            # 模式2: 嘗試找 JSON-LD 結構化資料
            json_ld_match = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
            if json_ld_match:
                try:
                    json_data = json.loads(json_ld_match.group(1))
                    if isinstance(json_data, dict) and 'offers' in json_data:
                        price = json_data['offers'].get('price')
                        if price:
                            return int(float(price))
                except:
                    pass
            
            console.print(f"[yellow]⚠️ 官網頁面找不到 {model_clean} 價格[/yellow]")
            return None
            
        except requests.exceptions.RequestException as e:
            console.print(f"[yellow]⚠️ 網路請求失敗: {e}[/yellow]")
            return None
        except Exception as e:
            console.print(f"[yellow]⚠️ 抓取失敗: {e}[/yellow]")
            return None
    
    def _fetch_from_search(self, model: str) -> Optional[int]:
        """
        嘗試直接訪問已知的產品頁 URL 模式
        注意：搜尋頁是 JavaScript 動態載入，無法用 requests 抓取
        """
        model_clean = model.upper().strip()
        full_model = self._to_full_model(model_clean).lower()
        
        # 嘗試常見的 URL 模式
        url_patterns = [
            f"https://www.samsung.com/tw/monitors/gaming/odyssey-g3-g30d-24-inch-180hz-freesync-{full_model}/",
            f"https://www.samsung.com/tw/monitors/gaming/odyssey-g3-g30d-27-inch-180hz-freesync-{full_model}/",
            f"https://www.samsung.com/tw/monitors/gaming/odyssey-g5-g50f-24-inch-165hz-{full_model}/",
            f"https://www.samsung.com/tw/monitors/flat/{full_model}/",
            f"https://www.samsung.com/tw/monitors/full-hd-1080p/{full_model}/",
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        for url in url_patterns:
            try:
                response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
                if response.status_code == 200:
                    html = response.text
                    
                    # 找 JSON 價格
                    json_match = re.search(r'"price"[:\s]+"?([\d,]+)"?', html)
                    if json_match:
                        price = int(json_match.group(1).replace(',', ''))
                        if 1000 < price < 100000:
                            console.print(f"[green]✓ 找到 {model_clean} 官方價格: NT${price:,}[/green]")
                            return price
            except:
                continue
        
        return None
    
    def _fetch_from_product_page(self, model: str) -> Optional[int]:
        """
        從產品詳情頁抓取價格（最可靠的方式）
        [v18.96] 價格邏輯修正：排除折扣金額，取實際售價（通常是次低價或明確標示者）
        
        流程：
        1. 在分類頁找包含「完整型號」的產品連結
        2. 進入產品頁後，再次驗證頁面確實包含此型號
        3. 抓取所有候選價格，過濾掉「省下/Save」
        4. 使用啟發式規則選出正確售價
        """
        model_clean = model.upper().strip()
        full_model = self._to_full_model(model_clean).lower()
        
        # [v18.70] 顯示聯網查詢狀態
        _log_price_status(f"[cyan]🌐 正在聯網查詢 {model_clean} 官方價格...[/cyan]")
        
        # 產品頁面分類
        categories = [
            "full-hd-1080p",
            "gaming", 
            "high-resolution",
            "smart",
            "curved",
            "flat",
            "4k-uhd",
            "oled",
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        }
        
        _log_price_status(f"[dim]🔍 正在查找 {model_clean} 產品頁 (完整型號: {full_model})...[/dim]")
        
        for category in categories:
            try:
                # 先嘗試列出分類頁面，找到正確的產品 URL
                list_url = f"https://www.samsung.com/tw/monitors/{category}/"
                response = requests.get(list_url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    html = response.text
                    
                    # [v18.72] 嚴格匹配：URL 必須包含完整型號
                    # 例如：/tw/monitors/gaming/odyssey-g5-xxx-ls27cg552ecxzw/
                    pattern = rf'(/tw/monitors/[^"]*{re.escape(full_model)}[^"]*/)' 
                    match = re.search(pattern, html, re.IGNORECASE)
                    
                    if match:
                        product_url = "https://www.samsung.com" + match.group(1)
                        _log_price_status(f"[dim]📄 找到產品頁: {product_url}[/dim]")
                        
                        # 抓取產品頁
                        prod_response = requests.get(product_url, headers=headers, timeout=15)
                        if prod_response.status_code == 200:
                            prod_html = prod_response.text
                            
                            # [v18.73] 超嚴格驗證：檢查 URL 和頁面標題
                            if full_model.lower() not in product_url.lower():
                                _log_price_status(f"[yellow]⚠️ URL 驗證失敗：{product_url} 不包含 {full_model}[/yellow]")
                                continue
                            
                            # 頁面標題或主要內容區必須包含型號
                            title_match = re.search(r'<title[^>]*>(.*?)</title>', prod_html, re.I | re.S)
                            h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', prod_html, re.I | re.S)
                            title_text = title_match.group(1) if title_match else ""
                            h1_text = h1_match.group(1) if h1_match else ""
                            
                            # 檢查商業型號（S27CG552EC）
                            if model_clean.lower() not in title_text.lower() and model_clean.lower() not in h1_text.lower():
                                _log_price_status(f"[yellow]⚠️ 標題驗證失敗：{model_clean} 不在頁面標題中[/yellow]")
                                continue
                            
                            # === [v18.96 修正] 價格抓取策略 ===
                            candidates = []
                            
                            # 1. 優先：從 JSON-LD 抓取明確定義的價格
                            # Samsung 官網通常有 "offers": { "price": "14990", ... }
                            json_ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', prod_html, re.DOTALL)
                            for block in json_ld_blocks:
                                try:
                                    data = json.loads(block)
                                    # 遞迴搜尋 "offers" 或直接找 "price"
                                    # (簡化版：直接轉字串找 "price": "xxxx")
                                    # 這裡使用更安全的解析：
                                    if isinstance(data, dict):
                                        offers = data.get('offers')
                                        if isinstance(offers, dict):
                                            p = offers.get('price')
                                            if p:
                                                candidates.append({'val': int(float(p)), 'src': 'json-ld', 'priority': 10})
                                        elif isinstance(offers, list):
                                            for offer in offers:
                                                p = offer.get('price')
                                                if p:
                                                    candidates.append({'val': int(float(p)), 'src': 'json-ld', 'priority': 10})
                                except:
                                    pass

                            # 2. 備用：從 HTML 文字抓取 NT$ 價格
                            # 需要排除 "省下 NT$200" / "Save NT$200"
                            # 用 regex 把 "Save/省下" 附近的排除
                            
                            # 先把 HTML 轉純文字或針對性分割
                            text_content = re.sub(r'<[^>]+>', ' ', prod_html)
                            
                            # 找所有 NT$ 價格
                            # [v18.97 Fix] 增強 regex：允許前綴與價格之間有更多符號
                            # 捕獲前 20 個字元來檢查關鍵字
                            price_matches = re.finditer(r'(.{0,20})\s*NT\$\s*([\d,]+)', text_content, re.IGNORECASE)
                            
                            for pm in price_matches:
                                prefix = pm.group(1)
                                price_str = pm.group(2)
                                try:
                                    val = int(price_str.replace(',', ''))
                                    
                                    # 過濾邏輯
                                    # [v18.97 Fix] 移除 < 2000 門檻（因為折扣金額可能很大）
                                    # 強制檢查關鍵字 "省下", "Save", "折", "Discount", "Recycle", "Trade-in"
                                    if prefix:
                                        prefix_lower = prefix.lower()
                                        if any(k in prefix_lower for k in ['省下', 'save', '折', 'discount', 'recycle', 'trade-in']):
                                            continue
                                    
                                    candidates.append({'val': val, 'src': 'html-regex', 'priority': 5})
                                except:
                                    pass

                            if not candidates:
                                _log_price_status(f"[yellow]⚠️ {model_clean} 頁面沒找到有效價格[/yellow]")
                                continue
                                
                            # === 決策邏輯 ===
                            # 去重
                            unique_prices = sorted(list(set([c['val'] for c in candidates])))
                            
                            if not unique_prices:
                                continue
                                
                            final_price = None
                            
                            # 策略 A: 如果只有一個價格，就用它
                            if len(unique_prices) == 1:
                                final_price = unique_prices[0]
                                _log_price_status(f"[green]✓ 找到單一價格: NT${final_price:,}[/green]")
                            else:
                                # 策略 B: 取有效最低價 (因為我們已濾除折扣額，剩餘通常是 [特價, 原價])
                                final_price = min(unique_prices)
                                _log_price_status(f"[green]✓ 找到多個價格 {unique_prices}，取有效最低價: NT${final_price:,} (已過濾折扣額)[/green]")
                            
                            if final_price:
                                return final_price
                
            except requests.exceptions.Timeout:
                _log_price_status(f"[yellow]⚠️ 連接 {category} 頁面超時[/yellow]")
                continue
            except Exception as e:
                continue
        
        return None
    
    def get_official_price(self, model: str) -> Optional[int]:
        """
        取得官方價格
        流程：
        1. 先查 TXT（用戶可能已手動修改）
        2. TXT 沒有 → 上網抓 → 寫入 TXT
        3. 都沒有 → 返回 None（顯示 ?）
        """
        if not model or model.lower() in ['none', 'null', '']:
            return None
        
        model_clean = model.upper().strip()
        
        # 1. 即時從 TXT 讀取（用戶可能已手動修改）
        self._load_from_txt()
        
        # 2. TXT 有 → 直接使用
        if model_clean in self.price_cache:
            return self.price_cache[model_clean]
        
        # 3. 檢查是否本次執行期間已嘗試上網抓過（避免重複請求）
        if model_clean in self.session_fetched:
            # [v18.71] 檢查是否已確認停產
            if model_clean in self.discontinued_models:
                return -1  # 返回 -1 代表停產
            return None  # 已經嘗試過，官網沒有
        
        self.session_fetched.add(model_clean)
        
        # 4. TXT 沒有 → 上網抓 (優先使用產品分類頁)
        price = self._fetch_from_product_page(model_clean)
        if price:
            self._save_to_cache(model_clean, price, "product-page")
            return price
        
        # 備用：嘗試直接訪問已知 URL 模式
        price = self._fetch_from_search(model_clean)
        if price:
            self._save_to_cache(model_clean, price, "direct-url")
            return price
        
        # 5. 都沒有 → 認定為停產，返回 -1（顯示 -）
        # [v18.71] 記錄為停產，避免重複查詢
        self.discontinued_models.add(model_clean)
        _log_price_status(f"[red]❌ {model_clean} 官網查無此型號 (已停產)[/red]")
        return -1  # 返回 -1 代表停產
    
    def validate_price(self, model: str, ocr_price: int) -> Tuple[str, Optional[int]]:
        """
        驗證 OCR 價格與官方價格（精確比對，差一元也算不符）
        
        Returns:
            Tuple[status, official_price]
            status: "match" | "high" | "low" | "unknown" | "discontinued"
        """
        if not model or not ocr_price:
            return "unknown", None
        
        official_price = self.get_official_price(model)
        
        # [v18.71] 停產型號
        if official_price == -1:
            return "discontinued", None
        
        if not official_price:
            return "unknown", None
        
        # [v18.67 修正] 精確比對，不用範圍
        if ocr_price == official_price:
            return "match", official_price
        elif ocr_price > official_price:
            return "high", official_price
        else:
            return "low", official_price
    
    def get_price_symbol(self, status: str) -> str:
        """取得價格比較符號"""
        symbols = {
            "match": "✓",        # 符合
            "high": "↑",         # 高於官方
            "low": "↓",          # 低於官方
            "unknown": "?",       # 未知
            "discontinued": "-"  # [v18.71] 停產
        }
        return symbols.get(status, "?")


# 全域實例
_price_manager: Optional[OfficialPriceManager] = None

def get_price_manager() -> OfficialPriceManager:
    """取得全域價格管理器實例"""
    global _price_manager
    if _price_manager is None:
        _price_manager = OfficialPriceManager()
    return _price_manager

def try_discover_model(model: str) -> bool:
    """
    嘗試驗證並發現新型號（便捷函數）
    
    若型號為 S+數字 格式且不在型號表中：
    1. 去三星官網驗證是否存在
    2. 若存在，自動加入「型號表.txt」
    
    Returns:
        True: 型號有效（已存在或新加入）
        False: 型號無效或無法驗證
    """
    manager = get_price_manager()
    return manager.verify_and_add_model(model)

def validate_ocr_price(model: str, ocr_price: int) -> dict:
    """
    驗證 OCR 價格的便捷函數
    
    Returns:
        {
            "status": "match" | "high" | "low" | "unknown",
            "symbol": "✓" | "↑" | "↓" | "?",
            "official_price": int | None,
            "ocr_price": int,
            "diff_percent": float | None
        }
    """
    manager = get_price_manager()
    status, official_price = manager.validate_price(model, ocr_price)
    
    result = {
        "status": status,
        "symbol": manager.get_price_symbol(status),
        "official_price": official_price,
        "ocr_price": ocr_price,
        "diff_percent": None
    }
    
    if official_price and ocr_price:
        result["diff_percent"] = round((ocr_price - official_price) / official_price * 100, 1)
    
    return result
