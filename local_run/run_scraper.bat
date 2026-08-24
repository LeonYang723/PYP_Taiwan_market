@echo off
setlocal
rem 這支批次檔要放在 repo 底下的 local_run\ 資料夾裡，會自動切回 repo 根目錄執行。
cd /d "%~dp0.."

if not exist "local_run\logs" mkdir "local_run\logs"
set LATEST=local_run\logs\latest_run.log
set HISTORY=local_run\logs\history.log

echo ============================================ > "%LATEST%"
echo   PYP 蝦皮爬蟲 - 本機執行紀錄 >> "%LATEST%"
echo   執行時間: %date% %time% >> "%LATEST%"
echo ============================================ >> "%LATEST%"

echo [1/2] 執行蝦皮搜尋爬蟲... >> "%LATEST%"
python scraper\shopee_scraper.py >> "%LATEST%" 2>&1
set SCRAPE_EXIT=%ERRORLEVEL%

echo [2/2] 重新產生季報/年報 JSON... >> "%LATEST%"
python scripts\build_reports.py >> "%LATEST%" 2>&1

echo. >> "%LATEST%"
if %SCRAPE_EXIT% NEQ 0 (
    echo 警告：爬蟲結束碼 %SCRAPE_EXIT%（非 0 代表這次很可能被擋下或設定有誤），請往上檢查詳細紀錄。 >> "%LATEST%"
) else (
    echo 爬蟲執行成功，沒有被擋下的跡象。 >> "%LATEST%"
)

type "%LATEST%" >> "%HISTORY%"
echo. >> "%HISTORY%"

echo 完成！這次執行的紀錄寫在：%LATEST%
echo 接下來請打開 GitHub Desktop，確認 data\raw 和 docs\data 底下有沒有新的變動，
echo 有變動的話寫個 commit 說明、按 Commit 再按 Push origin，儀表板網站就會更新。
pause
