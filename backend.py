import sqlite3
from typing import List, Tuple, Optional, Dict
from datetime import datetime
import cohere
import os

# ----------------- CONFIGURATION -----------------
DB_FILE = "students.db"
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "your_cohere_api_key_here")
COHERE_MODEL = "command-r-plus"
co = cohere.Client(COHERE_API_KEY)

# ----------------- DATABASE INITIALIZATION -----------------
def create_db():
    """Create or update the students table with all required columns."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                roll_no TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                age INTEGER DEFAULT 0,
                gender TEXT,
                category TEXT,
                address TEXT,
                course TEXT NOT NULL,
                current_year INTEGER DEFAULT 1,
                semester INTEGER DEFAULT 1,
                type TEXT,
                room_no TEXT,
                hostel_building TEXT,
                block TEXT,
                bus_no TEXT,
                route TEXT,
                attendance INTEGER DEFAULT 0,
                marks INTEGER DEFAULT 0,
                performance TEXT DEFAULT 'Average',
                date_of_birth TEXT,
                created_at TEXT DEFAULT (DATE('now'))
            )
        """)
        conn.commit()

        expected_columns = {
            "age": "INTEGER",
            "gender": "TEXT",
            "category": "TEXT",
            "address": "TEXT",
            "current_year": "INTEGER",
            "semester": "INTEGER",
            "type": "TEXT",
            "room_no": "TEXT",
            "hostel_building": "TEXT",
            "block": "TEXT",
            "bus_no": "TEXT",
            "route": "TEXT",
            "attendance": "INTEGER",
            "marks": "INTEGER",
            "performance": "TEXT",
            "date_of_birth": "TEXT",
            "created_at": "TEXT"
        }

        c.execute("PRAGMA table_info(students)")
        existing_cols = {row[1] for row in c.fetchall()}

        for col, dtype in expected_columns.items():
            if col not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE students ADD COLUMN {col} {dtype}")
                except Exception as e:
                    print(f"⚠️ Could not add column {col}: {e}")

        conn.commit()

    create_triggers()
    create_views()

# ----------------- TRIGGERS -----------------
def create_triggers():
    """Auto-update performance when marks or attendance change."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_update_performance
            AFTER INSERT ON students
            BEGIN
                UPDATE students
                SET performance = CASE
                    WHEN NEW.marks >= 85 AND NEW.attendance >= 90 THEN 'Excellent'
                    WHEN NEW.marks >= 70 THEN 'Good'
                    WHEN NEW.marks >= 50 THEN 'Average'
                    ELSE 'Poor'
                END
                WHERE student_id = NEW.student_id;
            END;
        """)
        c.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_update_marks_attendance
            AFTER UPDATE OF marks, attendance ON students
            BEGIN
                UPDATE students
                SET performance = CASE
                    WHEN NEW.marks >= 85 AND NEW.attendance >= 90 THEN 'Excellent'
                    WHEN NEW.marks >= 70 THEN 'Good'
                    WHEN NEW.marks >= 50 THEN 'Average'
                    ELSE 'Poor'
                END
                WHERE student_id = NEW.student_id;
            END;
        """)
        conn.commit()

# ----------------- VIEWS -----------------
def create_views():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE VIEW IF NOT EXISTS student_performance_summary AS
            SELECT course,
                   COUNT(*) AS total_students,
                   AVG(attendance) AS avg_attendance,
                   AVG(marks) AS avg_marks,
                   SUM(CASE WHEN performance = 'Excellent' THEN 1 ELSE 0 END) AS toppers,
                   SUM(CASE WHEN attendance < 75 THEN 1 ELSE 0 END) AS low_attendance
            FROM students
            GROUP BY course;
        """)
        conn.commit()

# ----------------- CRUD -----------------
def add_student(student_id, roll_no, name, age, gender, category, address,
                course, current_year, semester, type_, room_no, hostel_building,
                block, bus_no, route, attendance):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO students (
                    student_id, roll_no, name, age, gender, category, address,
                    course, current_year, semester, type, room_no, hostel_building,
                    block, bus_no, route, attendance, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, DATE('now'))
            """, (student_id, roll_no, name, age, gender, category, address,
                  course, current_year, semester, type_, room_no, hostel_building,
                  block, bus_no, route, attendance))
            conn.commit()
        return True, "✅ Student added successfully!"
    except sqlite3.IntegrityError:
        return False, "⚠️ Student with this ID or Roll Number already exists!"
    except Exception as e:
        return False, f"❌ Error adding student: {e}"

def update_student(student_id, **kwargs):
    if not kwargs:
        return False, "⚠️ No fields provided for update"
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        fields = ", ".join([f"{k}=?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [student_id]
        try:
            c.execute(f"UPDATE students SET {fields} WHERE student_id=?", values)
            conn.commit()
            return True, "✅ Student updated successfully!"
        except Exception as e:
            return False, f"❌ Error updating student: {e}"

def delete_student(roll_no: str):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM students WHERE roll_no=?", (roll_no,))
        conn.commit()

def fetch_all_students(filters=None):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        query = "SELECT * FROM students"
        params = []
        if filters and isinstance(filters, dict):
            clauses = [f"{k} = ?" for k, v in filters.items() if v]
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
                params = [v for v in filters.values() if v]
        c.execute(query, params)
        return c.fetchall()

def all_rows() -> List[Dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM students")
        return [dict(row) for row in c.fetchall()]

def get_student_by_roll(roll_no: str) -> Optional[Dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE roll_no=?", (roll_no,))
        row = c.fetchone()
        return dict(row) if row else None

def get_student(student_id: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE student_id=?", (student_id,))
        row = c.fetchone()
        if not row:
            return None
        return dict(row)

# ----------------- ANALYTICS -----------------
def get_course_summary() -> List[Tuple]:
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM student_performance_summary")
        return c.fetchall()

# ----------------- AI FEATURES -----------------
def generate_feedback(name: str, attendance: int, marks: int) -> str:
    prompt = f"Give a short motivational feedback for {name} with attendance {attendance}% and marks {marks}."
    try:
        response = co.chat(model=COHERE_MODEL, message=prompt)
        return response.text.strip()
    except Exception:
        return f"{name}, keep pushing forward — success is near! 💪"

# ✅ FIXED SQL GENERATION + EXECUTION -----------------
def ai_generate_sql(prompt: str) -> str:
    """Converts natural language query to SQLite SELECT query."""
    p = prompt.lower().strip()

    if "attendance" in p and "<" in p:
        val = ''.join([c for c in p if c.isdigit()])
        return f"SELECT * FROM students WHERE attendance < {val};"
    elif "attendance" in p and ">" in p:
        val = ''.join([c for c in p if c.isdigit()])
        return f"SELECT * FROM students WHERE attendance > {val};"
    elif "show all" in p or "list all" in p:
        return "SELECT * FROM students;"
    elif "female" in p:
        return "SELECT * FROM students WHERE gender = 'Female';"
    elif "male" in p:
        return "SELECT * FROM students WHERE gender = 'Male';"
    elif "performance" in p and "poor" in p:
        return "SELECT * FROM students WHERE LOWER(performance) = 'poor';"
    elif "performance" in p and "good" in p:
        return "SELECT * FROM students WHERE LOWER(performance) = 'good';"
    elif "hosteller" in p:
        return "SELECT * FROM students WHERE type = 'Hosteller';"
    elif "day scholar" in p or "dayscholar" in p:
        return "SELECT * FROM students WHERE type = 'Day Scholar';"
    elif "course" in p:
        words = p.split()
        idx = words.index("course") if "course" in words else -1
        if idx != -1 and idx + 1 < len(words):
            course_name = words[idx + 1].capitalize()
            return f"SELECT * FROM students WHERE course LIKE '%{course_name}%';"
        else:
            return "SELECT DISTINCT course FROM students;"
    elif "year" in p:
        year = ''.join([c for c in p if c.isdigit()])
        if year:
            return f"SELECT * FROM students WHERE current_year = {year};"
    elif "semester" in p:
        sem = ''.join([c for c in p if c.isdigit()])
        if sem:
            return f"SELECT * FROM students WHERE semester = {sem};"

    return ""  # ✅ Return blank if not recognized


def execute_generated_sql(query: str):
    """Safely execute the generated SQL query and handle errors gracefully."""
    if not query or "error" in query.lower() or not query.strip().endswith(";"):
        return False, "⚠️ Could not generate a valid SQL query."

    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query)
            rows = c.fetchall()
            return True, [dict(r) for r in rows] if rows else []
    except Exception as e:
        return False, f"⚠️ Could not execute the query: {e}"

def generate_sql(prompt: str) -> str:
    """Wrapper used by Streamlit chatbot."""
    return ai_generate_sql(prompt)

# ----------------- ADMIN CHATBOT -----------------
def admin_chatbot_query(prompt: str) -> str:
    p = prompt.lower()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        if "average" in p and "marks" in p:
            c.execute("SELECT AVG(marks) FROM students")
            avg = round(c.fetchone()[0] or 0, 2)
            return f"📊 The average marks are {avg}."
        elif "attendance" in p:
            c.execute("SELECT AVG(attendance) FROM students")
            avg = round(c.fetchone()[0] or 0, 2)
            return f"📅 Average attendance is {avg}%."
        elif "count" in p or "total" in p:
            c.execute("SELECT COUNT(*) FROM students")
            total = c.fetchone()[0]
            return f"👥 Total students: {total}."
        else:
            return "🤖 Try asking about total students, average marks, or attendance."

# ----------------- RISK PREDICTION -----------------
def predict_risk(roll_no: str) -> Dict:
    student = get_student_by_roll(roll_no)
    if not student:
        return {"error": "Student not found."}
    marks = student.get("marks", 0)
    attendance = student.get("attendance", 0)
    if marks < 40 or attendance < 60:
        risk = "High Risk 🚨"
    elif marks < 60 or attendance < 75:
        risk = "Moderate Risk ⚠️"
    else:
        risk = "Low Risk ✅"
    feedback = generate_feedback(student["name"], attendance, marks)
    return {
        "name": student["name"],
        "roll_no": roll_no,
        "marks": marks,
        "attendance": attendance,
        "performance": student["performance"],
        "risk_level": risk,
        "feedback": feedback
    }

# ----------------- MAIN -----------------
if __name__ == "__main__":
    create_db()
    print("✅ Database initialized successfully with all features.")
