-- Five-L Pharmacy database schema for XAMPP / phpMyAdmin
-- Compatible with backend/main.py and the current transactions/customers routes.
-- Import this file in phpMyAdmin, then point backend/.env to this database.

CREATE DATABASE IF NOT EXISTS `five-l`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `five-l`;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  full_name VARCHAR(100) NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash TEXT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'staff',
  assigned_category VARCHAR(255) NULL,
  account_status VARCHAR(20) NOT NULL DEFAULT 'active',
  can_process_pos TINYINT(1) NOT NULL DEFAULT 1,
  can_view_reports TINYINT(1) NOT NULL DEFAULT 1,
  can_adjust_inventory TINYINT(1) NOT NULL DEFAULT 1,
  can_approve_voids TINYINT(1) NOT NULL DEFAULT 1,
  created_by_admin INT NULL,
  last_login DATETIME NULL,
  active_token TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_users_email (email),
  INDEX idx_users_role (role),
  INDEX idx_users_status (account_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  reset_code VARCHAR(32) NOT NULL,
  used_at DATETIME NULL,
  expires_at DATETIME NOT NULL,
  INDEX idx_password_reset_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS login_otp_tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  otp_code VARCHAR(32) NOT NULL,
  used_at DATETIME NULL,
  expires_at DATETIME NOT NULL,
  INDEX idx_login_otp_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS medicines (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(200) NOT NULL,
  medicine_name VARCHAR(200) NULL,
  product_code VARCHAR(50) NULL UNIQUE,
  category VARCHAR(100) NULL,
  classification VARCHAR(50) NOT NULL DEFAULT 'Generic',
  dosage_form VARCHAR(100) NOT NULL DEFAULT 'Tablet',
  strength VARCHAR(100) NULL,
  volume VARCHAR(100) NULL,
  manufacturer VARCHAR(200) NULL,
  stock INT NULL DEFAULT 0,
  reorder_level INT NULL DEFAULT 0,
  expiry DATE NULL,
  avg_daily_sales FLOAT NULL DEFAULT 0,
  unit_price FLOAT NULL DEFAULT 0,
  supplier VARCHAR(200) NULL,
  unit_type VARCHAR(50) NOT NULL DEFAULT 'pcs',
  base_unit VARCHAR(20) NOT NULL DEFAULT 'pcs',
  purchase_unit VARCHAR(50) NOT NULL DEFAULT 'pcs',
  conversion_factor INT NOT NULL DEFAULT 1,
  is_new_arrival TINYINT(1) NOT NULL DEFAULT 0,
  is_archived TINYINT(1) NOT NULL DEFAULT 0,
  added_by INT NULL,
  owner_admin_id INT NULL,
  updated_by INT NULL,
  assigned_staff INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_restocked_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_medicines_name (name),
  INDEX idx_medicines_medicine_name (medicine_name),
  INDEX idx_medicines_owner (owner_admin_id),
  INDEX idx_medicines_category (category),
  CONSTRAINT fk_medicines_added_by FOREIGN KEY (added_by) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_medicines_owner FOREIGN KEY (owner_admin_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_medicines_updated_by FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_medicines_assigned_staff FOREIGN KEY (assigned_staff) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS medicine_supplies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  medicine_id INT NOT NULL,
  batch_number VARCHAR(100) NOT NULL,
  quantity INT NOT NULL DEFAULT 0,
  supplier VARCHAR(200) NULL,
  expiry_date DATE NOT NULL,
  received_date DATE NULL,
  unit_cost FLOAT NOT NULL DEFAULT 0,
  selling_price FLOAT NOT NULL DEFAULT 0,
  is_archived TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_supplies_medicine (medicine_id),
  INDEX idx_supplies_expiry (expiry_date),
  CONSTRAINT fk_supplies_medicine FOREIGN KEY (medicine_id) REFERENCES medicines(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inventory_alert_configs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  dosage_form VARCHAR(100) NOT NULL UNIQUE,
  low_stock_threshold INT NOT NULL DEFAULT 0,
  expiry_alert_days INT NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO inventory_alert_configs (dosage_form, low_stock_threshold, expiry_alert_days) VALUES
  ('Tablet', 20, 30), ('Capsule', 20, 30), ('Syrup', 10, 30),
  ('Suspension', 10, 30), ('Injectable', 5, 60), ('Cream', 10, 30),
  ('Ointment', 10, 30), ('Drops', 10, 30), ('Other', 5, 30);

CREATE TABLE IF NOT EXISTS categories (
  category_id INT AUTO_INCREMENT PRIMARY KEY,
  category_name VARCHAR(150) NOT NULL,
  description TEXT NULL,
  owner_admin_id INT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_categories_owner (owner_admin_id),
  CONSTRAINT fk_categories_owner FOREIGN KEY (owner_admin_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS suppliers (
  supplier_id INT AUTO_INCREMENT PRIMARY KEY,
  supplier_name VARCHAR(200) NOT NULL,
  contact_person VARCHAR(200) NULL,
  email VARCHAR(255) NULL,
  phone VARCHAR(60) NULL,
  address TEXT NULL,
  city VARCHAR(100) NULL,
  country VARCHAR(100) NULL,
  payment_terms VARCHAR(100) NULL,
  owner_admin_id INT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_suppliers_owner (owner_admin_id),
  CONSTRAINT fk_suppliers_owner FOREIGN KEY (owner_admin_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS price_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  inventory_id INT NOT NULL,
  old_price FLOAT NOT NULL,
  new_price FLOAT NOT NULL,
  updated_by VARCHAR(100) NULL,
  status ENUM('PENDING', 'APPROVED', 'REJECTED') NOT NULL DEFAULT 'PENDING',
  approved_by VARCHAR(255) NULL,
  approved_at DATETIME NULL,
  reason TEXT NULL,
  adjustment_type VARCHAR(32) NULL,
  old_quantity INT NULL,
  new_quantity INT NULL,
  remarks TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_price_history_inventory (inventory_id),
  CONSTRAINT fk_price_history_supply FOREIGN KEY (inventory_id) REFERENCES medicine_supplies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sales_transactions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  medicine_id INT NULL,
  quantity INT NULL,
  unit_price FLOAT NULL,
  total FLOAT NULL,
  transaction_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_sales_transactions_medicine (medicine_id),
  INDEX idx_sales_transactions_date (transaction_date),
  CONSTRAINT fk_sales_transactions_medicine FOREIGN KEY (medicine_id) REFERENCES medicines(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS stock_movements (
  id INT AUTO_INCREMENT PRIMARY KEY,
  medicine_id INT NULL,
  type VARCHAR(50) NULL,
  quantity INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_stock_movements_medicine (medicine_id),
  INDEX idx_stock_movements_date (created_at),
  CONSTRAINT fk_stock_movements_medicine FOREIGN KEY (medicine_id) REFERENCES medicines(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS transactions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  transaction_number VARCHAR(64) NOT NULL UNIQUE,
  sale_reference VARCHAR(64) NULL,
  medicine_id INT NOT NULL,
  medicine_name VARCHAR(255) NOT NULL,
  quantity INT NOT NULL,
  price FLOAT NOT NULL,
  total_amount FLOAT NOT NULL,
  customer_name VARCHAR(255) NULL,
  cashier_name VARCHAR(255) NULL,
  payment_method VARCHAR(64) NULL,
  transaction_type VARCHAR(64) NOT NULL DEFAULT 'sale',
  cash_received FLOAT NULL,
  change_amount FLOAT NULL,
  customer_id INT NULL,
  customer_id_number VARCHAR(100) NULL,
  discount_eligible TINYINT(1) NULL,
  eligible_subtotal FLOAT NULL,
  non_eligible_subtotal FLOAT NULL,
  sale_unit VARCHAR(32) NULL,
  unit_factor FLOAT NULL,
  category VARCHAR(255) NULL,
  classification VARCHAR(64) NULL,
  dosage_form VARCHAR(100) NULL,
  customer_type VARCHAR(32) NULL,
  gross_amount FLOAT NULL,
  vatable_sales FLOAT NULL,
  vat_amount FLOAT NULL,
  vat_exempt_sales FLOAT NULL,
  discount_amount FLOAT NULL,
  senior_citizen_discount FLOAT NULL,
  pwd_discount FLOAT NULL,
  other_discount FLOAT NULL,
  net_sales FLOAT NULL,
  owner_admin_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_transactions_date (created_at),
  INDEX idx_transactions_medicine (medicine_id),
  INDEX idx_transactions_sale_reference (sale_reference),
  INDEX idx_transactions_owner (owner_admin_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS transaction_void_requests (
  id INT AUTO_INCREMENT PRIMARY KEY,
  transaction_id INT NOT NULL,
  requested_by VARCHAR(255) NOT NULL,
  reason TEXT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  reviewed_by VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at DATETIME NULL,
  owner_admin_id INT NULL,
  INDEX idx_void_status (status),
  INDEX idx_void_transaction (transaction_id),
  INDEX idx_void_owner (owner_admin_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS customers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  customer_type VARCHAR(32) NOT NULL,
  id_number VARCHAR(100) NOT NULL,
  full_name VARCHAR(255) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  registered_by VARCHAR(255) NULL,
  date_registered DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_customers_type_id (customer_type, id_number),
  INDEX idx_customers_type (customer_type),
  INDEX idx_customers_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS active_admin_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  token TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME NOT NULL,
  INDEX idx_admin_sessions_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS login_attempts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  ip_address VARCHAR(64) NULL,
  attempt_count INT NOT NULL DEFAULT 0,
  locked_until DATETIME NULL,
  last_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_login_attempts_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  event_type VARCHAR(64) NOT NULL,
  email VARCHAR(255) NULL,
  ip_address VARCHAR(64) NULL,
  detail TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_audit_email (email),
  INDEX idx_audit_event (event_type),
  INDEX idx_audit_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS medicine_audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  action_type VARCHAR(64) NOT NULL,
  medicine_name VARCHAR(200) NOT NULL,
  performed_by VARCHAR(255) NOT NULL,
  role VARCHAR(20) NOT NULL,
  timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  old_value TEXT NULL,
  new_value TEXT NULL,
  INDEX idx_medicine_audit_action (action_type),
  INDEX idx_medicine_audit_name (medicine_name),
  INDEX idx_medicine_audit_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_alerts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  alert_type VARCHAR(64) NOT NULL,
  message TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  is_read TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_admin_alert_type (alert_type),
  INDEX idx_admin_alert_read (is_read)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Compatibility tables used by the FIFO/forecast helper routes.
CREATE TABLE IF NOT EXISTS products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  category VARCHAR(120) NULL,
  unit_price DECIMAL(12,2) NOT NULL DEFAULT 0,
  reorder_level INT NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_products_name (name),
  INDEX idx_products_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inventory_batches (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  batch_number VARCHAR(100) NOT NULL,
  quantity INT NOT NULL DEFAULT 0,
  remaining_quantity INT NOT NULL DEFAULT 0,
  received_date DATE NOT NULL,
  expiry_date DATE NOT NULL,
  supplier VARCHAR(255) NULL,
  cost_per_unit DECIMAL(10,2) NULL DEFAULT 0,
  notes VARCHAR(500) NULL,
  is_expired TINYINT(1) NOT NULL DEFAULT 0,
  is_removed TINYINT(1) NOT NULL DEFAULT 0,
  removed_reason VARCHAR(120) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_batches_product (product_id),
  INDEX idx_batches_expiry (expiry_date),
  CONSTRAINT fk_batches_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS forecasts (
  forecast_id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  forecast_month DATE NULL,
  predicted_demand FLOAT NULL,
  algorithm VARCHAR(80) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_forecasts_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inventory_movements (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  batch_id INT NULL,
  movement_type VARCHAR(50) NOT NULL,
  quantity INT NOT NULL,
  reference_id INT NULL,
  reference_type VARCHAR(50) NULL,
  notes TEXT NULL,
  created_by VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_inventory_movements_product (product_id),
  INDEX idx_inventory_movements_batch (batch_id),
  CONSTRAINT fk_inventory_movements_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  CONSTRAINT fk_inventory_movements_batch FOREIGN KEY (batch_id) REFERENCES inventory_batches(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS expired_stock_log (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  batch_id INT NOT NULL,
  batch_number VARCHAR(100) NULL,
  quantity_expired INT NOT NULL DEFAULT 0,
  expiry_date DATE NOT NULL,
  detected_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  action_taken VARCHAR(255) NULL,
  notes TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_expired_stock_product (product_id),
  INDEX idx_expired_stock_expiry (expiry_date),
  CONSTRAINT fk_expired_stock_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  CONSTRAINT fk_expired_stock_batch FOREIGN KEY (batch_id) REFERENCES inventory_batches(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- The backend creates the first admin account through the signup flow.
-- Do not insert plain-text passwords here.
