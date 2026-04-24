"""
Licensing Portal - Centralized license management for multiple feedback system deployments
"""
import os
import json
import logging
import mysql.connector
from mysql.connector import MySQLConnection, Error
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, date
import secrets
import bcrypt
from functools import wraps
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app() -> Flask:
    app = Flask(__name__)
    
    # Configuration
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "licensing-secret-key-change-me")
    
    # Database configuration
    def get_db_connection() -> MySQLConnection:
        try:
            # Use MYSQL_URL or individual variables
            if os.getenv("MYSQL_URL"):
                import urllib.parse
                db_url = os.getenv("MYSQL_URL")
                parsed = urllib.parse.urlparse(db_url)
                return mysql.connector.connect(
                    host=parsed.hostname,
                    port=parsed.port,
                    user=parsed.username,
                    password=parsed.password,
                    database=parsed.path[1:]
                )
            else:
                return mysql.connector.connect(
                    host=os.getenv("DB_HOST", "localhost"),
                    port=int(os.getenv("DB_PORT", 3306)),
                    user=os.getenv("DB_USER", "root"),
                    password=os.getenv("DB_PASSWORD", ""),
                    database=os.getenv("DB_NAME", "licensing_db")
                )
        except mysql.connector.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting to database: {e}")
            raise

    @contextmanager
    def get_db_connection_with_transaction():
        """Context manager for database connections with automatic rollback on error."""
        conn = get_db_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    # Initialize database schema
    def init_schema():
        retries = 3
        while retries > 0:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Create licenses table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS licenses (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        license_key VARCHAR(255) NOT NULL UNIQUE,
                        license_key_hash VARCHAR(255) NOT NULL UNIQUE,
                        company_name VARCHAR(255) NOT NULL,
                        contact_email VARCHAR(255),
                        max_stores INT DEFAULT 0,
                        max_questionnaires INT DEFAULT 0,
                        features JSON NULL,
                        expiry_date DATE NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        api_key VARCHAR(255) NOT NULL UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                
                conn.commit()
                logger.info("Licensing database schema initialized successfully")
                return
            except Exception as e:
                retries -= 1
                logger.error(f"Schema initialization failed: {e}. Retries left: {retries}")
                if retries == 0:
                    raise
            finally:
                if 'conn' in locals():
                    conn.close()
    
    # Helper functions
    def fetch_users_from_main_app():
        """Fetch users from the main application API"""
        import requests
        main_app_url = os.getenv("MAIN_APP_URL", "http://localhost:8000")
        api_key = os.getenv("LICENSING_API_KEY", "change-me")
        
        try:
            response = requests.get(
                f"{main_app_url}/api/licensing/users",
                headers={"X-Licensing-API-Key": api_key},
                timeout=10
            )
            if response.status_code == 200:
                return response.json().get("users", [])
            else:
                logger.error(f"Failed to fetch users: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error fetching users from main app: {e}")
            return []

    def generate_license_key() -> str:
        return secrets.token_urlsafe(32)
    
    def generate_api_key() -> str:
        return secrets.token_urlsafe(32)
    
    def hash_key(key: str) -> str:
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()
    
    def get_all_licenses():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM licenses ORDER BY created_at DESC")
            return cursor.fetchall()
        finally:
            conn.close()
    
    def get_license_by_key(license_key: str):
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM licenses WHERE license_key = %s", (license_key,))
            return cursor.fetchone()
        finally:
            conn.close()
    
    def validate_license_key(license_key: str) -> dict:
        """Validate a license key and return its details"""
        license_data = get_license_by_key(license_key)
        if not license_data:
            return {"valid": False, "message": "License not found"}
        
        if not license_data["is_active"]:
            return {"valid": False, "message": "License is inactive"}
        
        if license_data["expiry_date"]:
            if isinstance(license_data["expiry_date"], date):
                expiry_date = license_data["expiry_date"]
            else:
                expiry_date = datetime.strptime(license_data["expiry_date"], "%Y-%m-%d").date()
            
            if datetime.now().date() > expiry_date:
                return {"valid": False, "message": "License has expired"}
        
        return {
            "valid": True,
            "company_name": license_data["company_name"],
            "max_stores": license_data["max_stores"],
            "max_questionnaires": license_data["max_questionnaires"],
            "features": json.loads(license_data["features"]) if license_data["features"] else {},
            "expiry_date": license_data["expiry_date"].isoformat() if license_data["expiry_date"] else None
        }
    
    def save_license(company_name, contact_email, max_stores, max_questionnaires, features, expiry_date):
        """Save a new license with validation."""
        # Input validation
        if not company_name or not company_name.strip():
            logger.error("Company name is required")
            return None
        if max_stores < 0 or max_questionnaires < 0:
            logger.error("max_stores and max_questionnaires must be non-negative")
            return None
        if contact_email and "@" not in contact_email:
            logger.error("Invalid email format")
            return None
        
        try:
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                
                license_key = generate_license_key()
                api_key = generate_api_key()
                license_key_hash = hash_key(license_key)
                
                cursor.execute(
                    """
                    INSERT INTO licenses (license_key, license_key_hash, company_name, contact_email, 
                                         max_stores, max_questionnaires, features, expiry_date, api_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (license_key, license_key_hash, company_name.strip(), contact_email, 
                     max_stores, max_questionnaires, json.dumps(features), expiry_date, api_key)
                )
                
                return {"license_key": license_key, "api_key": api_key}
        except mysql.connector.Error as e:
            logger.error(f"Database error saving license: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error saving license: {e}")
            return None
    
    def toggle_license(license_id):
        """Toggle license active status."""
        try:
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE licenses SET is_active = NOT is_active WHERE id = %s", (license_id,))
            return True
        except mysql.connector.Error as e:
            logger.error(f"Database error toggling license: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error toggling license: {e}")
            return False
    
    def delete_license(license_id):
        """Delete a license by ID."""
        try:
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM licenses WHERE id = %s", (license_id,))
            return True
        except mysql.connector.Error as e:
            logger.error(f"Database error deleting license: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting license: {e}")
            return False
    
    # Routes
    @app.route("/")
    def index():
        licenses = get_all_licenses()
        users = fetch_users_from_main_app()
        return render_template("licensing/index.html", licenses=licenses, users=users)
    
    @app.route("/license/add", methods=["POST"])
    def add_license():
        company_name = request.form.get("company_name", "").strip()
        contact_email = request.form.get("contact_email", "").strip() or None
        max_stores = int(request.form.get("max_stores", "0"))
        max_questionnaires = int(request.form.get("max_questionnaires", "0"))
        expiry_date_str = request.form.get("expiry_date", "").strip() or None
        user_id = request.form.get("user_id", "").strip() or None
        
        features = {
            "analytics": request.form.get("feature_analytics") == "on",
            "reports": request.form.get("feature_reports") == "on",
            "email_notifications": request.form.get("feature_email_notifications") == "on",
            "custom_branding": request.form.get("feature_custom_branding") == "on",
        }
        
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid expiry date", "danger")
                return redirect(url_for("index"))
        
        result = save_license(company_name, contact_email, max_stores, max_questionnaires, features, expiry_date)
        
        if result:
            flash(f"License created for {company_name}. Key: {result['license_key']}", "success")
        else:
            flash("Failed to create license", "danger")
        
        return redirect(url_for("index"))

    @app.route("/license/generate/<int:user_id>", methods=["POST"])
    def generate_license_for_user(user_id):
        """Generate a license for an existing user from the main app"""
        users = fetch_users_from_main_app()
        user = next((u for u in users if u["id"] == user_id), None)
        
        if not user:
            flash("User not found", "danger")
            return redirect(url_for("index"))
        
        company_name = user.get("username", user.get("email", "Unknown"))
        contact_email = user.get("email")
        max_stores = user.get("max_stores", 0)
        max_questionnaires = 0  # Default value
        
        features = {
            "analytics": True,
            "reports": True,
            "email_notifications": False,
            "custom_branding": False,
        }
        
        expiry_date = None
        
        result = save_license(company_name, contact_email, max_stores, max_questionnaires, features, expiry_date)
        
        if result:
            flash(f"License generated for {company_name}. Key: {result['license_key']}", "success")
        else:
            flash("Failed to generate license", "danger")
        
        return redirect(url_for("index"))
    
    @app.route("/license/<int:license_id>/toggle", methods=["POST"])
    def toggle_license_route(license_id):
        if toggle_license(license_id):
            flash("License status updated", "success")
        else:
            flash("Failed to update license", "danger")
        return redirect(url_for("index"))
    
    @app.route("/license/<int:license_id>/delete", methods=["POST"])
    def delete_license_route(license_id):
        if delete_license(license_id):
            flash("License deleted", "success")
        else:
            flash("Failed to delete license", "danger")
        return redirect(url_for("index"))
    
    @app.route("/api/validate/<license_key>")
    def api_validate(license_key):
        """API endpoint for validating licenses"""
        result = validate_license_key(license_key)
        return jsonify(result)
    
    # Initialize schema on startup
    init_schema()
    
    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 8081))
    app.run(host="0.0.0.0", port=port)
