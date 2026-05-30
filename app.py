"""
EnglishUp Backend — Flask API
Maneja: usuarios, progreso, sesiones, estadísticas
Base de datos: SQLite
"""
import anthropic
from flask import Flask, request, jsonify, session
from flask_cors import CORS
import sqlite3
import hashlib
import os
import json
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'englishup-secret-dev-2024')
CORS(app, supports_credentials=True, origins=['*'])

DB_PATH = os.environ.get('DB_PATH', 'englishup.db')

# =====================
# DATABASE SETUP
# =====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active DATE,
                streak INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                xp INTEGER DEFAULT 0,
                lessons_completed TEXT DEFAULT '[]',
                quizzes_completed INTEGER DEFAULT 0,
                chat_messages INTEGER DEFAULT 0,
                badges TEXT DEFAULT '[]',
                levels_visited TEXT DEFAULT '[]',
                perfect_quiz INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT,
                xp_earned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS quiz_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                level TEXT,
                score INTEGER,
                total INTEGER,
                xp_earned INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

# =====================
# HELPERS
# =====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Debes iniciar sesión'}), 401
        return f(*args, **kwargs)
    return decorated

def get_or_create_progress(conn, user_id: int) -> dict:
    row = conn.execute('SELECT * FROM progress WHERE user_id = ?', (user_id,)).fetchone()
    if not row:
        conn.execute('INSERT INTO progress (user_id) VALUES (?)', (user_id,))
        conn.commit()
        row = conn.execute('SELECT * FROM progress WHERE user_id = ?', (user_id,)).fetchone()
    return dict(row)

# =====================
# AUTH ROUTES
# =====================
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not name or not email or not password:
        return jsonify({'error': 'Todos los campos son requeridos'}), 400
    if len(password) < 6:
        return jsonify({'error': 'La contraseña debe tener al menos 6 caracteres'}), 400
    if '@' not in email:
        return jsonify({'error': 'Correo inválido'}), 400

    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO users (name, email, password_hash, last_active) VALUES (?, ?, ?, ?)',
                (name, email, hash_password(password), datetime.now().date())
            )
            conn.commit()
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            # Create initial progress
            conn.execute('INSERT INTO progress (user_id) VALUES (?)', (user['id'],))
            conn.commit()

        session['user_id'] = user['id']
        session['user_name'] = name
        session['user_email'] = email

        return jsonify({
            'success': True,
            'user': {'id': user['id'], 'name': name, 'email': email},
            'message': f'¡Bienvenido, {name}!'
        })
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Este correo ya está registrado'}), 409

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    with get_db() as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE email = ? AND password_hash = ?',
            (email, hash_password(password))
        ).fetchone()

        if not user:
            return jsonify({'error': 'Credenciales incorrectas'}), 401

        # Update streak
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        last = user['last_active']

        streak = user['streak']
        if last == str(yesterday):
            streak += 1
        elif last != str(today):
            streak = 1

        conn.execute(
            'UPDATE users SET last_active = ?, streak = ? WHERE id = ?',
            (today, streak, user['id'])
        )
        conn.commit()

        progress = get_or_create_progress(conn, user['id'])

    session['user_id'] = user['id']
    session['user_name'] = user['name']
    session['user_email'] = email

    return jsonify({
        'success': True,
        'user': {'id': user['id'], 'name': user['name'], 'email': email, 'streak': streak},
        'progress': {
            'xp': progress['xp'],
            'lessons_completed': json.loads(progress['lessons_completed'] or '[]'),
            'quizzes_completed': progress['quizzes_completed'],
            'chat_messages': progress['chat_messages'],
            'badges': json.loads(progress['badges'] or '[]'),
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
@login_required
def me():
    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        progress = get_or_create_progress(conn, session['user_id'])
    return jsonify({
        'user': {'id': user['id'], 'name': user['name'], 'email': user['email'], 'streak': user['streak']},
        'progress': {
            'xp': progress['xp'],
            'lessons_completed': json.loads(progress['lessons_completed'] or '[]'),
            'quizzes_completed': progress['quizzes_completed'],
            'chat_messages': progress['chat_messages'],
            'badges': json.loads(progress['badges'] or '[]'),
        }
    })

# =====================
# PROGRESS ROUTES
# =====================
@app.route('/api/progress', methods=['GET'])
@login_required
def get_progress():
    with get_db() as conn:
        progress = get_or_create_progress(conn, session['user_id'])
        user = conn.execute('SELECT streak FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    return jsonify({
        'xp': progress['xp'],
        'lessons_completed': json.loads(progress['lessons_completed'] or '[]'),
        'quizzes_completed': progress['quizzes_completed'],
        'chat_messages': progress['chat_messages'],
        'badges': json.loads(progress['badges'] or '[]'),
        'levels_visited': json.loads(progress['levels_visited'] or '[]'),
        'perfect_quiz': bool(progress['perfect_quiz']),
        'streak': user['streak'] if user else 0,
    })

@app.route('/api/progress/sync', methods=['POST'])
@login_required
def sync_progress():
    """Sync frontend state to backend"""
    data = request.get_json()
    user_id = session['user_id']

    with get_db() as conn:
        conn.execute('''
            UPDATE progress SET
                xp = ?, lessons_completed = ?, quizzes_completed = ?,
                chat_messages = ?, badges = ?, levels_visited = ?,
                perfect_quiz = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        ''', (
            data.get('xp', 0),
            json.dumps(data.get('lessons_completed', [])),
            data.get('quizzes_completed', 0),
            data.get('chat_messages', 0),
            json.dumps(data.get('badges', [])),
            json.dumps(data.get('levels_visited', [])),
            1 if data.get('perfect_quiz') else 0,
            user_id
        ))
        conn.commit()

    return jsonify({'success': True})

@app.route('/api/progress/lesson', methods=['POST'])
@login_required
def complete_lesson():
    data = request.get_json()
    lesson_id = data.get('lesson_id')
    xp_earned = data.get('xp', 20)
    user_id = session['user_id']

    with get_db() as conn:
        progress = get_or_create_progress(conn, user_id)
        lessons = json.loads(progress['lessons_completed'] or '[]')

        if lesson_id not in lessons:
            lessons.append(lesson_id)
            new_xp = progress['xp'] + xp_earned

            conn.execute(
                'UPDATE progress SET lessons_completed = ?, xp = ? WHERE user_id = ?',
                (json.dumps(lessons), new_xp, user_id)
            )
            conn.execute(
                'INSERT INTO activity_log (user_id, action, detail, xp_earned) VALUES (?, ?, ?, ?)',
                (user_id, 'lesson_complete', lesson_id, xp_earned)
            )
            conn.commit()
            return jsonify({'success': True, 'xp': new_xp, 'xp_earned': xp_earned})

    return jsonify({'success': False, 'message': 'Lección ya completada'})

@app.route('/api/progress/quiz', methods=['POST'])
@login_required
def save_quiz():
    data = request.get_json()
    user_id = session['user_id']
    score = data.get('score', 0)
    total = data.get('total', 10)
    level = data.get('level', 'beginner')
    xp_earned = score * 5

    with get_db() as conn:
        progress = get_or_create_progress(conn, user_id)
        new_xp = progress['xp'] + xp_earned
        new_quizzes = progress['quizzes_completed'] + 1
        perfect = 1 if score == total else progress['perfect_quiz']

        conn.execute(
            'UPDATE progress SET xp = ?, quizzes_completed = ?, perfect_quiz = ? WHERE user_id = ?',
            (new_xp, new_quizzes, perfect, user_id)
        )
        conn.execute(
            'INSERT INTO quiz_results (user_id, level, score, total, xp_earned) VALUES (?, ?, ?, ?, ?)',
            (user_id, level, score, total, xp_earned)
        )
        conn.commit()

    return jsonify({'success': True, 'xp': new_xp, 'xp_earned': xp_earned})

@app.route('/api/progress/chat', methods=['POST'])
@login_required
def log_chat():
    user_id = session['user_id']
    xp_earned = request.get_json().get('xp', 2)

    with get_db() as conn:
        progress = get_or_create_progress(conn, user_id)
        conn.execute(
            'UPDATE progress SET chat_messages = chat_messages + 1, xp = xp + ? WHERE user_id = ?',
            (xp_earned, user_id)
        )
        conn.commit()
        progress = get_or_create_progress(conn, user_id)

    return jsonify({'success': True, 'xp': progress['xp']})

# =====================
# LEADERBOARD
# =====================
@app.route('/api/leaderboard', methods=['GET'])
def leaderboard():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT u.name, p.xp, p.quizzes_completed, p.chat_messages
            FROM users u
            JOIN progress p ON u.id = p.user_id
            ORDER BY p.xp DESC
            LIMIT 10
        ''').fetchall()
    return jsonify([dict(r) for r in rows])

# =====================
# STATS
# =====================
@app.route('/api/stats', methods=['GET'])
def stats():
    with get_db() as conn:
        total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total_lessons = conn.execute('SELECT SUM(quizzes_completed) FROM progress').fetchone()[0] or 0
        total_messages = conn.execute('SELECT SUM(chat_messages) FROM progress').fetchone()[0] or 0
    return jsonify({
        'total_users': total_users,
        'total_quizzes': total_lessons,
        'total_chat_messages': total_messages,
    })

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# =====================
# INIT
# =====================
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') == 'development'
    print(f"🚀 EnglishUp backend running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    messages = data.get('messages', [])
    
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        system="""You are Emma, a friendly English teacher for Spanish-speaking students...""",
        messages=messages
    )
    
    return jsonify({'reply': response.content[0].text})
