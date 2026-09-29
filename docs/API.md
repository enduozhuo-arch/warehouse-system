# Warehouse System API Specification

本文件定義竹南倉儲系統前後端之間的主要功能介面。

## 1. 查詢 FIFO 出貨建議

### GET /api/inventory/fifo

依照進貨時間由早到晚查詢庫存，提供 FIFO 出貨順序。

Request：

- warehouse_id：倉庫編號
- product_id：商品編號
- quantity：預計出貨數量

Example：

GET /api/inventory/fifo?warehouse_id=1&product_id=1&quantity=60

Response：

```json
{
  "requested_quantity": 60,
  "outbound": [
    {
      "batch_id": 1,
      "location": "A01",
      "quantity": 50
    },
    {
      "batch_id": 2,
      "location": "A02",
      "quantity": 10
    }
  ]
}