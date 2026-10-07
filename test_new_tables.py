#!/usr/bin/env python3
"""
Test script for expired_stock_log and inventory_movements tables
Run this to test the new FIFO tables
"""

import requests
import json
from datetime import datetime, timedelta
from typing import Dict, Any

# API Base URL
BASE_URL = "https://five-l-backend.onrender.com/api"

def print_header(title: str):
    """Print a formatted header"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)

def print_response(response_data: Dict[str, Any], title: str = "Response"):
    """Pretty print response"""
    print(f"\n{title}:")
    print(json.dumps(response_data, indent=2, default=str))

def get_products() -> list:
    """Get list of products from database"""
    try:
        response = requests.get(f"{BASE_URL}/inventory/list-products")
        if response.status_code == 200:
            data = response.json()
            return data.get("products", [])
    except:
        pass
    return []

def get_batches() -> list:
    """Get list of inventory batches"""
    try:
        response = requests.get(f"{BASE_URL}/inventory/list-batches")
        if response.status_code == 200:
            data = response.json()
            return data.get("batches", [])
    except:
        pass
    return []

def test_expired_stock_log():
    """Test expired_stock_log table"""
    print_header("TEST 1: EXPIRED STOCK LOG TABLE")
    
    # Get a product and batch to use
    products = get_products()
    batches = get_batches()
    
    if not products:
        print("❌ No products found. Please add products first.")
        return False
    
    if not batches:
        print("❌ No batches found. Please add inventory batches first.")
        return False
    
    product_id = products[0]["id"]
    batch_id = batches[0]["id"]
    batch_number = batches[0].get("batch_number", "BATCH-001")
    
    print(f"Using Product ID: {product_id}")
    print(f"Using Batch ID: {batch_id}")
    
    # Test 1.1: Log expired stock
    print("\n[1.1] Logging expired stock entry...")
    
    expired_date = datetime.now().date() - timedelta(days=10)
    
    payload = {
        "product_id": product_id,
        "batch_id": batch_id,
        "batch_number": batch_number,
        "quantity_expired": 50,
        "expiry_date": str(expired_date),
        "action_taken": "destroyed",
        "notes": "Test expired stock log entry"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/inventory/test/expired-stock-log",
            json=payload,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print_response(data, "Expired Stock Log Response")
        
        if response.status_code in [200, 201] and data.get("success"):
            print("✓ Expired stock log entry created successfully!")
            return True
        else:
            print("❌ Failed to create expired stock log entry")
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_inventory_movements():
    """Test inventory_movements table"""
    print_header("TEST 2: INVENTORY MOVEMENTS TABLE")
    
    # Get a product
    products = get_products()
    
    if not products:
        print("❌ No products found. Please add products first.")
        return False
    
    product_id = products[0]["id"]
    print(f"Using Product ID: {product_id}")
    
    # Test 2.1: Log stock in
    print("\n[2.1] Logging STOCK_IN movement...")
    
    payload = {
        "product_id": product_id,
        "batch_id": None,
        "movement_type": "STOCK_IN",
        "quantity": 100,
        "reference_id": None,
        "reference_type": "purchase_order",
        "notes": "Test stock in movement",
        "created_by": "test_user"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/inventory/test/inventory-movement",
            json=payload,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print_response(data, "Stock In Movement Response")
        
        if response.status_code not in [200, 201] or not data.get("success"):
            print("❌ Failed to log stock in movement")
            return False
            
        print("✓ Stock in movement logged successfully!")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False
    
    # Test 2.2: Log sale
    print("\n[2.2] Logging SALE movement...")
    
    batches = get_batches()
    batch_id = batches[0]["id"] if batches else None
    
    payload = {
        "product_id": product_id,
        "batch_id": batch_id,
        "movement_type": "SALE",
        "quantity": 5,
        "reference_id": 1,
        "reference_type": "sale",
        "notes": "Test sale movement",
        "created_by": "cashier_1"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/inventory/test/inventory-movement",
            json=payload,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print_response(data, "Sale Movement Response")
        
        if response.status_code in [200, 201] and data.get("success"):
            print("✓ Sale movement logged successfully!")
            return True
        else:
            print("❌ Failed to log sale movement")
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def test_retrieve_logs():
    """Test retrieving logs"""
    print_header("TEST 3: RETRIEVE LOGS")
    
    # Test 3.1: Get expired stock logs
    print("\n[3.1] Retrieving expired stock logs...")
    
    try:
        response = requests.get(
            f"{BASE_URL}/inventory/test/expired-stock-logs",
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        data = response.json()
        
        if response.status_code == 200:
            count = data.get("count", 0)
            print(f"✓ Retrieved {count} expired stock log entries")
            print_response(data, "Expired Stock Logs")
        else:
            print("❌ Failed to retrieve expired stock logs")
            print_response(data)
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Test 3.2: Get inventory movements
    print("\n[3.2] Retrieving inventory movements...")
    
    try:
        response = requests.get(
            f"{BASE_URL}/inventory/test/inventory-movements",
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        data = response.json()
        
        if response.status_code == 200:
            count = data.get("count", 0)
            print(f"✓ Retrieved {count} inventory movement entries")
            print_response(data, "Inventory Movements")
        else:
            print("❌ Failed to retrieve inventory movements")
            print_response(data)
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")

def main():
    """Run all tests"""
    print("\n" + "█"*70)
    print("█  TESTING NEW FIFO DATABASE TABLES")
    print("█  - expired_stock_log")
    print("█  - inventory_movements")
    print("█"*70)
    
    print("\n🔍 Checking API connection...")
    try:
        response = requests.get(f"{BASE_URL}/inventory/health", timeout=5)
        if response.status_code == 200:
            print("✓ API is running and accessible")
        else:
            print("❌ API returned error")
            return
    except Exception as e:
        print(f"❌ Cannot connect to API at {BASE_URL}")
        print(f"   Error: {str(e)}")
        print("\n💡 Make sure the backend is running:")
        print("   cd backend && python3 main.py")
        return
    
    # Run tests
    results = []
    
    results.append(("Expired Stock Log", test_expired_stock_log()))
    results.append(("Inventory Movements", test_inventory_movements()))
    test_retrieve_logs()
    
    # Summary
    print_header("TEST SUMMARY")
    for test_name, result in results:
        status = "✓ PASSED" if result else "❌ FAILED"
        print(f"{test_name:.<50} {status}")
    
    all_passed = all(r for _, r in results)
    if all_passed:
        print("\n✓ All tests passed!")
    else:
        print("\n❌ Some tests failed. Check the output above.")

if __name__ == "__main__":
    main()
