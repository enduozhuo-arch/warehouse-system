(() => {
  "use strict";

  const API_BASE = "http://127.0.0.1:8000";
  const DAY_MS = 86400000;
  const $ = id => document.getElementById(id);
  const state = {
    batches: [], transactions: [], products: [], users: [],
    alerts: { expiry: [], aging: [], safety: [] },
    wasteSummary: [], safetyLevels: [], agingRules: [],
    stocktake: { items: [], total: 0, counted: 0, completed: false },
    pendingInbound: null, pendingPick: null,
    currentOperator: sessionStorage.getItem("zhunan-warehouse-operator") || ""
  };
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
  const dateOnly = value => {
    const date = new Date(value);
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
  };
  const formatTime = value => value
    ? new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short", hour12: false }).format(new Date(value))
    : "—";
  const warehouseName = id => "倉庫 " + id;
  const announce = message => { $("app-status").textContent = message; };
  const activeBatches = () => state.batches.filter(batch => Number(batch.qty) > 0);
  const productNames = () => state.products.length
    ? [...state.products].sort((a, b) => a.localeCompare(b, "zh-Hant"))
    : [...new Set(state.batches.map(batch => batch.name))].sort((a, b) => a.localeCompare(b, "zh-Hant"));

  async function api(path, options) {
    const config = options || {};
    const headers = Object.assign({ Accept: "application/json" }, config.headers || {});
    if (config.body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(API_BASE + path, Object.assign({}, config, { headers: headers }));
    } catch (error) {
      $("sync-state").textContent = "目前無法連線後端，請確認 API 已啟動。";
      throw new Error("連線不到後端，請先依 README 啟動 API（" + API_BASE + "）。");
    }
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload && payload.detail ? payload.detail : "後端回應錯誤（" + response.status + "）。");
    return payload;
  }

  async function run(action) {
    try {
      await action();
    } catch (error) {
      announce(error.message || "操作失敗，請稍後再試。");
    }
  }

  async function loadData() {
    const today = encodeURIComponent(dateOnly(new Date()));
    const results = await Promise.all([
      api("/api/inventory"),
      api("/api/transactions"),
      api("/api/products"),
      api("/api/users"),
      api("/api/alerts"),
      api("/api/waste-summary"),
      api("/api/safety-levels"),
      api("/api/aging-rules"),
      api("/api/stocktake?date=" + today),
      api("/api/inbound-tasks"),
      api("/api/pick-tasks")
    ]);
    state.batches = results[0];
    state.transactions = results[1];
    state.products = results[2];
    state.users = results[3];
    state.alerts = results[4];
    state.wasteSummary = results[5];
    state.safetyLevels = results[6];
    state.agingRules = results[7];
    state.stocktake = results[8];
    state.pendingInbound = results[9].find(task => task.status === "open") || null;
    state.pendingPick = results[10].find(task => task.status === "open") || null;
    if (!state.users.some(user => user.name === state.currentOperator)) {
      state.currentOperator = state.users[0] ? state.users[0].name : "";
    }
    refreshOperatorSelect();
    refreshProductOptions();
    renderInboundTask();
    renderPickTask();
    renderDashboard();
    renderStocktake();
    renderManagement();
    $("sync-state").textContent = "已連線後端 API";
  }

  function currentOperator() { return $("current-operator").value.trim(); }

  function refreshOperatorSelect() {
    $("current-operator").innerHTML = state.users.map(user =>
      '<option value="' + esc(user.name) + '">' + esc(user.name) + "（" + (user.role === "manager" ? "管理者" : "倉管人員") + "）</option>"
    ).join("");
    $("current-operator").value = state.currentOperator;
  }

  function refreshProductOptions() {
    const names = productNames();
    $("product-options").innerHTML = names.map(name => '<option value="' + esc(name) + '"></option>').join("");
    const select = $("out-item");
    const previous = select.value;
    select.innerHTML = '<option value="">請選擇商品</option>' + names.map(name =>
      '<option value="' + esc(name) + '">' + esc(name) + "</option>"
    ).join("");
    if (names.includes(previous)) select.value = previous;
  }

  function expiryStatus(value) {
    if (!value) return "未設定";
    const days = Math.ceil((new Date(value + "T00:00:00") - new Date(dateOnly(new Date()) + "T00:00:00")) / DAY_MS);
    if (days < 0) return "已過期，請確認";
    if (days === 0) return "今日到期";
    if (days <= 3) return days + " 天內到期";
    return "正常";
  }

  function renderWarehouseTables() {
    const batches = activeBatches().sort((a, b) => Date.parse(a.receivedAt) - Date.parse(b.receivedAt));
    ["1", "2"].forEach(warehouse => {
      const rows = batches.filter(batch => String(batch.warehouse) === warehouse);
      $("warehouse" + warehouse + "-table").innerHTML = rows.length
        ? rows.map(batch => "<tr><td>" + esc(batch.bin) + "</td><td>" + esc(batch.name) + "</td><td>" + Number(batch.qty) + " 箱</td><td>" + esc(batch.lotNumber) + "</td><td>" + esc(formatTime(batch.receivedAt)) + "</td><td>" + esc(batch.expiryDate || "未設定") + "／" + esc(expiryStatus(batch.expiryDate)) + "</td></tr>").join("")
        : '<tr><td colspan="6">目前沒有庫存。</td></tr>';
      $("warehouse" + warehouse + "-summary").textContent = rows.length + " 個批次，共 " +
        rows.reduce((sum, batch) => sum + Number(batch.qty), 0) + " 箱";
    });
  }

  function renderAlerts() {
    const expiry = state.alerts.expiry || [];
    $("expiry-alert-table").innerHTML = expiry.length
      ? expiry.map(batch => "<tr><td>" + esc(batch.name) + "</td><td>" + warehouseName(batch.warehouse) + "／" + esc(batch.bin) + "</td><td>" + esc(batch.lotNumber) + "</td><td>" + esc(batch.status || expiryStatus(batch.expiryDate)) + "</td></tr>").join("")
      : '<tr><td colspan="4">目前沒有需要提醒的效期。</td></tr>';

    const aging = state.alerts.aging || [];
    $("aging-alert-table").innerHTML = aging.length
      ? aging.map(batch => "<tr><td>" + esc(batch.name) + "</td><td>" + warehouseName(batch.warehouse) + "／" + esc(batch.bin) + "</td><td>" + Number(batch.days) + " 天</td><td>" + Number(batch.threshold) + " 天</td></tr>").join("")
      : '<tr><td colspan="4">目前沒有久放提醒。</td></tr>';

    const safety = state.alerts.safety || [];
    $("safety-alert-table").innerHTML = safety.length
      ? safety.map(item => "<tr><td>" + esc(item.name) + "</td><td>" + warehouseName(item.warehouse) + "</td><td>" + Number(item.quantity) + " 箱</td><td>" + Number(item.level) + " 箱</td></tr>").join("")
      : '<tr><td colspan="4">目前沒有低於安全庫存的商品。</td></tr>';
  }

  function renderWasteSummary() {
    const rows = state.wasteSummary || [];
    $("waste-table").innerHTML = rows.length
      ? rows.map(item => "<tr><td>" + esc(item.reason || "未填原因") + "</td><td>" + Number(item.quantity) + " 箱</td></tr>").join("")
      : '<tr><td colspan="2">尚無報廢紀錄。</td></tr>';
  }

  function renderActivity() {
    const rows = state.transactions.slice(0, 30);
    $("activity-table").innerHTML = rows.length
      ? rows.map(tx => "<tr><td>" + esc(formatTime(tx.at)) + "</td><td>" + esc(tx.kind) + "</td><td>" +
        esc(tx.name) + "／" + warehouseName(tx.warehouse) + " " + esc(tx.bin) + "<br>" + esc(tx.lotNumber) +
        "</td><td>" + (Number(tx.quantity) > 0 ? "+" : "") + Number(tx.quantity) + " 箱</td><td>" +
        esc(tx.reason || "—") + "</td><td>" + esc(tx.operator) + "</td></tr>").join("")
      : '<tr><td colspan="6">目前沒有異動紀錄。</td></tr>';
  }

  function renderDashboard() {
    renderWarehouseTables();
    renderAlerts();
    renderWasteSummary();
    renderActivity();
    const now = new Date();
    $("last-updated").dateTime = now.toISOString();
    $("last-updated").textContent = formatTime(now);
  }

  function renderManagement() {
    $("user-table").innerHTML = state.users.length
      ? state.users.map(user => "<tr><td>" + esc(user.name) + "</td><td>" + (user.role === "manager" ? "管理者" : "倉管人員") + "</td></tr>").join("")
      : '<tr><td colspan="2">尚無人員。</td></tr>';
  }

  function renderStocktake() {
    const stocktake = state.stocktake || { items: [], total: 0, counted: 0 };
    $("stocktake-date").dateTime = stocktake.date || dateOnly(new Date());
    $("stocktake-date").textContent = stocktake.date || dateOnly(new Date());
    $("stocktake-progress").textContent = "今日已盤 " + Number(stocktake.counted) + "／" + Number(stocktake.total) +
      " 個批次" + (stocktake.completed ? "，今日清單已完成。" : "，尚有項目待盤點。");
    const warehouse = $("count-warehouse").value;
    const query = $("count-search").value.trim().toLocaleLowerCase();
    const rows = (stocktake.items || []).filter(item =>
      (warehouse === "all" || String(item.warehouse) === warehouse) &&
      [item.name, item.bin, item.lotNumber].some(value => String(value || "").toLocaleLowerCase().includes(query))
    );
    $("count-table").innerHTML = rows.length ? rows.map(item => {
      const actual = item.counted ? item.actual : item.qty;
      const reason = item.reason || "";
      return '<tr data-count-batch="' + esc(item.id) + '"><td>' +
        (item.counted ? "已盤（" + esc(formatTime(item.countedAt)) + "）" : "待盤") +
        "</td><td>" + warehouseName(item.warehouse) + "／" + esc(item.bin) + "</td><td>" +
        esc(item.name) + "<br>" + esc(item.lotNumber) + "</td><td>" + Number(item.qty) + ' 箱</td><td>' +
        '<label for="actual-' + esc(item.id) + '">實際數量</label><input id="actual-' + esc(item.id) +
        '" data-count-actual type="number" min="0" step="1" value="' + Number(actual) + '"></td><td>' +
        '<label for="reason-' + esc(item.id) + '">差異原因</label><select id="reason-' + esc(item.id) +
        '" data-count-reason><option value="">選擇原因</option>' +
        ["腐爛報廢", "破損報廢", "盤點差異", "數量確認"].map(option =>
          '<option value="' + option + '"' + (reason === option ? " selected" : "") + ">" + option + "</option>"
        ).join("") + '</select></td><td><button type="button" data-save-count>儲存盤點</button></td></tr>';
    }).join("") : '<tr><td colspan="7">目前沒有符合條件的庫存。</td></tr>';
  }

  function renderInboundTask() {
    const task = state.pendingInbound;
    const batch = task && task.batch;
    $("inbound-lot").textContent = batch ? batch.lotNumber : "尚未建立";
    $("inbound-location-code").textContent = task ? task.locationCode : "—";
    $("in-verify-location").disabled = !task;
    $("in-scan-crate-button").disabled = !task || !task.locationVerified;
    $("inbound-confirm").disabled = !task || !task.ready;
    $("inbound-cancel").disabled = !task;
    if (!task) {
      $("inbound-progress").textContent = "尚未建立收貨工作。";
      $("inbound-crate-codes").innerHTML = "";
      return;
    }
    $("inbound-progress").textContent = "已登記 " + task.scannedCodes.length + "／" + batch.qty +
      " 箱；" + (task.locationVerified ? "儲位已核對。" : "請先核對儲位。");
    $("inbound-crate-codes").innerHTML = batch.crateCodes.map(code =>
      "<li>" + esc(code) + (task.scannedCodes.includes(code) ? "（已掃描）" : "（待掃描）") + "</li>"
    ).join("");
  }

  function renderPickTask() {
    const task = state.pendingPick;
    const tbody = $("pick-plan-table");
    if (!task) {
      tbody.innerHTML = '<tr><td colspan="5">尚未建立取貨工作。</td></tr>';
      $("pick-next-instruction").textContent = "建立出貨需求後，這裡會顯示最早入庫的取貨位置。";
      $("out-verify-location").disabled = true;
      $("out-scan-crate-button").disabled = true;
      $("outbound-confirm").disabled = true;
      $("outbound-cancel").disabled = true;
      $("outbound-progress").textContent = "請先建立取貨工作。";
      $("outbound-scanned-list").innerHTML = "";
      return;
    }
    tbody.innerHTML = task.plan.map((line, index) =>
      "<tr><td>" + (index + 1) + "</td><td>" + warehouseName(line.warehouse) + "／" + esc(line.bin) +
      "</td><td>" + esc(line.lotNumber) + "<br>" + esc(formatTime(line.receivedAt)) +
      "</td><td>" + Number(line.quantity) + " 箱</td><td>" + Number(line.scanned) + " 箱</td></tr>"
    ).join("");
    const next = task.next;
    $("pick-next-instruction").textContent = next
      ? "下一步：前往 " + warehouseName(next.warehouse) + " " + next.bin + "（" + next.lotNumber + "）取貨。"
      : "需求數量已全部掃描，請確認出庫。";
    $("out-verify-location").disabled = !next || task.ready;
    $("out-scan-crate-button").disabled = !next || task.ready || String(task.verifiedBatchId) !== String(next.batchId);
    $("outbound-confirm").disabled = !task.ready;
    $("outbound-cancel").disabled = task.status !== "open";
    $("outbound-progress").textContent = "已掃描 " + task.scanned.length + "／" + task.quantity + " 箱。" +
      (task.verifiedBatchId ? "目前儲位已核對。" : "每換一個批次／儲位，請重新核對儲位。");
    $("outbound-scanned-list").innerHTML = task.scanned.length
      ? task.scanned.map(item => "<li>" + esc(item.crateCode) + "（" + esc(item.lotNumber) + "）</li>").join("")
      : "<li>尚未掃描箱子。</li>";
  }

  async function loadInboundTask(taskId) {
    state.pendingInbound = await api("/api/inbound-tasks/" + taskId);
    renderInboundTask();
  }
  async function loadPickTask(taskId) {
    state.pendingPick = await api("/api/pick-tasks/" + taskId);
    renderPickTask();
  }
  async function finishAndReload(message) {
    await loadData();
    announce(message);
  }

  $("inbound-form").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const data = new FormData(form);
      const task = await api("/api/inbound-tasks", {
        method: "POST",
        body: JSON.stringify({
          item: String(data.get("item")).trim(),
          warehouse: String(data.get("warehouse")),
          bin: String(data.get("bin")).trim().toUpperCase(),
          quantity: Number(data.get("quantity")),
          operator: currentOperator(),
          expiryDate: String(data.get("expiryDate") || "")
        })
      });
      state.pendingInbound = task;
      form.reset();
      await loadData();
      state.pendingInbound = task;
      renderInboundTask();
      announce("收貨工作已建立，請核對儲位並逐箱登記。");
    });
  });

  $("in-verify-location").addEventListener("click", () => run(async () => {
    const task = state.pendingInbound;
    if (!task) return;
    const updated = await api("/api/inbound-tasks/" + task.id + "/verify-location", {
      method: "POST", body: JSON.stringify({ locationCode: $("in-scan-location").value.trim() })
    });
    state.pendingInbound = updated;
    renderInboundTask();
    $("in-scan-crate").focus();
    announce("儲位核對完成，請逐箱登記箱號。");
  }));

  $("in-scan-crate-button").addEventListener("click", () => run(async () => {
    const task = state.pendingInbound;
    if (!task) return;
    const updated = await api("/api/inbound-tasks/" + task.id + "/scan", {
      method: "POST", body: JSON.stringify({ crateCode: $("in-scan-crate").value.trim() })
    });
    state.pendingInbound = updated;
    $("in-scan-crate").value = "";
    renderInboundTask();
    announce("箱號已登記：" + updated.scannedCodes.length + "／" + updated.batch.qty + " 箱。");
  }));

  $("in-scan-location").addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); $("in-verify-location").click(); }
  });
  $("in-scan-crate").addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); $("in-scan-crate-button").click(); }
  });

  $("inbound-confirm").addEventListener("click", () => run(async () => {
    const task = state.pendingInbound;
    if (!task) return;
    const result = await api("/api/inbound-tasks/" + task.id + "/confirm", { method: "POST" });
    state.pendingInbound = null;
    await finishAndReload(result.message || "進貨已確認，庫存已更新。");
  }));

  $("inbound-cancel").addEventListener("click", () => run(async () => {
    const task = state.pendingInbound;
    if (!task) return;
    const result = await api("/api/inbound-tasks/" + task.id + "/cancel", { method: "POST" });
    state.pendingInbound = null;
    await finishAndReload(result.message || "收貨工作已取消，庫存未變更。");
  }));

  $("outbound-form").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const data = new FormData(event.currentTarget);
      const task = await api("/api/pick-tasks", {
        method: "POST",
        body: JSON.stringify({
          item: String(data.get("item")),
          quantity: Number(data.get("quantity")),
          operator: currentOperator()
        })
      });
      state.pendingPick = task;
      renderPickTask();
      announce("FIFO 取貨工作已建立，請依指示核對儲位並逐箱登記。");
    });
  });

  $("out-verify-location").addEventListener("click", () => run(async () => {
    const task = state.pendingPick;
    if (!task || !task.next) return;
    const updated = await api("/api/pick-tasks/" + task.id + "/verify-location", {
      method: "POST", body: JSON.stringify({ locationCode: $("out-scan-location").value.trim() })
    });
    state.pendingPick = updated;
    renderPickTask();
    $("out-scan-crate").focus();
    announce("儲位核對完成，請登記實際取出的箱號。");
  }));

  $("out-scan-crate-button").addEventListener("click", () => run(async () => {
    const task = state.pendingPick;
    if (!task) return;
    const updated = await api("/api/pick-tasks/" + task.id + "/scan", {
      method: "POST", body: JSON.stringify({ crateCode: $("out-scan-crate").value.trim() })
    });
    state.pendingPick = updated;
    $("out-scan-crate").value = "";
    renderPickTask();
    announce("箱號已登記：" + updated.scanned.length + "／" + updated.quantity + " 箱。");
  }));

  $("out-scan-location").addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); $("out-verify-location").click(); }
  });
  $("out-scan-crate").addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); $("out-scan-crate-button").click(); }
  });

  $("outbound-confirm").addEventListener("click", () => run(async () => {
    const task = state.pendingPick;
    if (!task) return;
    const result = await api("/api/pick-tasks/" + task.id + "/confirm", { method: "POST" });
    state.pendingPick = null;
    await finishAndReload(result.message || "出貨已確認，庫存已依 FIFO 更新。");
  }));

  $("outbound-cancel").addEventListener("click", () => run(async () => {
    const task = state.pendingPick;
    if (!task) return;
    const result = await api("/api/pick-tasks/" + task.id + "/cancel", { method: "POST" });
    state.pendingPick = null;
    await finishAndReload(result.message || "取貨工作已取消，庫存未變更。");
  }));

  $("count-warehouse").addEventListener("change", renderStocktake);
  $("count-search").addEventListener("input", renderStocktake);
  $("count-table").addEventListener("click", event => {
    if (!event.target.closest("[data-save-count]")) return;
    run(async () => {
      const row = event.target.closest("tr[data-count-batch]");
      const actual = Number(row.querySelector("[data-count-actual]").value);
      const reason = row.querySelector("[data-count-reason]").value;
      if (!Number.isSafeInteger(actual) || actual < 0) throw new Error("實際數量請輸入 0 或正整數。");
      const item = state.stocktake.items.find(entry => String(entry.id) === row.dataset.countBatch);
      if (!item) throw new Error("找不到這筆盤點資料，請重新整理。");
      if (actual !== Number(item.qty) && !reason) throw new Error("數量有差異，請選擇差異原因。");
      const result = await api("/api/stocktake/counts", {
        method: "POST",
        body: JSON.stringify({
          id: Number(item.id),
          actual_quantity: actual,
          reason: reason || "數量確認",
          note: "",
          operator: currentOperator()
        })
      });
      await finishAndReload(result.kind === "報廢"
        ? "盤點完成，已登記報廢 " + Math.abs(Number(result.difference)) + " 箱。"
        : "盤點完成，庫存已依實際數量更新。");
    });
  });

  $("safety-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    run(async () => {
      const name = $("safety-item").value.trim();
      const warehouse = $("safety-wh").value;
      const quantity = Number($("safety-qty").value);
      if (!name || !Number.isSafeInteger(quantity) || quantity < 0) throw new Error("請輸入商品及有效安全庫存量。");
      await api("/api/safety-levels", {
        method: "PUT",
        body: JSON.stringify({ warehouse: warehouse, name: name, quantity: quantity })
      });
      form.reset();
      await finishAndReload("安全庫存設定已儲存至後端。");
    });
  });

  $("aging-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    run(async () => {
      const name = $("aging-item").value.trim();
      const days = Number($("aging-days").value);
      if (!name || !Number.isSafeInteger(days) || days < 1) throw new Error("請輸入商品和大於 0 的提醒天數。");
      await api("/api/aging-rules", {
        method: "PUT",
        body: JSON.stringify({ name: name, days: days })
      });
      form.reset();
      await finishAndReload("久放提醒已儲存至後端。");
    });
  });

  $("user-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    run(async () => {
      const name = $("user-name").value.trim();
      const role = $("user-role").value;
      if (!name) throw new Error("請輸入人員姓名。");
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({ name: name, role: role })
      });
      form.reset();
      await finishAndReload("人員名單已新增。");
    });
  });

  $("current-operator").addEventListener("change", () => {
    state.currentOperator = currentOperator();
    sessionStorage.setItem("zhunan-warehouse-operator", state.currentOperator);
    announce("目前操作人已切換為 " + state.currentOperator);
  });

  async function switchTab(id) {
    document.querySelectorAll("main > section[data-panel]").forEach(section => { section.hidden = section.id !== id; });
    document.querySelectorAll("button[data-tab]").forEach(button => {
      if (button.dataset.tab === id) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    await run(loadData);
  }

  $("dashboard").addEventListener("click", event => {
    const target = event.target.closest("[data-open-tab]");
    if (target) switchTab(target.dataset.openTab);
  });
  document.querySelectorAll("button[data-tab]").forEach(button => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });

  run(loadData);
})();
