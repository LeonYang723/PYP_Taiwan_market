# PYP 蝦皮市場銷量追蹤系統

自動追蹤蝦皮台灣站（shopee.tw）上「PYP」品牌相關商品（卡套/卡夾/保護套等）的市場銷量與商品動態，每日透過 GitHub Actions 自動抓取，並用 GitHub Pages 呈現成一個季報／年報儀表板。

## ⚠️ 開始之前，請務必先讀這段：風險與限制

這套系統抓取資料的方式，是用真的瀏覽器引擎（Playwright + headless Chromium）打開蝦皮網頁，在瀏覽器的 JS 環境裡呼叫蝦皮前端本身在用的「內部搜尋 API」（`shopee.tw/api/v4/search/search_items`），**不是蝦皮官方提供、有正式文件的公開 API**。這是目前唯一能拿到「整個蝦皮平台上其他賣家商品資料」的方式（蝦皮官方的 Open Platform API 只能查詢你自己開通授權的店鋪，查不到別人的店）。這個做法有幾個必須先知道的限制：

1. **穩定性風險**：蝦皮隨時可能改變回傳格式、加上更嚴格的驗證機制，屆時這支爬蟲會失效，需要有人更新程式碼。
2. **條款/合規風險**：這種抓取方式很可能不符合蝦皮的服務條款。要不要用這種方式做市場監控，是你們公司要自行承擔、自行評估的商業決定，這份 README 只負責把技術風險講清楚，不構成法律意見。
3. **封鎖風險**：GitHub Actions 執行環境的出口 IP 屬於雲端機房，比一般家用網路更容易被平台的風控機制盯上而封鎖。**這套系統一開始是用 Python `requests` 直接發 HTTP 請求，結果蝦皮在連線層級（TLS 指紋）就直接判斷「不是真瀏覽器」並回傳 403，改 headers 完全沒用**——後來才改成現在這版，用真的 Chromium 瀏覽器引擎去呼叫 API，讓連線特徵跟真人瀏覽一致。這個版本繞過連線層級的偵測，但蝦皮仍然可能用其他方式偵測（瀏覽器指紋、請求頻率異常等），無法保證長期穩定可用。**如果你發現連續好幾天都抓不到資料，很可能就是被擋了**，請看下面「當爬蟲被封鎖時怎麼辦」。
4. **執行變慢**：因為要啟動真的瀏覽器，GitHub Actions 每次執行大概要 5–10 分鐘（含安裝瀏覽器的時間），比純 HTTP 請求版本慢不少，這是正常現象。

如果之後你們自己開通蝦皮賣家帳號、成為蝦皮的官方合作夥伴，可以另外串接 [Shopee Open Platform](https://open.shopee.com/) 的正式 API 來穩定取得**自己店鋪**的官方銷售數據，那會是完全不同、合法穩定的另一條路，但只能看到自己的店，看不到市場上其他賣家。這套系統目前的設計是市場監控用途，兩者可以並存、不衝突，未來要加官方 API 的模組也不會動到現有的架構。

## 這個系統在做什麼

```
GitHub Actions（每天排程）
   │
   ├─ 1. scraper/shopee_scraper.py
   │      依 config/keywords.yaml 的關鍵字，搜尋蝦皮，抓下符合的商品
   │      （名稱、價格、賣家、累計已售出數量……），存成當天的快照
   │      → data/raw/2026-08-24.jsonl
   │
   ├─ 2. scripts/build_reports.py
   │      把所有快照依時間序列彙整，估算每一季/每一年賣了幾件
   │      → docs/data/*.json
   │
   └─ 3. 自動 commit、push 回 repo
          （data/raw 的原始快照 + docs/data 的彙整報表）

GitHub Pages（docs/ 資料夾）
   └─ index.html 讀取 docs/data/*.json，畫出儀表板
      （季報/年報趨勢圖、商品排行、賣家排行）
```

## 銷量是怎麼算出來的

蝦皮商品頁顯示的「已售出 N 件」，是**商品上架以來的累計銷量**，不是「今天賣了幾件」。這套系統每天抓一次快照，等於在時間軸上對這條累計曲線取樣。要估算「某一季賣了幾件」：

```
該季預估銷量 = 季末前最後一次快照的累計銷量 − 季初前最後一次快照的累計銷量
```

如果系統在季初之前還沒開始追蹤某個商品（例如系統剛上線、或商品是季中才第一次被搜尋到），就沒有「季初基準值」可用，只能退而求其次用「該季第一次快照」當基準——這樣算出來的數字**一定會偏低**（漏掉了季初到我們第一次看到它之間賣掉的量）。這種情況在儀表板上會用「偏低估計」標籤標出來，提醒這是低估值、不是精確值。系統累積的追蹤時間越長，這種偏低估計的情況會越少。

## 快速開始

### 1. 建立你自己的 GitHub repo

這份專案不含 `.git`，你需要自己初始化並推到你的 GitHub 帳號：

```bash
cd pyp-shopee-tracker
git init
git add .
git commit -m "init: PYP 蝦皮市場銷量追蹤系統"
git branch -M main
git remote add origin https://github.com/<你的帳號>/<repo名稱>.git
git push -u origin main
```

（也可以先在 GitHub 網站上開一個空 repo，再照它給的指令 push 上去。）

### 2. 開啟 GitHub Pages

repo 頁面 → **Settings → Pages** → Source 選擇 **Deploy from a branch** → Branch 選 `main`，資料夾選 `/docs` → Save。等一兩分鐘後，儀表板網址會是：

```
https://<你的帳號>.github.io/<repo名稱>/
```

### 3. 確認 GitHub Actions 權限

repo 頁面 → **Settings → Actions → General** → 拉到最下面「Workflow permissions」，選 **Read and write permissions**（排程工作流程要能把抓到的資料自動 commit 回 repo，需要這個權限）。

### 4. 手動跑第一次

repo 頁面 → **Actions** 頁籤 → 左側選「蝦皮 PYP 市場資料抓取與報表更新」→ 右上角 **Run workflow** → Run workflow。跑完後（約 5–10 分鐘，包含安裝瀏覽器的時間，視關鍵字數量而定），檢查：

- `data/raw/` 底下有沒有出現今天日期的 `.jsonl` 檔
- 儀表板網址打開後，是不是已經不是「尚無資料」的畫面

之後它會照 `.github/workflows/scrape.yml` 裡設定的排程（預設每天台北時間早上 9 點）自動執行，不需要手動介入。

## 設定關鍵字

編輯 `config/keywords.yaml`：

```yaml
keywords:
  - "PYP"
  - "PYP 卡套"
  - "PYP 卡夾"
```

每個關鍵字都會各自搜尋一次。關鍵字越精準，抓回來的商品裡「不相關的雜訊」越少。**強烈建議上線後先跑個一兩天，人工檢查 `data/raw/` 抓回來的商品是不是真的都是 PYP 相關商品**，再回頭調整這份清單——蝦皮搜尋是全文比對，太短的關鍵字（例如單獨的「PYP」）可能會混進去一些不相關的商品。

`config/keywords.yaml` 裡另外還有幾個參數可以調：

| 參數 | 說明 |
|---|---|
| `max_pages_per_keyword` | 每個關鍵字最多翻幾頁（每頁 60 筆），預設 3 頁 |
| `request_delay_seconds` | 每次請求之間的隨機延遲秒數區間，數字拉大可以降低被擋機率，但整個排程會跑比較久 |
| `enrich_shop_names` | 是否額外查詢賣家名稱（預設關閉，因為會大幅增加請求量、提高被擋風險）。關閉時儀表板會顯示「賣家 #shopid」並附上賣場連結，人工點進去看名稱即可 |

## 當爬蟲被封鎖時怎麼辦

如果 GitHub Actions 的執行紀錄裡開始出現大量 `403` / `429` 或「疑似被反爬蟲機制擋下」的警告，且儀表板頂端出現「資料可能已過期」的提示，代表抓取已經不穩定或完全失效了。目前這版已經是「真瀏覽器（Playwright）呼叫 API」的做法，如果還是持續被擋，代表蝦皮用的是更進階的偵測（例如瀏覽器指紋、請求頻率／行為模式異常），可以按嚴重程度依序嘗試：

1. **拉長延遲、降低頻率**：把 `config/keywords.yaml` 的 `request_delay_seconds` 調大（例如從 `[3, 7]` 改成 `[10, 20]`），或把 `.github/workflows/scrape.yml` 裡的 cron 從每天一次改成每兩三天一次，讓抓取行為更接近真人瀏覽的節奏，而不是規律的機器人模式。
2. **加代理伺服器 / 住宅 IP**：GitHub Actions 的雲端機房 IP 本身就容易被平台歸類為「可疑流量」，如果懷疑是 IP 信譽問題，可以考慮讓 Playwright 透過代理伺服器（尤其是住宅 IP 的代理服務）連線，而不是直接用 GitHub Actions 的原生 IP。這需要額外訂閱代理服務，並在 `new_page()` 裡的 `browser.launch()` 加上 `proxy` 參數，屬於進階調整，需要另外投入。
3. **改用自架/家用網路執行**：如果長期不穩定，可以考慮把排程改成在自己的電腦或伺服器上用 cron 執行，而不是 GitHub Actions（`scraper/shopee_scraper.py` 和 `scripts/build_reports.py` 都是可以獨立在任何有 Python + Playwright 的機器上執行的腳本，跑完後手動或用你自己的方式 `git push` 回 repo 即可）。
4. **最後手段：改回人工蒐集**：如果自動化抓取長期不可行，可以退回最穩妥的做法——每週/每月人工上蝦皮搜尋 PYP 關鍵字，把看到的商品資訊記錄成一筆快照，存成跟 `data/raw/2026-08-24.jsonl` 一樣的格式（可以參考 `scraper/shopee_scraper.py` 裡 `ProductSnapshot` 的欄位），放進 `data/raw/`，`scripts/build_reports.py` 一樣讀得懂、能繼續產生報表。自動化只是效率手段，資料格式本身不綁定抓取方式。

## 目錄結構

```
pyp-shopee-tracker/
├── config/
│   └── keywords.yaml          # 監控關鍵字與抓取參數設定
├── scraper/
│   └── shopee_scraper.py      # 呼叫蝦皮搜尋，抓每日商品快照
├── scripts/
│   └── build_reports.py       # 把快照彙整成季報/年報 JSON
├── data/
│   └── raw/                   # 每日快照（.jsonl，一天一檔）
├── docs/                      # GitHub Pages 網站根目錄
│   ├── index.html             # 儀表板頁面
│   └── data/                  # build_reports.py 產生的報表 JSON，前端讀這裡
├── .github/workflows/
│   └── scrape.yml             # 每日排程：抓取 → 彙整 → 自動 commit
└── requirements.txt
```

## 本地測試

```bash
pip install -r requirements.txt
playwright install --with-deps chromium   # 第一次要先下載瀏覽器執行檔
python scraper/shopee_scraper.py          # 抓一次快照到 data/raw/
python scripts/build_reports.py           # 重新產生 docs/data/*.json
python -m http.server 8000 --directory docs   # 在本機預覽儀表板
# 打開 http://localhost:8000
```

（這個負責開發這套系統的沙盒環境本身的網路是白名單制，連不到 shopee.tw，所以爬蟲「實際呼叫蝦皮 API」這一段沒辦法在那個環境裡跑過完整流程；`parse_items`／分頁／重試/dedupe 等核心邏輯都已經用模擬資料驗證過沒問題，但 Shopee API 回傳的真實欄位格式是否跟程式解析的一致，還是要在你自己的電腦或 GitHub Actions 上實跑才能確認。如果欄位對不上或抓不到，最可能是蝦皮那邊的 API 回傳格式跟寫這支腳本時不一樣了，需要打開瀏覽器開發者工具的 Network 分頁、實際搜尋一次 PYP，對照真實的請求/回應格式調整 `scraper/shopee_scraper.py` 的欄位解析。）

## 之後可以擴充的方向

- **自己店鋪的官方數據**：串接 Shopee Open Platform API，把「自己開的蝦皮 B2B 賣場」的官方訂單/銷售數據也拉進同一套報表，跟市場監控數據並列呈現。
- **賣家名稱**：把 `enrich_shop_names` 打開，或改成排程結束後每週跑一次批次的賣家名稱查詢（而不是每天都查），在風險和資訊完整度之間取得平衡。
- **價格趨勢圖**：目前的欄位已經有 `price_twd` 的每日快照，可以再加一張「同商品價格隨時間變化」的折線圖，觀察競品的價格策略。
- **異常提醒**：例如某個商品單日銷量暴增、新賣家新上架 PYP 相關商品時，透過 GitHub Actions 發 Email 或 Slack 通知。
