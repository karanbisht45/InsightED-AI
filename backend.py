"""
backend.py

Final backend module matched to app.py expectations.

Provides:
- create_db()
- add_student(...)
- get_student(student_id)
- get_student_by_roll(roll_no)
- update_student(student_id, **fields)
- delete_student(student_id)
- fetch_all_students(filters)
- all_rows()
- generate_sql(prompt)
- admin_chatbot_query(prompt)
- predict_risk(roll_no)

Keeps Cohere integration (uses COHERE_API_KEY env var), triggers, and SQL view for performance summary.
Integrates with timetable.py by calling timetable.create_timetable_table() at DB init if available.

This version adds explicit transaction handling (BEGIN / COMMIT / ROLLBACK)
to all write/DDL operations while keeping function names, signatures,
and overall logic exactly the same.
"""
import os
import sqlite3
from typing import List, Dict, Optional, Tuple, Any
from datetime import date, datetime
import traceback

# Cohere may not be available in all environments; safe import pattern.
try:
    import cohere
except Exception:
    cohere = None

# Try to import timetable module (kept separate)
try:
    import timetable as tt
except Exception:
    tt = None

# ---------------- CONFIG ----------------
DB_FILE = "students.db"
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "ZDRGnW9Jbj1a6IhwjjTqNimk4BPcxM1bOSn3Hl33")
COHERE_MODEL = os.getenv("COHERE_MODEL", "command-r-plus")

# create Cohere client if possible
co = None
if cohere is not None and COHERE_API_KEY:
    try:
        co = cohere.Client(COHERE_API_KEY)
    except Exception:
        co = None

# ---------------- DATABASE INITIALIZATION ----------------
def create_db():
    """
    Create (or migrate) the students table, notifications, and other objects used by the app.
    Also creates triggers and views. Ensures timetable table exists by delegating to timetable.py if present.
    Transactions are used for DDL to ensure atomicity where supported by SQLite.
    """
    # Use a connection and explicit transaction for table creation
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("BEGIN;")
        c = conn.cursor()

        # Primary students table schema matching the app.py to_df ordering and fields
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
                created_at TEXT DEFAULT (date('now'))
            )
        """)

        # Notifications table used by app.py
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                title TEXT,
                body TEXT,
                type TEXT,
                payload TEXT,
                read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    except Exception as e:
        # If anything fails, rollback to avoid partial DDL
        try:
            conn.rollback()
        except Exception:
            pass
        print("❌ Error during create_db transaction:", e)
        traceback.print_exc()
    finally:
        conn.close()

    # Create triggers and views (idempotent) — these functions handle their own transactions
    _create_triggers()
    _create_views()

    # Ensure timetable table exists (delegated to timetable.py if present)
    if tt is not None and hasattr(tt, "create_timetable_table"):
        try:
            tt.create_timetable_table()
        except Exception:
            # If timetable.create_timetable_table fails, ignore here; app.py will handle
            pass

# ---------------- TRIGGERS & VIEWS ----------------
def _create_triggers():
    """Create triggers to auto-set performance based on marks/attendance."""
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("BEGIN;")
        c = conn.cursor()
        try:
            c.execute("DROP TRIGGER IF EXISTS trg_auto_performance_insert")
        except Exception:
            # ignore if drop fails
            pass

        c.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_auto_performance_insert
            AFTER INSERT ON students
            BEGIN
                UPDATE students
                SET performance = CASE
                    WHEN NEW.attendance >= 90 THEN 'Excellent'
                    WHEN NEW.attendance >= 75 THEN 'Good'
                    WHEN NEW.attendance >= 60 THEN 'Average'
                    ELSE 'Poor'
                END
                WHERE student_id = NEW.student_id;
            END;
        """)

        try:
            c.execute("DROP TRIGGER IF EXISTS trg_auto_performance_update")
        except Exception:
            pass

        c.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_auto_performance_update
            AFTER UPDATE OF attendance, marks ON students
            BEGIN
                UPDATE students
                SET performance = CASE
                    WHEN NEW.attendance >= 90 THEN 'Excellent'
                    WHEN NEW.attendance >= 75 THEN 'Good'
                    WHEN NEW.attendance >= 60 THEN 'Average'
                    ELSE 'Poor'
                END
                WHERE student_id = NEW.student_id;
            END;
        """)
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("❌ Could not create triggers, rolled back. Error:", e)
        traceback.print_exc()
    finally:
        conn.close()

def _create_views():
    """Create view student_performance_summary used by the app dashboard."""
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("BEGIN;")
        c = conn.cursor()
        try:
            c.execute("DROP VIEW IF EXISTS student_performance_summary")
        except Exception:
            pass

        c.execute("""
            CREATE VIEW IF NOT EXISTS student_performance_summary AS
            SELECT course,
                   COUNT(*) AS total_students,
                   ROUND(AVG(attendance),2) AS avg_attendance,
                   ROUND(AVG(marks),2) AS avg_marks,
                   SUM(CASE WHEN performance = 'Excellent' THEN 1 ELSE 0 END) AS toppers,
                   SUM(CASE WHEN attendance < 75 THEN 1 ELSE 0 END) AS low_attendance
            FROM students
            GROUP BY course;
        """)
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("❌ Could not create view, rolled back. Error:", e)
        traceback.print_exc()
    finally:
        conn.close()

# ---------------- CRUD: students ----------------
def add_student(student_id: str, roll_no: str, name: str, age: int,
                gender: str, category: str, address: str, course: str,
                current_year: int, semester: int, type_: str,
                room_no: Optional[str], hostel_building: Optional[str],
                block: Optional[str], bus_no: Optional[str], route: Optional[str],
                attendance: int) -> Tuple[bool, str]:
    """Insert a new student. Returns (ok, message). Uses transaction for atomic insert."""
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("BEGIN;")
        c = conn.cursor()
        c.execute("""
            INSERT INTO students (
                student_id, roll_no, name, age, gender, category, address,
                course, current_year, semester, type, room_no, hostel_building,
                block, bus_no, route, attendance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, date('now'))
        """, (student_id, roll_no, name, age, gender, category, address,
              course, current_year, semester, type_, room_no, hostel_building,
              block, bus_no, route, attendance))
        conn.commit()
        return True, "✅ Student added successfully!"
    except sqlite3.IntegrityError:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, "⚠️ Student with this ID or Roll Number already exists!"
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"❌ Error adding student: {e}"
    finally:
        conn.close()

def get_student(student_id: str) -> Optional[Dict]:
    """Return student dict by student_id."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE student_id=?", (student_id,))
        row = c.fetchone()
        return dict(row) if row else None

def get_student_by_roll(roll_no: str) -> Optional[Dict]:
    """Return student dict by roll_no (used by app search)."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE roll_no=?", (roll_no,))
        row = c.fetchone()
        return dict(row) if row else None

def update_student(student_id: str, **kwargs) -> Tuple[bool, str]:
    """
    Update student fields by student_id.
    Accepts keys such as: roll_no, name, age, gender, category, address, course,
    current_year, semester, type, room_no, hostel_building, block, bus_no, route,
    attendance, marks, performance, date_of_birth, created_at
    Transaction-safe update.
    """
    if not kwargs:
        return False, "⚠️ No fields provided for update"
    allowed = {
        "roll_no","name","age","gender","category","address","course","current_year",
        "semester","type","room_no","hostel_building","block","bus_no","route",
        "attendance","marks","performance","date_of_birth","created_at"
    }
    for k in kwargs.keys():
        if k not in allowed:
            return False, f"⚠️ Invalid field: {k}"
    fields = ", ".join([f"{k}=?" for k in kwargs.keys()])
    values = list(kwargs.values()) + [student_id]
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("BEGIN;")
        c = conn.cursor()
        c.execute(f"UPDATE students SET {fields} WHERE student_id=?", values)
        if c.rowcount == 0:
            conn.rollback()
            return False, "⚠️ Student not found."
        conn.commit()
        return True, "✅ Student updated successfully!"
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"❌ Error updating student: {e}"
    finally:
        conn.close()

def delete_student(student_id: str) -> Tuple[bool, str]:
    """Delete a student by student_id. Returns (ok, message). Transaction-safe delete."""
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("BEGIN;")
        c = conn.cursor()
        c.execute("DELETE FROM students WHERE student_id=?", (student_id,))
        if c.rowcount == 0:
            conn.rollback()
            return False, "⚠️ Student not found."
        conn.commit()
        return True, "✅ Student deleted."
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"❌ Error deleting student: {e}"
    finally:
        conn.close()

def fetch_all_students(filters: Optional[Dict] = None) -> List[tuple]:
    """
    Return rows (tuples) matching filters.
    Expected filter keys (from app.py):
      - type: None or [value]
      - gender: None or [value]
      - category: list or None
      - course_contains: str or None
      - year_in: list or None
      - sem_in: list or None
    """
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        base = "SELECT * FROM students"
        clauses = []
        params: List[Any] = []

        if filters and isinstance(filters, dict):
            if filters.get("type"):
                clauses.append("type = ?")
                params.append(filters["type"][0] if isinstance(filters["type"], list) else filters["type"])
            if filters.get("gender"):
                clauses.append("gender = ?")
                params.append(filters["gender"][0] if isinstance(filters["gender"], list) else filters["gender"])
            if filters.get("category"):
                cats = filters["category"]
                if isinstance(cats, list) and cats:
                    placeholders = ",".join(["?"]*len(cats))
                    clauses.append(f"category IN ({placeholders})")
                    params.extend(cats)
            if filters.get("course_contains"):
                val = filters["course_contains"]
                clauses.append("course LIKE ?")
                params.append(f"%{val}%")
            if filters.get("year_in"):
                yrs = filters["year_in"]
                if isinstance(yrs, list) and yrs:
                    placeholders = ",".join(["?"]*len(yrs))
                    clauses.append(f"current_year IN ({placeholders})")
                    params.extend(yrs)
            if filters.get("sem_in"):
                sems = filters["sem_in"]
                if isinstance(sems, list) and sems:
                    placeholders = ",".join(["?"]*len(sems))
                    clauses.append(f"semester IN ({placeholders})")
                    params.extend(sems)

        if clauses:
            base += " WHERE " + " AND ".join(clauses)

        try:
            c.execute(base, tuple(params))
            rows = c.fetchall()
            return rows
        except Exception:
            return []

def all_rows() -> List[Dict]:
    """Return all student rows as list of dicts."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM students")
        return [dict(r) for r in c.fetchall()]

def ai_generate_sql(prompt: str) -> str:
    """
    Deterministic natural language -> SQL generator used by InsightBot.
    Fallback if Cohere fails.
    """
    p = prompt.lower().strip()
    # basic patterns
    if "attendance" in p and "<" in p:
        val = ''.join([ch for ch in p if ch.isdigit()])
        if val:
            return f"SELECT * FROM students WHERE attendance < {val};"
    if "attendance" in p and ">" in p:
        val = ''.join([ch for ch in p if ch.isdigit()])
        if val:
            return f"SELECT * FROM students WHERE attendance > {val};"
    if "show all" in p or "list all" in p:
        return "SELECT * FROM students;"
    if "female" in p:
        return "SELECT * FROM students WHERE gender = 'Female';"
    if "male" in p:
        return "SELECT * FROM students WHERE gender = 'Male';"
    if "performance" in p and "poor" in p:
        return "SELECT * FROM students WHERE LOWER(performance) = 'poor';"
    if "performance" in p and "good" in p:
        return "SELECT * FROM students WHERE LOWER(performance) = 'good';"
    if "hosteller" in p:
        return "SELECT * FROM students WHERE type = 'Hosteller';"
    if "day scholar" in p or "dayscholar" in p:
        return "SELECT * FROM students WHERE type = 'Day Scholar';"
    if "course" in p:
        words = p.split()
        if "course" in words:
            idx = words.index("course")
            if idx + 1 < len(words):
                course_name = words[idx + 1].capitalize()
                return f"SELECT * FROM students WHERE course LIKE '%{course_name}%';"
        return "SELECT DISTINCT course FROM students;"
    if "year" in p:
        digits = ''.join([c for c in p if c.isdigit()])
        if digits:
            return f"SELECT * FROM students WHERE current_year = {digits};"
    if "semester" in p:
        digits = ''.join([c for c in p if c.isdigit()])
        if digits:
            return f"SELECT * FROM students WHERE semester = {digits};"

    return ""


def generate_sql(prompt: str) -> str:
    """
    Try Cohere AI for query generation first, then fallback to deterministic patterns.
    """
    schema = """
    Table: students
    Columns: id, name, roll_no, course, semester, section, marks, attendance,
    gender, performance, type, current_year, email
    """

    cohere_prompt = f"""
    You are an SQL assistant. Convert the following natural language question
    into a valid SQL SELECT query for the 'students' table. 
    Only use SELECT — never DELETE, UPDATE, INSERT, DROP, or ALTER.
    Use WHERE when filters are mentioned.

    Example conversions:
    - "show all students" → SELECT * FROM students;
    - "show students in btech" → SELECT * FROM students WHERE course='BTech';
    - "students in IT with attendance > 80" → SELECT * FROM students WHERE course='IT' AND attendance > 80;
    - "average marks of semester 3" → SELECT AVG(marks) FROM students WHERE semester=3;

    Schema: {schema}
    Query: {prompt.strip()}
    """

    try:
        response = co.generate(
            model=COHERE_MODEL,
            prompt=cohere_prompt,
            temperature=0.2,
            max_tokens=80,
        )
        sql_query = response.generations[0].text.strip()

        # Safety: only allow SELECT
        if not sql_query.lower().startswith("select"):
            return ai_generate_sql(prompt)
        if any(x in sql_query.lower() for x in ["insert", "update", "delete", "drop", "alter"]):
            return ai_generate_sql(prompt)

        return sql_query

    except Exception as e:
        print("⚠️ Cohere failed:", e)
        return ai_generate_sql(prompt)

# ---------------- ADVANCED ADMIN CHATBOT ----------------
def admin_chatbot_query(prompt: str) -> str:
    """
    Enhanced Admin Chatbot that can handle both natural questions and
    direct SQL SELECT queries (with WHERE, ORDER BY, etc.).
    Prevents unsafe operations like UPDATE, DELETE, or DROP.
    """
    p = prompt.strip().lower()

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            # ✅ 1. Detect if the prompt contains a SELECT query
            if p.startswith("select"):
                # Safety check: block dangerous SQL keywords
                unsafe_keywords = ["update", "delete", "insert", "drop", "alter", "truncate"]
                if any(word in p for word in unsafe_keywords):
                    return "⚠️ Modification queries (UPDATE, DELETE, etc.) are not allowed."

                # Execute user-provided SELECT query safely
                try:
                    c.execute(prompt)
                    rows = c.fetchall()

                    if not rows:
                        return "🤷 No data found for your query."

                    # Get column names
                    columns = [desc[0] for desc in c.description]

                    # Format result table
                    header = " | ".join(columns)
                    separator = "-" * len(header)
                    result_table = f"{header}\n{separator}\n"

                    # Show only first 10 rows for readability
                    for row in rows[:10]:
                        result_table += " | ".join([str(x) for x in row]) + "\n"

                    if len(rows) > 10:
                        result_table += f"...and {len(rows)-10} more rows."

                    return f"📊 SQL Query Executed Successfully:\n```sql\n{prompt}\n```\n\n📋 Results:\n{result_table}"

                except Exception as e:
                    return f"⚠️ Error executing SQL: {e}"

            # ✅ 2. Handle natural language prompts (existing logic)
            if "average" in p and "marks" in p:
                c.execute("SELECT AVG(marks) FROM students")
                avg = round(c.fetchone()[0] or 0, 2)
                return f"📊 The average marks are {avg}."

            elif "attendance" in p and "average" in p:
                c.execute("SELECT AVG(attendance) FROM students")
                avg = round(c.fetchone()[0] or 0, 2)
                return f"📅 Average attendance is {avg}%."

            elif "count" in p or "total" in p:
                c.execute("SELECT COUNT(*) FROM students")
                total = c.fetchone()[0] or 0
                return f"👥 Total students: {total}."

            else:
                return (
                    "🤖 Try asking questions like:\n"
                    "- *Average marks of students*\n"
                    "- *Average attendance*\n"
                    "- *Total number of students*\n"
                    "- Or enter a full SQL query like:*\n"
                    "`SELECT name, marks FROM students WHERE marks > 80;`"
                )

        except Exception as e:
            return f"⚠️ Unexpected error: {e}"


# ---------------- RISK PREDICTION ----------------
def predict_risk(roll_no: str) -> dict:
    """
    Predict the academic risk level of a student based on marks and attendance.

    Args:
        roll_no (str): Roll number of the student.

    Returns:
        dict: Risk details including marks, attendance, performance, and risk level.
    """
    student = get_student_by_roll(roll_no)
    if not student:
        return {"error": "Student not found."}

    # Extract values safely
    marks = student.get("marks", 0)
    attendance = student.get("attendance", 0)
    performance = student.get("performance", "N/A")

    # Risk logic
    if marks < 40 or attendance < 60:
        risk_level = "High Risk 🚨"
        message = "Student is in high risk due to poor performance or very low attendance."
    elif marks < 60 or attendance < 75:
        risk_level = "Moderate Risk ⚠️"
        message = "Student shows moderate risk. Improvement in attendance or marks needed."
    else:
        risk_level = "Low Risk ✅"
        message = "Student is performing well."

    # Return structured result
    return {
        "roll_no": roll_no,
        "name": student.get("name"),
        "course": student.get("course"),
        "section": student.get("section"),
        "marks": marks,
        "attendance": attendance,
        "performance": performance,
        "risk_level": risk_level,
        "message": message
    }


    # generate simple feedback (use Cohere if available)
    feedback = ""
    prompt = (
    f"Generate a short, personalized, motivational feedback (2-3 lines) for a student named "
    f"{student.get('name', 'Student')} with {attendance}% attendance and {marks} marks. "
    f"Use a positive and encouraging tone. Mention one area of improvement if needed."
)  
    if co is not None:
      try:
        # Attempt AI-based feedback using Cohere
        resp = co.chat(model=COHERE_MODEL, message=prompt)
        feedback = resp.text.strip()

        # Safety fallback: if response is empty or generic
        if not feedback or len(feedback) < 10:
            feedback = _fallback_feedback(student.get("name", "Student"), attendance, marks)

      except Exception:
        # Use fallback feedback on any Cohere API failure
        feedback = _fallback_feedback(student.get("name", "Student"), attendance, marks)
    else:
    # Use fallback if Cohere not configured
     feedback = _fallback_feedback(student.get("name", "Student"), attendance, marks)
    
    if "great" in feedback.lower() or "excellent" in feedback.lower():
      feedback += " 🌟"
    elif "improve" in feedback.lower():
      feedback += " 💪"
    elif "good" in feedback.lower():
      feedback += " 👍"
    else:
      feedback += " 🚀"

    return {
    "name": student.get("name", ""),
    "roll_no": roll_no,
    "marks": marks,
    "attendance": attendance,
    "performance": perf,
    "risk_level": risk,
    "feedback": feedback
}

def _fallback_feedback(name: str, attendance: int, marks: int) -> str:
    """Improved fallback feedback with motivational tone and slight variation."""
    
    if attendance < 60 and marks < 50:
        return (
            f"{name}, both your attendance and marks need attention. "
            f"Start small — attend regularly and revise consistently. You’ve got great potential! 💪"
        )
    elif attendance < 60:
        return (
            f"{name}, your attendance is on the lower side. "
            f"Try being more consistent in classes — every session adds up to success! 📅"
        )
    elif marks < 50:
        return (
            f"{name}, your marks can improve with a bit more focus. "
            f"Revise key concepts and don’t hesitate to seek help from teachers. You can do it! 📘"
        )
    elif marks >= 85 and attendance >= 90:
        return (
            f"Outstanding performance, {name}! Your hard work and consistency truly shine. Keep it going strong! 🌟"
        )
    elif marks >= 70:
        return (
            f"Good work, {name}! You’re doing well — keep refining your skills and aim even higher! 🚀"
        )
    else:
        return (
            f"{name}, you’re on the right path! Keep attending regularly and revising often to reach your full potential. 👍"
        )

# ---------------- TIMETABLE HELPER (convenience) ----------------
def get_timetable_for_student(course: str, semester: int, section: str, day: Optional[str] = None):
    """
    Convenience wrapper used by app.py: returns daily (if day provided) or weekly view.
    Delegates to timetable module.
    """
    if tt is None:
        return []
    if day:
        if hasattr(tt, "get_daily_view"):
            return tt.get_daily_view(course, semester, section, day)
        else:
            return tt.get_timetable(course, semester, section, day)
    else:
        if hasattr(tt, "get_weekly_view"):
            return tt.get_weekly_view(course, semester, section)
        else:
            return tt.get_timetable(course, semester, section)

# ---------------- MAIN ----------------
if __name__ == "__main__":
    try:
        create_db()
        print("✅ backend.py: Database initialized and timetable table ensured (if timetable module present).")
    except Exception as e:
        print("❌ Error during backend init:", e)
        traceback.print_exc()
