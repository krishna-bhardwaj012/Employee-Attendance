from flask import Flask, request, jsonify, render_template
import os
import mysql.connector
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime

app = Flask(__name__)

# --- Load environment variables from .env file FIRST ---
load_dotenv()

# --- Groq API Configuration (from environment variables) ---
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable not set. Please set it before running the app.")

groq_client = Groq(api_key=GROQ_API_KEY)

# --- Database Configuration (from environment variables) ---
DB_HOST = os.environ.get('DB_HOST')
DB_PORT = os.environ.get('DB_PORT')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_NAME_CR = os.environ.get('DB_NAME_CR') # Assuming employee table is here
DB_NAME_NRKINDEX_TRN = os.environ.get('DB_NAME_NRKINDEX_TRN') # Assuming PMO_DAILY_ATTENDNACE table is here

# Validate essential DB configs
if not all([DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME_CR, DB_NAME_NRKINDEX_TRN]):
    raise ValueError("One or more database environment variables (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME_CR, DB_NAME_NRKINDEX_TRN) are not set. Please set them before running the app.")

# --- ADDED DEBUGGING PRINTS ---
print("\n--- Flask App DB Config Check ---")
print(f"DB_HOST: {DB_HOST}")
print(f"DB_PORT: {DB_PORT}")
print(f"DB_USER: {DB_USER}")
print(f"DB_PASSWORD: {'*' * len(DB_PASSWORD) if DB_PASSWORD else 'NOT SET'}") # Mask password for security
print(f"DB_NAME_CR: {DB_NAME_CR}")
print(f"DB_NAME_NRKINDEX_TRN: {DB_NAME_NRKINDEX_TRN}")
print("---------------------------------\n")
# --- END DEBUGGING PRINTS ---

# --- Helper function to get database connection ---
def get_db_connection(db_name):
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=int(DB_PORT), # Ensure port is an integer
            database=db_name,
            user=DB_USER,
            password=DB_PASSWORD
        )
        return conn
    except ValueError as ve:
        print(f"Configuration Error: DB_PORT '{DB_PORT}' is not a valid integer. Please check your .env file or environment variables.")
        return None
    except mysql.connector.Error as err:
        print(f"Database connection error to '{db_name}': {err}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred connecting to database '{db_name}': {e}")
        return None

# --- Frontend Endpoint (MODIFIED to fetch all employees) ---
@app.route('/')
def index():
    conn = None
    employees = []
    try:
        conn = get_db_connection(DB_NAME_CR) # Assuming 'employee' table is in DB_NAME_CR
        if conn:
            with conn.cursor(dictionary=True) as cur: # Use dictionary=True to get column names
                # Assuming 'employee' table has 'id' and 'name' columns
                cur.execute("SELECT id, name FROM employee ORDER BY name ASC")
                employees = cur.fetchall()
        else:
            print(f"Failed to get DB connection for {DB_NAME_CR} in index route.")
    except Exception as e:
        print(f"Error fetching employees for frontend: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()
    return render_template('index.html', employees=employees)

# --- API Endpoint 1: Fetch all employees (for frontend to consume) ---
@app.route('/get_all_employees', methods=['GET'])
def get_all_employees():
    conn = None
    try:
        conn = get_db_connection(DB_NAME_CR) # Assuming 'employee' table is in DB_NAME_CR
        if conn is None:
            return jsonify({"error": f"Failed to connect to the CR database '{DB_NAME_CR}'. Check backend logs for connection details."}), 500

        with conn.cursor(dictionary=True) as cur: # Use dictionary=True to get column names
            # Assuming 'employee' table has 'id' and 'name' columns
            cur.execute("SELECT id, name FROM employee ORDER BY name ASC")
            employees = cur.fetchall()

        return jsonify({"employees": employees}), 200
    except Exception as e:
        print(f"Error fetching all employees: {e}")
        return jsonify({"error": f"An internal server error occurred while fetching employees: {str(e)}"}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- API Endpoint 2: Generate SQL INSERT Queries using Groq (MODIFIED) ---
@app.route('/generate_attendance_sql', methods=['POST'])
def generate_attendance_sql():
    data = request.json
    present_employee_ids = data.get('present_employee_ids', [])
    all_employee_ids = data.get('all_employee_ids', [])
    meeting_time = data.get('meeting_time_str')
    meeting_date = data.get('meeting_date')
    inserted_by_id = data.get('inserted_by_id')

    if not all([all_employee_ids, present_employee_ids, meeting_time, meeting_date, inserted_by_id]):
        return jsonify({"error": "All fields (all_employee_ids, present_employee_ids, date, time, inserted by ID) are required."}), 400

    generated_sqls = []

    for emp_id in all_employee_ids:
        employee_type = 'Trainee'
        attendance_type = 'Present' if emp_id in present_employee_ids else 'Absent'

        prompt = f"""
        You are an SQL query generator. Generate an SQL INSERT statement for recording employee attendance in a MySQL database.
        The table name is `PMO_DAILY_ATTENDNACE`.
        The table has the following columns (and their data types/behavior):
        - `ATTENDANCE_ID` (INT AUTO_INCREMENT PRIMARY KEY - auto-generated, do NOT include in INSERT)
        - `EMPLOYEE_ID` (VARCHAR(50) - refers to emp_id, a string)
        - `EMPLOYEE_TYPE` (VARCHAR(50) or ENUM - e.g., 'Trainee', 'Full-time')
        - `ATTENDANCE_TYPE` (VARCHAR(50) or ENUM - e.g., 'Present', 'Absent', 'Late')
        - `ATTENDANCE_DATE` (DATE - in 'YYYY-MM-DD' format)
        - `ATTENDANCE_TIME` (TIME - in 'HH:MM:SS' format)
        - `INSERTED_BY_ID` (VARCHAR(50) - refers to the emp_id of the person recording, a string)
        - `INSERTION_DATETIME` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP - auto-generated, do NOT include in INSERT)

        Based on the following information, generate ONLY the SQL INSERT statement. Do not include any other text, explanations, or backticks.
        Ensure all string values are enclosed in single quotes.
        Ensure the date is in 'YYYY-MM-DD' format and time is in 'HH:MM:SS' format.

        Employee ID: '{emp_id}'
        Employee Type: '{employee_type}'
        Attendance Type: '{attendance_type}'
        Attendance Date: '{meeting_date}'
        Attendance Time: '{meeting_time}'
        Inserted By ID: '{inserted_by_id}'

        Example format: INSERT INTO PMO_DAILY_ATTENDNACE (EMPLOYEE_ID, EMPLOYEE_TYPE, ATTENDANCE_TYPE, ATTENDANCE_DATE, ATTENDANCE_TIME, INSERTED_BY_ID) VALUES ('1483', 'Trainee', 'Present', '2025-06-05', '09:00:00', '1483');
        """

        try:
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are an SQL query generator assistant for MySQL. Provide only the SQL INSERT query with all specified columns."},
                    {"role": "user", "content": prompt},
                ],
                model="llama3-8b-8192",
                temperature=0.1,
                max_tokens=200,
            )
            generated_sql = chat_completion.choices[0].message.content.strip()

            expected_columns = ["EMPLOYEE_ID", "EMPLOYEE_TYPE", "ATTENDANCE_TYPE", "ATTENDANCE_DATE", "ATTENDANCE_TIME", "INSERTED_BY_ID"]
            normalized_sql = generated_sql.upper()
            if not (normalized_sql.startswith("INSERT INTO PMO_DAILY_ATTENDNACE") and
                    all(col in normalized_sql for col in expected_columns)):
                raise ValueError(f"Groq generated an invalid SQL query. It must be an INSERT into PMO_DAILY_ATTENDNACE and include all required columns. Generated: {generated_sql}")

            generated_sqls.append(generated_sql)

        except Exception as e:
            print(f"Error generating SQL with Groq for emp_id {emp_id}: {e}")
            return jsonify({"error": f"Failed to generate SQL query for employee {emp_id}: {str(e)}"}), 500
    
    return jsonify({"sql_queries": generated_sqls}), 200

# --- API Endpoint 3: Execute SQL Queries (MODIFIED to execute multiple) ---
@app.route('/execute_generated_sql', methods=['POST'])
def execute_generated_sql():
    sql_queries = request.json.get('sql_queries', []) # Expect a list of queries
    if not sql_queries:
        return jsonify({"error": "No SQL queries provided for execution"}), 400

    conn = None
    try:
        conn = get_db_connection(DB_NAME_NRKINDEX_TRN)
        if conn is None:
            return jsonify({"error": f"Failed to connect to the attendance database '{DB_NAME_NRKINDEX_TRN}'. Check backend logs for connection details."}), 500

        with conn.cursor() as cur:
            for sql_query in sql_queries:
                normalized_query = sql_query.strip().upper()
                expected_columns_pattern = "EMPLOYEE_ID, EMPLOYEE_TYPE, ATTENDANCE_TYPE, ATTENDANCE_DATE, ATTENDANCE_TIME, INSERTED_BY_ID"
                
                # Basic validation for each query
                if not (normalized_query.startswith("INSERT INTO PMO_DAILY_ATTENDNACE (") and
                        expected_columns_pattern in normalized_query and
                        ") VALUES (" in normalized_query):
                    raise ValueError(f"One of the queries does not match the expected INSERT into PMO_DAILY_ATTENDNACE with all required columns: {sql_query}")

                dangerous_keywords = ["DELETE", "UPDATE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE", "SELECT", "UNION", "JOIN", "EXEC", "EXECUTE", "INTO OUTFILE", "--", "/*", "*/"]
                if any(keyword in normalized_query for keyword in dangerous_keywords):
                    raise ValueError(f"One of the queries contains disallowed keywords, indicating a potential security risk: {sql_query}")
                
                cur.execute(sql_query)
            
        conn.commit() # Commit all inserts in one transaction
        return jsonify({"message": f"Successfully executed {len(sql_queries)} SQL queries. Attendance recorded."}), 200
    except Exception as e:
        print(f"Error executing SQL queries: {e}")
        if conn and conn.is_connected():
            conn.rollback() # Rollback if any query fails
        return jsonify({"error": f"Error executing queries: {str(e)}. Please check the generated SQL syntax and data."}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()

if __name__ == '__main__':
    app.run(debug=True)