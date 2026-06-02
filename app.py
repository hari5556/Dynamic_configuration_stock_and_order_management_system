from flask import Flask, request, jsonify, render_template,abort, session, redirect
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
import os
from database_manager import db_manager
from api_key_manager import (
    customer_api_key_required, 
    create_customer_management_tables
)
from dotenv import load_dotenv
import json
import openpyxl
import re
import uuid
import tempfile
from datetime import datetime, timedelta  
import secrets
from functools import wraps
from api_key_manager import validate_customer_api_key
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import secrets
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import urllib.parse
import mimetypes
from flask import send_file
import io
from PIL import Image, ImageDraw
import traceback

class SimpleMFA:
    def generate_otp(self):
        """Generate 6-digit OTP"""
        return ''.join([str(secrets.randbelow(10)) for _ in range(6)])
    
    def send_email_otp(self, email, otp_code, user_name):
        """Send OTP via email"""
        try:
            # Email configuration - ADD THESE TO YOUR .env FILE
            smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
            smtp_port = int(os.getenv('SMTP_PORT', 587))
            smtp_username = os.getenv('SMTP_USERNAME', '')
            smtp_password = os.getenv('SMTP_PASSWORD', '')
            
            # If no email config, just log the OTP (for testing)
            if not smtp_username or not smtp_password:
                return True
            
            message = MIMEText(f"""
            Hello {user_name},
            
            Your verification code is: {otp_code}
            
            This code will expire in 10 minutes.
            
            If you didn't request this code, please ignore this email.
            """)
            
            message['Subject'] = 'Your Verification Code - Order Management System'
            message['From'] = smtp_username
            message['To'] = email
            
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(message)
            
            return True
        except Exception as e:
            return True
    
    def create_mfa_session(self, user_id, user_type):
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Generate OTP and session
            otp_code = self.generate_otp()
            session_token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(minutes=10)
            
            # Store session
            cursor.execute("""
                INSERT INTO mfa_sessions 
                (user_id, user_type, session_token, verification_code, expires_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (user_id, user_type, session_token, otp_code, expires_at))
            
            conn.commit()
            return {'success': True, 'session_token': session_token, 'otp_code': otp_code}
            
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            cursor.close()
            conn.close()
    
    def verify_mfa_code(self, session_token, entered_code):
        """Verify MFA code"""
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        try:
            cursor.execute("""
                SELECT * FROM mfa_sessions 
                WHERE session_token = %s AND expires_at > NOW() AND is_used = FALSE
            """, (session_token,))
            
            session = cursor.fetchone()
            
            if not session:
                return {'success': False, 'message': 'Invalid or expired session'}
            
            if session['verification_code'] == entered_code:
                # Mark as used
                cursor.execute("UPDATE mfa_sessions SET is_used = TRUE WHERE session_token = %s", 
                             (session_token,))
                conn.commit()
                
                return {
                    'success': True, 
                    'user_id': session['user_id'],
                    'user_type': session['user_type']
                }
            else:
                return {'success': False, 'message': 'Invalid verification code'}
                
        except Exception as e:
            conn.rollback()
            return {'success': False, 'message': str(e)}
        finally:
            cursor.close()
            conn.close()

simple_mfa = SimpleMFA()

load_dotenv()

app = Flask(__name__)
CORS(app)

db_config = {
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'auth_plugin': 'mysql_native_password'
}

API_KEY = os.getenv('API_KEY')

app.secret_key = secrets.token_hex(32)  #

limiter = Limiter(
    get_remote_address,  
    app=app,
    default_limits=["200 per day", "50 per hour"],  
    storage_uri="memory://",  
)

from flask import send_from_directory
import os

def get_db_connection():
    """Get database connection - automatically switches based on IP/port or API key"""
    try:
        if session.get('software_customer_database'):
            software_db = session['software_customer_database']
            return db_manager.get_customer_connection(software_db)

        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if api_key:
            customer_info = validate_customer_api_key(api_key)
            if customer_info:
                return db_manager.get_customer_connection(customer_info['database_name'])
                
    except RuntimeError:
        pass  

    try:
        connection = mysql.connector.connect(**db_config)
        return connection
    except Error as e:
        return None
    
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return jsonify({'success': False, 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

def user_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in') and not session.get('user_logged_in'):
            return jsonify({'success': False, 'message': 'Login required'}), 403
        return f(*args, **kwargs)
    return decorated_function

def create_admin_table():
    """Create admin table if it doesn't exist"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SHOW TABLES LIKE 'admin_users'")
        if not cursor.fetchone():
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
            conn.commit()
        else:
            print()
            
    except Error as e:
        print(f"Error creating admin table: {str(e)}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

def create_users_table():

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SHOW TABLES LIKE 'users'")
        if not cursor.fetchone():
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
            conn.commit()
        else:
            print("User table already exists!")
            
    except Error as e:
        print(f"Error creating admin table: {str(e)}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# Creating a TmpProducts Table for the configurations storing table okay
def create_table():
    """Creating a New Table TmpProducts"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'tmp_products'")
        if not cursor.fetchone():
            cursor.execute("create table tmp_products(Recid int, SystemName varchar(255), DataType varchar(255), Descr varchar(255))")
            conn.commit()
            print("Table Created successfully !")
        else:
            print("Table already Existed !")
        
    except Error as e:
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# Creating a Configuration Table if it is not present 
def create_configurations_table():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'configurations'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE configurations (
                    Recid INT,
                    ConfigName VARCHAR(255),
                    SystemName VARCHAR(255),
                    DisplayName VARCHAR(255),
                    Active TINYINT DEFAULT 1,
                    Descr TEXT,
                    SerialNo INT
                )
            """)
            conn.commit()
            print("Configurations table created successfully!")
        else:
            pass
            
    except Error as e:
        print(f"Error creating configurations table: {str(e)}")
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        

# This code is for inserting the values from the configurations to the tmp_products
def inserting_values():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()             

        cursor.execute("select count(*) from tmp_products")
        already_inserted_count = cursor.fetchone()[0]

        # CHANGED: Image now uses LONGBLOB instead of varchar(255)
        config_mappings={
            "Barcode" : "varchar(255)",
            "ItemGroup" : "varchar(255)",
            "ItemMaster" : "varchar(255)",
            "ItemPrice" : "decimal",
            "WareHouse" : "varchar(255)",
            "WHLocation" : "varchar(255)",
            "HSNCode" : "varchar(255)",
            "Quantity" : "decimal",
            "ItemCalc" : "decimal",
            "Image": "LONGBLOB" 
        }
        
        query_parts = []
        for config_name,datatype in config_mappings.items():
            query_parts.append(f"select Recid, SystemName, '{datatype}', concat(SystemName, ' {datatype}') from configurations where ConfigName = '{config_name}'")
        query = f"INSERT INTO tmp_products (recid, systemname, datatype, descr) {' UNION ALL '.join(query_parts)}"   
        
        cursor.execute(query)
        
        cursor.execute("delete from tmp_products limit %s", (already_inserted_count,))
        conn.commit()
        
        cursor.execute("select count(*) from tmp_products")
        rows_present = cursor.fetchone()[0]
        return rows_present

    except Error as e:
        print(f"Error : {str(e)}")
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# Creating a Column Name by cleaning the header name present in the excel header
# for the table temp_products (copy all the data exactly as a varchar datatype Okay..)
def clean_column_name(header):
    if header is None:
        return "unknown_column"
    
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', str(header))
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = cleaned.strip('_')
    
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"col_{cleaned}"    
    return cleaned.lower()


# Creating a temp_products Table in the Database ( New_Products_Automation )
def create_temp_varchar_table(headers, table_name='temp_products'):
    """Create temporary table for Excel import with proper data types"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get image column configurations
        cursor.execute("""
            SELECT DisplayName, SystemName 
            FROM configurations 
            WHERE ConfigName = 'Image' AND Active = 1
        """)
        image_configs = cursor.fetchall()
        
        # Create mapping
        image_display_names = {}
        for config in image_configs:
            image_display_names[config['DisplayName'].lower()] = True
        
        column_defs = []
        for header in headers:
            sql_header = clean_column_name(header)
            
            # Check if this is an image column
            if header.lower() in image_display_names:
                column_defs.append(f"`{sql_header}` LONGBLOB")
            else:
                column_defs.append(f"`{sql_header}` VARCHAR(255)")
        
        create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                {', '.join(column_defs)}
            )
        """
        
        cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        cursor.execute(create_table_sql)
        conn.commit()
        return True
        
    except Exception as err:
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

# Reading all the excel Data with the Help of Openpyxl and insert into the temp_products table present in the Database
# Database : New_Products_Automation
def import_excel_data(file_path, table_name='temp_products'):
    """Load Excel data into database table with BLOB image support"""
    try:
        workbook = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        sheet = workbook.active
        
        # Get headers from first row
        headers = []
        for cell in sheet[1]:
            if cell.value:
                headers.append(str(cell.value).strip())
        
        if not headers:
            return {'success': False, 'message': 'No headers found in Excel file'}
        
        # Get image column mappings from database
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get all image display names from configurations
        cursor.execute("""
            SELECT DisplayName, SystemName 
            FROM configurations 
            WHERE ConfigName = 'Image' AND Active = 1
        """)
        image_configs = cursor.fetchall()
        
        # Create mapping of display names (case-insensitive)
        image_display_names = {}
        for config in image_configs:
            image_display_names[config['DisplayName'].lower()] = config['SystemName']
        
        # Identify which headers are image columns
        image_headers = []
        regular_headers = []
        
        for header in headers:
            if header.lower() in image_display_names:
                image_headers.append(header)
            else:
                regular_headers.append(header)
        
        # Create temporary table with regular columns (varchar)
        column_defs = []
        for header in regular_headers:
            sql_header = clean_column_name(header)
            column_defs.append(f"`{sql_header}` VARCHAR(255)")
        
        # Add image columns as LONGBLOB
        for header in image_headers:
            sql_header = clean_column_name(header)
            column_defs.append(f"`{sql_header}` LONGBLOB")
        
        # Create the table
        create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                {', '.join(column_defs)}
            )
        """
        
        try:
            cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
            cursor.execute(create_table_sql)
            conn.commit()
        except Exception as err:
            print(f"Error creating temporary table: {err}")
            conn.rollback()
            return {'success': False, 'message': f'Failed to create table: {err}'}
        
        # Get SQL-safe column names for all columns
        sql_headers = [clean_column_name(header) for header in headers]
        
        # Build INSERT query
        columns = ', '.join([f"`{h}`" for h in sql_headers])
        placeholders = ', '.join(['%s'] * len(sql_headers))
        insert_sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
        
        # Find image column indices for processing
        image_indices = {}
        for idx, header in enumerate(headers):
            if header in image_headers:
                image_indices[idx] = header
        
        # Insert data row by row
        row_count = 0
        batch_size = 100
        batch_values = []
        
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if any(cell is not None for cell in row):
                processed_row = []
                
                for idx, cell in enumerate(row):
                    if idx in image_indices and cell:
                        # This is an image column - process as BLOB
                        try:
                            image_path = str(cell).strip()
                            if os.path.exists(image_path):
                                with open(image_path, 'rb') as img_file:
                                    image_binary = img_file.read()
                                    processed_row.append(image_binary)
                            else:
                                print(f"Warning: Image file not found: {image_path}")
                                processed_row.append(None)
                        except Exception as img_err:
                            print(f"Error reading image {image_path}: {img_err}")
                            processed_row.append(None)
                    else:
                        # Regular column
                        if cell is None:
                            processed_row.append(None)
                        else:
                            processed_row.append(str(cell))
                
                batch_values.append(tuple(processed_row))
                row_count += 1
                
                # Insert in batches for better performance
                if len(batch_values) >= batch_size:
                    cursor.executemany(insert_sql, batch_values)
                    batch_values = []
        
        # Insert remaining rows
        if batch_values:
            cursor.executemany(insert_sql, batch_values)
        
        conn.commit()
        
        return {
            'success': True,
            'message': f'Successfully imported {row_count} rows from Excel',
            'rows_imported': row_count,
            'columns': headers,
            'image_columns': image_headers,
            'regular_columns': regular_headers
        }
        
    except Exception as e:
        return {'success': False, 'message': f'Error during Excel import: {str(e)}'}
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()
        if 'workbook' in locals():
            workbook.close()

'''
# Read all the JSON Data to insert into the temp_products Okay
def import_json_data(file_path, table_name='temp_products'):
    """Load JSON data into database table"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            json_data = json.load(file)
        
        # Handle both array and single object
        if isinstance(json_data, list):
            data_list = json_data
        else:
            data_list = [json_data]
        
        if not data_list:
            return {'success': False, 'message': 'JSON file is empty'}
        
        # Get headers from first object
        headers = list(data_list[0].keys())
        
        # Create table
        if not create_temp_varchar_table(headers, table_name):
            return {'success': False, 'message': 'Failed to create table'}
        
        # Prepare database connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Build INSERT query
        sql_headers = [clean_column_name(header) for header in headers]
        columns = ', '.join([f"`{h}`" for h in sql_headers])
        placeholders = ', '.join(['%s'] * len(sql_headers))
        insert_sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
        
        # Insert data
        row_count = 0
        for item in data_list:
            values = [str(item.get(header)) if item.get(header) is not None else None for header in headers]
            cursor.execute(insert_sql, values)
            row_count += 1
        
        conn.commit()
        
        return {
            'success': True,
            'message': f'Successfully imported {row_count} rows from JSON',
            'rows_imported': row_count,
            'columns': headers
        }
        
    except Exception as e:
        return {'success': False, 'message': f'Error during JSON import: {str(e)}'}
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()
'''

# users.html related to the Endpoints and the function 
# This code section is to create a customer table if it does not exist 
def create_customers_table():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if users table exists
        cursor.execute("SHOW TABLES LIKE 'customers'")
        result = cursor.fetchone()
        
        if not result:
            # Create users table
            cursor.execute("""
                CREATE TABLE customers (
                    customer_id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_name VARCHAR(100) NOT NULL,
                    address1 VARCHAR(255) NOT NULL,
                    address2 VARCHAR(255),
                    address3 VARCHAR(255),
                    location VARCHAR(100) NOT NULL,
                    city VARCHAR(50) NOT NULL,
                    state VARCHAR(50) NOT NULL,
                    country VARCHAR(50) Default 'India',
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
            conn.commit()
            print("Customers table created successfully!")
        else:
            print("Customers table already exists!")
            
    except Error as e:
        print(f"Error creating users table: {str(e)}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# orders.html related endpoint and function Okay.
def create_orders_table():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SHOW TABLES LIKE 'orders'")
        result = cursor.fetchone()
        
        if not result:
            cursor.execute("""
                CREATE TABLE orders (
                    order_id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_id INT NOT NULL,
                    barcode VARCHAR(255) NOT NULL,
                    quantity INT NOT NULL,
                    value DECIMAL(10,2) NOT NULL,
                    price_method VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status ENUM('pending','confirmed','shipped','delivered','cancelled') DEFAULT 'pending',
                    placed_by_user_id INT NULL,
                    placed_by_admin_id INT NULL,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                )
            """)
            conn.commit()
            print("Orders table created successfully!")
        else:
            print("Orders table already exists!")
            
    except Error as e:
        print(f"Error creating orders table: {str(e)}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


def create_inventory_reservations_table():
    """Create inventory_reservations table dynamically on startup"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get Quantity and Barcode column names from configurations
        cursor.execute("""
            SELECT SystemName FROM configurations 
            WHERE ConfigName = 'Quantity' AND Active = 1
        """)
        qty_result = cursor.fetchone()
        if not qty_result:
            print("⚠️ Quantity configuration missing. Cannot create inventory_reservations.")
            return
        quantity_col = qty_result[0]

        cursor.execute("""
            SELECT SystemName FROM configurations 
            WHERE ConfigName = 'Barcode' AND Active = 1
        """)
        bar_result = cursor.fetchone()
        if not bar_result:
            print("⚠️ Barcode configuration missing. Cannot create inventory_reservations.")
            return
        barcode_col = bar_result[0]

        # Create table if not exists
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS inventory_reservations (
                `{barcode_col}` VARCHAR(255) PRIMARY KEY,
                `{quantity_col}` DECIMAL(10,2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        # Copy data from inventories (only if table was just created)
        cursor.execute("SELECT COUNT(*) FROM inventory_reservations")
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"""
                INSERT INTO inventory_reservations (`{barcode_col}`, `{quantity_col}`)
                SELECT `{barcode_col}`, `{quantity_col}` FROM inventories
                ON DUPLICATE KEY UPDATE `{quantity_col}` = VALUES(`{quantity_col}`)
            """)
            print(f"Copied {cursor.rowcount} rows from inventories to inventory_reservations")

        conn.commit()
        print("inventory_reservations table ready")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error creating inventory_reservations: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# Route to serve the HTML page
# The route thats show the root page or the mentioned page in the index.html
@app.route('/')
def home():
    # Try connecting to the customer database
    conn = get_db_connection()

    # If no database assigned for this IP/Port → show HTML error page
    if conn is None:
        return """
        <html>
        <body style='background:#111; color:white; font-family:Arial; padding:40px;'>
            <h2>Authentication Failed</h2>
            <p>No database is allocated for your IP / Port.</p>
        </body>
        </html>
        """, 401

    # If DB exists, close connection and continue normal flow
    conn.close()

    # If user already logged in → go to home
    if session.get('user_logged_in'):
        return render_template('home.html')

    # If admin logged in → go to home
    elif session.get('admin_logged_in'):
        return render_template('home.html')

    # If nobody logged in → show login page
    else:
        return redirect('/gateway')


@app.route('/configurations')
@admin_required
def index():
    return render_template('configuration-management.html')

@app.route('/dataimport')
@admin_required
def data():
    return render_template('data-import.html')

# inventory.html
# This code section is used to show the inventory.html page when the user jumps to inventory page from the home page 
@app.route('/inventory')
@user_required
def inventory():
    return render_template('inventory.html')


# users.html related endpoint and the function
# This code section is used to show the users.html page to the customers
@app.route('/customers')
@admin_required
def users():
    return render_template('customers.html')


# orders.html related endpoint and the function
# This code section is used to show the orders.html page to the customers
@app.route('/orders')
@user_required
def orders():
    return render_template('orders.html')


@app.route('/user-registration')
@admin_required
def user_registration():
    return render_template('user-registration.html')

@app.route('/api/image/<barcode>')
@limiter.exempt
@user_required
def serve_image(barcode):
    """Serve image directly from database BLOB storage"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get image column name from configurations
        cursor.execute("""
            SELECT SystemName 
            FROM configurations 
            WHERE ConfigName = 'Image' AND Active = 1 
            LIMIT 1
        """)
        image_col_result = cursor.fetchone()
        
        if not image_col_result:
            # Return a default placeholder image
            img = Image.new('RGB', (150, 150), color='#f0f0f0')
            draw = ImageDraw.Draw(img)
            draw.text((40, 65), "No Image", fill='#666666')
            
            img_io = io.BytesIO()
            img.save(img_io, 'PNG')
            img_io.seek(0)
            
            return send_file(img_io, mimetype='image/png')
        
        image_col = image_col_result['SystemName']
        
        # Get barcode column name
        cursor.execute("""
            SELECT SystemName 
            FROM configurations 
            WHERE ConfigName = 'Barcode' AND Active = 1 
            LIMIT 1
        """)
        barcode_col_result = cursor.fetchone()
        
        if not barcode_col_result:
            # Return placeholder
            img = Image.new('RGB', (150, 150), color='#f0f0f0')
            draw = ImageDraw.Draw(img)
            draw.text((30, 65), "Config Error", fill='#666666')
            
            img_io = io.BytesIO()
            img.save(img_io, 'PNG')
            img_io.seek(0)
            
            return send_file(img_io, mimetype='image/png')
        
        barcode_col = barcode_col_result['SystemName']
        
        # Fetch image BLOB data
        cursor.execute(f"SELECT `{image_col}` FROM inventories WHERE `{barcode_col}` = %s", (barcode,))
        result = cursor.fetchone()
        
        if not result or result[image_col] is None:
            # Return placeholder
            img = Image.new('RGB', (150, 150), color='#f0f0f0')
            draw = ImageDraw.Draw(img)
            draw.text((40, 65), "No Image", fill='#666666')
            
            img_io = io.BytesIO()
            img.save(img_io, 'PNG')
            img_io.seek(0)
            
            return send_file(img_io, mimetype='image/png')
        
        # Return image from BLOB
        img_io = io.BytesIO(result[image_col])
        img_io.seek(0)
        
        # Try to detect image type
        try:
            # Try to open with PIL to get format
            img = Image.open(img_io)
            format_name = img.format.lower() if img.format else 'jpeg'
            
            # Reset stream
            img_io.seek(0)
            
            mime_type = f'image/{format_name}'
            if format_name == 'jpg':
                mime_type = 'image/jpeg'
            
            return send_file(img_io, mimetype=mime_type)
        except:
            # If PIL can't detect, assume JPEG
            img_io.seek(0)
            return send_file(img_io, mimetype='image/jpeg')
            
    except Exception as e:
        print(f"Error serving image for barcode {barcode}: {str(e)}")
        
        # Return error placeholder
        img = Image.new('RGB', (150, 150), color='#ffcccc')
        draw = ImageDraw.Draw(img)
        draw.text((20, 65), "Image Error", fill='#cc0000')
        
        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        
        return send_file(img_io, mimetype='image/png')
        
    finally:
        cursor.close()
        conn.close()

@app.route('/admin-registration')
@admin_required
def admin_registration():
    return render_template('admin-registration.html')


# update-status.html related Endpoint and the Function . 
# This code section is used to show the update-status.html page to the customers
@app.route('/update-status')
@user_required
def update_status_page():
    return render_template('update-status.html')


@app.route('/Top-Moving-Products')
def top_moving_page():
    if session.get('user_logged_in') or session.get('admin_logged_in'):
        return render_template('Top.html')
    else:
        return redirect('/gateway')


@app.route('/Slow-Moving-Products')
def slow_moving_page():
    if session.get('user_logged_in') or session.get('admin_logged_in'):
        return render_template('Slow.html')
    else:
        return redirect('/gateway')

@app.route('/user-security-settings')
@user_required
def user_security_settings():
    """User security settings page"""
    return render_template('user-security-settings.html')

@app.route('/Non-Moving-Products') 
def non_moving_page():
    if session.get('user_logged_in') or session.get('admin_logged_in'):
        return render_template('Non.html')
    else:
        return redirect('/gateway')

@app.route('/api/configurations', methods=['POST'])
@admin_required
def add_configuration():    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    required_fields = ['Recid', 'ConfigName', 'DisplayName', 'SerialNo']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    system_name = f"{data['ConfigName']}{data['SerialNo']}"
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        
        check_combo_query = """
        SELECT Recid FROM configurations 
        WHERE Recid = %s 
        AND ConfigName = %s 
        AND SerialNo = %s
        LIMIT 1
        """
        cursor.execute(check_combo_query, (data['Recid'], data['ConfigName'], data['SerialNo']))
        existing_combo = cursor.fetchone()
        
        if existing_combo:
            return jsonify({
                'success': False,
                'error': 'Configuration already exists',
                'existing_id': existing_combo['id']
            }), 409
        
        check_system_query = "SELECT Recid FROM configurations WHERE SystemName = %s LIMIT 1"
        cursor.execute(check_system_query, (system_name,))
        existing_system = cursor.fetchone()
        
        if existing_system:
            return jsonify({
                'success': False,
                'error': 'SystemName already exists',
                'existing_id': existing_system['id']
            }), 409
        
        check_display_query = """
        SELECT Recid FROM configurations 
        WHERE ConfigName = %s AND DisplayName = %s
        LIMIT 1
        """
        cursor.execute(check_display_query, (data['ConfigName'], data['DisplayName']))
        existing_display = cursor.fetchone()
        
        if existing_display:
            return jsonify({
                'success': False,
                'error': 'DisplayName already used',
                'existing_id': existing_display['id']
            }), 409
        
        insert_query = """
        INSERT INTO configurations 
        (Recid, ConfigName, SystemName, DisplayName, Active, Descr, SerialNo)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        
        cursor.execute(insert_query, (
            data['Recid'],
            data['ConfigName'],
            system_name,
            data['DisplayName'],
            data.get('Active', 1),
            data.get('Descr', ''),
            data['SerialNo']
        ))
        
        new_config_id = cursor.lastrowid
        
        if data['ConfigName'] == 'ItemPrice':
            itemcalc_check_query = """
            SELECT Recid FROM configurations 
            WHERE ConfigName = 'ItemCalc' AND SerialNo = %s
            LIMIT 1
            """
            cursor.execute(itemcalc_check_query, (data['SerialNo'],))
            existing_itemcalc = cursor.fetchone()
            
            if not existing_itemcalc:
                itemcalc_display_name = f"{data['DisplayName']}Value"
                itemcalc_system_name = f"ItemCalc{data['SerialNo']}"
                
                cursor.execute(check_system_query, (itemcalc_system_name,))
                existing_itemcalc_system = cursor.fetchone()
                
                if not existing_itemcalc_system:
                    cursor.execute(insert_query, (
                        9,
                        'ItemCalc',
                        itemcalc_system_name,
                        itemcalc_display_name,
                        data.get('Active', 1),
                        f"Calculated value for {data['DisplayName']}",
                        data['SerialNo']
                    ))
        
        connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Configuration added successfully',
            'systemName': system_name,
            'id': new_config_id
        }), 201
        
    except Exception as e:
        if connection:
            connection.rollback()
        
        error_msg = str(e)
        if '1062' in error_msg or 'duplicate' in error_msg.lower():
            return jsonify({
                'success': False,
                'error': 'Database duplicate error',
                'database_error': error_msg
            }), 409
        
        return jsonify({
            'success': False,
            'error': 'Database error',
            'message': f'Failed to add configuration: {error_msg}'
        }), 500
        
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

# This code section help us to get the Configurations data from the configurations table that shows int he API Format 
@app.route('/api/configurations', methods=['GET'])
@admin_required
def get_configurations():
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM configurations")
        configurations = cursor.fetchall()
        
        return jsonify(configurations), 200
        
    except Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


# This code section is used to get the column preferences from the customer to store in api to show the dta only  
@app.route('/api/save-columns', methods=['POST'])
@user_required
def save_columns():
    try:
        data = request.json
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
            
# This code section is used to categorize the confuigurations data to show that to the user in a categorigal way  of the display name so it will help the user to see the category easily and find the display name very easily Right            
@app.route('/api/configuration-categories', methods=['GET'])
@user_required
def get_configuration_categories():

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        # Get all active configurations grouped by category
        cursor.execute("""
            SELECT ConfigName, DisplayName, SystemName 
            FROM configurations 
            WHERE Active = 1 
            ORDER BY ConfigName, SerialNo
        """)
        
        configurations = cursor.fetchall()
        
        # Group by category
        categories = {}
        for config in configurations:
            category = config['ConfigName']
            if category not in categories:
                categories[category] = []
            categories[category].append({
                'displayName': config['DisplayName'],
                'systemName': config['SystemName']
            })
        
        return jsonify({
            'success': True,
            'categories': categories
        }), 200
        
    except Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()                        

# index.html related endpoint and the function
@app.route('/api/insert-configurations', methods=['POST'])
@admin_required
def insert_configurations_api():
    try:
        # Call your existing function
        rows_affected = inserting_values()
        
        if rows_affected > 0:
            return jsonify({
                'success': True, 
                'message': 'Data inserted successfully into tmp_products!',
                'rows_affected': rows_affected 
            }), 200
        else:
            return jsonify({
                'success': True,  # Still success, just no new rows
                'message': 'Data already exists in tmp_products table.',
                'rows_affected': 0
            }), 200
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to insert data: {str(e)}'
        }), 500

@app.route('/api/batch-images', methods=['POST'])
@user_required
def get_batch_images():
    """Get multiple images in one request - reduces HTTP calls from thousands to just 1 per page!"""
    try:
        data = request.json
        barcodes = data.get('barcodes', [])
        
        if not barcodes:
            return jsonify({'success': False, 'message': 'No barcodes provided'}), 400
        
        # Limit batch size for safety
        if len(barcodes) > 100:
            barcodes = barcodes[:100]
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get image column name
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Image' AND Active = 1 LIMIT 1")
        image_col_result = cursor.fetchone()
        
        if not image_col_result:
            return jsonify({'success': False, 'message': 'Image configuration not found'}), 400
        
        image_col = image_col_result['SystemName']
        
        # Get barcode column name
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
        barcode_col_result = cursor.fetchone()
        
        if not barcode_col_result:
            return jsonify({'success': False, 'message': 'Barcode configuration not found'}), 400
        
        barcode_col = barcode_col_result['SystemName']
        
        # Fetch images for all barcodes in ONE QUERY
        placeholders = ', '.join(['%s'] * len(barcodes))
        query = f"""
            SELECT `{barcode_col}`, `{image_col}`
            FROM inventories 
            WHERE `{barcode_col}` IN ({placeholders})
        """
        
        cursor.execute(query, barcodes)
        results = cursor.fetchall()
        
        # Prepare response
        images_data = {}
        
        for row in results:
            barcode = row[barcode_col]
            image_blob = row[image_col]
            
            if image_blob:
                # Convert BLOB to base64 for easy frontend use
                import base64
                image_base64 = base64.b64encode(image_blob).decode('utf-8')
                
                # Try to detect image type
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(image_blob))
                    mime_type = f"image/{img.format.lower()}"
                except:
                    mime_type = "image/jpeg"
                
                images_data[barcode] = f"data:{mime_type};base64,{image_base64}"
            else:
                images_data[barcode] = None
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'images': images_data,
            'count': len(images_data),
            'requested_count': len(barcodes),
            'message': f'Loaded {len(images_data)} images in one request'
        })
        
    except Exception as e:
        print(f"Batch image loading error: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/check-images-exist', methods=['GET'])
@user_required
def check_images_exist():
    """Check if image configuration and data exist"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Step 1: Check if Image configuration exists
        cursor.execute("""
            SELECT COUNT(*) as config_count 
            FROM configurations 
            WHERE ConfigName = 'Image' AND Active = 1
        """)
        config_result = cursor.fetchone()
        
        if not config_result or config_result['config_count'] == 0:
            return jsonify({
                'success': True,
                'has_images': False,
                'reason': 'No image configuration found'
            })
        
        # Step 2: Get the image column name
        cursor.execute("""
            SELECT SystemName 
            FROM configurations 
            WHERE ConfigName = 'Image' AND Active = 1 
            LIMIT 1
        """)
        image_col_result = cursor.fetchone()
        
        if not image_col_result:
            return jsonify({
                'success': True,
                'has_images': False,
                'reason': 'Image column name not found'
            })
        
        image_col = image_col_result['SystemName']
        
        # Step 3: Check if inventories table has this column
        cursor.execute("SHOW COLUMNS FROM inventories LIKE %s", (image_col,))
        column_exists = cursor.fetchone()
        
        if not column_exists:
            return jsonify({
                'success': True,
                'has_images': False,
                'reason': f'Image column "{image_col}" not found in inventories table'
            })
        
        # Step 4: Check if any image data actually exists
        cursor.execute(f"""
            SELECT COUNT(*) as image_count 
            FROM inventories 
            WHERE `{image_col}` IS NOT NULL 
            AND `{image_col}` != ''
            AND LENGTH(`{image_col}`) > 0
        """)
        data_result = cursor.fetchone()
        
        has_images = data_result and data_result['image_count'] > 0
        
        return jsonify({
            'success': True,
            'has_images': has_images,
            'image_count': data_result['image_count'] if data_result else 0,
            'image_column': image_col,
            'reason': 'Images found' if has_images else 'No image data in table'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'has_images': False,
            'reason': str(e)
        }), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/tmpget', methods=['GET'])
@admin_required
def get_the_data():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(dictionary=True)
        cursor.execute("select * from tmp_products")
        table_result = cursor.fetchall()
        return jsonify({
            'success': True,
            'data': table_result,
            'count': len(table_result)
        }), 200
    except Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():  
            conn.close()


# index.html related endpoint and the function
# This is used to migrate the temp_products data to the inventories Table okay...
@app.route('/api/migrate-to-inventories', methods=['POST'])
@user_required
def migrate_to_inventories():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor()
    
    try:
        # Step 1: Check if inventories table exists, if not create it
        cursor.execute("SHOW TABLES LIKE 'inventories'")
        if not cursor.fetchone():
            # Create inventories table based on tmp_products structure
            cursor.execute("SELECT SystemName, DataType FROM tmp_products ORDER BY SystemName")
            tmp_columns = cursor.fetchall()
            
            column_defs = []
            for system_name, data_type in tmp_columns:
                column_defs.append(f"`{system_name}` {data_type}")
            
            create_sql = f"""
                CREATE TABLE inventories (
                    {', '.join(column_defs)},
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            
            cursor.execute(create_sql)
            print("Created inventories table")
        else:
            cursor.execute("Select systemname, datatype from tmp_products")
            tmp_columns = {row[0]: row[1] for row in cursor.fetchall()}
            
            cursor.execute("show columns from inventories")
            existing_columns = [row[0] for row in cursor.fetchall()]
            
            for system_name, data_type in tmp_columns.items():
                if system_name not in existing_columns and system_name != 'created_at':
                    alter_sql = f"alter table inventories add column `{system_name}` {data_type}"
                    cursor.execute(alter_sql)
                    print(f"Added missing column : {system_name} {data_type}")
                                                
        # Step 2: Get column mapping from configurations
        cursor.execute("SELECT DisplayName, SystemName FROM configurations WHERE Active = 1")
        mappings = cursor.fetchall()
        
        column_mapping = {}
        for display_name, system_name in mappings:
            if display_name and system_name:
                column_mapping[display_name.lower()] = system_name
        
        # Step 3: Get columns from temp_products
        cursor.execute("SHOW COLUMNS FROM temp_products")
        temp_columns = [row[0] for row in cursor.fetchall()]
        
        # Step 4: Prepare source and target columns
        source_cols = []
        target_cols = []
        
        for temp_col in temp_columns:
            system_name = column_mapping.get(temp_col.lower())
            if system_name:
                source_cols.append(f"`{temp_col}`")
                target_cols.append(f"`{system_name}`")
        
        if not source_cols:
            return jsonify({
                'success': False,
                'message': 'No column mappings found for migration'
            }), 400
        
        # Step 5: Get the barcode and quantity display names from configurations
        barcode_system_name = None
        barcode_temp_col = None
        quantity_system_name = None
        quantity_temp_col = None
        
        # Get the display name for barcode1 system name
        cursor.execute("SELECT DisplayName FROM configurations WHERE SystemName = 'barcode1' AND Active = 1")
        barcode_result = cursor.fetchone()
        
        if barcode_result:
            barcode_display_name = barcode_result[0].lower()
            
            # Now find the system name and temp column that maps to this display name
            for display_name, system_name in column_mapping.items():
                if display_name == barcode_display_name:
                    barcode_system_name = system_name
                    # Find the actual temp column name that maps to barcode
                    for temp_col in temp_columns:
                        if column_mapping.get(temp_col.lower()) == system_name:
                            barcode_temp_col = temp_col
                            break
                    break
        
        # Get the display name for quantity system name
        cursor.execute("""
            SELECT DisplayName FROM configurations 
            WHERE ConfigName = 'Quantity' AND Active = 1 
            LIMIT 1
        """)
        quantity_result = cursor.fetchone()
        
        if quantity_result:
            quantity_display_name = quantity_result[0].lower()
            
            # Now find the system name and temp column that maps to this display name
            for display_name, system_name in column_mapping.items():
                if display_name == quantity_display_name:
                    quantity_system_name = system_name
                    # Find the actual temp column name that maps to quantity
                    for temp_col in temp_columns:
                        if column_mapping.get(temp_col.lower()) == system_name:
                            quantity_temp_col = temp_col
                            break
                    break
        
        if not barcode_system_name or not barcode_temp_col:
            return jsonify({
                'success': False,
                'message': 'BarcodeNo mapping not found in configurations or temp table'
            }), 400
        
        # Step 6: Get calculation mappings dynamically
        cursor.execute("""
            SELECT SystemName, DisplayName
            FROM configurations 
            WHERE Active = 1 
            AND ConfigName = 'ItemCalc'
            AND DisplayName LIKE '%Value'
        """)
        calculation_mappings = cursor.fetchall()

        # Prepare dynamic calculation columns
        calculation_cols = []
        calculation_selects = []

        # Get the quantity field name from configurations
        cursor.execute("""
            SELECT DisplayName FROM configurations 
            WHERE Active = 1 AND ConfigName = 'Quantity'
            LIMIT 1
        """)
        quantity_result = cursor.fetchone()
        quantity_field = quantity_result[0].lower() if quantity_result else 'qty'  # Default to 'qty'

        for system_name, display_name in calculation_mappings:
            # Extract the base field name (remove 'Value' suffix)
            # Example: "RetailValue" → "retail"
            base_field_name = display_name.replace('Value', '').lower()
            
            # Dynamic formula: base_field * quantity_field
            formula = f"`{base_field_name}` * `{quantity_field}`"
            
            calculation_cols.append(f"`{system_name}`")
            calculation_selects.append(formula)

        has_quantity = quantity_temp_col is not None
        has_barcode = barcode_temp_col is not None
        
        if not has_quantity or not has_barcode:
            return jsonify({
                'success': False,
                'message': 'Required columns (quantity/Qty and barcodeNo/Barcode) not found in uploaded file'
            }), 400
        
        # Build dynamic WHERE clause based on available ItemPrice columns
        where_conditions = []
        for temp_col in temp_columns:
            system_name = column_mapping.get(temp_col.lower())
            if system_name:
                # Check if this is an ItemPrice field
                cursor.execute("""
                    SELECT ConfigName FROM configurations 
                    WHERE SystemName = %s AND Active = 1
                """, (system_name,))
                config_result = cursor.fetchone()
                if config_result and config_result[0] == 'ItemPrice':
                    where_conditions.append(f"tp.`{temp_col}` IS NOT NULL")
        
        # Add quantity check (find the actual quantity column name)
        quantity_col = None
        for col in temp_columns:
            if col.lower() in ['qty', 'quantity']:
                quantity_col = col
                break
        if quantity_col:
            where_conditions.append(f"tp.`{quantity_col}` IS NOT NULL")
        
        # Final migration query
        insert_sql = f"""
            INSERT INTO inventories (
                {', '.join(target_cols + calculation_cols)},
                created_at
            )
            SELECT 
                {', '.join(source_cols + calculation_selects)},
                NOW()
            FROM temp_products tp
            WHERE 
                {' AND '.join(where_conditions) if where_conditions else '1=1'}
                AND NOT EXISTS (
                    SELECT 1 FROM inventories inv 
                    WHERE inv.`{barcode_system_name}` = tp.`{barcode_temp_col}`
                )
        """
        cursor.execute(insert_sql)
        conn.commit()
        
        # Step 8: Get results
        new_rows_count = cursor.rowcount
        cursor.execute("SELECT COUNT(*) FROM inventories")
        total_rows_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM temp_products")
        source_count = cursor.fetchone()[0]
        
        return jsonify({
            'success': True,
            'message': f'Successfully migrated {new_rows_count} NEW rows to inventories',
            'new_rows_added': new_rows_count,
            'total_rows_in_inventories': total_rows_count,
            'rows_in_temp_table': source_count,
            'columns_mapped': len(target_cols)
        }), 200
        
    except Exception as e:
        conn.rollback()
        return jsonify({
            'success': False,
            'message': f'Database error during migration: {str(e)}'
        }), 500
    finally:
        cursor.close()
        conn.close()

# This code section is used to view the data present in the categorical order in the API Format this is only get  by the api-key only okay
@app.route('/api/inventories', methods=['GET'])
@user_required
def get_inventories_data():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. Check table exists
        cursor.execute("SHOW TABLES LIKE 'inventories'")
        if not cursor.fetchone():
            return jsonify({
                'success': False,
                'message': 'Inventories table does not exist'
            }), 404
        # 2. Load configurations
        cursor.execute("""
            SELECT 
                c.SystemName,
                c.DisplayName,
                c.ConfigName,
                c.SerialNo
            FROM configurations c
            WHERE c.Active = 1
            ORDER BY c.ConfigName, c.SerialNo
        """)
        configs = cursor.fetchall()

        # 3. Build category mapping
        category_map = {}

        for config in configs:
            config_name = config['ConfigName']
            system_name = config['SystemName']
            display_name = config['DisplayName']

            if config_name not in category_map:
                category_map[config_name] = []

            category_map[config_name].append({
                'system_name': system_name,
                'display_name': display_name
            })

        # 4. Get barcode column name for image URLs
        barcode_col = None
        for config in configs:
            if config['ConfigName'] == 'Barcode':
                barcode_col = config['SystemName']
                break

        # 5. Fetch inventory data
        cursor.execute("SELECT * FROM inventories ORDER BY created_at DESC")
        inventory_data = cursor.fetchall()

        # 6. Transform response
        result = []

        for row in inventory_data:
            formatted_row = {}

            for category, fields in category_map.items():
                category_data = {}

                for field in fields:
                    system_name = field['system_name']
                    display_name = field['display_name']

                    if system_name in row and row[system_name] is not None:
                        value = row[system_name]

                        # ✅ IMAGE HANDLING - Create URL instead of file path
                        if category == 'Image':
                            # Get barcode for this product
                            if barcode_col and barcode_col in row:
                                barcode_value = row[barcode_col]
                                if barcode_value:
                                    # Create image URL
                                    value = f"/api/image/{barcode_value}"
                                else:
                                    value = "/api/image/placeholder"
                            else:
                                value = "/api/image/placeholder"
                        
                        # For non-image fields, keep as is
                        category_data[display_name] = value

                if category_data:
                    formatted_row[category] = category_data
            # Metadata
            formatted_row['metadata'] = {
                'created_at': row['created_at'].isoformat()
                if row.get('created_at') else None
            }
            result.append(formatted_row)

        return jsonify({
            'success': True,
            'data': result,
            'total_records': len(result),
            'categories': list(category_map.keys())
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/column-mapping', methods=['GET'])
@user_required
def get_column_mapping():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(dictionary=True)
    try:
        # Get all active configurations
        cursor.execute("""
            SELECT 
                ConfigName,
                SystemName,
                DisplayName,
                SerialNo
            FROM configurations 
            WHERE Active = 1
            ORDER BY ConfigName, SerialNo
        """)
        
        mappings = cursor.fetchall()
        
        # Return flat list directly
        return jsonify({
            'success': True,
            'mappings': mappings,
            'total': len(mappings)
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        cursor.close()
        conn.close()

def ensure_indexes(connection, barcode_system_name, barcode_temp_col):
    """Create indexes on the join columns for lightning-fast lookups"""
    cursor = connection.cursor()
    try:
        # Create index on inventories table if it doesn't exist
        cursor.execute(f"SHOW INDEX FROM inventories WHERE Column_name = '{barcode_system_name}'")
        if not cursor.fetchone():
            cursor.execute(f"CREATE INDEX idx_inv_barcode ON inventories ({barcode_system_name})")
            print(f"Created index on inventories({barcode_system_name})")

        # Create index on temp_products table if it doesn't exist
        cursor.execute(f"SHOW INDEX FROM temp_products WHERE Column_name = '{barcode_temp_col}'")
        if not cursor.fetchone():
            cursor.execute(f"CREATE INDEX idx_tmp_barcode ON temp_products ({barcode_temp_col})")
            print(f"Created index on temp_products({barcode_temp_col})")

        connection.commit()
    except Error as e:
        print(f"Note: Could not create indexes (they may already exist): {e}")
        connection.rollback()
    finally:
        cursor.close()


# inventores.html related endpoint and the function okay
# This is used to update the data present in the inventories like the excel data present in the temp_products 
# This is actually a Updation of inventories table form the temp_products it will happen in quick seconds
@app.route('/api/sync-inventories', methods=['PUT'])
@user_required
def sync_inventories():
    #Database Connection
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500

    cursor = conn.cursor()

    try:
        # 2. Get the system name for the barcode column (the unique key)
        cursor.execute("""
            SELECT c.SystemName 
            FROM configurations c 
            WHERE c.Active = 1 AND c.ConfigName = 'Barcode'
            LIMIT 1
        """)
        barcode_system_name_result = cursor.fetchone()
        if not barcode_system_name_result:
            return jsonify({'success': False, 'message': 'Barcode configuration not found.'}), 400
        barcode_system_name = barcode_system_name_result[0]

        # 3. Find the actual column name in temp_products that maps to the barcode system name
        cursor.execute("SHOW COLUMNS FROM temp_products")
        temp_columns = [row[0] for row in cursor.fetchall()]
        
        # Create a mapping from temp table headers to system names
        cursor.execute("SELECT DisplayName, SystemName FROM configurations WHERE Active = 1")
        column_mapping = {row[0].lower(): row[1] for row in cursor.fetchall()}

        barcode_temp_col = None
        for col in temp_columns:
            mapped_system_name = column_mapping.get(col.lower())
            if mapped_system_name == barcode_system_name:
                barcode_temp_col = col
                break

        if not barcode_temp_col:
            return jsonify({'success': False, 'message': 'Could not find Barcode column in temp_products.'}), 400

        # !!! CRITICAL PERFORMANCE OPTIMIZATION !!!
        # Create indexes on the join columns if they don't exist
        ensure_indexes(conn, barcode_system_name, barcode_temp_col)

        # 4. Get all active configurations to know which columns to compare
        cursor.execute("""
            SELECT SystemName, ConfigName 
            FROM configurations 
            WHERE Active = 1
        """)
        all_configs = cursor.fetchall()

        # 4b. DYNAMIC FILTER: Define which ConfigNames should NOT be synced.
        exclude_configs = {'Barcode', 'ItemCalc'}
        configs_to_sync = []
        for system_name, config_name in all_configs:
            if config_name not in exclude_configs:
                configs_to_sync.append((system_name, config_name))

        # 5. Build the massive, efficient UPDATE query using JOIN and comparison.
        update_set_parts = []
        join_condition = f"inv.`{barcode_system_name}` = tp.`{barcode_temp_col}`"

        for system_name, config_name in configs_to_sync:
            # For each column to sync, find its corresponding temp column
            temp_col_for_system = None
            for temp_col in temp_columns:
                if column_mapping.get(temp_col.lower()) == system_name:
                    temp_col_for_system = temp_col
                    break
            if temp_col_for_system:
                update_set_parts.append(f"inv.`{system_name}` = tp.`{temp_col_for_system}`")

        if not update_set_parts:
            return jsonify({'success': False, 'message': 'No mappable columns found to update.'}), 400

        # 6. Construct the final query
        update_query = f"""
            UPDATE inventories inv
            INNER JOIN temp_products tp ON {join_condition}
            SET {', '.join(update_set_parts)}
        """

        # 7. EXECUTE THE SYNC
        cursor.execute(update_query)
        rows_updated = cursor.rowcount
        conn.commit()

        # 8. Return results
        return jsonify({
            'success': True,
            'message': f'Successfully synchronized inventories. {rows_updated} rows updated.',
            'rows_updated': rows_updated
        }), 200

    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error during sync: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Database error during synchronization: {str(e)}'
        }), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/import-excel', methods=['POST'])
def import_excel_api():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Only Excel files are allowed'}), 400
    
    try:
        # Save uploaded file temporarily
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"upload_{uuid.uuid4().hex}.xlsx")
        file.save(temp_path)
        
        # Import Excel data
        result = import_excel_data(temp_path)
        
        # Clean up temporary file
        try:
            os.remove(temp_path)
        except:
            pass
        
        return jsonify(result), 200 if result['success'] else 500
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


# users.html
# users.html related endpoint and functions
@app.route('/api/users', methods=['POST'])
@user_required
def create_user():
    try:
        data = request.get_json()
        
        conn = get_db_connection()
        cursor = conn.cursor()

        def clean_value(value):
            if value and value.strip() != '':
                return value
            else:
                return None
        
        insert_query = """
            INSERT INTO customers 
            (customer_name, address1, address2, address3, location, city, state, country, 
             pincode, mobile1, mobile2, ph1, ph2, emailid1, emailid2)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        values = (
            data['customer_name'],                                   # 1
            data['address1'],                                        # 2
            clean_value(data.get('address2')),                       # 3
            clean_value(data.get('address3')),                       # 4
            data['location'],                                        # 5
            data['city'],                                            # 6
            data['state'],                                           # 7
            clean_value(data.get('country')),                        # 8
            data['pincode'],                                         # 9
            data['mobile1'],                                         # 10
            clean_value(data.get('mobile2')),                        # 11
            clean_value(data.get('ph1')),                            # 12
            clean_value(data.get('ph2')),                            # 13
            data['emailid1'],                                        # 14
            clean_value(data.get('emailid2'))                      # 16                                                 # 17
        )
        
        cursor.execute(insert_query, values)
        conn.commit()
        customer_id = cursor.lastrowid
        
        cursor.close()
        conn.close()
        
        formatted_id = f"{customer_id:05d}"
        
        return jsonify({
            'success': True, 
            'message': f'Customer created successfully! Customer ID: {formatted_id}',
            'customer_id': customer_id,
            'formatted_id': formatted_id
        })
        
    except Error as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
            
'''
@app.route('/api/import-json', methods=['POST'])
def import_json_api():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400
    
    if not file.filename.endswith('.json'):
        return jsonify({'success': False, 'message': 'Only JSON files are allowed'}), 400
    
    try:
        # Save uploaded file temporarily
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"upload_{uuid.uuid4().hex}.json")
        file.save(temp_path)
        
        # Import JSON data
        result = import_json_data(temp_path)
        
        # Clean up temporary file
        try:
            os.remove(temp_path)
        except:
            pass
        
        return jsonify(result), 200 if result['success'] else 500
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
'''

# orders.html related endpoint and the functions
@app.route('/api/orders', methods=['POST'])
@user_required
def create_order():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['customer_id', 'barcode', 'quantity', 'price_method', 'value']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
        
        # Validate customer exists in customers table
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT customer_id FROM customers WHERE customer_id = %s", (data['customer_id'],))
        customer = cursor.fetchone()
        
        if not customer:
            return jsonify({'success': False, 'message': 'Customer not found'}), 404
        
        placed_by_user_id = None
        placed_by_admin_id = None
        
        if session.get('user_logged_in'):
            placed_by_user_id = session.get('user_id')
        
        elif session.get('admin_logged_in'):
            placed_by_admin_id = session.get('admin_id')
        
        if session.get('user_logged_in') and session.get('admin_logged_in'):
            placed_by_user_id = session.get('user_id')
            placed_by_admin_id = None
        
        print(f"Final - placed_by_user_id: {placed_by_user_id}, placed_by_admin_id: {placed_by_admin_id}")
        
        cursor.execute("""
            INSERT INTO orders (customer_id, barcode, quantity, value, price_method, status, placed_by_user_id, placed_by_admin_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data['customer_id'],
            data['barcode'],
            data['quantity'],
            data['value'],
            data['price_method'],
            data.get('status', 'pending'),
            placed_by_user_id,
            placed_by_admin_id
        ))
        
        conn.commit()
        order_id = cursor.lastrowid
        
        return jsonify({
            'success': True, 
            'message': 'Order created successfully!',
            'order_id': order_id
        }), 201
        
    except Error as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
 
@app.route('/api/inventory-products', methods=['GET'])
@user_required
def get_inventory_products():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT SystemName FROM configurations 
            WHERE ConfigName = 'Quantity' AND Active = 1 
            LIMIT 1
        """)
        qty_config = cursor.fetchone()
        if not qty_config:
            return jsonify({
                'success': False,
                'message': 'Quantity configuration not found in database'
            }), 500

        quantity_col = qty_config['SystemName']

        cursor.execute("SHOW COLUMNS FROM inventories LIKE %s", (quantity_col,))
        if not cursor.fetchone():
            return jsonify({
                'success': False,
                'message': f'Column "{quantity_col}" not found in inventories table'
            }), 500

        query = f"""
            SELECT * FROM inventories 
            WHERE `{quantity_col}` > 0 
            ORDER BY created_at DESC
        """
        cursor.execute(query)
        products = cursor.fetchall()

        return jsonify({
            'success': True,
            'products': products,
            'count': len(products),
            'used_quantity_column': quantity_col  # for debugging
        }), 200

    except Error as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Unexpected error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/price-methods', methods=['GET'])
def get_price_methods():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT SystemName, DisplayName 
            FROM configurations 
            WHERE ConfigName = 'ItemPrice' AND Active = 1
            ORDER BY SerialNo
        """)

        price_methods = cursor.fetchall()

        return jsonify({
            'success': True,
            'price_methods': price_methods,
            'count': len(price_methods)
        }), 200

    except Error as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# update-status.html related endpoint and the function
# This is used to get the user by the id present in the users table 
@app.route('/api/users/<int:customer_id>', methods=['GET'])
@user_required
def get_user_by_id(customer_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT customer_id, customer_name, emailid1, emailid2
            FROM customers 
            WHERE customer_id = %s
            LIMIT 1
        """, (customer_id,))
        customer = cursor.fetchone()
        if customer:
            return jsonify({
                'success': True,
                'customer': customer
            }), 200
        else:
            return jsonify({
                'success': False,
                'message': 'Customer not found'
            }), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# update-status.html related endpoint and the function
# This is used to get the orders present in the orders table
@app.route('/api/orders', methods=['GET'])
@user_required
def get_orders():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        customer_id = request.args.get('customer_id')
        if customer_id:
            cursor.execute("""
                SELECT order_id, customer_id, barcode, quantity, value, price_method, status, created_at
                FROM orders 
                WHERE customer_id = %s
                ORDER BY created_at DESC
            """, (customer_id,))
        else:
            cursor.execute("""
                SELECT order_id, customer_id, barcode, quantity, value, price_method, status, created_at
                FROM orders 
                ORDER BY created_at DESC
            """)
            
        orders = cursor.fetchall()
        for order in orders:
            if isinstance(order['created_at'], datetime):
                order['created_at'] = order['created_at'].strftime("%Y-%m-%dT%H:%M:%S")
        return jsonify({
            'success': True,
            'orders': orders,
            'count': len(orders)
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# update-status.html related endpoint and the function
@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
@user_required
def update_order_status(order_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        new_status = data.get('status')
        if not new_status:
            return jsonify({'success': False, 'message': 'Status is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get order details including previous status
        cursor.execute("""
            SELECT o.barcode, o.quantity, o.status as old_status 
            FROM orders o WHERE o.order_id = %s
        """, (order_id,))
        order = cursor.fetchone()
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404

        # Update order status
        cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", (new_status, order_id))

        # Handle inventory adjustments based on status transitions
        old_status = order['old_status']
        barcode = order['barcode']
        quantity = order['quantity']

        # Only process inventory changes if status is actually changing
        if old_status != new_status:
            # Get Quantity and Barcode column names DYNAMICALLY
            cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1 LIMIT 1")
            qty_result = cursor.fetchone()
            if not qty_result:
                return jsonify({'success': False, 'message': 'Quantity configuration missing'}), 500
            quantity_col = qty_result['SystemName']

            cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
            bar_result = cursor.fetchone()
            if not bar_result:
                return jsonify({'success': False, 'message': 'Barcode configuration missing'}), 500
            barcode_col = bar_result['SystemName']

            # Determine inventory adjustment based on status transition
            adjustment = 0
            adjustment_type = None
            
            # Case 1: Moving to confirmed/shipped/delivered (deduct from inventory)
            if (old_status in ['pending', 'cancelled'] and 
                new_status in ['confirmed', 'shipped', 'delivered']):
                adjustment = -quantity  # Deduct
                adjustment_type = "deduct"
            
            # Case 2: Moving back to pending or cancelled (add back to inventory)
            elif (old_status in ['confirmed', 'shipped', 'delivered'] and 
                  new_status in ['pending', 'cancelled']):
                adjustment = quantity  # Add back
                adjustment_type = "add"
            
            # Apply inventory adjustment if needed
            if adjustment != 0:
                # First check if product exists in inventory
                cursor.execute(f"""
                    SELECT `{quantity_col}` FROM inventories 
                    WHERE `{barcode_col}` = %s
                """, (barcode,))
                inventory_item = cursor.fetchone()
                
                if not inventory_item:
                    conn.rollback()
                    return jsonify({
                        'success': False,
                        'message': 'Product not found in inventory'
                    }), 400
                
                current_quantity = inventory_item[quantity_col] or 0
                
                # For deductions, check if enough stock is available
                if adjustment_type == "deduct" and current_quantity < quantity:
                    conn.rollback()
                    return jsonify({
                        'success': False,
                        'message': f'Insufficient stock. Available: {current_quantity}, Requested: {quantity}'
                    }), 400
                
                # Update inventory
                cursor.execute(f"""
                    UPDATE inventories 
                    SET `{quantity_col}` = `{quantity_col}` + %s 
                    WHERE `{barcode_col}` = %s
                """, (adjustment, barcode))
                
                if cursor.rowcount == 0:
                    conn.rollback()
                    return jsonify({
                        'success': False,
                        'message': 'Failed to update inventory'
                    }), 400

        conn.commit()
        return jsonify({
            'success': True,
            'message': f'Order status updated to {new_status}',
            'order_id': order_id,
            'inventory_adjusted': adjustment != 0,
            'adjustment_type': adjustment_type,
            'adjustment_amount': abs(adjustment) if adjustment != 0 else 0
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# admin-login.html these endpoint, and the function is related to this html page 
# Admin Logout API
@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    """Admin logout endpoint"""
    session.pop('admin_logged_in', None)
    session.pop('admin_user', None)
    return jsonify({'success': True, 'message': 'Logout successful'})


@app.route('/admin/orders', methods=['GET'])
@admin_required
def get_all_orders_admin():
    """Get all orders for admin dashboard with customer AND placer details"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get all orders with customer AND placer information
        cursor.execute("""
            SELECT 
                o.order_id,
                o.customer_id,
                o.barcode,
                o.quantity,
                o.value,
                o.price_method,
                o.status,
                o.created_at,
                -- Customer details
                c.customer_name,
                c.emailid1 as customer_email,
                c.mobile1 as customer_mobile,
                -- User who placed order (if any)
                u.user_id as placed_by_user_id,
                u.user_name as placed_by_user_name,
                u.user_email as placed_by_user_email,
                -- Admin who placed order (if any)  
                a.admin_id as placed_by_admin_id,
                a.admin_name as placed_by_admin_name,
                a.admin_email as placed_by_admin_email,
                -- Determine who placed the order
                CASE 
                    WHEN o.placed_by_user_id IS NOT NULL THEN 'user'
                    WHEN o.placed_by_admin_id IS NOT NULL THEN 'admin'
                    ELSE 'unknown'
                END as placed_by_type,
                -- Get the actual placer's name and email
                COALESCE(u.user_name, a.admin_name) as placed_by_name,
                COALESCE(u.user_email, a.admin_email) as placed_by_email
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN users u ON o.placed_by_user_id = u.user_id
            LEFT JOIN admin_users a ON o.placed_by_admin_id = a.admin_id
            ORDER BY o.created_at DESC
        """)
        
        orders = cursor.fetchall()
        
        # Format the response
        formatted_orders = []
        for order in orders:
            # Get product name from inventories table using barcode
            product_name = "Unknown Product"
            try:
                # Get ItemMaster column names dynamically
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
                barcode_col_result = cursor.fetchone()
                barcode_col = barcode_col_result['SystemName'] if barcode_col_result else 'BarcodeNo'
                
                cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1 LIMIT 1")
                itemmaster_result = cursor.fetchone()
                
                if itemmaster_result:
                    itemmaster_col = itemmaster_result['SystemName']
                    # Get product name from inventories
                    cursor.execute(f"SELECT `{itemmaster_col}` FROM inventories WHERE `{barcode_col}` = %s LIMIT 1", (order['barcode'],))
                    product_result = cursor.fetchone()
                    if product_result:
                        product_name = product_result[itemmaster_col] or "Unknown Product"
            except Exception as e:
                print(f"Error getting product name: {str(e)}")
                product_name = "Unknown Product"
            
            formatted_orders.append({
                'order_id': order['order_id'],
                'customer_id': order['customer_id'],
                'customer_name': order['customer_name'] or 'Unknown Customer',
                'customer_email': order['customer_email'] or 'No email',
                'customer_mobile': order['customer_mobile'] or 'No mobile',
                'barcode': order['barcode'],
                'product_name': product_name,  # NEW: Add product name
                'quantity': order['quantity'],
                'value': float(order['value']) if order['value'] else 0,
                'price_method': order['price_method'],
                'status': order['status'],
                'created_at': order['created_at'].isoformat() if order['created_at'] else None,
                # Placer information
                'placed_by_type': order['placed_by_type'],
                'placed_by_name': order['placed_by_name'] or 'Unknown',
                'placed_by_email': order['placed_by_email'] or 'No email',
                'placed_by_user_id': order['placed_by_user_id'],
                'placed_by_admin_id': order['placed_by_admin_id']
            })
        
        return jsonify({
            'success': True,
            'orders': formatted_orders,
            'total_orders': len(orders),
            'admin_user': session.get('admin_user', 'admin')
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# Check admin authentication status
# admin-login.html related endpoint and the function
@app.route('/admin/check-auth', methods=['GET'])
def check_admin_auth():
    """Check if admin is logged in"""
    return jsonify({
        'authenticated': session.get('admin_logged_in', False),
        'user': session.get('admin_user', '')
    })

# Admin dashboard page route
@app.route('/admin-dashboard')
@admin_required
def admin_dashboard_page():
    return render_template('admin-dashboard.html')

# Admin login page route
@app.route('/admin')
def admin_login_page():
    return render_template('admin-login.html')

#update-status.html related endpoint and the function
@app.route('/api/orders/<int:order_id>', methods=['DELETE'])
@user_required
def delete_order(order_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        reason = data.get('reason', 'No reason provided')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get order details including status
        cursor.execute("""
            SELECT o.customer_id, o.barcode, o.quantity, o.status 
            FROM orders o WHERE o.order_id = %s
        """, (order_id,))
        order = cursor.fetchone()
        
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'}), 404

        # Check if order can be deleted based on status
        if order['status'] in ['shipped', 'delivered']:
            return jsonify({
                'success': False, 
                'message': f'Cannot delete order with status: {order["status"]}'
            }), 400

        # Restore inventory for confirmed orders
        if order['status'] == 'confirmed':
            # Get Quantity and Barcode column names dynamically
            cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1 LIMIT 1")
            qty_result = cursor.fetchone()
            if not qty_result:
                return jsonify({'success': False, 'message': 'Quantity configuration missing'}), 500
            quantity_col = qty_result['SystemName']

            cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
            bar_result = cursor.fetchone()
            if not bar_result:
                return jsonify({'success': False, 'message': 'Barcode configuration missing'}), 500
            barcode_col = bar_result['SystemName']

            # Restore inventory quantity
            cursor.execute(f"""
                UPDATE inventories 
                SET `{quantity_col}` = `{quantity_col}` + %s 
                WHERE `{barcode_col}` = %s
            """, (order['quantity'], order['barcode']))
            
            if cursor.rowcount == 0:
                conn.rollback()
                return jsonify({
                    'success': False,
                    'message': 'Failed to restore inventory: product not found'
                }), 400

        # Create audit log (if you have an audit table)
        try:
            cursor.execute("""
                INSERT INTO order_audit_log (order_id, action, reason, performed_at)
                VALUES (%s, %s, %s, NOW())
            """, (order_id, 'DELETE', reason))
        except:
            # If audit table doesn't exist, continue without auditing
            pass

        # Delete the order
        cursor.execute("DELETE FROM orders WHERE order_id = %s", (order_id,))
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Order deleted successfully',
            'inventory_restored': order['status'] == 'confirmed'
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


@app.route('/security-settings')
@admin_required
def security_settings():
    """Security settings page"""
    return render_template('security-settings.html')


# admin-login.html related endpoint and the function..
@app.route('/admin/signup', methods=['POST'])
@admin_required
def admin_signup():
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()

        if not name or not email or not password:
            return jsonify({'success': False, 'message': 'All fields are required'}), 400

        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if email already exists
        cursor.execute("SELECT admin_id FROM admin_users WHERE admin_email = %s", (email,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Email already registered'}), 400

        # Create admin account (in production, hash the password!)
        cursor.execute("""
            INSERT INTO admin_users (admin_name, admin_email, admin_password) 
            VALUES (%s, %s, %s)
        """, (name, email, password))  # In production, use: generate_password_hash(password)

        conn.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Admin account created successfully!',
            'admin_id': cursor.lastrowid
        })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Error creating account: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# admin-login.html page related endpoint and function Okay.
@app.route('/admin/login', methods=['POST'])
@limiter.limit("5 per minute")
def admin_login():
    """Admin login with MFA support"""
    conn = None
    cursor = None
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()

        if not email or not password:
            return jsonify({'success': False, 'message': 'Email and password required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Check credentials
        cursor.execute("""
            SELECT admin_id, admin_name, admin_email, admin_password 
            FROM admin_users 
            WHERE admin_email = %s AND is_active = TRUE
        """, (email,))
        
        admin = cursor.fetchone()

        if admin and admin['admin_password'] == password:
            # Check if MFA is enabled for this admin
            cursor.execute("""
                SELECT * FROM user_mfa 
                WHERE user_id = %s AND user_type = 'admin' AND is_mfa_enabled = TRUE
            """, (admin['admin_id'],))
            
            mfa_enabled = cursor.fetchone()
            
            if mfa_enabled:
                # MFA REQUIRED - Create session and send OTP
                mfa_result = simple_mfa.create_mfa_session(admin['admin_id'], 'admin')
                
                if mfa_result['success']:
                    # Send OTP via email
                    email_sent = simple_mfa.send_email_otp(
                        admin['admin_email'], 
                        mfa_result['otp_code'], 
                        admin['admin_name']
                    )
                    
                    if email_sent:
                        return jsonify({
                            'success': True,
                            'message': 'Verification code sent to your email',
                            'mfa_required': True,
                            'session_token': mfa_result['session_token'],
                            'user_email': admin['admin_email']
                        })
                    else:
                        return jsonify({'success': False, 'message': 'Failed to send verification code'}), 500
                else:
                    return jsonify({'success': False, 'message': 'MFA setup failed'}), 500
            else:
                # NO MFA - Direct login
                session['admin_logged_in'] = True
                session['admin_user'] = admin['admin_name']
                session['admin_email'] = admin['admin_email']
                session['admin_id'] = admin['admin_id']
                
                return jsonify({
                    'success': True, 
                    'message': 'Login successful',
                    'user': admin['admin_name'],
                    'admin_id': admin['admin_id'],
                    'mfa_required': False
                })
        
        return jsonify({'success': False, 'message': 'Invalid email or password'}), 401
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Login error: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# orders.html related endpoint and function
@app.route('/api/find-customer', methods=['GET'])
@user_required
def find_customer():
    search_term = request.args.get('q', '').strip().lower()
    if not search_term:
        return jsonify({'success': False, 'message': 'Search term is required'}), 400

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Search by emailid1 or emailid2 (case-insensitive) in customers table
        cursor.execute("""
            SELECT customer_id, customer_name, emailid1, emailid2, mobile1
            FROM customers 
            WHERE LOWER(emailid1) = %s OR LOWER(emailid2) = %s
            LIMIT 1
        """, (search_term, search_term))

        customer = cursor.fetchone()

        if customer:
            return jsonify({
                'success': True,
                'customer': {
                    'customer_id': customer['customer_id'],
                    'customer_name': customer['customer_name'],
                    'email': customer['emailid1'] or customer['emailid2'],
                    'mobile': customer['mobile1'] or 'No mobile'
                }
            }), 200
        else:
            return jsonify({
                'success': False,
                'message': 'Customer not found in customers table'
            }), 404

    except Error as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


@app.route('/admin/users-with-orders', methods=['GET'])
@admin_required
def get_users_with_orders():
    """Dynamically get all users who have placed orders"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # DYNAMIC: Get all possible order statuses from the database
        cursor.execute("SHOW COLUMNS FROM orders LIKE 'status'")
        status_result = cursor.fetchone()
        status_enum = status_result['Type'] if status_result else ""
        
        # Extract status values dynamically from ENUM definition
        status_values = []
        if 'enum' in status_enum:
            status_values = [s.strip("'") for s in status_enum.split('enum(')[1].rstrip(')').split(',')]
        else:
            # Fallback to common statuses if not ENUM
            status_values = ['pending', 'confirmed', 'shipped', 'delivered', 'cancelled']
        
        # 🔥 FIX: Use CAST to ensure numeric addition instead of string concatenation
        status_counts = []
        for status in status_values:
            status_counts.append(f"CAST(SUM(CASE WHEN o.status = '{status}' THEN 1 ELSE 0 END) AS UNSIGNED) as {status}_orders")
        
        status_counts_sql = ", ".join(status_counts)
        
        # DYNAMIC: Main query - gets customers with their order statistics
        query = f"""
            SELECT 
                c.customer_id,
                c.customer_name,
                COALESCE(c.emailid1, c.emailid2, 'No Email') as email,
                c.mobile1,
                CAST(COUNT(o.order_id) AS UNSIGNED) as total_orders,
                {status_counts_sql},
                MAX(o.created_at) as last_order_date
            FROM customers c
            INNER JOIN orders o ON c.customer_id = o.customer_id
            GROUP BY c.customer_id, c.customer_name, c.emailid1, c.emailid2, c.mobile1
            HAVING total_orders > 0
            ORDER BY total_orders DESC, last_order_date DESC
        """
        
        cursor.execute(query)
        users = cursor.fetchall()
        
        # DYNAMIC: Format response - CHANGED: Use email as primary identifier
        formatted_users = []
        for user in users:
            user_data = {
                'customer_id': user['customer_id'],
                'customer_name': user['customer_name'],  # Keep for reference
                'email': user['email'],  # Now using email as primary identifier
                'mobile': user['mobile1'] or 'No Mobile',
                'total_orders': int(user['total_orders']),  # 🔥 Ensure integer
                'last_order_date': user['last_order_date'].isoformat() if user['last_order_date'] else None,
                'status_breakdown': {}
            }
            
            # DYNAMIC: Add status counts with integer conversion
            for status in status_values:
                status_key = f"{status}_orders"
                user_data['status_breakdown'][status] = int(user.get(status_key, 0))  # 🔥 Convert to int
            
            formatted_users.append(user_data)
        
        return jsonify({
            'success': True,
            'users': formatted_users,
            'total_users': len(users),
            'available_statuses': status_values
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/admin/user-orders/<int:customer_id>', methods=['GET'])
@admin_required
def get_orders_by_user(customer_id):
    """Dynamically get all orders for a specific user"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # DYNAMIC: Get customer details
        cursor.execute("""
            SELECT customer_name, emailid1, emailid2, mobile1 
            FROM customers WHERE customer_id = %s
        """, (customer_id,))
        customer = cursor.fetchone()
        
        if not customer:
            return jsonify({'success': False, 'message': 'User not found'}), 404
        
        # DYNAMIC: Get all orders for this user
        cursor.execute("""
            SELECT order_id, barcode, quantity, value, price_method, status, created_at
            FROM orders 
            WHERE customer_id = %s 
            ORDER BY created_at DESC
        """, (customer_id,))
        
        orders = cursor.fetchall()
        
        # DYNAMIC: Format orders with product names
        formatted_orders = []
        for order in orders:
            # Get product name from inventories table
            product_name = "Unknown Product"
            try:
                # Get ItemMaster column names dynamically
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
                barcode_col_result = cursor.fetchone()
                barcode_col = barcode_col_result['SystemName'] if barcode_col_result else 'BarcodeNo'
                
                cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1 LIMIT 1")
                itemmaster_result = cursor.fetchone()
                
                if itemmaster_result:
                    itemmaster_col = itemmaster_result['SystemName']
                    # Get product name from inventories
                    cursor.execute(f"SELECT `{itemmaster_col}` FROM inventories WHERE `{barcode_col}` = %s LIMIT 1", (order['barcode'],))
                    product_result = cursor.fetchone()
                    if product_result:
                        product_name = product_result[itemmaster_col] or "Unknown Product"
            except Exception as e:
                print(f"Error getting product name: {str(e)}")
                product_name = "Unknown Product"
            
            formatted_orders.append({
                'order_id': order['order_id'],
                'barcode': order['barcode'],
                'product_name': product_name,  # NEW: Add product name
                'quantity': order['quantity'],
                'value': float(order['value']) if order['value'] else 0,
                'price_method': order['price_method'],
                'status': order['status'],
                'created_at': order['created_at'].isoformat() if order['created_at'] else None
            })
        
        return jsonify({
            'success': True,
            'customer': {
                'customer_id': customer_id,
                'customer_name': customer['customer_name'],
                'email': customer['emailid1'] or customer['emailid2'] or 'No Email',
                'mobile': customer['mobile1'] or 'No Mobile'
            },
            'orders': formatted_orders,
            'total_orders': len(orders)
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/inventory-count', methods=['GET'])
@user_required
def get_inventory_count():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    cursor = conn.cursor()
    try:
        # Fast COUNT query - doesn't load all data
        cursor.execute("SELECT COUNT(*) as total_count FROM inventories")
        result = cursor.fetchone()
        count = result[0] if result else 0
        
        return jsonify({
            'success': True,
            'total_count': count
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error counting inventory: {str(e)}'
        }), 500
    finally:
        cursor.close()
        conn.close()

# Add these dashboard endpoints to your app.py
@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/dashboard/summary')
@user_required
def dashboard_summary():
    # Get period parameter
    period = request.args.get('period', 'today')
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    # FIX 1: Use dictionary cursor and buffered
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    try:
        # Dynamic date filters based on period
        date_filters = {
            'today': "DATE(o.created_at) = CURDATE()",
            'week': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
            'month': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
        }
        
        date_filter = date_filters.get(period, date_filters['today'])
        
        # 1. Orders Summary for the selected period
        cursor.execute(f"""
            SELECT 
                COUNT(*) as orders_count, 
                COALESCE(SUM(o.value), 0) as revenue,
                COALESCE(AVG(o.value), 0) as avg_order_value
            FROM orders o
            WHERE {date_filter}
        """)
        period_stats = cursor.fetchone()
        
        # FIX 2: Consume all results
        cursor.fetchall()
        
        orders_count = period_stats['orders_count'] if period_stats else 0
        revenue = float(period_stats['revenue']) if period_stats and period_stats['revenue'] else 0.0
        avg_order_value = float(period_stats['avg_order_value']) if period_stats and period_stats['avg_order_value'] else 0.0

        # 2. Total Customers (always all time)
        cursor.execute("SELECT COUNT(*) as total_customers FROM customers")
        total_customers_result = cursor.fetchone()
        cursor.fetchall()  # Consume
        
        total_customers = total_customers_result['total_customers'] if total_customers_result else 0

        # 3. Inventory Value (always current) - FIX THIS SECTION
        inventory_value = 0.0
        try:
            # Check if inventories table exists
            cursor.execute("SHOW TABLES LIKE 'inventories'")
            table_exists = cursor.fetchone()
            cursor.fetchall()  # Consume results
            
            if table_exists:
                # Get price columns
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'ItemPrice' AND Active = 1")
                price_results = cursor.fetchall()
                cursor.fetchall()  # Consume
                
                price_columns = [row['SystemName'] for row in price_results] if price_results else []
                
                # Get quantity column
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1")
                quantity_result = cursor.fetchone()
                cursor.fetchall()  # Consume
                
                quantity_col = quantity_result['SystemName'] if quantity_result else 'Qty'
                
                if price_columns:
                    price_col = None
                    for col in price_columns:
                        if 'MRP' in col.upper():
                            price_col = col
                            break
                    if not price_col and price_columns:
                        price_col = price_columns[0]
                    
                    if price_col:
                        # Check if column exists
                        cursor.execute(f"SHOW COLUMNS FROM inventories LIKE '{price_col}'")
                        col_exists = cursor.fetchone()
                        cursor.fetchall()  # Consume
                        
                        if col_exists:
                            cursor.execute(f"""
                                SELECT COALESCE(SUM(`{price_col}` * `{quantity_col}`), 0) as inv_value
                                FROM inventories 
                                WHERE `{quantity_col}` > 0
                            """)
                            inv_value_result = cursor.fetchone()
                            cursor.fetchall()  # Consume
                            
                            inventory_value = float(inv_value_result['inv_value']) if inv_value_result and inv_value_result['inv_value'] else 0.0
        except Exception as inv_error:
            print(f"Inventory value calculation warning: {inv_error}")
            inventory_value = 0.0

        return jsonify({
            'success': True,
            'summary': {
                'orders_count': orders_count,
                'revenue': revenue,
                'avg_order_value': avg_order_value,
                'total_customers': total_customers,
                'inventory_value': inventory_value,
                'period': period
            }
        }), 200
        
    except Exception as e:
        print(f"Dashboard summary error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error generating dashboard summary: {str(e)}'
        }), 500
    finally:
        # FIX 3: Always consume any remaining results
        try:
            cursor.fetchall()
        except:
            pass
            
        cursor.close()
        conn.close()

@app.route('/api/dashboard/inventory-alerts')
@user_required
def inventory_alerts():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = None
    try:
        cursor = conn.cursor(buffered=True, dictionary=True)
        
        # Get dynamic column names
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1")
        quantity_result = cursor.fetchone()
        if not quantity_result:
            return jsonify({'success': False, 'message': 'Quantity configuration not found'}), 400
        quantity_col = quantity_result['SystemName']
        
        # Clear results
        while cursor.nextset():
            pass
        
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1")
        barcode_result = cursor.fetchone()
        if not barcode_result:
            return jsonify({'success': False, 'message': 'Barcode configuration not found'}), 400
        barcode_col = barcode_result['SystemName']
        
        # Clear results
        while cursor.nextset():
            pass

        # Get ItemMaster column names dynamically - FIX THIS PART
        cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1")
        itemmaster_configs = cursor.fetchall()
        
        # Clear results
        while cursor.nextset():
            pass

        # Build dynamic SELECT for ItemMaster columns - CHECK IF COLUMN EXISTS
        itemmaster_selects = []
        itemmaster_columns = {}
        
        # First, get all columns that exist in inventories table
        cursor.execute("SHOW COLUMNS FROM inventories")
        existing_columns = [row['Field'] for row in cursor.fetchall()]
        
        while cursor.nextset():
            pass
        
        for config in itemmaster_configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            
            # CHECK if column exists in inventories table
            if system_name in existing_columns:
                itemmaster_selects.append(f"i.`{system_name}` as `{display_name}`")
                itemmaster_columns[system_name] = display_name
            else:
                print(f"⚠️ Column '{system_name}' not found in inventories table, skipping...")

        itemmaster_select_sql = ", ".join(itemmaster_selects) if itemmaster_selects else "'' as no_itemmaster"

        # Get low stock (less than 20) and out of stock items with ItemMaster details
        query = f"""
            SELECT 
                i.`{barcode_col}` as barcode, 
                i.`{quantity_col}` as quantity,
                {itemmaster_select_sql}
            FROM inventories i
            WHERE i.`{quantity_col}` < 20
            ORDER BY i.`{quantity_col}` ASC
        """
        
        cursor.execute(query)
        alerts = cursor.fetchall()
        
        # Categorize alerts
        low_stock = [item for item in alerts if 0 < item['quantity'] < 20]
        out_of_stock = [item for item in alerts if item['quantity'] == 0]
        
        return jsonify({
            'success': True,
            'alerts': {
                'low_stock': low_stock,
                'out_of_stock': out_of_stock,
                'total_alerts': len(alerts),
                'itemmaster_columns': list(itemmaster_columns.values())
            }
        }), 200
        
    except Exception as e:
        print(f"Inventory alerts error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error generating inventory alerts: {str(e)}'
        }), 500
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            conn.close()

@app.route('/api/dashboard/top-products')
@user_required
def top_products():
    """ALL products including 0 sales, sorted highest to lowest"""
    period = request.args.get('period', 'today')
    
    date_ranges = {
        'today': "DATE(o.created_at) = CURDATE()",
        'week': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
        'month': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
    }
    
    if period not in date_ranges:
        period = 'today'
    
    date_condition = date_ranges[period]
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = None
    try:
        cursor = conn.cursor(buffered=True, dictionary=True)
        
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1")
        barcode_result = cursor.fetchone()
        if not barcode_result:
            return jsonify({'success': False, 'message': 'Barcode configuration not found'}), 400
        barcode_col = barcode_result['SystemName']
        
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1")
        quantity_result = cursor.fetchone()
        quantity_col = quantity_result['SystemName'] if quantity_result else 'Qty'
        
        cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1")
        itemmaster_configs = cursor.fetchall()
        
        itemmaster_selects = []
        itemmaster_columns = {}
        for config in itemmaster_configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            itemmaster_selects.append(f"ANY_VALUE(i.`{system_name}`) as `{display_name}`")  # ✅ ANY_VALUE()
            itemmaster_columns[system_name] = display_name

        itemmaster_select_sql = ", ".join(itemmaster_selects) if itemmaster_selects else "'' as no_itemmaster"

        # ✅ FIXED with ANY_VALUE() for all non-aggregated columns
        query = f"""
            SELECT 
                i.`{barcode_col}` as barcode,
                ANY_VALUE(i.`{quantity_col}`) as current_stock,  # ✅ ANY_VALUE()
                COALESCE(SUM(o.quantity), 0) as total_sold,
                COALESCE(SUM(o.value), 0) as total_revenue,
                {itemmaster_select_sql}
            FROM inventories i
            LEFT JOIN orders o ON i.`{barcode_col}` = o.barcode 
                AND {date_condition}
            GROUP BY i.`{barcode_col}`  # ✅ Only group by barcode
            ORDER BY total_sold DESC, barcode ASC
        """
        
        cursor.execute(query)
        all_products = cursor.fetchall()
        
        return jsonify({
            'success': True,
            'top_products': all_products,
            'itemmaster_columns': list(itemmaster_columns.values()),
            'period': period,
            'total_count': len(all_products),
            'description': f'ALL products (including 0 sales) sorted by highest to lowest sales'
        }), 200
        
    except Exception as e:
        print(f"Top products error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/api/dashboard/slow-moving-products')
@user_required
def slow_moving_products():
    """ALL products, with 0 sales at bottom, sorted by sales lowest to highest"""
    period = request.args.get('period', 'today')
    
    date_ranges = {
        'today': "DATE(o.created_at) = CURDATE()",
        'week': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
        'month': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
    }
    
    if period not in date_ranges:
        period = 'today'
    
    date_condition = date_ranges[period]
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = None
    try:
        cursor = conn.cursor(buffered=True, dictionary=True)
        
        # Get dynamic column names
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1")
        barcode_result = cursor.fetchone()
        
        if not barcode_result:
            return jsonify({'success': False, 'message': 'Barcode configuration not found'}), 400
        barcode_col = barcode_result['SystemName']
        
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1")
        quantity_result = cursor.fetchone()
        quantity_col = quantity_result['SystemName'] if quantity_result else 'Qty'
        
        # Get ItemMaster columns
        cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1")
        itemmaster_configs = cursor.fetchall()

        itemmaster_selects = []
        itemmaster_columns = {}
        for config in itemmaster_configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            itemmaster_selects.append(f"ANY_VALUE(i.`{system_name}`) as `{display_name}`")
            itemmaster_columns[system_name] = display_name

        itemmaster_select_sql = ", ".join(itemmaster_selects) if itemmaster_selects else "'' as no_itemmaster"

        # Build the query
        query = f"""
            SELECT 
                i.`{barcode_col}` as barcode,
                ANY_VALUE(i.`{quantity_col}`) as current_stock,
                COALESCE(SUM(o.quantity), 0) as total_sold,
                COALESCE(SUM(o.value), 0) as total_revenue,
                {itemmaster_select_sql}
            FROM inventories i
            LEFT JOIN orders o ON i.`{barcode_col}` = o.barcode 
                AND {date_condition}
            GROUP BY i.`{barcode_col}`
            ORDER BY 
                CASE WHEN COALESCE(SUM(o.quantity), 0) = 0 THEN 1 ELSE 0 END,
                COALESCE(SUM(o.quantity), 0) ASC,
                barcode ASC
        """
        
        cursor.execute(query)
        slow_products = cursor.fetchall()
        
        return jsonify({
            'success': True,
            'slow_moving_products': slow_products,
            'itemmaster_columns': list(itemmaster_columns.values()),
            'period': period,
            'total_count': len(slow_products),
            'description': f'ALL products, sorted by lowest sales first, with 0 sales at bottom'
        }), 200
        
    except Exception as e:
        print(f"ERROR in slow moving products: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}',
            'details': traceback.format_exc()  # Include full traceback for debugging
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/api/dashboard/non-moving-products')
@user_required
def non_moving_products():
    """ONLY products WITH ZERO sales in period"""
    period = request.args.get('period', 'today')
    
    # ACTUAL time periods (SAME as above)
    date_ranges = {
        'today': "DATE(o.created_at) = CURDATE()",  # Actual 1 day
        'week': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",  # 7 days
        'month': "o.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"  # 30 days
    }
    
    if period not in date_ranges:
        period = 'today'
    
    date_condition = date_ranges[period]
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = None
    try:
        cursor = conn.cursor(buffered=True, dictionary=True)
        
        # Get dynamic column names
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1")
        barcode_result = cursor.fetchone()
        if not barcode_result:
            return jsonify({'success': False, 'message': 'Barcode configuration not found'}), 400
        barcode_col = barcode_result['SystemName']
        
        cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Quantity' AND Active = 1")
        quantity_result = cursor.fetchone()
        quantity_col = quantity_result['SystemName'] if quantity_result else 'Qty'
        
        # Get ItemMaster columns
        cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1")
        itemmaster_configs = cursor.fetchall()
        
        # Build dynamic SELECT
        itemmaster_selects = []
        itemmaster_columns = {}
        for config in itemmaster_configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            itemmaster_selects.append(f"i.`{system_name}` as `{display_name}`")
            itemmaster_columns[system_name] = display_name

        itemmaster_select_sql = ", ".join(itemmaster_selects) if itemmaster_selects else "'' as no_itemmaster"

        # ✅ ONLY products with ZERO sales in period
        query = f"""
            SELECT 
                i.`{barcode_col}` as barcode,
                i.`{quantity_col}` as current_stock,
                {itemmaster_select_sql}
            FROM inventories i
            LEFT JOIN orders o ON i.`{barcode_col}` = o.barcode 
                              AND {date_condition}
            WHERE i.`{quantity_col}` > 0  -- Has stock
              AND o.order_id IS NULL      -- 🎯 ZERO sales
            ORDER BY i.`{quantity_col}` DESC  -- Most stock first
        """
        
        cursor.execute(query)
        non_moving = cursor.fetchall()
        
        return jsonify({
            'success': True,
            'non_moving_products': non_moving,
            'itemmaster_columns': list(itemmaster_columns.values()),
            'period': period,
            'total_count': len(non_moving),
            'description': f'Products with zero sales in the {period} period'
        }), 200
        
    except Exception as e:
        print(f"Non moving products error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Dynamic permissions endpoint
@app.route('/api/user/permissions', methods=['GET'])
def get_user_permissions():
    """Get dynamic permissions based on user type"""
    try:
        user_type = request.args.get('user_type', 'user')
        
        # Check if admin is logged in
        if user_type == 'admin' and session.get('admin_logged_in'):
            return jsonify({
                'success': True,
                'permissions': {
                    'can_view_quick_actions': True,
                    'allowed_pages': ['/', '/dashboard', '/configurations', '/dataimport', '/inventory', '/customers', '/orders', '/admin-dashboard', '/user-registration', '/admin-registration'],
                    'allowed_quick_actions': ['create_order', 'manage_inventory', 'add_user', 'view_reports']
                }
            })
        
        # Check if user is logged in  
        elif user_type == 'user' and session.get('user_logged_in'):
            return jsonify({
                'success': True,
                'permissions': {
                    'can_view_quick_actions': True,
                    'allowed_pages': ['/', '/dashboard', '/inventory', '/orders'],
                    'allowed_quick_actions': ['create_order', 'manage_inventory', 'view_reports']
                }
            })
        
        else:
            return jsonify({
                'success': True,
                'permissions': {
                    'can_view_quick_actions': False,
                    'allowed_pages': ['/'],
                    'allowed_quick_actions': []
                }
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error getting permissions: {str(e)}'
        }), 500
        

# User Login endpoint
@app.route('/user/login', methods=['POST'])
@limiter.limit("5 per minute")
def user_login():
    """User login with MFA support"""
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()

        if not email or not password:
            return jsonify({'success': False, 'message': 'Email and password required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Check user credentials using email
        cursor.execute("""
            SELECT user_id, user_name, user_password, user_email
            FROM users 
            WHERE LOWER(user_email) = %s AND is_active = TRUE
        """, (email,))
        
        user = cursor.fetchone()

        if user and user['user_password'] == password:
            # Check if MFA is enabled for this user
            cursor.execute("""
                SELECT * FROM user_mfa 
                WHERE user_id = %s AND user_type = 'user' AND is_mfa_enabled = TRUE
            """, (user['user_id'],))
            
            mfa_enabled = cursor.fetchone()
            
            if mfa_enabled:
                # MFA REQUIRED - Create session and send OTP
                mfa_result = simple_mfa.create_mfa_session(user['user_id'], 'user')
                
                if mfa_result['success']:
                    # Send OTP via email
                    email_sent = simple_mfa.send_email_otp(
                        user['user_email'], 
                        mfa_result['otp_code'], 
                        user['user_name']
                    )
                    
                    if email_sent:
                        return jsonify({
                            'success': True,
                            'message': 'Verification code sent to your email',
                            'mfa_required': True,
                            'session_token': mfa_result['session_token'],
                            'user_email': user['user_email']
                        })
                    else:
                        return jsonify({'success': False, 'message': 'Failed to send verification code'}), 500
                else:
                    return jsonify({'success': False, 'message': 'MFA setup failed'}), 500
            else:
                # NO MFA - Direct login
                session['user_logged_in'] = True
                session['user_id'] = user['user_id']
                session['user_name'] = user['user_name']
                session['user_type'] = 'user'
                session['user_email'] = email
                
                return jsonify({
                    'success': True, 
                    'message': 'Login successful',
                    'user_id': user['user_id'],
                    'user_name': user['user_name'],
                    'email': email,
                    'mfa_required': False
                })
        
        return jsonify({'success': False, 'message': 'Invalid email or password'}), 401
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Login error: {str(e)}'}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# User logout endpoint
@app.route('/user/logout', methods=['POST'])
def user_logout():
    """User logout endpoint"""
    session.pop('user_logged_in', None)
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_type', None)
    session.pop('user_email', None)
    return jsonify({'success': True, 'message': 'Logout successful'})

# User authentication check endpoint
@app.route('/user/check-auth', methods=['GET'])
def check_user_auth():
    """Check if user is logged in"""
    return jsonify({
        'authenticated': session.get('user_logged_in', False),
        'user_type': session.get('user_type', ''),
        'user_name': session.get('user_name', ''),
        'user_id': session.get('user_id', '')
    })

# User login page route
@app.route('/user-login')
def user_login_page():
    """Serve the user login page"""
    return render_template('user-login.html')

# Gateway page route
@app.route('/gateway')
def gateway():
    """Serve the gateway page"""
    return render_template('gateway.html')

# User Registration API Endpoint
@app.route('/api/register-user', methods=['POST'])
@admin_required
def register_user():
    try:
        data = request.get_json()
        print("Received user registration data:", data)
        
        # Validate required fields
        required_fields = ['user_name', 'user_email', 'user_password']
        for field in required_fields:
            if field not in data or not data[field].strip():
                return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
        
        user_name = data['user_name'].strip()
        user_email = data['user_email'].strip().lower()
        user_password = data['user_password'].strip()
        
        # Validate email format
        if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', user_email):
            return jsonify({'success': False, 'message': 'Invalid email format'}), 400
        
        # Validate password length
        if len(user_password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters long'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if email already exists
        cursor.execute("SELECT user_id FROM users WHERE user_email = %s", (user_email,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Email already registered'}), 400
        
        # Insert new user
        insert_query = """
            INSERT INTO users (user_name, user_email, user_password)
            VALUES (%s, %s, %s)
        """
        
        cursor.execute(insert_query, (user_name, user_email, user_password))
        conn.commit()
        user_id = cursor.lastrowid
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'User registered successfully! User ID: {user_id}',
            'user_id': user_id
        })
        
    except Error as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn and conn.is_connected():
            conn.close()

# Admin Registration API Endpoint
@app.route('/api/register-admin', methods=['POST'])
@admin_required
def register_admin():
    try:
        data = request.get_json()
        print("Received admin registration data:", data)
        
        # Validate required fields
        required_fields = ['admin_name', 'admin_email', 'admin_password']
        for field in required_fields:
            if field not in data or not data[field].strip():
                return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
        
        admin_name = data['admin_name'].strip()
        admin_email = data['admin_email'].strip().lower()
        admin_password = data['admin_password'].strip()
        
        # Validate email format
        if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', admin_email):
            return jsonify({'success': False, 'message': 'Invalid email format'}), 400
        
        # Validate password length (stricter for admin)
        if len(admin_password) < 8:
            return jsonify({'success': False, 'message': 'Admin password must be at least 8 characters long'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if email already exists
        cursor.execute("SELECT admin_id FROM admin_users WHERE admin_email = %s", (admin_email,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Email already registered as admin'}), 400
        
        # Insert new admin
        insert_query = """
            INSERT INTO admin_users (admin_name, admin_email, admin_password)
            VALUES (%s, %s, %s)
        """
        
        cursor.execute(insert_query, (admin_name, admin_email, admin_password))
        conn.commit()
        admin_id = cursor.lastrowid
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'Admin registered successfully! Admin ID: {admin_id}',
            'admin_id': admin_id
        })
        
    except Error as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn and conn.is_connected():
            conn.close()


@app.route('/admin/user-order-relationships', methods=['GET'])
@admin_required
def get_user_order_relationships():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Complex query joining users, admins, customers, and orders
        cursor.execute("""
            SELECT 
                o.order_id,
                o.barcode,
                o.quantity,
                o.value,
                o.price_method,
                o.status,
                o.created_at,
                
                -- Customer details
                c.customer_id,
                c.customer_name,
                c.emailid1 as customer_email,
                c.mobile1 as customer_mobile,
                
                -- User who placed order (if any)
                u.user_id as placed_by_user_id,
                u.user_name as placed_by_user_name,
                u.user_email as placed_by_user_email,
                
                -- Admin who placed order (if any)  
                a.admin_id as placed_by_admin_id,
                a.admin_name as placed_by_admin_name,
                a.admin_email as placed_by_admin_email,
                
                -- Determine who placed the order
                CASE 
                    WHEN o.placed_by_user_id IS NOT NULL THEN 'user'
                    WHEN o.placed_by_admin_id IS NOT NULL THEN 'admin'
                    ELSE 'unknown'
                END as placed_by_type,
                
                -- Get the actual placer's name and email
                COALESCE(u.user_name, a.admin_name) as placed_by_name,
                COALESCE(u.user_email, a.admin_email) as placed_by_email
                
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN users u ON o.placed_by_user_id = u.user_id
            LEFT JOIN admin_users a ON o.placed_by_admin_id = a.admin_id
            ORDER BY o.created_at DESC
        """)
        
        relationships = cursor.fetchall()
        
        # Add product names to each relationship
        formatted_relationships = []
        for rel in relationships:
            # Get product name from inventories table
            product_name = "Unknown Product"
            try:
                # Get ItemMaster column names dynamically
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
                barcode_col_result = cursor.fetchone()
                barcode_col = barcode_col_result['SystemName'] if barcode_col_result else 'BarcodeNo'
                
                cursor.execute("SELECT SystemName, DisplayName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1 LIMIT 1")
                itemmaster_result = cursor.fetchone()
                
                if itemmaster_result:
                    itemmaster_col = itemmaster_result['SystemName']
                    # Get product name from inventories
                    cursor.execute(f"SELECT `{itemmaster_col}` FROM inventories WHERE `{barcode_col}` = %s LIMIT 1", (rel['barcode'],))
                    product_result = cursor.fetchone()
                    if product_result:
                        product_name = product_result[itemmaster_col] or "Unknown Product"
            except Exception as e:
                print(f"Error getting product name: {str(e)}")
                product_name = "Unknown Product"
            
            # Create a new relationship object with product name
            rel_with_product = dict(rel)
            rel_with_product['product_name'] = product_name
            formatted_relationships.append(rel_with_product)
        
        return jsonify({
            'success': True,
            'relationships': formatted_relationships,
            'total_relationships': len(relationships)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/admin/user-orders/<int:user_id>')
@admin_required
def user_orders(user_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get user details
        user_query = """
        SELECT customer_id, customer_name, email, mobile 
        FROM customers WHERE customer_id = %s
        """
        cursor.execute(user_query, (user_id,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'success': False, 'message': 'User not found'})
        
        # Get orders placed by this user
        orders_query = """
        SELECT o.order_id, o.barcode, o.quantity, o.value, o.price_method, o.status, o.created_at,
               c.customer_name, c.email as customer_email, c.mobile as customer_mobile
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        WHERE o.placed_by_id = %s AND o.placed_by_type = 'user'
        ORDER BY o.created_at DESC
        """
        cursor.execute(orders_query, (user_id,))
        orders = cursor.fetchall()
        
        return jsonify({
            'success': True,
            'user': {
                'customer_id': user['customer_id'],
                'customer_name': user['customer_name'],
                'email': user['email'],
                'mobile': user['mobile']
            },
            'orders': orders
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cursor.close()
        conn.close()


@app.route('/api/user/my-orders', methods=['GET'])
@user_required
def get_user_orders():
    conn = None
    cursor = None
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'message': 'User not authenticated'}), 401

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get orders placed by this user with customer and product details
        cursor.execute("""
            SELECT 
                o.order_id,
                o.barcode,
                o.quantity,
                o.value,
                o.price_method,
                o.status,
                o.created_at,
                -- Customer details
                c.customer_id,
                c.customer_name,
                c.emailid1 as customer_email,
                c.mobile1 as customer_mobile
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.placed_by_user_id = %s
            ORDER BY o.created_at DESC
        """, (user_id,))
        
        orders = cursor.fetchall()
        
        # Add product names dynamically
        formatted_orders = []
        for order in orders:
            # Get product name from inventories table DYNAMICALLY
            product_name = "Unknown Product"
            try:
                # Get column names from configurations DYNAMICALLY
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
                barcode_col_result = cursor.fetchone()
                barcode_col = barcode_col_result['SystemName'] if barcode_col_result else 'BarcodeNo'
                
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1 LIMIT 1")
                itemmaster_result = cursor.fetchone()
                
                if itemmaster_result:
                    itemmaster_col = itemmaster_result['SystemName']
                    # Get product name from inventories DYNAMICALLY
                    cursor.execute(f"SELECT `{itemmaster_col}` FROM inventories WHERE `{barcode_col}` = %s LIMIT 1", (order['barcode'],))
                    product_result = cursor.fetchone()
                    if product_result:
                        product_name = product_result[itemmaster_col] or "Unknown Product"
            except Exception as e:
                print(f"Error getting product name: {str(e)}")
                product_name = "Unknown Product"
            
            formatted_orders.append({
                'order_id': order['order_id'],
                'barcode': order['barcode'],
                'product_name': product_name,
                'quantity': order['quantity'],
                'value': float(order['value']) if order['value'] else 0,
                'price_method': order['price_method'],
                'status': order['status'],
                'created_at': order['created_at'].isoformat() if order['created_at'] else None,
                'customer': {
                    'customer_id': order['customer_id'],
                    'customer_name': order['customer_name'] or 'Unknown Customer',
                    'customer_email': order['customer_email'] or 'No email',
                    'customer_mobile': order['customer_mobile'] or 'No mobile'
                }
            })
        
        return jsonify({
            'success': True,
            'orders': formatted_orders,
            'total_orders': len(orders),
            'user_id': user_id
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/my-personal-orders', methods=['GET'])
@user_required
def get_my_personal_orders():
    """Get orders placed by the currently logged-in person (user or admin)"""
    conn = None
    cursor = None
    try:
        user_id = session.get('user_id')
        admin_id = session.get('admin_id')
        admin_logged_in = session.get('admin_logged_in')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        if admin_logged_in:
            # Get orders placed by this specific admin
            cursor.execute("""
                SELECT 
                    o.order_id,
                    o.barcode,
                    o.quantity,
                    o.value,
                    o.price_method,
                    o.status,
                    o.created_at,
                    -- Customer details
                    c.customer_id,
                    c.customer_name,
                    c.emailid1 as customer_email,
                    c.mobile1 as customer_mobile
                FROM orders o
                LEFT JOIN customers c ON o.customer_id = c.customer_id
                WHERE o.placed_by_admin_id = %s
                ORDER BY o.created_at DESC
            """, (admin_id,))
        else:
            # Get orders placed by this specific user
            cursor.execute("""
                SELECT 
                    o.order_id,
                    o.barcode,
                    o.quantity,
                    o.value,
                    o.price_method,
                    o.status,
                    o.created_at,
                    -- Customer details
                    c.customer_id,
                    c.customer_name,
                    c.emailid1 as customer_email,
                    c.mobile1 as customer_mobile
                FROM orders o
                LEFT JOIN customers c ON o.customer_id = c.customer_id
                WHERE o.placed_by_user_id = %s
                ORDER BY o.created_at DESC
            """, (user_id,))
        
        orders = cursor.fetchall()
        
        # Add product names dynamically
        formatted_orders = []
        for order in orders:
            product_name = "Unknown Product"
            try:
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'Barcode' AND Active = 1 LIMIT 1")
                barcode_col_result = cursor.fetchone()
                barcode_col = barcode_col_result['SystemName'] if barcode_col_result else 'BarcodeNo'
                
                cursor.execute("SELECT SystemName FROM configurations WHERE ConfigName = 'ItemMaster' AND Active = 1 LIMIT 1")
                itemmaster_result = cursor.fetchone()
                
                if itemmaster_result:
                    itemmaster_col = itemmaster_result['SystemName']
                    cursor.execute(f"SELECT `{itemmaster_col}` FROM inventories WHERE `{barcode_col}` = %s LIMIT 1", (order['barcode'],))
                    product_result = cursor.fetchone()
                    if product_result:
                        product_name = product_result[itemmaster_col] or "Unknown Product"
            except Exception as e:
                print(f"Error getting product name: {str(e)}")
                product_name = "Unknown Product"
            
            formatted_orders.append({
                'order_id': order['order_id'],
                'barcode': order['barcode'],
                'product_name': product_name,
                'quantity': order['quantity'],
                'value': float(order['value']) if order['value'] else 0,
                'price_method': order['price_method'],
                'status': order['status'],
                'created_at': order['created_at'].isoformat() if order['created_at'] else None,
                'customer': {
                    'customer_id': order['customer_id'],
                    'customer_name': order['customer_name'] or 'Unknown Customer',
                    'customer_email': order['customer_email'] or 'No email',
                    'customer_mobile': order['customer_mobile'] or 'No mobile'
                }
            })
        
        return jsonify({
            'success': True,
            'orders': formatted_orders,
            'total_orders': len(orders),
            'user_type': 'admin' if admin_logged_in else 'user',
            'user_id': admin_id if admin_logged_in else user_id
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Database error: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/user-dashboard')
@user_required
def user_dashboard_page():
    """Serve the user dashboard page"""
    return render_template('user-dashboard.html')

@app.route('/admin/onboard-customer', methods=['POST'])
@admin_required
def onboard_customer():
    try:
        data = request.get_json()
        customer_name = data.get('customer_name')
        customer_email = data.get('customer_email')
        product_type = data.get('product_type', 'standard')
        
        if not customer_name or not customer_email:
            return jsonify({'success': False, 'message': 'Customer name and email required'}), 400
        
        conn = db_manager.get_main_connection()
        cursor = conn.cursor()
        
        # Check if customer exists
        cursor.execute("SELECT customer_id FROM customer_api_keys WHERE customer_email = %s", (customer_email,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Customer email already exists'}), 400
        
        # Generate API key and database
        from api_key_manager import generate_api_key, encrypt_data, hash_api_key
        api_key = generate_api_key()
        encrypted_key = encrypt_data(api_key)
        key_hash = hash_api_key(api_key)
        
        # Create unique database name
        db_name = f"cust_{customer_email.split('@')[0]}_{secrets.token_hex(4)}"
        db_name = db_name.replace('.', '_').lower()
        
        # Create customer database
        if not db_manager.create_customer_database(db_name):
            return jsonify({'success': False, 'message': 'Failed to create customer database'}), 500
        
        # Store customer info
        cursor.execute("""
            INSERT INTO customer_api_keys 
            (customer_name, customer_email, encrypted_api_key, original_key_hash, database_name, product_type)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (customer_name, customer_email, encrypted_key, key_hash, db_name, product_type))
        
        customer_id = cursor.lastrowid
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Customer onboarded successfully!',
            'customer_id': customer_id,
            'api_key': api_key,  # Give this to customer securely
            'database_name': db_name,
            'note': 'Save this API key securely - it cannot be retrieved again'
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Onboarding failed: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/admin/customer-list', methods=['GET'])
@admin_required
def get_customer_list():
    """Get list of all customers (Admin only)"""
    conn = db_manager.get_main_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT customer_id, customer_name, customer_email, database_name, 
                   product_type, is_active, created_at
            FROM customer_api_keys 
            ORDER BY created_at DESC
        """)
        customers = cursor.fetchall()
        
        return jsonify({
            'success': True,
            'customers': customers,
            'total_customers': len(customers)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

def get_software_customer_by_ip_port(ip_address, port_number):
    """Get SOFTWARE CUSTOMER database name based on IP address and port number"""
    conn = db_manager.get_main_connection()
    if not conn:
        return None
        
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT database_name, customer_name, customer_email
            FROM customer_api_keys 
            WHERE ip_address = %s AND port_number = %s AND is_active = TRUE
        """, (ip_address, port_number))
        
        result = cursor.fetchone()
        if result:
            print(f"✅ Found software customer: {result['customer_name']} -> Database: {result['database_name']}")
            return result['database_name']
        else:
            print(f"ℹ️  No software customer found for {ip_address}:{port_number}")
            return None
        
    except Exception as e:
        print(f"❌ Error getting software customer by IP/port: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

@app.before_request
def dynamic_port_based_routing():
    """Dynamic routing based on request port"""
    # Skip for static files
    if request.endpoint and 'static' not in request.endpoint:
        try:
            # Get current port from request
            current_port = request.environ.get('SERVER_PORT')
            
            if not current_port:
                return
            
            port = int(current_port)
            
            # Get main server port from environment
            main_port = int(os.getenv('FLASK_PORT', 5000))
            
            # If this is main port, use main database
            if port == main_port:
                if 'software_customer_database' in session:
                    session.pop('software_customer_database')
                return
            
            # Find which customer owns this port
            conn = db_manager.get_main_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT database_name, customer_name, customer_email, 
                       server_ip, assigned_port, access_url
                FROM customer_api_keys 
                WHERE assigned_port = %s AND is_active = TRUE
            """, (port,))
            
            customer = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if customer:
                # Set customer database in session
                session['software_customer_database'] = customer['database_name']
                session['customer_info'] = {
                    'name': customer['customer_name'],
                    'email': customer['customer_email'],
                    'port': customer['assigned_port'],
                    'access_url': customer['access_url']
                }
                
            else:
                # Clear any previous customer session
                if 'software_customer_database' in session:
                    session.pop('software_customer_database')
                if 'customer_info' in session:
                    session.pop('customer_info')
                print(f"⚠️  No customer assigned to port {port}")
                
        except Exception as e:
            print(f"❌ Error in dynamic port routing: {e}")

def clean_hsn_code(value):
    """Remove commas from HSN codes"""
    if value and isinstance(value, str):
        # Remove all commas and extra spaces
        cleaned = value.replace(',', '').strip()
        return cleaned
    return value
'''
@app.route('/api/inventories/grouped', methods=['GET', 'POST'])
@user_required
def get_grouped_inventories():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get parameters from request
        if request.method == 'GET':
            columns_param = request.args.get('columns', '')
            filters_param = request.args.get('filters', '{}')
            
            # Parse columns
            group_fields = [col.strip() for col in columns_param.split(',') if col.strip()]
            
            # Parse filters
            try:
                filters = json.loads(filters_param)
            except:
                filters = {}
                
        else:  # POST request
            data = request.json
            group_fields = data.get('group_fields', [])
            filters = data.get('filters', {})
        
        # If no columns specified, return empty
        if not group_fields:
            return jsonify({
                'success': True,
                'data': [],
                'message': 'No columns selected for grouping',
                'total_records': 0
            }), 200
        
        # 1. Load configurations
        cursor.execute("""
            SELECT 
                c.SystemName,
                c.DisplayName,
                c.ConfigName,
                c.SerialNo
            FROM configurations c
            WHERE c.Active = 1
            ORDER BY c.ConfigName, c.SerialNo
        """)
        configs = cursor.fetchall()
        
        # 2. Create mappings
        system_to_display = {}
        display_to_system = {}
        config_name_map = {}
        
        for config in configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            config_name = config['ConfigName']
            
            system_to_display[system_name] = display_name
            display_to_system[display_name] = system_name
            config_name_map[system_name] = config_name
        
        numeric_configs = ['Quantity', 'ItemPrice', 'ItemCalc'] 
        non_numeric_configs = ['ItemMaster', 'HSNCode', 'Barcode', 'WareHouse', 'WHLocation', 'ItemGroup'] 
        
        numeric_fields = []
        non_numeric_fields = []
        
        for display_field in group_fields:
            if display_field in display_to_system:
                system_field = display_to_system[display_field]
                config_name = config_name_map.get(system_field, '')
                
                if config_name in numeric_configs:
                    # DECIMAL fields: ItemCalc, ItemPrice, Quantity
                    numeric_fields.append({
                        'display': display_field,
                        'system': system_field,
                        'type': 'numeric',
                        'config': config_name
                    })
                else:
                    # VARCHAR fields: ItemMaster, HSNCode, Barcode, etc.
                    non_numeric_fields.append({
                        'display': display_field,
                        'system': system_field,
                        'type': 'non_numeric',
                        'config': config_name
                    })
        
        # If no non-numeric fields selected, we can't group
        if not non_numeric_fields:
            return jsonify({
                'success': False,
                'message': 'Please select at least one non-numeric field (like Product) for grouping'
            }), 400
        
        # 4. Build SELECT clause dynamically - CORRECTLY this time!
        select_parts = []
        
        # For NON-NUMERIC fields (VARCHAR): Use them directly, NO SUM()
        for field in non_numeric_fields:
            # VARCHAR fields should be in GROUP BY, not aggregated
            select_parts.append(f"`{field['system']}` AS `{field['display']}`")
        
        # For NUMERIC fields (DECIMAL): Use SUM()
        for field in numeric_fields:
            # DECIMAL fields should be summed
            select_parts.append(f"SUM(`{field['system']}`) AS `{field['display']}`")
        
        # 5. Build WHERE clause from filters
        where_conditions = []
        where_params = []
        
        for field_display, values in filters.items():
            if field_display in display_to_system:
                system_field = display_to_system[field_display]
                if isinstance(values, list) and values:
                    placeholders = ', '.join(['%s'] * len(values))
                    where_conditions.append(f"`{system_field}` IN ({placeholders})")
                    where_params.extend(values)
        
        # 6. Build GROUP BY clause - only for non-numeric fields
        group_by_parts = [f"`{field['system']}`" for field in non_numeric_fields]
        
        # 7. Construct final SQL query
        if not select_parts:
            return jsonify({
                'success': False,
                'message': 'No valid columns to select'
            }), 400
        
        select_clause = ', '.join(select_parts)
        query = f"SELECT {select_clause} FROM inventories"
        
        if where_conditions:
            query += f" WHERE {' AND '.join(where_conditions)}"
        
        if group_by_parts:
            query += f" GROUP BY {', '.join(group_by_parts)}"
        
        # Add ORDER BY first non-numeric field
        if non_numeric_fields:
            first_field = non_numeric_fields[0]['system']
            query += f" ORDER BY `{first_field}`"
     
        #print(f"Generated SQL: {query}")
        #print(f"Parameters: {where_params}")
        #print(f"Numeric fields to SUM: {[f['display'] for f in numeric_fields]}")
        #print(f"Non-numeric fields to GROUP BY: {[f['display'] for f in non_numeric_fields]}")


        if where_params:
            cursor.execute(query, where_params)
        else:
            cursor.execute(query)
        
        results = cursor.fetchall()
        
        # 9. Transform results to flat structure
        flat_results = []
        
        for row in results:
            flat_row = {}
            
            for key, value in row.items():
                flat_row[key] = value
            
            flat_results.append(flat_row)
        
        # 10. Also fetch some original data for filtering sidebar
        # Build simple query for original data
        original_select = []
        for config in configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            original_select.append(f"`{system_name}` AS `{display_name}`")
        
        if original_select:
            original_query = f"SELECT {', '.join(original_select)} FROM inventories LIMIT 1000"
            cursor.execute(original_query)
            original_data = cursor.fetchall()
        else:
            original_data = []
        
        return jsonify({
            'success': True,
            'data': flat_results,
            'original_data': original_data,  # For filtering sidebar
            'total_records': len(flat_results),
            'grouped_by': [field['display'] for field in non_numeric_fields],
            'summed_fields': [field['display'] for field in numeric_fields],
            'sql_query': query  # For debugging
        }), 200
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error: {str(e)}")
        print(f"Traceback: {error_details}")
        
        return jsonify({
            'success': False,
            'message': f'Error grouping data: {str(e)}',
            'error_details': error_details
        }), 500
    finally:
        cursor.close()
        conn.close()
'''

from datetime import datetime, date  # Add this import at the top of your file
from decimal import Decimal  # Make sure Decimal is imported

@app.route('/api/inventories/grouped', methods=['GET', 'POST'])
@user_required
def get_grouped_inventories():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get parameters from request
        if request.method == 'GET':
            columns_param = request.args.get('columns', '')
            filters_param = request.args.get('filters', '{}')
            
            # Parse columns
            group_fields = [col.strip() for col in columns_param.split(',') if col.strip()]
            
            # Parse filters
            try:
                filters = json.loads(filters_param)
            except:
                filters = {}
                
        else:  # POST request
            data = request.json
            group_fields = data.get('group_fields', [])
            filters = data.get('filters', {})
        
        # If no columns specified, return empty
        if not group_fields:
            return jsonify({
                'success': True,
                'data': [],
                'message': 'No columns selected for grouping',
                'total_records': 0
            }), 200
        
        # 1. Load configurations
        cursor.execute("""
            SELECT 
                c.SystemName,
                c.DisplayName,
                c.ConfigName,
                c.SerialNo
            FROM configurations c
            WHERE c.Active = 1
            ORDER BY c.ConfigName, c.SerialNo
        """)
        configs = cursor.fetchall()
        
        # 2. Create mappings
        system_to_display = {}
        display_to_system = {}
        config_name_map = {}
        
        for config in configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            config_name = config['ConfigName']
            
            system_to_display[system_name] = display_name
            display_to_system[display_name] = system_name
            config_name_map[system_name] = config_name
        
        # Define config categories
        numeric_configs = ['Quantity', 'ItemPrice', 'ItemCalc'] 
        non_numeric_configs = ['ItemMaster', 'HSNCode', 'Barcode', 'WareHouse', 'WHLocation', 'ItemGroup'] 
        
        numeric_fields = []
        non_numeric_fields = []
        
        # Process each requested field
        for display_field in group_fields:
            if display_field in display_to_system:
                system_field = display_to_system[display_field]
                config_name = config_name_map.get(system_field, '')
                
                if config_name in numeric_configs:
                    # DECIMAL fields: ItemCalc, ItemPrice, Quantity
                    numeric_fields.append({
                        'display': display_field,
                        'system': system_field,
                        'type': 'numeric',
                        'config': config_name
                    })
                elif config_name in non_numeric_configs:
                    # VARCHAR fields: ItemMaster, HSNCode, Barcode, etc.
                    non_numeric_fields.append({
                        'display': display_field,
                        'system': system_field,
                        'type': 'non_numeric',
                        'config': config_name
                    })
                else:
                    print(f"Warning: Field '{display_field}' has unknown config type: {config_name}")
            else:
                print(f"Warning: Field '{display_field}' not found in display_to_system mapping")
        
        if not non_numeric_fields:
            return jsonify({
                'success': False,
                'message': 'Please select at least one non-numeric field (like Product) for grouping'
            }), 400

        select_parts = []
        
        for field in non_numeric_fields:
            select_parts.append(f"`{field['system']}` AS `{field['display']}`")
        
        for field in numeric_fields:
            select_parts.append(f"SUM(`{field['system']}`) AS `{field['display']}`")

        where_conditions = []
        where_params = []
        
        for field_display, values in filters.items():
            if field_display in display_to_system:
                system_field = display_to_system[field_display]
                if isinstance(values, list) and values:
                    placeholders = ', '.join(['%s'] * len(values))
                    where_conditions.append(f"`{system_field}` IN ({placeholders})")
                    where_params.extend(values)

        group_by_parts = [f"`{field['system']}`" for field in non_numeric_fields]
        
        if not select_parts:
            return jsonify({
                'success': False,
                'message': 'No valid columns to select'
            }), 400
        
        select_clause = ', '.join(select_parts)
        query = f"SELECT {select_clause} FROM inventories"
        
        if where_conditions:
            query += f" WHERE {' AND '.join(where_conditions)}"
        
        if group_by_parts:
            query += f" GROUP BY {', '.join(group_by_parts)}"
        
        if non_numeric_fields:
            first_field = non_numeric_fields[0]['system']
            query += f" ORDER BY `{first_field}`"
        
        if where_params:
            cursor.execute(query, where_params)
        else:
            cursor.execute(query)
        
        results = cursor.fetchall()
        
        def convert_value(value):
            if value is None:
                return None
            elif isinstance(value, bytes):
                try:
                    return value.decode('utf-8')
                except (UnicodeDecodeError, AttributeError):
                    return "[IMAGE_DATA]"
            elif isinstance(value, (datetime, date)):
                return value.isoformat()
            elif isinstance(value, Decimal):
                return float(value)
            else:
                return value
        
        flat_results = []
        
        for row in results:
            flat_row = {}
            
            for key, value in row.items():
                flat_row[key] = convert_value(value)
            
            flat_results.append(flat_row)
        
        original_select = []
        
        for config in configs:
            system_name = config['SystemName']
            display_name = config['DisplayName']
            config_name = config['ConfigName']
            
            if config_name.lower() != 'image':
                original_select.append(f"`{system_name}` AS `{display_name}`")
        
        if original_select:
            original_query = f"SELECT {', '.join(original_select)} FROM inventories LIMIT 1000"
            cursor.execute(original_query)
            original_data_rows = cursor.fetchall()
            
            original_data = []
            for row in original_data_rows:
                processed_row = {}
                for key, value in row.items():
                    processed_row[key] = convert_value(value)
                original_data.append(processed_row)
            
        else:
            original_data = []
        
        return jsonify({
            'success': True,
            'data': flat_results,
            'original_data': original_data,  # For filtering sidebar
            'total_records': len(flat_results),
            'grouped_by': [field['display'] for field in non_numeric_fields],
            'summed_fields': [field['display'] for field in numeric_fields],
            'sql_query': query  # For debugging
        }), 200
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in get_grouped_inventories: {str(e)}")
        print(f"Traceback: {error_details}")
        
        return jsonify({
            'success': False,
            'message': f'Error grouping data: {str(e)}',
            'error_details': error_details
        }), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/onboard-software-customer', methods=['POST'])
def onboard_software_customer():
    try:
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')

        if not api_key or api_key != API_KEY:
            return jsonify({'success': False, 'message': 'Invalid API key'}), 401
        
        data = request.get_json()
        customer_name = data.get('customer_name')
        customer_email = data.get('customer_email')
        product_type = data.get('product_type', 'standard')
        
        if not customer_name or not customer_email:
            return jsonify({'success': False, 'message': 'Customer name and email required'}), 400
        
        conn = db_manager.get_main_connection()
        cursor = conn.cursor()
        
        # Check if customer already exists
        cursor.execute("SELECT customer_id FROM customer_api_keys WHERE customer_email = %s", (customer_email,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Software customer email already exists'}), 400
        
        # DYNAMICALLY get server IP from environment
        server_ip = os.getenv('SERVER_IP')
        if not server_ip:
            # Auto-detect server IP if not set
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            server_ip = s.getsockname()[0]
            s.close()
        
        # DYNAMICALLY get next available port
        assigned_port = get_next_available_port()
        if not assigned_port:
            return jsonify({'success': False, 'message': 'No available ports'}), 500
        
        # Generate API key and database
        from api_key_manager import generate_api_key, encrypt_data, hash_api_key
        customer_api_key = generate_api_key()
        encrypted_key = encrypt_data(customer_api_key)
        key_hash = hash_api_key(customer_api_key)
        
        # Create unique database name
        import re
        safe_email = re.sub(r'[^a-zA-Z0-9_]', '_', customer_email.split('@')[0])
        db_name = f"cust_{safe_email}_{secrets.token_hex(4)}".lower()
        
        # Create customer database
        if not db_manager.create_customer_database(db_name):
            return jsonify({'success': False, 'message': 'Failed to create customer database'}), 500
        
        # Build access URL
        access_url = f"http://{server_ip}:{assigned_port}"
        
        # Store customer info
        cursor.execute("""
            INSERT INTO customer_api_keys 
            (customer_name, customer_email, encrypted_api_key, original_key_hash, 
             database_name, product_type, server_ip, assigned_port, access_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (customer_name, customer_email, encrypted_key, key_hash, db_name, 
              product_type, server_ip, assigned_port, access_url))
        
        customer_id = cursor.lastrowid
        
        cursor.execute("""
            INSERT INTO plain_api_keys_backup (customer_id, plain_api_key)
            VALUES (%s, %s)
        """, (customer_id, customer_api_key))
        
        conn.commit()
        
        # Return dynamic access information
        response_data = {
            'success': True,
            'message': 'Software customer onboarded successfully!',
            'customer': {
                'id': customer_id,
                'name': customer_name,
                'email': customer_email
            },
            'access': {
                'url': access_url,
                'server_ip': server_ip,
                'port': assigned_port,
                'api_key': customer_api_key
            },
            'database': {
                'name': db_name,
                'type': product_type
            },
            'instructions': [
                f'1. Customer will access: {access_url}',
                f'2. Save this API key securely: {customer_api_key}',
                f'3. Port {assigned_port} is exclusively assigned to this customer'
            ]
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        return jsonify({'success': False, 'message': f'Onboarding failed: {str(e)}'}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()


def create_fixed_mfa_tables():
    """Create fixed MFA tables that work for both admin and users"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        
        # Create new fixed tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_mfa (
                user_id INT,
                user_type ENUM('admin', 'user') NOT NULL,
                secret_key VARCHAR(255),
                is_mfa_enabled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, user_type)  -- ✅ FIX: Composite primary key
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
                INDEX idx_user_type (user_id, user_type)  -- ✅ Better performance
            )
        """)
        
        conn.commit()
        
    except Exception as e:
        print(f"Error creating fixed MFA tables: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

@app.route('/admin/verify-mfa', methods=['POST'])
@limiter.limit("5 per minute")
def verify_mfa():
    """Verify MFA code for admin login"""
    try:
        data = request.get_json()
        session_token = data.get('session_token')
        verification_code = data.get('verification_code')

        if not session_token or not verification_code:
            return jsonify({'success': False, 'message': 'Session token and verification code required'}), 400

        # Verify MFA code
        result = simple_mfa.verify_mfa_code(session_token, verification_code)
        
        if result['success']:
            user_id = result['user_id']
            
            # Get admin details and create session
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT * FROM admin_users WHERE admin_id = %s", (user_id,))
            admin = cursor.fetchone()
            
            session['admin_logged_in'] = True
            session['admin_user'] = admin['admin_name']
            session['admin_email'] = admin['admin_email']
            session['admin_id'] = admin['admin_id']
            
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'user': admin['admin_name'],
                'admin_id': admin['admin_id']
            })
        else:
            return jsonify(result), 401
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Verification error: {str(e)}'}), 500

@app.route('/user/verify-mfa', methods=['POST'])
@limiter.limit("5 per minute")
def verify_user_mfa():
    """Verify MFA code for user login"""
    try:
        data = request.get_json()
        session_token = data.get('session_token')
        verification_code = data.get('verification_code')

        if not session_token or not verification_code:
            return jsonify({'success': False, 'message': 'Session token and verification code required'}), 400

        # Verify MFA code
        result = simple_mfa.verify_mfa_code(session_token, verification_code)
        
        if result['success']:
            user_id = result['user_id']
            
            # Get user details and create session
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            user = cursor.fetchone()
            
            session['user_logged_in'] = True
            session['user_id'] = user['user_id']
            session['user_name'] = user['user_name']
            session['user_type'] = 'user'
            session['user_email'] = user['user_email']
            
            # ✅ Store customer email for orders page
            session['customer_email']= user['user_email']
            
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'user_id': user['user_id'],
                'user_name': user['user_name'],
                'email': user['user_email']
            })
        else:
            return jsonify(result), 401
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Verification error: {str(e)}'}), 500

@app.route('/api/enable-mfa', methods=['POST'])
@admin_required
def enable_mfa():
    """Enable MFA for current admin"""
    try:
        admin_id = session.get('admin_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO user_mfa (user_id, user_type, is_mfa_enabled)
            VALUES (%s, 'admin', TRUE)
            ON DUPLICATE KEY UPDATE is_mfa_enabled = TRUE
        """, (admin_id,))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'MFA enabled successfully. You will need to verify via email on next login.'
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Error enabling MFA: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/disable-mfa', methods=['POST'])
@admin_required
def disable_mfa():
    """Disable MFA for current admin"""
    try:
        admin_id = session.get('admin_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Disable MFA
        cursor.execute("""
            UPDATE user_mfa 
            SET is_mfa_enabled = FALSE 
            WHERE user_id = %s AND user_type = 'admin'
        """, (admin_id,))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'MFA disabled successfully'
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Error disabling MFA: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/mfa-status', methods=['GET'])
@admin_required
def get_mfa_status():
    """Get MFA status for current admin"""
    try:
        admin_id = session.get('admin_id')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT is_mfa_enabled FROM user_mfa 
            WHERE user_id = %s AND user_type = 'admin'
        """, (admin_id,))
        
        result = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'mfa_enabled': bool(result and result['is_mfa_enabled'])
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/user/enable-mfa', methods=['POST'])
@user_required
def enable_user_mfa():
    """Enable MFA for current user"""
    try:
        user_id = session.get('user_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Enable MFA
        cursor.execute("""
            INSERT INTO user_mfa (user_id, user_type, is_mfa_enabled)
            VALUES (%s, 'user', TRUE)
            ON DUPLICATE KEY UPDATE is_mfa_enabled = TRUE
        """, (user_id,))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'MFA enabled successfully. You will need to verify via email on next login.'
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Error enabling MFA: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/user/disable-mfa', methods=['POST'])
@user_required
def disable_user_mfa():
    """Disable MFA for current user"""
    try:
        user_id = session.get('user_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Disable MFA
        cursor.execute("""
            UPDATE user_mfa 
            SET is_mfa_enabled = FALSE 
            WHERE user_id = %s AND user_type = 'user'
        """, (user_id,))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'MFA disabled successfully'
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Error disabling MFA: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/user/mfa-status', methods=['GET'])
@user_required
def get_user_mfa_status():
    """Get MFA status for current user"""
    try:
        user_id = session.get('user_id')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT is_mfa_enabled FROM user_mfa 
            WHERE user_id = %s AND user_type = 'user'
        """, (user_id,))
        
        result = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'mfa_enabled': bool(result and result['is_mfa_enabled'])
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

                       
def get_next_available_port():
    """Dynamically find the next available port"""
    conn = db_manager.get_main_connection()
    cursor = conn.cursor()
    
    try:
        # Get base and max port from environment
        base_port = int(os.getenv('BASE_PORT', 5001))
        max_port = int(os.getenv('MAX_PORT', 6000))
        
        # Get all currently assigned ports
        cursor.execute("SELECT assigned_port FROM customer_api_keys WHERE assigned_port IS NOT NULL")
        used_ports = {row[0] for row in cursor.fetchall()}
        
        # Find next available port
        for port in range(base_port, max_port + 1):
            if port not in used_ports:
                # Check if port is actually available on system
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                
                try:
                    # Try to bind to check if port is free
                    sock.bind(('0.0.0.0', port))
                    sock.close()
                    return port
                except socket.error:
                    # Port is in use by another process, skip it
                    print(f"Port {port} is already in use by system, skipping...")
                    continue
        
        raise Exception(f"No available ports between {base_port} and {max_port}")
        
    except Exception as e:
        print(f"Error finding available port: {e}")
        return None
    finally:
        cursor.close()
        conn.close()
                    
                    
@app.route('/api/customer/ports', methods=['GET'])
@admin_required
def get_all_customer_ports():
    """Get all customer ports dynamically"""
    conn = db_manager.get_main_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get main server IP
        server_ip = os.getenv('SERVER_IP')
        if not server_ip:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            server_ip = s.getsockname()[0]
            s.close()
        
        cursor.execute("""
            SELECT customer_id, customer_name, customer_email,
                   database_name, assigned_port, access_url,
                   is_active, created_at
            FROM customer_api_keys 
            ORDER BY assigned_port
        """)
        
        customers = cursor.fetchall()
        
        return jsonify({
            'success': True,
            'server_info': {
                'ip': server_ip,
                'main_port': int(os.getenv('FLASK_PORT', 5000)),
                'base_port': int(os.getenv('BASE_PORT', 5001)),
                'max_port': int(os.getenv('MAX_PORT', 6000))
            },
            'customers': customers,
            'total_customers': len(customers),
            'available_ports': get_available_ports()
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

def get_available_ports():
    """Get list of all available ports"""
    base_port = int(os.getenv('BASE_PORT', 5001))
    max_port = int(os.getenv('MAX_PORT', 6000))
    
    conn = db_manager.get_main_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT assigned_port FROM customer_api_keys WHERE assigned_port IS NOT NULL")
        used_ports = {row[0] for row in cursor.fetchall()}
        
        available_ports = []
        for port in range(base_port, max_port + 1):
            if port not in used_ports:
                available_ports.append(port)
        
        return available_ports
        
    finally:
        cursor.close()
        conn.close()

@app.route('/api/customer/update-port/<int:customer_id>', methods=['PUT'])
@admin_required
def update_customer_port(customer_id):
    """Change customer's assigned port"""
    try:
        data = request.get_json()
        new_port = data.get('new_port')
        
        if not new_port:
            return jsonify({'success': False, 'message': 'New port required'}), 400
        
        # Validate port range
        base_port = int(os.getenv('BASE_PORT', 5001))
        max_port = int(os.getenv('MAX_PORT', 6000))
        
        if not (base_port <= new_port <= max_port):
            return jsonify({
                'success': False, 
                'message': f'Port must be between {base_port} and {max_port}'
            }), 400
        
        conn = db_manager.get_main_connection()
        cursor = conn.cursor()
        
        # Check if port is already assigned
        cursor.execute("SELECT customer_id FROM customer_api_keys WHERE assigned_port = %s AND customer_id != %s", 
                      (new_port, customer_id))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Port already assigned to another customer'}), 400
        
        # Update port
        cursor.execute("""
            UPDATE customer_api_keys 
            SET assigned_port = %s, 
                access_url = CONCAT(server_ip, ':', %s)
            WHERE customer_id = %s
        """, (new_port, new_port, customer_id))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': f'Port updated to {new_port}',
            'new_access_url': f"http://{os.getenv('SERVER_IP')}:{new_port}"
        })
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()
                
create_fixed_mfa_tables()
create_customer_management_tables()
create_table()
create_configurations_table()
create_customers_table()
create_orders_table()
create_admin_table() 
create_users_table()       
            
if __name__ == '__main__':
    import sys
    
    '''
    import logging
    
    class TLSFilter(logging.Filter):
        def filter(self, record):
            # Filter out TLS handshake error messages
            message = record.getMessage()
            return 'Bad request version' not in message and \
                   'Bad HTTP/0.9 request type' not in message and \
                   'Bad request syntax' not in message
    
    # Apply filter to werkzeug logger
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.addFilter(TLSFilter())
    
    # Optional: Also reduce log level for cleaner output
    werkzeug_logger.setLevel(logging.WARNING)
    '''
    
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])  
            print(f"🚀 Starting on port: {port}")
        except ValueError:
            print(f"⚠️ Invalid port: {sys.argv[1]}, using default 5000")
            port = 5000
    else:
        port = 5000  
    app.run(debug=False, port=port, host='0.0.0.0')