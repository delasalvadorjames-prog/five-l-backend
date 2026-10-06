"""
FIFO Inventory Service - Production Grade
Implements First-In-First-Out algorithm for pharmaceutical inventory
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
import logging

from models import (
    Product,
    InventoryBatch,
    Sale,
    SaleItem,
    InventoryMovement,
    ExpiredStockLog,
    MovementType,
    PaymentMethod,
    PaymentStatus,
)

logger = logging.getLogger(__name__)


class FIFOInventoryService:
    """
    Production-grade FIFO inventory management for pharmaceuticals.
    
    Core Principle:
    Always sell the OLDEST stock first (earliest received_date)
    
    Expiry Rule:
    NEVER sell expired stock (expiry_date <= today is EXPIRED)
    """

    @staticmethod
    def _generate_sale_number() -> str:
        """Generate unique sale number"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        import random
        suffix = random.randint(1000, 9999)
        return f"SALE-{timestamp}-{suffix}"

    @staticmethod
    def _generate_batch_number(product_sku: str) -> str:
        """Generate unique batch number"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        import random
        suffix = random.randint(1000, 9999)
        return f"BATCH-{product_sku}-{timestamp}-{suffix}"

    # ==========================================
    # CORE FIFO LOGIC
    # ==========================================

    @staticmethod
    def get_available_stock(session: Session, product_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Get TOTAL available (non-expired) stock for product.
        
        Returns:
            (total_quantity, list_of_batches_in_fifo_order)
            
        FIFO Order: Sorted by received_date (oldest first)
        Not For Sale: Any batch expiring within 30 days is excluded
        """
        today = date.today()
        sale_cutoff = today + timedelta(days=30)
        
        # Query non-expired batches, FIFO ordered (oldest first)
        batches = (
            session.query(InventoryBatch)
            .filter(
                and_(
                    InventoryBatch.product_id == product_id,
                    InventoryBatch.remaining_quantity > 0,
                    InventoryBatch.expiry_date > sale_cutoff,  # Exclude Not For Sale batches
                    InventoryBatch.is_expired == False
                )
            )
            .order_by(InventoryBatch.expiry_date, InventoryBatch.received_date, InventoryBatch.id)  # FEFO
            .all()
        )

        total_quantity = sum(b.remaining_quantity for b in batches)
        
        batch_details = [
            {
                "id": b.id,
                "batch_number": b.batch_number,
                "quantity": b.remaining_quantity,
                "received_date": b.received_date.isoformat(),
                "expiry_date": b.expiry_date.isoformat(),
                "days_until_expiry": (b.expiry_date - today).days,
                "is_near_expiry": 0 < (b.expiry_date - today).days <= 7,
                "supplier": b.supplier,
            }
            for b in batches
        ]

        return total_quantity, batch_details

    @staticmethod
    def get_expired_stock(session: Session, product_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Get EXPIRED stock quantity and details.
        
        Returns:
            (total_expired_quantity, list_of_expired_batches)
        """
        today = date.today()
        sale_cutoff = today + timedelta(days=30)
        
        expired_batches = (
            session.query(InventoryBatch)
            .filter(
                and_(
                    InventoryBatch.product_id == product_id,
                    InventoryBatch.remaining_quantity > 0,
                    InventoryBatch.expiry_date <= today
                )
            )
            .order_by(InventoryBatch.expiry_date)
            .all()
        )

        total_expired = sum(b.remaining_quantity for b in expired_batches)
        
        expired_details = [
            {
                "id": b.id,
                "batch_number": b.batch_number,
                "quantity": b.remaining_quantity,
                "expiry_date": b.expiry_date.isoformat(),
                "days_expired": (today - b.expiry_date).days,
                "supplier": b.supplier,
            }
            for b in expired_batches
        ]

        return total_expired, expired_details

    @staticmethod
    def add_stock_batch(
        session: Session,
        product_id: int,
        quantity: int,
        received_date: date,
        expiry_date: date,
        supplier: Optional[str] = None,
        cost_per_unit: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> InventoryBatch:
        """
        Add new stock batch to inventory.
        
        Args:
            session: Database session
            product_id: Product ID
            quantity: Quantity received
            received_date: Date received
            expiry_date: Date expires
            supplier: Supplier name
            cost_per_unit: Unit cost
            notes: Batch notes
            
        Returns:
            Created InventoryBatch
            
        Raises:
            ValueError: If validation fails
        """
        # Validate product exists
        product = session.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Product {product_id} not found")

        # Validate dates
        if expiry_date <= received_date:
            raise ValueError("Expiry date must be after received date")
        
        if expiry_date <= date.today():
            raise ValueError("Cannot add already expired batch")

        # Create batch
        batch = InventoryBatch(
            product_id=product_id,
            batch_number=FIFOInventoryService._generate_batch_number(product.sku),
            quantity=quantity,
            remaining_quantity=quantity,
            received_date=received_date,
            expiry_date=expiry_date,
            supplier=supplier,
            cost_per_unit=cost_per_unit,
            notes=notes,
            is_expired=False
        )

        session.add(batch)
        session.flush()  # Get batch ID

        # Log movement
        movement = InventoryMovement(
            batch_id=batch.id,
            product_id=product_id,
            movement_type=MovementType.RECEIPT,
            quantity_change=quantity,
            reference_type="batch_receipt",
            notes=f"Received batch {batch.batch_number}"
        )
        session.add(movement)

        return batch

    @staticmethod
    def allocate_fifo_batches(
        session: Session,
        product_id: int,
        required_quantity: int
    ) -> List[Tuple[InventoryBatch, int]]:
        """
        Allocate batches using FIFO algorithm.
        
        Algorithm:
        1. Get all non-expired batches sorted by received_date (OLDEST FIRST)
        2. Allocate from oldest batch first
        3. Move to next batch if current insufficient
        
        Args:
            session: Database session
            product_id: Product ID
            required_quantity: Quantity needed
            
        Returns:
            List of (batch, quantity_to_allocate) tuples
            
        Raises:
            ValueError: If insufficient stock
        """
        today = date.today()
        
        # Get available stock in FIFO order
        available_qty, batch_details = FIFOInventoryService.get_available_stock(session, product_id)
        
        if available_qty < required_quantity:
            raise ValueError(
                f"Insufficient stock: Required {required_quantity}, "
                f"Available {available_qty}"
            )

        # Get batches in FIFO order (oldest first)
        batches = (
            session.query(InventoryBatch)
            .filter(
                and_(
                    InventoryBatch.product_id == product_id,
                    InventoryBatch.remaining_quantity > 0,
                    InventoryBatch.expiry_date > sale_cutoff,
                    InventoryBatch.is_expired == False
                )
            )
            .order_by(InventoryBatch.expiry_date, InventoryBatch.received_date, InventoryBatch.id)  # FEFO
            .all()
        )

        # Allocate from oldest batches first
        allocation: List[Tuple[InventoryBatch, int]] = []
        remaining_to_allocate = required_quantity

        for batch in batches:
            if remaining_to_allocate <= 0:
                break

            qty_from_batch = min(batch.remaining_quantity, remaining_to_allocate)
            allocation.append((batch, qty_from_batch))
            remaining_to_allocate -= qty_from_batch

        return allocation

    @staticmethod
    def process_sale_fifo(
        session: Session,
        sale_items: List[Dict[str, Any]],
        payment_method: str = "cash",
        cashier_id: Optional[int] = None,
        customer_name: Optional[str] = None,
    ) -> Sale:
        """
        Process complete sale using FIFO allocation.
        
        Args:
            session: Database session
            sale_items: List of {product_id, quantity}
            payment_method: Payment method
            cashier_id: Cashier reference
            customer_name: Customer name
            
        Returns:
            Created Sale with SaleItems
            
        Raises:
            ValueError: If any product out of stock
        """
        # Create sale header
        sale = Sale(
            sale_number=FIFOInventoryService._generate_sale_number(),
            total_amount=0.0,
            payment_method=PaymentMethod(payment_method),
            payment_status=PaymentStatus.COMPLETED,
            cashier_id=cashier_id,
            customer_name=customer_name,
        )
        session.add(sale)
        session.flush()  # Get sale ID

        total_amount = 0.0

        # Process each item
        for item in sale_items:
            product_id = item["product_id"]
            quantity = item["quantity"]

            # Validate product
            product = session.query(Product).filter(Product.id == product_id).first()
            if not product:
                raise ValueError(f"Product {product_id} not found")

            # Allocate batches using FIFO
            allocation = FIFOInventoryService.allocate_fifo_batches(
                session, product_id, quantity
            )

            # Create sale items and deduct from batches
            for batch, qty_from_batch in allocation:
                # Create sale item (batch reference for audit)
                sale_item = SaleItem(
                    sale_id=sale.id,
                    product_id=product_id,
                    batch_id=batch.id,
                    quantity=qty_from_batch,
                    unit_price=product.unit_price,
                    line_total=product.unit_price * qty_from_batch
                )
                session.add(sale_item)

                # Deduct from batch
                batch.remaining_quantity -= qty_from_batch
                batch.updated_at = datetime.now()

                # Log movement
                movement = InventoryMovement(
                    batch_id=batch.id,
                    product_id=product_id,
                    movement_type=MovementType.SALE,
                    quantity_change=-qty_from_batch,
                    reference_type="sale",
                    reference_id=sale.id,
                    notes=f"Sold {qty_from_batch} units in sale #{sale.id}"
                )
                session.add(movement)

                total_amount += sale_item.line_total

        # Update sale total
        sale.total_amount = round(total_amount, 2)

        return sale

    @staticmethod
    def remove_expired_batches(
        session: Session,
        product_id: Optional[int] = None
    ) -> int:
        """
        Mark expired batches and log for disposal.
        
        Args:
            session: Database session
            product_id: Optional - specific product
            
        Returns:
            Number of batches marked expired
        """
        today = date.today()
        
        # Find expired batches with remaining stock
        query = (
            session.query(InventoryBatch)
            .filter(
                and_(
                    InventoryBatch.expiry_date <= today,
                    InventoryBatch.remaining_quantity > 0,
                    InventoryBatch.is_expired == False
                )
            )
        )
        
        if product_id:
            query = query.filter(InventoryBatch.product_id == product_id)

        expired_batches = query.all()
        count = 0

        for batch in expired_batches:
            # Mark as expired
            batch.is_expired = True
            batch.updated_at = datetime.now()

            # Log to expired stock log
            expired_log = ExpiredStockLog(
                batch_id=batch.id,
                product_id=batch.product_id,
                quantity_expired=batch.remaining_quantity,
                expiry_date=batch.expiry_date,
                days_expired=(today - batch.expiry_date).days,
                notes=f"Auto-marked expired on {today}"
            )
            session.add(expired_log)

            # Log movement
            movement = InventoryMovement(
                batch_id=batch.id,
                product_id=batch.product_id,
                movement_type=MovementType.EXPIRED,
                quantity_change=-batch.remaining_quantity,
                reference_type="expiry",
                notes=f"Batch expired on {batch.expiry_date}"
            )
            session.add(movement)

            count += 1

        return count

    @staticmethod
    def get_product_dashboard(session: Session, product_id: int) -> Dict[str, Any]:
        """
        Get comprehensive product inventory status.
        """
        product = session.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Product {product_id} not found")

        available_qty, available_batches = FIFOInventoryService.get_available_stock(session, product_id)
        expired_qty, expired_batches = FIFOInventoryService.get_expired_stock(session, product_id)

        # Count near-expiry items
        today = date.today()
        near_expiry_batches = [b for b in available_batches if 0 < b["days_until_expiry"] <= 7]

        return {
            "product_id": product.id,
            "sku": product.sku,
            "name": product.name,
            "unit_price": float(product.unit_price),
            "available_quantity": available_qty,
            "available_batches": available_batches,
            "expired_quantity": expired_qty,
            "expired_batches": expired_batches,
            "near_expiry_count": len(near_expiry_batches),
            "near_expiry_batches": near_expiry_batches,
            "is_out_of_stock": available_qty == 0,
            "status": "OUT_OF_STOCK" if available_qty == 0 else "AVAILABLE"
        }
