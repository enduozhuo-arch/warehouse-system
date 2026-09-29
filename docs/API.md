# Warehouse System API Specification

本文件定義竹南倉儲系統前後端之間的主要功能介面。

## 1. 查詢 FIFO 出貨建議

### GET /api/inventory/fifo

依照進貨時間由早到晚查詢庫存，提供 FIFO 出貨順序。

Request：

- warehouse：倉庫編號
- product_id：商品編號
- quantity：預計出貨數量

Example：

GET /api/inventory/fifo?warehouse=1&product_id=1&quantity=60

Response：

```json
{
  "success": true,
  "requested_quantity": 60,
  "outbound": [
    {
      "id": 1,
      "bin": "A01",
      "quantity": 50,
      "receivedAt": "2026-09-20T08:00:00+08:00"
    },
    {
      "id": 2,
      "bin": "A02",
      "quantity": 10,
      "receivedAt": "2026-09-25T10:00:00+08:00"
    }
  ]
}
```

## 欄位命名（與前端 fronted/app.js 一致）

| 欄位 | 說明 |
| --- | --- |
| id | 庫存批次編號 |
| name | 商品名稱 |
| warehouse | 倉庫編號（回應中為字串，例如 "1"） |
| bin | 儲位代碼 |
| qty | 批次目前庫存數量 |
| quantity | 本次出貨／請求數量 |
| receivedAt | 入庫時間，ISO 8601 格式（+08:00） |
