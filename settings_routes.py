"""
Settings API Routes
Handles categories, suppliers, and system settings
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

router = APIRouter(prefix="/api/settings", tags=["settings"])

# ==========================================
# PYDANTIC MODELS
# ==========================================

class CategoryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "name": "Antibiotics",
                "description": "Antibiotic medications"
            }
        }


class CategoryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: bool
    product_count: int


class SupplierRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    payment_terms: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "name": "PharmaCorp Supplies",
                "contact_person": "John Doe",
                "email": "john@pharmacorp.com",
                "phone": "+1-555-1234",
                "address": "123 Medical Ave",
                "city": "New York",
                "country": "USA"
            }
        }


class SupplierResponse(BaseModel):
    id: int
    name: str
    contact_person: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    address: Optional[str]
    city: Optional[str]
    country: Optional[str]
    payment_terms: Optional[str]
    is_active: bool
    batch_count: int


class InventoryAlertConfigPayload(BaseModel):
    dosage_form: str = Field(..., min_length=1, max_length=100)
    low_stock_threshold: int = Field(..., ge=0)
    expiry_alert_days: int = Field(..., ge=0)
    is_active: bool = True

    @field_validator('dosage_form')
    @classmethod
    def validate_dosage_form(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('dosage_form cannot be empty')
        return cleaned


class InventoryAlertConfigResponse(InventoryAlertConfigPayload):
    id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ==========================================
# DATABASE SESSION
# ==========================================

def get_db():
    """Get database session"""
    from main import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# AUTHENTICATION DEPENDENCY
# ==========================================

async def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Get current authenticated user"""
    from main import get_current_user as main_get_current_user
    return await main_get_current_user(authorization, db)


async def require_admin_user(current_user = Depends(get_current_user)):
    """Require admin role"""
    from main import require_admin_user as main_require_admin_user
    return main_require_admin_user(current_user)


@router.get('/inventory-alert-configs', response_model=List[InventoryAlertConfigResponse])
async def get_inventory_alert_configs(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user),
):
    """Return dosage-form alert rules stored in the existing MySQL database."""
    from main import InventoryAlertConfig
    rows = db.query(InventoryAlertConfig).order_by(InventoryAlertConfig.id).all()
    return [
        {
            'id': row.id,
            'dosage_form': row.dosage_form,
            'low_stock_threshold': row.low_stock_threshold,
            'expiry_alert_days': row.expiry_alert_days,
            'is_active': bool(row.is_active),
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


@router.put('/inventory-alert-configs', response_model=List[InventoryAlertConfigResponse])
async def save_inventory_alert_configs(
    payload: List[InventoryAlertConfigPayload],
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user),
):
    """Upsert dosage-form rules while preventing duplicate dosage forms."""
    from main import InventoryAlertConfig

    if not payload:
        raise HTTPException(status_code=400, detail='At least one dosage-form configuration is required.')

    seen = set()
    for item in payload:
        key = item.dosage_form.casefold()
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate dosage form: {item.dosage_form}")
        seen.add(key)

    try:
        for item in payload:
            row = db.query(InventoryAlertConfig).filter(
                InventoryAlertConfig.dosage_form.ilike(item.dosage_form)
            ).first()
            if row:
                row.low_stock_threshold = item.low_stock_threshold
                row.expiry_alert_days = item.expiry_alert_days
                row.is_active = item.is_active
            else:
                db.add(InventoryAlertConfig(
                    dosage_form=item.dosage_form,
                    low_stock_threshold=item.low_stock_threshold,
                    expiry_alert_days=item.expiry_alert_days,
                    is_active=item.is_active,
                ))
        db.commit()
        rows = db.query(InventoryAlertConfig).order_by(InventoryAlertConfig.id).all()
        return [
            {
                'id': row.id,
                'dosage_form': row.dosage_form,
                'low_stock_threshold': row.low_stock_threshold,
                'expiry_alert_days': row.expiry_alert_days,
                'is_active': bool(row.is_active),
                'created_at': row.created_at.isoformat() if row.created_at else None,
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f'Unable to save dosage-form alert settings: {exc}')


def _category_schema(db: Session):
    """Return the column names used by the installed categories/products tables."""
    category_columns = {row[0] for row in db.execute(text("SHOW COLUMNS FROM categories")).fetchall()}
    id_column = "category_id" if "category_id" in category_columns else "id"
    name_column = "category_name" if "category_name" in category_columns else "name"

    product_columns = {row[0] for row in db.execute(text("SHOW COLUMNS FROM products")).fetchall()}
    if "category" in product_columns:
        product_join = f"p.category = c.{name_column}"
    elif "category_id" in product_columns:
        product_join = f"p.category_id = c.{id_column}"
    else:
        product_join = "1 = 0"

    return id_column, name_column, product_join


def _sync_inventory_categories(db: Session, name_column: str):
    """Persist categories already used by inventory into Settings categories."""
    existing_names = {
        str(row[0]).strip().casefold()
        for row in db.execute(text(f"SELECT {name_column} FROM categories")).fetchall()
        if row[0]
    }
    inventory_rows = db.execute(text("""
        SELECT DISTINCT TRIM(category)
        FROM medicines
        WHERE category IS NOT NULL AND TRIM(category) <> ''
    """)).fetchall()

    added = False
    for row in inventory_rows:
        category_name = str(row[0]).strip()
        if not category_name or category_name.casefold() in existing_names:
            continue
        db.execute(text(f"""
            INSERT INTO categories ({name_column}, description)
            VALUES (:name, :description)
        """), {
            "name": category_name,
            "description": "Imported from existing inventory",
        })
        existing_names.add(category_name.casefold())
        added = True

    if added:
        db.commit()


# ==========================================
# CATEGORIES ENDPOINTS
# ==========================================

@router.get("/categories", response_model=List[CategoryResponse])
async def get_categories(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Get all categories"""
    try:
        id_column, name_column, product_join = _category_schema(db)
        _sync_inventory_categories(db, name_column)
        
        query = f"""
            SELECT c.{id_column} as id, c.{name_column} as name, c.description,
                   COALESCE(COUNT(p.id), 0) as product_count
            FROM categories c
            LEFT JOIN products p ON {product_join}
            GROUP BY c.{id_column}, c.{name_column}, c.description
            ORDER BY c.{name_column}
        """
        
        categories_data = db.execute(text(query)).fetchall()
        
        result = []
        for row in categories_data:
            result.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "is_active": True,
                "product_count": row[3] if len(row) > 3 else 0
            })
        
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching categories: {str(e)}"
        )


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    request: CategoryRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Create a new category"""
    try:
        id_column, name_column, _ = _category_schema(db)
        
        # Check for duplicate
        duplicate_query = f"""
            SELECT {id_column} FROM categories WHERE {name_column} = :name
        """
        existing = db.execute(text(duplicate_query), {"name": request.name.strip()}).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category '{request.name}' already exists"
            )
        
        insert_query = f"""
            INSERT INTO categories ({name_column}, description)
            VALUES (:name, :description)
        """
        
        db.execute(text(insert_query), {
            "name": request.name.strip(),
            "description": request.description.strip() if request.description else None
        })

        db.commit()
        
        select_query = f"""
            SELECT {id_column} FROM categories
            WHERE {name_column} = :name
            ORDER BY {id_column} DESC
            LIMIT 1
        """
        
        category_id = db.execute(
            text(select_query), 
            {"name": request.name.strip()}
        ).scalar()
        
        return {
            "id": category_id,
            "name": request.name.strip(),
            "description": request.description,
            "is_active": True,
            "product_count": 0
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating category: {str(e)}"
        )


@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    request: CategoryRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Update a category"""
    try:
        id_column, name_column, _ = _category_schema(db)
        
        check_query = f"""
            SELECT {id_column} FROM categories WHERE {id_column} = :id
        """
        existing = db.execute(text(check_query), {"id": category_id}).first()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {category_id} not found"
            )
        
        # Check for duplicate (excluding current)
        duplicate_query = f"""
            SELECT {id_column} FROM categories 
            WHERE {name_column} = :name AND {id_column} != :id
        """
        duplicate = db.execute(text(duplicate_query), {
            "name": request.name.strip(),
            "id": category_id
        }).first()
        
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category '{request.name}' already exists"
            )
        
        update_query = f"""
            UPDATE categories 
            SET {name_column} = :name, description = :description
            WHERE {id_column} = :id
        """
        
        db.execute(text(update_query), {
            "name": request.name.strip(),
            "description": request.description.strip() if request.description else None,
            "id": category_id
        })
        
        db.commit()
        
        return {
            "id": category_id,
            "name": request.name.strip(),
            "description": request.description,
            "is_active": True,
            "product_count": 0
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating category: {str(e)}"
        )


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Delete a category"""
    try:
        id_column, _, _ = _category_schema(db)
        
        check_query = f"""
            SELECT {id_column} FROM categories WHERE {id_column} = :id
        """
        existing = db.execute(text(check_query), {"id": category_id}).first()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {category_id} not found"
            )
        
        delete_query = f"""
            DELETE FROM categories WHERE {id_column} = :id
        """
        db.execute(text(delete_query), {"id": category_id})
        
        db.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting category: {str(e)}"
        )


# ==========================================
# SUPPLIERS ENDPOINTS
# ==========================================

@router.get("/suppliers", response_model=List[SupplierResponse])
async def get_suppliers(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Get all suppliers"""
    try:
        suppliers_data = db.execute(text("""
            SELECT s.supplier_id as id, s.supplier_name as name, 
                   s.contact_person, s.email, s.phone, s.address,
                   NULL as city, NULL as country, NULL as payment_terms,
                   COALESCE(COUNT(ib.id), 0) as batch_count
            FROM suppliers s
            LEFT JOIN inventory_batches ib ON ib.supplier = s.supplier_name
            GROUP BY s.supplier_id, s.supplier_name, s.contact_person, s.email, s.phone, s.address
            ORDER BY s.supplier_name
        """)).fetchall()
        
        result = []
        for row in suppliers_data:
            result.append({
                "id": row[0],
                "name": row[1],
                "contact_person": row[2],
                "email": row[3],
                "phone": row[4],
                "address": row[5],
                "city": row[6],
                "country": row[7],
                "payment_terms": row[8],
                "is_active": True,
                "batch_count": row[9] if len(row) > 9 else 0
            })
        
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching suppliers: {str(e)}"
        )


@router.post("/suppliers", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    request: SupplierRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Create a new supplier"""
    try:
        existing = db.execute(text("""
            SELECT supplier_id FROM suppliers WHERE supplier_name = :name
        """), {"name": request.name.strip()}).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Supplier '{request.name}' already exists"
            )
        
        result = db.execute(text("""
            INSERT INTO suppliers (supplier_name, contact_person, email, phone, address)
            VALUES (:name, :contact_person, :email, :phone, :address)
        """), {
            "name": request.name.strip(),
            "contact_person": request.contact_person.strip() if request.contact_person else None,
            "email": request.email.strip() if request.email else None,
            "phone": request.phone.strip() if request.phone else None,
            "address": request.address.strip() if request.address else None
        })
        
        db.commit()
        supplier_id = result.lastrowid
        
        return {
            "id": supplier_id,
            "name": request.name.strip(),
            "contact_person": request.contact_person,
            "email": request.email,
            "phone": request.phone,
            "address": request.address,
            "city": request.city,
            "country": request.country,
            "payment_terms": request.payment_terms,
            "is_active": True,
            "batch_count": 0
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating supplier: {str(e)}"
        )


@router.put("/suppliers/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    request: SupplierRequest,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Update a supplier"""
    try:
        existing = db.execute(text("""
            SELECT supplier_id FROM suppliers WHERE supplier_id = :id
        """), {"id": supplier_id}).first()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found"
            )
        
        duplicate = db.execute(text("""
            SELECT supplier_id FROM suppliers 
            WHERE supplier_name = :name AND supplier_id != :id
        """), {
            "name": request.name.strip(),
            "id": supplier_id
        }).first()
        
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Supplier '{request.name}' already exists"
            )
        
        db.execute(text("""
            UPDATE suppliers 
            SET supplier_name = :name, contact_person = :contact_person, 
                email = :email, phone = :phone, address = :address
            WHERE supplier_id = :id
        """), {
            "name": request.name.strip(),
            "contact_person": request.contact_person.strip() if request.contact_person else None,
            "email": request.email.strip() if request.email else None,
            "phone": request.phone.strip() if request.phone else None,
            "address": request.address.strip() if request.address else None,
            "id": supplier_id
        })
        
        db.commit()
        
        return {
            "id": supplier_id,
            "name": request.name.strip(),
            "contact_person": request.contact_person,
            "email": request.email,
            "phone": request.phone,
            "address": request.address,
            "city": request.city,
            "country": request.country,
            "payment_terms": request.payment_terms,
            "is_active": True,
            "batch_count": 0
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating supplier: {str(e)}"
        )


@router.delete("/suppliers/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin_user)
):
    """Delete a supplier"""
    try:
        existing = db.execute(text("""
            SELECT supplier_id FROM suppliers WHERE supplier_id = :id
        """), {"id": supplier_id}).first()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found"
            )
        
        in_use = db.execute(text("""
            SELECT COUNT(*) FROM inventory_batches WHERE supplier = (
                SELECT supplier_name FROM suppliers WHERE supplier_id = :id
            )
        """), {"id": supplier_id}).scalar()
        
        if in_use > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete supplier as it is being used by {in_use} inventory batch(es)"
            )
        
        db.execute(text("""
            DELETE FROM suppliers WHERE supplier_id = :id
        """), {"id": supplier_id})
        
        db.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting supplier: {str(e)}"
        )
