#!/usr/bin/env python3
"""
Staff Data Generator
Generates realistic staff data for all stores in the feedback system
"""

import os
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
import mysql.connector
from mysql.connector.connection import MySQLConnection

load_dotenv()

# Database configuration
def get_db_connection():
    """Get database connection using the same configuration as app.py"""
    
    if os.getenv("MYSQLHOST"):
        # Railway individual variables
        config = {
            "host": os.getenv("MYSQLHOST"),
            "user": os.getenv("MYSQLUSER"),
            "password": os.getenv("MYSQLPASSWORD"),
            "database": os.getenv("MYSQLDATABASE"),
            "port": int(os.getenv("MYSQLPORT", 3306)),
        }
    elif os.getenv("MYSQL_URL"):
        # Parse MYSQL_URL
        import urllib.parse
        mysql_url = os.getenv("MYSQL_URL").strip()
        parsed = urllib.parse.urlparse(mysql_url)
        config = {
            "host": parsed.hostname,
            "user": parsed.username,
            "password": parsed.password,
            "database": parsed.path.lstrip('/'),
            "port": parsed.port or 3306,
        }
    else:
        # Local environment
        config = {
            "host": os.getenv("DB_HOST", "localhost"),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "feedback_system"),
            "port": int(os.getenv("DB_PORT", "3306")),
        }
    
    return mysql.connector.connect(**config)

# Sample data for realistic staff generation
FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
    "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Nancy", "Daniel", "Lisa",
    "Matthew", "Betty", "Anthony", "Helen", "Mark", "Sandra", "Donald", "Donna",
    "Steven", "Carol", "Paul", "Ruth", "Andrew", "Sharon", "Joshua", "Michelle",
    "Kenneth", "Laura", "Kevin", "Sarah", "Brian", "Kimberly", "George", "Deborah",
    "Edward", "Dorothy", "Ronald", "Lisa", "Timothy", "Nancy", "Jason", "Karen",
    "Jeffrey", "Betty", "Ryan", "Helen", "Jacob", "Sandra", "Gary", "Donna"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Green", "Baker", "Adams",
    "Nelson", "Cox", "Ward", "Cook", "Bailey", "Cooper", "Reed", "Bell",
    "Murphy", "Bailey", "Rivera", "Cooper", "Richardson", "Cox", "Howard", "Ward"
]

POSITIONS = [
    "Store Manager", "Assistant Manager", "Sales Associate", "Cashier", "Customer Service Representative",
    "Shift Supervisor", "Team Lead", "Department Manager", "Sales Manager", "Operations Manager",
    "Floor Supervisor", "Senior Associate", "Lead Cashier", "Service Manager", "Retail Associate",
    "Sales Consultant", "Customer Service Lead", "Shift Lead", "Store Supervisor", "Assistant Supervisor"
]

def generate_random_date(start_year=2018, end_year=2024):
    """Generate a random hire date between start_year and end_year"""
    start_date = datetime(start_year, 1, 1)
    end_date = datetime(end_year, 12, 31)
    
    random_days = random.randint(0, (end_date - start_date).days)
    hire_date = start_date + timedelta(days=random_days)
    
    return hire_date.date()

def generate_phone_number():
    """Generate a realistic US phone number"""
    area_codes = ["212", "646", "917", "718", "347", "929", "516", "631", "914", "845", "203", "475"]
    area_code = random.choice(area_codes)
    exchange = random.randint(200, 999)
    number = random.randint(1000, 9999)
    
    return f"({area_code}) {exchange}-{number}"

def generate_email(first_name, last_name, store_id):
    """Generate a realistic email address"""
    domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "company.com", "storemail.com"]
    domain = random.choice(domains)
    
    # Various email formats
    formats = [
        f"{first_name.lower()}.{last_name.lower()}@{domain}",
        f"{first_name.lower()}{last_name.lower()}@{domain}",
        f"{first_name[0].lower()}{last_name.lower()}@{domain}",
        f"{first_name.lower()}.{last_name[0].lower()}@{domain}",
        f"{first_name.lower()}{last_name[0].lower()}{store_id}@{domain}"
    ]
    
    return random.choice(formats)

def generate_staff_data(store_id, num_staff):
    """Generate staff data for a specific store"""
    staff_data = []
    
    # Ensure at least one manager and one supervisor
    roles = ['manager', 'supervisor'] + ['staff'] * (num_staff - 2)
    random.shuffle(roles)
    
    for i in range(num_staff):
        first_name = random.choice(FIRST_NAMES)
        last_name = random.choice(LAST_NAMES)
        email = generate_email(first_name, last_name, store_id)
        phone = generate_phone_number()
        position = random.choice(POSITIONS)
        role = roles[i] if i < len(roles) else 'staff'
        hire_date = generate_random_date()
        status = random.choice(['active', 'active', 'active', 'inactive'])  # 75% active
        
        staff_data.append({
            'store_id': store_id,
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'phone': phone,
            'position': position,
            'role': role,
            'hire_date': hire_date,
            'status': status
        })
    
    return staff_data

def main():
    """Main function to generate staff data"""
    print("Starting staff data generation...")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get all stores
        cursor.execute("SELECT id, store_name FROM stores")
        stores = cursor.fetchall()
        
        if not stores:
            print("No stores found. Please create stores first.")
            return
        
        print(f"Found {len(stores)} stores")
        
        total_staff_generated = 0
        
        for store_id, store_name in stores:
            # Check existing staff count
            cursor.execute("SELECT COUNT(*) FROM staff WHERE store_id = %s", (store_id,))
            existing_count = cursor.fetchone()[0]
            
            # Generate between 10-15 staff per store
            target_staff = random.randint(10, 15)
            staff_to_generate = max(0, target_staff - existing_count)
            
            if staff_to_generate > 0:
                print(f"\nGenerating {staff_to_generate} staff for store '{store_name}' (ID: {store_id})")
                
                staff_data = generate_staff_data(store_id, staff_to_generate)
                
                # Insert staff data
                for staff in staff_data:
                    cursor.execute("""
                        INSERT INTO staff (store_id, first_name, last_name, email, phone, position, role, hire_date, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        staff['store_id'],
                        staff['first_name'],
                        staff['last_name'],
                        staff['email'],
                        staff['phone'],
                        staff['position'],
                        staff['role'],
                        staff['hire_date'],
                        staff['status']
                    ))
                
                total_staff_generated += staff_to_generate
                print(f"  Generated {staff_to_generate} staff members")
            else:
                print(f"\nStore '{store_name}' already has {existing_count} staff members")
        
        conn.commit()
        print(f"\nSuccessfully generated {total_staff_generated} staff members across all stores!")
        
        # Show summary
        cursor.execute("SELECT COUNT(*) FROM staff")
        total_staff = cursor.fetchone()[0]
        print(f"Total staff in database: {total_staff}")
        
        cursor.execute("""
            SELECT s.store_name, COUNT(st.id) as staff_count
            FROM stores s
            LEFT JOIN staff st ON s.id = st.store_id
            GROUP BY s.id, s.store_name
            ORDER BY staff_count DESC
        """)
        
        print("\nStaff count by store:")
        for store_name, count in cursor.fetchall():
            print(f"  {store_name}: {count} staff members")
        
    except Exception as e:
        print(f"Error generating staff data: {e}")
        conn.rollback()
    
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
