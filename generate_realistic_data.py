import os
import random
from datetime import datetime, timedelta
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# Feedback templates
positive_feedback = [
    "Excellent service! Staff was very friendly and helpful.",
    "Great experience overall. Would definitely recommend to others.",
    "Outstanding quality and attention to detail. Very impressed!",
    "Fantastic atmosphere and amazing customer service.",
    "Exceeded my expectations. Will be coming back for sure!",
    "Professional staff and high-quality products. A+ experience!",
    "Wonderful experience from start to finish. Highly recommend!",
    "Top-notch service and great value for money.",
    "Absolutely love this place! Always a pleasure to visit.",
    "Exceptional service and quality. Couldn't be happier!"
]

neutral_feedback = [
    "Good service overall. Some room for improvement.",
    "Decent experience. Staff was helpful but could be more attentive.",
    "Average quality for the price. Nothing exceptional.",
    "Service was okay. Wait time was a bit long.",
    "Mixed experience. Some things were good, others not so much.",
    "Fair prices but service could be improved.",
    "Average experience. Nothing to complain about but nothing special.",
    "Decent quality but the atmosphere could be better.",
    "Service was acceptable but not outstanding.",
    "Reasonable prices but the quality varies."
]

negative_feedback = [
    "Poor service. Staff was rude and unhelpful.",
    "Very disappointed with the quality. Not worth the price.",
    "Terrible experience. Waited forever for service.",
    "Dirty establishment and poor customer service.",
    "Overpriced for the quality offered. Very disappointed.",
    "Staff was inattentive and the food was cold.",
    "Worst experience ever. Would not recommend.",
    "Poor quality and terrible customer service.",
    "Very unsatisfactory experience. Will not be returning.",
    "Extremely disappointed. Service was slow and rude."
]

# Customer names
customer_names = [
    "John Smith", "Emily Johnson", "Michael Brown", "Sarah Davis", "David Wilson",
    "Jessica Martinez", "Robert Anderson", "Lisa Thompson", "William Garcia", "Mary Rodriguez",
    "James Miller", "Patricia Jones", "Christopher Williams", "Jennifer Taylor", "Daniel Moore"
]

def get_db_connection():
    """Get database connection"""
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "root"),
        database=os.getenv("DB_NAME", "feedback_system"),
        port=int(os.getenv("DB_PORT", "3306"))
    )

def generate_feedback_for_store(store_id, store_name, num_feedback=15):
    """Generate random feedback for a store"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Each store gets a unique rating bias for more realistic comparison
        # Some stores are excellent, some are average, some are struggling
        store_bias = random.choice([
            {'weights': [0.8, 0.15, 0.05], 'type': 'excellent'}, # High rating
            {'weights': [0.6, 0.3, 0.1], 'type': 'good'},      # Good rating
            {'weights': [0.4, 0.4, 0.2], 'type': 'average'},   # Average rating
            {'weights': [0.2, 0.4, 0.4], 'type': 'poor'}       # Poor rating
        ])
        
        # Get questionnaire_id for this store
        cursor.execute("SELECT id FROM questionnaires WHERE store_id = %s LIMIT 1", (store_id,))
        q_row = cursor.fetchone()
        
        # If no store-specific questionnaire, create one linked to the template (ID 20)
        if not q_row:
            cursor.execute("""
                INSERT INTO questionnaires (store_id, title, is_active, created_at, is_template, template_id, version)
                VALUES (%s, 'Feedback Form', 1, NOW(), 0, 20, 1)
            """, (store_id,))
            questionnaire_id = cursor.lastrowid
            
            # Copy question 176 (the rating question) to the new questionnaire
            cursor.execute("""
                INSERT INTO questions (questionnaire_id, question_text, question_type, question_order, is_active)
                VALUES (%s, 'how was the food?', 'rating', 1, 1)
            """, (questionnaire_id,))
            question_id = cursor.lastrowid
        else:
            questionnaire_id = q_row[0]
            # Get the rating question for this questionnaire
            cursor.execute("SELECT id FROM questions WHERE questionnaire_id = %s AND question_type = 'rating' LIMIT 1", (questionnaire_id,))
            ques_row = cursor.fetchone()
            if not ques_row:
                # Add rating question if missing
                cursor.execute("""
                    INSERT INTO questions (questionnaire_id, question_text, question_type, question_order, is_active)
                    VALUES (%s, 'how was the food?', 'rating', 1, 1)
                """, (questionnaire_id,))
                question_id = cursor.lastrowid
            else:
                question_id = ques_row[0]

        for i in range(num_feedback):
            # Random feedback type based on store bias
            feedback_type = random.choices(
                ['positive', 'neutral', 'negative'],
                weights=store_bias['weights']
            )[0]
            
            # Select rating based on type
            if feedback_type == 'positive':
                rating = random.uniform(4.0, 5.0)
            elif feedback_type == 'neutral':
                rating = random.uniform(3.0, 4.0)
            else:
                rating = random.uniform(1.0, 3.0)
            
            # Random customer email
            customer_name = random.choice(customer_names)
            customer_email = f"{customer_name.lower().replace(' ', '.')}@example.com"
            
            # Random date within last 14 days for a "live" feel
            days_ago = random.randint(0, 14)
            submitted_at = datetime.now() - timedelta(days=days_ago)
            
            # Insert response
            cursor.execute("""
                INSERT INTO responses (questionnaire_id, store_id, user_email, submitted_at)
                VALUES (%s, %s, %s, %s)
            """, (questionnaire_id, store_id, customer_email, submitted_at))
            
            response_id = cursor.lastrowid
            
            # Insert answer for rating
            cursor.execute("""
                INSERT INTO answers (response_id, question_id, rating_value)
                VALUES (%s, %s, %s)
            """, (response_id, question_id, round(rating, 1)))
        
        conn.commit()
        print(f"Generated {num_feedback} feedback entries for {store_name} ({store_bias['type']} profile)")
        
    except Exception as e:
        print(f"Error generating feedback for {store_name}: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

def main():
    """Main function to generate feedback for all stores"""
    print("Generating realistic feedback for all stores...")
    print("=" * 60)
    
    # Get all stores
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if stores exist, if not create them
        cursor.execute("SELECT COUNT(*) FROM stores")
        if cursor.fetchone()[0] == 0:
            print("No stores found in production. Creating demo stores...")
            demo_stores = [
                "Store 1 Example", "Sushi Master", "Bella's Boutique", "Tech Hub", 
                "Green Grocer", "Urban Coffee", "Fitness First", "Book Nook",
                "Ocean Grill", "Mountain Gear", "Pet Palace", "Game Zone",
                "Flower Power", "Auto Ace", "Music Maker", "Toy Town",
                "Art Attic", "Craft Corner", "Daily Deli"
            ]
            for name in demo_stores:
                cursor.execute("INSERT INTO stores (store_name, status) VALUES (%s, 'active')", (name,))
            conn.commit()

        cursor.execute("SELECT id, store_name FROM stores ORDER BY id")
        stores = cursor.fetchall()
        
        for store_id, store_name in stores:
            # Skip Store 1 Example if it already has enough data, or just add more
            num_feedback = random.randint(15, 35)
            generate_feedback_for_store(store_id, store_name, num_feedback)
        
        print(f"\nSuccessfully generated realistic feedback for all {len(stores)} stores!")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
