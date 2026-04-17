#!/usr/bin/env python3

import app
import sys

def check_and_clear_notifications():
    # Access the Flask app and get the connection function
    flask_app = app.create_app()
    with flask_app.app_context():
        conn = app.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        try:
        # Check unread feedback responses
        cursor.execute('SELECT COUNT(*) as count FROM responses WHERE is_read = FALSE')
        feedback_count = cursor.fetchone()['count']
        print(f'Unread feedback responses: {feedback_count}')
        
        # Check unread system notifications  
        cursor.execute('SELECT COUNT(*) as count FROM system_notifications WHERE is_read = FALSE')
        system_count = cursor.fetchone()['count']
        print(f'Unread system notifications: {system_count}')
        
        total_unread = feedback_count + system_count
        print(f'Total unread notifications: {total_unread}')
        
        # Show actual notifications
        cursor.execute('SELECT id, message, type, created_at, is_read FROM system_notifications ORDER BY created_at DESC LIMIT 10')
        system_notifs = cursor.fetchall()
        print('\nSystem notifications:')
        for n in system_notifs:
            print(f'ID: {n["id"]}, Type: {n["type"]}, Read: {n["is_read"]}, Message: {n["message"][:50]}...')
        
        cursor.execute('SELECT r.id, r.user_email, r.submitted_at, s.store_name, r.is_read FROM responses r JOIN stores s ON r.store_id = s.id WHERE r.is_read = FALSE ORDER BY r.submitted_at DESC LIMIT 10')
        feedback_notifs = cursor.fetchall()
        print('\nUnread feedback responses:')
        for n in feedback_notifs:
            print(f'ID: {n["id"]}, Email: {n["user_email"]}, Store: {n["store_name"]}, Date: {n["submitted_at"]}')
        
        # Clear all notifications if user confirms
        if total_unread > 0:
            print(f'\nFound {total_unread} unread notifications. Clearing them...')
            
            # Mark all feedback responses as read
            cursor.execute('UPDATE responses SET is_read = TRUE WHERE is_read = FALSE')
            feedback_updated = cursor.rowcount
            
            # Mark all system notifications as read
            cursor.execute('UPDATE system_notifications SET is_read = TRUE WHERE is_read = FALSE')
            system_updated = cursor.rowcount
            
            conn.commit()
            print(f'Marked {feedback_updated} feedback responses and {system_updated} system notifications as read.')
        else:
            print('No unread notifications found.')
            
    except Exception as e:
        print(f'Error: {e}')
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    check_and_clear_notifications()
