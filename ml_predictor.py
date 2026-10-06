"""
Machine Learning Module for Pharmacy Sales Prediction
Provides sales forecasting, demand prediction, and trend analysis
"""

import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
try:
    from sklearn.linear_model import LinearRegression
    from sklearn.preprocessing import StandardScaler
except Exception as exc:  # pragma: no cover - used only when sklearn is unavailable
    print(f"[WARNING] scikit-learn unavailable ({exc}); using NumPy ML fallback")

    class LinearRegression:  # type: ignore[no-redef]
        """Small linear-regression fallback for constrained environments."""
        def fit(self, X, y):
            values = np.asarray(X, dtype=float).reshape(-1)
            targets = np.asarray(y, dtype=float).reshape(-1)
            design = np.column_stack((values, np.ones(values.shape[0])))
            self.coef_, self.intercept_ = np.linalg.lstsq(design, targets, rcond=None)[0]
            return self

        def predict(self, X):
            values = np.asarray(X, dtype=float).reshape(-1)
            return values * self.coef_ + self.intercept_

    class StandardScaler:  # type: ignore[no-redef]
        def fit(self, X, y=None):
            return self
import warnings

warnings.filterwarnings('ignore')


class SalesPredictor:
    """Predicts future sales based on historical data"""
    
    def __init__(self):
        self.model = LinearRegression()
        self.scaler = StandardScaler()
        self.is_trained = False
        self.last_training_date = None
        
    def prepare_features(self, sales_data: List[Dict]) -> Tuple[np.ndarray, np.ndarray]:
        """Convert sales data to features for ML model"""
        if not sales_data:
            return np.array([]).reshape(0, 1), np.array([])
            
        # Extract date and total sales
        dates = []
        totals = []
        
        for sale in sales_data:
            try:
                if isinstance(sale.get('timestamp'), str):
                    date = datetime.fromisoformat(sale['timestamp'].replace('Z', '+00:00'))
                else:
                    date = sale['timestamp']
                dates.append(date)
                totals.append(float(sale.get('total', 0)))
            except:
                continue
        
        if not dates:
            return np.array([]).reshape(0, 1), np.array([])
        
        # Convert dates to days since first sale (X values)
        min_date = min(dates)
        X = np.array([(d - min_date).days for d in dates]).reshape(-1, 1)
        y = np.array(totals)
        
        return X, y
    
    def train(self, sales_data: List[Dict]) -> bool:
        """Train the sales prediction model"""
        X, y = self.prepare_features(sales_data)
        
        if len(X) < 2:
            return False
        
        try:
            self.model.fit(X, y)
            self.scaler.fit(X)
            self.is_trained = True
            self.last_training_date = datetime.now()
            return True
        except Exception as e:
            print(f"Training error: {e}")
            return False
    
    def predict_next_days(self, days: int = 7) -> Dict[str, float]:
        """Predict sales for next N days"""
        if not self.is_trained:
            return {}
        
        predictions = {}
        max_days = 365 if hasattr(self, 'model') and self.model.coef_ is not None else 30
        days = min(days, max_days)
        
        for i in range(1, days + 1):
            X_future = np.array([[i]]).reshape(-1, 1)
            pred = self.model.predict(X_future)[0]
            pred = max(0, pred)  # Ensure non-negative
            
            future_date = (datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d')
            predictions[future_date] = round(pred, 2)
        
        return predictions
    
    def get_trend(self, sales_data: List[Dict]) -> str:
        """Analyze sales trend: 'increasing', 'decreasing', or 'stable'"""
        X, y = self.prepare_features(sales_data)
        
        if len(y) < 3:
            return 'insufficient_data'
        
        # Split data into two halves
        mid = len(y) // 2
        first_half_avg = np.mean(y[:mid]) if mid > 0 else 0
        second_half_avg = np.mean(y[mid:])
        
        if first_half_avg == 0:
            return 'stable'
        
        change_percent = ((second_half_avg - first_half_avg) / first_half_avg) * 100
        
        if change_percent > 10:
            return 'increasing'
        elif change_percent < -10:
            return 'decreasing'
        else:
            return 'stable'


class DemandPredictor:
    """Predicts medicine demand based on patterns"""
    
    def __init__(self):
        self.medicine_patterns = {}
        self.day_patterns = {}
        
    def analyze_patterns(self, sales_data: List[Dict]) -> Dict:
        """Analyze purchase patterns by medicine and day of week"""
        medicine_sales = {}
        day_sales = {}
        
        for sale in sales_data:
            try:
                # Track by medicine
                medicine_name = sale.get('medicineName', 'Unknown')
                if medicine_name not in medicine_sales:
                    medicine_sales[medicine_name] = []
                medicine_sales[medicine_name].append(sale.get('units', 0))
                
                # Track by day of week
                if isinstance(sale.get('timestamp'), str):
                    date = datetime.fromisoformat(sale['timestamp'].replace('Z', '+00:00'))
                else:
                    date = sale.get('timestamp', datetime.now())
                
                day_name = date.strftime('%A')
                if day_name not in day_sales:
                    day_sales[day_name] = []
                day_sales[day_name].append(sale.get('units', 0))
            except:
                continue
        
        # Calculate averages
        medicine_avg = {med: np.mean(units) if units else 0 
                       for med, units in medicine_sales.items()}
        day_avg = {day: np.mean(units) if units else 0 
                  for day, units in day_sales.items()}
        
        self.medicine_patterns = medicine_avg
        self.day_patterns = day_avg
        
        return {
            'medicine_demand': medicine_avg,
            'day_demand': day_avg,
            'top_medicines': sorted(medicine_avg.items(), 
                                    key=lambda x: x[1], reverse=True)[:5]
        }
    
    def get_recommendations(self) -> List[Dict]:
        """Get inventory recommendations based on patterns"""
        recommendations = []
        
        for medicine, avg_demand in sorted(self.medicine_patterns.items(), 
                                           key=lambda x: x[1], reverse=True)[:10]:
            daily_avg = avg_demand
            weekly_avg = daily_avg * 7
            
            recommendations.append({
                'medicine': medicine,
                'daily_avg': round(daily_avg, 2),
                'weekly_avg': round(weekly_avg, 2),
                'suggested_reorder': round(weekly_avg * 2, 0),
                'risk_level': 'high' if daily_avg > 10 else 'medium' if daily_avg > 5 else 'low'
            })
        
        return recommendations


class AnomalyDetector:
    """Detects unusual sales patterns"""
    
    @staticmethod
    def detect_anomalies(sales_data: List[Dict], threshold: float = 2.0) -> Dict:
        """Detect anomalous transactions using statistical methods"""
        if not sales_data:
            return {'anomalies': [], 'normal_count': 0}
        
        # Extract sales amounts
        amounts = [sale.get('total', 0) for sale in sales_data]
        amounts = [a for a in amounts if a > 0]
        
        if len(amounts) < 3:
            return {'anomalies': [], 'normal_count': len(amounts)}
        
        # Calculate mean and std
        mean = np.mean(amounts)
        std = np.std(amounts)
        
        anomalies = []
        normal_count = 0
        
        for i, sale in enumerate(sales_data):
            amount = sale.get('total', 0)
            if std > 0:
                z_score = abs((amount - mean) / std)
                if z_score > threshold:
                    anomalies.append({
                        'transaction_id': sale.get('id', i),
                        'amount': amount,
                        'z_score': round(z_score, 2),
                        'severity': 'high' if z_score > 3 else 'medium',
                        'timestamp': sale.get('timestamp', 'unknown')
                    })
                else:
                    normal_count += 1
            else:
                normal_count += 1
        
        return {
            'anomalies': anomalies,
            'normal_count': normal_count,
            'anomaly_rate': round((len(anomalies) / len(sales_data)) * 100, 2) if sales_data else 0,
            'mean_sale': round(mean, 2),
            'std_dev': round(std, 2)
        }


class RevenueOptimizer:
    """Optimizes pricing and inventory for maximum revenue"""
    
    @staticmethod
    def analyze_price_elasticity(sales_data: List[Dict]) -> Dict:
        """Analyze how quantity changes with different factors"""
        metrics = {
            'total_revenue': 0,
            'total_units': 0,
            'avg_price_per_unit': 0,
            'high_value_items': [],
            'volume_items': []
        }
        
        medicine_metrics = {}
        
        for sale in sales_data:
            try:
                medicine = sale.get('medicineName', 'Unknown')
                units = sale.get('units', 0)
                total = sale.get('total', 0)
                
                if medicine not in medicine_metrics:
                    medicine_metrics[medicine] = {'units': 0, 'revenue': 0, 'transactions': 0}
                
                medicine_metrics[medicine]['units'] += units
                medicine_metrics[medicine]['revenue'] += total
                medicine_metrics[medicine]['transactions'] += 1
                
                metrics['total_revenue'] += total
                metrics['total_units'] += units
            except:
                continue
        
        if metrics['total_units'] > 0:
            metrics['avg_price_per_unit'] = round(
                metrics['total_revenue'] / metrics['total_units'], 2
            )
        
        # Categorize medicines
        for med, data in medicine_metrics.items():
            avg_price = data['revenue'] / data['units'] if data['units'] > 0 else 0
            
            if avg_price > metrics['avg_price_per_unit'] * 1.5:
                metrics['high_value_items'].append({
                    'medicine': med,
                    'avg_price': round(avg_price, 2),
                    'units_sold': data['units'],
                    'revenue': round(data['revenue'], 2)
                })
            elif data['units'] > 50:
                metrics['volume_items'].append({
                    'medicine': med,
                    'units_sold': data['units'],
                    'revenue': round(data['revenue'], 2),
                    'transactions': data['transactions']
                })
        
        metrics['high_value_items'] = sorted(
            metrics['high_value_items'], key=lambda x: x['revenue'], reverse=True
        )[:5]
        metrics['volume_items'] = sorted(
            metrics['volume_items'], key=lambda x: x['units_sold'], reverse=True
        )[:5]
        
        return metrics


# Initialize ML models
sales_predictor = SalesPredictor()
demand_predictor = DemandPredictor()
anomaly_detector = AnomalyDetector()
revenue_optimizer = RevenueOptimizer()
