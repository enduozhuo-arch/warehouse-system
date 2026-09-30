# Warehouse System API Specification

本文件定義竹南冷凍倉儲系統前後端之間的介面。欄位名稱與前端 `fronted/app.js` 一致。

## 啟動方式

```bash
pip install -r requirements.txt
python -m backend.database --reset
python -m uvicorn backend.main:app --reload
```

- API 位址：`http://127.0.0.1:8000`，互動式文件：`http://127.0.0.1:8000/docs`
- `python -m backend.database --reset` 會刪除現有的 `database/warehouse.db` 並由 `schema.sql` 重建。
  資料庫結構有更新時（例如拉到新版程式後）需要執行一次，原本的資料會清掉。
- 已開放跨來源請求（CORS），前端頁面可以直接用 `fetch` 呼叫。

## 測試

```bash
python -m unittest tests.test_fifo tests.test_inventory tests.test_api tests.test_pick_tasks tests.test_inbound_tasks tests.test_stocktake tests.test_management
```

每個測試都會由 `schema.sql` 重建資料庫，跑完後還原原本的 Demo 資料庫。

## 初始資料

`schema.sql` 的初始資料與前端 `fronted/app.js` 的範例資料相同。
入庫時間與效期以「重建資料庫當天」為基準推算，所以示範當天一定看得到效期與久放提醒。

| id | 商品 | 倉庫 | 儲位 | 數量 | 入庫 | 效期 | 批號 | 儲位代碼 | 箱號 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 甘藍菜 | 1 | A-10 | 5 | 5 天前 | 8 天後 | ZN-1-SEED-01 | LOC-1-A10 | seed-01-BOX-001～005 |
| 2 | 青江菜 | 1 | B-02 | 3 | 9 天前 | 2 天後 | ZN-1-SEED-02 | LOC-1-B02 | seed-02-BOX-001～003 |
| 3 | 青江菜 | 2 | C-05 | 10 | 3 天前 | 12 天後 | ZN-2-SEED-03 | LOC-2-C05 | seed-03-BOX-001～010 |
| 4 | 高麗菜 | 2 | D-01 | 8 | 4 天前 | 15 天後 | ZN-2-SEED-04 | LOC-2-D01 | seed-04-BOX-001～008 |
| 5 | 番茄 | 1 | A-03 | 6 | 6 天前 | 6 天後 | ZN-1-SEED-05 | LOC-1-A03 | seed-05-BOX-001～006 |

其他初始設定：人員「李太太（管理者）」「倉管人員」；倉庫 1 甘藍菜安全庫存 4 箱；青江菜久放門檻 7 天。

### 上台示範（NO1）對應的 API

1. `POST /api/pick-tasks`：`{"item": "青江菜", "quantity": 1, "operator": "李太太"}` → 回傳指示倉庫 1、儲位 B-02。
2. 直接 `POST /api/pick-tasks/1/confirm` → 400，庫存不變。
3. `POST /api/pick-tasks/1/verify-location`：`{"locationCode": "LOC-1-B02"}`。
4. `POST /api/pick-tasks/1/scan`：`{"crateCode": "seed-02-BOX-001"}`。
5. `POST /api/pick-tasks/1/confirm` → 青江菜少一箱，`GET /api/transactions` 多一筆出貨紀錄。

## 共用格式

### 批次（batch）

| 欄位 | 說明 |
| --- | --- |
| id | 庫存批次編號（整數） |
| name | 商品名稱 |
| warehouse | 倉庫編號（字串，例如 `"1"`） |
| bin | 儲位代碼 |
| qty | 批次目前庫存數量（箱） |
| receivedAt | 入庫時間，ISO 8601（+08:00），由伺服器記錄 |
| lotNumber | 批號，例如 `ZN-1-SEED-02`（初始資料）、`ZN-1-20260930-000006`（新入庫） |
| expiryDate | 效期 `YYYY-MM-DD`，未設定為空字串 |
| locationCode | 儲位 QR Code 內容，例如 `LOC-1-B02` |
| crateCodes | 目前在庫的箱號，例如 `["seed-02-BOX-001", ...]`（只有部分 API 回傳） |

### 代碼規則

- 儲位代碼：`LOC-{倉庫}-{儲位去掉符號}`，例如倉庫 1 的 `B-02` → `LOC-1-B02`。比對時不分大小寫。
- 箱號：新入庫的批次為 `{批次編號}-BOX-{三位流水號}`，例如 `6-BOX-001`；
  初始資料的批次沿用前端範例的格式 `seed-02-BOX-001`。

### 錯誤回應

錯誤時回傳 `{"detail": "錯誤說明"}`。

| 狀態碼 | 意義 |
| --- | --- |
| 400 | 輸入有誤，或掃到錯的儲位／箱號 |
| 404 | 找不到資料（批次、工作、倉庫） |
| 409 | 目前狀態不允許（重複掃描、尚未核對儲位、工作已結束、庫存已變動） |

---

## 1. 庫存與異動紀錄

### GET /api/inventory

回傳所有批次（含 `crateCodes`），依倉庫、入庫時間排序。

### GET /api/transactions

回傳異動紀錄，最新的在前。

```json
{
  "id": 1,
  "at": "2026-09-30T18:05:48+08:00",
  "kind": "出貨",
  "name": "青江菜",
  "warehouse": "1",
  "bin": "B-02",
  "lotNumber": "ZN-1-SEED-02",
  "quantity": -1,
  "operator": "李太太",
  "operatorRole": "manager",
  "reason": "PDA／掃碼確認",
  "note": ""
}
```

`kind`：`進貨`、`出貨`、`報廢`、`盤點調整`。

### GET /api/products

回傳商品名稱清單（供搜尋與下拉選單使用）。進貨時輸入清單以外的商品名稱會自動新增。

---

## 2. 出貨：FIFO 取貨工作（掃碼確認）

流程：建立取貨工作 → 核對儲位 → 逐箱掃描 → 確認出庫。
庫存只在「確認出庫」時扣除。

### POST /api/pick-tasks

```json
{ "item": "青江菜", "quantity": 5, "operator": "李太太" }
```

伺服器依入庫時間（FIFO）排出取貨計畫，可跨倉庫。庫存不足回 400。

回傳取貨工作（以下各 API 也回傳相同格式）：

```json
{
  "id": 1,
  "name": "青江菜",
  "quantity": 5,
  "operator": "李太太",
  "status": "open",
  "plan": [
    {
      "batchId": 2, "warehouse": "1", "bin": "B-02",
      "lotNumber": "ZN-1-SEED-02",
      "receivedAt": "2026-09-21T08:00:00+08:00",
      "locationCode": "LOC-1-B02",
      "quantity": 3, "scanned": 0
    },
    {
      "batchId": 3, "warehouse": "2", "bin": "C-05",
      "lotNumber": "ZN-2-SEED-03",
      "receivedAt": "2026-09-27T08:00:00+08:00",
      "locationCode": "LOC-2-C05",
      "quantity": 2, "scanned": 0
    }
  ],
  "scanned": [],
  "verifiedBatchId": null,
  "next": { "batchId": 2, "locationCode": "LOC-1-B02", "...": "與 plan 項目相同" },
  "ready": false
}
```

- `next`：目前應該去取貨的批次；全部掃完時為 `null`。
- `verifiedBatchId`：已核對儲位的批次；每換一個批次都要重新核對。
- `ready`：掃描箱數等於需求數量，可以確認出庫。
- `status`：`open` 進行中、`confirmed` 已出庫、`cancelled` 已取消。

### POST /api/pick-tasks/{id}/verify-location

```json
{ "locationCode": "LOC-1-B02" }
```

必須是 `next` 指定的儲位，否則回 400（訊息會提示正確的儲位代碼）。

### POST /api/pick-tasks/{id}/scan

```json
{ "crateCode": "seed-02-BOX-001" }
```

- 尚未核對儲位：409。
- 箱號不屬於目前 FIFO 指定批次（含不存在的箱號）：400。
- 同一箱重複掃描：409。

### POST /api/pick-tasks/{id}/confirm

掃描箱數不足回 400。成功時扣除庫存、寫入「出貨」異動紀錄。
若建立工作後庫存已被其他人異動（例如那一箱被報廢），回 409，需取消後重新建立。

### POST /api/pick-tasks/{id}/cancel

取消工作，庫存不變。

### GET /api/pick-tasks、GET /api/pick-tasks/{id}

列表預設只回傳進行中的工作；`?status=all` 回傳全部。

---

## 3. 入庫：收貨工作（掃碼確認）

流程：建立收貨單 → 核對儲位 → 逐箱掃描 → 確認入庫。
庫存只在「確認入庫」時入帳，入庫時間以確認當下的伺服器時間為準。

### POST /api/inbound-tasks

```json
{
  "item": "白蘿蔔",
  "warehouse": "1",
  "bin": "B-02",
  "quantity": 2,
  "operator": "倉管人員",
  "expiryDate": "2026-10-15"
}
```

`expiryDate` 可省略。商品或儲位不存在時，會在確認入庫時自動建立。

回傳收貨工作：

```json
{
  "id": 1,
  "operator": "倉管人員",
  "status": "open",
  "batch": {
    "id": 6, "name": "白蘿蔔", "warehouse": "1", "bin": "B-02", "qty": 2,
    "receivedAt": "",
    "lotNumber": "ZN-1-20260930-000006",
    "expiryDate": "2026-10-15",
    "locationCode": "LOC-1-B02",
    "crateCodes": ["6-BOX-001", "6-BOX-002"]
  },
  "locationCode": "LOC-1-B02",
  "locationVerified": false,
  "scannedCodes": [],
  "ready": false
}
```

### POST /api/inbound-tasks/{id}/verify-location、/scan、/confirm、/cancel

請求格式與取貨工作相同（`locationCode`、`crateCode`）。
要先核對儲位才能掃箱；`crateCodes` 全部掃完才能確認入庫。

### GET /api/inbound-tasks、GET /api/inbound-tasks/{id}

同取貨工作。

---

## 4. 每日盤點

### GET /api/stocktake

參數：`date`（預設今天）、`warehouse`（`all`、`1`、`2`，預設 `all`）。

當日有庫存的批次都會列入清單。`total`、`counted` 是整張清單的進度，不受 `warehouse` 篩選影響。

```json
{
  "date": "2026-09-30",
  "total": 5,
  "counted": 1,
  "completed": false,
  "items": [
    {
      "id": 5, "name": "番茄", "warehouse": "1", "bin": "A-03", "qty": 4,
      "lotNumber": "ZN-1-SEED-05",
      "counted": true,
      "oldQty": 6,
      "actual": 4,
      "reason": "腐爛報廢",
      "note": "",
      "operator": "倉管人員",
      "countedAt": "2026-09-30T18:10:00+08:00"
    }
  ]
}
```

### POST /api/stocktake/counts

```json
{
  "id": 5,
  "actual_quantity": 4,
  "reason": "腐爛報廢",
  "note": "",
  "operator": "倉管人員"
}
```

- 數量有差異時必須填 `reason`，否則回 400。
- 數量減少且原因為 `腐爛報廢`、`破損報廢`（或舊代碼 `rotten`、`damaged`）記為「報廢」，其他記為「盤點調整」。
- 數量相同也可以送出，代表「已盤點、數量確認」。
- 同一天重複盤點同一批次，以最後一次為準。

回傳批次資料，加上 `kind`、`oldQty`、`actual`、`difference`，以及當日進度 `counted`、`total`、`completed`。

---

## 5. 管理與提醒

### GET /api/alerts

參數：`today`（`YYYY-MM-DD`，預設今天，可用來指定基準日）。

```json
{
  "today": "2026-09-30",
  "expiry": [ { "id": 2, "name": "青江菜", "daysLeft": 2, "status": "2 天內到期", "...": "批次欄位" } ],
  "aging": [ { "id": 2, "name": "青江菜", "days": 9, "threshold": 7, "...": "批次欄位" } ],
  "safety": [ { "warehouse": "1", "name": "甘藍菜", "quantity": 3, "level": 4 } ]
}
```

- `expiry`：已過期、今日到期、3 天內到期的批次。
- `aging`：放置天數達到久放門檻的批次。
- `safety`：某倉庫某商品總量低於安全庫存。

### 安全庫存：GET /api/safety-levels、PUT /api/safety-levels

```json
{ "warehouse": "1", "name": "甘藍菜", "quantity": 4 }
```

### 久放門檻：GET /api/aging-rules、PUT /api/aging-rules

```json
{ "name": "青江菜", "days": 7 }
```

### GET /api/waste-summary

依原因統計報廢箱數，多的在前。

```json
[
  { "reason": "腐爛報廢", "quantity": 3 },
  { "reason": "破損報廢", "quantity": 1 }
]
```

### 人員名單：GET /api/users、POST /api/users

```json
{ "name": "小陳", "role": "warehouse" }
```

`role`：`manager` 管理者、`warehouse` 倉管人員。姓名重複回 409。

人員名單只用來標示異動紀錄的操作人角色（`operatorRole`）。
目前沒有登入與權限控管，任何人都可以呼叫所有 API。

---

## 6. 不經掃碼的舊版 API（仍可使用）

| API | 說明 |
| --- | --- |
| GET /api/inventory/fifo?item=青江菜&quantity=5 | 只查詢 FIFO 出貨建議，不扣庫存 |
| POST /api/inbound | 直接進貨：`item`、`warehouse`、`bin`、`quantity`、`operator`、`expiryDate`（選填） |
| POST /api/outbound | 直接依 FIFO 出貨：`item`、`quantity`、`operator` |
| PATCH /api/inventory | 單筆盤點調整：`id`、`actual_quantity`、`reason`、`note`、`operator` |

這幾支不需要核對儲位與箱號。要避免「拿了貨卻忘記登記」，出入庫請使用第 2、3 節的掃碼流程。
