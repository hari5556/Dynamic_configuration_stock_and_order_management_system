# Dynamic Configurations Stock and Order Management System

#### Overview of the project

A scalable inventory and order management platform built using Flask and MySQL with multi tenant architecture and dynamic configuration capabilities.
* Developed a scalable multi-tenant backend architecture with separate customer databases and dynamic request routing.
* Built RESTful APIs for inventory management, order processing, customer operations, and configuration management.
* Designed a dynamic configuration engine allowing admins to create custom fields without modifying backend code.
* Built an Excel import pipeline with dynamic header mapping and automated inventory synchronization workflows.
* Added inventory deduction workflows, order confirmation logic, MFA, and API rate limiting features.
* Used Git and GitHub for version control, code collaboration, and project management.
---
#### Features

##### Authentication & Authorization
- Secure user login and logout
- Session-based authentication
- Role-based access control
###### Product Management
- Add new products
- Update product details
- Delete products
- View product inventory
###### Stock Management
- Real-time stock tracking
- Automatic stock updates after order processing
- Low-stock monitoring
- Inventory history tracking
###### Order Management
- Create customer orders
- Update order status
- Cancel orders
- Order history management
###### Customer Management
- Add customer details
- View customer order history
- Manage customer information
###### Dashboard
- Total products overview
- Total orders overview
- Stock availability summary
- Business performance insights
---
#### Technologies Used

###### Frontend
- HTML
- CSS
- JavaScript
###### Backend
- Python
- Flask
- REST API
###### Database
- MySQL
###### Tools
- Git
- GitHub
- PostMan API for API testing
- VS Code
---
#### Installation :

###### Clone the Repository
```bash
git clone https://github.com/hari5556/Dynamic_Configuration_Stock_and_Order_Management_System.git
```

###### Navigate to Project Directory
```bash
cd Dynamic_Configuration_Stock_and_Order_Management_System
```

###### Create Virtual Environment
```bash
python -m venv venv
```

###### Activate Virtual Environment
Linux/Mac:
```bash
source venv/bin/activate
```
Windows:
```bash
venv\Scripts\activate
```

###### Install Dependencies
```bash
pip install -r requirements.txt
```

###### Alter some Database settings in the environment file based on the database credentials
###### Run the python script written for this project
```python
python app.py
```
###### Open Browser
```text
http://127.0.0.1:8000/
```

---
#### Future Enhancements

- Barcode scanning
- Sales analytics dashboard
- Email notifications
- Purchase order automation
- Export reports to PDF and Excel
- Mobile application support
---
#### Learning Outcomes

This project helped in understanding:
- Flask Framework
- Database Design
- CRUD Operations
- Session Management
- Authentication & Authorization
- Inventory Management Concepts
- Order Processing Workflow
- Full Stack Web Development
---
#### Author

**A Hari**
Software Developer
GitHub: https://github.com/hari5556

---
