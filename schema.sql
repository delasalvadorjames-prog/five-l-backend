-- ============================================
-- INVENTORY + POS SYSTEM DATABASE SCHEMA
-- Production-Ready with FIFO Support
-- ============================================

-- Products Table
CREATE TABLE products (
    id INT PRIMARY KEY AUTO_INCREMENT,
    sku VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    unit_price DECIMAL(10, 2) NOT NULL,
    reorder_level INT DEFAULT 10,
    status ENUM('active', 'inactive', 'discontinued') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_sku (sku),
    INDEX idx_status (status)
);

-- Inventory Batches Table (Core FIFO structure)
-- Each batch represents a purchase/receipt
CREATE TABLE inventory_batches (
    id INT PRIMARY KEY AUTO_INCREMENT,
    product_id INT NOT NULL,
    batch_number VARCHAR(100) UNIQUE NOT NULL,
    quantity INT NOT NULL,
    remaining_quantity INT NOT NULL,
    received_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    supplier_id INT,
    cost_per_unit DECIMAL(10, 2),
    notes TEXT,
    is_expired BOOLEAN DEFAULT FALSE,
    is_removed BOOLEAN NOT NULL DEFAULT FALSE,
    removed_reason VARCHAR(120) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_product_received (product_id, received_date),
    INDEX idx_product_expiry (product_id, expiry_date),
    INDEX idx_remaining (remaining_quantity),
    UNIQUE KEY unique_batch (product_id, batch_number)
);

-- Sales Table (Transaction header)
CREATE TABLE sales (
    id INT PRIMARY KEY AUTO_INCREMENT,
    sale_number VARCHAR(50) UNIQUE NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    payment_method ENUM('cash', 'card', 'transfer') DEFAULT 'cash',
    payment_status ENUM('pending', 'completed', 'refunded') DEFAULT 'pending',
    cashier_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_date (created_at),
    INDEX idx_status (payment_status)
);

-- Sale Items Table (Transaction line items with batch tracking)
CREATE TABLE sale_items (
    id INT PRIMARY KEY AUTO_INCREMENT,
    sale_id INT NOT NULL,
    product_id INT NOT NULL,
    batch_id INT NOT NULL,
    quantity_sold INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    subtotal DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id),
    INDEX idx_sale (sale_id),
    INDEX idx_batch (batch_id)
);

-- Inventory Movements Table (Audit trail)
CREATE TABLE inventory_movements (
    id INT PRIMARY KEY AUTO_INCREMENT,
    batch_id INT NOT NULL,
    movement_type ENUM('receipt', 'sale', 'adjustment', 'expired') NOT NULL,
    quantity_change INT NOT NULL,
    reference_id INT,
    reference_type VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id) ON DELETE CASCADE,
    INDEX idx_batch (batch_id),
    INDEX idx_date (created_at)
);

-- Dosage-form-specific inventory alert overrides.
-- Global Inventory Rules remain the fallback when no active row matches.
CREATE TABLE inventory_alert_configs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dosage_form VARCHAR(100) NOT NULL UNIQUE,
    low_stock_threshold INT NOT NULL DEFAULT 0,
    expiry_alert_days INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT IGNORE INTO inventory_alert_configs (dosage_form, low_stock_threshold, expiry_alert_days)
VALUES
    ('Tablet', 20, 30),
    ('Capsule', 20, 30),
    ('Syrup', 10, 30),
    ('Suspension', 10, 30),
    ('Injectable', 5, 60),
    ('Cream', 10, 30),
    ('Ointment', 10, 30),
    ('Drops', 10, 30),
    ('Other', 5, 30);

-- Expired Stock Log (For reporting and audit)
CREATE TABLE expired_stock_log (
    id INT PRIMARY KEY AUTO_INCREMENT,
    batch_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity_expired INT NOT NULL,
    expiry_date DATE NOT NULL,
    expired_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id) ON DELETE CASCADE,
    INDEX idx_expired_date (expired_on)
);

-- ============================================
-- VIEWS FOR EASY QUERIES
-- ============================================

-- Available Stock View (Non-expired only)
CREATE VIEW available_stock AS
SELECT 
    p.id,
    p.sku,
    p.name,
    p.unit_price,
    SUM(ib.remaining_quantity) as available_quantity,
    COUNT(DISTINCT ib.id) as batch_count,
    MIN(ib.received_date) as oldest_batch_date
FROM products p
LEFT JOIN inventory_batches ib ON p.id = ib.product_id 
    AND ib.remaining_quantity > 0 
    AND ib.expiry_date > CURDATE()
    AND ib.is_expired = FALSE
GROUP BY p.id, p.sku, p.name, p.unit_price;

-- Expired Stock View
CREATE VIEW expired_stock AS
SELECT 
    p.id,
    p.sku,
    p.name,
    SUM(ib.remaining_quantity) as expired_quantity,
    COUNT(DISTINCT ib.id) as expired_batch_count,
    MIN(ib.expiry_date) as earliest_expiry
FROM products p
LEFT JOIN inventory_batches ib ON p.id = ib.product_id 
    AND ib.remaining_quantity > 0 
    AND ib.expiry_date <= CURDATE()
GROUP BY p.id, p.sku, p.name;

-- Near Expiry View (Expiring within 7 days)
CREATE VIEW near_expiry_stock AS
SELECT 
    p.id,
    p.sku,
    p.name,
    SUM(ib.remaining_quantity) as near_expiry_quantity,
    COUNT(DISTINCT ib.id) as near_expiry_batch_count,
    MIN(ib.expiry_date) as earliest_expiry,
    DATEDIFF(MIN(ib.expiry_date), CURDATE()) as days_until_expiry
FROM products p
LEFT JOIN inventory_batches ib ON p.id = ib.product_id 
    AND ib.remaining_quantity > 0 
    AND ib.expiry_date > CURDATE()
    AND ib.expiry_date <= DATE_ADD(CURDATE(), INTERVAL 7 DAY)
GROUP BY p.id, p.sku, p.name;
