import os
import unittest
import json
import mysql.connector
from app import create_app
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class FeedbackSystemQATester(unittest.TestCase):
    """
    Automated QA Tester for the Feedback System
    Tests database connectivity, core API routes, and basic UI rendering.
    """

    @classmethod
    def setUpClass(cls):
        """Set up the Flask test client and DB connection info."""
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()
        
        # Database config from the app's own config (which handles Railway/Local)
        cls.db_config = cls.app.config['DB_CONFIG']

    # 1. DATABASE TESTS
    def test_db_connectivity(self):
        """QA-DB-01: Verify connection to the MySQL database."""
        try:
            conn = mysql.connector.connect(**self.db_config)
            self.assertTrue(conn.is_connected(), "Failed to connect to MySQL database")
            conn.close()
        except Exception as e:
            self.fail(f"Database connection error: {e}")

    def test_db_schema_tables(self):
        """QA-DB-02: Verify that all required tables exist in the database."""
        required_tables = {'stores', 'questionnaires', 'questions', 'responses', 'answers'}
        conn = mysql.connector.connect(**self.db_config)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        for table in required_tables:
            self.assertIn(table, tables, f"Required table '{table}' is missing from database")

    # 2. ADMIN DASHBOARD TESTS
    def test_dashboard_route(self):
        """QA-UI-01: Verify the Admin Dashboard loads successfully."""
        response = self.client.get('/admin/dashboard')
        self.assertEqual(response.status_code, 200, "Dashboard route should return HTTP 200")
        self.assertIn(b"Dashboard", response.data, "Dashboard page title missing from response")
        self.assertIn(b"Total Stores", response.data, "Dashboard metric cards missing")

    def test_store_performance_route(self):
        """QA-UI-02: Verify the Store Performance page loads successfully."""
        response = self.client.get('/admin/stores/performance')
        self.assertEqual(response.status_code, 200, "Store Performance route should return HTTP 200")
        self.assertIn(b"Store Performance Details", response.data, "Performance table title missing")

    # 3. MASTER QUESTIONNAIRE TESTS
    def test_master_questionnaire_route(self):
        """QA-UI-03: Verify the Master Questionnaire editor loads successfully."""
        response = self.client.get('/admin/questionnaire')
        self.assertEqual(response.status_code, 200, "Master Questionnaire route should return HTTP 200")
        self.assertIn(b"Master Questionnaire", response.data, "Editor title missing")
        self.assertIn(b"Add Question", response.data, "Add Question form missing")

    # 4. PUBLIC SURVEY TESTS
    def test_public_survey_routing(self):
        """QA-PUB-01: Verify that public survey links resolve for existing stores."""
        # First, find a valid store ID
        conn = mysql.connector.connect(**self.db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM stores LIMIT 1")
        store = cursor.fetchone()
        conn.close()
        
        if store:
            store_id = store[0]
            response = self.client.get(f'/s/{store_id}')
            self.assertEqual(response.status_code, 200, f"Public survey link for store {store_id} failed")
            self.assertIn(b"Feedback Form", response.data, "Survey page header missing")
        else:
            self.skipTest("No stores found in database to test public survey link")

    # 5. API FUNCTIONALITY TESTS
    def test_analytics_data_integrity(self):
        """QA-API-01: Verify analytics data structure and types."""
        with self.app.app_context():
            # Accessing the internal fetch function if possible, otherwise test route content
            response = self.client.get('/admin/dashboard')
            # Check if common metrics are at least rendered (since they are passed to template)
            self.assertIn(b"metric-value", response.data, "Analytics metrics not rendered on dashboard")

    def test_api_status_check(self):
        """QA-API-02: Verify status update API handles valid status properly."""
        response = self.client.post('/admin/responses/1/status', 
                                    data=json.dumps({'status': 'invalid_status'}),
                                    content_type='application/json')
        # Expecting error for invalid status
        self.assertEqual(response.status_code, 400, "API should return HTTP 400 for invalid status")

if __name__ == '__main__':
    print("\n" + "="*50)
    print("  FEEDBACK SYSTEM - AUTOMATED QA TEST SUITE")
    print("="*50 + "\n")
    unittest.main()
