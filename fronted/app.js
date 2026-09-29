(() => {
  "use strict";

  const STORAGE_KEY = "zhunan-warehouse-demo-v2";
  const seedBatches = [
    { id: "seed-1", name: "甘藍菜", warehouse: "1", bin: "A-10", qty: 5, receivedAt: "2026-09-25T08:00:00+08:00" },
    { id: "seed-2", name: "青江菜", warehouse: "1", bin: "B-02", qty: 3, receivedAt: "2026-09-20T14:00:00+08:00" },
    { id: "seed-3", name: "青江菜", warehouse: "2", bin: "C-05", qty: 10, receivedAt: "2026-09-27T09:30:00+08:00" },
    { id: "seed-4", name: "高麗菜", warehouse: "2", bin: "D-01", qty: 8, receivedAt: "2026-09-26T11:00:00+08:00" },
    { id: "seed-5", name: "番茄", warehouse: "1", bin: "A-03", qty: 6, receivedAt: "2026-09-24T16:15:00+08:00" }
  ];
  const newId = () => globalThis.crypto?.randomUUID?.() || `batch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const clone = value => JSON.parse(JSON.stringify(value));
  const escapeHTML = value => String(value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const byOldest = (a, b) => Date.parse(a.receivedAt) - Date.parse(b.receivedAt) || String(a.id).localeCompare(String(b.id));
  const formatDate = value => new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short", hour12: false }).format(new Date(value));
  const warehouseName = id => `倉庫 ${id}`;
  const statusNode = document.getElementById("app-status");
  let state;

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (saved && Array.isArray(saved.batches) && Array.isArray(saved.transactions)) return saved;
    } catch (error) {
      console.warn("無法讀取本機展示資料，已載入範例資料。", error);
    }
    return { batches: clone(seedBatches), transactions: [] };
  }
  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      return true;
    } catch (error) {
      console.error("無法儲存本機展示資料。", error);
      announce("瀏覽器無法儲存資料，請確認本機儲存空間設定。此次操作未能可靠保存。");
      return false;
    }
  }
  function announce(message) { statusNode.textContent = message; }
  function record(kind, batch, quantity, operator, reason = "") {
    state.transactions.unshift({ id: newId(), at: new Date().toISOString(), kind, name: batch.name, warehouse: batch.warehouse, bin: batch.bin, quantity, operator, reason });
    state.transactions = state.transactions.slice(0, 100);
  }
  function activeBatches() { return state.batches.filter(batch => batch.qty > 0).sort(byOldest); }

  function renderDashboard() {
    const batches = activeBatches();
    for (const wh of ["1", "2"]) {
      const tbody = document.getElementById(`wh${wh}-table`);
      const warehouseBatches = batches.filter(batch => batch.warehouse === wh);
      tbody.innerHTML = warehouseBatches.length ? warehouseBatches.map(batch => `<tr><td class="font-bold">${escapeHTML(batch.bin)}</td><td>${escapeHTML(batch.name)}</td><td class="font-bold">${batch.qty} 箱</td><td>${escapeHTML(formatDate(batch.receivedAt))}</td></tr>`).join("") : `<tr><td colspan="4" class="p-5 text-center text-slate-500">目前沒有可用庫存</td></tr>`;
      document.getElementById(`warehouse${wh}-summary`).textContent = `${warehouseBatches.length} 筆庫存 · ${warehouseBatches.reduce((sum, batch) => sum + batch.qty, 0)} 箱`;
    }
    const activity = document.getElementById("activity-table");
    activity.innerHTML = state.transactions.length ? state.transactions.slice(0, 12).map(tx => `<tr><td>${escapeHTML(formatDate(tx.at))}</td><td>${escapeHTML(tx.kind)}</td><td>${escapeHTML(tx.name)}／${warehouseName(tx.warehouse)} ${escapeHTML(tx.bin)}</td><td>${tx.quantity > 0 ? "+" : ""}${tx.quantity} 箱</td><td>${escapeHTML([tx.reason, tx.operator].filter(Boolean).join("／"))}</td></tr>`).join("") : `<tr><td colspan="5" class="p-5 text-center text-slate-500">尚無異動紀錄</td></tr>`;
    const updated = document.getElementById("last-updated");
    updated.dateTime = new Date().toISOString();
    updated.textContent = formatDate(updated.dateTime);
  }

  function refreshItemOptions() {
    const select = document.getElementById("out-item");
    const previous = select.value;
    const names = [...new Set(activeBatches().map(batch => batch.name))].sort((a, b) => a.localeCompare(b, "zh-Hant"));
    select.innerHTML = `<option value="">— 請選擇蔬菜 —</option>${names.map(name => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("")}`;
    if (names.includes(previous)) select.value = previous;
  }

  function getFifoQueue(name) {
    return state.batches.filter(batch => batch.name === name && batch.qty > 0).sort(byOldest);
  }
  function buildPlan(queue, quantity) {
    let remaining = quantity;
    const plan = [];
    for (const batch of queue) {
      if (remaining <= 0) break;
      const take = Math.min(batch.qty, remaining);
      plan.push({ batch, quantity: take });
      remaining -= take;
    }
    return { plan, remaining };
  }
  function renderFifo() {
    const name = document.getElementById("out-item").value;
    const suggestion = document.getElementById("fifo-suggestion");
    const noStock = document.getElementById("no-stock-msg");
    const submit = document.getElementById("out-submit");
    const queue = getFifoQueue(name);
    if (!name) {
      suggestion.classList.add("hidden"); noStock.classList.add("hidden"); submit.disabled = true; return;
    }
    if (!queue.length) {
      suggestion.classList.add("hidden"); noStock.classList.remove("hidden"); submit.disabled = true; return;
    }
    noStock.classList.add("hidden"); suggestion.classList.remove("hidden"); submit.disabled = false;
    const oldest = queue[0];
    document.getElementById("fifo-wh").textContent = warehouseName(oldest.warehouse);
    document.getElementById("fifo-bin").textContent = oldest.bin;
    document.getElementById("fifo-date").textContent = formatDate(oldest.receivedAt);
    document.getElementById("fifo-qty").textContent = oldest.qty;
    const quantity = Number(document.getElementById("out-qty").value);
    const { plan, remaining } = Number.isInteger(quantity) && quantity > 0 ? buildPlan(queue, quantity) : { plan: [], remaining: 0 };
    document.getElementById("fifo-allocation").textContent = plan.length ? `預計依序從以下位置出貨：${plan.map(item => `${warehouseName(item.batch.warehouse)} ${item.batch.bin} ${item.quantity}箱`).join("，接著 ")}${remaining ? "（庫存不足）" : ""}` : "輸入出貨數量後，這裡會顯示取貨順序。";
  }

  function renderInventory() {
    const wh = document.getElementById("inv-wh-select").value;
    const tbody = document.getElementById("inv-table");
    const batches = state.batches.filter(batch => batch.warehouse === wh && batch.qty > 0).sort(byOldest);
    if (!batches.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="p-8 text-center font-bold text-slate-500">此倉庫目前沒有可盤點庫存</td></tr>`;
      return;
    }
    tbody.innerHTML = batches.map(batch => `<tr data-batch-id="${escapeHTML(batch.id)}"><td class="font-bold">${escapeHTML(batch.bin)}</td><td class="font-bold">${escapeHTML(batch.name)}</td><td data-current-qty>${batch.qty} 箱</td><td><label class="sr-only" for="actual-${escapeHTML(batch.id)}">${escapeHTML(batch.name)} ${escapeHTML(batch.bin)}實際可用數量</label><input id="actual-${escapeHTML(batch.id)}" data-actual type="number" value="${batch.qty}" min="0" step="1" class="w-24 rounded-lg border border-slate-300 p-2 text-center font-bold"></td><td><label class="sr-only" for="reason-${escapeHTML(batch.id)}">${escapeHTML(batch.name)} ${escapeHTML(batch.bin)}差異原因</label><select id="reason-${escapeHTML(batch.id)}" data-reason class="rounded-lg border border-slate-300 p-2"><option value="">選擇原因（差異時必填）</option><option value="rotten">腐爛報廢</option><option value="damaged">破損報廢</option><option value="count">盤點差異</option></select><label class="sr-only" for="note-${escapeHTML(batch.id)}">${escapeHTML(batch.name)} ${escapeHTML(batch.bin)}備註</label><input id="note-${escapeHTML(batch.id)}" data-note type="text" maxlength="160" placeholder="補充說明（選填）" class="mt-2 w-full rounded-lg border border-slate-300 p-2 text-sm"></td><td><label class="sr-only" for="operator-${escapeHTML(batch.id)}">${escapeHTML(batch.name)} ${escapeHTML(batch.bin)}操作人</label><input id="operator-${escapeHTML(batch.id)}" data-operator type="text" value="" placeholder="請輸入姓名" maxlength="60" class="w-32 rounded-lg border border-slate-300 p-2"></td><td><button type="button" data-save-count class="rounded-lg bg-slate-800 px-3 py-2 text-sm font-bold text-white hover:bg-slate-700">儲存盤點</button></td></tr>`).join("");
  }

  function switchTab(tabId) {
    document.querySelectorAll(".tab-content").forEach(section => {
      const active = section.id === tabId;
      section.hidden = !active;
      section.classList.toggle("hidden", !active);
    });
    document.querySelectorAll(".nav-button").forEach(button => {
      const active = button.dataset.tab === tabId;
      button.classList.toggle("bg-white/10", active);
      button.classList.toggle("text-white", active);
      button.classList.toggle("text-slate-300", !active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    if (tabId === "dashboard") renderDashboard();
    if (tabId === "inventory") renderInventory();
    if (tabId === "outbound") { refreshItemOptions(); renderFifo(); }
  }

  document.querySelectorAll(".nav-button").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  document.getElementById("inbound-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const batch = { id: newId(), name: String(data.get("item")).trim(), warehouse: String(data.get("warehouse")), bin: String(data.get("bin")).trim().toUpperCase(), qty: Number(data.get("quantity")), receivedAt: new Date().toISOString() };
    if (!Number.isSafeInteger(batch.qty) || batch.qty < 1 || !batch.bin) { announce("請確認進貨儲位與正整數數量。"); return; }
    state.batches.push(batch);
    record("進貨", batch, batch.qty, String(data.get("operator")).trim());
    if (!saveState()) return;
    renderDashboard(); form.reset();
    document.getElementById("in-qty").value = "2";
    announce(`${batch.name} 已登記進貨 ${batch.qty} 箱至${warehouseName(batch.warehouse)} ${batch.bin}。`);
  });

  document.getElementById("out-item").addEventListener("change", () => { document.getElementById("out-qty").value = ""; renderFifo(); });
  document.getElementById("out-qty").addEventListener("input", renderFifo);
  document.getElementById("outbound-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form), name = String(data.get("item")), quantity = Number(data.get("quantity"));
    const queue = getFifoQueue(name), { plan, remaining } = buildPlan(queue, quantity);
    if (!Number.isSafeInteger(quantity) || quantity < 1 || remaining > 0) { announce(`出貨失敗：${name} 可用庫存不足，請確認數量。`); renderFifo(); return; }
    const changes = plan.map(({ batch, quantity: take }) => ({ batch, before: batch.qty, take }));
    changes.forEach(({ batch, take }) => { batch.qty -= take; });
    for (const change of changes) record("出貨", change.batch, -change.take, String(data.get("operator")).trim());
    if (!saveState()) { changes.forEach(({ batch, before }) => { batch.qty = before; }); return; }
    renderDashboard(); renderInventory(); renderFifo();
    announce(`出貨完成，共 ${quantity} 箱；已先扣除較早進貨的庫存。`);
  });

  document.getElementById("inv-wh-select").addEventListener("change", renderInventory);
  document.getElementById("inv-table").addEventListener("click", event => {
    const button = event.target.closest("[data-save-count]");
    if (!button) return;
    const row = button.closest("tr[data-batch-id]");
    const batch = state.batches.find(item => item.id === row.dataset.batchId);
    if (!batch) { announce("找不到這筆庫存資料，請重新整理盤點清單。"); return; }
    const actualInput = row.querySelector("[data-actual]"), reasonSelect = row.querySelector("[data-reason]"), noteInput = row.querySelector("[data-note]"), operatorInput = row.querySelector("[data-operator]");
    const actual = Number(actualInput.value), oldQty = batch.qty, difference = actual - oldQty;
    if (!Number.isSafeInteger(actual) || actual < 0) { actualInput.focus(); announce("實際可用數量必須是 0 或正整數。"); return; }
    if (difference !== 0 && !reasonSelect.value) { reasonSelect.focus(); announce("庫存數量有差異，請選擇盤點或報廢原因。"); return; }
    if (difference === 0 && !noteInput.value.trim() && !reasonSelect.value) { announce("數量沒有差異；如需留存盤點紀錄，請填寫原因或備註。"); return; }
    if (difference < 0 && !noteInput.value.trim() && reasonSelect.value === "count") { announce("若選擇盤點差異，請在備註說明差異原因。"); noteInput.focus(); return; }
    if (!operatorInput.value.trim()) { operatorInput.focus(); announce("請填寫盤點操作人。"); return; }
    const kind = difference < 0 && ["rotten", "damaged"].includes(reasonSelect.value) ? "報廢" : "盤點調整";
    const reasonText = [reasonSelect.value ? reasonSelect.selectedOptions[0]?.textContent : "", noteInput.value.trim()].filter(Boolean).join("：");
    batch.qty = actual;
    record(kind, batch, difference, operatorInput.value.trim(), reasonText || "數量確認");
    if (!saveState()) { batch.qty = oldQty; state.transactions.shift(); return; }
    renderDashboard(); renderInventory();
    announce(difference < 0 && kind === "報廢" ? `${batch.name} ${batch.bin} 已登記報廢 ${Math.abs(difference)} 箱。` : `${batch.name} ${batch.bin} 盤點已記錄，數量由 ${oldQty} 箱調整為 ${actual} 箱。`);
  });

  state = loadState();
  renderDashboard();
  renderInventory();
  refreshItemOptions();
  renderFifo();
})();


