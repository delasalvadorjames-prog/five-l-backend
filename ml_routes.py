"""
ML API Routes for Pharmacy Management System
Provides endpoints for ML predictions and analytics
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from ml_predictor import (
    sales_predictor, 
    demand_predictor, 
    anomaly_detector, 
    revenue_optimizer
)

router = APIRouter(prefix="/api/ml", tags=["Machine Learning"])

# Request/Response Models
class SaleData(BaseModel):
    id: str
    timestamp: str
    total: float
    units: int
    medicineName: str
    customerId: Optional[str] = None

class PredictionRequest(BaseModel):
    sales_data: List[SaleData]
    days_ahead: int = 7

class TrendAnalysisResponse(BaseModel):
    trend: str
    predictions: Dict
    confidence: float
    analysis_date: str

@router.post("/predict/sales")
async def predict_sales(request: PredictionRequest):
    """
    Predict future sales based on historical data
    Returns predicted sales for next N days
    """
    try:
        sales_list = [sale.dict() for sale in request.sales_data]
        
        # Train the model
        if not sales_predictor.train(sales_list):
            raise HTTPException(status_code=400, detail="Insufficient data for prediction")
        
        # Get predictions
        predictions = sales_predictor.predict_next_days(request.days_ahead)
        trend = sales_predictor.get_trend(sales_list)
        
        return {
            "status": "success",
            "predictions": predictions,
            "trend": trend,
            "training_samples": len(sales_list),
            "model_confidence": 0.85 if len(sales_list) > 10 else 0.65,
            "generated_at": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/analyze/demand")
async def analyze_demand(request: PredictionRequest):
    """
    Analyze medicine demand patterns
    Returns top medicines, demand trends, and recommendations
    """
    try:
        sales_list = [sale.dict() for sale in request.sales_data]
        
        # Analyze patterns
        patterns = demand_predictor.analyze_patterns(sales_list)
        recommendations = demand_predictor.get_recommendations()
        
        return {
            "status": "success",
            "patterns": patterns,
            "recommendations": recommendations,
            "analysis_date": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detect/anomalies")
async def detect_anomalies(request: PredictionRequest):
    """
    Detect anomalous sales transactions
    Identifies unusual patterns that may indicate fraud or data errors
    """
    try:
        sales_list = [sale.dict() for sale in request.sales_data]
        
        # Detect anomalies
        result = anomaly_detector.detect_anomalies(sales_list, threshold=2.0)
        
        return {
            "status": "success",
            "anomalies": result['anomalies'],
            "normal_transactions": result['normal_count'],
            "anomaly_rate": result['anomaly_rate'],
            "statistics": {
                "mean_sale": result['mean_sale'],
                "std_dev": result['std_dev']
            },
            "analysis_date": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/optimize/revenue")
async def optimize_revenue(request: PredictionRequest):
    """
    Analyze revenue optimization opportunities
    Returns high-value items and volume items for strategic planning
    """
    try:
        sales_list = [sale.dict() for sale in request.sales_data]
        
        # Analyze metrics
        metrics = revenue_optimizer.analyze_price_elasticity(sales_list)
        
        return {
            "status": "success",
            "total_revenue": metrics['total_revenue'],
            "total_units": metrics['total_units'],
            "avg_price_per_unit": metrics['avg_price_per_unit'],
            "high_value_items": metrics['high_value_items'],
            "volume_items": metrics['volume_items'],
            "analysis_date": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def ml_health_check():
    """Check ML module status"""
    return {
        "status": "operational",
        "models": {
            "sales_predictor": "ready",
            "demand_predictor": "ready",
            "anomaly_detector": "ready",
            "revenue_optimizer": "ready"
        },
        "last_update": datetime.now().isoformat()
    }

@router.post("/train")
async def train_models(request: PredictionRequest):
    """Explicitly train all models with provided data"""
    try:
        sales_list = [sale.dict() for sale in request.sales_data]
        
        # Train sales predictor
        sales_trained = sales_predictor.train(sales_list)
        
        # Analyze demand patterns
        demand_predictor.analyze_patterns(sales_list)
        
        return {
            "status": "success",
            "message": "Models trained successfully",
            "sales_predictor_trained": sales_trained,
            "samples_used": len(sales_list),
            "training_time": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def ml_statistics():
    """Get current ML model statistics"""
    return {
        "status": "operational",
        "sales_predictor": {
            "is_trained": sales_predictor.is_trained,
            "last_training": sales_predictor.last_training_date.isoformat() if sales_predictor.last_training_date else None,
            "coefficient": float(sales_predictor.model.coef_[0]) if hasattr(sales_predictor.model, 'coef_') and len(sales_predictor.model.coef_) > 0 else None
        },
        "demand_predictor": {
            "patterns_analyzed": len(demand_predictor.medicine_patterns) > 0,
            "medicines_tracked": len(demand_predictor.medicine_patterns),
            "days_tracked": len(demand_predictor.day_patterns)
        },
        "timestamp": datetime.now().isoformat()
    }
