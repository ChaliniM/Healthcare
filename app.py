import sqlite3
import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from datetime import timedelta,datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from flask import send_file
import io


APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, 'database.db')

app = Flask(__name__)
app.secret_key = 'change_this_to_a_random_secret'
app.permanent_session_lifetime = timedelta(minutes=60)


# ----------------- Database helpers -----------------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    if os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'staff'
    );

    CREATE TABLE patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        age INTEGER,
        gender TEXT,
        phone TEXT,
        email TEXT,
        notes TEXT
    );

    CREATE TABLE appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        datetime TEXT NOT NULL,
        doctor TEXT,
        reason TEXT,
        status TEXT DEFAULT 'scheduled',
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    );

    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        message TEXT NOT NULL,
        severity TEXT DEFAULT 'info', -- info, warning, critical
        created_at TEXT DEFAULT (datetime('now','localtime')),
        sent INTEGER DEFAULT 0,
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    );

    """)
    # create demo users
    cur.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ('admin', 'admin123', 'admin'))
    cur.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ('doctor', 'doc123', 'doctor'))
    conn.commit()
    conn.close()

init_db()

# ----------------- Auth helpers -----------------
def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

def role_required(role):
    """Decorator to restrict access to a specific role (e.g., 'admin')"""
    from functools import wraps
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if 'role' not in session or session['role'] != role:
                flash("You don't have permission to access this page.", 'danger')
                # Redirect based on role
                if session.get('role') == 'admin':
                    return redirect(url_for('admin_dashboard'))
                else:
                    return redirect(url_for('user_dashboard'))
            return fn(*args, **kwargs)
        return wrapper
    return decorator




# ----------------- Routes -----------------
@app.route('/')
def root():
    if 'user' in session:
        if session['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))

@app.route('/portal')
def portal():
    """Serve the sequential hospital portal HTML."""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uname = request.form.get('username')
        pwd = request.form.get('password')
        db = get_db()
        cur = db.execute("SELECT * FROM users WHERE username = ? AND password = ?", (uname, pwd))
        user = cur.fetchone()
        if user:
            session.permanent = True
            session['user'] = user['username']
            session['role'] = user['role']
            flash('Logged in successfully', 'success')
            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

# ----------------- Dashboard -----------------
# ----------------- Dashboards -----------------
@app.route('/admin')
@login_required
@role_required('admin')
def admin_dashboard():
    """Admin-specific dashboard"""
    db = get_db()
    total_patients = db.execute("SELECT COUNT(*) as cnt FROM patients").fetchone()['cnt']
    total_users = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()['cnt']
    upcoming_appointments = db.execute("SELECT COUNT(*) as cnt FROM appointments WHERE status='scheduled'").fetchone()['cnt']
    open_alerts = db.execute("SELECT COUNT(*) as cnt FROM alerts WHERE sent=0").fetchone()['cnt']
    recent_alerts = db.execute("SELECT a.*, p.name as patient_name FROM alerts a LEFT JOIN patients p ON a.patient_id=p.id ORDER BY a.created_at DESC LIMIT 5").fetchall()
    return render_template('admin_dashboard.html',
                           total_patients=total_patients,
                           total_users=total_users,
                           upcoming_appointments=upcoming_appointments,
                           open_alerts=open_alerts,
                           recent_alerts=recent_alerts)

@app.route('/user')
@login_required
def user_dashboard():
    """Normal user dashboard"""
    db = get_db()
    total_patients = db.execute("SELECT COUNT(*) as cnt FROM patients").fetchone()['cnt']
    upcoming_appointments = db.execute("SELECT COUNT(*) as cnt FROM appointments WHERE status='scheduled'").fetchone()['cnt']
    open_alerts = db.execute("SELECT COUNT(*) as cnt FROM alerts WHERE sent=0").fetchone()['cnt']
    recent_alerts = db.execute("SELECT a.*, p.name as patient_name FROM alerts a LEFT JOIN patients p ON a.patient_id=p.id ORDER BY a.created_at DESC LIMIT 5").fetchall()
    return render_template('user_dashboard.html',
                           total_patients=total_patients,
                           upcoming_appointments=upcoming_appointments,
                           open_alerts=open_alerts,
                           recent_alerts=recent_alerts)


# ----------------- Patients CRUD -----------------
@app.route('/patients')
@login_required
def patients():
    db = get_db()
    q = request.args.get('q', '').strip()
    if q:
        rows = db.execute("SELECT * FROM patients WHERE name LIKE ? OR phone LIKE ? OR email LIKE ? ORDER BY id DESC",
                          (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        rows = db.execute("SELECT * FROM patients ORDER BY id DESC").fetchall()
    return render_template('patients.html', patients=rows, q=q)

@app.route('/patients/add', methods=['GET', 'POST'])
@login_required
def add_patient():
    if request.method == 'POST':
        name = request.form.get('name').strip()
        age = request.form.get('age') or None
        gender = request.form.get('gender')
        phone = request.form.get('phone')
        email = request.form.get('email')
        notes = request.form.get('notes')
        if not name:
            flash('Patient name is required', 'danger')
            return redirect(url_for('add_patient'))
        db = get_db()
        db.execute("INSERT INTO patients (name, age, gender, phone, email, notes) VALUES (?, ?, ?, ?, ?, ?)",
                   (name, age, gender, phone, email, notes))
        db.commit()
        flash('Patient added', 'success')
        return redirect(url_for('patients'))
    return render_template('add_patient.html')

@app.route('/patients/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
def edit_patient(pid):
    db = get_db()
    patient = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    if not patient:
        flash('Patient not found', 'danger')
        return redirect(url_for('patients'))
    if request.method == 'POST':
        name = request.form.get('name').strip()
        age = request.form.get('age') or None
        gender = request.form.get('gender')
        phone = request.form.get('phone')
        email = request.form.get('email')
        notes = request.form.get('notes')
        if not name:
            flash('Name required', 'danger')
            return redirect(url_for('edit_patient', pid=pid))
        db.execute("UPDATE patients SET name=?, age=?, gender=?, phone=?, email=?, notes=? WHERE id=?",
                   (name, age, gender, phone, email, notes, pid))
        db.commit()
        flash('Patient updated', 'success')
        return redirect(url_for('patients'))
    return render_template('edit_patient.html', patient=patient)

@app.route('/patients/delete/<int:pid>', methods=['POST'])
@login_required
def delete_patient(pid):
    db = get_db()
    db.execute("DELETE FROM patients WHERE id = ?", (pid,))
    db.commit()
    flash('Patient deleted', 'info')
    return redirect(url_for('patients'))

# ----------------- Appointments -----------------
@app.route('/appointments')
@login_required
def appointments():
    db = get_db()
    rows = db.execute("""
        SELECT a.*, p.name as patient_name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        ORDER BY datetime(a.datetime) DESC
    """).fetchall()
    return render_template('appointments.html', appointments=rows)

@app.route('/appointments/add', methods=['GET', 'POST'])
@login_required
def add_appointment():
    db = get_db()
    patients = db.execute("SELECT id, name FROM patients ORDER BY name").fetchall()
    if request.method == 'POST':
        patient_id = request.form.get('patient_id')
        dt = request.form.get('datetime')  # expected "YYYY-MM-DD HH:MM"
        doctor = request.form.get('doctor')
        reason = request.form.get('reason')
        if not (patient_id and dt):
            flash('Patient and datetime are required', 'danger')
            return redirect(url_for('add_appointment'))
        
        db.execute("INSERT INTO appointments (patient_id, datetime, doctor, reason) VALUES (?, ?, ?, ?)",
                   (patient_id, dt, doctor, reason))
        db.commit()
        flash('Appointment scheduled', 'success')
        
        # Redirect straight to PDF download after booking
        return redirect(url_for('download_patient_pdf', pid=patient_id))
    
    return render_template('add_appointment.html', patients=patients)


@app.route('/appointments/update_status/<int:aid>', methods=['POST'])
@login_required
def update_appointment_status(aid):
    new_status = request.form.get('status')
    db = get_db()
    db.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, aid))
    db.commit()
    flash('Appointment status updated', 'success')
    return redirect(url_for('appointments'))

@app.route('/appointments/delete/<int:aid>', methods=['POST'])
@login_required
def delete_appointment(aid):
    db = get_db()
    db.execute("DELETE FROM appointments WHERE id = ?", (aid,))
    db.commit()
    flash('Appointment deleted', 'info')
    return redirect(url_for('appointments'))


@app.route('/patients/<int:pid>/download')
@login_required
def download_patient_pdf(pid):
    db = get_db()
    patient = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    if not patient:
        flash('Patient not found', 'danger')
        return redirect(url_for('patients'))

    # Get all appointments for this patient
    appointments = db.execute("""
        SELECT datetime, doctor, reason, status
        FROM appointments
        WHERE patient_id = ?
        ORDER BY datetime(datetime) DESC
    """, (pid,)).fetchall()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # --- Logo (optional) ---
    logo_path = os.path.join(APP_DIR, "static", "logo.png")  # Place your logo in /static/logo.png
    if os.path.exists(logo_path):
        elements.append(Image(logo_path, width=80, height=80))
        elements.append(Spacer(1, 12))

    # --- Title ---
    elements.append(Paragraph(f"<b>Patient Medical Report</b>", styles['Title']))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    elements.append(Spacer(1, 24))

    # --- Patient Info Table ---
    patient_data = [
        ['Patient ID', patient['id']],
        ['Name', patient['name']],
        ['Age', patient['age'] or 'N/A'],
        ['Gender', patient['gender'] or 'N/A'],
        ['Phone', patient['phone'] or 'N/A'],
        ['Email', patient['email'] or 'N/A'],
        ['Notes', patient['notes'] or '']
    ]
    patient_table = Table(patient_data, colWidths=[100, 350])
    patient_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (1, 0), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(Paragraph("<b>Patient Information</b>", styles['Heading2']))
    elements.append(patient_table)
    elements.append(Spacer(1, 24))

    # --- Appointments Table ---
    if appointments:
        elements.append(Paragraph("<b>Appointment History</b>", styles['Heading2']))
        table_data = [['Date & Time', 'Doctor', 'Reason', 'Status']]
        for appt in appointments:
            table_data.append([
                appt['datetime'],
                appt['doctor'] or 'N/A',
                appt['reason'] or 'N/A',
                appt['status']
            ])
        appt_table = Table(table_data, colWidths=[120, 120, 150, 60])
        appt_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(appt_table)
    else:
        elements.append(Paragraph("No appointment history found.", styles['Normal']))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(buffer, as_attachment=True,
                     download_name=f"patient_{patient['id']}_report.pdf",
                     mimetype='application/pdf')
#-------------- Alerts -----------------

@app.route('/alerts')
@login_required
def alerts():
    db = get_db()
    rows = db.execute("""
        SELECT a.*, p.name as patient_name
        FROM alerts a
        LEFT JOIN patients p ON a.patient_id = p.id
        ORDER BY a.created_at DESC
    """).fetchall()
    return render_template('alerts.html', alerts=rows)

@app.route('/alerts/add', methods=['POST'])
@login_required
def add_alert():
    patient_id = request.form.get('patient_id') or None
    message = request.form.get('message')
    severity = request.form.get('severity') or 'info'
    if not message:
        flash('Alert message cannot be empty', 'danger')
        return redirect(url_for('alerts'))
    db = get_db()
    db.execute("INSERT INTO alerts (patient_id, message, severity, sent) VALUES (?, ?, ?, 0)",
               (patient_id, message, severity))
    db.commit()
    flash('Alert created (not actually sent in demo)', 'success')
    return redirect(url_for('alerts'))

@app.route('/alerts/mark_sent/<int:aid>', methods=['POST'])
@login_required
def mark_alert_sent(aid):
    db = get_db()
    db.execute("UPDATE alerts SET sent = 1 WHERE id = ?", (aid,))
    db.commit()
    flash('Marked as sent', 'success')
    return redirect(url_for('alerts'))

@app.route('/alerts/delete/<int:aid>', methods=['POST'])
@login_required
def delete_alert(aid):
    db = get_db()
    db.execute("DELETE FROM alerts WHERE id = ?", (aid,))
    db.commit()
    flash('Alert deleted', 'info')
    return redirect(url_for('alerts'))
# ----------------- Simple user creation (demo only) -----------------
@app.route('/create_user_demo')
def create_user_demo():
    # demo helper to create a sample user - not protected (remove in prod)
    db = get_db()
    try:
        db.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ('nurse', 'nurse123', 'nurse'))
        db.commit()
        return "Demo user 'nurse' created"
    except Exception:
        return "User exists or error"

if __name__ == '__main__':
    app.run(debug=True)


