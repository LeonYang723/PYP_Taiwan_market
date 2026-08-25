# PYP 蝦皮市場銷量追蹤系統

追蹤蝦皮台灣站（shopee.tw）上「PYP」品牌相關商品（卡套/卡夾/保護套等）的市場銷量與商品動態，用 GitHub Pages 呈現成一個季報／年報儀表板，並額外提供「不同規格/選項市場需求比例」的推估。

**目前的資料蒐集方式是人工定期填表匯入**，原本設計的每日自動化爬蟲已經測試確認會被蝦皮擋下（詳見下方「自動化爬蟲（目前暫停）」），程式碼還留在 repo 裡，但排程已關閉。這不影響儀表板本身——季報/年報的彙整邏輯、GitHub Pages 網站完全不需要爬蟲跑過才能用，只要有人定期把資料填進 Excel 範本、匯入系統即可。

## 現況總覽

| 項目 | 狀態 |
|---|---|
| 儀表板網站（GitHub Pages） | 可正常運作，讀 `docs/data/*.json` |
| 季報/年報彙整（`scripts/build_reports.py`） | 可正常運作，資料來源改為人工匯入 |
| **人工資料蒐集 Excel 範本** | `manual_data/PYP蝦皮市場手動蒐集表.xlsx`，**目前主要的資料輸入方式** |
| 規格別市場需求推估（評價抽樣法） | 可正常運作，需要人工在 Excel 裡統計評價則數 |
| 自動化爬蟲（`scraper/shopee_scraper.py`）——在 GitHub Actions 上 | **已暫停**：被蝦皮風控機制擋下，研判是雲端機房 IP 被整批封鎖，程式碼保留但排程關閉 |
| 自動化爬蟲——改在本機/家用網路執行 | **測試中**：詳見 [`本機排程設定指南.md`](./本機排程設定指南.md)，測試家用網路 IP 是否能繞過封鎖 |

## 這個系統在做什麼

```
① 人工進已知的 PYP 賣場逐一查看商品，把看到的商品資訊
   填進 manual_data/PYP蝦皮市場手動蒐集表.xlsx
   （卡套類別、卡套尺寸、商品名稱、價格、賣家、該商品總銷量……，選填：規格評價統計）
        │
        ▼
② scripts/import_manual_products.py
   把「商品資料」工作表匯入成當天的快照
   → data/raw/2026-08-24.jsonl
   （格式跟舊版自動化爬蟲輸出的完全相容）

   scripts/import_variant_estimates.py
   把「規格評價統計」工作表的推估結果匯入
   → docs/data/variant_estimates.json
        │
        ▼
③ scripts/build_reports.py
   把 data/raw/ 底下所有快照依時間序列彙整，
   估算每一季/每一年賣了幾件
   → docs/data/{meta,latest,quarterly,annual}.json
        │
        ▼
④ 把 data/raw/ 與 docs/data/ 底下更新的檔案 commit、push 回 repo

GitHub Pages（docs/ 資料夾，Navy 深藍主題）
   ├─ index.html    總覽頁：統計摘要、季報/年報銷量趨勢圖、賣家排行
   └─ products.html 商品資料頁：商品排行（價格/尺寸）、目前追蹤中的商品、規格別需求推估
      （兩頁都讀 docs/data/*.json，共用 assets/style.css、assets/dashboard.js；
      商品數較多的表格上方有分頁下拉選單可以選頁）
```

## 手動資料蒐集流程

這是目前系統實際運作的主要方式，分成三個步驟：填表 → 匯入 → 上傳。

### 目前已知的主要賣場

蝦皮上賣 PYP 相關商品的賣場其實不多，目前已知主要是這兩家：

- **放開那隻貓的腳｜禮物盒｜各式卡牌收納配件**
- **數碼遊戲_CyberGamer**

因為賣場數量少，蒐集資料時**建議直接進這兩個賣場頁面逐一看商品，而不是用關鍵字搜尋**——這樣更精準（不會混進不相關的商品），資料量也不大，土法煉鋼手動填表是可行的。之後如果發現還有其他賣場也在賣 PYP 相關商品，可以再補充進這份清單。

### 1. 填 Excel 範本

範本檔案在 `manual_data/PYP蝦皮市場手動蒐集表.xlsx`，打開後有三個工作表：

**「填寫說明」**：完整的填表說明，第一次填之前建議先讀一次。

**「商品資料」**（主要資料，每次都要填）：每一列 = 一個蝦皮商品頁面。逐一進上面兩個賣場，把每個 PYP 相關商品填進去——**卡套類別**（例如掀蓋式、證件夾式、透明殼等）、**卡套尺寸**（例如一般卡片尺寸、悠遊卡尺寸、多卡收納等）、商品名稱、賣家名稱、商品連結、價格、**該商品總銷量**（商品頁上寫的「已售出 N 件」）。都是商品頁面上直接看得到的數字，不用計算。黃色欄位是輸入欄，第 2 列（綠色）是範例，實際填寫時整列刪掉或蓋掉即可。

> **商品連結請盡量填完整**（例如 `https://shopee.tw/product/1234567/89012345` 這種格式），系統要靠這個網址判斷「這次填的商品」跟「上次填的是不是同一個」，才能算出每一季賣了多少件。如果連結格式解析不出來，系統會退而求其次用「商品名稱＋賣家名稱」當代用 ID——這種情況下，之後每一輪填寫，同一個商品的名稱、賣家名稱都要打得盡量一致。
>
> **卡套類別/卡套尺寸這兩欄可以直接對應到儀表板上的「類別」「尺寸」欄位**，如果同一個尺寸的商品在蝦皮上就是獨立的一個商品頁面（而不是同一頁面裡用選項切換），那麼這裡填的「該商品總銷量」就已經是這個尺寸的精確銷量，不需要再透過下面的「規格評價統計」去估算——只有當一個商品頁面本身把好幾個規格/顏色包在同一頁、只顯示一個總銷量時，才需要用評價抽樣法去拆分。

**「規格評價統計」**（選填，用來推估不同規格/選項各自的市場需求比例）：蝦皮公開頁面不會顯示「這個規格賣了幾件」，只會顯示整個商品的總銷量。這裡用一個變通做法——蝦皮的評價區通常會標示買家當初買的是哪個規格，把最近一批評價（例如最新 30～50 則，或全部，視評價數量多寡）依規格分類數一數則數，填進「抽樣評價則數」欄，其他欄位（佔比、商品累計已售出、推估此規格銷量）都是公式自動算好的，不用手動填。

**這是一個推估值，不是蝦皮公開的精確數字**，前提假設是「留評價的比例在各規格之間差不多」，實際上可能有落差（例如某規格的人比較常留評價）。如果一個商品的評價則數太少（例如低於 10 則），推估的可信度會很低，建議乾脆不填這個商品的規格統計，只填「商品資料」的總銷量就好。儀表板上這部分的數字都會清楚標示「推估值」，不會跟精確銷量數字混在一起看。

填完後，**用 Excel 或 Google 試算表打開存一次**，讓「規格評價統計」的公式重新計算過（如果只用 Numbers 或其他工具編輯過沒重新存過，佔比/推估銷量那幾欄可能還是空的，匯入時會被自動跳過並印出警告）。

### 2. 匯入系統

有兩種方式，看誰比較方便操作：

**方式 A：自己在電腦上跑（如果你的電腦有裝 Python）**

```bash
pip install -r requirements.txt
python scripts/import_manual_products.py manual_data/PYP蝦皮市場手動蒐集表.xlsx
python scripts/import_variant_estimates.py manual_data/PYP蝦皮市場手動蒐集表.xlsx
python scripts/build_reports.py
```

跑完之後，`data/raw/` 底下會多一個當天日期的 `.jsonl` 檔，`docs/data/` 底下的 JSON 都會被重新產生，接著只要把這兩個資料夾裡新的/改變的檔案 commit、push 回 GitHub 即可（GitHub Desktop、`git add data/raw docs/data && git commit -m "更新 PYP 市場資料" && git push`，或直接在 GitHub 網頁上把改變的檔案一個個上傳都可以）。

**方式 B：填完直接把 Excel 傳回來**，交給負責維護這套系統的人（例如透過這個對話）幫忙跑上面同樣的匯入流程，再把更新後的 `data/raw/` 與 `docs/data/` 檔案交給你上傳到 GitHub，或直接幫你 push（如果有 repo 存取權限的話）。

### 3. 確認儀表板更新

上傳完，等 GitHub Pages 重新部署（通常一兩分鐘），打開儀表板網址確認：資料是不是不再顯示「尚無資料」、最上面的「最後更新時間」是不是變成今天。

### 建議頻率

**至少每季一次**（配合季報，最好在每季結束後、下一季開始前的一兩週內填），有餘力的話可以**每月做一次**。資料點越密集，季報的準確度越高——尤其是新商品剛開始追蹤的那一季，如果只有季末一次快照、沒有季初的基準值可以相減，數字會被標記「偏低估計」（詳見下方「銷量是怎麼算出來的」）。

## 銷量是怎麼算出來的

蝦皮商品頁顯示的「已售出 N 件」，是**商品上架以來的累計銷量**，不是「這段期間賣了幾件」。這套系統每次蒐集一次快照，等於在時間軸上對這條累計曲線取樣。要估算「某一季賣了幾件」：

```
該季預估銷量 = 季末前最後一次快照的累計銷量 − 季初前最後一次快照的累計銷量
```

如果系統在季初之前還沒開始追蹤某個商品（例如系統剛上線、或商品是季中才第一次被填進表格），就沒有「季初基準值」可用，只能退而求其次用「該季第一次快照」當基準——這樣算出來的數字**一定會偏低**（漏掉了季初到我們第一次看到它之間賣掉的量）。這種情況在儀表板上會用「偏低估計」標籤標出來，提醒這是低估值、不是精確值。系統累積的追蹤時間越長、蒐集頻率越高，這種偏低估計的情況會越少。

「規格別市場需求推估」（評價抽樣法）用的是另一種估算邏輯：不是靠時間序列相減，而是用「評價則數佔比 × 商品總銷量」反推，詳見上方「手動資料蒐集流程」裡的說明。

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

此時因為還沒有任何資料，儀表板會顯示「尚無資料」，這是正常的，照上面「手動資料蒐集流程」填第一批資料進去即可。

### 3. 填第一批資料

打開 `manual_data/PYP蝦皮市場手動蒐集表.xlsx`，照「手動資料蒐集流程」填、匯入、上傳，儀表板就會開始有資料。

> 如果之後想重新嘗試自動化抓取（例如改用住宅 IP 代理），repo 裡的 `.github/workflows/scrape.yml` 和 `scraper/shopee_scraper.py` 都還在，`.github/workflows/scrape.yml` 裡也有寫怎麼恢復排程，屆時還需要到 **Settings → Actions → General** 把 Workflow permissions 設成 **Read and write permissions**，讓排程能自動 commit 資料回 repo。

## 目錄結構

```
pyp-shopee-tracker/
├── config/
│   └── keywords.yaml                # （自動化爬蟲用）監控關鍵字與抓取參數設定
├── manual_data/
│   └── PYP蝦皮市場手動蒐集表.xlsx    # 人工資料蒐集範本，主要的資料輸入方式
├── scraper/
│   └── shopee_scraper.py            # 自動化爬蟲（GitHub Actions 排程已暫停，程式碼保留）
├── local_run/
│   ├── run_scraper.bat              # 在本機/家用網路手動或排程執行爬蟲用的批次檔（Windows）
│   └── logs/                        # 本機執行紀錄（latest_run.log / history.log）
├── scripts/
│   ├── import_manual_products.py    # 把「商品資料」工作表匯入成 data/raw/<日期>.jsonl
│   ├── import_variant_estimates.py  # 把「規格評價統計」工作表匯入成 docs/data/variant_estimates.json
│   ├── build_manual_template.py     # 重新產生空白版 Excel 範本（平常不需要跑）
│   └── build_reports.py             # 把快照彙整成季報/年報 JSON
├── data/
│   └── raw/                         # 每次蒐集的快照（.jsonl，一天一檔）
├── docs/                            # GitHub Pages 網站根目錄
│   ├── index.html                   # 總覽頁（統計摘要、銷量趨勢圖、賣家排行）
│   ├── products.html                # 商品資料頁（商品排行含價格/尺寸、最新快照、規格別需求推估）
│   ├── assets/
│   │   ├── style.css                # 兩頁共用樣式（Navy 深藍主題）
│   │   └── dashboard.js             # 兩頁共用邏輯（資料載入、格式化、分頁下拉選單）
│   └── data/                        # build_reports.py / import_variant_estimates.py 產生的 JSON，前端讀這裡
├── .github/workflows/
│   └── scrape.yml                   # 自動化爬蟲排程（目前已關閉，僅供手動測試）
├── 本機排程設定指南.md               # 在自己電腦/家用網路測試並設定自動排程的步驟說明
└── requirements.txt
```

## 本地測試

```bash
pip install -r requirements.txt

# 人工匯入流程（目前主要的資料來源）
python scripts/import_manual_products.py manual_data/PYP蝦皮市場手動蒐集表.xlsx
python scripts/import_variant_estimates.py manual_data/PYP蝦皮市場手動蒐集表.xlsx
python scripts/build_reports.py

# 在本機預覽儀表板
python -m http.server 8000 --directory docs
# 打開 http://localhost:8000
```

## 自動化爬蟲（目前暫停）

以下內容是這套系統最初的設計——每日透過 GitHub Actions 自動抓取蝦皮資料，**目前已確認會被蝦皮擋下而暫停排程**，保留在這裡當作技術紀錄，也方便之後如果想重新嘗試（例如改用住宅 IP 代理）時參考。

抓取資料的方式，是用真的瀏覽器引擎（Playwright + headless Chromium）打開蝦皮網頁，在瀏覽器的 JS 環境裡呼叫蝦皮前端本身在用的「內部搜尋 API」（`shopee.tw/api/v4/search/search_items`），**不是蝦皮官方提供、有正式文件的公開 API**（蝦皮官方的 Open Platform API 只能查詢自己開通授權的店鋪，查不到別人的店）。這個做法有幾個限制：

1. **穩定性/合規風險**：這種抓取方式很可能不符合蝦皮的服務條款，且蝦皮隨時可能改變機制讓爬蟲失效。要不要用這種方式做市場監控是商業決定，這份 README 不構成法律意見。
2. **封鎖風險（已證實發生）**：抓取方式前後調整過三版，每次都是蝦皮升級一次防護才跟著改，但**最終三版都被擋下**：
   - 第一版用 Python `requests` 直接發 HTTP 請求 → 蝦皮在**連線層級（TLS 指紋）**判斷「不是真瀏覽器」，直接回 403。
   - 第二版改用 Playwright 開真瀏覽器，在頁面 JS 裡自己組 API 網址呼叫 `fetch()` → 403 不見了，但蝦皮的 API 改回傳一個看似正常、其實是**風控系統錯誤物件**（`{"error": 90309999, ...}`）的 JSON，沒有真正的商品資料。
   - 第三版改成讓 Playwright **導航到真正的搜尋頁面**，讓蝦皮自己的前端完整跑一次，攔截它自己發出的 API 回應，而不是自己另外組網址呼叫 → **仍然回傳一模一樣的風控錯誤代碼 90309999**。

   三版方式在請求的「真實程度」上一次比一次高（從純 HTTP → 真瀏覽器但假請求 → 真瀏覽器真請求），結果完全一樣，研判問題不在請求本身怎麼發，而是**GitHub Actions 雲端機房的出口 IP／裝置指紋已經被蝦皮整批封鎖**——這種等級的封鎖不是調整請求標頭、延遲時間能解決的，需要換一個不同信譽等級的 IP（例如住宅 IP 代理）才有機會繞過，但這已經超出單純程式調整的範圍，需要額外投入（訂閱代理服務、承擔更高的合規風險）。
3. **執行變慢**：因為要啟動真的瀏覽器，GitHub Actions 每次執行大概要 5–10 分鐘（含安裝瀏覽器的時間）。

如果之後想重新嘗試，可以參考的方向（依成本/風險由低到高）：

1. **加代理伺服器 / 住宅 IP**：在 `scraper/shopee_scraper.py` 的 `new_page()` 裡 `browser.launch()` 加上 `proxy` 參數，改用住宅 IP 代理服務連線，而不是直接用 GitHub Actions 的原生 IP。這需要額外訂閱代理服務。
2. **改用自架/家用網路執行**：把排程改成在自己的電腦或伺服器上執行，而不是 GitHub Actions（`scraper/shopee_scraper.py` 和 `scripts/build_reports.py` 都是可以獨立在任何有 Python + Playwright 的機器上執行的腳本），家用 IP 的信譽通常比雲端機房好。**這條路目前正在測試**，Windows 使用者可以照 [`本機排程設定指南.md`](./本機排程設定指南.md) 的步驟設定（含一次性環境安裝、`local_run/run_scraper.bat` 手動測試腳本、Windows 工作排程器排程設定），先手動測試一次確認家用網路真的沒被擋，再考慮設定自動排程。
3. **恢復排程**：確認上述方式有效後，把 `.github/workflows/scrape.yml` 裡註解掉的 `schedule:` 區塊取消註解即可恢復每日排程，並到 repo 的 **Settings → Actions → General** 把 Workflow permissions 設成 **Read and write permissions**。

資料格式本身不綁定抓取方式——`scraper/shopee_scraper.py` 輸出的 `data/raw/<日期>.jsonl` 格式，跟 `scripts/import_manual_products.py` 從 Excel 匯入產生的格式完全相容，`scripts/build_reports.py` 不需要知道資料是自動抓的還是人工填的，兩種方式可以交替使用、甚至同時併用（例如自動化偶爾能跑通的那幾天照樣會被納入計算）。

## 之後可以擴充的方向

- **自己店鋪的官方數據**：串接 Shopee Open Platform API，把「自己開的蝦皮 B2B 賣場」的官方訂單/銷售數據也拉進同一套報表，跟市場監控數據並列呈現。
- **價格趨勢圖**：目前的欄位已經有 `price_twd` 的每次快照，可以再加一張「同商品價格隨時間變化」的折線圖，觀察競品的價格策略。
- **匯入流程簡化**：如果每次都要手動執行兩支匯入腳本、再手動 push 覺得麻煩，可以考慮做一個簡單的網頁表單或 Google 試算表 + Apps Script，讓填表跟匯入合併成一步。
- **自動化重新嘗試**：如果之後有住宅 IP 代理資源，可以照上方「自動化爬蟲（目前暫停）」的步驟重新啟用排程，跟人工匯入並存，互相補足彼此的資料空窗期。
