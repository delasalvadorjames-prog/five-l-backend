"""
FastAPI Routes for FIFO Inventory & POS System
Production-ready endpoints with validation and error handling
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from datetime import date
from decimal import Decimal
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session

from models import Product, InventoryBatch
from fifo_service import FIFOInventoryService

# ==========================================
# PYDANTIC MODELS (Request/Response)
# ==========================================

class AddBatchRequest(BaseModel):
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    received_date: date
    expiry_date: date
    supplier: Optional[str] = None
    cost_per_unit: Optional[Decimal] = None
    notes: Optional[str] = None

    class Config:
        schema_extra = {
            "example": {
                "product_id": 1,
                "quantity": 100,
                "received_date": "2024-01-01",
                "expiry_date": "2025-01-01",
                "supplier_id": 5,
                "cost_per_unit": 2.50
            }
        }


class BatchResponse(BaseModel):
    """Batch response model"""
    id: int
    batch_number: str
    quantity: int
    remaining_quantity: int
    received_date: str
    expiry_date: str
    days_until_expiry: int
    is_near_expiry: bool


class AvailableStockResponse(BaseModel):
    """Available stock response"""
    product_id: int
    sku: str
    name: str
    unit_price: float
    available_quantity: int
    batches: List[Dict[str, Any]]
    is_out_of_stock: bool


class ProductDashboardResponse(BaseModel):
    """Comprehensive product inventory status"""
    product_id: int
    sku: str
    name: str
    unit_price: float
    available_quantity: int
    available_batches: List[Dict[str, Any]]
    expired_quantity: int
    expired_batches: List[Dict[str, Any]]
    near_expiry_count: int
    near_expiry_batches: List[Dict[str, Any]]
    is_out_of_stock: bool
    status: str


class SaleItemRequest(BaseModel):
    """Item in a sale"""
    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)

    class Config:
        schema_extra = {
            "example": {
                "product_id": 1,
                "quantity": 5
            }
        }


class ProcessSaleRequest(BaseModel):
    """Process sale request"""
    items: List[SaleItemRequest]
    payment_method: str = Field("cash", regex="^(cash|card|transfer)$")
    cashier_id: Optional[int] = None

    class Config:
        schema_extra = {
            "example": {
                "items": [
                    {"product_id": 1, "quantity": 2},
                    {"product_id": 3, "quantity": 5}
                ],
                "payment_method": "cash",
                "cashier_id": 1
            }
        }


class SaleResponse(BaseModel):
    """Sale response after processing"""
    id: int
    sale_number: str
    total_amount: float
    payment_method: str
    payment_status: str
    created_at: str
    items_count: int


# ==========================================
# ROUTER SETUP
# ==========================================

def get_db():
    """Get database session - imported from main.py at runtime"""
    from main import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


# ==========================================
# INVENTORY ENDPOINTS
# ==========================================

@router.post(
    "/add-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Add new inventory batch",
    description="Add new stock batch with FIFO tracking and expiry date"
)
async def add_batch(request: AddBatchRequest, db: Session = Depends(get_db)):
    """
    Add new inventory batch
    
    **Rules:**
    - Expiry date must be after received date
    - Quantity must be positive
    - Product must exist
    """
    try:
        batch = FIFOInventoryService.add_stock_batch(
            session=db,
            product_id=request.product_id,
            quantity=request.quantity,
            received_date=request.received_date,
            expiry_date=request.expiry_date,
            supplier=request.supplier,
            cost_per_unit=request.cost_per_unit,
            notes=request.notes
        )
        db.commit()

        return {
            "success": True,
            "batch_id": batch.id,
            "batch_number": batch.batch_number,
            "quantity": batch.quantity,
            "message": f"Batch {batch.batch_number} created successfully"
        }
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/available/{product_id}",
    response_model=AvailableStockResponse,
    summary="Get available (non-expired) stock",
    description="Returns available stock quantity and FIFO-ordered batch details"
)
async def get_available_stock(product_id: int, db: Session = Depends(get_db)):
    """
    Get available stock for product
    
    **Returns:**
    - Total available quantity (non-expired only)
    - Batch details in FIFO order (oldest first)
    - Out of stock status
    - Near-expiry warnings
    """
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

        available_qty, batches = FIFOInventoryService.get_available_stock(db, product_id)

        return AvailableStockResponse(
            product_id=product.id,
            sku=product.sku,
            name=product.name,
            unit_price=float(product.unit_price),
            available_quantity=available_qty,
            batches=batches,
            is_out_of_stock=available_qty == 0
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/product/{product_id}",
    response_model=ProductDashboardResponse,
    summary="Get product inventory dashboard",
    description="Comprehensive product status including available, expired, and near-expiry stock"
)
async def get_product_dashboard(product_id: int, db: Session = Depends(get_db)):
    """
    Get complete inventory status for product
    
    **Returns:**
    - Available stock with FIFO batches
    - Expired stock details
    - Near-expiry items (expiring within 7 days)
    - Stock status indicators
    """
    try:
        dashboard = FIFOInventoryService.get_product_dashboard(db, product_id)
        return ProductDashboardResponse(**dashboard)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ==========================================
# POS/SALES ENDPOINTS
# ==========================================

@router.post(
    "/sales/process",
    response_model=SaleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Process sale with FIFO allocation",
    description="Process complete sale transaction with automatic FIFO batch allocation"
)
async def process_sale(request: ProcessSaleRequest, db: Session = Depends(get_db)):
    """
    Process sale transaction
    
    **FIFO Algorithm:**
    1. Validates all products exist and have stock
    2. Allocates batches in FIFO order (oldest first)
    3. Creates sale and line items with batch tracking
    4. Deducts from inventory
    5. Rolls back if any error (transactional safety)
    
    **Rules:**
    - Cannot sell expired stock
    - Cannot sell more than available
    - Expired stock is excluded from availability check
    """
    try:
        # Validate items
        if not request.items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sale must contain at least one item"
            )

        # Convert to service format
        sale_items = [{"product_id": item.product_id, "quantity": item.quantity} for item in request.items]

        # Process sale (FIFO allocation happens here)
        sale = FIFOInventoryService.process_sale_fifo(
            session=db,
            sale_items=sale_items,
            payment_method=request.payment_method,
            cashier_id=request.cashier_id
        )
        db.commit()

        return SaleResponse(
            id=sale.id,
            sale_number=sale.sale_number,
            total_amount=float(sale.total_amount),
            payment_method=sale.payment_method.value,
            payment_status=sale.payment_status.value,
            created_at=sale.created_at.isoformat(),
            items_count=len(sale.items)
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ==========================================
# MAINTENANCE ENDPOINTS
# ==========================================

@router.post(
    "/remove-expired",
    response_model=Dict[str, Any],
    summary="Mark expired batches",
    description="Automatically mark and log expired batches for disposal"
)
async def remove_expired(product_id: Optional[int] = None, db: Session = Depends(get_db)):
    """
    Remove (mark as expired) all expired batches
    
    **Operations:**
    - Marks batches as expired
    - Logs to expired stock log
    - Records inventory movements
    - No actual deletion (audit trail preserved)
    """
    try:
        count = FIFOInventoryService.remove_expired_batches(db, product_id)
        db.commit()

        return {
            "success": True,
            "batches_expired": count,
            "message": f"Marked {count} batches as expired"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ==========================================
# TEST ENDPOINTS FOR NEW TABLES
# ==========================================

class ExpiredStockLogRequest(BaseModel):
    """Test endpoint for expired stock log"""
    product_id: int = Field(..., gt=0)
    batch_id: int = Field(..., gt=0)
    batch_number: Optional[str] = None
    quantity_expired: int = Field(..., gt=0)
    expiry_date: date
    action_taken: Optional[str] = "destroyed"
    notes: Optional[str] = None


class InventoryMovementRequest(BaseModel):
    """Test endpoint for inventory movements"""
    product_id: int = Field(..., gt=0)
    batch_id: Optional[int] = None
    movement_type: str = Field(...)  # STOCK_IN, SALE, RETURN, ADJUSTMENT, EXPIRED, etc.
    quantity: int = Field(..., gt=0)
    reference_id: Optional[int] = None
    reference_type: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None


@router.post(
    "/test/expired-stock-log",
    summary="Test: Log expired stock",
    description="Test endpoint - Log expired stock entry"
)
async def test_log_expired_stock(request: ExpiredStockLogRequest, db: Session = Depends(get_db)):
    """
    **TEST ENDPOINT** - Log expired stock entry
    
    This tests the expired_stock_log table
    """
    try:
        from sqlalchemy import text
        
        # Verify product and batch exist
        product = db.execute(text("SELECT id FROM products WHERE id = :id"), {"id": request.product_id}).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {request.product_id} not found")
        
        batch = db.execute(text("SELECT id FROM inventory_batches WHERE id = :id"), {"id": request.batch_id}).first()
        if not batch:
            raise HTTPException(status_code=404, detail=f"Batch {request.batch_id} not found")
        
        # Insert into expired_stock_log
        result = db.execute(text("""
            INSERT INTO expired_stock_log (
                product_id, batch_id, batch_number, quantity_expired, 
                expiry_date, action_taken, notes, created_at
            ) VALUES (
                :product_id, :batch_id, :batch_number, :quantity_expired,
                :expiry_date, :action_taken, :notes, NOW()
            )
        """), {
            "product_id": request.product_id,
            "batch_id": request.batch_id,
            "batch_number": request.batch_number,
            "quantity_expired": request.quantity_expired,
            "expiry_date": request.expiry_date,
            "action_taken": request.action_taken,
            "notes": request.notes
        })
        
        db.commit()
        
        return {
            "success": True,
            "message": "Expired stock logged successfully",
            "log_id": result.lastrowid,
            "data": {
                "product_id": request.product_id,
                "batch_id": request.batch_id,
                "quantity_expired": request.quantity_expired,
                "expiry_date": str(request.expiry_date),
                "action_taken": request.action_taken
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post(
    "/test/inventory-movement",
    summary="Test: Log inventory movement",
    description="Test endpoint - Log inventory movement entry"
)
async def test_log_inventory_movement(request: InventoryMovementRequest, db: Session = Depends(get_db)):
    """
    **TEST ENDPOINT** - Log inventory movement entry
    
    This tests the inventory_movements table
    
    Movement types: STOCK_IN, SALE, RETURN, ADJUSTMENT_IN, ADJUSTMENT_OUT, EXPIRED, DAMAGED, TRANSFER
    """
    try:
        from sqlalchemy import text
        
        # Verify product exists
        product = db.execute(text("SELECT id FROM products WHERE id = :id"), {"id": request.product_id}).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {request.product_id} not found")
        
        # Verify batch exists if provided
        if request.batch_id:
            batch = db.execute(text("SELECT id FROM inventory_batches WHERE id = :id"), {"id": request.batch_id}).first()
            if not batch:
                raise HTTPException(status_code=404, detail=f"Batch {request.batch_id} not found")
        
        # Insert into inventory_movements
        result = db.execute(text("""
            INSERT INTO inventory_movements (
                product_id, batch_id, movement_type, quantity,
                reference_id, reference_type, notes, created_by, created_at
            ) VALUES (
                :product_id, :batch_id, :movement_type, :quantity,
                :reference_id, :reference_type, :notes, :created_by, NOW()
            )
        """), {
            "product_id": request.product_id,
            "batch_id": request.batch_id,
            "movement_type": request.movement_type,
            "quantity": request.quantity,
            "reference_id": request.reference_id,
            "reference_type": request.reference_type,
            "notes": request.notes,
            "created_by": request.created_by or "test_user"
        })
        
        db.commit()
        
        return {
            "success": True,
            "message": "Inventory movement logged successfully",
            "movement_id": result.lastrowid,
            "data": {
                "product_id": request.product_id,
                "batch_id": request.batch_id,
                "movement_type": request.movement_type,
                "quantity": request.quantity,
                "created_by": request.created_by or "test_user"
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get(
    "/test/expired-stock-logs",
    summary="Test: Get all expired stock logs",
    description="Test endpoint - Retrieve all expired stock log entries"
)
async def test_get_expired_stock_logs(product_id: Optional[int] = None, db: Session = Depends(get_db)):
    """
    **TEST ENDPOINT** - Get all expired stock logs
    
    Optional filter by product_id
    """
    try:
        from sqlalchemy import text
        
        if product_id:
            query = """
                SELECT id, product_id, batch_id, batch_number, quantity_expired, 
                       expiry_date, action_taken, notes, created_at
                FROM expired_stock_log
                WHERE product_id = :product_id
                ORDER BY created_at DESC
            """
            rows = db.execute(text(query), {"product_id": product_id}).fetchall()
        else:
            query = """
                SELECT id, product_id, batch_id, batch_number, quantity_expired, 
                       expiry_date, action_taken, notes, created_at
                FROM expired_stock_log
                ORDER BY created_at DESC
                LIMIT 100
            """
            rows = db.execute(text(query)).fetchall()
        
        logs = []
        for row in rows:
            logs.append({
                "id": row[0],
                "product_id": row[1],
                "batch_id": row[2],
                "batch_number": row[3],
                "quantity_expired": row[4],
                "expiry_date": str(row[5]),
                "action_taken": row[6],
                "notes": row[7],
                "created_at": str(row[8])
            })
        
        return {
            "success": True,
            "count": len(logs),
            "logs": logs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get(
    "/test/inventory-movements",
    summary="Test: Get all inventory movements",
    description="Test endpoint - Retrieve all inventory movement entries"
)
async def test_get_inventory_movements(product_id: Optional[int] = None, movement_type: Optional[str] = None, db: Session = Depends(get_db)):
    """
    **TEST ENDPOINT** - Get all inventory movements
    
    Optional filters by product_id and/or movement_type
    """
    try:
        from sqlalchemy import text
        
        conditions = []
        params = {}
        
        if product_id:
            conditions.append("product_id = :product_id")
            params["product_id"] = product_id
        
        if movement_type:
            conditions.append("movement_type = :movement_type")
            params["movement_type"] = movement_type
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT id, product_id, batch_id, movement_type, quantity,
                   reference_id, reference_type, notes, created_by, created_at
            FROM inventory_movements
            {where_clause}
            ORDER BY created_at DESC
            LIMIT 100
        """
        
        rows = db.execute(text(query), params).fetchall()
        
        movements = []
        for row in rows:
            movements.append({
                "id": row[0],
                "product_id": row[1],
                "batch_id": row[2],
                "movement_type": row[3],
                "quantity": row[4],
                "reference_id": row[5],
                "reference_type": row[6],
                "notes": row[7],
                "created_by": row[8],
                "created_at": str(row[9])
            })
        
        return {
            "success": True,
            "count": len(movements),
            "movements": movements
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.delete(
    "/test/clear-logs",
    summary="Test: Clear all test logs",
    description="Test endpoint - DELETE all expired stock logs and inventory movements (USE WITH CAUTION)"
)
async def test_clear_logs(confirm: str = "", db: Session = Depends(get_db)):
    """
    **TEST ENDPOINT - DESTRUCTIVE** - Clear all logs
    
    Requires confirm='yes-delete-all' to proceed
    """
    if confirm != "yes-delete-all":
        raise HTTPException(status_code=400, detail="Must pass confirm='yes-delete-all' to proceed")
    
    try:
        from sqlalchemy import text
        
        db.execute(text("DELETE FROM expired_stock_log"))
        db.execute(text("DELETE FROM inventory_movements"))
        db.commit()
        
        return {
            "success": True,
            "message": "All test logs cleared"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get(
    "/health",
    summary="Health check",
    description="Verify API is operational"
)
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "FIFO Inventory System"}
