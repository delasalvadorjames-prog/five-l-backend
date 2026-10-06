import sys
from main import SessionLocal, User

db = SessionLocal()
try:
    staff_users = db.query(User).filter(User.role == 'staff').all()
    count = 0
    for user in staff_users:
        if "@" not in (user.email or ""):  # Ensure we only delete staff that use usernames
            print(f"Deleting {user.full_name} ({user.email})...")
            db.delete(user)
            count += 1
    db.commit()
    print(f"Deleted {count} staff accounts.")
except Exception as e:
    print(f"Error: {e}")
finally:
    db.close()
