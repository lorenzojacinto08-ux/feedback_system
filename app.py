import os
import csv
import base64
import io
import urllib.parse
import logging
import sys
import time
import traceback
import socket
import re
import random
from typing import List, Dict, Any
from fpdf import FPDF

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_file
from dotenv import load_dotenv
import mysql.connector
from mysql.connector.connection import MySQLConnection
import qrcode
from email_config import EmailConfig
from collections import defaultdict
from datetime import datetime, timedelta, timezone


load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)

    # --- LOGGING SETUP ---
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logger = logging.getLogger(__name__)

    # Debug: Log all environment variable keys (NOT values) to see what's available
    logger.info(f"AVAILABLE ENV VARS: {list(os.environ.keys())}")

    # --- ENVIRONMENT CONFIG ---
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")

    # Database configuration handling
    # PRIORITY: 1. Railway individual variables (Most reliable)
    #           2. MYSQL_URL connection string
    #           3. Local environment / Defaults
    
    if os.getenv("MYSQLHOST"):
        logger.info("Railway individual variables detected, using them for DB config.")
        app.config["DB_CONFIG"] = {
            "host": os.getenv("MYSQLHOST"),
            "user": os.getenv("MYSQLUSER"),
            "password": os.getenv("MYSQLPASSWORD"),
            "database": os.getenv("MYSQLDATABASE"),
            "port": int(os.getenv("MYSQLPORT", 3306)),
        }
    elif os.getenv("MYSQL_URL"):
        mysql_url = os.getenv("MYSQL_URL")
        logger.info("MYSQL_URL detected, parsing connection string...")
        try:
            # Clean up the URL
            mysql_url = mysql_url.strip()
            parsed = urllib.parse.urlparse(mysql_url)
            app.config["DB_CONFIG"] = {
                "host": parsed.hostname,
                "user": parsed.username,
                "password": parsed.password,
                "database": parsed.path.lstrip('/'),
                "port": parsed.port or 3306,
            }
        except Exception as e:
            logger.error(f"CRITICAL: Failed to parse MYSQL_URL: {e}")
            app.config["DB_CONFIG"] = {"host": "localhost", "port": 3306}
    else:
        logger.info("No production variables found, falling back to local .env or defaults.")
        app.config["DB_CONFIG"] = {
            "host": os.getenv("DB_HOST", "localhost"),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "feedback_system"),
            "port": int(os.getenv("DB_PORT", "3306")),
        }

    db_host = app.config["DB_CONFIG"].get("host")
    db_port = app.config["DB_CONFIG"].get("port")
    db_name = app.config["DB_CONFIG"].get("database")
    logger.info(f"DB CONFIG FINALIZED: host={db_host}, port={db_port}, database={db_name}")

    # FORCE FAIL if host is still localhost on Railway
    if os.getenv("RAILWAY_ENVIRONMENT") and db_host == "localhost":
        logger.critical("FATAL: App is running on Railway but host is still 'localhost'. Check variables!")

    def get_db_connection() -> MySQLConnection:
        try:
            return mysql.connector.connect(**app.config["DB_CONFIG"])
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    def log_audit(entity_type: str, entity_id: int, action: str, old_values: str = None, new_values: str = None, user_id: str = None) -> None:
        """Log an audit entry for tracking changes"""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_logs (entity_type, entity_id, action, old_values, new_values, user_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (entity_type, entity_id, action, old_values, new_values, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    # Initialize SMTP email configuration
    email_config = EmailConfig()
    email_config.init_app(app)

    def init_master_schema() -> None:
        retries = 3
        while retries > 0:
            try:
                logger.info(f"Attempting schema initialization... ({retries} retries left)")
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # --- CREATE TABLES IF NOT EXIST ---
                
                # 1. Stores Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS stores (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        store_name VARCHAR(255) NOT NULL,
                        address TEXT,
                        city VARCHAR(100),
                        province VARCHAR(100),
                        postal_code VARCHAR(20),
                        contact_number VARCHAR(20),
                        email VARCHAR(255),
                        store_manager_name VARCHAR(255),
                        manager_contact VARCHAR(20),
                        store_type VARCHAR(100),
                        operating_hours VARCHAR(255),
                        status ENUM('active', 'inactive', 'pending') DEFAULT 'active',
                        logo_url VARCHAR(500),
                        access_token VARCHAR(100) UNIQUE,
                        subdomain VARCHAR(100) UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 2. Staff Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS staff (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        store_id INT NOT NULL,
                        first_name VARCHAR(100) NOT NULL,
                        last_name VARCHAR(100) NOT NULL,
                        email VARCHAR(255),
                        phone VARCHAR(20),
                        position VARCHAR(100),
                        role ENUM('staff', 'manager', 'supervisor') DEFAULT 'staff',
                        hire_date DATE,
                        status ENUM('active', 'inactive') DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
                    )
                """)

                # 3. Staff Commendations Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS staff_commendations (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        response_id INT NOT NULL,
                        staff_id INT NOT NULL,
                        rating INT DEFAULT 5,
                        commendation_type ENUM('excellent_service', 'friendly_attitude', 'professional', 'helpful', 'knowledgeable') DEFAULT 'excellent_service',
                        comment TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (response_id) REFERENCES responses(id) ON DELETE CASCADE,
                        FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
                    )
                """)

                # 4. Questionnaires Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS questionnaires (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        store_id INT NULL,
                        title VARCHAR(255) NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        is_template BOOLEAN DEFAULT FALSE,
                        template_id INT NULL,
                        version INT DEFAULT 1,
                        logo_url VARCHAR(500),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 3. Questions Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS questions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        questionnaire_id INT NOT NULL,
                        question_text TEXT NOT NULL,
                        question_type ENUM('rating', 'text', 'multiple_choice') NOT NULL,
                        min_label VARCHAR(255) DEFAULT 'Poor',
                        max_label VARCHAR(255) DEFAULT 'Excellent',
                        allow_comment BOOLEAN DEFAULT FALSE,
                        is_required BOOLEAN DEFAULT TRUE,
                        question_order INT DEFAULT 0,
                        is_active BOOLEAN DEFAULT TRUE,
                        is_template BOOLEAN DEFAULT FALSE,
                        template_id INT NULL
                    )
                """)

                # 4. Question Options Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS question_options (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        question_id INT NOT NULL,
                        option_text VARCHAR(255) NOT NULL
                    )
                """)

                # 5. Responses Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS responses (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        questionnaire_id INT NOT NULL,
                        store_id INT NOT NULL,
                        user_email VARCHAR(255),
                        receipt_number VARCHAR(100),
                        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status ENUM('unresolved', 'resolved') DEFAULT 'unresolved'
                    )
                """)

                # 6. Answers Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS answers (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        response_id INT NOT NULL,
                        question_id INT NOT NULL,
                        answer_text TEXT,
                        rating_value DECIMAL(3,1)
                    )
                """)

                conn.commit()

                # --- UPDATE EXISTING TABLES (MIGRATIONS) ---
                
                # Ensure question_options table exists (fixing crash in master_questionnaire)
                cursor.execute("SHOW TABLES LIKE 'question_options'")
                if not cursor.fetchone():
                    logger.info("Table 'question_options' missing. Creating it now...")
                    cursor.execute("""
                        CREATE TABLE question_options (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            question_id INT NOT NULL,
                            option_text VARCHAR(255) NOT NULL
                        )
                    """)
                    conn.commit()
                
                # Ensure responses table exists
                cursor.execute("SHOW TABLES LIKE 'responses'")
                if not cursor.fetchone():
                    logger.info("Table 'responses' missing. Creating it now...")
                    cursor.execute("""
                        CREATE TABLE responses (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            questionnaire_id INT NOT NULL,
                            store_id INT NOT NULL,
                            user_email VARCHAR(255),
                            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            status ENUM('unresolved', 'resolved') DEFAULT 'unresolved'
                        )
                    """)
                    conn.commit()

                # Ensure answers table exists
                cursor.execute("SHOW TABLES LIKE 'answers'")
                if not cursor.fetchone():
                    logger.info("Table 'answers' missing. Creating it now...")
                    cursor.execute("""
                        CREATE TABLE answers (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            response_id INT NOT NULL,
                            question_id INT NOT NULL,
                            answer_text TEXT,
                            rating_value DECIMAL(3,1)
                        )
                    """)
                    conn.commit()
                
                # Check for responses table columns
                cursor.execute("SHOW COLUMNS FROM responses LIKE 'user_email'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE responses ADD COLUMN user_email VARCHAR(255) AFTER submitted_at")
                
                cursor.execute("SHOW COLUMNS FROM responses LIKE 'status'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE responses ADD COLUMN status ENUM('unresolved', 'resolved') DEFAULT 'unresolved' AFTER user_email")
                
                cursor.execute("SHOW COLUMNS FROM responses LIKE 'is_read'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE responses ADD COLUMN is_read BOOLEAN DEFAULT FALSE AFTER status")
                
                # Ensure system_notifications table exists
                cursor.execute("SHOW TABLES LIKE 'system_notifications'")
                if not cursor.fetchone():
                    logger.info("Table 'system_notifications' missing. Creating it now...")
                    cursor.execute("""
                        CREATE TABLE system_notifications (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            message TEXT NOT NULL,
                            type VARCHAR(50) DEFAULT 'info',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            is_read BOOLEAN DEFAULT FALSE
                        )
                    """)
                    conn.commit()
                
                # Create audit log table
                cursor.execute("SHOW TABLES LIKE 'audit_logs'")
                if not cursor.fetchone():
                    logger.info("Creating audit_logs table...")
                    cursor.execute("""
                        CREATE TABLE audit_logs (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            entity_type VARCHAR(50) NOT NULL,
                            entity_id INT NOT NULL,
                            action VARCHAR(50) NOT NULL,
                            old_values TEXT,
                            new_values TEXT,
                            user_id VARCHAR(255),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.commit()
                
                # Check for questionnaires table columns
                cursor.execute("SHOW COLUMNS FROM questionnaires LIKE 'is_template'")
                if not cursor.fetchone():
                    logger.info("Adding 'is_template' column to questionnaires table...")
                    cursor.execute("ALTER TABLE questionnaires ADD COLUMN is_template BOOLEAN DEFAULT FALSE AFTER is_active")
                    conn.commit()
                
                cursor.execute("SHOW COLUMNS FROM questionnaires LIKE 'template_id'")
                if not cursor.fetchone():
                    logger.info("Adding 'template_id' column to questionnaires table...")
                    cursor.execute("ALTER TABLE questionnaires ADD COLUMN template_id INT NULL AFTER is_template")
                    conn.commit()
                
                cursor.execute("SHOW COLUMNS FROM questionnaires LIKE 'version'")
                if not cursor.fetchone():
                    logger.info("Adding 'version' column to questionnaires table...")
                    cursor.execute("ALTER TABLE questionnaires ADD COLUMN version INT DEFAULT 1 AFTER template_id")
                    conn.commit()

                cursor.execute("SHOW COLUMNS FROM questionnaires LIKE 'logo_url'")
                if not cursor.fetchone():
                    logger.info("Adding 'logo_url' column to questionnaires table...")
                    cursor.execute("ALTER TABLE questionnaires ADD COLUMN logo_url LONGTEXT AFTER version")
                    conn.commit()
                else:
                    # Always try to update to LONGTEXT to ensure it can handle base64 data
                    try:
                        logger.info("Ensuring 'logo_url' column is LONGTEXT for base64 storage...")
                        cursor.execute("ALTER TABLE questionnaires MODIFY COLUMN logo_url LONGTEXT")
                        conn.commit()
                        logger.info("'logo_url' column updated to LONGTEXT")
                    except Exception as e:
                        logger.info(f"Column may already be LONGTEXT: {e}")

                # Ensure Master Template exists
                cursor.execute("SELECT id FROM questionnaires WHERE is_template = 1 LIMIT 1")
                if not cursor.fetchone():
                    logger.info("No master template found. Creating default master template...")
                    cursor.execute("INSERT INTO questionnaires (title, is_active, is_template) VALUES ('Master Questionnaire', 1, 1)")
                    conn.commit()

                # Check for questions table columns
                cursor.execute("SHOW COLUMNS FROM questions LIKE 'is_active'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE questions ADD COLUMN is_active BOOLEAN DEFAULT TRUE AFTER question_order")
                
                cursor.execute("SHOW COLUMNS FROM questions LIKE 'is_template'")
                if not cursor.fetchone():
                    logger.info("Adding 'is_template' column to questions table...")
                    cursor.execute("ALTER TABLE questions ADD COLUMN is_template BOOLEAN DEFAULT FALSE AFTER is_active")
                    conn.commit()

                cursor.execute("SHOW COLUMNS FROM questions LIKE 'template_id'")
                if not cursor.fetchone():
                    logger.info("Adding 'template_id' column to questions table...")
                    cursor.execute("ALTER TABLE questions ADD COLUMN template_id INT NULL AFTER is_template")
                    conn.commit()
                
                cursor.execute("SHOW COLUMNS FROM questions LIKE 'min_label'")
                if not cursor.fetchone():
                    logger.info("Adding 'min_label' column to questions table...")
                    cursor.execute("ALTER TABLE questions ADD COLUMN min_label VARCHAR(255) DEFAULT 'Poor' AFTER question_type")
                    conn.commit()

                cursor.execute("SHOW COLUMNS FROM questions LIKE 'max_label'")
                if not cursor.fetchone():
                    logger.info("Adding 'max_label' column to questions table...")
                    cursor.execute("ALTER TABLE questions ADD COLUMN max_label VARCHAR(255) DEFAULT 'Excellent' AFTER min_label")
                    conn.commit()

                cursor.execute("SHOW COLUMNS FROM questions LIKE 'allow_comment'")
                if not cursor.fetchone():
                    logger.info("Adding 'allow_comment' column to questions table...")
                    cursor.execute("ALTER TABLE questions ADD COLUMN allow_comment BOOLEAN DEFAULT FALSE AFTER max_label")
                    conn.commit()
                
                # Check for stores table columns
                store_columns = [
                    ("store_manager_name", "VARCHAR(255)"),
                    ("manager_contact", "VARCHAR(20)"),
                    ("store_type", "VARCHAR(100)"),
                    ("operating_hours", "VARCHAR(255)"),
                    ("status", "ENUM('active', 'inactive', 'pending') DEFAULT 'active'"),
                    ("logo_url", "VARCHAR(500)"),
                    ("access_token", "VARCHAR(100) UNIQUE"),
                    ("subdomain", "VARCHAR(100) UNIQUE")
                ]
                
                for column_name, column_type in store_columns:
                    cursor.execute(f"SHOW COLUMNS FROM stores LIKE '{column_name}'")
                    if not cursor.fetchone():
                        cursor.execute(f"ALTER TABLE stores ADD COLUMN {column_name} {column_type}")

                # Generate access tokens for existing stores that don't have them
                import secrets
                cursor.execute("SELECT id FROM stores WHERE access_token IS NULL OR access_token = ''")
                stores_without_token = cursor.fetchall()
                if stores_without_token:
                    logger.info(f"Generating access tokens for {len(stores_without_token)} existing stores...")
                    for store_row in stores_without_token:
                        store_id = store_row[0]
                        access_token = secrets.token_urlsafe(32)
                        cursor.execute("UPDATE stores SET access_token = %s WHERE id = %s", (access_token, store_id))
                    conn.commit()

                # Generate subdomains for existing stores that don't have them
                cursor.execute("SELECT id, store_name FROM stores WHERE subdomain IS NULL OR subdomain = ''")
                stores_without_subdomain = cursor.fetchall()
                if stores_without_subdomain:
                    logger.info(f"Generating subdomains for {len(stores_without_subdomain)} existing stores...")
                    for store_row in stores_without_subdomain:
                        store_id = store_row[0]
                        store_name = store_row[1]
                        # Generate subdomain from store name (lowercase, alphanumeric, hyphens)
                        import re
                        subdomain = re.sub(r'[^a-zA-Z0-9\s]', '', store_name).lower().replace(' ', '-')
                        subdomain = re.sub(r'-+', '-', subdomain).strip('-')
                        # Ensure uniqueness by adding random suffix if needed
                        cursor.execute("SELECT id FROM stores WHERE subdomain = %s", (subdomain,))
                        if cursor.fetchone():
                            subdomain = f"{subdomain}-{secrets.token_hex(3)}"
                        cursor.execute("UPDATE stores SET subdomain = %s WHERE id = %s", (subdomain, store_id))
                    conn.commit()

                # Check for responses table receipt_number column
                cursor.execute("SHOW COLUMNS FROM responses LIKE 'receipt_number'")
                if not cursor.fetchone():
                    logger.info("Adding 'receipt_number' column to responses table...")
                    cursor.execute("ALTER TABLE responses ADD COLUMN receipt_number VARCHAR(100) AFTER user_email")
                    conn.commit()
                
                # Add rating column to staff_commendations if missing
                cursor.execute("SHOW COLUMNS FROM staff_commendations LIKE 'rating'")
                if not cursor.fetchone():
                    logger.info("Adding 'rating' column to staff_commendations table...")
                    cursor.execute("ALTER TABLE staff_commendations ADD COLUMN rating INT DEFAULT 5 AFTER staff_id")
                    conn.commit()
                
                conn.commit()
                conn.close()
                logger.info("Master schema check/update completed.")
                break
            except Exception as e:
                logger.error(f"Database initialization error: {e}")
                retries -= 1
                if retries > 0:
                    time.sleep(5)
                else:
                    logger.critical("Could not initialize database schema after multiple attempts.")

    # Only run schema init if we're not in a testing environment
    if not os.getenv("TESTING"):
        init_master_schema()

    # --- ERROR HANDLERS ---
    @app.errorhandler(Exception)
    def handle_exception(e):
        """Global error handler to show tracebacks for ANY crash in Railway"""
        error_details = traceback.format_exc()
        logger.error(f"Global Crash: {e}\n{error_details}")
        
        # In production, we'll show the error directly to fix things quickly
        return f"""
        <div style="font-family: sans-serif; padding: 20px; color: #721c24; background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px;">
            <h2 style="margin-top: 0;">Oops! Something crashed.</h2>
            <p><b>Error:</b> {e}</p>
            <hr>
            <p><b>Traceback for Debugging:</b></p>
            <pre style="background: #fff; padding: 15px; border-radius: 4px; overflow: auto; font-size: 13px;">{error_details}</pre>
        </div>
        """, 500

    @app.errorhandler(404)
    def not_found_error(error):
        return "404 Not Found", 404

    @app.route("/debug/env")
    def debug_env():
        """Route to see available environment variable keys (NOT values)"""
        return jsonify({
            "available_keys": list(os.environ.keys()),
            "db_config_host": app.config["DB_CONFIG"].get("host"),
            "db_config_port": app.config["DB_CONFIG"].get("port"),
            "python_version": sys.version
        })

    def fetch_stores() -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, store_name, address, city, province, postal_code,
                       contact_number, email, store_manager_name, manager_contact,
                       store_type, status, created_at, access_token, subdomain
                FROM stores
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        return rows

    def fetch_store_by_id(store_id: int) -> Dict[str, Any] | None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, store_name, address, city, province, postal_code,
                       contact_number, email, store_manager_name, manager_contact,
                       store_type, status, created_at, logo_url, access_token, subdomain
                FROM stores
                WHERE id = %s
                LIMIT 1
                """,
                (store_id,),
            )
            store = cursor.fetchone()
        finally:
            conn.close()

        return store

    def create_store(
        store_name: str,
        address: str | None = None,
        city: str | None = None,
        province: str | None = None,
        postal_code: str | None = None,
        contact_number: str | None = None,
        email: str | None = None,
        store_manager_name: str | None = None,
        manager_contact: str | None = None,
        store_type: str | None = None,
        status: str = "active",
        logo_url: str | None = None,
        subdomain: str | None = None
    ) -> int:
        import secrets
        access_token = secrets.token_urlsafe(32)
        
        # Generate subdomain from store name if not provided
        if not subdomain:
            import re
            subdomain = re.sub(r'[^a-zA-Z0-9\s]', '', store_name).lower().replace(' ', '-')
            subdomain = re.sub(r'-+', '-', subdomain).strip('-')
        
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO stores (
                    store_name, address, city, province, postal_code,
                    contact_number, email, store_manager_name, manager_contact,
                    store_type, status, logo_url, access_token, subdomain
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    store_name, address, city, province, postal_code,
                    contact_number, email, store_manager_name, manager_contact,
                    store_type, status, logo_url, access_token, subdomain
                ),
            )
            conn.commit()
            new_store_id = int(cursor.lastrowid)
        finally:
            conn.close()

        return new_store_id

    def fetch_questionnaire_by_store(store_id: int) -> Dict[str, Any] | None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, store_id, title, is_active, created_at
                FROM questionnaires
                WHERE store_id = %s
                ORDER BY id ASC
                LIMIT 1
                """,
                (store_id,),
            )
            questionnaire = cursor.fetchone()
        finally:
            conn.close()

        return questionnaire

    def fetch_questions_for_questionnaire(questionnaire_id: int) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, question_text, question_type, min_label, max_label, allow_comment, is_required, question_order
                FROM questions
                WHERE questionnaire_id = %s AND is_active = TRUE
                ORDER BY question_order ASC, id ASC
                """,
                (questionnaire_id,),
            )
            questions = cursor.fetchall()
        finally:
            conn.close()

        return questions

    def fetch_options_for_questions(question_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        if not question_ids:
            return {}

        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            placeholders = ", ".join(["%s"] * len(question_ids))
            cursor.execute(
                f"""
                SELECT question_id, id, option_text
                FROM question_options
                WHERE question_id IN ({placeholders})
                ORDER BY question_id ASC, id ASC
                """,
                tuple(question_ids),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        by_question: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            qid = int(row["question_id"])
            by_question.setdefault(qid, []).append({"id": row["id"], "option_text": row["option_text"]})
        return by_question

    def get_store_public_url(store_id: int) -> str:
        # Get the IP address of the machine to make it accessible on the local network
        try:
            s = socket.socket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            base_url = f"http://{local_ip}:8000"
        except Exception:
            base_url = request.url_root.rstrip('/')
            
        return f"{base_url}{url_for('public_survey', store_id=store_id)}"

    def generate_qr_data_uri(text: str) -> str:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    @app.route("/admin/stores/<int:store_id>/qr-download")
    def download_qr(store_id: int):
        """Download QR code as PNG file."""
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            flash("Store not found", "danger")
            return redirect(url_for("stores_management"))
        public_url = get_store_public_url(store_id=store_id)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(public_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        filename = f"QR_{store['store_name'].replace(' ', '_')}.png"
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name=filename)

    # -------------------------
    # REPORT GENERATION (CSV & PDF)
    # -------------------------
    def _get_report_data(store_id: int, month: str = None):
        """Gather feedback data for reports, optionally filtered by month (YYYY-MM)."""
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return None, None, None, None, None
        all_feedback = fetch_responses_for_store(store_id=store_id, limit=10000)

        # Filter by month if provided
        if month:
            filtered = []
            for fb in all_feedback:
                submitted = fb.get("submitted_at")
                if submitted:
                    if isinstance(submitted, str):
                        try:
                            submitted = datetime.strptime(submitted, "%Y-%m-%d %H:%M:%S")
                        except (ValueError, TypeError):
                            continue
                    if submitted.strftime("%Y-%m") == month:
                        filtered.append(fb)
            all_feedback = filtered

        response_ids = [int(r["id"]) for r in all_feedback]
        answers_map = fetch_answers_for_responses(response_ids) if response_ids else {}

        # Get commendations
        commendations_map = {}
        if response_ids:
            conn = get_db_connection()
            try:
                cursor = conn.cursor(dictionary=True)
                ph = ','.join(['%s'] * len(response_ids))
                cursor.execute(f"""
                    SELECT sc.response_id, s.first_name, s.last_name, s.position
                    FROM staff_commendations sc
                    JOIN staff s ON s.id = sc.staff_id
                    WHERE sc.response_id IN ({ph})
                """, response_ids)
                for row in cursor.fetchall():
                    commendations_map.setdefault(int(row["response_id"]), []).append(row)
            finally:
                conn.close()

        return store, all_feedback, answers_map, commendations_map, response_ids

    @app.route("/admin/stores/<int:store_id>/report/csv")
    def download_report_csv(store_id: int):
        """Download feedback data as CSV."""
        month = request.args.get("month", "")
        store, feedback_list, answers_map, commendations_map, _ = _get_report_data(store_id, month or None)
        if not store:
            flash("Store not found", "danger")
            return redirect(url_for("stores_management"))

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Feedback ID", "Date", "Email", "Receipt #", "Status",
            "Question", "Type", "Rating", "Answer",
            "Commended Staff"
        ])

        for fb in feedback_list:
            fb_id = int(fb["id"])
            submitted = fb.get("submitted_at", "")
            if hasattr(submitted, "strftime"):
                submitted = submitted.strftime("%Y-%m-%d %H:%M:%S")
            email = fb.get("user_email", "")
            receipt = fb.get("receipt_number", "")
            status = fb.get("status", "")
            answers = answers_map.get(fb_id, [])
            comms = commendations_map.get(fb_id, [])
            comm_names = ", ".join(f"{c['first_name']} {c['last_name']}" for c in comms)

            if answers:
                for ans in answers:
                    writer.writerow([
                        fb_id, submitted, email, receipt, status,
                        ans.get("question_text", ""),
                        ans.get("question_type", ""),
                        ans.get("rating_value", ""),
                        ans.get("answer_text", ""),
                        comm_names
                    ])
            else:
                writer.writerow([fb_id, submitted, email, receipt, status, "", "", "", "", comm_names])

        buf = io.BytesIO()
        buf.write(output.getvalue().encode("utf-8"))
        buf.seek(0)
        month_label = month if month else "all"
        filename = f"Report_{store['store_name'].replace(' ', '_')}_{month_label}.csv"
        return send_file(buf, mimetype="text/csv", as_attachment=True, download_name=filename)

    @app.route("/admin/stores/<int:store_id>/report/pdf")
    def download_report_pdf(store_id: int):
        """Download feedback report as PDF."""
        month = request.args.get("month", "")
        store, feedback_list, answers_map, commendations_map, _ = _get_report_data(store_id, month or None)
        if not store:
            flash("Store not found", "danger")
            return redirect(url_for("stores_management"))

        total = len(feedback_list)
        resolved = sum(1 for f in feedback_list if f.get("status") == "resolved")
        unresolved = total - resolved
        resolution_rate = round(resolved / total * 100, 1) if total > 0 else 0

        # Rating stats
        rating_dist = [0, 0, 0, 0, 0]
        total_ratings = 0
        for fb in feedback_list:
            for ans in answers_map.get(int(fb["id"]), []):
                rv = ans.get("rating_value")
                if rv:
                    r = int(float(rv))
                    if 1 <= r <= 5:
                        rating_dist[r - 1] += 1
                        total_ratings += 1
        avg_rating = round(sum((i + 1) * c for i, c in enumerate(rating_dist)) / total_ratings, 2) if total_ratings > 0 else 0

        # Top commended staff
        staff_counts = defaultdict(int)
        for fb in feedback_list:
            for c in commendations_map.get(int(fb["id"]), []):
                staff_counts[f"{c['first_name']} {c['last_name']}"] += 1
        top_staff = sorted(staff_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Build PDF
        month_label = month if month else "All Time"
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Title
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, f"{store['store_name']} - Feedback Report", ln=True, align="C")
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, f"Period: {month_label}  |  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
        pdf.ln(8)

        # KPI Summary
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Summary", ln=True)
        pdf.set_font("Helvetica", "", 10)
        col_w = 47.5
        pdf.set_fill_color(240, 240, 240)
        for label, val in [("Total Feedback", total), ("Resolved", resolved), ("Unresolved", unresolved), ("Resolution Rate", f"{resolution_rate}%")]:
            pdf.cell(col_w, 18, f"{label}\n{val}", border=1, align="C", fill=True)
        pdf.ln(18)
        pdf.ln(4)

        # Rating summary
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Ratings", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(95, 8, f"Average Rating: {avg_rating} / 5", ln=False)
        pdf.cell(95, 8, f"Total Ratings: {total_ratings}", ln=True)
        # Rating distribution table
        pdf.set_font("Helvetica", "B", 9)
        for i in range(5):
            pdf.cell(38, 7, f"{i+1} Star", border=1, align="C", fill=True)
        pdf.ln(7)
        pdf.set_font("Helvetica", "", 9)
        for i in range(5):
            pct = round(rating_dist[i] / total_ratings * 100, 1) if total_ratings > 0 else 0
            pdf.cell(38, 7, f"{rating_dist[i]} ({pct}%)", border=1, align="C")
        pdf.ln(7)
        pdf.ln(6)

        # Top Commended Staff
        if top_staff:
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(0, 8, "Top Commended Staff", ln=True)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(10, 7, "#", border=1, align="C", fill=True)
            pdf.cell(100, 7, "Staff Member", border=1, fill=True)
            pdf.cell(40, 7, "Commendations", border=1, align="C", fill=True)
            pdf.ln(7)
            pdf.set_font("Helvetica", "", 9)
            for idx, (name, count) in enumerate(top_staff, 1):
                pdf.cell(10, 7, str(idx), border=1, align="C")
                pdf.cell(100, 7, name, border=1)
                pdf.cell(40, 7, str(count), border=1, align="C")
                pdf.ln(7)
            pdf.ln(6)

        # Feedback Detail Table
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Feedback Details", ln=True)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(230, 230, 230)
        col_widths = [15, 30, 45, 25, 20, 55]
        headers = ["ID", "Date", "Email", "Receipt #", "Status", "Avg Rating"]
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 7, h, border=1, align="C", fill=True)
        pdf.ln(7)

        pdf.set_font("Helvetica", "", 7)
        for fb in feedback_list:
            fb_id = int(fb["id"])
            submitted = fb.get("submitted_at", "")
            if hasattr(submitted, "strftime"):
                submitted = submitted.strftime("%m/%d/%y")
            elif isinstance(submitted, str) and len(submitted) > 10:
                submitted = submitted[:10]
            email = (fb.get("user_email", "") or "")[:25]
            receipt = (fb.get("receipt_number", "") or "")[:15]
            status = fb.get("status", "")
            answers = answers_map.get(fb_id, [])
            ratings = [float(a["rating_value"]) for a in answers if a.get("rating_value")]
            avg_r = round(sum(ratings) / len(ratings), 1) if ratings else "N/A"

            pdf.cell(col_widths[0], 6, str(fb_id), border=1, align="C")
            pdf.cell(col_widths[1], 6, str(submitted), border=1, align="C")
            pdf.cell(col_widths[2], 6, email, border=1)
            pdf.cell(col_widths[3], 6, receipt, border=1, align="C")
            pdf.cell(col_widths[4], 6, status, border=1, align="C")
            pdf.cell(col_widths[5], 6, str(avg_r), border=1, align="C")
            pdf.ln(6)

        buf = io.BytesIO()
        pdf.output(buf)
        buf.seek(0)
        filename = f"Report_{store['store_name'].replace(' ', '_')}_{month_label.replace(' ', '_')}.pdf"
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=filename)

    # -------------------------
    # TEMPLATE QUESTIONNAIRE CRUD
    # -------------------------
    def fetch_template_questionnaire() -> Dict[str, Any] | None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, title, is_active, version, created_at, updated_at, logo_url
                FROM questionnaires
                WHERE is_template = TRUE
                ORDER BY id ASC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        return row

    def ensure_template_questionnaire() -> Dict[str, Any]:
        existing = fetch_template_questionnaire()
        if existing:
            return existing
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO questionnaires (store_id, title, is_active, is_template, version)
                VALUES (NULL, %s, %s, %s, %s)
                """,
                ("Customer Feedback", True, True, 1),
            )
            conn.commit()
            template_id = int(cursor.lastrowid)
        finally:
            conn.close()
        return {"id": template_id, "title": "Customer Feedback", "is_active": 1, "version": 1, "created_at": None, "is_template": True}

    def update_template_questionnaire(title: str, is_active: bool, updated_at: str | None = None) -> None:
        template = ensure_template_questionnaire()
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            if updated_at:
                cursor.execute(
                    """
                    UPDATE questionnaires
                    SET title = %s, is_active = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (title, is_active, updated_at, int(template["id"])),
                )
            else:
                cursor.execute(
                    """
                    UPDATE questionnaires
                    SET title = %s, is_active = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (title, is_active, int(template["id"])),
                )
            conn.commit()
        finally:
            conn.close()

    def fetch_template_questions(template_questionnaire_id: int) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, question_text, question_type, min_label, max_label, allow_comment, is_required, question_order
                FROM questions
                WHERE questionnaire_id = %s
                ORDER BY question_order ASC, id ASC
                """,
                (template_questionnaire_id,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        return rows

    def fetch_template_options_by_question(template_question_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        if not template_question_ids:
            return {}
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            placeholders = ", ".join(["%s"] * len(template_question_ids))
            cursor.execute(
                f"""
                SELECT question_id, id, option_text
                FROM question_options
                WHERE question_id IN ({placeholders})
                ORDER BY question_id ASC, id ASC
                """,
                tuple(template_question_ids),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        by_q: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            qid = int(r["question_id"])
            by_q.setdefault(qid, []).append({"id": r["id"], "option_text": r["option_text"]})
        return by_q

    def add_template_question(
        template_questionnaire_id: int,
        question_text: str,
        question_type: str,
        is_required: bool,
        question_order: int,
        min_label: str = "Poor",
        max_label: str = "Excellent",
        allow_comment: bool = False,
    ) -> int:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO questions
                (questionnaire_id, question_text, question_type, min_label, max_label, allow_comment, is_required, question_order, is_template)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (template_questionnaire_id, question_text, question_type, min_label, max_label, allow_comment, is_required, question_order, True),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def delete_template_question(template_question_id: int) -> None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM questions WHERE id = %s", (template_question_id,))
            conn.commit()
        finally:
            conn.close()

    def update_template_question(question_id: int, question_text: str, question_type: str, is_required: bool, min_label: str = "Poor", max_label: str = "Excellent", allow_comment: bool = False) -> None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE questions
                SET question_text = %s, question_type = %s, is_required = %s, min_label = %s, max_label = %s, allow_comment = %s
                WHERE id = %s AND is_template = TRUE
                """,
                (question_text, question_type, is_required, min_label, max_label, allow_comment, question_id),
            )
            conn.commit()
        finally:
            conn.close()

    def add_template_option(template_question_id: int, option_text: str) -> int:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO question_options (question_id, option_text)
                VALUES (%s, %s)
                """,
                (template_question_id, option_text),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def delete_template_option(template_option_id: int) -> None:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM question_options WHERE id = %s", (template_option_id,))
            conn.commit()
        finally:
            conn.close()

    def publish_template_to_all_stores() -> int:
        template = ensure_template_questionnaire()
        template_id = int(template["id"])
        template_questions = fetch_template_questions(template_questionnaire_id=template_id)
        template_options_by_question_id = fetch_template_options_by_question([int(q["id"]) for q in template_questions])

        stores = fetch_stores()
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            published_count = 0

            for store in stores:
                store_id = int(store["id"])

                # Check if store already has a questionnaire
                cursor.execute("SELECT id FROM questionnaires WHERE store_id = %s AND is_template = FALSE ORDER BY id ASC LIMIT 1", (store_id,))
                existing = cursor.fetchone()
                
                if existing:
                    # Update existing questionnaire metadata without deleting it
                    cursor.execute(
                        """
                        UPDATE questionnaires
                        SET title = %s, is_active = %s, template_id = %s
                        WHERE id = %s
                        """,
                        (template["title"], bool(template["is_active"]), template_id, int(existing["id"])),
                    )
                    questionnaire_id = int(existing["id"])
                    
                    # Deactivate existing questions instead of deleting them
                    cursor.execute(
                        """
                        UPDATE questions
                        SET is_active = FALSE
                        WHERE questionnaire_id = %s AND is_template = FALSE
                        """,
                        (questionnaire_id,),
                    )
                else:
                    # Create new store questionnaire
                    cursor.execute(
                        """
                        INSERT INTO questionnaires (store_id, title, is_active, is_template, template_id)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (store_id, template["title"], bool(template["is_active"]), False, template_id),
                    )
                    questionnaire_id = int(cursor.lastrowid)

                # Add new active questions from template
                question_id_map: Dict[int, int] = {}
                for tq in template_questions:
                    cursor.execute(
                        """
                        INSERT INTO questions
                        (questionnaire_id, question_text, question_type, min_label, max_label, allow_comment, is_required, question_order, is_template, template_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            questionnaire_id,
                            tq["question_text"],
                            tq["question_type"],
                            tq.get("min_label", "Poor"),
                            tq.get("max_label", "Excellent"),
                            bool(tq.get("allow_comment", False)),
                            bool(tq["is_required"]),
                            int(tq["question_order"]),
                            False,  # Store questions are not templates
                            int(tq["id"]),  # Link to template question
                        ),
                    )
                    new_qid = int(cursor.lastrowid)
                    question_id_map[int(tq["id"])] = new_qid

                for old_tq_id, opts in template_options_by_question_id.items():
                    new_qid = question_id_map.get(int(old_tq_id))
                    if not new_qid:
                        continue
                    for opt in opts:
                        cursor.execute(
                            """
                            INSERT INTO question_options (question_id, option_text)
                            VALUES (%s, %s)
                            """,
                            (new_qid, opt["option_text"]),
                        )

                published_count += 1

            conn.commit()
            return published_count
        finally:
            conn.close()

    @app.route("/")
    def index():
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/questionnaire", methods=["GET", "POST"])
    def master_questionnaire():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            is_active = request.form.get("is_active") == "on"
            updated_at = request.form.get("updated_at", "").strip()
            if not title:
                flash("Template questionnaire title is required.", "danger")
                return redirect(url_for("master_questionnaire"))
            
            template = ensure_template_questionnaire()
            update_template_questionnaire(title=title, is_active=is_active, updated_at=updated_at if updated_at else None)
            flash("Questionnaire Saved Successfully", "success")
            return redirect(url_for("master_questionnaire"))

        # Single database connection for better performance
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Get template
            cursor.execute(
                """
                SELECT id, title, is_active, version, created_at, updated_at, logo_url
                FROM questionnaires
                WHERE is_template = TRUE
                ORDER BY id ASC
                LIMIT 1
                """
            )
            template = cursor.fetchone()
            
            if template:
                template_id = int(template["id"])
                
                # Get questions with single query
                cursor.execute(
                    """
                    SELECT q.id, q.question_text, q.question_type, q.min_label, q.max_label, 
                           q.allow_comment, q.is_required, q.question_order,
                           qo.id as option_id, qo.option_text
                    FROM questions q
                    LEFT JOIN question_options qo ON q.id = qo.question_id
                    WHERE q.questionnaire_id = %s
                    ORDER BY q.question_order ASC, q.id ASC, qo.id ASC
                    """,
                    (template_id,),
                )
                rows = cursor.fetchall()
                
                # Organize questions and options
                questions = []
                options_by_question_id = {}
                current_question = None
                
                for row in rows:
                    qid = int(row["id"])
                    
                    # Create question if not exists
                    if qid not in [q.get("id") for q in questions]:
                        questions.append({
                            "id": qid,
                            "question_text": row["question_text"],
                            "question_type": row["question_type"],
                            "min_label": row["min_label"],
                            "max_label": row["max_label"],
                            "allow_comment": bool(row["allow_comment"]),
                            "is_required": bool(row["is_required"]),
                            "question_order": int(row["question_order"])
                        })
                        options_by_question_id[qid] = []
                    
                    # Add option if exists
                    if row["option_id"]:
                        options_by_question_id[qid].append({
                            "id": row["option_id"],
                            "option_text": row["option_text"]
                        })
                        
        finally:
            conn.close()

        return render_template(
            "master_questionnaire/master_questionnaire.html",
            master=template,
            questions=questions,
            options_by_question_id=options_by_question_id,
        )

    @app.route("/admin/questionnaire/questions/add", methods=["POST"])
    def master_add_question():
        template = ensure_template_questionnaire()
        template_id = int(template["id"])

        question_text = request.form.get("question_text", "").strip()
        question_type = request.form.get("question_type", "").strip()
        is_required = request.form.get("is_required") == "on"
        min_label = request.form.get("min_label", "Poor").strip() or "Poor"
        max_label = request.form.get("max_label", "Excellent").strip() or "Excellent"
        allow_comment = request.form.get("allow_comment") == "on"
        try:
            question_order = int(request.form.get("question_order", "0"))
        except ValueError:
            question_order = 0

        if not question_text:
            flash("Question text is required.", "danger")
            return redirect(url_for("master_questionnaire"))

        if question_type not in {"rating", "text", "multiple_choice"}:
            flash("Invalid question type.", "danger")
            return redirect(url_for("master_questionnaire"))

        add_template_question(
            template_questionnaire_id=template_id,
            question_text=question_text,
            question_type=question_type,
            is_required=is_required,
            question_order=question_order,
            min_label=min_label,
            max_label=max_label,
            allow_comment=allow_comment,
        )
        flash("Question Added Successfully", "success")
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/questions/<int:master_question_id>/delete", methods=["POST"])
    def master_delete_question(master_question_id: int):
        delete_template_question(template_question_id=master_question_id)
        flash("Question Deleted", "success")
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/questions/<int:master_question_id>/edit", methods=["POST"])
    def master_edit_question(master_question_id: int):
        question_text = request.form.get("question_text", "").strip()
        question_type = request.form.get("question_type", "").strip()
        is_required = request.form.get("is_required") == "on"
        min_label = request.form.get("min_label", "Poor").strip() or "Poor"
        max_label = request.form.get("max_label", "Excellent").strip() or "Excellent"
        allow_comment = request.form.get("allow_comment") == "on"

        if not question_text:
            flash("Question text is required.", "danger")
            return redirect(url_for("master_questionnaire"))

        update_template_question(master_question_id, question_text, question_type, is_required, min_label, max_label, allow_comment)
        flash("Question Updated Successfully", "success")
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/questions/<int:master_question_id>/options/add", methods=["POST"])
    def master_add_option(master_question_id: int):
        option_text = request.form.get("option_text", "").strip()
        if not option_text:
            flash("Option text is required.", "danger")
            return redirect(url_for("master_questionnaire"))
        add_template_option(template_question_id=master_question_id, option_text=option_text)
        flash("Option added.", "success")
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/options/<int:master_option_id>/delete", methods=["POST"])
    def master_delete_option(master_option_id: int):
        delete_template_option(template_option_id=master_option_id)
        flash("Option deleted.", "success")
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/upload-logo", methods=["POST"])
    def master_upload_logo():
        # Handle logo upload for master questionnaire - store as base64 in database
        logo_data = None
        if 'logo' in request.files:
            logo_file = request.files['logo']
            if logo_file and logo_file.filename:
                # Validate file type
                allowed_extensions = {'png', 'jpg', 'jpeg'}
                if logo_file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                    flash("Invalid file type. Only PNG, JPG, and JPEG files are allowed.", "danger")
                    return redirect(url_for("master_questionnaire"))
                
                # Validate file size (5MB max)
                logo_file.seek(0, os.SEEK_END)
                file_size = logo_file.tell()
                logo_file.seek(0)
                if file_size > 5 * 1024 * 1024:
                    flash("File size exceeds 5MB limit.", "danger")
                    return redirect(url_for("master_questionnaire"))
                
                # Convert image to base64 with data URI prefix
                import base64
                logo_bytes = logo_file.read()
                file_ext = logo_file.filename.rsplit('.', 1)[1].lower()
                mime_type = f"image/{file_ext}"
                logo_data = f"data:{mime_type};base64,{base64.b64encode(logo_bytes).decode('utf-8')}"

        # Update the master template questionnaire with the base64 logo data
        if logo_data:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE questionnaires SET logo_url = %s WHERE is_template = 1", (logo_data,))
                conn.commit()
                flash("Brand logo uploaded successfully", "success")
            except Exception as e:
                logger.error(f"Error uploading logo: {e}")
                flash(f"Error uploading logo: {e}", "danger")
            finally:
                conn.close()
        else:
            flash("No file selected", "warning")

        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/delete-logo", methods=["POST"])
    def master_delete_logo():
        # Delete the logo from the master questionnaire
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE questionnaires SET logo_url = NULL WHERE is_template = 1")
            conn.commit()
            flash("Brand logo deleted successfully", "success")
        except Exception as e:
            logger.error(f"Error deleting logo: {e}")
            flash(f"Error deleting logo: {e}", "danger")
        finally:
            conn.close()

        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/publish", methods=["POST"])
    def master_publish():
        template = ensure_template_questionnaire()
        template_id = int(template["id"])
        questions = fetch_template_questions(template_questionnaire_id=template_id)
        if not questions:
            flash("Add at least 1 question before publishing.", "danger")
            return redirect(url_for("master_questionnaire"))

        count = publish_template_to_all_stores()
        flash(f"Published to {count} store(s) Successfully", "success")
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/toggle-active", methods=["POST"])
    def master_toggle_active():
        template = ensure_template_questionnaire()
        current_active = template.get("is_active", False)
        new_active = not current_active
        
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE questionnaires
                SET is_active = %s
                WHERE id = %s AND is_template = TRUE
                """,
                (new_active, int(template["id"])),
            )
            conn.commit()
        finally:
            conn.close()
        
        if new_active:
            flash("Survey enabled successfully. Stores can now accept feedback.", "success")
        else:
            flash("Survey disabled successfully. Stores can no longer accept feedback.", "warning")
        
        return redirect(url_for("master_questionnaire"))

    @app.route("/admin/questionnaire/preview")
    def master_preview():
        template = ensure_template_questionnaire()
        template_id = int(template["id"])
        questions = fetch_template_questions(template_questionnaire_id=template_id)
        question_ids = [q["id"] for q in questions]
        options_by_question_id = fetch_options_for_questions(question_ids)

        return render_template(
            "master_questionnaire/preview.html",
            master=template,
            questions=questions,
            options_by_question_id=options_by_question_id,
        )

    # -------------------------
    # DASHBOARD ANALYTICS
    # -------------------------
    def fetch_dashboard_analytics() -> Dict[str, Any]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Store overview data
            cursor.execute(
                """
                SELECT s.id, s.store_name, s.address, s.city, s.created_at,
                       COUNT(DISTINCT r.id) as total_responses,
                       AVG(CASE WHEN q2.question_type = 'rating' THEN a.rating_value END) as avg_rating,
                       COUNT(DISTINCT r.user_email) as unique_users
                FROM stores s
                LEFT JOIN questionnaires q ON s.id = q.store_id
                LEFT JOIN responses r ON q.id = r.questionnaire_id
                LEFT JOIN answers a ON r.id = a.response_id
                LEFT JOIN questions q2 ON a.question_id = q2.id
                GROUP BY s.id, s.store_name, s.address, s.city, s.created_at
                ORDER BY total_responses DESC
                """
            )
            stores_data = cursor.fetchall()
            
            # Convert Decimal values to float for template compatibility
            for store in stores_data:
                store['avg_rating'] = float(store['avg_rating']) if store['avg_rating'] is not None else 0.0
            
            # Overall statistics
            cursor.execute(
                """
                SELECT 
                    COUNT(DISTINCT r.id) as total_responses,
                    COUNT(DISTINCT s.id) as total_stores,
                    COUNT(DISTINCT r.user_email) as total_unique_users,
                    AVG(CASE WHEN q2.question_type = 'rating' THEN a.rating_value END) as overall_avg_rating,
                    COUNT(DISTINCT q.id) as total_questionnaires
                FROM stores s
                LEFT JOIN questionnaires q ON s.id = q.store_id
                LEFT JOIN responses r ON q.id = r.questionnaire_id
                LEFT JOIN answers a ON r.id = a.response_id
                LEFT JOIN questions q2 ON a.question_id = q2.id
                """
            )
            overall_stats = cursor.fetchone()
            
            if overall_stats:
                overall_stats['overall_avg_rating'] = float(overall_stats['overall_avg_rating']) if overall_stats['overall_avg_rating'] is not None else 0.0
            else:
                overall_stats = {
                    'total_responses': 0,
                    'total_stores': 0,
                    'total_unique_users': 0,
                    'overall_avg_rating': 0,
                    'total_questionnaires': 0
                }
            
            # Recent activity (last 7 days, linked to stores)
            cursor.execute(
                """
                SELECT DATE(r.submitted_at) as date, COUNT(DISTINCT r.id) as responses
                FROM responses r
                INNER JOIN questionnaires q ON r.questionnaire_id = q.id
                INNER JOIN stores s ON q.store_id = s.id
                WHERE r.submitted_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                GROUP BY DATE(r.submitted_at)
                ORDER BY date
                """
            )
            recent_activity = cursor.fetchall()
            
            # Top performing stores by feedback
            cursor.execute(
                """
                SELECT s.store_name, COUNT(r.id) as response_count
                FROM stores s
                LEFT JOIN questionnaires q ON s.id = q.store_id
                LEFT JOIN responses r ON q.id = r.questionnaire_id
                GROUP BY s.id, s.store_name
                ORDER BY response_count DESC
                LIMIT 5
                """
            )
            top_stores = cursor.fetchall()

            # Best overall store (highest average rating with minimum responses)
            cursor.execute(
                """
                SELECT s.id, s.store_name, s.address, s.city,
                       COUNT(DISTINCT r.id) as total_responses,
                       AVG(CASE WHEN q2.question_type = 'rating' THEN a.rating_value END) as avg_rating
                FROM stores s
                LEFT JOIN questionnaires q ON s.id = q.store_id
                LEFT JOIN responses r ON q.id = r.questionnaire_id
                LEFT JOIN answers a ON r.id = a.response_id
                LEFT JOIN questions q2 ON a.question_id = q2.id
                WHERE q2.question_type = 'rating'
                GROUP BY s.id, s.store_name, s.address, s.city
                HAVING total_responses >= 1
                ORDER BY avg_rating DESC, total_responses DESC
                LIMIT 1
                """
            )
            best_overall_store = cursor.fetchone()
            if best_overall_store:
                best_overall_store['avg_rating'] = float(best_overall_store['avg_rating']) if best_overall_store['avg_rating'] is not None else 0.0

            # Best overall staff (highest weighted score: avg_rating * SQRT(count))
            cursor.execute(
                """
                SELECT s.id, s.first_name, s.last_name, s.position, s.role,
                       AVG(sc.rating) as avg_rating,
                       COUNT(sc.id) as commendation_count,
                       AVG(sc.rating) * SQRT(COUNT(sc.id)) as weighted_score,
                       st.store_name
                FROM staff s
                LEFT JOIN staff_commendations sc ON s.id = sc.staff_id
                LEFT JOIN responses r ON sc.response_id = r.id
                LEFT JOIN questionnaires q ON r.questionnaire_id = q.id
                LEFT JOIN stores st ON q.store_id = st.id
                GROUP BY s.id, s.first_name, s.last_name, s.position, s.role, st.store_name
                HAVING avg_rating IS NOT NULL
                ORDER BY weighted_score DESC
                LIMIT 1
                """
            )
            best_overall_staff = cursor.fetchone()
            if best_overall_staff:
                best_overall_staff['avg_rating'] = float(best_overall_staff['avg_rating']) if best_overall_staff['avg_rating'] is not None else 0.0
                best_overall_staff['weighted_score'] = float(best_overall_staff['weighted_score']) if best_overall_staff['weighted_score'] is not None else 0.0

            return {
                'stores_data': stores_data,
                'overall_stats': overall_stats,
                'recent_activity': recent_activity,
                'top_stores': top_stores,
                'best_overall_store': best_overall_store,
                'best_overall_staff': best_overall_staff
            }
        finally:
            conn.close()

    @app.route("/admin/dashboard")
    def admin_dashboard():
        try:
            logger.info("Accessing admin dashboard...")
            analytics = fetch_dashboard_analytics()
            return render_template("dashboard/dashboard.html", **analytics)
        except Exception as e:
            error_details = traceback.format_exc()
            logger.error(f"Dashboard Crash: {e}\n{error_details}")
            return f"Dashboard Error: {e}<br><pre>{error_details}</pre>", 500

    @app.route("/dashboard/staff-overall")
    def staff_overall():
        """Staff overall page showing all staff with ratings and performance metrics."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Fetch all staff with their commendation ratings and metrics
            cursor.execute("""
                SELECT s.id, s.first_name, s.last_name, s.email, s.phone, s.position, s.role, s.status, st.store_name, s.store_id,
                       AVG(sc.rating) as avg_rating,
                       COUNT(sc.id) as commendation_count,
                       AVG(sc.rating) * SQRT(COUNT(sc.id)) as weighted_score
                FROM staff s
                LEFT JOIN staff_commendations sc ON s.id = sc.staff_id
                LEFT JOIN stores st ON s.store_id = st.id
                GROUP BY s.id, s.first_name, s.last_name, s.email, s.phone, s.position, s.role, s.status, st.store_name, s.store_id
                ORDER BY weighted_score DESC, s.last_name, s.first_name
            """)
            staff_data = cursor.fetchall()
            
            # Format the data
            for staff in staff_data:
                staff['avg_rating'] = float(staff['avg_rating']) if staff['avg_rating'] else 0.0
                staff['commendation_count'] = int(staff['commendation_count']) if staff['commendation_count'] else 0
                staff['weighted_score'] = float(staff['weighted_score']) if staff['weighted_score'] else 0.0
            
            conn.close()
            return render_template("dashboard/staff_overall.html", staff_data=staff_data)
        except Exception as e:
            error_details = traceback.format_exc()
            logger.error(f"Staff Overall Error: {e}\n{error_details}")
            return f"Staff Overall Error: {e}<br><pre>{error_details}</pre>", 500

    @app.route("/api/dashboard/analytics")
    def api_dashboard_analytics():
        """JSON endpoint for overall dashboard analytics (used by store filter)."""
        try:
            analytics = fetch_dashboard_analytics()
            # Serialize for JSON
            stores_data = analytics.get('stores_data', [])
            overall = analytics.get('overall_stats', {})
            recent = analytics.get('recent_activity', [])
            top = analytics.get('top_stores', [])
            best_store = analytics.get('best_overall_store')
            best_staff = analytics.get('best_overall_staff')

            # Format recent_activity dates
            formatted_activity = []
            for a in recent:
                d = a.get('date')
                formatted_activity.append({
                    'date_label': d.strftime('%b %d') if d else '?',
                    'responses': a.get('responses', 0)
                })

            return jsonify({
                'stores_data': [
                    {
                        'id': s['id'],
                        'store_name': s['store_name'],
                        'address': s.get('address', ''),
                        'city': s.get('city', ''),
                        'total_responses': s.get('total_responses', 0),
                        'avg_rating': float(s['avg_rating']) if s.get('avg_rating') else 0.0,
                        'unique_users': s.get('unique_users', 0)
                    } for s in stores_data
                ],
                'overall_stats': {
                    'total_responses': overall.get('total_responses', 0),
                    'total_stores': overall.get('total_stores', 0),
                    'total_unique_users': overall.get('total_unique_users', 0),
                    'overall_avg_rating': float(overall.get('overall_avg_rating', 0)),
                    'total_questionnaires': overall.get('total_questionnaires', 0)
                },
                'recent_activity': formatted_activity,
                'top_stores': [
                    {'store_name': t['store_name'], 'response_count': t['response_count']}
                    for t in top
                ],
                'best_overall_store': {
                    'store_name': best_store['store_name'],
                    'avg_rating': float(best_store['avg_rating'])
                } if best_store else None,
                'best_overall_staff': {
                    'first_name': best_staff['first_name'],
                    'last_name': best_staff['last_name'],
                    'avg_rating': float(best_staff['avg_rating']) if best_staff.get('avg_rating') else 0.0
                } if best_staff else None
            })
        except Exception as e:
            logger.error(f"Dashboard API error: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route("/admin/stores/performance")
    def stores_performance():
        analytics = fetch_dashboard_analytics()
        return render_template(
            "dashboard/store_performance.html",
            stores_data=analytics.get("stores_data", []),
            overall_stats=analytics.get("overall_stats", {}),
        )

    @app.route("/admin/stores")
    def stores_management():
        stores = fetch_stores()

        selected_store_id_param = request.args.get("store_id")
        selected_store_id = None
        if selected_store_id_param:
            try:
                selected_store_id = int(selected_store_id_param)
            except ValueError:
                selected_store_id = None

        selected_store = None
        if selected_store_id is not None:
            for store in stores:
                if store["id"] == selected_store_id:
                    selected_store = store
                    break

        public_url = None
        qr_data_uri = None
        if selected_store:
            public_url = get_store_public_url(store_id=int(selected_store["id"]))
            qr_data_uri = generate_qr_data_uri(public_url)

        # Enhance stores with real feedback and staff counts
        stores_with_counts = []
        for store in stores:
            store_id = store["id"]
            feedback_count = get_feedback_count_for_store(store_id)
            staff_count = get_staff_count_for_store(store_id)
            store_with_counts = dict(store)
            store_with_counts["feedback_count"] = feedback_count
            store_with_counts["staff_count"] = staff_count
            stores_with_counts.append(store_with_counts)

        return render_template(
            "manage_stores/stores.html",
            stores=stores_with_counts,
            selected_store=selected_store,
            public_url=public_url,
            qr_data_uri=qr_data_uri,
        )

    def get_feedback_count_for_store(store_id: int) -> int:
        """Get the total number of feedback responses for a store."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM responses WHERE store_id = %s",
                (store_id,)
            )
            count = cursor.fetchone()[0]
            return int(count) if count else 0
        finally:
            conn.close()

    def get_staff_count_for_store(store_id: int) -> int:
        """Get the total number of staff members for a store."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM staff WHERE store_id = %s",
                (store_id,)
            )
            count = cursor.fetchone()[0]
            return int(count) if count else 0
        finally:
            conn.close()

    def get_staff_performance_for_store(store_id: int) -> List[Dict[str, Any]]:
        """Get staff members for a store ranked by weighted score (avg_rating * SQRT(count))."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT s.id, s.first_name, s.last_name, s.position, s.role,
                       AVG(sc.rating) as avg_rating,
                       COUNT(sc.id) as commendation_count,
                       AVG(sc.rating) * SQRT(COUNT(sc.id)) as weighted_score
                FROM staff s
                LEFT JOIN staff_commendations sc ON s.id = sc.staff_id
                WHERE s.store_id = %s
                GROUP BY s.id, s.first_name, s.last_name, s.position, s.role
                ORDER BY weighted_score DESC
                """,
                (store_id,)
            )
            return cursor.fetchall()
        finally:
            conn.close()

    # API endpoint for store feedback data
    @app.route("/api/stores/<int:store_id>/feedback", methods=["GET"])
    def api_store_feedback(store_id: int):
        """API endpoint to get feedback data for a store."""
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return jsonify({"error": "Store not found"}), 404
        
        feedback = fetch_responses_for_store(store_id=store_id, limit=5)
        return jsonify(feedback)

    # API endpoint for store analytics data
    @app.route("/api/stores/<int:store_id>/analytics", methods=["GET"])
    def api_store_analytics(store_id: int):
        """API endpoint to get analytics data for a store."""
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return jsonify({"error": "Store not found"}), 404
        
        # Fetch all feedback for analytics
        all_feedback = fetch_responses_for_store(store_id=store_id, limit=1000)
        total_feedback = len(all_feedback)
        
        # Resolved / unresolved counts
        resolved_count = sum(1 for f in all_feedback if f.get("status") == "resolved")
        unresolved_count = total_feedback - resolved_count
        resolution_rate = round((resolved_count / total_feedback * 100), 1) if total_feedback > 0 else 0
        
        # Calculate ratings
        all_response_ids = [int(r["id"]) for r in all_feedback]
        answers_by_response_id = fetch_answers_for_responses(all_response_ids) if all_feedback else {}
        
        # Rating distribution
        rating_distribution = [0, 0, 0, 0, 0]  # 1-5 stars
        total_ratings = 0
        for response_id, answers in answers_by_response_id.items():
            for answer in answers:
                if answer.get("rating_value"):
                    rating = int(float(answer["rating_value"]))
                    if 1 <= rating <= 5:
                        rating_distribution[rating - 1] += 1
                        total_ratings += 1
        
        # Calculate percentages
        five_star_count = rating_distribution[4]
        four_star_count = rating_distribution[3]
        
        five_star_rate = round((five_star_count / total_ratings * 100), 1) if total_ratings > 0 else 0
        four_plus_star_rate = round(((four_star_count + five_star_count) / total_ratings * 100), 1) if total_ratings > 0 else 0
        
        # Rating distribution percentages
        rating_pcts = [round(c / total_ratings * 100, 1) if total_ratings > 0 else 0 for c in rating_distribution]
        
        # Quality score
        quality_score = round(
            (rating_distribution[0] * 1 + rating_distribution[1] * 2 + 
             rating_distribution[2] * 3 + rating_distribution[3] * 4 + 
             rating_distribution[4] * 5) / total_ratings, 1
        ) if total_ratings > 0 else 0
        
        # Monthly feedback trend (last 6 months)
        monthly_trend = defaultdict(int)
        now = datetime.now()
        for fb in all_feedback:
            submitted = fb.get("submitted_at")
            if submitted:
                if isinstance(submitted, str):
                    try:
                        submitted = datetime.strptime(submitted, "%Y-%m-%d %H:%M:%S")
                    except (ValueError, TypeError):
                        continue
                key = submitted.strftime("%Y-%m")
                monthly_trend[key] += 1
        
        # Build last 6 months labels and values
        trend_labels = []
        trend_values = []
        for i in range(5, -1, -1):
            d = now - timedelta(days=i * 30)
            key = d.strftime("%Y-%m")
            label = d.strftime("%b")
            trend_labels.append(label)
            trend_values.append(monthly_trend.get(key, 0))
        
        # Staff commendations count + top staff
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            if all_response_ids:
                placeholders = ','.join(['%s'] * len(all_response_ids))
                cursor.execute(f"""
                    SELECT COUNT(*) as cnt FROM staff_commendations 
                    WHERE response_id IN ({placeholders})
                """, all_response_ids)
                total_commendations = cursor.fetchone()["cnt"]
                
                # Top 5 commended staff (by weighted score: avg_rating * SQRT(count))
                cursor.execute(f"""
                    SELECT s.first_name, s.last_name, s.position, s.role,
                           AVG(sc.rating) as avg_rating,
                           COUNT(sc.id) as commendation_count,
                           AVG(sc.rating) * SQRT(COUNT(sc.id)) as weighted_score
                    FROM staff_commendations sc
                    JOIN staff s ON s.id = sc.staff_id
                    WHERE sc.response_id IN ({placeholders})
                    GROUP BY s.id, s.first_name, s.last_name, s.position, s.role
                    ORDER BY weighted_score DESC
                    LIMIT 5
                """, all_response_ids)
                top_staff = cursor.fetchall()
            else:
                total_commendations = 0
                top_staff = []
        finally:
            conn.close()
        
        formatted_top_staff = []
        for s in top_staff:
            formatted_top_staff.append({
                "name": f"{s['first_name']} {s['last_name']}",
                "position": s["position"] or (s["role"].title() if s["role"] else "Staff"),
                "avg_rating": float(s["avg_rating"]) if s["avg_rating"] else 0.0
            })
        
        return jsonify({
            "overview": {
                "total_feedback": total_feedback,
                "resolved": resolved_count,
                "unresolved": unresolved_count,
                "resolution_rate": resolution_rate,
                "total_ratings": total_ratings
            },
            "rating_metrics": {
                "five_star_rate": five_star_rate,
                "four_plus_star_rate": four_plus_star_rate,
                "quality_score": quality_score,
                "distribution": rating_distribution,
                "distribution_pcts": rating_pcts
            },
            "trend": {
                "labels": trend_labels,
                "values": trend_values
            },
            "staff_metrics": {
                "total_commendations": total_commendations,
                "top_staff": formatted_top_staff
            }
        })

    # API endpoint for store staff data
    @app.route("/api/stores/<int:store_id>/staff", methods=["GET"])
    def api_store_staff(store_id: int):
        """API endpoint to get staff data for a store."""
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return jsonify({"error": "Store not found"}), 404
        
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Fetch staff with commendation ratings
            cursor.execute("""
                SELECT s.id, s.first_name, s.last_name, s.email, s.phone, s.position, s.role, s.status,
                       AVG(sc.rating) as avg_rating,
                       COUNT(sc.id) as commendation_count,
                       AVG(sc.rating) * SQRT(COUNT(sc.id)) as weighted_score
                FROM staff s
                LEFT JOIN staff_commendations sc ON s.id = sc.staff_id
                WHERE s.store_id = %s
                GROUP BY s.id
                ORDER BY weighted_score DESC, s.last_name, s.first_name
            """, (store_id,))
            
            staff_members = cursor.fetchall()
            
            # Format staff data
            total_commendations = sum(s["commendation_count"] or 0 for s in staff_members) if staff_members else 0
            max_commendations = max((s["commendation_count"] or 0 for s in staff_members), default=0)
            formatted_staff = []
            for staff in staff_members:
                formatted_staff.append({
                    "id": staff["id"],
                    "name": f"{staff['first_name']} {staff['last_name']}",
                    "first_name": staff["first_name"],
                    "last_name": staff["last_name"],
                    "position": staff["position"] or staff["role"].title(),
                    "email": staff.get("email", "") or "",
                    "phone": staff.get("phone", "") or "",
                    "role": staff["role"],
                    "status": staff["status"],
                    "avg_rating": float(staff["avg_rating"]) if staff["avg_rating"] else 0.0,
                    "commendation_count": staff["commendation_count"] or 0,
                    "store_id": store_id
                })
            
            return jsonify(formatted_staff)
        finally:
            conn.close()

    # -------------------------
    # PUBLIC SURVEY
    # -------------------------
    @app.route("/dashboard", methods=["GET"])
    def public_store_dashboard_subdomain():
        # Extract subdomain from request host
        host = request.host.split(':')[0]  # Remove port if present
        parts = host.split('.')
        
        # Check if accessing via subdomain
        if len(parts) >= 3:
            subdomain = parts[0]
            
            # Validate subdomain and fetch store
            conn = get_db_connection()
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT id, store_name, address, city, province, postal_code,
                           contact_number, email, store_manager_name, manager_contact,
                           store_type, status, logo_url
                    FROM stores
                    WHERE subdomain = %s
                    LIMIT 1
                    """,
                    (subdomain,)
                )
                store = cursor.fetchone()
            finally:
                conn.close()

            if store:
                # Fetch store performance data
                conn = get_db_connection()
                try:
                    cursor = conn.cursor(dictionary=True)

                    # Total feedback count
                    cursor.execute("SELECT COUNT(*) as total FROM responses WHERE store_id = %s", (store['id'],))
                    total_feedback = cursor.fetchone()['total']

                    # Average rating
                    cursor.execute("SELECT AVG(a.rating_value) as avg_rating FROM answers a JOIN responses r ON a.response_id = r.id WHERE r.store_id = %s AND a.rating_value IS NOT NULL", (store['id'],))
                    avg_rating = cursor.fetchone()['avg_rating']

                    # Total commendations
                    cursor.execute("SELECT COUNT(*) as total FROM staff_commendations sc JOIN responses r ON sc.response_id = r.id WHERE r.store_id = %s", (store['id'],))
                    total_commendations = cursor.fetchone()['total']

                    # Total staff
                    cursor.execute("SELECT COUNT(*) as total FROM staff WHERE store_id = %s", (store['id'],))
                    total_staff = cursor.fetchone()['total']

                    # Staff performance
                    cursor.execute(
                        """
                        SELECT s.id, s.first_name, s.last_name, s.position, s.role, s.status,
                               AVG(sc.rating) as avg_rating,
                               COUNT(sc.id) as commendation_count
                        FROM staff s
                        LEFT JOIN staff_commendations sc ON s.id = sc.staff_id
                        LEFT JOIN responses r ON sc.response_id = r.id
                        WHERE s.store_id = %s AND r.store_id = %s
                        GROUP BY s.id, s.first_name, s.last_name, s.position, s.role, s.status
                        ORDER BY avg_rating DESC
                        """,
                        (store['id'], store['id'])
                    )
                    staff_performance = cursor.fetchall()

                    # Recent feedback
                    cursor.execute(
                        """
                        SELECT a.rating_value as rating, a.answer_text as comment, r.created_at
                        FROM responses r
                        LEFT JOIN answers a ON r.id = a.response_id
                        WHERE r.store_id = %s
                        ORDER BY r.created_at DESC
                        LIMIT 10
                        """,
                        (store['id'],)
                    )
                    recent_feedback = cursor.fetchall()

                    # Fetch master questionnaire logo
                    cursor.execute("SELECT logo_url FROM questionnaires WHERE is_template = 1 LIMIT 1")
                    master_logo = cursor.fetchone()

                finally:
                    conn.close()

                return render_template(
                    "public/store_dashboard.html",
                    store=store,
                    master_logo=master_logo.get('logo_url') if master_logo else None,
                    total_feedback=total_feedback,
                    avg_rating=avg_rating,
                    total_commendations=total_commendations,
                    total_staff=total_staff,
                    staff_performance=staff_performance,
                    recent_feedback=recent_feedback
                )
        
        # If no subdomain match, redirect to main domain or show error
        return render_template("layout.html", error="Store not found or invalid subdomain"), 404

    @app.route("/d/<access_token>", methods=["GET"])
    def public_store_dashboard(access_token: str):
        # Validate access token and fetch store
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, store_name, address, city, province, postal_code,
                       contact_number, email, store_manager_name, manager_contact,
                       store_type, status, logo_url
                FROM stores
                WHERE access_token = %s
                LIMIT 1
                """,
                (access_token,)
            )
            store = cursor.fetchone()
        finally:
            conn.close()

        if not store:
            return render_template("layout.html", error="Invalid access token or store not found"), 404

        # Fetch store performance data
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # Total feedback count
            cursor.execute("SELECT COUNT(*) as total FROM responses WHERE store_id = %s", (store['id'],))
            total_feedback = cursor.fetchone()['total']

            # Average rating
            cursor.execute("SELECT AVG(a.rating_value) as avg_rating FROM answers a JOIN responses r ON a.response_id = r.id WHERE r.store_id = %s AND a.rating_value IS NOT NULL", (store['id'],))
            avg_rating = cursor.fetchone()['avg_rating']

            # Total commendations
            cursor.execute("SELECT COUNT(*) as total FROM staff_commendations sc JOIN responses r ON sc.response_id = r.id WHERE r.store_id = %s", (store['id'],))
            total_commendations = cursor.fetchone()['total']

            # Total staff
            cursor.execute("SELECT COUNT(*) as total FROM staff WHERE store_id = %s", (store['id'],))
            total_staff = cursor.fetchone()['total']

            # Staff performance
            cursor.execute(
                """
                SELECT s.id, s.first_name, s.last_name, s.position, s.role, s.status,
                       AVG(sc.rating) as avg_rating,
                       COUNT(sc.id) as commendation_count
                FROM staff s
                LEFT JOIN staff_commendations sc ON s.id = sc.staff_id
                LEFT JOIN responses r ON sc.response_id = r.id
                WHERE s.store_id = %s AND r.store_id = %s
                GROUP BY s.id, s.first_name, s.last_name, s.position, s.role, s.status
                ORDER BY avg_rating DESC
                """,
                (store['id'], store['id'])
            )
            staff_performance = cursor.fetchall()

            # Recent feedback
            cursor.execute(
                """
                SELECT a.rating_value as rating, a.answer_text as comment, r.created_at
                FROM responses r
                LEFT JOIN answers a ON r.id = a.response_id
                WHERE r.store_id = %s
                ORDER BY r.created_at DESC
                LIMIT 10
                """,
                (store['id'],)
            )
            recent_feedback = cursor.fetchall()

            # Fetch master questionnaire logo
            cursor.execute("SELECT logo_url FROM questionnaires WHERE is_template = 1 LIMIT 1")
            master_logo = cursor.fetchone()

        finally:
            conn.close()

        return render_template(
            "public/store_dashboard.html",
            store=store,
            master_logo=master_logo.get('logo_url') if master_logo else None,
            total_feedback=total_feedback,
            avg_rating=avg_rating,
            total_commendations=total_commendations,
            total_staff=total_staff,
            staff_performance=staff_performance,
            recent_feedback=recent_feedback
        )

    @app.route("/s/<int:store_id>", methods=["GET"])
    def public_survey(store_id: int):
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return render_template("survey_error.html", store=None, error="Page not found"), 404

        # Check if master questionnaire is active
        master_template = fetch_template_questionnaire()
        if not master_template or not master_template.get("is_active"):
            return render_template("survey_error.html", store=store, error="Sorry, the system is not accepting any feedbacks right now"), 404

        questionnaire = fetch_questionnaire_by_store(store_id=store_id)
        if not questionnaire or not questionnaire.get("is_active"):
            return render_template("survey_error.html", store=store, error="Sorry, the system is not accepting any feedbacks right now"), 404

        questions = fetch_questions_for_questionnaire(questionnaire_id=int(questionnaire["id"]))
        question_ids = [int(q["id"]) for q in questions]
        options_by_question_id = fetch_options_for_questions(question_ids=question_ids)

        # Fetch master questionnaire logo (brand logo)
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT logo_url FROM questionnaires WHERE is_template = 1 LIMIT 1")
        master_logo = cursor.fetchone()
        cursor.close()
        conn.close()

        # Fetch active staff for this store
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, first_name, last_name, position, role
            FROM staff
            WHERE store_id = %s AND status = 'active'
            ORDER BY role DESC, last_name, first_name
        """, (store_id,))
        staff_members = cursor.fetchall()
        cursor.close()
        conn.close()

        return render_template(
            "master_questionnaire/survey.html",
            store=store,
            master_logo=master_logo.get('logo_url') if master_logo else None,
            questionnaire=questionnaire,
            questions=questions,
            options_by_question_id=options_by_question_id,
            staff_members=staff_members,
        )

    @app.route("/s/<int:store_id>/submit", methods=["POST"])
    def submit_survey(store_id: int):
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return render_template("survey_error.html", store=None, error="Page not found"), 404

        # Check if master questionnaire is active
        master_template = fetch_template_questionnaire()
        if not master_template or not master_template.get("is_active"):
            return render_template("survey_error.html", store=store, error="Sorry, the system is not accepting any feedbacks right now"), 404

        questionnaire = fetch_questionnaire_by_store(store_id=store_id)
        if not questionnaire or not questionnaire.get("is_active"):
            return render_template("survey_error.html", store=store, error="Sorry, the system is not accepting any feedbacks right now"), 404

        questions = fetch_questions_for_questionnaire(questionnaire_id=int(questionnaire["id"]))
        options_by_question_id = fetch_options_for_questions([int(q["id"]) for q in questions])

        # Get and validate receipt number
        receipt_number = request.form.get("receipt_number", "").strip()
        if not receipt_number:
            flash("Receipt/Transaction number is required.", "danger")
            return redirect(url_for("public_survey", store_id=store_id))
        
        # Basic receipt number validation (5-50 characters, letters, numbers, hyphens only)
        if not re.match(r'^[A-Za-z0-9\-]{5,50}$', receipt_number):
            flash("Receipt number should be 5-50 characters (letters, numbers, and hyphens only).", "danger")
            return redirect(url_for("public_survey", store_id=store_id))

        # Get and validate email
        user_email = request.form.get("user_email", "").strip()
        if not user_email:
            flash("Email address is required.", "danger")
            return redirect(url_for("public_survey", store_id=store_id))
        
        # Basic email validation
        if "@" not in user_email or "." not in user_email.split("@")[1]:
            flash("Please enter a valid email address.", "danger")
            return redirect(url_for("public_survey", store_id=store_id))

        errors: List[str] = []
        answers_to_save: List[Dict[str, Any]] = []

        for q in questions:
            qid = int(q["id"])
            key = f"q_{qid}"
            q_type = q["question_type"]
            is_required = bool(q["is_required"])

            if q_type == "rating":
                raw = request.form.get(key, "").strip()
                if not raw:
                    if is_required:
                        errors.append(f"Rating required: {q['question_text']}")
                    continue
                try:
                    rating_value = int(raw)
                except ValueError:
                    errors.append(f"Invalid rating: {q['question_text']}")
                    continue
                if rating_value < 1 or rating_value > 5:
                    errors.append(f"Rating must be 1-5: {q['question_text']}")
                    continue
                comment = request.form.get(f"{key}_comment", "").strip()
                answers_to_save.append(
                    {"question_id": qid, "answer_text": comment if comment else None, "rating_value": rating_value}
                )

            elif q_type == "text":
                text = request.form.get(key, "")
                text = text.strip()
                if not text:
                    if is_required:
                        errors.append(f"Answer required: {q['question_text']}")
                    continue
                answers_to_save.append({"question_id": qid, "answer_text": text, "rating_value": None})

            elif q_type == "multiple_choice":
                raw = request.form.get(key, "").strip()
                if not raw:
                    if is_required:
                        errors.append(f"Choice required: {q['question_text']}")
                    continue

                try:
                    selected_option_id = int(raw)
                except ValueError:
                    errors.append(f"Invalid choice: {q['question_text']}")
                    continue

                options = options_by_question_id.get(qid, [])
                selected_text = None
                for opt in options:
                    if int(opt["id"]) == selected_option_id:
                        selected_text = opt["option_text"]
                        break

                if not selected_text:
                    errors.append(f"Invalid choice: {q['question_text']}")
                    continue

                answers_to_save.append(
                    {"question_id": qid, "answer_text": selected_text, "rating_value": None}
                )
            else:
                errors.append(f"Unsupported question type: {q_type}")

        if errors:
            for e in errors[:5]:
                flash(e, "danger")
            return redirect(url_for("public_survey", store_id=store_id))

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Use Philippine time (UTC+08:00)
            ph_tz = timezone(timedelta(hours=8))
            now_ph = datetime.now(ph_tz).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                """
                INSERT INTO responses (questionnaire_id, store_id, user_email, receipt_number, submitted_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (int(questionnaire["id"]), store_id, user_email, receipt_number, now_ph),
            )
            response_id = int(cursor.lastrowid)

            for a in answers_to_save:
                cursor.execute(
                    """
                    INSERT INTO answers (response_id, question_id, answer_text, rating_value)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (response_id, a["question_id"], a["answer_text"], a["rating_value"]),
                )

            # Handle staff commendation if provided
            staff_commendation = request.form.get("staff_commendation", "").strip()
            if staff_commendation and staff_commendation.isdigit():
                staff_id = int(staff_commendation)
                commendation_type = request.form.get("commendation_type", "excellent_service")
                commendation_comment = request.form.get("commendation_comment", "").strip()
                commendation_rating = request.form.get("commendation_rating", "5").strip()
                if not commendation_rating or not commendation_rating.isdigit():
                    commendation_rating = 5
                commendation_rating = int(commendation_rating)
                
                # Verify staff exists and belongs to this store
                cursor.execute("SELECT id FROM staff WHERE id = %s AND store_id = %s", (staff_id, store_id))
                if cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO staff_commendations (response_id, staff_id, rating, commendation_type, comment)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (response_id, staff_id, commendation_rating, commendation_type, commendation_comment if commendation_comment else None))

            conn.commit()
        finally:
            conn.close()

        return redirect(url_for("survey_thank_you", store_id=store_id))

    @app.route("/s/<int:store_id>/thanks", methods=["GET"])
    def survey_thank_you(store_id: int):
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            return render_template("layout.html", store=None, error="Page not found"), 404
        return render_template("master_questionnaire/thank_you.html", store=store)

    @app.route("/admin/stores/add", methods=["POST"])
    def add_store():
        store_name = request.form.get("store_name", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        province = request.form.get("province", "").strip()
        postal_code = request.form.get("postal_code", "").strip()
        contact_number = request.form.get("contact_number", "").strip()
        email = request.form.get("email", "").strip()
        store_manager_name = request.form.get("store_manager_name", "").strip()
        manager_contact = request.form.get("manager_contact", "").strip()
        store_type = request.form.get("store_type", "").strip()
        status = request.form.get("status", "active")
        subdomain = request.form.get("subdomain", "").strip()

        if not store_name:
            flash("Store name is required.", "danger")
            return redirect(url_for("stores_management"))

        # Basic email validation if provided
        if email and ("@" not in email or "." not in email.split("@")[1]):
            flash("Please enter a valid email address.", "danger")
            return redirect(url_for("stores_management"))

        # Handle logo upload
        logo_url = None
        if 'logo' in request.files:
            logo_file = request.files['logo']
            if logo_file and logo_file.filename:
                # Validate file type
                allowed_extensions = {'png', 'jpg', 'jpeg'}
                if logo_file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                    flash("Invalid file type. Only PNG, JPG, and JPEG files are allowed.", "danger")
                    return redirect(url_for("stores_management"))
                
                # Validate file size (5MB max)
                logo_file.seek(0, os.SEEK_END)
                file_size = logo_file.tell()
                logo_file.seek(0)
                if file_size > 5 * 1024 * 1024:
                    flash("File size exceeds 5MB limit.", "danger")
                    return redirect(url_for("stores_management"))
                
                # Save the file
                from werkzeug.utils import secure_filename
                import uuid
                filename = secure_filename(logo_file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                upload_path = os.path.join('static', 'uploads', 'logos')
                os.makedirs(upload_path, exist_ok=True)
                logo_file.save(os.path.join(upload_path, unique_filename))
                logo_url = f"/static/uploads/logos/{unique_filename}"

        new_store_id = create_store(
            store_name=store_name,
            address=address if address else None,
            city=city if city else None,
            province=province if province else None,
            postal_code=postal_code if postal_code else None,
            contact_number=contact_number if contact_number else None,
            email=email if email else None,
            store_manager_name=store_manager_name if store_manager_name else None,
            manager_contact=manager_contact if manager_contact else None,
            store_type=store_type if store_type else None,
            status=status,
            logo_url=logo_url,
            subdomain=subdomain if subdomain else None
        )
        
        # Log the store addition
        log_audit(
            entity_type="store",
            entity_id=new_store_id,
            action="created",
            new_values=f"Store Name: {store_name}, Address: {address}, City: {city}, Status: {status}"
        )
        
        flash(f"Store \"{store_name}\" added Successfully", "success")
        return redirect(url_for("stores_management"))

    def update_store(
        store_id: int,
        store_name: str,
        store_type: str | None,
        address: str | None,
        city: str | None,
        province: str | None,
        postal_code: str | None,
        contact_number: str | None,
        email: str | None,
        store_manager_name: str | None,
        manager_contact: str | None,
        status: str,
        logo_url: str | None = None,
        subdomain: str | None = None
    ) -> bool:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE stores
                SET store_name = %s, store_type = %s, address = %s, city = %s,
                    province = %s, postal_code = %s, contact_number = %s,
                    email = %s, store_manager_name = %s, manager_contact = %s,
                    status = %s, logo_url = %s, subdomain = %s
                WHERE id = %s
                """,
                (
                    store_name,
                    store_type,
                    address,
                    city,
                    province,
                    postal_code,
                    contact_number,
                    email,
                    store_manager_name,
                    manager_contact,
                    status,
                    logo_url,
                    subdomain,
                    store_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    @app.route("/admin/stores/<int:store_id>/upload-logo", methods=["POST"])
    def upload_store_logo(store_id: int):
        # Handle logo upload only
        logo_url = None
        if 'logo' in request.files:
            logo_file = request.files['logo']
            if logo_file and logo_file.filename:
                # Validate file type
                allowed_extensions = {'png', 'jpg', 'jpeg'}
                if logo_file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                    flash("Invalid file type. Only PNG, JPG, and JPEG files are allowed.", "danger")
                    return redirect(url_for("store_details", store_id=store_id))
                
                # Validate file size (5MB max)
                logo_file.seek(0, os.SEEK_END)
                file_size = logo_file.tell()
                logo_file.seek(0)
                if file_size > 5 * 1024 * 1024:
                    flash("File size exceeds 5MB limit.", "danger")
                    return redirect(url_for("store_details", store_id=store_id))
                
                # Save the file
                from werkzeug.utils import secure_filename
                import uuid
                filename = secure_filename(logo_file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                upload_path = os.path.join('static', 'uploads', 'logos')
                os.makedirs(upload_path, exist_ok=True)
                logo_file.save(os.path.join(upload_path, unique_filename))
                logo_url = f"/static/uploads/logos/{unique_filename}"

        # Update only the logo_url in the database
        if logo_url:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE stores SET logo_url = %s WHERE id = %s", (logo_url, store_id))
                conn.commit()
                flash("Logo uploaded successfully", "success")
            except Exception as e:
                logger.error(f"Error uploading logo: {e}")
                flash(f"Error uploading logo: {e}", "danger")
            finally:
                conn.close()
        else:
            flash("No file selected", "warning")

        return redirect(url_for("store_details", store_id=store_id))

    @app.route("/admin/stores/<int:store_id>/edit", methods=["POST"])
    def edit_store(store_id: int):
        store_name = request.form.get("store_name", "").strip()
        store_type = request.form.get("store_type", "").strip() or None
        address = request.form.get("address", "").strip() or None
        city = request.form.get("city", "").strip() or None
        province = request.form.get("province", "").strip() or None
        postal_code = request.form.get("postal_code", "").strip() or None
        contact_number = request.form.get("contact_number", "").strip() or None
        email = request.form.get("email", "").strip() or None
        store_manager_name = request.form.get("store_manager_name", "").strip() or None
        manager_contact = request.form.get("manager_contact", "").strip() or None
        status = request.form.get("status", "active")
        subdomain = request.form.get("subdomain", "").strip()

        if not store_name:
            flash("Store name is required.", "danger")
            return redirect(url_for("stores_management"))

        # Handle logo upload
        logo_url = None
        if 'logo' in request.files:
            logo_file = request.files['logo']
            if logo_file and logo_file.filename:
                # Validate file type
                allowed_extensions = {'png', 'jpg', 'jpeg'}
                if logo_file.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                    flash("Invalid file type. Only PNG, JPG, and JPEG files are allowed.", "danger")
                    return redirect(url_for("stores_management"))
                
                # Validate file size (5MB max)
                logo_file.seek(0, os.SEEK_END)
                file_size = logo_file.tell()
                logo_file.seek(0)
                if file_size > 5 * 1024 * 1024:
                    flash("File size exceeds 5MB limit.", "danger")
                    return redirect(url_for("stores_management"))
                
                # Save the file
                from werkzeug.utils import secure_filename
                import uuid
                filename = secure_filename(logo_file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                upload_path = os.path.join('static', 'uploads', 'logos')
                os.makedirs(upload_path, exist_ok=True)
                logo_file.save(os.path.join(upload_path, unique_filename))
                logo_url = f"/static/uploads/logos/{unique_filename}"

        success = update_store(
            store_id=store_id,
            store_name=store_name,
            store_type=store_type,
            address=address,
            city=city,
            province=province,
            postal_code=postal_code,
            contact_number=contact_number,
            email=email,
            store_manager_name=store_manager_name,
            manager_contact=manager_contact,
            status=status,
            logo_url=logo_url,
            subdomain=subdomain if subdomain else None
        )

        if success:
            # Log the store edit
            log_audit(
                entity_type="store",
                entity_id=store_id,
                action="updated",
                new_values=f"Store Name: {store_name}, Address: {address}, City: {city}, Status: {status}"
            )
            flash(f"Store \"{store_name}\" Edited", "success")
        else:
            flash("Store not found or update failed.", "danger")

        return redirect(url_for("stores_management", store_id=store_id))

    @app.route("/admin/stores/<int:store_id>/delete", methods=["POST"])
    def delete_store_route(store_id: int):
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Fetch store name before deletion for the notification
            cursor.execute("SELECT store_name FROM stores WHERE id = %s", (store_id,))
            store_row = cursor.fetchone()
            store_name = store_row[0] if store_row else "Unknown"

            # Cascading delete: delete staff_commendations first
            cursor.execute("""
                DELETE sc FROM staff_commendations sc
                JOIN responses r ON sc.response_id = r.id
                WHERE r.store_id = %s
            """, (store_id,))

            # Delete answers
            cursor.execute("""
                DELETE a FROM answers a
                JOIN responses r ON a.response_id = r.id
                WHERE r.store_id = %s
            """, (store_id,))
            
            # Delete responses
            cursor.execute("DELETE FROM responses WHERE store_id = %s", (store_id,))

            # Delete staff
            cursor.execute("DELETE FROM staff WHERE store_id = %s", (store_id,))
            
            # Delete question options for store's questionnaires
            cursor.execute("""
                DELETE qo FROM question_options qo
                JOIN questions q ON qo.question_id = q.id
                JOIN questionnaires qn ON q.questionnaire_id = qn.id
                WHERE qn.store_id = %s
            """, (store_id,))
            
            # Delete questions
            cursor.execute("""
                DELETE q FROM questions q
                JOIN questionnaires qn ON q.questionnaire_id = qn.id
                WHERE qn.store_id = %s
            """, (store_id,))
            
            # Delete questionnaires
            cursor.execute("DELETE FROM questionnaires WHERE store_id = %s", (store_id,))
            
            # Delete store itself
            cursor.execute("DELETE FROM stores WHERE id = %s", (store_id,))
            
            conn.commit()
            
            # Log the store deletion
            log_audit(
                entity_type="store",
                entity_id=store_id,
                action="deleted",
                old_values=f"Store Name: {store_name}"
            )
            
            flash(f"Store \"{store_name}\" Deleted", "success")
        except Exception as e:
            logger.error(f"Error deleting store: {e}")
            flash(f"Error deleting store: {e}", "danger")
        finally:
            conn.close()
            
        return redirect(url_for("stores_management"))

    @app.route("/admin/history")
    def history():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT id, entity_type, entity_id, action, old_values, new_values, user_id, created_at
                FROM audit_logs
                ORDER BY created_at DESC
                LIMIT 100
            """)
            logs = cursor.fetchall()
        finally:
            conn.close()
        
        return render_template("history.html", logs=logs)

    @app.route("/admin/clear-feedback", methods=["POST"])
    def clear_feedback_route():
        """Clear all feedback data while preserving stores and their configuration."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()

            # Count before deletion for feedback
            cursor.execute("SELECT COUNT(*) FROM responses")
            responses_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM answers")
            answers_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM staff_commendations")
            commendations_count = cursor.fetchone()[0]

            # Delete in correct order (respecting foreign keys)
            # 1. Delete staff_commendations
            cursor.execute("DELETE FROM staff_commendations")

            # 2. Delete answers
            cursor.execute("DELETE FROM answers")

            # 3. Delete responses
            cursor.execute("DELETE FROM responses")

            conn.commit()

            flash(f"Cleared {responses_count} responses, {answers_count} answers, and {commendations_count} commendations. Stores and configurations preserved.", "success")
            logger.info(f"Cleared feedback data: {responses_count} responses, {answers_count} answers, {commendations_count} commendations")
        except Exception as e:
            logger.error(f"Error clearing feedback data: {e}")
            flash(f"Error clearing feedback data: {e}", "danger")
        finally:
            conn.close()

        return redirect(url_for("stores_management"))

    @app.route("/admin/backup/csv", methods=["GET"])
    def backup_csv_route():
        """Export all data to CSV for backup purposes."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # Fetch all data
            cursor.execute("SELECT * FROM stores")
            stores = cursor.fetchall()

            cursor.execute("SELECT * FROM responses")
            responses = cursor.fetchall()

            cursor.execute("SELECT * FROM answers")
            answers = cursor.fetchall()

            cursor.execute("SELECT * FROM staff")
            staff = cursor.fetchall()

            cursor.execute("SELECT * FROM staff_commendations")
            commendations = cursor.fetchall()

            # Create in-memory CSV
            output = io.StringIO()
            writer = csv.writer(output)

            # Write stores
            writer.writerow(["--- STORES ---"])
            if stores:
                writer.writerow(stores[0].keys())
                for row in stores:
                    writer.writerow(row.values())
            writer.writerow([])

            # Write responses
            writer.writerow(["--- RESPONSES ---"])
            if responses:
                writer.writerow(responses[0].keys())
                for row in responses:
                    writer.writerow(row.values())
            writer.writerow([])

            # Write answers
            writer.writerow(["--- ANSWERS ---"])
            if answers:
                writer.writerow(answers[0].keys())
                for row in answers:
                    writer.writerow(row.values())
            writer.writerow([])

            # Write staff
            writer.writerow(["--- STAFF ---"])
            if staff:
                writer.writerow(staff[0].keys())
                for row in staff:
                    writer.writerow(row.values())
            writer.writerow([])

            # Write commendations
            writer.writerow(["--- STAFF COMMENDATIONS ---"])
            if commendations:
                writer.writerow(commendations[0].keys())
                for row in commendations:
                    writer.writerow(row.values())

            output.seek(0)
            csv_data = output.getvalue()

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"feedback_system_backup_{timestamp}.csv"

            # Send as downloadable file
            return send_file(
                io.BytesIO(csv_data.encode('utf-8')),
                mimetype='text/csv',
                as_attachment=True,
                download_name=filename
            )
        except Exception as e:
            logger.error(f"Error creating CSV backup: {e}")
            flash(f"Error creating backup: {e}", "danger")
            return redirect(url_for("stores_management"))
        finally:
            conn.close()

    @app.route("/admin/seed-feedback", methods=["POST"])
    def seed_feedback_route():
        """Seed sample feedback data for each store with answers and staff commendations."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # Fetch all stores
            cursor.execute("SELECT * FROM stores")
            stores = cursor.fetchall()

            # Fetch template questionnaire
            cursor.execute("SELECT * FROM questionnaires WHERE is_template = TRUE LIMIT 1")
            template_questionnaire = cursor.fetchone()

            if not template_questionnaire:
                flash("No template questionnaire found. Please create one first.", "danger")
                return redirect(url_for("stores_management"))

            # Fetch questions from template questionnaire
            cursor.execute("SELECT * FROM questions WHERE questionnaire_id = %s", (template_questionnaire["id"],))
            template_questions = cursor.fetchall()

            total_responses = 0
            total_answers = 0
            total_commendations = 0

            # Sample data for feedback
            sample_emails = [
                "customer1@example.com", "customer2@example.com", "customer3@example.com",
                "customer4@example.com", "customer5@example.com", "customer6@example.com",
                "customer7@example.com", "customer8@example.com", "customer9@example.com",
                "customer10@example.com"
            ]

            sample_receipts = [
                "REC-001", "REC-002", "REC-003", "REC-004", "REC-005",
                "REC-006", "REC-007", "REC-008", "REC-009", "REC-010"
            ]

            sample_answers_text = [
                "Great service!", "Very satisfied", "Excellent experience",
                "Good quality", "Friendly staff", "Quick service",
                "Clean environment", "Helpful team", "Professional",
                "Will return again"
            ]

            for store in stores:
                store_id = int(store["id"])

                # Fetch or create store-specific questionnaire
                cursor.execute("SELECT * FROM questionnaires WHERE store_id = %s", (store_id,))
                store_questionnaire = cursor.fetchone()

                if not store_questionnaire:
                    # Create a new questionnaire for this store from template
                    cursor.execute(
                        """
                        INSERT INTO questionnaires (title, store_id, is_active, is_template)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (template_questionnaire["title"], store_id, True, False),
                    )
                    store_questionnaire_id = int(cursor.lastrowid)

                    # Copy questions from template to store questionnaire
                    for template_q in template_questions:
                        cursor.execute(
                            """
                            INSERT INTO questions (questionnaire_id, question_text, question_type, is_required, min_label, max_label, allow_comment, question_order)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (store_questionnaire_id, template_q["question_text"], template_q["question_type"],
                             template_q["is_required"], template_q["min_label"], template_q["max_label"],
                             template_q["allow_comment"], template_q["question_order"]),
                        )
                        new_question_id = int(cursor.lastrowid)

                        # Copy options if it's a multiple choice question
                        if template_q["question_type"] == "multiple_choice":
                            cursor.execute("SELECT * FROM question_options WHERE question_id = %s", (template_q["id"],))
                            options = cursor.fetchall()
                            for opt in options:
                                cursor.execute(
                                    """
                                    INSERT INTO question_options (question_id, option_text)
                                    VALUES (%s, %s)
                                    """,
                                    (new_question_id, opt["option_text"]),
                                )
                else:
                    store_questionnaire_id = int(store_questionnaire["id"])

                # Fetch questions for this store's questionnaire
                cursor.execute("SELECT * FROM questions WHERE questionnaire_id = %s", (store_questionnaire_id,))
                questions = cursor.fetchall()

                # Fetch staff for this store
                cursor.execute("SELECT * FROM staff WHERE store_id = %s", (store_id,))
                staff_list = cursor.fetchall()

                # Determine number of feedbacks for this store (5-15)
                num_feedbacks = random.randint(5, 15)

                for i in range(num_feedbacks):
                    # Use Philippine time
                    ph_tz = timezone(timedelta(hours=8))
                    now_ph = datetime.now(ph_tz).strftime("%Y-%m-%d %H:%M:%S")

                    # Create response
                    cursor.execute(
                        """
                        INSERT INTO responses (questionnaire_id, store_id, user_email, receipt_number, submitted_at, status)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (store_questionnaire_id, store_id, sample_emails[i % len(sample_emails)],
                         sample_receipts[i % len(sample_receipts)], now_ph, random.choice(['resolved', 'unresolved'])),
                    )
                    response_id = int(cursor.lastrowid)
                    total_responses += 1

                    # Add answers for each question
                    for question in questions:
                        question_type = question["question_type"]

                        if question_type == "rating":
                            # Random rating 1-5
                            rating = str(random.randint(1, 5))
                            cursor.execute(
                                """
                                INSERT INTO answers (response_id, question_id, rating_value)
                                VALUES (%s, %s, %s)
                                """,
                                (response_id, question["id"], rating),
                            )
                            total_answers += 1
                        elif question_type == "text":
                            # Random text answer
                            answer_text = sample_answers_text[random.randint(0, len(sample_answers_text) - 1)]
                            cursor.execute(
                                """
                                INSERT INTO answers (response_id, question_id, answer_text)
                                VALUES (%s, %s, %s)
                                """,
                                (response_id, question["id"], answer_text),
                            )
                            total_answers += 1
                        elif question_type == "multiple_choice":
                            # Fetch options for this question
                            cursor.execute("SELECT * FROM question_options WHERE question_id = %s", (question["id"],))
                            options = cursor.fetchall()
                            if options:
                                selected_option = random.choice(options)
                                cursor.execute(
                                    """
                                    INSERT INTO answers (response_id, question_id, answer_text)
                                    VALUES (%s, %s, %s)
                                    """,
                                    (response_id, question["id"], selected_option["option_text"]),
                                )
                                total_answers += 1

                    # Add staff commendations (if staff exists and rating was good)
                    if staff_list and random.random() > 0.5:  # 50% chance
                        num_commendations = random.randint(1, min(3, len(staff_list)))
                        commended_staff = random.sample(staff_list, num_commendations)
                        for staff in commended_staff:
                            cursor.execute(
                                """
                                INSERT INTO staff_commendations (response_id, staff_id)
                                VALUES (%s, %s)
                                """,
                                (response_id, staff["id"]),
                            )
                            total_commendations += 1

            conn.commit()
            flash(f"Seeded {total_responses} feedback responses, {total_answers} answers, and {total_commendations} staff commendations across {len(stores)} stores.", "success")
            logger.info(f"Seeded feedback data: {total_responses} responses, {total_answers} answers, {total_commendations} commendations")
        except Exception as e:
            logger.error(f"Error seeding feedback data: {e}")
            flash(f"Error seeding feedback data: {e}", "danger")
        finally:
            conn.close()

        return redirect(url_for("stores_management"))

    # -------------------------
    # STAFF MANAGEMENT
    # -------------------------

    @app.route("/admin/stores/<int:store_id>/staff")
    def staff_management(store_id: int):
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Get store information
            cursor.execute("SELECT * FROM stores WHERE id = %s", (store_id,))
            store = cursor.fetchone()
            
            if not store:
                flash("Store not found", "danger")
                return redirect(url_for("stores_management"))
            
            # Get staff for this store
            cursor.execute("""
                SELECT * FROM staff 
                WHERE store_id = %s 
                ORDER BY role DESC, last_name, first_name
            """, (store_id,))
            staff = cursor.fetchall()
            
            # Generate QR code for the store
            public_url = get_store_public_url(store_id=store_id)
            qr_data_uri = generate_qr_data_uri(public_url)
            
            return render_template("manage_staff/staff.html", store=store, staff=staff, public_url=public_url, qr_data_uri=qr_data_uri)
        except Exception as e:
            logger.error(f"Error loading staff management: {e}")
            flash(f"Error loading staff: {e}", "danger")
            return redirect(url_for("stores_management"))
        finally:
            conn.close()

    @app.route("/admin/stores/<int:store_id>/staff/add", methods=["POST"])
    def add_staff(store_id: int):
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        position = request.form.get("position", "").strip() or None
        role = request.form.get("role", "staff")
        hire_date = request.form.get("hire_date", "").strip() or None
        
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            
            # Verify store exists
            cursor.execute("SELECT id FROM stores WHERE id = %s", (store_id,))
            if not cursor.fetchone():
                flash("Store not found", "danger")
                return redirect(url_for("stores_management"))
            
            # Insert new staff member
            cursor.execute("""
                INSERT INTO staff (store_id, first_name, last_name, email, phone, position, role, hire_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (store_id, first_name, last_name, email, phone, position, role, hire_date))
            
            new_staff_id = cursor.lastrowid
            conn.commit()
            
            # Log the staff addition
            log_audit(
                entity_type="staff",
                entity_id=new_staff_id,
                action="created",
                new_values=f"Name: {first_name} {last_name}, Position: {position}, Role: {role}, Store ID: {store_id}"
            )
            
            flash(f"Staff member \"{first_name} {last_name}\" added successfully", "success")
        except Exception as e:
            logger.error(f"Error adding staff: {e}")
            flash(f"Error adding staff: {e}", "danger")
        finally:
            conn.close()
            
        return redirect(url_for("store_feedback", store_id=store_id, tab='staff'))

    @app.route("/admin/stores/<int:store_id>/staff/<int:staff_id>/edit", methods=["POST"])
    def edit_staff(store_id: int, staff_id: int):
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        position = request.form.get("position", "").strip() or None
        role = request.form.get("role", "staff")
        status = request.form.get("status", "active")
        hire_date = request.form.get("hire_date", "").strip() or None
        
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            
            # Update staff member
            cursor.execute("""
                UPDATE staff 
                SET first_name = %s, last_name = %s, email = %s, phone = %s, 
                    position = %s, role = %s, status = %s, hire_date = %s
                WHERE id = %s AND store_id = %s
            """, (first_name, last_name, email, phone, position, role, status, hire_date, staff_id, store_id))
            
            if cursor.rowcount == 0:
                flash("Staff member not found", "danger")
            else:
                conn.commit()
                
                # Log the staff edit
                log_audit(
                    entity_type="staff",
                    entity_id=staff_id,
                    action="updated",
                    new_values=f"Name: {first_name} {last_name}, Position: {position}, Role: {role}, Status: {status}, Store ID: {store_id}"
                )
                
                flash(f"Staff member \"{first_name} {last_name}\" updated successfully", "success")
        except Exception as e:
            logger.error(f"Error updating staff: {e}")
            flash(f"Error updating staff: {e}", "danger")
        finally:
            conn.close()
            
        return redirect(url_for("store_feedback", store_id=store_id, tab='staff'))

    @app.route("/admin/stores/<int:store_id>/staff/<int:staff_id>/delete", methods=["POST"])
    def delete_staff(store_id: int, staff_id: int):
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            
            # Get staff member name for flash message
            cursor.execute("SELECT first_name, last_name FROM staff WHERE id = %s AND store_id = %s", (staff_id, store_id))
            staff = cursor.fetchone()
            
            if not staff:
                flash("Staff member not found", "danger")
                return redirect(url_for("store_feedback", store_id=store_id, tab='staff'))
            
            # Delete staff member
            cursor.execute("DELETE FROM staff WHERE id = %s AND store_id = %s", (staff_id, store_id))
            conn.commit()
            
            # Log the staff deletion
            log_audit(
                entity_type="staff",
                entity_id=staff_id,
                action="deleted",
                old_values=f"Name: {staff[0]} {staff[1]}, Store ID: {store_id}"
            )
            
            flash(f"Staff member \"{staff[0]} {staff[1]}\" deleted successfully", "success")
        except Exception as e:
            logger.error(f"Error deleting staff: {e}")
            flash(f"Error deleting staff: {e}", "danger")
        finally:
            conn.close()
            
        return redirect(url_for("store_feedback", store_id=store_id, tab='staff'))

    @app.route("/admin/responses/<int:response_id>/delete", methods=["POST"])
    def delete_response_route(response_id: int):
        store_id = request.args.get("store_id")
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Delete answers first
            cursor.execute("DELETE FROM answers WHERE response_id = %s", (response_id,))
            # Delete response
            cursor.execute("DELETE FROM responses WHERE id = %s", (response_id,))
            conn.commit()
            flash("Feedback Deleted", "success")
        except Exception as e:
            logger.error(f"Error deleting response: {e}")
            flash(f"Error deleting response: {e}", "danger")
        finally:
            conn.close()
            
        if store_id:
            return redirect(url_for("store_feedback", store_id=store_id))
        return redirect(url_for("admin_dashboard"))

    # -------------------------
    # QUESTION ORDER MANAGEMENT
    # -------------------------
    @app.route("/admin/questions/<int:question_id>/order", methods=["POST"])
    def update_question_order(question_id: int):
        if request.method == "POST":
            try:
                data = request.get_json()
                new_order = int(data.get("question_order", 0))
                
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        UPDATE questions 
                        SET question_order = %s 
                        WHERE id = %s AND is_template = TRUE
                        """,
                        (new_order, question_id),
                    )
                    conn.commit()
                    return {"success": True, "message": "Question order updated"}
                finally:
                    conn.close()
                    
            except Exception as e:
                return {"success": False, "error": str(e)}, 400
                
        return {"success": False, "error": "Method not allowed"}, 405

    # -------------------------
    # FEEDBACK VIEWER (ADMIN)
    # -------------------------
    def fetch_responses_for_store(store_id: int, limit: int = 50, status: str = None) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            if status == "unresolved":
                cursor.execute(
                    """
                    SELECT id, questionnaire_id, store_id, user_email, receipt_number, submitted_at, status
                    FROM responses
                    WHERE store_id = %s AND status = 'unresolved'
                    ORDER BY submitted_at DESC, id DESC
                    LIMIT %s
                    """,
                    (store_id, limit),
                )
            elif status == "resolved":
                cursor.execute(
                    """
                    SELECT id, questionnaire_id, store_id, user_email, receipt_number, submitted_at, status
                    FROM responses
                    WHERE store_id = %s AND status = 'resolved'
                    ORDER BY submitted_at DESC, id DESC
                    LIMIT %s
                    """,
                    (store_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, questionnaire_id, store_id, user_email, receipt_number, submitted_at, status
                    FROM responses
                    WHERE store_id = %s
                    ORDER BY submitted_at DESC, id DESC
                    LIMIT %s
                    """,
                    (store_id, limit),
                )
            rows = cursor.fetchall()
        finally:
            conn.close()
        return rows

    def fetch_answers_for_responses(response_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        if not response_ids:
            return {}
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            placeholders = ", ".join(["%s"] * len(response_ids))
            cursor.execute(
                f"""
                SELECT a.response_id, a.question_id, a.answer_text, a.rating_value, q.question_text, q.question_type
                FROM answers a
                JOIN questions q ON q.id = a.question_id
                WHERE a.response_id IN ({placeholders})
                ORDER BY a.response_id ASC, q.question_order ASC, a.id ASC
                """,
                tuple(response_ids),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        by_response: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            rid = int(r["response_id"])
            by_response.setdefault(rid, []).append(r)
        return by_response

    @app.route("/admin/stores/<int:store_id>/details", methods=["GET"])
    def store_details(store_id: int):
        store = fetch_store_by_id(store_id=store_id)
        if not store:
            flash("Store not found.", "danger")
            return redirect(url_for("admin_dashboard"))

        # Fetch recent feedback
        recent_feedback = fetch_responses_for_store(store_id=store_id, limit=5)
        
        # Calculate analytics data
        all_feedback = fetch_responses_for_store(store_id=store_id, limit=1000)
        total_feedback = len(all_feedback)
        
        # Calculate average rating
        avg_rating = 0
        if all_feedback:
            all_response_ids = [int(r["id"]) for r in all_feedback]
            answers_by_response_id = fetch_answers_for_responses(all_response_ids)
            all_ratings = []
            for response_id, answers in answers_by_response_id.items():
                for answer in answers:
                    if answer.get("rating_value"):
                        all_ratings.append(float(answer["rating_value"]))
            if all_ratings:
                avg_rating = sum(all_ratings) / len(all_ratings)
        
        # Enhanced 5-star rating analytics
        rating_distribution = [0, 0, 0, 0, 0]  # 1-5 stars
        total_ratings = 0
        for response_id, answers in answers_by_response_id.items():
            for answer in answers:
                if answer.get("rating_value"):
                    rating = int(float(answer["rating_value"]))
                    if 1 <= rating <= 5:
                        rating_distribution[rating - 1] += 1
                        total_ratings += 1
        
        # Calculate 5-star specific metrics
        five_star_count = rating_distribution[4]  # 5 stars
        four_star_count = rating_distribution[3]  # 4 stars
        three_star_count = rating_distribution[2]  # 3 stars
        two_star_count = rating_distribution[1]   # 2 stars
        one_star_count = rating_distribution[0]    # 1 star
        
        # Calculate percentages
        five_star_percentage = (five_star_count / total_ratings * 100) if total_ratings > 0 else 0
        four_plus_star_percentage = ((four_star_count + five_star_count) / total_ratings * 100) if total_ratings > 0 else 0
        three_plus_star_percentage = ((three_star_count + four_star_count + five_star_count) / total_ratings * 100) if total_ratings > 0 else 0
        
        # Rating quality score (weighted average)
        rating_quality_score = (
            (one_star_count * 1) +
            (two_star_count * 2) +
            (three_star_count * 3) +
            (four_star_count * 4) +
            (five_star_count * 5)
        ) / total_ratings if total_ratings > 0 else 0
        
        # Fetch staff members
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, first_name, last_name, email, phone, position, role, status
            FROM staff 
            WHERE store_id = %s
            ORDER BY role DESC, last_name, first_name
        """, (store_id,))
        staff_members = cursor.fetchall()
        cursor.close()
        conn.close()
        
        total_staff = len(staff_members)
        
        # Fetch commendations
        commendations_by_response_id = {}
        if all_feedback:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            response_ids = [int(r["id"]) for r in all_feedback]
            placeholders = ','.join(['%s'] * len(response_ids))
            cursor.execute(f"""
                SELECT sc.*, s.first_name, s.last_name, s.position, s.role
                FROM staff_commendations sc
                JOIN staff s ON sc.staff_id = s.id
                WHERE sc.response_id IN ({placeholders})
                ORDER BY sc.created_at DESC
            """, response_ids)
            commendations = cursor.fetchall()
            cursor.close()
            conn.close()
            
            for commendation in commendations:
                response_id = commendation['response_id']
                if response_id not in commendations_by_response_id:
                    commendations_by_response_id[response_id] = []
                commendations_by_response_id[response_id].append(commendation)
        
        total_commendations = sum(len(comms) for comms in commendations_by_response_id.values())
        
        # Staff analytics - commendations per staff
        staff_commendations = {}
        if commendations:
            for commendation in commendations:
                staff_id = commendation['staff_id']
                if staff_id not in staff_commendations:
                    staff_commendations[staff_id] = {
                        'staff_name': f"{commendation['first_name']} {commendation['last_name']}",
                        'staff_position': commendation['position'] or commendation['role'].title(),
                        'total_commendations': 0,
                        'rating_sum': 0,
                        'commendation_types': {},
                        'comments': []
                    }
                
                staff_commendations[staff_id]['total_commendations'] += 1
                staff_commendations[staff_id]['rating_sum'] += (commendation['rating'] or 5)
                
                # Count by type
                c_type = commendation['commendation_type']
                if c_type not in staff_commendations[staff_id]['commendation_types']:
                    staff_commendations[staff_id]['commendation_types'][c_type] = 0
                staff_commendations[staff_id]['commendation_types'][c_type] += 1
                
                # Collect comments
                if commendation['comment']:
                    staff_commendations[staff_id]['comments'].append(commendation['comment'])
        
        # Calculate avg_rating and weighted_score for each staff, sort by weighted_score
        for staff_id in staff_commendations:
            if staff_commendations[staff_id]['total_commendations'] > 0:
                staff_commendations[staff_id]['avg_rating'] = staff_commendations[staff_id]['rating_sum'] / staff_commendations[staff_id]['total_commendations']
                staff_commendations[staff_id]['weighted_score'] = staff_commendations[staff_id]['avg_rating'] * (staff_commendations[staff_id]['total_commendations'] ** 0.5)
            else:
                staff_commendations[staff_id]['avg_rating'] = 0
                staff_commendations[staff_id]['weighted_score'] = 0
        top_staff = sorted(staff_commendations.values(), key=lambda x: x['weighted_score'], reverse=True)
        
        # Identify staff with potential issues (low or no commendations)
        staff_performance = []
        for staff_member in staff_members:
            staff_id = staff_member['id']
            staff_name = f"{staff_member['first_name']} {staff_member['last_name']}"
            staff_position = staff_member['position'] or staff_member['role'].title()
            
            commendation_count = staff_commendations.get(staff_id, {}).get('total_commendations', 0)
            
            # Calculate performance score based on commendations
            performance_score = 'excellent'
            if commendation_count == 0:
                performance_score = 'needs_attention'
            elif commendation_count < 3:
                performance_score = 'average'
            
            staff_performance.append({
                'staff_id': staff_id,
                'staff_name': staff_name,
                'staff_position': staff_position,
                'commendation_count': commendation_count,
                'performance_score': performance_score,
                'role': staff_member['role']
            })
        
        # Sort by performance (those needing attention first)
        staff_performance.sort(key=lambda x: (x['performance_score'] != 'needs_attention', x['commendation_count']))
        
        # Calculate metrics (mock data for now)
        resolution_rate = 85 if total_feedback > 0 else 0
        response_time = 2.5
        commendation_rate = round((total_commendations / total_feedback * 100) if total_feedback > 0 else 0)
        repeat_rate = 42
        
        # Feedback trend data (mock data for now)
        feedback_trend_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun']
        feedback_trend_data = [12, 19, 15, 25, 22, 30]
        
        # Generate QR code for the store
        public_url = get_store_public_url(store_id=store['id'])
        qr_data_uri = generate_qr_data_uri(public_url)
        
        return render_template(
            "manage_stores/store_details.html",
            store=store,
            recent_feedback=recent_feedback,
            total_feedback=total_feedback,
            avg_rating=avg_rating,
            rating_distribution=rating_distribution,
            staff_members=staff_members,
            total_staff=total_staff,
            total_commendations=total_commendations,
            resolution_rate=resolution_rate,
            response_time=response_time,
            commendation_rate=commendation_rate,
            repeat_rate=repeat_rate,
            feedback_trend_labels=feedback_trend_labels,
            feedback_trend_data=feedback_trend_data,
            public_url=public_url,
            qr_data_uri=qr_data_uri,
            top_staff=top_staff,
            staff_performance=staff_performance,
            staff_commendations=staff_commendations,
            # Enhanced 5-star rating analytics
            total_ratings=total_ratings,
            five_star_count=five_star_count,
            five_star_percentage=five_star_percentage,
            four_plus_star_percentage=four_plus_star_percentage,
            three_plus_star_percentage=three_plus_star_percentage,
            rating_quality_score=rating_quality_score,
        )

    @app.route("/admin/stores/<int:store_id>/feedback", methods=["GET"])
    def store_feedback(store_id: int):
        # Handle marking a specific notification as read if requested
        mark_read_id = request.args.get('mark_read')
        if mark_read_id:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE responses SET is_read = TRUE WHERE id = %s", (int(mark_read_id),))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Error marking specific notification as read: {e}")

        store = fetch_store_by_id(store_id=store_id)
        if not store:
            flash("Store not found.", "danger")
            return redirect(url_for("admin_dashboard"))

        status = request.args.get('status', 'all')
        responses = fetch_responses_for_store(store_id=store_id, limit=50, status=status)
        answers_by_response_id = fetch_answers_for_responses([int(r["id"]) for r in responses])
        
        # Fetch staff commendations for these responses
        commendations_by_response_id = {}
        if responses:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            response_ids = [int(r["id"]) for r in responses]
            placeholders = ','.join(['%s'] * len(response_ids))
            cursor.execute(f"""
                SELECT sc.*, s.first_name, s.last_name, s.position, s.role
                FROM staff_commendations sc
                JOIN staff s ON sc.staff_id = s.id
                WHERE sc.response_id IN ({placeholders})
                ORDER BY sc.created_at DESC
            """, response_ids)
            commendations = cursor.fetchall()
            cursor.close()
            conn.close()
            
            for commendation in commendations:
                response_id = commendation['response_id']
                if response_id not in commendations_by_response_id:
                    commendations_by_response_id[response_id] = []
                commendations_by_response_id[response_id].append(commendation)

        # Calculate staff count and average rating
        staff_count = get_staff_count_for_store(store_id)
        staff_performance = get_staff_performance_for_store(store_id)
        
        # Calculate average rating from responses
        avg_ratings = []
        for response in responses:
            response_answers = answers_by_response_id.get(response["id"], [])
            rating_answers = [a for a in response_answers if a.get("question_type") == "rating"]
            if rating_answers:
                avg_rating = sum(a.get("rating_value", 0) for a in rating_answers) / len(rating_answers)
                avg_ratings.append(avg_rating)
        
        average_rating = round(sum(avg_ratings) / len(avg_ratings), 1) if avg_ratings else 0.0

        return render_template(
            "manage_stores/feedback.html",
            store=store,
            responses=responses,
            answers_by_response_id=answers_by_response_id,
            commendations_by_response_id=commendations_by_response_id,
            current_status=status,
            staff_count=staff_count,
            staff_performance=staff_performance,
            average_rating=average_rating,
        )

    @app.route("/admin/responses/<int:response_id>/status", methods=["POST"])
    def update_response_status(response_id: int):
        try:
            data = request.get_json()
            new_status = data.get('status')
            
            if new_status not in ['resolved', 'unresolved']:
                return {"success": False, "error": "Invalid status"}, 400
            
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE responses SET status = %s WHERE id = %s",
                    (new_status, response_id)
                )
                conn.commit()
                flash(f"Feedback marked as {new_status}", "success")
                return {"success": True}
            finally:
                conn.close()
                
        except Exception as e:
            return {"success": False, "error": str(e)}, 500

    @app.route("/api/notifications/unread")
    def get_unread_notifications():
        """Fetch feedback notifications for the bell icon, combining feedback and system notifications."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Fetch latest feedback responses (last 10, regardless of read status)
            cursor.execute(
                """
                SELECT r.id, r.user_email, r.submitted_at as created_at, s.store_name, s.id as store_id, r.is_read, 'feedback' as notification_type, NULL as message, NULL as type
                FROM responses r
                JOIN stores s ON r.store_id = s.id
                WHERE s.store_name IS NOT NULL
                ORDER BY r.submitted_at DESC
                LIMIT 10
                """
            )
            feedback_notifications = cursor.fetchall()

            # Fetch latest system notifications (last 10, regardless of read status)
            cursor.execute(
                """
                SELECT id, message, type, created_at, is_read, 'system' as notification_type, NULL as user_email, NULL as store_name, NULL as store_id
                FROM system_notifications
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
            system_notifications = cursor.fetchall()

            # Combine and sort all notifications by created_at
            all_notifications = sorted(
                feedback_notifications + system_notifications,
                key=lambda x: x['created_at'],
                reverse=True
            )[:10] # Take top 10 after sorting

            # Count total unread from both types
            cursor.execute("SELECT COUNT(*) as count FROM responses WHERE is_read = FALSE")
            unread_feedback_count = cursor.fetchone()['count']
            cursor.execute("SELECT COUNT(*) as count FROM system_notifications WHERE is_read = FALSE")
            unread_system_count = cursor.fetchone()['count']
            total_unread = unread_feedback_count + unread_system_count
            
            # Format dates for JSON and add a unique 'id' for system notifications
            for n in all_notifications:
                if n['created_at']:
                    n['created_at'] = n['created_at'].strftime('%b %d, %H:%M')
                else:
                    n['created_at'] = 'N/A'
                
                # Ensure system notifications have a distinct ID for frontend handling
                if n['notification_type'] == 'system':
                    n['system_id'] = n['id'] # Use a different key to avoid conflict with response.id
                    n['id'] = None # Clear original ID to prevent confusion

            return jsonify({
                "success": True,
                "notifications": all_notifications,
                "total_unread": total_unread
            })
        except Exception as e:
            logger.error(f"Error fetching notifications: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            conn.close()

    @app.route("/admin/responses/<int:response_id>/reply", methods=["POST"])
    def reply_to_feedback(response_id: int):
        try:
            data = request.get_json()
            reply_message = data.get('message', '').strip()
            template_type = data.get('template_type', 'standard')
            cc_emails = data.get('cc_emails', [])
            bcc_emails = data.get('bcc_emails', [])
            
            if not reply_message:
                return {"success": False, "error": "Reply message cannot be empty"}, 400
            
            if template_type not in ['standard', 'apology', 'appreciation', 'follow_up']:
                template_type = 'standard'
            
            conn = get_db_connection()
            try:
                cursor = conn.cursor(dictionary=True)
                
                # Get response details including customer email and store info
                cursor.execute("""
                    SELECT r.user_email, r.submitted_at, s.store_name,
                           (SELECT GROUP_CONCAT(a.answer_text SEPARATOR ' ') 
                            FROM answers a 
                            WHERE a.response_id = r.id 
                            AND a.answer_text IS NOT NULL 
                            LIMIT 3) as feedback_summary,
                           (SELECT AVG(a.rating_value) 
                            FROM answers a 
                            WHERE a.response_id = r.id 
                            AND a.rating_value IS NOT NULL) as avg_rating
                    FROM responses r
                    JOIN stores s ON r.store_id = s.id
                    WHERE r.id = %s
                """, (response_id,))
                
                response = cursor.fetchone()
                
                if not response:
                    return {"success": False, "error": "Response not found"}, 404
                
                if not response['user_email']:
                    return {"success": False, "error": "No email address found for this feedback"}, 400
                
                # Extract customer name from email
                customer_name = response['user_email'].split('@')[0].replace('.', ' ').title()
                
                # Auto-select template based on rating if not specified
                if template_type == 'standard' and response['avg_rating'] is not None:
                    try:
                        avg_rating = float(response['avg_rating'])
                        if avg_rating <= 2:
                            template_type = 'apology'
                        elif avg_rating >= 4:
                            template_type = 'appreciation'
                        else:
                            template_type = 'follow_up'
                    except (ValueError, TypeError):
                        template_type = 'standard'
                
                # Send email using API or SMTP
                try:
                    success, message = email_config.send_feedback_reply(
                        to_email=response['user_email'],
                        customer_name=customer_name,
                        reply_message=reply_message,
                        store_name=response['store_name'],
                        feedback_summary=response['feedback_summary'],
                        template_type=template_type
                    )
                    if success:
                        return {"success": True, "message": "Reply sent successfully", "template_used": template_type}
                    else:
                        return {"success": False, "error": message}, 500
                except Exception as e:
                    print(f"Email sending failed: {str(e)}")
                    return {"success": False, "error": str(e)}, 500
                    
            finally:
                conn.close()
                
        except Exception as e:
            return {"success": False, "error": str(e)}, 500
    
    @app.route("/admin/email/statistics", methods=["GET"])
    def email_statistics():
        """Get email sending statistics"""
        try:
            stats = email_config.get_email_statistics()
            return jsonify(stats)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/system_notifications/<int:notification_id>/read", methods=["POST"])
    def mark_system_notification_read(notification_id: int):
        """Mark a single system notification as read."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE system_notifications SET is_read = TRUE WHERE id = %s", (notification_id,))
            conn.commit()
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error marking system notification as read: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            conn.close()
    
    @app.route("/admin/email/bulk-reply", methods=["POST"])
    def bulk_reply_to_feedback():
        """Send bulk email replies to multiple feedback responses"""
        try:
            data = request.get_json()
            response_ids = data.get('response_ids', [])
            reply_message = data.get('message', '').strip()
            template_type = data.get('template_type', 'standard')
            
            if not response_ids:
                return {"success": False, "error": "No response IDs provided"}, 400
            
            if not reply_message:
                return {"success": False, "error": "Reply message cannot be empty"}, 400
            
            if template_type not in ['standard', 'apology', 'appreciation', 'follow_up']:
                template_type = 'standard'
            
            conn = get_db_connection()
            try:
                cursor = conn.cursor(dictionary=True)
                
                # Get all response details
                placeholders = ", ".join(["%s"] * len(response_ids))
                cursor.execute(f"""
                    SELECT r.id, r.user_email, s.store_name,
                           (SELECT GROUP_CONCAT(a.answer_text SEPARATOR ' ') 
                            FROM answers a 
                            WHERE a.response_id = r.id 
                            AND a.answer_text IS NOT NULL 
                            LIMIT 3) as feedback_summary
                    FROM responses r
                    JOIN stores s ON r.store_id = s.id
                    WHERE r.id IN ({placeholders}) AND r.user_email IS NOT NULL
                """, tuple(response_ids))
                
                responses = cursor.fetchall()
                
                if not responses:
                    return {"success": False, "error": "No valid responses found"}, 404
                
                # Prepare data for bulk email
                email_list = [r['user_email'] for r in responses]
                customer_names = [r['user_email'].split('@')[0].replace('.', ' ').title() for r in responses]
                feedback_summaries = [r['feedback_summary'] or "No text feedback provided" for r in responses]
                store_name = responses[0]['store_name']  # Use first store name (assuming same store)
                
                # Send bulk emails
                results = email_config.send_bulk_feedback_reply(
                    email_list=email_list,
                    customer_names=customer_names,
                    reply_message=reply_message,
                    store_name=store_name,
                    feedback_summaries=feedback_summaries,
                    template_type=template_type
                )
                
                # Mark responses as resolved
                successful_emails = [r['email'] for r in results if r['success']]
                if successful_emails:
                    placeholders = ", ".join(["%s"] * len(successful_emails))
                    cursor.execute(f"""
                        UPDATE responses 
                        SET status = 'resolved' 
                        WHERE user_email IN ({placeholders})
                    """, tuple(successful_emails))
                    conn.commit()
                
                return {
                    "success": True,
                    "message": f"Bulk reply completed. {len(successful_emails)} of {len(results)} emails sent successfully.",
                    "results": results
                }
                
            finally:
                conn.close()
                
        except Exception as e:
            return {"success": False, "error": str(e)}, 500

    return app


if __name__ == "__main__":
    app = create_app()
    # Railway provides the port via the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    # This is used by gunicorn (web: gunicorn "app:create_app()")
    app = create_app()
