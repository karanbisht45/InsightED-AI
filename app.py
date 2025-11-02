import streamlit as st
import pandas as pd
import sqlite3
from io import StringIO
from datetime import datetime, date
import matplotlib.pyplot as plt
import cohere

from datetime import datetime, date

def safe_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return date(2003, 1, 1)


# import your backend (the file you already provided)
from backend import (
    create_db, add_student, get_student, get_student_by_roll,
    update_student, delete_student, fetch_all_students, all_rows,
    generate_sql, admin_chatbot_query, predict_risk
)

# ---------------- CONFIG ----------------
DB_FILE = "students.db"
COHERE_API_KEY = "ZDRGnW9Jbj1a6IhwjjTqNimk4BPcxM1bOSn3Hl33"  # you already used this in backend
COHERE_MODEL = "command-r-plus"

co = cohere.Client(COHERE_API_KEY)

st.set_page_config(page_title="InsightED AI — Advanced", page_icon="🎓", layout="wide")
# ---------------- INITIAL DB SETUP & MIGRATIONS ----------------
create_db()  # from your backend (creates students table if not exists)

def ensure_columns_and_objects():
    """Ensure performance, date_of_birth, notifications table, views and triggers exist."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        # ensure columns
        c.execute("PRAGMA table_info(students)")
        cols = [r[1] for r in c.fetchall()]
        if "performance" not in cols:
            c.execute("ALTER TABLE students ADD COLUMN performance TEXT DEFAULT 'Average'")
        if "date_of_birth" not in cols:
            # store as ISO 'YYYY-MM-DD' text for simplicity
            c.execute("ALTER TABLE students ADD COLUMN date_of_birth TEXT")
        if "created_at" not in cols:
            c.execute("ALTER TABLE students ADD COLUMN created_at TEXT DEFAULT (date('now'))")

        # notifications table (simple)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                title TEXT,
                body TEXT,
                type TEXT,       -- 'birthday','performance','admin'
                payload TEXT,
                read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # create view for admin summary (acts like a materialized view)
        c.execute("""
            CREATE VIEW IF NOT EXISTS student_performance_summary AS
            SELECT course,
                   COUNT(*) AS total_students,
                   ROUND(AVG(attendance),2) AS avg_attendance,
                   SUM(CASE WHEN attendance < 75 THEN 1 ELSE 0 END) AS low_attendance,
                   SUM(CASE WHEN performance = 'Excellent' THEN 1 ELSE 0 END) AS top_performers
            FROM students
            GROUP BY course
        """)

        # triggers: auto-set performance after insert and after attendance update
        # SQLite: need IF NOT EXISTS guard — emulate by dropping same-named trigger then creating.
        try:
            c.execute("DROP TRIGGER IF EXISTS trg_auto_performance_insert")
            c.execute("""
                CREATE TRIGGER trg_auto_performance_insert
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
        except Exception:
            pass

        try:
            c.execute("DROP TRIGGER IF EXISTS trg_auto_performance_update")
            c.execute("""
                CREATE TRIGGER trg_auto_performance_update
                AFTER UPDATE OF attendance ON students
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
        except Exception:
            pass

        conn.commit()

ensure_columns_and_objects()

# ---------------- NOTIFICATIONS & MESSAGES ----------------
def push_notification(student_id: str, title: str, body: str, notif_type: str = "admin", payload: str = None):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO notifications (student_id, title, body, type, payload)
            VALUES (?, ?, ?, ?, ?)
        """, (student_id, title, body, notif_type, payload))
        conn.commit()

def get_unread_notifications(limit: int = 50):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, student_id, title, body, type, created_at FROM notifications WHERE read=0 ORDER BY created_at DESC LIMIT ?", (limit,))
        return c.fetchall()

def get_notifications(all_rows: bool = False, limit: int = 200):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        if all_rows:
            c.execute("SELECT id, student_id, title, body, type, read, created_at FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            c.execute("SELECT id, student_id, title, body, type, read, created_at FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,))
        return c.fetchall()

def mark_notification_read(notification_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("UPDATE notifications SET read=1 WHERE id=?", (notification_id,))
        conn.commit()

def generate_erp_notifications():
    """
    Scans students and inserts notifications for:
      - Birthdays (if not already present today for that student)
      - Excellent performers (one-time since created_at or every run? we'll insert if not existing today)
      - Low performers (attendance < 60) -> gentle warning
    """
    today_md = datetime.now().strftime("%m-%d")
    today_iso = date.today().isoformat()

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT student_id, name, date_of_birth, performance, attendance FROM students")
        rows = c.fetchall()

        for sid, name, dob, perf, attendance in rows:
            # Birthday
            if dob:
                try:
                    if dob.strip() and dob[5:7] and dob[8:10]:
                        if dob[5:10] == today_md:
                            # check if there's already a birthday notif for today
                            c.execute("""
                                SELECT 1 FROM notifications
                                WHERE student_id=? AND type='birthday' AND date(created_at)=date('now')
                            """, (sid,))
                            if not c.fetchone():
                                title = f"🎂 Happy Birthday, {name}!"
                                body = f"Happy Birthday {name}! Best wishes from Graphic Era Hill University."
                                c.execute("INSERT INTO notifications (student_id, title, body, type) VALUES (?, ?, ?, 'birthday')",
                                          (sid, title, body))
                except Exception:
                    # If date format weird, skip
                    pass

            # Excellent performer -> congratulatory (insert once per day)
            if perf == "Excellent":
                c.execute("""
                    SELECT 1 FROM notifications
                    WHERE student_id=? AND type='performance' AND date(created_at)=date('now') AND title LIKE 'Congrats%'
                """, (sid,))
                if not c.fetchone():
                    title = f"🌟 Congrats {name}!"
                    body = f"{name}, outstanding performance! Keep it up."
                    c.execute("INSERT INTO notifications (student_id, title, body, type) VALUES (?, ?, ?, 'performance')",
                              (sid, title, body))

            # Low attendance warning (attendance < 60)
            if attendance is not None and attendance < 60:
                c.execute("""
                    SELECT 1 FROM notifications
                    WHERE student_id=? AND type='attendance_warn' AND date(created_at)=date('now')
                """, (sid,))
                if not c.fetchone():
                    title = f"⚠️ Low Attendance: {name}"
                    body = f"{name}, your attendance is {attendance}%. Please meet your mentor."
                    c.execute("INSERT INTO notifications (student_id, title, body, type) VALUES (?, ?, ?, 'attendance_warn')",
                              (sid, title, body))
        conn.commit()

# ---------------- AI FEEDBACK UTIL (Cohere) ----------------
def generate_feedback(name: str, attendance: int) -> str:
    """
    Use Cohere to create a short 1-2 line encouragement or guidance message.
    Falls back to a deterministic message if Cohere call fails.
    """
    prompt = f"Write a friendly 1-2 line motivational feedback for a student named {name} who has attendance {attendance}%. Keep it short and actionable."
    try:
        resp = co.chat(model=COHERE_MODEL, message=prompt, temperature=0.6)
        return resp.text.strip()
    except Exception:
        if attendance < 75:
            return f"{name}, try to attend classes regularly — small improvements every day add up!"
        else:
            return f"{name}, good job — keep the momentum going!"

# ---------------- STREAMLIT UI ----------------
# SESSION KEYS
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""
if "choice" not in st.session_state:
    st.session_state.choice = "➕ Add Student"

# ---------- AUTH (lightweight -- re-use your auth.py if present) ----------
try:
    from auth import create_user_table, signup_user, login_user
    create_user_table()
    HAS_AUTH = True
except Exception:
    HAS_AUTH = False

if not st.session_state.logged_in and HAS_AUTH:
    st.title("🔐 InsightED AI - Login / Signup")
    tab1, tab2 = st.tabs(["Login", "Signup"])
    with tab1:
        uname = st.text_input("Username", key="login_user")
        passwd = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login", key="login_btn"):
            if login_user(uname, passwd):
                st.session_state.logged_in = True
                st.session_state.username = uname
                st.session_state.choice = "➕ Add Student"
                st.rerun()
            else:
                st.error("Invalid credentials ❌")
    with tab2:
        new_user = st.text_input("New Username", key="signup_user")
        new_pass = st.text_input("New Password", type="password", key="signup_pass")
        if st.button("Signup", key="signup_btn"):
            ok, msg = signup_user(new_user, new_pass)
            if ok:
                st.success(msg)
                st.session_state.logged_in = True
                st.session_state.username = new_user
                st.session_state.choice = "➕ Add Student"
                st.rerun()
            else:
                st.error(msg)
    st.stop()

# If auth not available, auto-login local dev user
if not HAS_AUTH:
    st.session_state.logged_in = True
    st.session_state.username = "dev_user"

# ------------- SIDEBAR & Menu -------------
st.sidebar.success(f"👤 Logged in as: {st.session_state.username}")
if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

menu = st.sidebar.radio(
    "📚 InsightED AI Menu",
    ["➕ Add Student", "📋 View / Filter Students", "🔎 Search", "✏️ Update", "🗑️ Delete",
     "🔔 Notifications", "🤖 InsightBot", "📊 Performance Insights", "🏅 Feedback Generator"],
    index=0
)
st.session_state.choice = menu
choice = menu

st.title("🎓 InsightED AI — Advanced Student DBMS")

# quick topbar: show unread notifications count
unread = len(get_unread_notifications())
if unread:
    st.sidebar.markdown(f"🔔 **{unread}** new notification(s)")

# Generate ERP notifications each time user opens dashboard (light-weight)
generate_erp_notifications()

# ---------------- HELPERS ----------------
def to_df(rows):
    return pd.DataFrame(rows, columns=[
        "student_id", "roll_no", "name", "age", "gender", "category",
        "address", "course", "current_year", "semester", "type", "room_no",
        "hostel_building", "block", "bus_no", "route", "attendance", "marks",
        "performance", "date_of_birth", "created_at"
    ]) if rows else pd.DataFrame(columns=[
        "student_id", "roll_no", "name", "age", "gender", "category",
        "address", "course", "current_year", "semester", "type", "room_no",
        "hostel_building", "block", "bus_no", "route", "attendance", "marks",
        "performance", "date_of_birth", "created_at"
    ])


# ---------------- ADD STUDENT ----------------
if choice == "➕ Add Student":
    st.subheader("➕ Add New Student (Advanced)")
    colA, colB, colC = st.columns(3)
    with colA:
        student_id = st.text_input("Student ID (Unique)")
        roll_no = st.text_input("Roll No (Unique)")
        name = st.text_input("Full Name")
        age = st.number_input("Age", min_value=1, max_value=120, step=1, key="add_age")
    with colB:
        gender = st.selectbox("Gender", ["Male", "Female", "Others"], key="add_gender")
        category = st.selectbox("Category", ["General", "OBC", "SC", "ST", "Other"], key="add_cat")
        course = st.text_input("Course (e.g., B.Tech CSE)")
        address = st.text_area("Address", height=90)
    with colC:
        current_year = st.selectbox("Current Year", list(range(1,6)), key="add_year")
        semester = st.selectbox("Semester", list(range(1,9)), key="add_sem")
        type_ = st.radio("Student Type", ["Hosteller", "Day Scholar"], key="add_type")
    # hosteller/day scholar fields
    room_no = hostel_building = block = bus_no = route = None
    if type_ == "Hosteller":
        colH1, colH2, colH3 = st.columns(3)
        with colH1: room_no = st.text_input("Room No")
        with colH2: hostel_building = st.text_input("Hostel Building")
        with colH3: block = st.text_input("Block")
    else:
        colD1, colD2 = st.columns(2)
        with colD1: bus_no = st.text_input("Bus No")
        with colD2: route = st.text_input("Route")
    attendance = st.number_input("Attendance (%)", min_value=0, max_value=100, step=1, value=80)
    dob = st.date_input("Date of Birth (YYYY-MM-DD)", value=date(2003,1,1))
    if st.button("Add Student", type="primary"):
        required = [student_id.strip(), roll_no.strip(), name.strip(), course.strip(), address.strip()]
        if not all(required):
            st.warning("Please fill required fields: Student ID, Roll No, Name, Course, Address.")
        else:
            # insert
            ok, msg = add_student(
                student_id.strip(), roll_no.strip(), name.strip(), int(age),
                gender, category, address.strip(), course.strip(), int(current_year),
                int(semester), type_, room_no, hostel_building, block, bus_no, route, int(attendance)
            )
            # set date_of_birth and created_at and performance (update)
            if ok:
                # set dob and created_at via update
                update_student(student_id.strip(), date_of_birth=dob.isoformat(), created_at=date.today().isoformat())
                st.success(f"Student '{name}' added successfully ✅")
                # generate immediate notification for excellent performance (if any) or birthday
                generate_erp_notifications()
            else:
                st.error(f"❌ {msg}")

# ---------------- VIEW / FILTER ----------------
elif choice == "📋 View / Filter Students":
    st.subheader("📋 View & Filter Students")
    with st.expander("Filters", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            type_filter = st.selectbox("Type", ["All", "Hosteller", "Day Scholar"], index=0)
            gender_filter = st.selectbox("Gender", ["All", "Male", "Female", "Others"], index=0)
        with col2:
            category_filter = st.multiselect("Category", ["General", "OBC", "SC", "ST", "Other"], default=[])
            course_filter = st.multiselect("Course", ["B.Tech", "M.Tech", "MBA", "B.Sc", "M.Sc", "Other"], default=[])
        with col3:
            year_filter = st.multiselect("Year", list(range(1,6)), default=[])
        with col4:
            sem_filter = st.multiselect("Semester", list(range(1,9)), default=[])
        filters = {
            "type": None if type_filter == "All" else [type_filter],
            "gender": None if gender_filter == "All" else [gender_filter],
            "category": category_filter or None,
            "course_contains": course_filter[0] if course_filter else None,
            "year_in": year_filter or None,
            "sem_in": sem_filter or None,
        }

    rows = fetch_all_students(filters)
    df = to_df(rows)
    st.write(f"Total: **{len(df)}** records")
    st.dataframe(df, use_container_width=True)
    if st.checkbox("📊 Show summary statistics (GROUP BY course)"):
        with sqlite3.connect(DB_FILE) as conn:
            try:
                summary = pd.read_sql_query("SELECT * FROM student_performance_summary", conn)
                st.dataframe(summary)
                st.bar_chart(summary.set_index("course")[["total_students", "top_performers", "low_attendance"]])
            except Exception as e:
                st.warning("Could not fetch summary view. " + str(e))

    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False)
    st.download_button("⬇️ Download CSV", data=csv_buf.getvalue(), file_name="students.csv", mime="text/csv")

# ---------------- SEARCH ----------------
elif choice == "🔎 Search":
    st.subheader("🔎 Search Student")
    tab1, tab2 = st.tabs(["By Student ID", "By Roll No"])
    with tab1:
        sid = st.text_input("Student ID", key="search_sid")
        if st.button("Search by ID"):
            row = get_student(sid.strip())
            if row:
                st.dataframe(to_df([row]))
            else:
                st.warning("No student found.")
    with tab2:
        rno = st.text_input("Roll No", key="search_rno")
        if st.button("Search by Roll No"):
            row = get_student_by_roll(rno.strip())
            if row:
                st.dataframe(to_df([row]))
            else:
                st.warning("No student found.")

# ---------------- UPDATE ----------------
elif choice == "✏️ Update":
    st.subheader("✏️ Update Student")

    if "upd_student" not in st.session_state:
        st.session_state.upd_student = None

    sid = st.text_input("Enter Student ID to update", key="upd_sid")

    if st.button("Fetch", key="upd_fetch"):
        row = get_student(sid.strip())
        st.session_state.upd_student = row if row else None
        if not row:
            st.error("Student not found.")

    if st.session_state.upd_student:
        # Directly use dictionary keys from backend
        row = st.session_state.upd_student

        student_id = row.get("student_id")
        roll_no = row.get("roll_no", "")
        name = row.get("name", "")
        age = int(row.get("age", 18))
        gender = row.get("gender", "Male")
        category = row.get("category", "General")
        address = row.get("address", "")
        course = row.get("course", "")
        current_year = int(row.get("current_year", 1))
        semester = int(row.get("semester", 1))
        type_ = row.get("type", "Hosteller")
        room_no = row.get("room_no", "")
        hostel_building = row.get("hostel_building", "")
        block = row.get("block", "")
        bus_no = row.get("bus_no", "")
        route = row.get("route", "")
        attendance = int(row.get("attendance", 80))
        performance = row.get("performance", "")
        date_of_birth = row.get("date_of_birth", "2003-01-01")
        created_at = row.get("created_at", "")

        # ✅ DOB Safe Conversion
        try:
            dob_value = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        except Exception:
            dob_value = date(2003, 1, 1)

        # ---------- UI FIELDS ----------
        colA, colB, colC = st.columns(3)
        with colA:
            new_roll = st.text_input("Roll No (Unique)", value=roll_no, key="upd_roll")
            new_name = st.text_input("Full Name", value=name, key="upd_name")
            new_age = st.number_input("Age", min_value=1, max_value=120, value=age, key="upd_age")

        with colB:
            new_gender = st.selectbox("Gender", ["Male", "Female", "Others"],
                                      index=["Male", "Female", "Others"].index(gender), key="upd_gender")
            new_category = st.selectbox("Category", ["General", "OBC", "SC", "ST", "Other"],
                                        index=["General", "OBC", "SC", "ST", "Other"].index(category), key="upd_cat")
            new_course = st.text_input("Course", value=course, key="upd_course")

        with colC:
            new_address = st.text_area("Address", value=address, height=90, key="upd_addr")
            new_year = st.selectbox("Current Year", list(range(1,6)), index=(current_year-1), key="upd_year")
            new_sem = st.selectbox("Semester", list(range(1,9)), index=(semester-1), key="upd_sem")

        new_type = st.radio("Student Type", ["Hosteller", "Day Scholar"],
                            index=["Hosteller", "Day Scholar"].index(type_), key="upd_type")

        new_room = new_hostel = new_block = new_bus = new_route = None
        if new_type == "Hosteller":
            colH1, colH2, colH3 = st.columns(3)
            with colH1: new_room = st.text_input("Room No", value=room_no, key="upd_room")
            with colH2: new_hostel = st.text_input("Hostel Building", value=hostel_building, key="upd_hostel")
            with colH3: new_block = st.text_input("Block", value=block, key="upd_block")
        else:
            colD1, colD2 = st.columns(2)
            with colD1: new_bus = st.text_input("Bus No", value=bus_no, key="upd_bus")
            with colD2: new_route = st.text_input("Route", value=route, key="upd_route")

        new_attendance = st.number_input("Attendance (%)", min_value=0, max_value=100,
                                         value=attendance, key="upd_att")
        new_dob = st.date_input("Date of Birth (YYYY-MM-DD)", value=dob_value)

        if st.button("Save Changes", type="primary", key="upd_save"):
            fields = {
                "roll_no": new_roll.strip(),
                "name": new_name.strip(),
                "age": int(new_age),
                "gender": new_gender,
                "category": new_category,
                "address": new_address.strip(),
                "course": new_course.strip(),
                "current_year": int(new_year),
                "semester": int(new_sem),
                "type": new_type,
                "room_no": new_room,
                "hostel_building": new_hostel,
                "block": new_block,
                "bus_no": new_bus,
                "route": new_route,
                "attendance": int(new_attendance),
                "date_of_birth": new_dob.isoformat()
            }
            ok, msg = update_student(student_id, **fields)
            if ok:
                st.success("Student updated successfully ✅")
                st.session_state.upd_student = None
                generate_erp_notifications()
            else:
                st.error(f"❌ {msg}")


# ---------------- DELETE ----------------
elif choice == "🗑️ Delete":
    st.subheader("🗑️ Delete Student")
    sid = st.text_input("Student ID to delete", key="del_sid")
    confirm = st.checkbox("I'm sure", key="del_confirm")
    if st.button("Delete", type="secondary", key="del_btn"):
        if not confirm:
            st.warning("Please confirm deletion.")
        else:
            row = get_student(sid.strip())
            if not row:
                st.error("Student ID not found.")
            else:
                delete_student(sid.strip())
                st.success("Record deleted ✅")

# ---------------- NOTIFICATIONS ----------------
elif choice == "🔔 Notifications":
    st.subheader("🔔 Notification Center")
    if st.button("🔄 Refresh Notifications"):
        generate_erp_notifications()
        st.rerun()

    unread_list = get_unread_notifications()
    if unread_list:
        st.markdown("### 🔔 Unread")
        for nid, sid, title, body, ntype, created in unread_list:
            st.info(f"**{title}**\n\n{body}\n\n_Type: {ntype}_ — {created}")
            cols = st.columns([1, 6])
            with cols[0]:
                if st.button("Mark Read", key=f"mr_{nid}"):
                    mark_notification_read(nid)
                    st.rerun()
    else:
        st.info("No new notifications.")

    st.markdown("---")
    st.markdown("### 📜 All Notifications")
    all_notifs = get_notifications(all_rows=True, limit=500)
    dfn = pd.DataFrame(all_notifs, columns=["id", "student_id", "title", "body", "type", "read", "created_at"])
    st.dataframe(dfn)

# ---------------- INSIGHTBOT (Natural language -> SQL) ----------------
elif choice == "🤖 InsightBot":
    st.subheader("🤖 InsightBot - Ask in plain English (SELECT only)")
    user_query = st.text_input("Enter your query (e.g., 'Show students with attendance < 60')")
    if st.button("Run Query") and user_query:
        try:
            sql_query = generate_sql(user_query).strip()
        except Exception:
            sql_query = ""
        if not sql_query:
            st.info("🤔 AI could not generate a query. Try rephrasing.")
        else:
            st.write("📄 Generated SQL:", sql_query)
            try:
                conn = sqlite3.connect(DB_FILE)
                df = pd.read_sql_query(sql_query, conn)
                conn.close()
                if not df.empty:
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("No results found.")
            except Exception as e:
                st.warning("⚠️ Could not execute the query. " + str(e))

# ---------------- PERFORMANCE INSIGHTS ----------------
elif choice == "📊 Performance Insights":
    st.subheader("📊 Performance Insights & Reports")
    with sqlite3.connect(DB_FILE) as conn:
        try:
            df = pd.read_sql_query("SELECT * FROM student_performance_summary", conn)
            st.dataframe(df)
            st.markdown("#### Course-wise charts")
            if not df.empty:
                st.bar_chart(df.set_index("course")[["total_students", "top_performers", "low_attendance"]])
        except Exception as e:
            st.warning("Could not fetch performance summary. " + str(e))

    # top N performers per course using GROUP BY & window-like behavior simulated in pandas
    if st.checkbox("Show Top performers per course"):
        rows = all_rows()
        dfr = to_df(rows)
        if not dfr.empty:
            dfr["avg_attendance"] = dfr["attendance"]  # placeholder if you later store marks you can average
            topn = dfr.sort_values(["course", "avg_attendance"], ascending=[True, False]).groupby("course").head(3)
            st.dataframe(topn[["course", "name", "roll_no", "attendance", "performance"]])
        else:
            st.info("No student data.")

# ---------------- FEEDBACK GENERATOR ----------------
elif choice == "🏅 Feedback Generator":
    st.subheader("🏅 AI Feedback Generator")
    sid = st.text_input("Enter Student ID for feedback", key="fb_sid")
    
    if st.button("Generate Feedback"):
        row = get_student(sid.strip())
        
        if not row:
            st.error("Student not found.")
        else:
            name = row["name"]
            
            # Correct attendance access
            attendance = row["attendance"] if "attendance" in row.keys() else 80
            
            fb = generate_feedback(name, attendance)
            st.success(fb)

# ---------------- END ----------------
st.markdown("---")

