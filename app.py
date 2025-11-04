import streamlit as st
import pandas as pd
import sqlite3
from io import StringIO
from datetime import datetime, date, time
import matplotlib.pyplot as plt
import cohere
import inspect
import os

from backend import (
    create_db, add_student, get_student, get_student_by_roll,
    update_student, delete_student, fetch_all_students, all_rows,
    generate_sql, admin_chatbot_query, predict_risk
)

import timetable as tt  

# ---------------- CONFIG ----------------
DB_FILE = "students.db"
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "ZDRGnW9Jbj1a6IhwjjTqNimk4BPcxM1bOSn3Hl33")
COHERE_MODEL = os.getenv("COHERE_MODEL", "command-r-plus")

# safe cohere client creation
try:
    co = cohere.Client(COHERE_API_KEY)
except Exception:
    co = None

st.set_page_config(page_title="InsightED AI — Advanced", page_icon="🎓", layout="wide")

# ---------------- INITIAL DB SETUP ----------------
create_db()  

try:
    if hasattr(tt, "create_timetable_table"):
        tt.create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            c.execute("SELECT COUNT(*) FROM timetable")
            count = c.fetchone()[0]
        except Exception:
            count = 0
    if count == 0:
        if hasattr(tt, "auto_generate_timetable"):
            default_courses = tt.get_all_courses() if hasattr(tt, "get_all_courses") else ["BCA", "B.Tech", "BBA", "MBA"]
            try:
                tt.auto_generate_timetable(course_list=default_courses, semesters=getattr(tt, "get_all_semesters", lambda: [1,2,3,4,5,6])(), sections=getattr(tt, "get_all_sections", lambda c: ["A","B"])(None))
            except TypeError:
                # fallback if tt.auto_generate_timetable signature differs
                tt.auto_generate_timetable(default_courses, semesters=6, sections=["A", "B"])
            st.experimental_rerun()
except Exception as e:
    print("Could not ensure timetable table or auto-generate:", e)

# ---------------- HELPERS ----------------
def to_df(rows):
    """Convert student rows (tuples/dicts) to DataFrame with consistent columns."""
    cols = [
        "student_id", "roll_no", "name", "age", "gender", "category",
        "address", "course", "current_year", "semester", "type", "room_no",
        "hostel_building", "block", "bus_no", "route", "attendance", "marks",
        "performance", "date_of_birth", "created_at"
    ]
    try:
        if not rows:
            return pd.DataFrame(columns=cols)
        if isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
            existing = [c for c in cols if c in df.columns]
            return df[existing] if existing else df
        else:
            return pd.DataFrame(rows, columns=cols)
    except Exception:
        return pd.DataFrame(rows)

def is_admin_user() -> bool:
    return True

# ---------------- SESSION KEYS ----------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = True
if "username" not in st.session_state:
    st.session_state.username = "admin"
if "choice" not in st.session_state:
    st.session_state.choice = "➕ Add Student"

# ------------- SIDEBAR & MENU -------------
st.sidebar.success(f"👤 Logged in as: {st.session_state.username}")
if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

menu = st.sidebar.radio(
    "📚 InsightED AI Menu",
    ["➕ Add Student", "📋 View / Filter Students", "🔎 Search", "✏️ Update", "🗑️ Delete",
     "🔔 Notifications", "🤖 InsightBot", "📊 Performance Insights", "🏅 Feedback Generator", "🗓 Timetable"],
    index=0
)
st.session_state.choice = menu
choice = menu

st.title("🎓 InsightED AI — Advanced Student DBMS (Admin)")

# ---------------- NOTIFICATIONS ----------------
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
        try:
            c.execute("SELECT id, student_id, title, body, type, created_at FROM notifications WHERE read=0 ORDER BY created_at DESC LIMIT ?", (limit,))
            return c.fetchall()
        except Exception:
            return []

def get_notifications(all_rows: bool = False, limit: int = 200):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            c.execute("SELECT id, student_id, title, body, type, read, created_at FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,))
            return c.fetchall()
        except Exception:
            return []

def mark_notification_read(notification_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("UPDATE notifications SET read=1 WHERE id=?", (notification_id,))
        conn.commit()

def generate_erp_notifications():
    """Generate birthday/performance/attendance notifications."""
    today_md = datetime.now().strftime("%m-%d")
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            c.execute("SELECT student_id, name, date_of_birth, performance, attendance FROM students")
            rows = c.fetchall()
        except Exception:
            rows = []
        for sid, name, dob, perf, attendance in rows:
            # Birthday
            if dob:
                try:
                    if dob.strip() and dob[5:10] == today_md:
                        c.execute("SELECT 1 FROM notifications WHERE student_id=? AND type='birthday' AND date(created_at)=date('now')", (sid,))
                        if not c.fetchone():
                            title = f"🎂 Happy Birthday, {name}!"
                            body = f"Happy Birthday {name}! Best wishes from the institute."
                            c.execute("INSERT INTO notifications (student_id, title, body, type) VALUES (?, ?, ?, 'birthday')", (sid, title, body))
                except Exception:
                    pass
            if perf == "Excellent":
                c.execute("SELECT 1 FROM notifications WHERE student_id=? AND type='performance' AND date(created_at)=date('now')", (sid,))
                if not c.fetchone():
                    title = f"🌟 Congrats {name}!"
                    body = f"{name}, outstanding performance! Keep it up."
                    c.execute("INSERT INTO notifications (student_id, title, body, type) VALUES (?, ?, ?, 'performance')", (sid, title, body))
            if attendance is not None and attendance < 60:
                c.execute("SELECT 1 FROM notifications WHERE student_id=? AND type='attendance_warn' AND date(created_at)=date('now')", (sid,))
                if not c.fetchone():
                    title = f"⚠️ Low Attendance: {name}"
                    body = f"{name}, your attendance is {attendance}%. Please meet your mentor."
                    c.execute("INSERT INTO notifications (student_id, title, body, type) VALUES (?, ?, ?, 'attendance_warn')", (sid, title, body))
        conn.commit()

generate_erp_notifications()

unread = len(get_unread_notifications())
if unread:
    st.sidebar.markdown(f"🔔 **{unread}** new notification(s)")

# ---------------- AI FEEDBACK UTIL (Cohere) ----------------
def generate_feedback(name: str, attendance: int) -> str:
    prompt = f"Write a friendly 1-2 line motivational feedback for a student named {name} who has attendance {attendance}%. Keep it short and actionable."
    if co:
        try:
            resp = co.chat(model=COHERE_MODEL, message=prompt, temperature=0.6)
            return resp.text.strip()
        except Exception:
            pass
    if attendance < 75:
        return f"{name}, try to attend classes regularly — small improvements every day add up!"
    else:
        return f"{name}, good job — keep the momentum going!"

# ===================== ADD STUDENT SECTION =====================
if choice == "➕ Add Student":
    st.markdown("<h2 style='color:#3B82F6;'>🎓 Add New Student (Advanced Form)</h2>", unsafe_allow_html=True)
    st.markdown("<hr style='margin-top:-10px;margin-bottom:15px;'>", unsafe_allow_html=True)

    with st.container():
        st.markdown("### 🧍 Basic Information")
        with st.expander("Click to expand / collapse", expanded=True):
            colA, colB, colC = st.columns(3)
            with colA:
                student_id = st.text_input("🆔 Student ID", placeholder="Enter unique Student ID")
                roll_no = st.text_input("🔢 Roll No", placeholder="Enter unique Roll Number")
                name = st.text_input("👤 Full Name", placeholder="Enter full name")
                age = st.number_input("🎂 Age", min_value=1, max_value=120, step=1, key="add_age", help="Enter the student's age")
            with colB:
                gender = st.selectbox("⚧ Gender", ["Male", "Female", "Others"], key="add_gender")
                category = st.selectbox("🏷️ Category", ["General", "OBC", "SC", "ST", "Other"], key="add_cat")
                dob = st.date_input("📅 Date of Birth", value=date(2003,1,1))
                attendance = st.number_input("📊 Attendance (%)", min_value=0, max_value=100, step=1, value=80)
            with colC:
                address = st.text_area("🏠 Address", height=120, placeholder="Enter full address")

    st.markdown("### 🎓 Academic Information")
    with st.expander("Show/Hide Academic Details", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            course = st.text_input("📘 Course", placeholder="e.g., B.Tech CSE")
        with col2:
            current_year = st.selectbox("📅 Current Year", list(range(1,6)), key="add_year")
        with col3:
            semester = st.selectbox("🧮 Semester", list(range(1,9)), key="add_sem")

    st.markdown("### 🏡 Accommodation Details")
    with st.expander("Show/Hide Accommodation", expanded=True):
        type_ = st.radio("🚏 Student Type", ["Hosteller", "Day Scholar"], horizontal=True, key="add_type")

        if type_ == "Hosteller":
            st.info("🏠 Please provide hostel details below.")
            colH1, colH2, colH3 = st.columns(3)
            with colH1:
                room_no = st.text_input("Room No", placeholder="e.g., A-102")
            with colH2:
                hostel_building = st.text_input("Hostel Building", placeholder="e.g., Aryabhatta")
            with colH3:
                block = st.text_input("Block", placeholder="e.g., Block B")
            bus_no = route = None
        else:
            st.info("🚌 Please provide day scholar details below.")
            colD1, colD2 = st.columns(2)
            with colD1:
                bus_no = st.text_input("Bus No", placeholder="e.g., 12B")
            with colD2:
                route = st.text_input("Route", placeholder="e.g., Sector 62 to Campus")
            room_no = hostel_building = block = None

    st.markdown("<br>", unsafe_allow_html=True)

    # ---------------------- SUBMIT SECTION ----------------------
    st.markdown("### ✅ Final Submission")
    if st.button("💾 Add Student", type="primary", use_container_width=True):
        required = [student_id.strip(), roll_no.strip(), name.strip(), course.strip(), address.strip()]
        if not all(required):
            st.warning("⚠️ Please fill all required fields: Student ID, Roll No, Name, Course, and Address.")
        else:
            ok, msg = add_student(
                student_id.strip(), roll_no.strip(), name.strip(), int(age),
                gender, category, address.strip(), course.strip(), int(current_year),
                int(semester), type_, room_no, hostel_building, block, bus_no, route, int(attendance)
            )
            if ok:
                _ok, _msg = update_student(student_id.strip(), date_of_birth=dob.isoformat(), created_at=date.today().isoformat())
                st.success(f"✅ {msg}")
                st.balloons()
                generate_erp_notifications()
            else:
                st.error(f"❌ {msg}")

# ---------- VIEW / FILTER ----------
elif choice == "📋 View / Filter Students":
    st.markdown("<h2 style='color:#3B82F6;'>📋 View & Filter Students</h2>", unsafe_allow_html=True)
    st.markdown("<hr style='margin-top:-10px;margin-bottom:10px;'>", unsafe_allow_html=True)

    # -------- FILTERS PANEL --------
    with st.expander("🔍 Filters & Search", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            type_filter = st.selectbox("🏠 Type", ["All", "Hosteller", "Day Scholar"], index=0)
            gender_filter = st.selectbox("⚧ Gender", ["All", "Male", "Female", "Others"], index=0)
        with col2:
            category_filter = st.multiselect("🏷️ Category", ["General", "OBC", "SC", "ST", "Other"], default=[])
            course_filter = st.multiselect("📘 Course", ["B.Tech", "M.Tech", "MBA", "B.Sc", "M.Sc", "Other"], default=[])
        with col3:
            year_filter = st.multiselect("📅 Year", list(range(1, 6)), default=[])
        with col4:
            sem_filter = st.multiselect("🧮 Semester", list(range(1, 9)), default=[])

        filters = {
            "type": None if type_filter == "All" else [type_filter],
            "gender": None if gender_filter == "All" else [gender_filter],
            "category": category_filter or None,
            "course_contains": course_filter[0] if course_filter else None,
            "year_in": year_filter or None,
            "sem_in": sem_filter or None,
        }

        st.markdown("<br>", unsafe_allow_html=True)
        st.info("Tip 💡: Apply multiple filters to narrow down your results. You can also download filtered data below.")

    # -------- FETCH FILTERED DATA --------
    rows = fetch_all_students(filters)
    df = to_df(rows)

    # --------- METRICS CARDS ----------
    if not df.empty:
        total = len(df)
        hostellers = len(df[df["type"] == "Hosteller"])
        day_scholars = len(df[df["type"] == "Day Scholar"])
        avg_att = df["attendance"].mean() if "attendance" in df.columns else 0

        colA, colB, colC, colD = st.columns(4)
        colA.metric("👥 Total Students", total)
        colB.metric("🏠 Hostellers", hostellers)
        colC.metric("🚌 Day Scholars", day_scholars)
        colD.metric("📊 Avg Attendance", f"{avg_att:.1f}%")

    st.markdown("<br>", unsafe_allow_html=True)

    # ----------- DISPLAY TABLE -----------
    st.markdown("### 🧾 Student Records")
    st.dataframe(df, use_container_width=True, height=400)

    # --------- SUMMARY STATISTICS ---------
    with st.expander("📊 View Summary Statistics", expanded=False):
        st.markdown("#### Course-wise Performance Overview")
        with sqlite3.connect(DB_FILE) as conn:
            try:
                summary = pd.read_sql_query("SELECT * FROM student_performance_summary", conn)
                if not summary.empty:
                    st.dataframe(summary, use_container_width=True)
                    st.bar_chart(summary.set_index("course")[["total_students", "low_attendance"]])
                else:
                    st.info("No summary data available.")
            except Exception as e:
                st.warning("⚠️ Could not fetch summary view. " + str(e))

    # ---------- DOWNLOAD OPTION ----------
    st.markdown("<br>", unsafe_allow_html=True)
    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False)

    st.download_button(
        label="⬇️ Download Filtered Data (CSV)",
        data=csv_buf.getvalue(),
        file_name="students_filtered.csv",
        mime="text/csv",
        type="primary",
        use_container_width=True
    )

# ---------- SEARCH ----------
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

# ---------- UPDATE ----------
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
        date_of_birth = row.get("date_of_birth", "2003-01-01")

        try:
            dob_value = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        except Exception:
            dob_value = date(2003,1,1)

        colA, colB, colC = st.columns(3)
        with colA:
            new_roll = st.text_input("Roll No (Unique)", value=roll_no, key="upd_roll")
            new_name = st.text_input("Full Name", value=name, key="upd_name")
            new_age = st.number_input("Age", min_value=1, max_value=120, value=age, key="upd_age")
        with colB:
            new_gender = st.selectbox("Gender", ["Male", "Female", "Others"],
                                      index=["Male","Female","Others"].index(gender), key="upd_gender")
            new_category = st.selectbox("Category", ["General","OBC","SC","ST","Other"],
                                        index=["General","OBC","SC","ST","Other"].index(category), key="upd_cat")
            new_course = st.text_input("Course", value=course, key="upd_course")
        with colC:
            new_address = st.text_area("Address", value=address, height=90, key="upd_addr")
            new_year = st.selectbox("Current Year", list(range(1,6)), index=(current_year-1), key="upd_year")
            new_sem = st.selectbox("Semester", list(range(1,9)), index=(semester-1), key="upd_sem")

        new_type = st.radio("Student Type", ["Hosteller","Day Scholar"],
                            index=["Hosteller","Day Scholar"].index(type_), key="upd_type")

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

        new_attendance = st.number_input("Attendance (%)", min_value=0, max_value=100, value=attendance, key="upd_att")
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
                st.success(msg)
                st.session_state.upd_student = None
                generate_erp_notifications()
            else:
                st.error(msg)

# ---------- DELETE ----------
elif choice == "🗑️ Delete":
    st.subheader("🗑️ Delete Student")
    sid = st.text_input("Student ID to delete", key="del_sid")
    confirm = st.checkbox("I'm sure", key="del_confirm")
    if st.button("Delete", type="secondary", key="del_btn"):
        if not confirm:
            st.warning("Please confirm deletion.")
        else:
            try:
                res = delete_student(sid.strip())
                if isinstance(res, tuple):
                    ok, msg = res
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
                elif res is True or res is None:
                    st.success("Record deleted ✅")
                else:
                    st.error("Could not delete record.")
            except Exception as e:
                st.error(f"Error: {e}")

# ---------- NOTIFICATIONS ----------
elif choice == "🔔 Notifications":
    st.subheader("🔔 Notification Center")
    if st.button("🔄 Refresh Notifications"):
        generate_erp_notifications()
        st.experimental_rerun()

    unread_list = get_unread_notifications()
    if unread_list:
        st.markdown("### 🔔 Unread")
        for nid, sid, title, body, ntype, created in unread_list:
            st.info(f"**{title}**\n\n{body}\n\n_Type: {ntype}_ — {created}")
            cols = st.columns([1, 6])
            with cols[0]:
                if st.button("Mark Read", key=f"mr_{nid}"):
                    mark_notification_read(nid)
                    st.experimental_rerun()
    else:
        st.info("No new notifications.")

    st.markdown("---")
    st.markdown("### 📜 All Notifications")
    all_notifs = get_notifications(all_rows=True, limit=500)
    dfn = pd.DataFrame(all_notifs, columns=["id", "student_id", "title", "body", "type", "read", "created_at"])
    st.dataframe(dfn)

# ---------- INSIGHTBOT ----------
elif choice == "🤖 InsightBot":
    st.markdown("""
    <h2 style='text-align: center; color: #00BFFF;'>🤖 InsightBot</h2>
    <p style='text-align: center; color: gray; font-size: 17px;'>
    Ask your database anything in plain English — <b>InsightBot</b> will translate it into an SQL query for you!<br>
    (Safe Mode: <b>SELECT-only</b> queries)
    </p>
    """, unsafe_allow_html=True)

    user_query = st.text_input("💬 Type your question here (e.g., 'Show students with attendance < 60')")

    st.markdown("<hr style='border: 1px solid #ccc;'>", unsafe_allow_html=True)

    run_query = st.button("🚀 Run Query", use_container_width=True)

    if run_query and user_query:
        with st.spinner("🤖 Generating SQL query using AI..."):
            try:
                sql_query = generate_sql(user_query).strip()
            except Exception as e:
                sql_query = ""
                st.error(f"❌ Error: {e}")

        if not sql_query:
            st.warning("🤔 AI couldn’t generate a valid SQL query. Try rephrasing your question.")
        else:
            st.markdown(f"""
            <div style='background-color: #1e1e1e; color: #00FF7F; padding: 10px;
                        border-radius: 10px; font-family: monospace; font-size: 15px;'>
            <b>📄 Generated SQL:</b><br><code>{sql_query}</code>
            </div>
            """, unsafe_allow_html=True)

            try:
                conn = sqlite3.connect(DB_FILE)
                df = pd.read_sql_query(sql_query, conn)
                conn.close()

                if not df.empty:
                    st.success("✅ Query executed successfully!")
                    st.dataframe(df, use_container_width=True, height=400)

                    numeric_cols = df.select_dtypes(include=['int64', 'float64']).columns
                    if not numeric_cols.empty:
                        st.markdown("### 📊 Quick Insights")
                        st.write(df.describe())
                else:
                    st.info("ℹ️ No results found for this query.")

            except Exception as e:
                st.warning(f"⚠️ Could not execute the query.<br><b>Error:</b> {e}", unsafe_allow_html=True)

    with st.expander("💡 Example Queries"):
        st.markdown("""
        - *Show all students where attendance > 80*  
        - *Average marks by department*  
        - *Count students in Computer Science*  
        - *List top 5 students by marks*  
        """)

    st.markdown("""
    <style>
    .stButton>button {
        background: linear-gradient(90deg, #00BFFF, #1E90FF);
        color: white;
        border: none;
        border-radius: 10px;
        font-size: 16px;
        font-weight: bold;
        transition: 0.3s;
    }
    .stButton>button:hover {
        background: linear-gradient(90deg, #1E90FF, #00BFFF);
        transform: scale(1.02);
    }
    </style>
    """, unsafe_allow_html=True)


# ---------- RISK PREDICTION DASHBOARD ----------
elif choice == "📊 Performance Insights":
    st.subheader("⚠️ Attendance Risk Prediction Dashboard")

    with sqlite3.connect(DB_FILE) as conn:
        try:
            df = pd.read_sql_query("SELECT * FROM students", conn)

            if df.empty:
                st.info("No student data found in the database.")
            else:
                for col in ["course", "attendance", "roll_no", "name"]:
                    if col not in df.columns:
                        df[col] = "N/A" if col == "course" else 0

                course_filter = st.selectbox(
                    "🎓 Select Course",
                    ["All"] + sorted(df["course"].dropna().unique().tolist())
                )

                filtered_df = df.copy()
                if course_filter != "All":
                    filtered_df = filtered_df[filtered_df["course"] == course_filter]

                filtered_df["Risk_Status"] = filtered_df["attendance"].apply(
                    lambda x: "⚠️ At Risk (<75%)" if x < 75 else "✅ Safe"
                )

                total_students = len(filtered_df)
                at_risk = len(filtered_df[filtered_df["attendance"] < 75])
                safe = total_students - at_risk

                st.markdown(f"### 📊 Summary ({course_filter if course_filter != 'All' else 'All Courses'})")
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Students", total_students)
                col2.metric("At Risk (<75%)", at_risk)
                col3.metric("Safe (≥75%)", safe)

                if at_risk > 0:
                    st.markdown("### ⚠️ Alerts for Students Below 75% Attendance")
                    risky_students = filtered_df[filtered_df["attendance"] < 75][
                        ["roll_no", "name", "course", "attendance"]
                    ].sort_values(by="attendance")

                    for _, row in risky_students.iterrows():
                        st.warning(
                            f"🚨 {row['name']} ({row['roll_no']}) — {row['attendance']}% attendance in {row['course']}."
                        )
                else:
                    st.success("✅ All students have 75% or higher attendance!")

        except Exception as e:
            st.error(f"Error generating risk insights: {str(e)}")

# ---------- FEEDBACK GENERATOR ----------
elif choice == "🏅 Feedback Generator":
    st.subheader("🏅 AI-Powered Student Feedback Generator")
    st.markdown("✨ _Get personalized, AI-generated feedback based on a student's performance and attendance!_")

    sid = st.text_input("🔍 Enter Student ID to Generate Feedback", key="fb_sid")

    if st.button("🚀 Generate Smart Feedback"):
        row = get_student(sid.strip())
        if not row:
            st.error("⚠️ Student not found. Please check the ID and try again.")
        else:
            name = row.get("name", "Student")
            course = row.get("course", "N/A")
            attendance = row.get("attendance", 80)
            grade = row.get("grade", "Not Available")

            st.write(f"📅 **Attendance Overview:** {attendance}%")
            st.progress(min(attendance / 100, 1.0))

            fb = generate_feedback(name, attendance)

            st.markdown("### 🎯 **Personalized Feedback Report**")
            st.info(f"""
            👤 **Name:** {name}  
            🎓 **Course:** {course}  
            🧮 **Grade:** {grade}  
            📈 **Attendance:** {attendance}%  
            """)

            if attendance >= 90:
                mood = "🌟 Excellent consistency! Keep up the great work ethic!"
            elif attendance >= 75:
                mood = "💪 Good effort! A little more focus on attendance can make a big difference."
            else:
                mood = "⚡ Improvement needed. Try maintaining regularity for better outcomes."

            st.success(f"🧠 **AI Feedback:** {fb}\n\n{mood}")


# ---------- TIMETABLE ----------
elif choice == "🗓 Timetable":
    st.subheader("🗓 Timetable Dashboard (Admin)")

    # Courses (prefer timetable's list)
    try:
        if hasattr(tt, "get_all_courses"):
            course_list = tt.get_all_courses()
        else:
            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                c.execute("SELECT DISTINCT course FROM students WHERE course IS NOT NULL AND course != ''")
                course_rows = [r[0] for r in c.fetchall()]
                course_list = course_rows if course_rows else ["BCA", "B.Tech", "MBA", "M.Tech", "BBA"]
    except Exception:
        course_list = ["BCA", "B.Tech", "MBA", "M.Tech", "BBA"]

    course = st.selectbox("Select Course", course_list)
    # Semesters from timetable module if present
    semesters_available = tt.get_all_semesters() if hasattr(tt, "get_all_semesters") else list(range(1,7))
    semester = st.selectbox("Select Semester", semesters_available)
    sections = tt.get_all_sections(course) if hasattr(tt, "get_all_sections") else ["A", "B", "C"]
    section = st.selectbox("Select Section", sections)

    # Auto-generation controls - available to admin 
    st.markdown("#### Auto-generate timetables")
    col_gen1, col_gen2 = st.columns([2,1])
    with col_gen1:
        semesters_to_generate = st.number_input("Semesters to generate (1-8)", min_value=1, max_value=8, value=6)
        sections_to_gen = st.text_input("Sections (comma separated)", value="A,B")
        courses_to_gen = st.text_input("Courses (comma separated) — leave blank to use defaults", value=",".join(course_list))
    with col_gen2:
        if st.button("Regenerate All Timetables"):
            sections_list = [s.strip() for s in sections_to_gen.split(",") if s.strip()]
            courses_list = [c.strip() for c in courses_to_gen.split(",") if c.strip()] or course_list
            try:
                if hasattr(tt, "auto_generate_timetable"):
                    tt.auto_generate_timetable(course_list=courses_list, semesters=int(semesters_to_generate), sections=sections_list)
                    st.success("✅ Auto-generated timetables for selected courses/semesters/sections.")
                else:
                    st.error("Auto-generate function not found in timetable module.")
            except Exception as e:
                st.error(f"Error during auto-generation: {e}")

    st.markdown("---")

    view_type = st.radio("View Mode", ["📆 Daily View", "🗓 Weekly View"], horizontal=True)

    if view_type == "📆 Daily View":
        days = getattr(tt, "DAYS", ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"])
        day = st.selectbox("Select Day", days)
        if st.button("Show Timetable (Daily)"):
            try:
                if hasattr(tt, "get_daily_view"):
                    data = tt.get_daily_view(course, semester, section, day)
                else:
                    data = tt.get_timetable(course, semester, section, day)
                if data:
                    df_tt = pd.DataFrame(data)
                    cols_try = ["id","day","start_time","end_time","subject","faculty","room_no"]
                    cols_present = [c for c in cols_try if c in df_tt.columns]
                    st.dataframe(df_tt[cols_present] if cols_present else df_tt, use_container_width=True)
                else:
                    st.info("No timetable found for this day.")
            except Exception as e:
                st.error(f"Could not fetch timetable: {e}")

    else:
        if st.button("Show Full Week"):
            try:
                if hasattr(tt, "get_weekly_view"):
                    weekly = tt.get_weekly_view(course, semester, section)
                    if weekly:
                        order = getattr(tt, "DAYS", ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"])
                        for d in order:
                            st.markdown(f"**{d}**")
                            day_entries = weekly.get(d, [])
                            if day_entries:
                                df_day = pd.DataFrame(day_entries)
                                cols_try = ["start_time","end_time","subject","faculty","room_no"]
                                cols_present = [c for c in cols_try if c in df_day.columns]
                                st.dataframe(df_day[cols_present] if cols_present else df_day, use_container_width=True)
                            else:
                                st.info(f"No entries for {d}.")
                    else:
                        st.info("No weekly timetable found for this selection.")
                else:
                    # fallback: call get_timetable without day and group by day
                    data = tt.get_timetable(course, semester, section)
                    if data:
                        df_tt = pd.DataFrame(data)
                        if "day" in df_tt.columns:
                            try:
                                order = getattr(tt, "DAYS", ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"])
                                df_tt["day"] = pd.Categorical(df_tt["day"], categories=order, ordered=True)
                                df_tt = df_tt.sort_values(["day","start_time"] if "start_time" in df_tt.columns else ["day"])
                                st.dataframe(df_tt, use_container_width=True)
                            except Exception:
                                st.dataframe(df_tt, use_container_width=True)
                        else:
                            st.dataframe(df_tt, use_container_width=True)
                    else:
                        st.info("No weekly timetable found for this selection.")
            except Exception as e:
                st.error(f"Could not fetch weekly timetable: {e}")

    st.markdown("---")
    st.subheader("🛠️ Manage Timetable (Add / Delete)")

    with st.form("add_tt_form"):
        st.markdown("### ➕ Add New Entry (manual)")
        day_add = st.selectbox("Day", getattr(tt, "DAYS", ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]), key="add_day")
        subject = st.text_input("Subject", key="add_subject")
        faculty = st.text_input("Faculty Name", key="add_faculty")
        start = st.time_input("Start Time", value=time(9,0), key="add_start")
        end = st.time_input("End Time", value=time(10,0), key="add_end")
        room = st.text_input("Room No", key="add_room")
        submitted = st.form_submit_button("Add Entry")
        if submitted:
            try:
                if hasattr(tt, "add_timetable_entry"):
                    ok = tt.add_timetable_entry(course, semester, section, day_add, subject.strip(), faculty.strip(), str(start), str(end), room.strip())
                    if ok:
                        st.success("✅ Timetable entry added successfully!")
                    else:
                        st.error("❌ Failed to add timetable entry.")
                else:
                    st.error("Timetable module does not support manual add.")
            except Exception as e:
                st.error(f"Error adding entry: {e}")

    st.markdown("### ❌ Delete Entry (manual)")
    try:
        entries = tt.get_timetable(course, semester, section)
        if entries:
            ids = {f"{e.get('day','')}: {e.get('subject','')} ({e.get('start_time','')}-{e.get('end_time','')})": e.get('id') for e in entries}
            del_choice = st.selectbox("Select Entry to Delete", list(ids.keys()), key="del_choice")
            if st.button("Delete Entry"):
                entry_id = ids[del_choice]
                if hasattr(tt, "delete_timetable_entry"):
                    ok = tt.delete_timetable_entry(entry_id)
                    if ok:
                        st.success("✅ Entry deleted successfully!")
                    else:
                        st.error("❌ Could not delete entry.")
                else:
                    st.error("Timetable module does not support deletion.")
        else:
            st.info("No timetable entries found to delete.")
    except Exception as e:
        st.error(f"Error fetching entries: {e}")

# ---------- END OF PAGES ----------
st.markdown("---")
