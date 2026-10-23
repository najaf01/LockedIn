from flask import Flask, request, redirect, url_for, session, render_template_string, send_from_directory, abort
import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import re
import json

app = Flask(__name__)
app.secret_key = os.environ.get("LOCKEDIN_SECRET_KEY") or os.urandom(32)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lockedin.db"
UPLOAD_DIR = BASE_DIR / "lockedin_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_UPLOADS = {"pdf", "txt", "md"}
SUBJECTS = ["Mathematics", "Physics", "Computer Science", "Urdu", "English", "Pakistan Studies", "Al-Quran"]


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'whole_syllabus' CHECK(mode IN ('whole_syllabus','exam_scheme')),
            chapter TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exam_schemes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL DEFAULT '',
            source_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            due_date TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'normal',
            completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exam_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'whole_syllabus',
            score REAL NOT NULL DEFAULT 0,
            total_marks REAL NOT NULL DEFAULT 0,
            weak_topics TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exam_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            mode TEXT NOT NULL,
            title TEXT NOT NULL,
            paper_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # Safe migrations for databases created by earlier versions.
        for table, column, definition in [
            ("exam_schemes", "source_text", "TEXT NOT NULL DEFAULT ''"),
        ]:
            cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        # Import records from the earlier experimental tables without duplicating them.
        existing_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "lockedin_syllabus_topics" in existing_tables:
            db.execute("""INSERT INTO study_topics(user_id,subject,mode,chapter,topic,completed,created_at)
                SELECT old.user_id, old.subject,
                       CASE WHEN old.mode IN ('whole_syllabus','exam_scheme') THEN old.mode ELSE 'whole_syllabus' END,
                       COALESCE(old.chapter,''), old.topic, COALESCE(old.completed,0),
                       COALESCE(old.created_at,CURRENT_TIMESTAMP)
                FROM lockedin_syllabus_topics old
                WHERE NOT EXISTS (SELECT 1 FROM study_topics n WHERE n.user_id=old.user_id
                  AND n.subject=old.subject AND n.mode=old.mode AND n.chapter=old.chapter AND n.topic=old.topic)""")
        if "lockedin_daily_tasks" in existing_tables:
            db.execute("""INSERT INTO study_tasks(user_id,title,subject,due_date,priority,completed)
                SELECT old.user_id, old.task, COALESCE(old.subject,'General'), old.task_date, 'normal', COALESCE(old.completed,0)
                FROM lockedin_daily_tasks old
                WHERE NOT EXISTS (SELECT 1 FROM study_tasks n WHERE n.user_id=old.user_id
                  AND n.title=old.task AND n.due_date=old.task_date AND n.subject=COALESCE(old.subject,'General'))""")


def log_activity(user_id, activity_type):
    with get_db() as db:
        db.execute("INSERT INTO activity_log(user_id, activity_date, activity_type) VALUES(?,?,?)",
                   (user_id, date.today().isoformat(), activity_type))


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        return db.execute("SELECT id, name, email FROM users WHERE id=?", (uid,)).fetchone()


def login_required():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return None


def page_shell(title, body, active=""):
    # Shared styling for tools; AUTH_PAGE is preserved separately and unchanged.
    return render_template_string("""<!doctype html><html lang="en"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }} | lockedin</title><style>
    *{box-sizing:border-box}body{margin:0;background:#fff;color:#302832;font-family:Arial,sans-serif}
    header{display:flex;justify-content:space-between;align-items:center;padding:18px 6%;border-bottom:1px solid #f1e4eb}
    .brand{font-weight:800;font-size:25px}.brand span{color:#e6b4cf}.nav{display:flex;flex-wrap:wrap;gap:8px;padding:12px 6%;border-bottom:1px solid #f5edf1}
    .nav a,.btn{display:inline-block;padding:9px 12px;border-radius:9px;background:#fff5fa;color:#79455f;text-decoration:none;border:1px solid #f1dce7;font-size:13px}
    .nav a.active,.btn.primary,button{background:#f4c6dc;color:#432b39;border:1px solid #efc0d6}
    main{max-width:1050px;margin:0 auto;padding:26px 18px 45px}h1{margin:0 0 8px;font-size:28px}h2{font-size:18px;margin:0 0 12px}p{line-height:1.6;color:#756b75}.muted{color:#958592;font-size:13px}
    .panel,.card{border:1px solid #f0e3ea;border-radius:14px;padding:18px;margin:14px 0;background:#fff}.soft{background:#fff7fb}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    label{display:block;font-size:13px;font-weight:600;margin:12px 0 6px}input,select,textarea{width:100%;padding:11px;border:1px solid #e7dce3;border-radius:9px;font:inherit;background:#fff}textarea{min-height:120px;resize:vertical}
    button{border-radius:9px;padding:10px 14px;cursor:pointer;font-weight:600}form.inline{display:inline}.small{font-size:12px}.bar{height:8px;border-radius:8px;background:#f4e8ef;overflow:hidden}.fill{height:100%;background:#d990b5}.topic,.task,.note{padding:12px 0;border-bottom:1px solid #f2e8ee}.topic:last-child,.task:last-child,.note:last-child{border-bottom:0}
    .flash{padding:12px;border-radius:9px;background:#fff1f6;color:#824566;margin:12px 0}.right{margin-left:auto}.done{text-decoration:line-through;color:#958592}.pill{font-size:11px;padding:4px 7px;background:#fff1f7;border-radius:20px;color:#8b5271}
    @media(max-width:600px){header{padding:15px}main{padding:20px 13px}.nav{padding:10px 13px}h1{font-size:24px}}
    </style></head><body><header><div class="brand">lockedin<span>.</span> 🎀</div><a class="btn" href="{{ url_for('logout') }}">Log out</a></header>
    <nav class="nav"><a class="{{ 'active' if active=='dashboard' else '' }}" href="{{ url_for('dashboard') }}">Dashboard</a>
    <a class="{{ 'active' if active=='tracker' else '' }}" href="{{ url_for('syllabus_tracker') }}">Syllabus Tracker</a>
    <a class="{{ 'active' if active=='tasks' else '' }}" href="{{ url_for('daily_tasks') }}">Daily Tasks</a>
    <a class="{{ 'active' if active=='planner' else '' }}" href="{{ url_for('study_planner') }}">Study Planner</a>
    <a class="{{ 'active' if active=='notes' else '' }}" href="{{ url_for('notes') }}">My Notes</a>
    <a class="{{ 'active' if active=='schemes' else '' }}" href="{{ url_for('exam_schemes_page') }}">Exam Scheme</a>
    <a class="{{ 'active' if active=='progress' else '' }}" href="{{ url_for('progress') }}">Progress</a>
    <a class="{{ 'active' if active=='simulator' else '' }}" href="{{ url_for('exam_simulator') }}">Exam Simulator</a>
    <a class="{{ 'active' if active=='motivation' else '' }}" href="{{ url_for('motivation') }}">Motivation</a></nav>
    <main>{% if message %}<div class="flash">{{ message }}</div>{% endif %}{{ body|safe }}</main></body></html>""", title=title, body=body, active=active, message=request.args.get("message", ""))


AUTH_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>lockedin 🎀</title>
<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    min-height: 100vh;
    background: #ffffff;
    color: #29252a;
    font-family: Arial, Helvetica, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
}

.page {
    width: 100%;
    max-width: 410px;
    position: relative;
}

.bow {
    color: #e8a8c5;
    text-align: center;
    font-size: 27px;
    margin-bottom: 8px;
}

.brand {
    font-size: 39px;
    font-weight: 800;
    letter-spacing: -1.8px;
    text-align: center;
    margin: 0;
}

.tagline {
    color: #8b8189;
    text-align: center;
    font-size: 14px;
    margin: 8px 0 27px;
}

.card {
    border: 1px solid #f1e5ec;
    border-radius: 18px;
    padding: 27px;
    background: #fff;
    box-shadow: 0 8px 28px rgba(190, 135, 163, 0.07);
}

.tabs {
    display: flex;
    padding: 4px;
    background: #fff5fa;
    border-radius: 11px;
    margin-bottom: 24px;
}

.tabs a {
    width: 50%;
    padding: 11px 5px;
    text-align: center;
    text-decoration: none;
    color: #81747d;
    font-size: 14px;
    border-radius: 8px;
}

.tabs a.active {
    background: #f4dce9;
    color: #6e4056;
    font-weight: bold;
}

h2 {
    font-size: 23px;
    margin: 0 0 7px;
}

.subtitle {
    color: #8b8189;
    font-size: 13px;
    line-height: 1.6;
    margin: 0 0 23px;
}

label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    margin: 15px 0 8px;
}

input {
    width: 100%;
    padding: 12px 13px;
    border: 1px solid #e8dfe5;
    border-radius: 9px;
    font-size: 14px;
    outline: none;
    background: #fff;
}

input:focus {
    border-color: #d99abb;
    box-shadow: 0 0 0 3px #fff0f7;
}

.submit {
    width: 100%;
    border: none;
    border-radius: 9px;
    padding: 13px;
    background: #edc9dc;
    color: #56374a;
    font-size: 15px;
    font-weight: bold;
    margin-top: 23px;
    cursor: pointer;
}

.submit:hover { background: #e6b7d0; }

.message {
    background: #fff1f5;
    color: #8b3659;
    padding: 11px;
    border-radius: 8px;
    font-size: 13px;
    margin-bottom: 17px;
}

.decoration {
    position: absolute;
    color: #e8b3cd;
    font-size: 21px;
}

.left { left: -15px; top: 125px; }
.right { right: -12px; bottom: 100px; }

.footer {
    text-align: center;
    font-size: 12px;
    color: #a0969e;
    margin-top: 22px;
}

@media (max-width: 420px) {
    body { padding: 17px; }
    .card { padding: 21px; }
    .brand { font-size: 35px; }
}
</style>
</head>

<body>
<div class="page">
    <span class="decoration left">♡</span>
    <span class="decoration right">୨୧</span>

    <div class="bow">୨୧</div>
    <h1 class="brand">lockedin<span style="color:#e6b4cf">.</span></h1>
    <p class="tagline">your locked-in era starts here.</p>

    <div class="card">
        <div class="tabs">
            <a href="{{ url_for('login') }}"
               class="{{ 'active' if mode == 'login' else '' }}">
                Log in
            </a>
            <a href="{{ url_for('signup') }}"
               class="{{ 'active' if mode == 'signup' else '' }}">
                Sign up
            </a>
        </div>

        {% if message %}
            <div class="message">{{ message }}</div>
        {% endif %}

        {% if mode == 'login' %}
            <h2>Welcome back ♡</h2>
            <p class="subtitle">Let's get back to your goals.</p>

            <form method="POST">
                <label for="email">Email address</label>
                <input id="email" name="email" type="email"
                       placeholder="you@example.com" required>

                <label for="password">Password</label>
                <input id="password" name="password"
                       type="password" placeholder="Your password"
                       required>

                <button class="submit" type="submit">
                    Log in ♡
                </button>
            </form>

        {% else %}
            <h2>Create your account ♡</h2>
            <p class="subtitle">
                A little progress every day adds up.
            </p>

            <form method="POST">
                <label for="name">Your name</label>
                <input id="name" name="name" type="text"
                       placeholder="What should we call you?"
                       maxlength="80" required>

                <label for="email">Email address</label>
                <input id="email" name="email" type="email"
                       placeholder="you@example.com" required>

                <label for="password">Create password</label>
                <input id="password" name="password"
                       type="password" minlength="8"
                       placeholder="At least 8 characters" required>

                <button class="submit" type="submit">
                    Create account 🎀
                </button>
            </form>
        {% endif %}
    </div>

    <p class="footer">made for your next chapter ♡</p>
</div>
</body>
</html>
"""

@app.route("/")
def home():
    return redirect(url_for("dashboard" if session.get("user_id") else "login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    message = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 8:
            message = "Please complete every field. Passwords need 8 characters."
        else:
            try:
                with get_db() as db:
                    cur = db.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",
                                     (name, email, generate_password_hash(password)))
                    session["user_id"] = cur.lastrowid
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                message = "An account with that email already exists."
    return render_template_string(AUTH_PAGE, mode="signup", message=message)


@app.route("/login", methods=["GET", "POST"])
def login():
    message = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        message = "Email or password is incorrect. Please try again."
    return render_template_string(AUTH_PAGE, mode="login", message=message)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    gate = login_required()
    if gate: return gate
    user = current_user()
    uid = user["id"]
    with get_db() as db:
        topics = db.execute("SELECT COUNT(*) total, COALESCE(SUM(completed),0) done FROM study_topics WHERE user_id=? AND mode='whole_syllabus'", (uid,)).fetchone()
        tasks = db.execute("SELECT COUNT(*) total, COALESCE(SUM(completed),0) done FROM study_tasks WHERE user_id=? AND due_date=?", (uid, date.today().isoformat())).fetchone()
        notes_count = db.execute("SELECT COUNT(*) FROM study_notes WHERE user_id=?", (uid,)).fetchone()[0]
        schemes_count = db.execute("SELECT COUNT(*) FROM exam_schemes WHERE user_id=?", (uid,)).fetchone()[0]
    pct = round(100 * topics["done"] / topics["total"]) if topics["total"] else 0
    body = render_template_string("""
    <div class="panel soft"><div class="small pill">YOUR STUDY SPACE ♡</div><h1>Hi, {{ name }}!</h1><p>Welcome to your locked-in era. Small steps, real progress.</p></div>
    <h2>Today at a glance</h2><div class="grid">
    <div class="card"><p class="muted">Whole syllabus progress</p><h1>{{ pct }}%</h1><div class="bar"><div class="fill" style="width:{{ pct }}%"></div></div><p>{{ topic_done }} of {{ topic_total }} topics completed</p><a class="btn primary" href="{{ url_for('syllabus_tracker') }}">Open tracker</a></div>
    <div class="card"><p class="muted">Today's tasks</p><h1>{{ task_done }}/{{ task_total }}</h1><p>Tasks completed today</p><a class="btn primary" href="{{ url_for('daily_tasks') }}">Open tasks</a></div>
    <div class="card"><p class="muted">Saved notes</p><h1>{{ notes_count }}</h1><p>Notes ready whenever you need them.</p><a class="btn" href="{{ url_for('notes') }}">Open notes</a></div>
    <div class="card"><p class="muted">Saved exam schemes</p><h1>{{ schemes_count }}</h1><p>Keep your exam plan in one place.</p><a class="btn" href="{{ url_for('exam_schemes_page') }}">Open schemes</a></div></div>
    <h2>Study tools</h2><div class="grid">
    {% for item in tools %}<div class="card"><h2>{{ item[0] }}</h2><p>{{ item[1] }}</p><a class="btn primary" href="{{ url_for(item[2]) }}">Open tool →</a></div>{% endfor %}</div>
    """, name=user["name"], pct=pct, topic_done=topics["done"], topic_total=topics["total"], task_done=tasks["done"], task_total=tasks["total"], notes_count=notes_count, schemes_count=schemes_count,
    tools=[("Syllabus Tracker", "Whole syllabus and exam-scheme topic tracking.", "syllabus_tracker"), ("Daily Tasks", "Add tasks, mark work done, and track today.", "daily_tasks"), ("Study Planner", "Plan study work by date and priority.", "study_planner"), ("My Notes", "Save, edit, reopen, and print notes.", "notes"), ("Exam Scheme", "Save your own schemes and source files.", "exam_schemes_page"), ("Progress", "See real completion percentages by subject.", "progress"), ("Board Exam Simulator", "Build practice papers from your topics or provided text/PDF.", "exam_simulator"), ("Motivation & Streaks", "See recent study activity and streaks.", "motivation")])
    return page_shell("Dashboard", body, "dashboard")


@app.route("/syllabus-tracker", methods=["GET", "POST"])
def syllabus_tracker():
    gate = login_required()
    if gate: return gate
    uid = session["user_id"]
    if request.method == "POST":
        action = request.form.get("action", "add")
        activity_to_log = None
        with get_db() as db:
            if action == "add":
                subject = request.form.get("subject", "")
                mode = request.form.get("mode", "whole_syllabus")
                chapter = request.form.get("chapter", "").strip()[:150]
                topic = request.form.get("topic", "").strip()[:250]
                if subject in SUBJECTS and mode in ("whole_syllabus", "exam_scheme") and chapter and topic:
                    db.execute("INSERT INTO study_topics(user_id,subject,mode,chapter,topic) VALUES(?,?,?,?,?)", (uid,subject,mode,chapter,topic))
                    activity_to_log = "topic_added"
            elif action == "toggle":
                topic_id = request.form.get("topic_id", type=int)
                if topic_id:
                    db.execute("UPDATE study_topics SET completed=1-completed WHERE id=? AND user_id=?", (topic_id,uid))
                    activity_to_log = "topic_updated"
            elif action == "delete":
                topic_id = request.form.get("topic_id", type=int)
                if topic_id: db.execute("DELETE FROM study_topics WHERE id=? AND user_id=?", (topic_id,uid))
        if activity_to_log:
            log_activity(uid, activity_to_log)
        return redirect(url_for("syllabus_tracker", mode=request.form.get("mode_filter", request.form.get("mode", "whole_syllabus"))))
    mode = request.args.get("mode", "whole_syllabus")
    if mode not in ("whole_syllabus", "exam_scheme"): mode = "whole_syllabus"
    with get_db() as db:
        rows = db.execute("SELECT * FROM study_topics WHERE user_id=? AND mode=? ORDER BY subject,chapter,id", (uid,mode)).fetchall()
    rows = [dict(x) for x in rows]
    subject_stats=[]
    for s in SUBJECTS:
        items=[x for x in rows if x["subject"]==s]
        done=sum(x["completed"] for x in items); total=len(items)
        subject_stats.append({"name":s,"items":items,"done":done,"total":total,"pct":round(done*100/total) if total else 0})
    total=len(rows); done=sum(x["completed"] for x in rows); pct=round(done*100/total) if total else 0
    body=render_template_string("""
    <h1>Syllabus Tracker 🎀</h1><p>Track small topics instead of feeling overwhelmed by whole books.</p>
    <div class="row"><a class="btn {{ 'primary' if mode=='whole_syllabus' else '' }}" href="{{ url_for('syllabus_tracker',mode='whole_syllabus') }}">Whole Syllabus</a><a class="btn {{ 'primary' if mode=='exam_scheme' else '' }}" href="{{ url_for('syllabus_tracker',mode='exam_scheme') }}">Exam Scheme</a></div>
    <div class="panel soft"><h2>{{ pct }}% complete</h2><div class="bar"><div class="fill" style="width:{{ pct }}%"></div></div><p>{{ done }} of {{ total }} topics completed in this mode.</p></div>
    <div class="panel"><h2>＋ Add chapter/topic</h2><form method="post"><input type="hidden" name="action" value="add"><input type="hidden" name="mode" value="{{ mode }}">
    <label>Subject</label><select name="subject" required>{% for s in subjects %}<option>{{ s }}</option>{% endfor %}</select>
    <label>Chapter name or number</label><input name="chapter" required maxlength="150" placeholder="Chapter 1">
    <label>Topic, exercise, or question group</label><input name="topic" required maxlength="250" placeholder="Exercise 1.1 or Topic 1">
    <button type="submit">Add topic</button></form></div>
    {% for s in stats %}<div class="panel"><div class="row"><h2>{{ s.name }}</h2><span class="right pill">{{ s.pct }}% · {{ s.done }}/{{ s.total }}</span></div><div class="bar"><div class="fill" style="width:{{ s.pct }}%"></div></div>
    {% for t in s["items"] %}<div class="topic"><div class="row"><div class="{{ 'done' if t.completed else '' }}"><strong>{{ t.chapter }} — {{ t.topic }}</strong></div><span class="right"></span>
    <form method="post" class="inline"><input type="hidden" name="action" value="toggle"><input type="hidden" name="topic_id" value="{{ t.id }}"><input type="hidden" name="mode_filter" value="{{ mode }}"><button type="submit">{{ 'Undo' if t.completed else 'Mark done' }}</button></form>
    <form method="post" class="inline" onsubmit="return confirm('Delete this topic?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="topic_id" value="{{ t.id }}"><input type="hidden" name="mode_filter" value="{{ mode }}"><button type="submit">Delete</button></form></div></div>{% endfor %}</div>{% endfor %}
    """,mode=mode,subjects=SUBJECTS,stats=subject_stats,pct=pct,done=done,total=total)
    return page_shell("Syllabus Tracker",body,"tracker")


@app.route("/daily-tasks", methods=["GET", "POST"])
def daily_tasks():
    gate=login_required()
    if gate: return gate
    uid=session["user_id"]
    if request.method=="POST":
        action=request.form.get("action", "add")
        activity_to_log = None
        with get_db() as db:
            if action=="add":
                title=request.form.get("title", "").strip()[:200]
                subject=request.form.get("subject", "General")
                due=request.form.get("due_date", date.today().isoformat())
                priority=request.form.get("priority", "normal")
                if title and subject in SUBJECTS+['General'] and priority in ('low','normal','high'):
                    try: datetime.strptime(due, "%Y-%m-%d")
                    except ValueError: due=date.today().isoformat()
                    db.execute("INSERT INTO study_tasks(user_id,title,subject,due_date,priority) VALUES(?,?,?,?,?)",(uid,title,subject,due,priority))
            elif action=="toggle":
                task_id=request.form.get("task_id",type=int)
                if task_id:
                    db.execute("UPDATE study_tasks SET completed=1-completed WHERE id=? AND user_id=?",(task_id,uid))
                    activity_to_log = "task_completed"
            elif action=="delete":
                task_id=request.form.get("task_id",type=int)
                if task_id: db.execute("DELETE FROM study_tasks WHERE id=? AND user_id=?",(task_id,uid))
        if activity_to_log:
            log_activity(uid, activity_to_log)
        return redirect(url_for("daily_tasks"))
    today=date.today().isoformat()
    with get_db() as db:
        tasks=[dict(x) for x in db.execute("SELECT * FROM study_tasks WHERE user_id=? ORDER BY completed, due_date, CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, id DESC",(uid,)).fetchall()]
    todays=[t for t in tasks if t["due_date"]==today]
    done=sum(t["completed"] for t in todays)
    body=render_template_string("""
    <h1>Daily Tasks ☷</h1><p>Add the things you want to finish. Tick them when you're done.</p>
    <div class="panel soft"><h2>Today's progress: {{ done }}/{{ total }}</h2><div class="bar"><div class="fill" style="width:{{ pct }}%"></div></div><p>{{ pct }}% of today's tasks completed.</p></div>
    <div class="panel"><h2>＋ Add a task</h2><form method="post"><input type="hidden" name="action" value="add"><label>Task</label><input name="title" maxlength="200" required placeholder="Revise Physics chapter 2"><label>Subject</label><select name="subject">{% for s in subjects %}<option>{{ s }}</option>{% endfor %}<option>General</option></select><div class="grid"><div><label>Date</label><input type="date" name="due_date" value="{{ today }}" required></div><div><label>Priority</label><select name="priority"><option value="normal">Normal</option><option value="high">High</option><option value="low">Low</option></select></div></div><button>Add task</button></form></div>
    <div class="panel"><h2>My tasks</h2>{% if tasks %}{% for t in tasks %}<div class="task"><div class="row"><div class="{{ 'done' if t.completed else '' }}"><strong>{{ t.title }}</strong><div class="muted">{{ t.subject }} · {{ t.due_date }} · {{ t.priority|title }} priority</div></div><span class="right"></span><form method="post" class="inline"><input type="hidden" name="action" value="toggle"><input type="hidden" name="task_id" value="{{ t.id }}"><button>{{ 'Undo' if t.completed else 'Mark done' }}</button></form><form method="post" class="inline" onsubmit="return confirm('Delete this task?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="task_id" value="{{ t.id }}"><button>Delete</button></form></div></div>{% endfor %}{% else %}<p>No tasks yet. Add your first goal above ♡</p>{% endif %}</div>
    """,tasks=tasks,today=today,subjects=SUBJECTS,done=done,total=len(todays),pct=round(done*100/len(todays)) if todays else 0)
    return page_shell("Daily Tasks",body,"tasks")


@app.route("/study-planner", methods=["GET", "POST"])
def study_planner():
    gate=login_required()
    if gate: return gate
    # Planner uses the same saved tasks table, so there are no duplicate task records.
    if request.method=="POST":
        action=request.form.get("action")
        if action=="add":
            title=request.form.get("title","").strip()[:200]; subject=request.form.get("subject","General"); due=request.form.get("due_date",date.today().isoformat()); priority=request.form.get("priority","normal")
            if title:
                with get_db() as db: db.execute("INSERT INTO study_tasks(user_id,title,subject,due_date,priority) VALUES(?,?,?,?,?)",(session["user_id"],title,subject,due,priority))
        return redirect(url_for("study_planner"))
    with get_db() as db: tasks=[dict(x) for x in db.execute("SELECT * FROM study_tasks WHERE user_id=? ORDER BY due_date,id",(session["user_id"],)).fetchall()]
    body=render_template_string("""<h1>Study Planner ୨୧</h1><p>Build a flexible plan. You can change the date and priority by deleting a task and adding it again from Daily Tasks.</p>
    <div class="panel"><h2>Plan a study session</h2><form method="post"><input type="hidden" name="action" value="add"><label>What will you study?</label><input name="title" required maxlength="200"><label>Subject</label><select name="subject">{% for s in subjects %}<option>{{ s }}</option>{% endfor %}<option>General</option></select><div class="grid"><div><label>Date</label><input type="date" name="due_date" value="{{ today }}" required></div><div><label>Priority</label><select name="priority"><option value="normal">Normal</option><option value="high">High</option><option value="low">Low</option></select></div></div><button>Save to planner</button></form></div>
    <div class="panel"><h2>Upcoming plan</h2>{% for t in tasks %}<div class="task"><strong>{{ t.title }}</strong><div class="muted">{{ t.due_date }} · {{ t.subject }} · {{ 'Completed' if t.completed else 'Not completed' }}</div></div>{% else %}<p>No planned sessions yet.</p>{% endfor %}</div>""",tasks=tasks,subjects=SUBJECTS,today=date.today().isoformat())
    return page_shell("Study Planner",body,"planner")


@app.route("/notes", methods=["GET", "POST"])
def notes():
    gate=login_required()
    if gate: return gate
    uid=session["user_id"]
    if request.method=="POST":
        action=request.form.get("action","save")
        saved_note = False
        with get_db() as db:
            if action=="save":
                note_id=request.form.get("note_id",type=int); title=request.form.get("title","").strip()[:200]; subject=request.form.get("subject",""); content=request.form.get("content","")[:50000]
                if title and subject in SUBJECTS+['General']:
                    if note_id:
                        db.execute("UPDATE study_notes SET title=?,subject=?,content=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(title,subject,content,note_id,uid))
                    else:
                        db.execute("INSERT INTO study_notes(user_id,subject,title,content) VALUES(?,?,?,?)",(uid,subject,title,content))
                    saved_note = True
            elif action=="delete":
                note_id=request.form.get("note_id",type=int)
                if note_id: db.execute("DELETE FROM study_notes WHERE id=? AND user_id=?",(note_id,uid))
        if saved_note:
            log_activity(uid,"note_saved")
        return redirect(url_for("notes"))
    edit_id=request.args.get("edit",type=int)
    with get_db() as db:
        all_notes=[dict(x) for x in db.execute("SELECT * FROM study_notes WHERE user_id=? ORDER BY updated_at DESC,id DESC",(uid,)).fetchall()]
        selected=next((n for n in all_notes if n["id"]==edit_id),None)
    body=render_template_string("""<h1>My Notes ✎</h1><p>Save notes here and reopen them whenever you need revision.</p>
    <div class="panel"><h2>{{ 'Edit note' if selected else 'Create a note' }}</h2><form method="post"><input type="hidden" name="action" value="save"><input type="hidden" name="note_id" value="{{ selected.id if selected else '' }}"><label>Title</label><input name="title" maxlength="200" required value="{{ selected.title if selected else '' }}"><label>Subject</label><select name="subject">{% for s in subjects %}<option {% if selected and selected.subject==s %}selected{% endif %}>{{ s }}</option>{% endfor %}<option {% if selected and selected.subject=='General' %}selected{% endif %}>General</option></select><label>Note content (Urdu and English supported)</label><textarea name="content" dir="auto" placeholder="Write your revision notes here...">{{ selected.content if selected else '' }}</textarea><button>Save note</button> {% if selected %}<a class="btn" href="{{ url_for('notes') }}">Cancel edit</a>{% endif %} <button type="button" onclick="window.print()">Print / Save as PDF</button></form></div>
    <div class="panel"><h2>Saved notes</h2>{% for n in notes %}<div class="note"><div class="row"><strong>{{ n.title }}</strong><span class="pill">{{ n.subject }}</span><span class="right"></span><a class="btn" href="{{ url_for('notes',edit=n.id) }}">Open / edit</a><form class="inline" method="post" onsubmit="return confirm('Delete this note?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="note_id" value="{{ n.id }}"><button>Delete</button></form></div><p>{{ n.content[:180] }}{% if n.content|length>180 %}…{% endif %}</p><div class="muted">Updated {{ n.updated_at }}</div></div>{% else %}<p>No saved notes yet.</p>{% endfor %}</div>""",selected=selected,notes=all_notes,subjects=SUBJECTS)
    return page_shell("My Notes",body,"notes")


@app.route("/exam-schemes", methods=["GET", "POST"])
def exam_schemes_page():
    gate=login_required()
    if gate: return gate
    uid=session["user_id"]
    if request.method=="POST":
        action=request.form.get("action","save")
        with get_db() as db:
            if action=="save":
                title=request.form.get("title","").strip()[:200]; source_text=request.form.get("source_text","")[:100000]; file=request.files.get("scheme_file"); saved_path=""
                if file and file.filename:
                    filename=secure_filename(file.filename)
                    ext=filename.rsplit(".",1)[-1].lower() if "." in filename else ""
                    if ext in ALLOWED_UPLOADS:
                        unique=f"{uid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
                        file.save(UPLOAD_DIR/unique); saved_path=unique
                        if ext=="txt" or ext=="md":
                            try: source_text += "\n" + (UPLOAD_DIR/unique).read_text(encoding="utf-8",errors="ignore")[:100000]
                            except OSError: pass
                        elif ext=="pdf":
                            try:
                                from pypdf import PdfReader
                                reader=PdfReader(str(UPLOAD_DIR/unique))
                                source_text += "\n" + "\n".join((p.extract_text() or "") for p in reader.pages)[:100000]
                            except Exception:
                                source_text += "\n[PDF saved. Install pypdf to extract its text for paper generation.]"
                if title:
                    db.execute("INSERT INTO exam_schemes(user_id,title,file_path,source_text) VALUES(?,?,?,?)",(uid,title,saved_path,source_text))
            elif action=="delete":
                sid=request.form.get("scheme_id",type=int)
                row=db.execute("SELECT file_path FROM exam_schemes WHERE id=? AND user_id=?",(sid,uid)).fetchone() if sid else None
                if row:
                    db.execute("DELETE FROM exam_schemes WHERE id=? AND user_id=?",(sid,uid))
                    if row["file_path"]:
                        try: (UPLOAD_DIR/row["file_path"]).unlink(missing_ok=True)
                        except OSError: pass
        return redirect(url_for("exam_schemes_page"))
    with get_db() as db:
        schemes=[dict(x) for x in db.execute("SELECT id,title,file_path,created_at,substr(source_text,1,250) preview FROM exam_schemes WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()]
    body=render_template_string("""<h1>Exam Scheme 🎀</h1><p>Save a scheme as text, or attach a PDF/TXT/MD file. Saved schemes remain here until you delete them.</p>
    <div class="panel"><h2>＋ Add a scheme</h2><form method="post" enctype="multipart/form-data"><input type="hidden" name="action" value="save"><label>Scheme name</label><input name="title" required maxlength="200" placeholder="e.g. Class 12 Physics Annual Scheme"><label>Paste scheme details / board pattern</label><textarea name="source_text" placeholder="Add chapter selection, MCQs, short questions, long questions, marks, etc."></textarea><label>Optional file (PDF, TXT, MD)</label><input type="file" name="scheme_file" accept=".pdf,.txt,.md"><button>Save scheme</button></form></div>
    <div class="panel"><h2>Saved schemes</h2>{% for s in schemes %}<div class="note"><div class="row"><strong>{{ s.title }}</strong><span class="right"></span>{% if s.file_path %}<a class="btn" href="{{ url_for('scheme_file',scheme_id=s.id) }}">Open file</a>{% endif %}<a class="btn" href="{{ url_for('exam_simulator',scheme_id=s.id) }}">Use in simulator</a><form method="post" class="inline" onsubmit="return confirm('Delete this scheme?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="scheme_id" value="{{ s.id }}"><button>Delete</button></form></div><p>{{ s.preview }}</p><div class="muted">Saved {{ s.created_at }}</div></div>{% else %}<p>No schemes saved yet. Add your first one above.</p>{% endfor %}</div>""",schemes=schemes)
    return page_shell("Exam Scheme",body,"schemes")


@app.route("/scheme-file/<int:scheme_id>")
def scheme_file(scheme_id):
    gate=login_required()
    if gate: return gate
    with get_db() as db: row=db.execute("SELECT file_path FROM exam_schemes WHERE id=? AND user_id=?",(scheme_id,session["user_id"])).fetchone()
    if not row or not row["file_path"]: abort(404)
    return send_from_directory(UPLOAD_DIR,row["file_path"],as_attachment=False)


@app.route("/progress")
def progress():
    gate=login_required()
    if gate: return gate
    uid=session["user_id"]
    with get_db() as db:
        topics=[dict(x) for x in db.execute("SELECT subject,mode,completed FROM study_topics WHERE user_id=?",(uid,)).fetchall()]
        task=db.execute("SELECT COUNT(*) total,COALESCE(SUM(completed),0) done FROM study_tasks WHERE user_id=?",(uid,)).fetchone()
        results=db.execute("SELECT * FROM exam_results WHERE user_id=? ORDER BY id DESC LIMIT 10",(uid,)).fetchall()
    stats=[]
    for subject in SUBJECTS:
        group=[x for x in topics if x["subject"]==subject and x["mode"]=="whole_syllabus"]
        done=sum(x["completed"] for x in group); total=len(group)
        scheme=[x for x in topics if x["subject"]==subject and x["mode"]=="exam_scheme"]
        sd=sum(x["completed"] for x in scheme); st=len(scheme)
        stats.append({"name":subject,"done":done,"total":total,"pct":round(100*done/total) if total else 0,"sdone":sd,"stotal":st,"spct":round(100*sd/st) if st else 0})
    total=len(topics); done=sum(x["completed"] for x in topics if x["mode"]=="whole_syllabus"); whole=sum(x["mode"]=="whole_syllabus" for x in topics); pct=round(done*100/whole) if whole else 0
    body=render_template_string("""<h1>Progress Dashboard ♡</h1><p>These percentages are calculated from topics and tasks you actually save and complete.</p><div class="grid"><div class="card"><p class="muted">Whole syllabus</p><h1>{{ pct }}%</h1><p>{{ done }}/{{ whole }} topics completed</p><div class="bar"><div class="fill" style="width:{{ pct }}%"></div></div></div><div class="card"><p class="muted">All saved tasks</p><h1>{{ task.done }}/{{ task.total }}</h1><p>Completed tasks overall</p><div class="bar"><div class="fill" style="width:{{ taskpct }}%"></div></div></div></div><h2>Subject-wise progress</h2>{% for s in stats %}<div class="panel"><div class="row"><strong>{{ s.name }}</strong><span class="right">Whole: {{ s.pct }}% ({{ s.done }}/{{ s.total }})</span></div><div class="bar"><div class="fill" style="width:{{ s.pct }}%"></div></div><p class="muted">Exam scheme: {{ s.spct }}% ({{ s.sdone }}/{{ s.stotal }})</p><div class="bar"><div class="fill" style="width:{{ s.spct }}%"></div></div></div>{% endfor %}<h2>Recent exam attempts</h2>{% for r in results %}<div class="panel"><strong>{{ r.subject }}</strong><p>{{ r.score }}/{{ r.total_marks }} self-assessed marks · {{ r.created_at }}</p></div>{% else %}<p>No exam attempts saved yet.</p>{% endfor %}""",stats=stats,pct=pct,done=done,whole=whole,task=task,taskpct=round(100*task["done"]/task["total"]) if task["total"] else 0,results=results)
    return page_shell("Progress",body,"progress")


def extract_uploaded_text(file_storage):
    filename=secure_filename(file_storage.filename or "upload")
    ext=filename.rsplit(".",1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_UPLOADS: return "", "Please upload a PDF, TXT, or MD file."
    unique=f"paper_{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
    path=UPLOAD_DIR/unique; file_storage.save(path)
    if ext in ("txt","md"):
        return path.read_text(encoding="utf-8",errors="ignore")[:100000], ""
    try:
        from pypdf import PdfReader
        reader=PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)[:100000], ""
    except ImportError:
        return "", "The PDF was uploaded, but PDF text extraction needs pypdf. Install it with: pip install pypdf. You can also paste the relevant text below."
    except Exception:
        return "", "Could not extract text from this PDF. It may be scanned; paste its text or use an OCR tool first."


def make_paper(subject, mode, source_text, topics, mcq_count, short_count, long_count):
    # Transparent template-based generator. It uses the supplied text/topics; it does not call an AI API.
    cleaned=[]
    for line in re.split(r"[\n\r]+|(?<=[.!?])\s+", source_text or ""):
        line=re.sub(r"\s+"," ",line).strip(" •\t-–")
        if 35 <= len(line) <= 350 and not line.lower().startswith(("page ","chapter ")):
            cleaned.append(line)
    topic_names=[f"{x['chapter']}: {x['topic']}" for x in topics]
    seeds=[]
    for x in topic_names+cleaned:
        if x and x not in seeds: seeds.append(x)
    if not seeds:
        return [], "Add syllabus topics or provide extracted book/past-paper text before generating a paper."
    questions=[]
    for i in range(mcq_count):
        seed=seeds[i % len(seeds)]
        questions.append({"type":"MCQ","number":i+1,"marks":1,"prompt":f"Choose the correct answer related to: {seed}","choices":["Option A — write/select the correct concept","Option B — alternative concept","Option C — alternative concept","Option D — alternative concept"]})
    for i in range(short_count):
        seed=seeds[(i+mcq_count) % len(seeds)]
        prompts=[f"Define or explain briefly: {seed}.",f"Write a short note on {seed}.",f"State the main points related to {seed}.",f"Give one relevant example and explain {seed}."]
        questions.append({"type":"Short question","number":i+1,"marks":3,"prompt":prompts[i%len(prompts)],"choices":[]})
    for i in range(long_count):
        seed=seeds[(i+mcq_count+short_count) % len(seeds)]
        prompts=[f"Explain {seed} in detail, including important steps or examples.",f"Discuss the main concepts related to {seed} and support your answer with examples.",f"Describe {seed} fully and explain its significance."]
        questions.append({"type":"Long question","number":i+1,"marks":8,"prompt":prompts[i%len(prompts)],"choices":[]})
    return questions, ""


@app.route("/exam-simulator", methods=["GET", "POST"])
def exam_simulator():
    gate=login_required()
    if gate: return gate
    uid=session["user_id"]
    if request.method=="POST":
        action=request.form.get("action","generate")
        if action=="save_attempt":
            subject=request.form.get("subject",SUBJECTS[0]); mode=request.form.get("mode","whole_syllabus"); score=request.form.get("score",type=float) or 0; total=request.form.get("total",type=float) or 0; weak=request.form.get("weak_topics","")[:1000]
            with get_db() as db: db.execute("INSERT INTO exam_results(user_id,subject,mode,score,total_marks,weak_topics) VALUES(?,?,?,?,?,?)",(uid,subject,mode,score,total,weak))
            log_activity(uid,"exam_attempt")
            return redirect(url_for("progress"))
        subject=request.form.get("subject",SUBJECTS[0]); mode=request.form.get("mode","whole_syllabus")
        if subject not in SUBJECTS: subject=SUBJECTS[0]
        if mode not in ("whole_syllabus","exam_scheme"): mode="whole_syllabus"
        source=request.form.get("source_text","")[:100000]
        upload=request.files.get("source_file")
        if upload and upload.filename:
            extracted, err=extract_uploaded_text(upload)
            if extracted: source += "\n"+extracted
            if err: return redirect(url_for("exam_simulator",message=err))
        scheme_id=request.form.get("scheme_id",type=int) or request.args.get("scheme_id",type=int)
        if scheme_id:
            with get_db() as db:
                scheme=db.execute("SELECT source_text FROM exam_schemes WHERE id=? AND user_id=?",(scheme_id,uid)).fetchone()
            if scheme: source += "\n"+scheme["source_text"]
        with get_db() as db: topics=[dict(x) for x in db.execute("SELECT chapter,topic FROM study_topics WHERE user_id=? AND subject=? AND mode=? ORDER BY id",(uid,subject,mode)).fetchall()]
        try: mcqs=max(0,min(50,int(request.form.get("mcq_count",10)))); shorts=max(0,min(30,int(request.form.get("short_count",8)))); longs=max(0,min(15,int(request.form.get("long_count",3))))
        except (ValueError,TypeError): mcqs,shorts,longs=10,8,3
        questions,err=make_paper(subject,mode,source,topics,mcqs,shorts,longs)
        if err: return redirect(url_for("exam_simulator",message=err))
        title=f"{subject} practice paper — {date.today().isoformat()}"
        with get_db() as db:
            cur=db.execute("INSERT INTO exam_papers(user_id,subject,mode,title,paper_json) VALUES(?,?,?,?,?)",(uid,subject,mode,title,json.dumps(questions,ensure_ascii=False)))
            paper_id=cur.lastrowid
        return redirect(url_for("exam_simulator",paper_id=paper_id))
    paper_id=request.args.get("paper_id",type=int)
    paper=None
    if paper_id:
        with get_db() as db: row=db.execute("SELECT * FROM exam_papers WHERE id=? AND user_id=?",(paper_id,uid)).fetchone()
        if row: paper=dict(row); paper["questions"]=json.loads(paper["paper_json"])
    with get_db() as db:
        schemes=[dict(x) for x in db.execute("SELECT id,title FROM exam_schemes WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()]
    body=render_template_string("""<h1>Board Exam Simulator ✿</h1><p>Create a practice paper from your saved syllabus topics, a saved exam scheme, or text extracted from your book/past paper.</p>
    <div class="panel soft"><strong>Important:</strong> This built-in version uses templates and the source material you provide; it does not use an AI model or automatically know the official board pattern. Set question counts to match your board scheme. For PDF extraction, install <code>pypdf</code> with <code>pip install pypdf</code>.</div>
    <div class="panel"><h2>Generate a paper</h2><form method="post" enctype="multipart/form-data"><input type="hidden" name="action" value="generate"><label>Subject</label><select name="subject">{% for s in subjects %}<option>{{ s }}</option>{% endfor %}</select><label>Study mode</label><select name="mode"><option value="whole_syllabus">Whole Syllabus</option><option value="exam_scheme">Exam Scheme</option></select><label>Use saved scheme (optional)</label><select name="scheme_id"><option value="">No saved scheme</option>{% for s in schemes %}<option value="{{ s.id }}" {% if request.args.get('scheme_id')|int == s.id %}selected{% endif %}>{{ s.title }}</option>{% endfor %}</select><div class="grid"><div><label>MCQs (1 mark each)</label><input type="number" name="mcq_count" min="0" max="50" value="10"></div><div><label>Short questions (3 marks each)</label><input type="number" name="short_count" min="0" max="30" value="8"></div><div><label>Long questions (8 marks each)</label><input type="number" name="long_count" min="0" max="15" value="3"></div></div><label>Paste relevant book/past-paper/scheme text (optional if topics exist)</label><textarea name="source_text" placeholder="Paste readable text from your book or past paper here..."></textarea><label>Or upload PDF/TXT/MD</label><input type="file" name="source_file" accept=".pdf,.txt,.md"><button>Generate practice paper</button></form></div>
    {% if paper %}<div class="panel"><h2>{{ paper.title }}</h2><p class="muted">{{ paper.subject }} · {{ 'Exam Scheme' if paper.mode=='exam_scheme' else 'Whole Syllabus' }}</p>{% for q in paper.questions %}<div class="topic"><strong>{{ q.type }} {{ q.number }}.</strong> {{ q.prompt }} <span class="pill">{{ q.marks }} mark(s)</span>{% for choice in q.choices %}<div class="muted">{{ loop.index }}. {{ choice }}</div>{% endfor %}<label>Your answer / working</label><textarea placeholder="Write your answer on paper or here..."></textarea></div>{% endfor %}<h2>Record your result</h2><p>This is self-assessed; the current version does not automatically mark answers.</p><form method="post"><input type="hidden" name="action" value="save_attempt"><input type="hidden" name="subject" value="{{ paper.subject }}"><input type="hidden" name="mode" value="{{ paper.mode }}"><label>Your score</label><input type="number" name="score" min="0" step="0.5" value="0"><label>Total marks</label><input type="number" name="total" min="0" step="0.5" value="{{ paper.questions|sum(attribute='marks') }}"><label>Topics to revise</label><input name="weak_topics" placeholder="Topics you found difficult"><button>Save attempt to progress</button><button type="button" onclick="window.print()">Print / Save as PDF</button></form></div>{% endif %}
    """,subjects=SUBJECTS,schemes=schemes,paper=paper)
    return page_shell("Exam Simulator",body,"simulator")


@app.route("/motivation")
def motivation():
    gate=login_required()
    if gate: return gate
    uid=session["user_id"]
    with get_db() as db:
        days=[x[0] for x in db.execute("SELECT DISTINCT activity_date FROM activity_log WHERE user_id=? ORDER BY activity_date DESC",(uid,)).fetchall()]
        completed=db.execute("SELECT COUNT(*) FROM study_tasks WHERE user_id=? AND completed=1",(uid,)).fetchone()[0]
        topics=db.execute("SELECT COUNT(*) FROM study_topics WHERE user_id=? AND completed=1",(uid,)).fetchone()[0]
    dates=set(days); streak=0; d=date.today()
    if d.isoformat() not in dates: d-=timedelta(days=1)
    while d.isoformat() in dates:
        streak+=1; d-=timedelta(days=1)
    body=render_template_string("""<h1>Motivation & Streaks 🌸</h1><p>Consistency matters more than perfection. A little effort still counts.</p><div class="grid"><div class="card"><p class="muted">Current activity streak</p><h1>{{ streak }} day{{ '' if streak==1 else 's' }}</h1><p>Based on recorded activity. Marking topics and tasks creates activity.</p></div><div class="card"><p class="muted">Tasks completed</p><h1>{{ completed }}</h1><p>Every finished task is progress.</p></div><div class="card"><p class="muted">Topics completed</p><h1>{{ topics }}</h1><p>One topic at a time adds up.</p></div></div><div class="panel soft"><h2>Your reminder ♡</h2><p>You don't have to finish everything today. Choose one small task, give it your attention, and let that be enough to start.</p><a class="btn primary" href="{{ url_for('daily_tasks') }}">Choose today's task</a></div><div class="panel"><h2>Recent active dates</h2>{% for d in days[:14] %}<span class="pill">{{ d }}</span> {% else %}<p>No activity logged yet. Complete a task or topic to start your streak.</p>{% endfor %}</div>""",streak=streak,completed=completed,topics=topics,days=days)
    return page_shell("Motivation",body,"motivation")


# Initialize the shared database once before the server begins accepting requests.
init_db()

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5005)
