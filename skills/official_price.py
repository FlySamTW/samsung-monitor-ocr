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
from typing import Optional, Tuple, Dict, Callable, List
from rich.console import Console

from skills.model_catalog_rules import (
    FOLLOWME_BUNDLES,
    FOLLOWME_UNRESOLVED,
    compact_model,
    normalize_followme_family,
    normalize_samsung_model,
)

console = Console()

# 三星產品頁 URL 模板 (使用完整型號)
SAMSUNG_PRODUCT_API = "https://www.samsung.com/tw/api/v1/product/{model_id}/spec"
SAMSUNG_SEARCH_API = "https://www.samsung.com/tw/searchProduct"
# [v19.14] Samsung Product Finder API：一次取得所有顯示器型號與價格
SAMSUNG_FINDER_API = "https://searchapi.samsung.com/v6/front/b2c/product/finder/global"
PCHOME_SEARCH_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"

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
        """[v19.14] 初始化價格管理器（不再清空既有快取，啟動時會用 Finder API 一次性刷新）"""
        try:
            # 若檔案不存在則建立空白檔案
            if not os.path.exists(self.cache_file):
                with open(self.cache_file, 'w', encoding='utf-8') as f:
                    f.write("# Samsung 官方價格快取\n")
                    f.write("# 格式: 型號|價格|來源|時間\n")
                    f.write("# 你可以即時編輯此檔案來修正價格\n")
                    f.write("# ========================================\n")
            # 載入既有快取，避免每次啟動都重新抓價
            self._load_from_txt()
            self.session_fetched.clear()
            self.discontinued_models.clear()  # [v18.71] 清空停產記錄
            console.print("[cyan]🔄 已載入即時價格表快取，啟動後將一次性刷新[/cyan]")
        except Exception as e:
            console.print(f"[yellow]⚠️ 初始化價格管理器失敗: {e}[/yellow]")
    
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

    def _rewrite_cache_file(self):
        """[v19.14] 用目前的 price_cache 重建快取檔案，避免重複行累積"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                f.write("# Samsung 官方價格快取\n")
                f.write("# 格式: 型號|價格|來源|時間\n")
                f.write("# 你可以即時編輯此檔案來修正價格\n")
                f.write("# ========================================\n")
                timestamp = time.strftime("%Y-%m-%d %H:%M")
                for model in sorted(self.price_cache.keys()):
                    price = self.price_cache[model]
                    if price and price > 0:
                        f.write(f"{model}|{price}|finder-api-bulk|{timestamp}\n")
        except Exception as e:
            _log_price_status(f"[yellow]⚠️ 重建價格快取檔案失敗: {e}[/yellow]")

    def _to_full_model(self, model: str) -> str:
        """
        商業型號 → 完整型號
        S24F332EAC → LS24F332EACXZW
        S24DG302EC → LS24DG302ECXZW
        
        規則：
        - 加上 L 前綴
        - 加上 XZW 後綴 (不是 CXZW！因為型號本身可能已有 C 結尾)
        """
        short_model = normalize_samsung_model(model)
        if not re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{5,12}", short_model):
            return compact_model(model)
        return f"L{short_model}XZW"
    
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

    def _fetch_from_pchome_24h(self, model: str) -> Optional[int]:
        """Fallback to PChome 24h Shopping when Samsung Taiwan has no price."""
        model_clean = model.upper().strip()
        queries = [model_clean]
        is_followme = "FOLLOW" in model_clean
        if is_followme:
            family = normalize_followme_family(model_clean)
            if not family or family == FOLLOWME_UNRESOLVED:
                _log_price_status(
                    f"[yellow]FollowMe 型號未細分，略過特定型號價格查詢：{model_clean}[/yellow]"
                )
                return None
            panel_models = list(dict.fromkeys(
                bundle.panel_model
                for bundle in FOLLOWME_BUNDLES
                if bundle.family_model == family
            ))
            queries = panel_models + [family.replace('"', ""), model_clean]
        else:
            short_model = re.sub(r'[A-Z]$', '', model_clean)
            if len(short_model) >= 6 and short_model != model_clean:
                queries.append(short_model)
            base_model = re.sub(r'[A-Z]{1,2}$', '', model_clean)
            if len(base_model) >= 6 and base_model != model_clean and base_model != short_model:
                queries.append(base_model)
        samsung_keywords = {'SAMSUNG', '三星', 'MONITOR', '螢幕', '顯示器', 'SAMSUNG MONITOR'}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json,text/plain,*/*',
            'Referer': 'https://24h.pchome.com.tw/',
        }
        candidates = []
        for query in dict.fromkeys(queries):
            compact_query = re.sub(r"[^A-Z0-9]", "", query.upper())
            try:
                response = requests.get(
                    PCHOME_SEARCH_API,
                    params={"q": query, "page": 1, "sort": "sale/dc"},
                    headers=headers,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                _log_price_status(f"[yellow]PChome 24h price lookup failed for {query}: {e}[/yellow]")
                continue

            products = data.get("prods", []) if isinstance(data, dict) else []
            for product in products or []:
                name = str(product.get("name") or "").upper()
                product_id = str(product.get("Id") or product.get("Idno") or "").upper()
                haystack = re.sub(r"[^A-Z0-9]", "", f"{name} {product_id}")
                if compact_query not in haystack:
                    continue
                if not is_followme:
                    name_upper = name.upper()
                    if not any(kw in name_upper for kw in samsung_keywords):
                        continue
                price = product.get("price") or product.get("originPrice")
                try:
                    price_int = int(str(price).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if 1000 < price_int < 200000:
                    candidates.append(price_int)

        if not candidates:
            _log_price_status(f"[yellow]PChome 24h has no matched price for {model_clean}[/yellow]")
            return None
        final_price = min(candidates)
        _log_price_status(f"[green]PChome 24h reference price {model_clean}: NT${final_price:,}[/green]")
        return final_price
    
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
            # Unknown price stays unknown; do not label products as discontinued.
            if model_clean in self.discontinued_models:
                return None
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

        price = self._fetch_from_pchome_24h(model_clean)
        if price:
            self._save_to_cache(model_clean, price, "pchome-24h")
            return price
        
        # 5. 都沒有 → unknown. Do not infer discontinued from lookup failure.
        self.discontinued_models.add(model_clean)
        _log_price_status(f"[yellow]⚠️ {model_clean} 官網查無價格，標記為未知[/yellow]")
        return None
    
    def validate_price(self, model: str, ocr_price: int) -> Tuple[str, Optional[int]]:
        """
        驗證 OCR 價格與官方價格（精確比對，差一元也算不符）
        
        Returns:
            Tuple[status, official_price]
            status: "match" | "high" | "low" | "unknown"
        """
        if not model or not ocr_price:
            return "unknown", None
        
        official_price = self.get_official_price(model)
        
        if official_price == -1:
            return "unknown", None
        
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
            "discontinued": "?"   # Legacy rows: never emit discontinued in filenames/UI.
        }
        return symbols.get(status, "?")

    def discover_models_from_samsung(self, update_model_list: bool = True) -> List[str]:
        """
        [v19.13] 從三星台灣 monitors 全產品頁面一次性抓取所有顯示器型號，並更新型號表。
        主要只抓 https://www.samsung.com/tw/monitors/all-monitors/ 這一頁，速度接近即時。
        """
        url = "https://www.samsung.com/tw/monitors/all-monitors/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.samsung.com/tw/',
        }

        discovered_short = set()
        discovered_full = set()

        try:
            _log_price_status("[cyan]🌐 正在從 Samsung 全產品頁抓取型號清單...[/cyan]")
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            html = response.text

            # 提取所有 /tw/monitors/.../ 產品連結，並取出最後一段 slug
            # 只保留至少有三層路徑的產品頁，例如 /tw/monitors/gaming/odyssey-...-ls27cg552ecxzw/
            product_urls = re.findall(r'https://www\.samsung\.com(/tw/monitors/[^"\s<>]+/)', html)
            slugs = set()
            for u in product_urls:
                parts = [p for p in u.rstrip('/').split('/') if p]
                # 排除 /tw/monitors/ 根目錄與 /tw/monitors/all-monitors/
                if len(parts) >= 4 and parts[-1] not in ('monitors', 'all-monitors'):
                    slugs.add(parts[-1])

            for slug in slugs:
                # slug 通常長這樣：essential-monitor-s3-27-inch-100hz-ls27d362gacxzw
                segments = slug.split('-')
                if not segments:
                    continue
                last = segments[-1].upper().strip()
                if not last or len(last) < 10:
                    continue

                # 完整型號（例如 LS27D362GACXZW）
                discovered_full.add(last)

                # 嘗試轉換成短型號
                short = self._extract_best_short_model(last)
                if short:
                    discovered_short.add(short)

            # 合併現有型號表與新發現的短型號（只保留短型號，因為 OCR 輸出與後續比對皆以短型號為主）
            existing = set(self._load_model_list())
            all_models = existing | discovered_short

            if update_model_list and all_models:
                try:
                    sorted_models = sorted(all_models, key=lambda x: x.upper())
                    # 先寫入暫存檔再覆蓋，避免寫到一半損毀原檔
                    tmp_file = f"{self.model_list_file}.tmp"
                    with open(tmp_file, 'w', encoding='utf-8') as f:
                        for m in sorted_models:
                            f.write(f"{m}\n")
                    os.replace(tmp_file, self.model_list_file)
                    _log_price_status(f"[green]✅ 型號表已更新：原有 {len(existing)} 筆，新增 {len(all_models - existing)} 筆，共 {len(all_models)} 筆[/green]")
                except Exception as e:
                    _log_price_status(f"[yellow]⚠️ 寫入型號表失敗: {e}[/yellow]")

            return sorted(discovered_short)

        except Exception as e:
            _log_price_status(f"[yellow]⚠️ 抓取 Samsung 型號清單失敗: {e}[/yellow]")
            return []

    def _extract_best_short_model(self, full_code: str) -> Optional[str]:
        """
        從完整官網型號碼提取短型號。
        例如 LS27D362GACXZW -> S27D362GAC；LC34G55TWWCXZW -> C34G55TWW。
        只移除官方前導 L 與區域尾碼 XZW，保留型號本身的完整結尾。
        """
        short = normalize_samsung_model(full_code)
        if re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{5,12}", short):
            return short
        return None

    def _bulk_fetch_prices_from_searchapi(self) -> Dict[str, int]:
        """
        [v19.14] 使用 Samsung Product Finder API 一次性抓取所有顯示器型號與價格。
        回傳 {短型號: 價格}，只回傳有價格的項目。
        """
        result: Dict[str, int] = {}
        try:
            params = {
                "type": "07010000",
                "siteCode": "tw",
                "start": 1,
                "num": 200,
                "sort": "newest",
                "onlyFilterInfoYN": "N",
                "keySummaryYN": "Y",
            }
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
                'Referer': 'https://www.samsung.com/tw/monitors/all-monitors/',
            }
            _log_price_status("[cyan]🌐 正在使用 Samsung Product Finder API 一次性抓取所有顯示器價格...[/cyan]")
            response = requests.get(SAMSUNG_FINDER_API, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()

            product_list = data.get('response', {}).get('resultData', {}).get('productList', [])
            total = data.get('response', {}).get('resultData', {}).get('common', {}).get('totalRecord', len(product_list))

            for product in product_list:
                for model in product.get('modelList', []):
                    full = compact_model(model.get('modelCode', ''))
                    short = normalize_samsung_model(model.get('modelName') or full)
                    if not short or not full:
                        continue

                    # 選擇實際售價：優先促銷價，其次標價
                    price = None
                    promo = model.get('promotionPrice')
                    if promo:
                        try:
                            price = int(promo)
                        except (ValueError, TypeError):
                            pass
                    if not price:
                        raw = model.get('price')
                        if raw:
                            try:
                                price = int(raw)
                            except (ValueError, TypeError):
                                pass

                    if price and 1000 <= price <= 1000000:
                        result[short] = price
                        # 同時記錄完整型號對應價格，供後續比對
                        result[full] = price

            _log_price_status(f"[green]✅ Finder API 抓取完成：共 {total} 個產品，{len(result)} 筆價格[/green]")
        except Exception as e:
            _log_price_status(f"[yellow]⚠️ Finder API 抓取失敗: {e}[/yellow]")
        return result

    def prefetch_all_models(self):
        """
        [v19.14] 啟動時在背景一次性刷新所有顯示器官網價格。
        使用 Samsung Product Finder API（單一請求），不再逐型號慢抓。
        """
        import threading

        def _run():
            # 1. 使用 Finder API 一次性取得所有型號與價格
            prices = self._bulk_fetch_prices_from_searchapi()
            if not prices:
                # 若 Finder API 失敗，改用舊的網頁型號發現作為後備
                self.discover_models_from_samsung(update_model_list=True)
                return

            # 2. 更新型號表（保留既有 + 新增發現的短型號）
            discovered_short = {m for m in prices.keys() if self._is_samsung_model_format(m)}
            existing = set(self._load_model_list())
            all_models = existing | discovered_short
            try:
                sorted_models = sorted(all_models, key=lambda x: x.upper())
                tmp_file = f"{self.model_list_file}.tmp"
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    for m in sorted_models:
                        f.write(f"{m}\n")
                os.replace(tmp_file, self.model_list_file)
                _log_price_status(f"[green]✅ 型號表已更新：原有 {len(existing)} 筆，新增 {len(all_models - existing)} 筆，共 {len(all_models)} 筆[/green]")
            except Exception as e:
                _log_price_status(f"[yellow]⚠️ 寫入型號表失敗: {e}[/yellow]")

            # 3. 更新價格快取並重建檔案，避免重複行累積
            for model, price in prices.items():
                if price and price > 0:
                    self.price_cache[model] = price
            self._rewrite_cache_file()

            _log_price_status(f"[cyan]✅ 背景價格刷新完成：已更新 {len(prices)} 筆價格紀錄[/cyan]")

        threading.Thread(target=_run, daemon=True).start()



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
