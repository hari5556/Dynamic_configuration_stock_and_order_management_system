# database_manager.py
import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv

load_dotenv()

class DatabaseManager:
    def __init__(self):
        # Use YOUR existing database configuration
        self.main_db_config = {
            'host': os.getenv('DB_HOST'),
            'database': os.getenv('DB_NAME'),  # Your existing database
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'auth_plugin': 'mysql_native_password'
        }
        
        # Template for customer databases (same credentials, different DB name)
        self.customer_db_config = {
            'host': os.getenv('DB_HOST'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'auth_plugin': 'mysql_native_password'
        }

    def get_main_connection(self):
        """Connect to your EXISTING main database - NO CHANGES"""
        try:
            connection = mysql.connector.connect(**self.main_db_config)
            return connection
        except Error as e:
            print(f"Error connecting to main database: {e}")
            return None

    def get_customer_connection(self, database_name):
        """Connect to specific customer database"""
        try:
            if not database_name:
                return self.get_main_connection()
                
            config = self.customer_db_config.copy()
            config['database'] = database_name
            connection = mysql.connector.connect(**config)
            return connection
        except Error as e:
            print(f"Error connecting to customer database {database_name}: {e}")
            return None

    def create_customer_database(self, db_name):
        """Create a new database for a customer"""
        try:
            conn = mysql.connector.connect(**self.customer_db_config)
            cursor = conn.cursor()
            
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            print(f"✓ Created customer database: {db_name}")
            
            # Initialize with basic tables (similar to your existing ones)
            self.initialize_customer_database(db_name)
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
            
        except Error as e:
            print(f"Error creating customer database: {e}")
            return False

    def initialize_customer_database(self, db_name):
        """Create basic tables in customer database (similar to your existing ones)"""
        conn = self.get_customer_connection(db_name)
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        try:
            # Create configurations table (like your existing one)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS configurations (
                    Recid INT,
                    ConfigName VARCHAR(255),
                    SystemName VARCHAR(255),
                    DisplayName VARCHAR(255),
                    Active TINYINT DEFAULT 1,
                    Descr TEXT,
                    SerialNo INT
                )
            """)
            
            # Create tmp_products table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tmp_products (
                    Recid int, 
                    SystemName varchar(255), 
                    DataType varchar(255), 
                    Descr varchar(255)
                )
            """)
            
            # Create customers table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_name VARCHAR(100) NOT NULL,
                    address1 VARCHAR(255) NOT NULL,
                    address2 VARCHAR(255),
                    address3 VARCHAR(255),
                    location VARCHAR(100) NOT NULL,
                    city VARCHAR(50) NOT NULL,
                    state VARCHAR(50) NOT NULL,
                    country VARCHAR(50) DEFAULT 'India',
                    pincode VARCHAR(6) NOT NULL,
                    mobile1 VARCHAR(10) NOT NULL,
                    mobile2 VARCHAR(10),
                    ph1 VARCHAR(12),
                    ph2 VARCHAR(12),
                    emailid1 VARCHAR(255) NOT NULL,
                    emailid2 VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create orders table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_id INT NOT NULL,
                    barcode VARCHAR(255) NOT NULL,
                    quantity INT NOT NULL,
                    value DECIMAL(10,2) NOT NULL,
                    price_method VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status ENUM('pending','confirmed','shipped','delivered','cancelled') DEFAULT 'pending',
                    placed_by_user_id INT NULL,
                    placed_by_admin_id INT NULL
                )
            """)
            
            cursor.execute("""
                CREATE TABLE admin_users (
                    admin_id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_name VARCHAR(100) NOT NULL,
                    admin_email VARCHAR(255) UNIQUE NOT NULL,
                    admin_password VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)
            
            cursor.execute("""
                CREATE TABLE users (
                    user_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_name VARCHAR(100) NOT NULL,
                    user_email VARCHAR(255) UNIQUE NOT NULL,
                    user_password VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_mfa (
                    user_id INT,
                    user_type ENUM('admin', 'user') NOT NULL,
                    secret_key VARCHAR(255),
                    is_mfa_enabled BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, user_type)  -- ✅ Composite key
                )
            """)
        
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mfa_sessions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    user_type ENUM('admin', 'user') NOT NULL,
                    session_token VARCHAR(100) UNIQUE NOT NULL,
                    verification_code VARCHAR(10),
                    expires_at TIMESTAMP NOT NULL,
                    is_used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_type (user_id, user_type)
                )
            """)
            print(f"✓ Initialized customer database: {db_name}")
            return True
            
        except Error as e:
            print(f"Error initializing customer database: {e}")
            return False
        finally:
            cursor.close()
            conn.close()

# Global instance
db_manager = DatabaseManager()