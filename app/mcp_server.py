import sqlite3
import os
import datetime
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("elder-care-mcp")

# Database path (placed in the parent directory of the app folder)
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "elder_care.db"))

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create medications table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS medications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        schedule TEXT NOT NULL,
        dosage TEXT NOT NULL
    )
    """)
    
    # Create medication_logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS medication_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medication_name TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
    """)
    
    # Create appointments table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor TEXT NOT NULL,
        time TEXT NOT NULL,
        reason TEXT
    )
    """)
    
    # Insert initial mock data if tables are empty
    cursor.execute("SELECT COUNT(*) FROM medications")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
        INSERT INTO medications (name, schedule, dosage) VALUES (?, ?, ?)
        """, [
            ("Lisinopril", "Every morning at 8:00 AM", "10mg"),
            ("Metformin", "With dinner at 6:30 PM", "500mg"),
            ("Atorvastatin", "Before bed at 9:00 PM", "20mg")
        ])
        
    cursor.execute("SELECT COUNT(*) FROM appointments")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
        INSERT INTO appointments (doctor, time, reason) VALUES (?, ?, ?)
        """, [
            ("Dr. Smith (Cardiologist)", "2026-07-10 10:00 AM", "Routine blood pressure checkup"),
            ("Dr. Patel (Endocrinologist)", "2026-08-15 02:30 PM", "Diabetes management review")
        ])
        
    conn.commit()
    conn.close()

# Initialize the SQLite tables on import/startup
init_db()

@mcp.tool()
def get_medications() -> str:
    """Retrieves the list of active medications, their schedules, and dosages.
    
    Returns:
        A list or description of all active medications.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, schedule, dosage FROM medications")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return "No medications found in the active schedule."
        
    res = ["Active Medications:"]
    for row in rows:
        res.append(f"- {row[0]}: {row[2]} ({row[1]})")
    return "\n".join(res)

@mcp.tool()
def log_medication_taken(medication_name: str, time_taken: str = None) -> str:
    """Logs that a medication dose was successfully taken.
    
    Args:
        medication_name: The name of the medication taken (e.g. Lisinopril).
        time_taken: Optional ISO timestamp or text (e.g. '2026-06-24 08:05 AM'). Defaults to current time.
        
    Returns:
        Confirmation message.
    """
    if not time_taken:
        time_taken = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Try to match input to existing medication names
    cursor.execute("SELECT name FROM medications WHERE name LIKE ?", (f"%{medication_name}%",))
    match = cursor.fetchone()
    if match:
        medication_name = match[0]
        
    cursor.execute("""
    INSERT INTO medication_logs (medication_name, timestamp) VALUES (?, ?)
    """, (medication_name, time_taken))
    conn.commit()
    conn.close()
    
    return f"Successfully logged that {medication_name} was taken at {time_taken}."

@mcp.tool()
def add_medication_schedule(medication_name: str, schedule: str, dosage: str) -> str:
    """Adds a new medication to the tracking schedule.
    
    Args:
        medication_name: Name of the medication.
        schedule: How/when the medication should be taken (e.g. 'Once daily at breakfast').
        dosage: The amount of medication to take (e.g. '5mg' or '1 pill').
        
    Returns:
        Confirmation message.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO medications (name, schedule, dosage) VALUES (?, ?, ?)
        """, (medication_name, schedule, dosage))
        conn.commit()
        msg = f"Successfully added {medication_name} ({dosage}) to the schedule: {schedule}."
    except sqlite3.IntegrityError:
        # If it already exists, update the schedule and dosage
        cursor.execute("""
        UPDATE medications SET schedule = ?, dosage = ? WHERE name = ?
        """, (schedule, dosage, medication_name))
        conn.commit()
        msg = f"Successfully updated medication schedule/dosage for existing medication: {medication_name}."
    finally:
        conn.close()
    return msg

@mcp.tool()
def get_appointments() -> str:
    """Retrieves all scheduled doctor visits and appointments.
    
    Returns:
        A list of scheduled doctor appointments.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT doctor, time, reason FROM appointments ORDER BY time ASC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return "No upcoming doctor appointments scheduled."
        
    res = ["Upcoming Doctor Appointments:"]
    for row in rows:
        reason_str = f" for {row[2]}" if row[2] else ""
        res.append(f"- Appointment with {row[0]} on {row[1]}{reason_str}")
    return "\n".join(res)

@mcp.tool()
def schedule_appointment(doctor: str, time: str, reason: str = "") -> str:
    """Saves a new doctor appointment/visit schedule.
    
    Args:
        doctor: Name of the doctor or clinic (e.g. 'Dr. Adams').
        time: Date and time of the appointment (e.g. '2026-07-15 10:00 AM').
        reason: Purpose of the appointment (e.g. 'Checkup').
        
    Returns:
        Confirmation message.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO appointments (doctor, time, reason) VALUES (?, ?, ?)
    """, (doctor, time, reason))
    conn.commit()
    conn.close()
    
    return f"Successfully scheduled appointment with {doctor} on {time}."

if __name__ == "__main__":
    mcp.run()
