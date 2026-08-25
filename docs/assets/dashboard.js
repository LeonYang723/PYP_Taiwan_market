/* PYP 蝦皮市場儀表板 — 共用邏輯：資料載入、格式化、分頁 */
"use strict";

const fmtInt = (n) => (n === null || n === undefined) ? "–" : n.toLocaleString("zh-TW");
const fmtPrice = (n) => (n === null || n === undefined) ? "–" : "NT$" + n.toLocaleString("zh-TW", { maximumFractionDigits: 0 });

async function loadJSON(path) {
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

function daysSinceDateString(dateStr) {
  // dateStr is a plain "YYYY-MM-DD" (no time/timezone) date, e.g. meta.last_date
  const then = new Date(dateStr + "T00:00:00+08:00");
  const now = new Date();
  return Math.floor((now - then) / (1000 * 60 * 60 * 24));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function shopLabel(row) {
  const name = row.shop_name;
  const url = row.shop_url || (row.shopid ? `https://shopee.tw/shop/${row.shopid}` : "#");
  const text = name ? name : (row.shopid ? `賣家 #${row.shopid}` : "未知賣家");
  return `<a href="${url}" target="_blank" rel="noopener">${escapeHtml(text)}</a>`;
}

// 商品名稱通常是蝦皮 SEO 關鍵字堆疊出來的長標題（例如「數碼遊戲 透明卡套 寶可夢卡套
// 遊戲王卡套 七龍珠卡套…｜規格：35PT 透明卡套(1包/25個)」），這裡擷取「重點文字」給
// 表格顯示：標題最前面的核心詞 + 規格描述（如果有），完整原始標題保留在連結的滑鼠
// 提示文字（title attribute）裡，不會遺失資訊。
function shortenItemName(name) {
  if (!name) return "";
  const s = String(name).trim();
  const specMatch = s.match(/[｜|]\s*規格[:：]\s*(.+)$/);
  const base = s.split(/[｜|]/)[0].trim();
  const words = base.split(/\s+/).filter(Boolean);
  let short = "";
  for (const w of words) {
    if (short && (short.length + w.length + 1 > 16)) break;
    short = short ? short + " " + w : w;
    if (short.length >= 10) break;
  }
  if (!short) short = base.slice(0, 16);
  if (specMatch) {
    let spec = specMatch[1].trim();
    if (spec.length > 26) spec = spec.slice(0, 26) + "…";
    return `${short}｜${spec}`;
  }
  return short + (base.length > short.length ? "…" : "");
}

function itemNameCell(row) {
  const full = escapeHtml(row.item_name || "");
  const short = escapeHtml(shortenItemName(row.item_name));
  return `<a href="${escapeHtml(row.url || "#")}" target="_blank" rel="noopener" title="${full}">${short}</a>`;
}

// ---- 分頁下拉選單 ----
// 把 rows 依 pageSize 切頁，selectEl 是頁碼下拉選單，切換時呼叫 renderFn(該頁的資料列)。
// infoEl（選填）顯示總筆數。回傳一個 refresh() 供資料來源換掉後（例如切換季/年）重新分頁用。
function setupPager(selectEl, infoEl, rows, pageSize, renderFn) {
  function build(currentRows) {
    const total = currentRows.length;
    const pageCount = Math.max(1, Math.ceil(total / pageSize));

    selectEl.innerHTML = "";
    for (let i = 0; i < pageCount; i++) {
      const startN = i * pageSize + 1;
      const endN = Math.min(total, (i + 1) * pageSize);
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = pageCount > 1 ? `第 ${i + 1} 頁（${startN}–${endN}）` : `全部 ${total} 筆`;
      selectEl.appendChild(opt);
    }
    selectEl.style.display = pageCount > 1 ? "" : (total > 0 ? "none" : "none");

    function renderPage() {
      const page = Number(selectEl.value || 0);
      const start = page * pageSize;
      const pageRows = currentRows.slice(start, start + pageSize);
      renderFn(pageRows, start);
      if (infoEl) {
        infoEl.textContent = total === 0 ? "" : `共 ${total.toLocaleString("zh-TW")} 筆`;
      }
    }
    selectEl.onchange = renderPage;
    renderPage();
  }

  build(rows);
  return { refresh: build };
}

// ---- 賣家下拉篩選 ----
// 從一組商品列（需含 shopid / shop_name）算出不重複的賣家清單，依商品數量排序。
// 目前系統只追蹤一個賣家，但架構上已經支援之後新增賣場時自動出現在下拉選單裡，
// 不需要再改前端程式碼。
function buildShopOptions(rows) {
  const map = new Map();
  (rows || []).forEach((r) => {
    if (r.shopid === null || r.shopid === undefined) return;
    const key = String(r.shopid);
    if (!map.has(key)) {
      map.set(key, { key, name: r.shop_name || `賣家 #${r.shopid}`, count: 0 });
    }
    map.get(key).count += 1;
  });
  return Array.from(map.values()).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-Hant"));
}

// directoryRows：用來決定下拉選單「有哪些賣家可選」的資料來源（建議用 latest.json，
// 因為它代表目前完整的賣家名錄，不會因為切換季/年而讓賣家清單忽多忽少）。
// onChange(selectedShopKey) 在選單初始化、以及每次使用者切換時都會被呼叫一次。
function setupShopFilter(selectEl, directoryRows, onChange) {
  const shops = buildShopOptions(directoryRows);
  selectEl.innerHTML = "";

  const allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = shops.length > 1 ? `全部賣家（${shops.length} 家）` : "全部賣家";
  selectEl.appendChild(allOpt);

  shops.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.key;
    opt.textContent = s.name;
    selectEl.appendChild(opt);
  });

  selectEl.onchange = () => onChange(selectEl.value);
  onChange(selectEl.value);
}

function filterByShop(rows, shopKey) {
  if (!shopKey) return rows || [];
  return (rows || []).filter((r) => String(r.shopid ?? "") === shopKey);
}

// ---- 頂部導覽列 active 狀態 ----
function markActiveNav(pageKey) {
  document.querySelectorAll(".nav-links a[data-nav]").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === pageKey);
  });
}

// ---- 各期預估銷量趨勢圖（長條圖） ----
function renderTrendChart(periods, svgId, emptyElId) {
  const svg = document.getElementById(svgId);
  const emptyEl = document.getElementById(emptyElId);
  svg.innerHTML = "";

  if (!periods || periods.length === 0) {
    emptyEl.style.display = "block";
    svg.style.display = "none";
    return;
  }
  emptyEl.style.display = "none";
  svg.style.display = "block";

  const W = Math.max(480, periods.length * 90);
  const H = 220;
  const padL = 46, padR = 16, padT = 16, padB = 32;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);

  const maxVal = Math.max(1, ...periods.map(p => p.total_sold_estimate || 0));
  const innerH = H - padT - padB;
  const innerW = W - padL - padR;
  const barSlot = innerW / periods.length;
  const barW = Math.min(48, barSlot * 0.5);

  const ns = "http://www.w3.org/2000/svg";
  const g = document.createElementNS(ns, "g");

  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const y = padT + innerH - (innerH * i / steps);
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", padL); line.setAttribute("x2", W - padR);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("class", i === 0 ? "baseline" : "grid-line");
    g.appendChild(line);

    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", padL - 8);
    label.setAttribute("y", y + 4);
    label.setAttribute("text-anchor", "end");
    label.textContent = Math.round(maxVal * i / steps).toLocaleString("zh-TW");
    g.appendChild(label);
  }

  periods.forEach((p, i) => {
    const val = p.total_sold_estimate || 0;
    const barH = maxVal > 0 ? (val / maxVal) * innerH : 0;
    const x = padL + i * barSlot + (barSlot - barW) / 2;
    const y = padT + innerH - barH;

    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", y);
    rect.setAttribute("width", barW);
    rect.setAttribute("height", Math.max(barH, 1));
    rect.setAttribute("rx", 4);
    rect.setAttribute("fill", "var(--series-1)");
    const title = document.createElementNS(ns, "title");
    title.textContent = `${p.period}：預估銷量 ${val.toLocaleString("zh-TW")} 件${p.has_partial_data ? "（含偏低估計）" : ""}`;
    rect.appendChild(title);
    g.appendChild(rect);

    if (p.has_partial_data) {
      const mark = document.createElementNS(ns, "text");
      mark.setAttribute("x", x + barW / 2);
      mark.setAttribute("y", y - 6);
      mark.setAttribute("text-anchor", "middle");
      mark.setAttribute("fill", "var(--status-warn)");
      mark.textContent = "≈";
      g.appendChild(mark);
    }

    const valLabel = document.createElementNS(ns, "text");
    valLabel.setAttribute("class", "bar-value");
    valLabel.setAttribute("x", x + barW / 2);
    valLabel.setAttribute("y", y - (p.has_partial_data ? 20 : 6));
    valLabel.setAttribute("text-anchor", "middle");
    valLabel.textContent = val.toLocaleString("zh-TW");
    g.appendChild(valLabel);

    const xLabel = document.createElementNS(ns, "text");
    xLabel.setAttribute("x", x + barW / 2);
    xLabel.setAttribute("y", H - padB + 18);
    xLabel.setAttribute("text-anchor", "middle");
    xLabel.textContent = p.period;
    g.appendChild(xLabel);
  });

  svg.appendChild(g);
}
