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
(batch_id, product_id, location_id, quantity, received_at)
VALUES
(1, 1, 1, 50, '2026-09-20 08:00:00'),
(2, 1, 2, 30, '2026-09-25 10:00:00'),
(3, 2, 3, 40, '2026-09-22 09:00:00'),
(4, 1, 4, 60, '2026-09-23 11:00:00'),
(5, 3, 5, 35, '2026-09-26 14:00:00');