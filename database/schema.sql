-- 竹南冷凍倉儲系統 Database Schema
-- 功能：管理兩個倉庫、商品批次、儲位與 FIFO 出貨

-- 1. 倉庫
CREATE TABLE warehouses (
    warehouse_id INT PRIMARY KEY,
    warehouse_name VARCHAR(50) NOT NULL
);

-- 2. 商品
CREATE TABLE products (
    product_id INT PRIMARY KEY,
    product_name VARCHAR(100) NOT NULL
);

-- 3. 儲位
CREATE TABLE locations (
    location_id INT PRIMARY KEY,
    warehouse_id INT NOT NULL,
    location_code VARCHAR(20) NOT NULL,

    FOREIGN KEY (warehouse_id)
        REFERENCES warehouses(warehouse_id)
);

-- 4. 庫存批次
CREATE TABLE inventory_batches (
    batch_id INT PRIMARY KEY,
    product_id INT NOT NULL,
    location_id INT NOT NULL,
    quantity INT NOT NULL,
    received_at DATETIME NOT NULL,
    lot_number VARCHAR(40),
    expiry_date DATE,

    FOREIGN KEY (product_id)
        REFERENCES products(product_id),

    FOREIGN KEY (location_id)
        REFERENCES locations(location_id)
);
-- =========================================
-- 測試資料
-- =========================================

-- 兩個倉庫
INSERT INTO warehouses (warehouse_id, warehouse_name) VALUES
(1, '倉庫 1'),
(2, '倉庫 2');

-- 商品
INSERT INTO products (product_id, product_name) VALUES
(1, '高麗菜'),
(2, '番茄'),
(3, '白蘿蔔');

-- 儲位
INSERT INTO locations (location_id, warehouse_id, location_code) VALUES
(1, 1, 'A01'),
(2, 1, 'A02'),
(3, 1, 'A03'),
(4, 2, 'B01'),
(5, 2, 'B02');

-- 庫存批次
INSERT INTO inventory_batches
(batch_id, product_id, location_id, quantity, received_at, lot_number, expiry_date)
VALUES
(1, 1, 1, 50, '2026-09-20 08:00:00', 'ZN-1-20260920-000001', '2026-10-02'),
(2, 1, 2, 30, '2026-09-25 10:00:00', 'ZN-1-20260925-000002', '2026-10-09'),
(3, 2, 3, 40, '2026-09-22 09:00:00', 'ZN-1-20260922-000003', '2026-10-06'),
(4, 1, 4, 60, '2026-09-23 11:00:00', 'ZN-2-20260923-000004', '2026-10-07'),
(5, 3, 5, 35, '2026-09-26 14:00:00', 'ZN-2-20260926-000005', '2026-10-20');
CREATE TABLE inventory_transactions (
    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    transaction_type VARCHAR(20) NOT NULL,
    quantity_change INTEGER NOT NULL,
    operator VARCHAR(60) NOT NULL,
    operator_role VARCHAR(20),
    reason VARCHAR(100),
    note VARCHAR(160),
    created_at DATETIME NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(batch_id)
);

-- =========================================
-- 第三版：掃碼出入庫、每日盤點、管理設定
-- =========================================

-- 每一箱的唯一箱號（箱標籤）
-- status：in_stock 在庫／out 已出貨／removed 盤點或報廢扣除
CREATE TABLE crates (
    crate_code VARCHAR(60) PRIMARY KEY,
    batch_id INT NOT NULL,
    seq INT NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'in_stock',
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(batch_id)
);

-- FIFO 取貨工作（出貨掃碼確認）
-- status：open 進行中／confirmed 已出庫／cancelled 已取消
CREATE TABLE pick_tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name VARCHAR(100) NOT NULL,
    quantity INT NOT NULL,
    operator VARCHAR(60) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'open',
    verified_batch_id INT,
    created_at DATETIME NOT NULL,
    closed_at DATETIME
);

CREATE TABLE pick_task_lines (
    task_id INTEGER NOT NULL,
    line_no INT NOT NULL,
    batch_id INT NOT NULL,
    quantity INT NOT NULL,
    PRIMARY KEY (task_id, line_no),
    FOREIGN KEY (task_id) REFERENCES pick_tasks(task_id)
);

CREATE TABLE pick_task_scans (
    task_id INTEGER NOT NULL,
    crate_code VARCHAR(60) NOT NULL,
    batch_id INT NOT NULL,
    scanned_at DATETIME NOT NULL,
    PRIMARY KEY (task_id, crate_code),
    FOREIGN KEY (task_id) REFERENCES pick_tasks(task_id)
);

-- 收貨工作（入庫掃碼確認）；batch_id 為預留給這批貨的批次編號
CREATE TABLE inbound_tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INT NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    warehouse_id INT NOT NULL,
    location_code VARCHAR(20) NOT NULL,
    quantity INT NOT NULL,
    lot_number VARCHAR(40) NOT NULL,
    expiry_date DATE,
    operator VARCHAR(60) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'open',
    location_verified INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    closed_at DATETIME
);

CREATE TABLE inbound_task_scans (
    task_id INTEGER NOT NULL,
    crate_code VARCHAR(60) NOT NULL,
    PRIMARY KEY (task_id, crate_code),
    FOREIGN KEY (task_id) REFERENCES inbound_tasks(task_id)
);

-- 每日盤點清單；counted_at 為空代表當日尚未盤點
CREATE TABLE stocktake_items (
    count_date DATE NOT NULL,
    batch_id INT NOT NULL,
    system_quantity INT,
    actual_quantity INT,
    reason VARCHAR(100),
    note VARCHAR(160),
    operator VARCHAR(60),
    counted_at DATETIME,
    PRIMARY KEY (count_date, batch_id),
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(batch_id)
);

-- 安全庫存（每個倉庫、每項商品）
CREATE TABLE safety_levels (
    warehouse_id INT NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    quantity INT NOT NULL,
    PRIMARY KEY (warehouse_id, product_name)
);

-- 久放提醒天數
CREATE TABLE aging_rules (
    product_name VARCHAR(100) PRIMARY KEY,
    days INT NOT NULL
);

-- 人員名單；role：manager 管理者／warehouse 倉管人員
CREATE TABLE users (
    name VARCHAR(60) PRIMARY KEY,
    role VARCHAR(20) NOT NULL
);

-- 依初始庫存數量替每個批次產生箱號，例如 1-BOX-001
WITH RECURSIVE box_seq(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM box_seq WHERE n < 1000
)
INSERT INTO crates (crate_code, batch_id, seq, status)
SELECT
    printf('%d-BOX-%03d', ib.batch_id, box_seq.n),
    ib.batch_id,
    box_seq.n,
    'in_stock'
FROM inventory_batches ib
JOIN box_seq
    ON box_seq.n <= ib.quantity;

INSERT INTO users (name, role) VALUES
('李太太', 'manager'),
('倉管人員', 'warehouse');

INSERT INTO safety_levels (warehouse_id, product_name, quantity) VALUES
(1, '番茄', 50);

INSERT INTO aging_rules (product_name, days) VALUES
('高麗菜', 7);
