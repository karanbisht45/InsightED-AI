# timetable.py
"""
Timetable generator — compatible with your backend.py and app.py.
Generates unique timetables per (course, semester, section) with:
  - 3 classes per subject per week
  - random faculty & room assignments per section
  - conflict avoidance (faculty, room, section)
  - indexes and faculty_load_summary view for analytics
"""

import sqlite3
from typing import List, Dict, Optional, Tuple
import random
from collections import defaultdict
from itertools import cycle

DB_FILE = "students.db"

# Default constants (editable)
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
TIME_SLOTS = [
    ("09:00", "10:00"),
    ("10:00", "11:00"),
    ("11:15", "12:15"),
    ("12:15", "13:15"),
    ("14:00", "15:00"),
    ("15:00", "16:00")
]

# Example subject pools per course (used if you don't have an external subjects table)
SAMPLE_SUBJECTS = {
    "BCA": ["Python Programming", "Database Systems", "Networking", "Data Structures", "AI Fundamentals"],
    "B.Tech": ["DSA", "Operating Systems", "DBMS", "Computer Networks", "Machine Learning"],
    "BBA": ["Marketing Management", "Financial Accounting", "Business Law", "HR Management", "Economics"],
    "MBA": ["Corporate Finance", "Strategic Management", "Leadership Skills", "Business Analytics", "Organizational Behaviour"]
}

# Example faculty pool (can be extended)
SAMPLE_FACULTY = [
    "Dr. Sharma", "Prof. Singh", "Dr. Mehta", "Ms. Verma", "Mr. Gupta", "Dr. Kapoor", "Dr. Rao", "Dr. Iyer"
]

# Rooms pool
ROOMS = [f"A-{i}" for i in range(101, 106)] + [f"B-{i}" for i in range(201, 206)]

# ------------------ DB / Schema ------------------
def create_timetable_table():
    """Create timetable table, indexes, and analytics view. Safe to call repeatedly."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        # Enable foreign keys (no foreign keys declared now; helpful if added later)
        c.execute("PRAGMA foreign_keys = ON;")

        c.execute("""
            CREATE TABLE IF NOT EXISTS timetable (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course TEXT NOT NULL,
                semester INTEGER NOT NULL,
                section TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                subject TEXT NOT NULL,
                faculty TEXT,
                room_no TEXT,
                UNIQUE(course, semester, section, day, start_time, end_time, room_no)
            )
        """)

        # Useful indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_tt_course_sem_section_day ON timetable(course, semester, section, day);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tt_faculty_day_time ON timetable(faculty, day, start_time);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tt_room_day_time ON timetable(room_no, day, start_time);")

        # Faculty load view for admin dashboards
        try:
            c.execute("DROP VIEW IF EXISTS faculty_load_summary")
        except Exception:
            pass
        c.execute("""
            CREATE VIEW IF NOT EXISTS faculty_load_summary AS
            SELECT
                faculty,
                COUNT(*) AS classes_assigned,
                GROUP_CONCAT(DISTINCT course) AS courses_handled,
                ROUND(AVG(semester),2) AS avg_semester
            FROM timetable
            WHERE faculty IS NOT NULL
            GROUP BY faculty
            ORDER BY classes_assigned DESC
        """)
        conn.commit()
    print("✨ timetable: table, indexes, and faculty_load_summary view ensured.")

# ------------------ Helpers ------------------
def _time_overlap(a_start, a_end, b_start, b_end):
    """Return True if time ranges [a_start,a_end) and [b_start,b_end) overlap. Times are HH:MM strings."""
    return not (a_end <= b_start or a_start >= b_end)

def _pick_faculty_balanced(faculty_pool: List[str], faculty_load: Dict[str, int]) -> str:
    """Pick faculty with least assigned load; break ties randomly."""
    min_load = min(faculty_load.get(f, 0) for f in faculty_pool)
    candidates = [f for f in faculty_pool if faculty_load.get(f, 0) == min_load]
    return random.choice(candidates)

# ------------------ CRUD ------------------
def add_timetable_entry(course: str, semester: int, section: str, day: str,
                        start_time: str, end_time: str, subject: str,
                        faculty: Optional[str], room_no: Optional[str]) -> bool:
    """
    Add a timetable entry manually, with overlap checks for the same course/section and room.
    Returns True if inserted, False if overlap or error.
    """
    create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        # Check for section overlap
        c.execute("""
            SELECT id FROM timetable
            WHERE course=? AND semester=? AND section=? AND day=?
              AND NOT (end_time <= ? OR start_time >= ?)
            LIMIT 1
        """, (course, semester, section, day, start_time, end_time))
        if c.fetchone():
            print(f"⚠️ Overlap for {course} S{semester} Sec{section} on {day} {start_time}-{end_time}")
            return False
        # Check room overlap
        if room_no:
            c.execute("""
                SELECT id FROM timetable
                WHERE room_no=? AND day=? AND NOT (end_time <= ? OR start_time >= ?)
                LIMIT 1
            """, (room_no, day, start_time, end_time))
            if c.fetchone():
                print(f"⚠️ Room {room_no} booked on {day} during {start_time}-{end_time}")
                return False
        try:
            c.execute("""
                INSERT INTO timetable (course, semester, section, day, start_time, end_time, subject, faculty, room_no)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (course, semester, section, day, start_time, end_time, subject, faculty, room_no))
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Error inserting timetable entry: {e}")
            return False

def delete_timetable_entry(entry_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            c.execute("DELETE FROM timetable WHERE id=?", (entry_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Error deleting entry {entry_id}: {e}")
            return False

def get_timetable(course: str, semester: Optional[int] = None, section: Optional[str] = None,
                  day: Optional[str] = None) -> List[Dict]:
    """Return list of timetable entries (dicts) for given filters, ordered by day & start_time."""
    create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        query = "SELECT * FROM timetable WHERE course=?"
        params = [course]
        if semester is not None:
            query += " AND semester=?"
            params.append(int(semester))
        if section:
            query += " AND section=?"
            params.append(section)
        if day:
            query += " AND day=?"
            params.append(day)
        # Order using day order, then start_time
        order_clause = (" ORDER BY CASE day WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 "
                        "WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END, start_time")
        query += order_clause
        c.execute(query, tuple(params))
        return [dict(r) for r in c.fetchall()]

def get_daily_view(course: str, semester: int, section: str, day: str) -> List[Dict]:
    return get_timetable(course, semester, section, day)

def get_weekly_view(course: str, semester: int, section: str) -> Dict[str, List[Dict]]:
    entries = get_timetable(course, semester, section)
    week = {d: [] for d in DAYS}
    for e in entries:
        wkday = e.get("day", "Unknown")
        week.setdefault(wkday, []).append(e)
    for d in week:
        week[d] = sorted(week[d], key=lambda x: x.get("start_time"))
    return week

def get_all_days() -> List[str]:
    create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT day FROM timetable ORDER BY CASE day WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END")
        rows = [r[0] for r in c.fetchall()]
        return rows or DAYS

def get_all_sections(course: str) -> List[str]:
    create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT section FROM timetable WHERE course=? ORDER BY section", (course,))
        rows = [r[0] for r in c.fetchall()]
        return rows or ["A", "B"]

def get_all_courses() -> List[str]:
    """Return SAMPLE_SUBJECTS keys + DB courses (deduplicated)."""
    create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT course FROM timetable ORDER BY course")
        db_courses = [r[0] for r in c.fetchall()]
        # preserve SAMPLE_SUBJECTS order first, then DB extras
        courses = list(dict.fromkeys(list(SAMPLE_SUBJECTS.keys()) + db_courses))
        return courses

def get_all_semesters() -> List[int]:
    create_timetable_table()
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT semester FROM timetable ORDER BY semester")
        rows = [int(r[0]) for r in c.fetchall()]
        return rows or [1,2,3,4,5,6]

# ------------------ AUTO-GENERATION: unique per section ------------------
def auto_generate_timetable(course_list: List[str], semesters: int = 6, sections: List[str] = ["A", "B"]) -> None:
    """
    Generate unique timetables for each (course, semester, section).
    - Each subject gets exactly 3 classes/week.
    - Distributes classes across DAYS and TIME_SLOTS.
    - Avoids faculty/room/section overlaps.
    """
    create_timetable_table()
    random.seed()  # system time seed

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()

        # Load existing occupancy (room/day -> list of (start,end))
        occupied_room = defaultdict(list)   # key: (room, day) -> list of (start, end)
        occupied_faculty = defaultdict(list)  # key: (faculty, day) -> list of (start, end)
        occupied_section = defaultdict(list)  # key: (course, semester, section, day) -> list of (start, end)

        c.execute("SELECT room_no, faculty, course, semester, section, day, start_time, end_time FROM timetable")
        for row in c.fetchall():
            room, faculty, course_ex, sem_ex, section_ex, day, st, et = row
            if room:
                occupied_room[(room, day)].append((st, et))
            if faculty:
                occupied_faculty[(faculty, day)].append((st, et))
            occupied_section[(course_ex, sem_ex, section_ex, day)].append((st, et))

        # current faculty load counts
        faculty_load = defaultdict(int)
        c.execute("SELECT faculty, COUNT(*) FROM timetable WHERE faculty IS NOT NULL GROUP BY faculty")
        for f_row in c.fetchall():
            fac, cnt = f_row
            faculty_load[fac] = cnt or 0

        # We'll accumulate per-section inserts and then bulk insert to keep it transactional
        total_inserts = 0
        for course in course_list:
            subj_pool = SAMPLE_SUBJECTS.get(course, None)
            if not subj_pool:
                # fallback single generic subject if none known for a course
                subj_pool = ["Core Subject", "Elective", "Project Work"]
            for semester in range(1, semesters + 1):
                for section in sections:
                    print(f"🧩 Generating timetable for: {course} | Sem {semester} | Section {section}")
                    # delete only this section's previous entries
                    c.execute("DELETE FROM timetable WHERE course=? AND semester=? AND section=?", (course, semester, section))
                    # build desired weekly slots: each subject needs 3 slots/week
                    # Compute how many subjects to schedule: ensure total slots <= len(DAYS) * len(TIME_SLOTS)
                    subjects = subj_pool.copy()
                    random.shuffle(subjects)
                    # We'll take all subjects but ensure we can place 3 classes each.
                    # If too many subjects, limit by available weekly slots.
                    max_weekly_slots = len(DAYS) * len(TIME_SLOTS)
                    desired_slots = len(subjects) * 3
                    if desired_slots > max_weekly_slots:
                        # trim subjects
                        max_subjects = max(1, max_weekly_slots // 3)
                        subjects = subjects[:max_subjects]
                        print(f"⚠️ Too many subjects; trimmed to {len(subjects)} subjects to fit weekly slots.")

                    # Prepare a schedule map: day -> list of time_slots used (to avoid two classes for same section in same slot)
                    section_occupied_local = defaultdict(list)

                    # We'll create a cyclic list of (day, slot) pairs to iterate and assign classes fairly
                    day_slot_pairs = [(d, slot) for d in DAYS for slot in TIME_SLOTS]
                    # Shuffle to create variety between sections
                    random.shuffle(day_slot_pairs)

                    inserts = []
                    # For each subject, schedule exactly 3 sessions
                    for subj in subjects:
                        sessions_needed = 3
                        attempts = 0
                        while sessions_needed > 0 and attempts < 500:
                            attempts += 1
                            if not day_slot_pairs:
                                break
                            # pick a candidate day-slot (pop to avoid reusing same candidate immediately)
                            d, slot = day_slot_pairs.pop(0)
                            st, et = slot
                            # check section doesn't already have a class at this day-slot
                            if any(_time_overlap(st, et, a, b) for a, b in section_occupied_local.get(d, [])):
                                # skip - section already has a class overlapping on this day in local map
                                day_slot_pairs.append((d, slot))  # requeue for later
                                continue
                            # pick a faculty (balanced)
                            faculty = _pick_faculty_balanced(SAMPLE_FACULTY, faculty_load)
                            # ensure faculty not occupied at this day-slot
                            if any(_time_overlap(st, et, a, b) for a, b in occupied_faculty.get((faculty, d), [])):
                                # faculty busy; try different faculty
                                available_fac = [f for f in SAMPLE_FACULTY if not any(_time_overlap(st, et, a, b) for a, b in occupied_faculty.get((f, d), []))]
                                if not available_fac:
                                    # no faculty free at this slot; requeue and try later
                                    day_slot_pairs.append((d, slot))
                                    continue
                                faculty = random.choice(available_fac)
                            # pick a free room
                            free_rooms = [r for r in ROOMS if not any(_time_overlap(st, et, a, b) for a, b in occupied_room.get((r, d), []))]
                            room_choice = random.choice(free_rooms) if free_rooms else random.choice(ROOMS)
                            # final safety check: ensure section not double-booked (global)
                            if any(_time_overlap(st, et, a, b) for a, b in occupied_section.get((course, semester, section, d), [])):
                                day_slot_pairs.append((d, slot))
                                continue
                            # assign: record occupancy
                            occupied_faculty[(faculty, d)].append((st, et))
                            occupied_room[(room_choice, d)].append((st, et))
                            occupied_section[(course, semester, section, d)].append((st, et))
                            section_occupied_local[d].append((st, et))
                            faculty_load[faculty] += 1
                            inserts.append((course, semester, section, d, st, et, subj, faculty, room_choice))
                            sessions_needed -= 1
                            total_inserts += 1
                            # After successful assignment, we do not re-add this day-slot (consumed)
                        if sessions_needed > 0:
                            print(f"⚠️ Could not schedule all sessions for subject '{subj}' in {course} S{semester} Sec{section} (needed {sessions_needed} more).")
                        # Rebuild day_slot_pairs if low to keep assigning remaining subjects
                        if len(day_slot_pairs) < 5:
                            day_slot_pairs = [(d, slot) for d in DAYS for slot in TIME_SLOTS]
                            random.shuffle(day_slot_pairs)

                    # Bulk insert this section's entries
                    try:
                        c.executemany("""
                            INSERT INTO timetable (course, semester, section, day, start_time, end_time, subject, faculty, room_no)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, inserts)
                        conn.commit()
                        print(f"✅ Inserted {len(inserts)} slots for {course} Sem{semester} Section {section}")
                    except Exception as e:
                        conn.rollback()
                        print(f"❌ Failed to insert timetable for {course} Sem{semester} Section {section}: {e}")

        print(f"🎯 Finished auto-generation. Total inserted slots: {total_inserts}")

def generate_single_timetable(course: str, semester: int, section: str) -> None:
    """Convenience: generate timetable for a single course-sem-section combo."""
    auto_generate_timetable([course], semesters=semester, sections=[section])

# ------------------ CLI DEBUG ------------------
if __name__ == "__main__":
    create_timetable_table()
    auto_generate_timetable(list(SAMPLE_SUBJECTS.keys()), semesters=6, sections=["A", "B", "C"])
    print("✅ timetable.py: Sample timetables generated.")
