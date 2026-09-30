/* 未連接後端，資料只保存在此瀏覽器。 */
/* 標記：[CORE MUST] 對應老師要求；[V3 ADD] 為第三版改善；[BACKEND MUST／OPTIONAL] 標示第二組工作及粗估。 */
/* [BACKEND MUST｜NO1｜] 多人同步需共用資料庫/API；掃碼交易需由後端驗證並原子更新。未送出的實體搬運須以銷售系統串接或現場掃碼流程避免漏登。 */
/* [BACKEND MUST｜NO2｜] 伺服器保存每日盤點清單、進度、盤點前後數量、原因及操作人。 */
/* [BACKEND MUST｜NO3｜已含於 FIFO 核心工作] 伺服器使用可信入庫時間計算 FIFO，並拒絕不符建議批次／儲位的掃碼出庫。 */
(() => {
  "use strict";

  const STORAGE_KEY = "zhunan-warehouse-v3-demo";
  const DAY_MS = 86400000;
  const $ = id => document.getElementById(id);
  const makeId = prefix => `${prefix}-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const dateOnly = value => {
    const date = new Date(value);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  };
  const addDays = (days, base = new Date()) => dateOnly(new Date(base.getFullYear(), base.getMonth(), base.getDate() + days));
  const formatTime = value => value ? new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short", hour12: false }).format(new Date(value)) : "—";
  const elapsedDays = value => Math.max(0, Math.floor((new Date(`${dateOnly(new Date())}T00:00:00`) - new Date(`${dateOnly(value)}T00:00:00`)) / DAY_MS));
  const warehouseName = id => `倉庫 ${id}`;
  const locationCode = batch => `LOC-${batch.warehouse}-${String(batch.bin).toUpperCase().replace(/[^A-Z0-9]/g, "")}`;
  const crateCodesFor = (batchId, quantity) => Array.from({ length: quantity }, (_, index) => `${batchId}-BOX-${String(index + 1).padStart(3, "0")}`);
  const oldestFirst = (a, b) => Date.parse(a.receivedAt) - Date.parse(b.receivedAt) || String(a.id).localeCompare(String(b.id));
  const announce = message => { $("app-status").textContent = message; };

  function seedBatch(id, name, warehouse, bin, qty, daysOld, expiryDays) {
    const batch = {
      id, name, warehouse, bin, qty,
      receivedAt: new Date(Date.now() - daysOld * DAY_MS).toISOString(),
      expiryDate: addDays(expiryDays),
      lotNumber: `ZN-${warehouse}-${id.toUpperCase()}`
    };
    batch.crateCodes = crateCodesFor(id, qty);
    return batch;
  }

  function initialState() {
    return {
      batches: [
        seedBatch("seed-01", "甘藍菜", "1", "A-10", 5, 5, 8),
        seedBatch("seed-02", "青江菜", "1", "B-02", 3, 9, 2),
        seedBatch("seed-03", "青江菜", "2", "C-05", 10, 3, 12),
        seedBatch("seed-04", "高麗菜", "2", "D-01", 8, 4, 15),
        seedBatch("seed-05", "番茄", "1", "A-03", 6, 6, 6)
      ],
      transactions: [],
      users: [{ name: "李太太", role: "manager" }, { name: "倉管人員", role: "warehouse" }],
      currentOperator: "李太太",
      safetyLevels: { "1|甘藍菜": 4 },
      agingRules: { "青江菜": 7 },
      dailyCounts: {},
      dailySessions: {},
      pendingInbound: null,
      pendingPick: null
    };
  }

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved && Array.isArray(saved.batches) && Array.isArray(saved.transactions)) {
        saved.users ||= [{ name: "李太太", role: "manager" }];
        saved.safetyLevels ||= {};
        saved.agingRules ||= {};
        saved.dailyCounts ||= {};
        saved.dailySessions ||= {};
        saved.pendingInbound ||= null;
        saved.pendingPick ||= null;
        for (const batch of saved.batches) batch.crateCodes ||= crateCodesFor(batch.id, batch.qty);
        return saved;
      }
    } catch (error) {
      console.warn("無法讀取第三版展示資料，改用範例資料。", error);
    }
    return initialState();
  }

  let state = loadState();
  const operatorSelect = $("current-operator");

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      $("sync-state").textContent = "展示模式：已儲存至這台電腦的瀏覽器，尚未同步到其他員工或後端。";
      return true;
    } catch (error) {
      console.error("無法儲存展示資料。", error);
      $("sync-state").textContent = "儲存失敗：目前操作未能保存。";
      announce("瀏覽器無法儲存資料，請先確認儲存空間。操作未能保存。");
      return false;
    }
  }

  function currentOperator() { return operatorSelect.value.trim(); }
  function activeBatches() { return state.batches.filter(batch => batch.qty > 0); }
  function productNames() { return [...new Set(state.batches.map(batch => batch.name).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-Hant")); }
  function refreshOperatorSelect() {
    operatorSelect.innerHTML = state.users.map(user => `<option value="${esc(user.name)}">${esc(user.name)}（${user.role === "manager" ? "管理者" : "倉管人員"}）</option>`).join("");
    if (!state.users.some(user => user.name === state.currentOperator)) state.currentOperator = state.users[0]?.name || "";
    operatorSelect.value = state.currentOperator;
  }
  function record(kind, batch, quantity, reason = "") {
    state.transactions.unshift({
      id: makeId("TX"), at: new Date().toISOString(), kind,
      name: batch.name, warehouse: batch.warehouse, bin: batch.bin,
      lotNumber: batch.lotNumber || "", quantity, reason,
      operator: currentOperator(),
      operatorRole: state.users.find(user => user.name === currentOperator())?.role || "warehouse"
    });
    state.transactions = state.transactions.slice(0, 2000);
  }

  function expiryStatus(value) {
    if (!value) return "未設定";
    const days = Math.ceil((new Date(`${value}T00:00:00`) - new Date(`${dateOnly(new Date())}T00:00:00`)) / DAY_MS);
    if (days < 0) return "已過期，請確認";
    if (days === 0) return "今日到期";
    if (days <= 3) return `${days} 天內到期`;
    return "正常";
  }

  function renderWarehouseTables() {
    const batches = activeBatches().sort(oldestFirst);
    for (const warehouse of ["1", "2"]) {
      const rows = batches.filter(batch => batch.warehouse === warehouse);
      $(`warehouse${warehouse}-table`).innerHTML = rows.length ? rows.map(batch => `<tr><td>${esc(batch.bin)}</td><td>${esc(batch.name)}</td><td>${batch.qty} 箱</td><td>${esc(batch.lotNumber)}</td><td>${esc(formatTime(batch.receivedAt))}</td><td>${esc(batch.expiryDate || "未設定")}／${esc(expiryStatus(batch.expiryDate))}</td></tr>`).join("") : `<tr><td colspan="6">目前沒有庫存。</td></tr>`;
      $(`warehouse${warehouse}-summary`).textContent = `${rows.length} 個批次，共 ${rows.reduce((sum, batch) => sum + batch.qty, 0)} 箱`;
    }
  }

  function renderAlerts() {
    const expiring = activeBatches().filter(batch => batch.expiryDate && expiryStatus(batch.expiryDate) !== "正常").sort((a, b) => String(a.expiryDate).localeCompare(String(b.expiryDate)));
    $("expiry-alert-table").innerHTML = expiring.length ? expiring.map(batch => `<tr><td>${esc(batch.name)}</td><td>${warehouseName(batch.warehouse)}／${esc(batch.bin)}</td><td>${esc(batch.lotNumber)}</td><td>${esc(expiryStatus(batch.expiryDate))}</td></tr>`).join("") : `<tr><td colspan="4">目前沒有需要提醒的效期。</td></tr>`;

    const aging = activeBatches().filter(batch => Number(state.agingRules[batch.name]) > 0 && elapsedDays(batch.receivedAt) >= Number(state.agingRules[batch.name]));
    $("aging-alert-table").innerHTML = aging.length ? aging.map(batch => `<tr><td>${esc(batch.name)}</td><td>${warehouseName(batch.warehouse)}／${esc(batch.bin)}</td><td>${elapsedDays(batch.receivedAt)} 天</td><td>${Number(state.agingRules[batch.name])} 天</td></tr>`).join("") : `<tr><td colspan="4">目前沒有久放提醒。</td></tr>`;

    const safety = Object.entries(state.safetyLevels).map(([key, level]) => {
      const [warehouse, name] = key.split("|");
      const quantity = activeBatches().filter(batch => batch.warehouse === warehouse && batch.name === name).reduce((sum, batch) => sum + batch.qty, 0);
      return { warehouse, name, quantity, level: Number(level) };
    }).filter(item => item.quantity < item.level);
    $("safety-alert-table").innerHTML = safety.length ? safety.map(item => `<tr><td>${esc(item.name)}</td><td>${warehouseName(item.warehouse)}</td><td>${item.quantity} 箱</td><td>${item.level} 箱</td></tr>`).join("") : `<tr><td colspan="4">目前沒有低於安全庫存的商品。</td></tr>`;
  }

  function renderWasteSummary() {
    const totals = new Map();
    for (const tx of state.transactions) {
      if (tx.kind === "報廢") totals.set(tx.reason || "未填原因", (totals.get(tx.reason || "未填原因") || 0) + Math.abs(tx.quantity));
    }
    const rows = [...totals.entries()].sort((a, b) => b[1] - a[1]);
    $("waste-table").innerHTML = rows.length ? rows.map(([reason, qty]) => `<tr><td>${esc(reason)}</td><td>${qty} 箱</td></tr>`).join("") : `<tr><td colspan="2">尚無報廢紀錄。</td></tr>`;
  }

  function renderActivity() {
    const rows = state.transactions.slice(0, 30);
    $("activity-table").innerHTML = rows.length ? rows.map(tx => `<tr><td>${esc(formatTime(tx.at))}</td><td>${esc(tx.kind)}</td><td>${esc(tx.name)}／${warehouseName(tx.warehouse)} ${esc(tx.bin)}<br>${esc(tx.lotNumber)}</td><td>${tx.quantity > 0 ? "+" : ""}${tx.quantity} 箱</td><td>${esc(tx.reason || "—")}</td><td>${esc(tx.operator)}</td></tr>`).join("") : `<tr><td colspan="6">目前沒有異動紀錄。</td></tr>`;
  }

  function renderDashboard() {
    renderWarehouseTables();
    renderAlerts();
    renderWasteSummary();
    renderActivity();
    const time = new Date().toISOString();
    $("last-updated").dateTime = time;
    $("last-updated").textContent = formatTime(time);
  }

  function refreshProductOptions() {
    $("product-options").innerHTML = productNames().map(name => `<option value="${esc(name)}"></option>`).join("");
    const select = $("out-item");
    const previous = select.value;
    const names = productNames();
    select.innerHTML = `<option value="">請選擇商品</option>${names.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join("")}`;
    if (names.includes(previous)) select.value = previous;
  }

  function switchTab(id) {
    document.querySelectorAll("main > section[data-panel]").forEach(section => { section.hidden = section.id !== id; });
    document.querySelectorAll("button[data-tab]").forEach(button => {
      if (button.dataset.tab === id) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (id === "dashboard") renderDashboard();
    if (id === "outbound") { refreshProductOptions(); renderPickTask(); }
    if (id === "stocktake") renderStocktake();
    if (id === "management") renderManagement();
  }

  function renderManagement() {
    $("user-table").innerHTML = state.users.map(user => `<tr><td>${esc(user.name)}</td><td>${user.role === "manager" ? "管理者" : "倉管人員"}</td></tr>`).join("") || `<tr><td colspan="2">尚無人員。</td></tr>`;
  }

  // [V3 ADD｜NO2 每日盤點] 以日期及批次記錄今日是否完成，讓管理者看得到未盤項目。
  function renderStocktake() {
    const today = dateOnly(new Date());
    $("stocktake-date").dateTime = today;
    $("stocktake-date").textContent = today;
    const warehouse = $("count-warehouse").value;
    const query = $("count-search").value.trim().toLocaleLowerCase();
    let changedSession = false;
    state.dailySessions[today] ||= [];
    const knownIds = new Set(state.dailySessions[today]);
    for (const batch of activeBatches()) {
      if (!knownIds.has(batch.id)) { state.dailySessions[today].push(batch.id); knownIds.add(batch.id); changedSession = true; }
    }
    if (changedSession) saveState();
    const sessionBatches = state.dailySessions[today].map(id => state.batches.find(batch => batch.id === id)).filter(Boolean);
    const counted = sessionBatches.filter(batch => state.dailyCounts[`${today}|${batch.id}`]).length;
    $("stocktake-progress").textContent = `今日已盤 ${counted}／${sessionBatches.length} 個批次${counted === sessionBatches.length ? "，今日清單已完成。" : "，尚有項目待盤點。"}`;
    const batches = sessionBatches.filter(batch => (warehouse === "all" || batch.warehouse === warehouse) && [batch.name, batch.bin, batch.lotNumber].some(value => String(value).toLocaleLowerCase().includes(query)));
    $("count-table").innerHTML = batches.length ? batches.map(batch => {
      const saved = state.dailyCounts[`${today}|${batch.id}`];
      const actual = saved ? saved.actual : batch.qty;
      return `<tr data-count-batch="${esc(batch.id)}"><td>${saved ? `已盤（${esc(formatTime(saved.countedAt))}）` : "待盤"}</td><td>${warehouseName(batch.warehouse)}／${esc(batch.bin)}</td><td>${esc(batch.name)}<br>${esc(batch.lotNumber)}</td><td>${batch.qty} 箱</td><td><label for="actual-${esc(batch.id)}">實際數量</label><input id="actual-${esc(batch.id)}" data-count-actual type="number" min="0" step="1" value="${actual}"></td><td><label for="reason-${esc(batch.id)}">差異原因</label><select id="reason-${esc(batch.id)}" data-count-reason><option value="">選擇原因</option><option value="腐爛報廢" ${saved?.reason === "腐爛報廢" ? "selected" : ""}>腐爛報廢</option><option value="破損報廢" ${saved?.reason === "破損報廢" ? "selected" : ""}>破損報廢</option><option value="盤點差異" ${saved?.reason === "盤點差異" ? "selected" : ""}>盤點差異</option><option value="數量確認" ${saved?.reason === "數量確認" ? "selected" : ""}>數量確認</option></select></td><td><button type="button" data-save-count>儲存盤點</button></td></tr>`;
    }).join("") : `<tr><td colspan="7">目前沒有符合條件的庫存。</td></tr>`;
  }

  function updateCountedCodes(batch, actual) {
    if (actual < batch.qty) batch.crateCodes = batch.crateCodes.slice(0, actual);
    if (actual > batch.qty) batch.crateCodes.push(...crateCodesFor(`${batch.id}-ADJ-${makeId("X")}`, actual - batch.qty));
  }

  $("count-table").addEventListener("click", event => {
    if (!event.target.closest("[data-save-count]")) return;
    const row = event.target.closest("tr[data-count-batch]");
    const batch = state.batches.find(item => item.id === row.dataset.countBatch);
    if (!batch) return;
    const actual = Number(row.querySelector("[data-count-actual]").value);
    const reason = row.querySelector("[data-count-reason]").value;
    if (!Number.isSafeInteger(actual) || actual < 0) { announce("實際數量請輸入 0 或正整數。"); return; }
    const difference = actual - batch.qty;
    if (difference !== 0 && !reason) { announce("數量有差異，請選擇差異原因。"); return; }
    if (!currentOperator()) { announce("請先選擇操作人。"); return; }
    const previousQty = batch.qty;
    const previousCodes = [...batch.crateCodes];
    const countKey = `${dateOnly(new Date())}|${batch.id}`;
    const previousCount = state.dailyCounts[countKey];
    const previousTransactions = [...state.transactions];
    const kind = difference < 0 && ["腐爛報廢", "破損報廢"].includes(reason) ? "報廢" : "盤點調整";
    batch.qty = actual;
    updateCountedCodes(batch, actual);
    if (difference !== 0 || reason) record(kind, batch, difference, reason || "數量確認");
    state.dailyCounts[countKey] = { actual, reason: reason || "數量確認", countedAt: new Date().toISOString(), operator: currentOperator() };
    if (!saveState()) {
      batch.qty = previousQty; batch.crateCodes = previousCodes;
      if (previousCount) state.dailyCounts[countKey] = previousCount; else delete state.dailyCounts[countKey];
      state.transactions = previousTransactions;
      return;
    }
    renderStocktake(); renderDashboard(); renderPickTask();
    announce(kind === "報廢" ? `已盤點並登記報廢 ${Math.abs(difference)} 箱。` : `盤點完成，數量由 ${previousQty} 箱調整為 ${actual} 箱。`);
  });

  $("count-warehouse").addEventListener("change", renderStocktake);
  $("count-search").addEventListener("input", renderStocktake);

  // [V3 ADD｜NO1 入庫確認] 先建立收貨單，掃描儲位及每箱唯一代碼，數量核對後才入帳。
  $("inbound-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const name = String(data.get("item")).trim();
    const warehouse = String(data.get("warehouse"));
    const bin = String(data.get("bin")).trim().toUpperCase();
    const quantity = Number(data.get("quantity"));
    if (!Number.isSafeInteger(quantity) || quantity < 1 || !name || !bin) { announce("請確認商品、儲位和正整數箱數。"); return; }
    const id = makeId("BATCH");
    const batch = {
      id, name, warehouse, bin, qty: quantity,
      lotNumber: `ZN-${warehouse}-${dateOnly(new Date()).replaceAll("-", "")}-${id.slice(-6).toUpperCase()}`,
      expiryDate: String(data.get("expiryDate") || ""),
      receivedAt: "",
      crateCodes: crateCodesFor(id, quantity)
    };
    state.pendingInbound = { batch, scannedCodes: [], locationVerified: false };
    saveState();
    form.reset();
    renderInboundTask();
    announce(`已建立 ${name} 收貨工作。請核對儲位並逐箱掃描。`);
  });

  function renderInboundTask() {
    const task = state.pendingInbound;
    const disabled = !task;
    $("inbound-lot").textContent = task?.batch.lotNumber || "尚未建立";
    $("inbound-location-code").textContent = task ? locationCode(task.batch) : "—";
    $("in-verify-location").disabled = disabled;
    $("in-scan-crate-button").disabled = disabled || !task.locationVerified;
    $("inbound-confirm").disabled = disabled || task.scannedCodes.length !== task.batch.qty;
    $("inbound-cancel").disabled = disabled;
    if (!task) {
      $("inbound-progress").textContent = "尚未建立收貨工作。";
      $("inbound-crate-codes").innerHTML = "";
      return;
    }
    $("inbound-progress").textContent = `已核對 ${task.scannedCodes.length}／${task.batch.qty} 箱；${task.locationVerified ? "儲位已核對。" : "請先核對儲位。"}`;
    $("inbound-crate-codes").innerHTML = task.batch.crateCodes.map(code => `<li>${esc(code)}${task.scannedCodes.includes(code) ? "（已掃描）" : "（待掃描）"}</li>`).join("");
  }

  $("in-verify-location").addEventListener("click", () => {
    const task = state.pendingInbound;
    if (!task) return;
    if ($("in-scan-location").value.trim().toUpperCase() !== locationCode(task.batch)) { announce(`儲位不符，請掃描 ${locationCode(task.batch)}。`); return; }
    task.locationVerified = true;
    saveState(); renderInboundTask();
    $("in-scan-crate").focus();
    announce("儲位核對完成，請逐箱掃描商品箱標籤。");
  });

  $("in-scan-crate-button").addEventListener("click", () => {
    const task = state.pendingInbound;
    if (!task || !task.locationVerified) return;
    const code = $("in-scan-crate").value.trim();
    if (!task.batch.crateCodes.includes(code)) { announce("箱標籤不屬於這張收貨單，請核對商品及箱號。"); return; }
    if (task.scannedCodes.includes(code)) { announce("這箱已經登記過，請勿重複掃描。"); return; }
    task.scannedCodes.push(code);
    $("in-scan-crate").value = "";
    saveState(); renderInboundTask();
    announce(`已核對 ${task.scannedCodes.length}／${task.batch.qty} 箱。`);
  });

  // 多數掃描器會模擬鍵盤輸入並送出 Enter；可掃描後直接觸發核對。
  $("in-scan-location").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); $("in-verify-location").click(); } });
  $("in-scan-crate").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); $("in-scan-crate-button").click(); } });

  $("inbound-confirm").addEventListener("click", () => {
    const task = state.pendingInbound;
    if (!task || task.scannedCodes.length !== task.batch.qty || !task.locationVerified) { announce("請先完成儲位核對及逐箱掃描。"); return; }
    const batch = task.batch;
    batch.receivedAt = new Date().toISOString();
    const previousTransactions = [...state.transactions];
    state.batches.push(batch);
    record("進貨", batch, batch.qty, "收貨掃描完成");
    state.pendingInbound = null;
    if (!saveState()) { state.batches.pop(); state.transactions = previousTransactions; state.pendingInbound = task; return; }
    renderInboundTask(); refreshProductOptions(); renderDashboard();
    announce(`${batch.name} ${batch.qty} 箱已完成入庫，位置為${warehouseName(batch.warehouse)} ${batch.bin}。`);
  });

  $("inbound-cancel").addEventListener("click", () => {
    state.pendingInbound = null; saveState(); renderInboundTask(); announce("已取消收貨工作，庫存未變更。");
  });

  // [V3 ADD｜NO3 FIFO 取貨任務] 每次只允許掃描目前 FIFO 指定批次與儲位，箱條碼代表實際箱數。
  function fifoQueue(name) { return state.batches.filter(batch => batch.name === name && batch.qty > 0).sort(oldestFirst); }
  function makePlan(queue, quantity) {
    let remaining = quantity;
    const plan = [];
    for (const batch of queue) {
      if (remaining <= 0) break;
      const take = Math.min(batch.qty, remaining);
      plan.push({ batchId: batch.id, quantity: take });
      remaining -= take;
    }
    return { plan, remaining };
  }
  function scannedForBatch(batchId) { return state.pendingPick?.scanned.filter(item => item.batchId === batchId).length || 0; }
  function nextPickLine() { return state.pendingPick?.plan.find(line => scannedForBatch(line.batchId) < line.quantity) || null; }
  function currentPickBatch() {
    const line = nextPickLine();
    return line ? state.batches.find(batch => batch.id === line.batchId) : null;
  }

  $("outbound-form").addEventListener("submit", event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("item"));
    const quantity = Number(data.get("quantity"));
    if (!Number.isSafeInteger(quantity) || quantity < 1) { announce("出貨箱數請輸入正整數。"); return; }
    const { plan, remaining } = makePlan(fifoQueue(name), quantity);
    if (remaining > 0) { announce("可用庫存不足，請確認數量。"); return; }
    state.pendingPick = { name, quantity, plan, scanned: [], verifiedBatchId: "" };
    saveState(); renderPickTask();
    announce(`已建立 ${name} ${quantity} 箱 FIFO 取貨工作。請依順序掃描。`);
  });

  function renderPickTask() {
    const task = state.pendingPick;
    const tbody = $("pick-plan-table");
    const ready = task && task.scanned.length === task.quantity;
    if (!task) {
      tbody.innerHTML = `<tr><td colspan="5">尚未建立取貨工作。</td></tr>`;
      $("pick-next-instruction").textContent = "建立出貨需求後，這裡會顯示最早入庫的取貨位置。";
      $("out-verify-location").disabled = true;
      $("out-scan-crate-button").disabled = true;
      $("outbound-confirm").disabled = true;
      $("outbound-cancel").disabled = true;
      $("outbound-progress").textContent = "請先建立取貨工作。";
      $("outbound-scanned-list").innerHTML = "";
      return;
    }
    tbody.innerHTML = task.plan.map((line, index) => {
      const batch = state.batches.find(item => item.id === line.batchId);
      return batch ? `<tr><td>${index + 1}</td><td>${warehouseName(batch.warehouse)}／${esc(batch.bin)}</td><td>${esc(batch.lotNumber)}<br>${esc(formatTime(batch.receivedAt))}</td><td>${line.quantity} 箱</td><td>${scannedForBatch(batch.id)} 箱</td></tr>` : "";
    }).join("");
    const nextBatch = currentPickBatch();
    $("pick-next-instruction").textContent = nextBatch ? `下一步：掃描 ${warehouseName(nextBatch.warehouse)} ${nextBatch.bin}（${nextBatch.lotNumber}）的箱子。` : "需求數量已全部掃描，請確認出庫。";
    $("out-verify-location").disabled = ready || !nextBatch;
    $("out-scan-crate-button").disabled = ready || !nextBatch || task.verifiedBatchId !== nextBatch.id;
    $("outbound-confirm").disabled = !ready;
    $("outbound-cancel").disabled = false;
    $("outbound-progress").textContent = `已掃描 ${task.scanned.length}／${task.quantity} 箱。${task.verifiedBatchId ? "目前儲位已核對。" : "每換一個批次／儲位，請重新核對儲位。"}`;
    $("outbound-scanned-list").innerHTML = task.scanned.map(item => `<li>${esc(item.crateCode)}（${esc(item.lotNumber)}）</li>`).join("") || "<li>尚未掃描箱子。</li>";
  }

  $("out-verify-location").addEventListener("click", () => {
    const task = state.pendingPick;
    const batch = currentPickBatch();
    if (!task || !batch) return;
    if ($("out-scan-location").value.trim().toUpperCase() !== locationCode(batch)) { announce(`儲位不符，請依 FIFO 指示掃描 ${locationCode(batch)}。`); return; }
    task.verifiedBatchId = batch.id;
    saveState(); renderPickTask();
    $("out-scan-crate").focus();
    announce("儲位核對完成，請掃描實際取出的箱子。");
  });

  $("out-scan-crate-button").addEventListener("click", () => {
    const task = state.pendingPick;
    const batch = currentPickBatch();
    if (!task || !batch || task.verifiedBatchId !== batch.id) return;
    const code = $("out-scan-crate").value.trim();
    if (!batch.crateCodes.includes(code)) { announce("這個箱標籤不屬於目前 FIFO 指定批次，請重新確認。"); return; }
    if (task.scanned.some(item => item.crateCode === code)) { announce("這箱已掃描過，不能重複計數。"); return; }
    task.scanned.push({ crateCode: code, batchId: batch.id, lotNumber: batch.lotNumber });
    const line = task.plan.find(item => item.batchId === batch.id);
    if (scannedForBatch(batch.id) >= line.quantity) task.verifiedBatchId = "";
    $("out-scan-crate").value = "";
    saveState(); renderPickTask();
    announce(`已掃描 ${task.scanned.length}／${task.quantity} 箱。`);
  });

  $("outbound-confirm").addEventListener("click", () => {
    const task = state.pendingPick;
    if (!task || task.scanned.length !== task.quantity) { announce("實際掃描箱數與出貨需求不符，不能確認出庫。"); return; }
    const touched = [];
    for (const line of task.plan) {
      const batch = state.batches.find(item => item.id === line.batchId);
      if (!batch || batch.qty < line.quantity) { announce("庫存已變動，請取消此工作並重新建立 FIFO 取貨單。"); return; }
      const codes = task.scanned.filter(item => item.batchId === batch.id).map(item => item.crateCode);
      if (codes.length !== line.quantity || codes.some(code => !batch.crateCodes.includes(code))) { announce("箱標籤資料與庫存不一致，請重新建立取貨工作。"); return; }
      touched.push({ batch, quantity: line.quantity, codes, beforeQty: batch.qty, beforeCodes: [...batch.crateCodes] });
    }
    const previousTransactions = [...state.transactions];
    for (const item of touched) {
      item.batch.qty -= item.quantity;
      item.batch.crateCodes = item.batch.crateCodes.filter(code => !item.codes.includes(code));
      record("出貨", item.batch, -item.quantity, "PDA／掃碼模擬確認");
    }
    state.pendingPick = null;
    if (!saveState()) {
      for (const item of touched) { item.batch.qty = item.beforeQty; item.batch.crateCodes = item.beforeCodes; }
      state.transactions = previousTransactions;
      state.pendingPick = task;
      return;
    }
    renderPickTask(); renderDashboard(); renderStocktake(); refreshProductOptions();
    announce(`已確認出庫 ${task.name} ${task.quantity} 箱；庫存已依 FIFO 扣除。`);
  });

  $("outbound-cancel").addEventListener("click", () => {
    state.pendingPick = null; saveState(); renderPickTask(); announce("已取消取貨工作，庫存未變更。");
  });

  $("out-scan-location").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); $("out-verify-location").click(); } });
  $("out-scan-crate").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); $("out-scan-crate-button").click(); } });

  // [OPTIONAL｜管理加分] 人員名單、門檻和報廢分析；正式登入權限需由後端處理。
  // [BACKEND OPTIONAL｜小至中] 安全水位、久放門檻及報廢彙總需持久化並透過共用 API 提供。
  $("safety-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = $("safety-item").value.trim();
    const warehouse = $("safety-wh").value;
    const quantity = Number($("safety-qty").value);
    if (!name || !Number.isSafeInteger(quantity) || quantity < 0) { announce("請輸入商品及有效安全庫存量。"); return; }
    state.safetyLevels[`${warehouse}|${name}`] = quantity;
    saveState(); renderDashboard(); event.currentTarget.reset(); announce("安全庫存設定已儲存。展示版僅保存在本機。");
  });

  $("aging-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = $("aging-item").value.trim();
    const days = Number($("aging-days").value);
    if (!name || !Number.isSafeInteger(days) || days < 1) { announce("請輸入商品和大於 0 的提醒天數。"); return; }
    state.agingRules[name] = days;
    saveState(); renderDashboard(); event.currentTarget.reset(); announce("久放提醒已儲存。展示版僅保存在本機。");
  });

  // [BACKEND OPTIONAL｜大：3–5+ 人日] 正式帳號登入及角色權限需後端驗證；瀏覽器角色欄不可作為安全控管。
  $("user-form").addEventListener("submit", event => {
    event.preventDefault();
    const name = $("user-name").value.trim();
    const role = $("user-role").value;
    if (!name || state.users.some(user => user.name.toLocaleLowerCase() === name.toLocaleLowerCase())) { announce("請輸入未使用的人員姓名。"); return; }
    state.users.push({ name, role });
    saveState(); refreshOperatorSelect(); renderManagement(); event.currentTarget.reset(); announce(`已新增展示用人員 ${name}。正式帳號需由後端建立。`);
  });

  operatorSelect.addEventListener("change", () => {
    state.currentOperator = currentOperator();
    saveState(); renderManagement(); announce(`目前操作人已切換為 ${state.currentOperator}。`);
  });

  $("dashboard").addEventListener("click", event => {
    const target = event.target.closest("[data-open-tab]");
    if (target) switchTab(target.dataset.openTab);
  });
  document.querySelectorAll("button[data-tab]").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  refreshOperatorSelect();
  refreshProductOptions();
  renderInboundTask();
  renderPickTask();
  renderDashboard();
  renderStocktake();
  renderManagement();
})();
