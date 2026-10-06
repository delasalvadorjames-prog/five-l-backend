# Test New FIFO Tables - Quick Reference

## Setup

1. **Start the backend server** (in one terminal):
```bash
cd backend
python3 main.py
```

2. **Run the Python test script** (in another terminal):
```bash
cd backend
python3 test_new_tables.py
```

---

## Manual Testing with cURL

### 1. Test Health Check
```bash
curl -X GET "http://localhost:8000/api/inventory/health"
```

### 2. Log Expired Stock Entry
Replace `PRODUCT_ID` and `BATCH_ID` with actual values from your database.

```bash
curl -X POST "http://localhost:8000/api/inventory/test/expired-stock-log" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": 1,
    "batch_id": 1,
    "batch_number": "BATCH-001",
    "quantity_expired": 50,
    "expiry_date": "2024-01-15",
    "action_taken": "destroyed",
    "notes": "Test expired stock log"
  }'
```

### 3. Log Inventory Movement - Stock In

```bash
curl -X POST "http://localhost:8000/api/inventory/test/inventory-movement" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": 1,
    "batch_id": null,
    "movement_type": "STOCK_IN",
    "quantity": 100,
    "reference_id": null,
    "reference_type": "purchase_order",
    "notes": "Test stock in",
    "created_by": "supplier_1"
  }'
```

### 4. Log Inventory Movement - Sale

```bash
curl -X POST "http://localhost:8000/api/inventory/test/inventory-movement" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": 1,
    "batch_id": 1,
    "movement_type": "SALE",
    "quantity": 5,
    "reference_id": 1,
    "reference_type": "sale",
    "notes": "Test sale",
    "created_by": "cashier_1"
  }'
```

### 5. Log Inventory Movement - Return

```bash
curl -X POST "http://localhost:8000/api/inventory/test/inventory-movement" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": 1,
    "batch_id": 1,
    "movement_type": "RETURN",
    "quantity": 2,
    "reference_id": 1,
    "reference_type": "sale",
    "notes": "Customer return",
    "created_by": "cashier_1"
  }'
```

### 6. Get All Expired Stock Logs

```bash
curl -X GET "http://localhost:8000/api/inventory/test/expired-stock-logs"
```

### 7. Get Expired Stock Logs for Specific Product

Replace `PRODUCT_ID` with actual value:

```bash
curl -X GET "http://localhost:8000/api/inventory/test/expired-stock-logs?product_id=1"
```

### 8. Get All Inventory Movements

```bash
curl -X GET "http://localhost:8000/api/inventory/test/inventory-movements"
```

### 9. Get Inventory Movements by Product

```bash
curl -X GET "http://localhost:8000/api/inventory/test/inventory-movements?product_id=1"
```

### 10. Get Inventory Movements by Type

```bash
curl -X GET "http://localhost:8000/api/inventory/test/inventory-movements?movement_type=SALE"
```

### 11. Get Inventory Movements Filtered (Product + Type)

```bash
curl -X GET "http://localhost:8000/api/inventory/test/inventory-movements?product_id=1&movement_type=SALE"
```

### 12. Clear All Test Logs (DESTRUCTIVE)

```bash
curl -X DELETE "http://localhost:8000/api/inventory/test/clear-logs?confirm=yes-delete-all"
```

---

## Available Movement Types

- `STOCK_IN` - Stock received/added
- `SALE` - Stock sold
- `RETURN` - Customer return
- `ADJUSTMENT_IN` - Inventory adjustment (add)
- `ADJUSTMENT_OUT` - Inventory adjustment (remove)
- `EXPIRED` - Stock expired/removed
- `DAMAGED` - Stock damaged
- `TRANSFER` - Stock transfer between locations

---

## Database Tables

### expired_stock_log
Tracks expired inventory:
- `id` - Primary key
- `product_id` - Product reference
- `batch_id` - Batch reference
- `batch_number` - Batch number
- `quantity_expired` - Amount expired
- `expiry_date` - When it expired
- `detected_date` - When detected
- `action_taken` - What was done (destroyed, returned, etc)
- `notes` - Additional notes
- `created_at` - Log timestamp

### inventory_movements
Tracks all stock movements:
- `id` - Primary key
- `product_id` - Product reference
- `batch_id` - Batch reference (optional)
- `movement_type` - Type of movement
- `quantity` - Amount moved
- `reference_id` - Link to source (sale_id, batch_id, etc)
- `reference_type` - Type of reference
- `notes` - Additional notes
- `created_by` - User who created
- `created_at` - Movement timestamp

---

## Common Queries to Check Data

Using MySQL CLI:

```sql
-- Count expired stock logs
SELECT COUNT(*) as total FROM expired_stock_log;

-- Count inventory movements
SELECT COUNT(*) as total FROM inventory_movements;

-- Get all expired stock by product
SELECT p.name, e.quantity_expired, e.expiry_date, e.action_taken
FROM expired_stock_log e
JOIN products p ON e.product_id = p.id
ORDER BY e.created_at DESC;

-- Get all movements by type
SELECT movement_type, COUNT(*) as count
FROM inventory_movements
GROUP BY movement_type;

-- Get sales history
SELECT im.*, p.name 
FROM inventory_movements im
JOIN products p ON im.product_id = p.id
WHERE movement_type = 'SALE'
ORDER BY im.created_at DESC;
```

---

## Troubleshooting

### API Not Responding
- Check that backend is running: `python3 main.py` in `/backend`
- Verify database is running
- Check logs for errors

### Cannot Find Products or Batches
- Make sure you have products: `SELECT * FROM products LIMIT 5;`
- Make sure you have batches: `SELECT * FROM inventory_batches LIMIT 5;`
- Use actual IDs from your database in test requests

### Foreign Key Errors
- `product_id` must exist in `products` table
- `batch_id` must exist in `inventory_batches` table
- When logging movements, ensure product_id is valid
