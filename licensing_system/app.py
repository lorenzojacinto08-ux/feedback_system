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
    app = Flask(__name__, template_folder='templates', static_folder='static')
    
    # Configuration
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "licensing-secret-key-change-me")
    
    # Custom Jinja2 filter for parsing JSON
    @app.template_filter('from_json')
    def from_json_filter(s):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return {}
    
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
                
                # Create support_tickets table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS support_tickets (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        license_id INT NULL,
                        license_key VARCHAR(255) NULL,
                        company_name VARCHAR(255) NOT NULL,
                        contact_email VARCHAR(255) NOT NULL,
                        subject VARCHAR(255) NOT NULL,
                        message TEXT NOT NULL,
                        ticket_type ENUM('general', 'renewal', 'bug', 'feature') DEFAULT 'general',
                        status ENUM('open', 'in_progress', 'resolved', 'closed') DEFAULT 'open',
                        priority ENUM('low', 'medium', 'high') DEFAULT 'medium',
                        admin_reply TEXT NULL,
                        replied_at TIMESTAMP NULL,
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

        # All features enabled by default
        features = {
            "analytics": True,
            "reports": True,
            "email_notifications": True,
            "custom_branding": True,
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
    
    @app.route("/license/<int:license_id>/renew", methods=["POST"])
    def renew_license_route(license_id):
        """Renew a license with a custom expiry date"""
        try:
            new_expiry_str = request.form.get("new_expiry_date", "").strip()
            
            if not new_expiry_str:
                flash("Please provide an expiry date", "danger")
                return redirect(url_for("index"))
            
            # Parse the new expiry date
            try:
                new_expiry = datetime.strptime(new_expiry_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid expiry date format", "danger")
                return redirect(url_for("index"))
            
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                
                # Update the license with the new expiry date
                cursor.execute(
                    "UPDATE licenses SET expiry_date = %s, is_active = TRUE WHERE id = %s",
                    (new_expiry, license_id)
                )
            
            flash(f"License renewed successfully. New expiry: {new_expiry}", "success")
        except mysql.connector.Error as e:
            logger.error(f"Database error renewing license: {e}")
            flash("Failed to renew license", "danger")
        except Exception as e:
            logger.error(f"Unexpected error renewing license: {e}")
            flash("Failed to renew license", "danger")
        
        return redirect(url_for("index"))
    
    # ── Ticket helpers ──────────────────────────────────────────────
    def get_all_tickets():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM support_tickets ORDER BY FIELD(status,'open','in_progress','resolved','closed'), created_at DESC")
            return cursor.fetchall()
        finally:
            conn.close()

    def get_tickets_by_license(license_key):
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM support_tickets WHERE license_key = %s ORDER BY created_at DESC", (license_key,))
            return cursor.fetchall()
        finally:
            conn.close()

    def create_ticket(license_key, company_name, contact_email, subject, message, ticket_type='general'):
        try:
            license_data = get_license_by_key(license_key) if license_key else None
            license_id = license_data['id'] if license_data else None
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO support_tickets
                       (license_id, license_key, company_name, contact_email, subject, message, ticket_type)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (license_id, license_key, company_name, contact_email, subject, message, ticket_type)
                )
            return True
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            return False

    # ── API routes ────────────────────────────────────────────────
    @app.route("/api/validate/<license_key>", methods=["GET", "POST"])
    def api_validate(license_key):
        """API endpoint for validating licenses"""
        result = validate_license_key(license_key)
        return jsonify(result)

    @app.route("/api/tickets/create", methods=["POST"])
    def api_create_ticket():
        """API endpoint for creating tickets from the main app"""
        data = request.get_json() or {}
        license_key = data.get("license_key", "").strip()
        contact_email = data.get("contact_email", "").strip()
        subject = data.get("subject", "").strip()
        message = data.get("message", "").strip()
        ticket_type = data.get("ticket_type", "general")

        if not subject or not message or not contact_email:
            return jsonify({"error": "subject, message, and contact_email are required"}), 400

        license_data = get_license_by_key(license_key) if license_key else None
        company_name = license_data["company_name"] if license_data else "Unknown"

        if create_ticket(license_key, company_name, contact_email, subject, message, ticket_type):
            return jsonify({"success": True}), 201
        return jsonify({"error": "Failed to create ticket"}), 500

    @app.route("/api/tickets/<license_key>", methods=["GET"])
    def api_get_tickets(license_key):
        """API endpoint for fetching tickets by license key"""
        tickets = get_tickets_by_license(license_key)
        # Convert datetime objects for JSON serialization
        serialized = []
        for t in tickets:
            row = dict(t)
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
            serialized.append(row)
        return jsonify({"tickets": serialized})

    # ── Client support portal ────────────────────────────────────
    @app.route("/support")
    def support_portal():
        return render_template("licensing/support.html")

    @app.route("/support/lookup", methods=["GET", "POST"])
    def support_lookup():
        if request.method == "POST":
            license_key = request.form.get("license_key", "").strip()
        else:
            license_key = request.args.get("license_key", "").strip()
        if not license_key:
            flash("Please enter a license key.", "danger")
            return redirect(url_for("support_portal"))
        license_data = get_license_by_key(license_key)
        if not license_data:
            flash("License key not found.", "danger")
            return redirect(url_for("support_portal"))
        # Check expiry
        is_expired = False
        if license_data.get("expiry_date"):
            if isinstance(license_data["expiry_date"], date):
                is_expired = datetime.now().date() > license_data["expiry_date"]
            else:
                is_expired = datetime.now().date() > datetime.strptime(license_data["expiry_date"], "%Y-%m-%d").date()
        tickets = get_tickets_by_license(license_key)
        return render_template("licensing/support.html",
                               license=license_data, is_expired=is_expired,
                               tickets=tickets, license_key=license_key)

    @app.route("/support/ticket", methods=["POST"])
    def support_submit_ticket():
        license_key = request.form.get("license_key", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        ticket_type = request.form.get("ticket_type", "general")
        contact_email = request.form.get("contact_email", "").strip()

        if not subject or not message or not contact_email:
            flash("Subject, message, and email are required.", "danger")
            return redirect(url_for("support_portal"))

        license_data = get_license_by_key(license_key) if license_key else None
        company_name = license_data["company_name"] if license_data else "Unknown"

        if create_ticket(license_key, company_name, contact_email, subject, message, ticket_type):
            flash("Ticket submitted successfully. We'll get back to you soon.", "success")
        else:
            flash("Failed to submit ticket. Please try again.", "danger")

        if license_key:
            return redirect(url_for("support_lookup", license_key=license_key))
        return redirect(url_for("support_portal"))

    @app.route("/support/renew-request", methods=["POST"])
    def support_renew_request():
        license_key = request.form.get("license_key", "").strip()
        contact_email = request.form.get("contact_email", "").strip()

        if not license_key:
            flash("License key is required.", "danger")
            return redirect(url_for("support_portal"))

        license_data = get_license_by_key(license_key)
        if not license_data:
            flash("License key not found.", "danger")
            return redirect(url_for("support_portal"))

        company_name = license_data["company_name"]
        email = contact_email or license_data.get("contact_email", "")
        subject = f"License Renewal Request - {company_name}"
        message = f"Requesting renewal for license belonging to {company_name}. Current expiry: {license_data.get('expiry_date', 'N/A')}."

        if create_ticket(license_key, company_name, email, subject, message, 'renewal'):
            flash("Renewal request submitted. Our team will process it shortly.", "success")
        else:
            flash("Failed to submit renewal request.", "danger")

        return redirect(url_for("support_lookup", license_key=license_key))

    # ── Admin ticket management ──────────────────────────────────
    @app.route("/tickets")
    def admin_tickets():
        tickets = get_all_tickets()
        return render_template("licensing/tickets.html", tickets=tickets)

    @app.route("/ticket/<int:ticket_id>/reply", methods=["POST"])
    def admin_reply_ticket(ticket_id):
        reply = request.form.get("admin_reply", "").strip()
        new_status = request.form.get("status", "in_progress")
        if not reply:
            flash("Reply cannot be empty.", "danger")
            return redirect(url_for("admin_tickets"))
        try:
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE support_tickets SET admin_reply = %s, status = %s, replied_at = NOW() WHERE id = %s",
                    (reply, new_status, ticket_id)
                )
            flash("Reply sent successfully.", "success")
        except Exception as e:
            logger.error(f"Error replying to ticket: {e}")
            flash("Failed to send reply.", "danger")
        return redirect(url_for("admin_tickets"))

    @app.route("/ticket/<int:ticket_id>/status", methods=["POST"])
    def admin_update_ticket_status(ticket_id):
        new_status = request.form.get("status", "open")
        try:
            with get_db_connection_with_transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE support_tickets SET status = %s WHERE id = %s", (new_status, ticket_id))
            flash(f"Ticket status updated to {new_status}.", "success")
        except Exception as e:
            logger.error(f"Error updating ticket status: {e}")
            flash("Failed to update ticket.", "danger")
        return redirect(url_for("admin_tickets"))

    # Initialize schema on startup
    init_schema()
    
    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 8081))
    app.run(host="0.0.0.0", port=port)
