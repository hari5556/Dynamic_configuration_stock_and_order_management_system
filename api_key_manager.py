# api_key_manager.py
import secrets
import string
import hashlib
from cryptography.fernet import Fernet
import os
from database_manager import db_manager
from functools import wraps
from flask import request, jsonify, session

# Encryption setup
def get_master_key():
    """Get master encryption key from environment"""
    master_key = os.getenv('MASTER_ENCRYPTION_KEY')
    if not master_key:
        # Generate new key (do this once)
        master_key = Fernet.generate_key().decode()
        print(f"⚠️  NEW MASTER KEY: {master_key}")
        print("⚠️  Save this in your .env as MASTER_ENCRYPTION_KEY")
    return master_key.encode()

def encrypt_data(data):
    """Encrypt sensitive data"""
    if not data:
        return None
    fernet = Fernet(get_master_key())
    return fernet.encrypt(data.encode())

def decrypt_data(encrypted_data):
    """Decrypt data"""
    if not encrypted_data:
        return None
    fernet = Fernet(get_master_key())
    try:
        return fernet.decrypt(encrypted_data).decode()
    except Exception as e:
        print(f"Decryption error: {e}")
        return None

def hash_api_key(api_key):
    """Create hash for API key verification"""
    return hashlib.sha256(api_key.encode()).hexdigest()

def generate_api_key():
    """Generate secure API key WITHOUT escape characters"""
    # Define safe characters only - exclude backslash, quotes, etc.
    safe_punctuation = '!@#$%^&*()_+-=[]{}|;:,.<>?~'
    alphabet = string.ascii_letters + string.digits + safe_punctuation
    return 'cust_live_' + ''.join(secrets.choice(alphabet) for _ in range(32))

# Database table for customer management
def create_customer_management_tables():
    """Create tables in your EXISTING database for customer management"""
    conn = db_manager.get_main_connection()
    if not conn:
        return False
        
    cursor = conn.cursor()
    
    try:
        # Customer API keys table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_api_keys (
                customer_id INT AUTO_INCREMENT PRIMARY KEY,
                customer_name VARCHAR(255) NOT NULL,
                customer_email VARCHAR(255) UNIQUE NOT NULL,
                encrypted_api_key BLOB NOT NULL,
                original_key_hash VARCHAR(255) NOT NULL,
                database_name VARCHAR(100) NOT NULL UNIQUE,
                product_type VARCHAR(100) DEFAULT 'standard',
                server_ip VARCHAR(255),
                assigned_port INT UNIQUE NOT NULL,
                access_url VARCHAR(500),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_port (assigned_port),
                INDEX idx_server_ip (server_ip)
            )
        """)
        
        conn.commit()
        print("✓ Customer management tables created successfully!")
    
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plain_api_keys_backup (
                id INT AUTO_INCREMENT PRIMARY KEY,
                customer_id INT,
                plain_api_key VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        print("Plain_api_key_Backup Tables created Successfully!")
        return True
        
    except Exception as e:
        print(f"Error creating customer tables: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

def customer_api_key_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # ALLOW EXISTING SESSION ACCESS (no changes to your current flow)
        if session.get('admin_logged_in') or session.get('user_logged_in'):
            # Your existing sessions work exactly as before
            return f(*args, **kwargs)
        
        # NEW: Check for API key (only for external customers without sessions)
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        
        if api_key:
            # Validate API key for external customers
            customer_info = validate_customer_api_key(api_key)
            if customer_info:
                # Store customer info for database routing
                request.customer_db = customer_info['database_name']
                request.customer_id = customer_info['customer_id']
                return f(*args, **kwargs)
            else:
                return jsonify({'success': False, 'message': 'Invalid API key'}), 401
        else:
            # No session and no API key - use existing behavior
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
    
    return decorated_function

def validate_customer_api_key(api_key):
    """Validate customer API key"""
    conn = db_manager.get_main_connection()
    if not conn:
        return None
        
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get active customers
        cursor.execute("""
            SELECT customer_id, encrypted_api_key, original_key_hash, database_name 
            FROM customer_api_keys 
            WHERE is_active = TRUE
        """)
        customers = cursor.fetchall()
        
        # Check by comparing hashes
        provided_hash = hash_api_key(api_key)
        
        for customer in customers:
            if secrets.compare_digest(provided_hash, customer['original_key_hash']):
                # Double-check by decrypting
                decrypted_key = decrypt_data(customer['encrypted_api_key'])
                if decrypted_key and secrets.compare_digest(api_key, decrypted_key):
                    return customer
                    
        return None
        
    except Exception as e:
        print(f"API validation error: {e}")
        return None
    finally:
        cursor.close()
        conn.close()
