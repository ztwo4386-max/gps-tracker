import os
import sqlite3
import math
import secrets
from datetime import datetime
from flask import Flask, request, jsonify, g, redirect, url_for, render_template_string, flash
from flask_cors import CORS
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-ganti-ini-pas-production")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Silakan login dulu untuk mengakses dashboard."

# Railway pakai volume mount di /data untuk penyimpanan persisten.
# Kalau env var DB_PATH gak diset (misal waktu run lokal), fallback ke file lokal.
DB_PATH = os.environ.get("DB_PATH", "tracker.db")


# ---------- Database setup ----------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS armada (
            armada_id TEXT PRIMARY KEY,
            nama TEXT,
            nopol TEXT,
            last_zone_id TEXT,
            last_lat REAL,
            last_lon REAL,
            last_update TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS log_posisi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            armada_id TEXT,
            lat REAL,
            lon REAL,
            kecepatan REAL,
            waktu TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS zona (
            zona_id TEXT PRIMARY KEY,
            nama TEXT,
            lat REAL,
            lon REAL,
            radius_meter REAL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS event_zona (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            armada_id TEXT,
            zona_id TEXT,
            jenis_event TEXT,
            waktu TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            armada_id TEXT
        )
    """)

    # Seed akun admin default kalau belum ada user sama sekali
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        default_password = "admin123"
        cur.execute(
            "INSERT INTO users (username, password_hash, role, armada_id) VALUES (?, ?, ?, ?)",
            ("admin", generate_password_hash(default_password), "admin", None),
        )
        print("=" * 60)
        print("AKUN ADMIN DEFAULT DIBUAT:")
        print("  username: admin")
        print("  password: admin123")
        print("SEGERA LOGIN DAN GANTI PASSWORD INI lewat halaman /users")
        print("=" * 60)

    # Seed contoh zona kalau tabel masih kosong -- EDIT koordinat ini sesuai lokasi asli
    cur.execute("SELECT COUNT(*) FROM zona")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO zona (zona_id, nama, lat, lon, radius_meter) VALUES (?, ?, ?, ?, ?)",
            [
                ("ZONA-PLANT", "Plant Citeureup", -6.4433, 106.9316, 300),
                ("ZONA-GUDANG", "Gudang Bandung", -6.9175, 107.6191, 300),
            ],
        )

    conn.commit()
    conn.close()


# ---------- Geofence helper ----------

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_current_zone(db, lat, lon):
    zones = db.execute("SELECT * FROM zona").fetchall()
    for z in zones:
        d = haversine_meters(lat, lon, z["lat"], z["lon"])
        if d <= z["radius_meter"]:
            return z["zona_id"], z["nama"]
    return None, None


# ---------- Autentikasi ----------

class User(UserMixin):
    def __init__(self, id, username, role, armada_id):
        self.id = id
        self.username = username
        self.role = role
        self.armada_id = armada_id


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return User(row["id"], row["username"], row["role"], row["armada_id"])


LOGIN_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login - Fleet Tracker</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<style>
  body {
    margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    background:#1C1A17; color:#F3EFE6; font-family: -apple-system, "Segoe UI", sans-serif;
  }
  .login-card {
    background:#252220; border:1px solid #423D36; border-radius:10px;
    padding:32px; width:320px;
  }
  .login-card h4 { margin-bottom:20px; }
  .form-control {
    background:#2D2A25; border:1px solid #423D36; color:#F3EFE6;
  }
  .form-control:focus { background:#2D2A25; color:#F3EFE6; border-color:#FF6A1A; box-shadow:none; }
  .btn-primary { background:#FF6A1A; border-color:#FF6A1A; }
  .btn-primary:hover { background:#C8500F; border-color:#C8500F; }
  .alert-danger { font-size: 13px; }
</style>
</head>
<body>
<div class="login-card">
  <h4><i class="bi bi-truck"></i> Fleet Tracker</h4>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-danger py-2">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Username</label>
      <input type="text" name="username" class="form-control" required autofocus>
    </div>
    <div class="mb-3">
      <label class="form-label">Password</label>
      <input type="password" name="password" class="form-control" required>
    </div>
    <button type="submit" class="btn btn-primary w-100">Login</button>
  </form>
</div>
</body>
</html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if row is None or not check_password_hash(row["password_hash"], password):
            flash("Username atau password salah.")
            return redirect(url_for("login"))

        user = User(row["id"], row["username"], row["role"], row["armada_id"])
        login_user(user)
        return redirect(url_for("dashboard"))

    return render_template_string(LOGIN_HTML)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


def admin_required(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            return jsonify({"status": "error", "message": "Khusus admin"}), 403
        return f(*args, **kwargs)
    return wrapper


USERS_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kelola User - Fleet Tracker</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<style>
  body { margin:0; background:#1C1A17; color:#F3EFE6; font-family: -apple-system, "Segoe UI", sans-serif; padding:24px; }
  .panel { background:#252220; border:1px solid #423D36; border-radius:8px; padding:20px; max-width:600px; margin-bottom:20px; }
  .form-control, .form-select { background:#2D2A25; border:1px solid #423D36; color:#F3EFE6; }
  .form-control:focus, .form-select:focus { background:#2D2A25; color:#F3EFE6; border-color:#FF6A1A; box-shadow:none; }
  .btn-primary { background:#FF6A1A; border-color:#FF6A1A; }
  table { width:100%; font-size:13px; }
  th, td { padding:8px; border-bottom:1px solid #423D36; }
  a { color:#FF6A1A; }
</style>
</head>
<body>
<p><a href="/">&larr; Kembali ke dashboard</a></p>
<h4>Kelola User</h4>

{% with messages = get_flashed_messages() %}
  {% if messages %}<div class="alert alert-info py-2">{{ messages[0] }}</div>{% endif %}
{% endwith %}

<div class="panel">
  <h6>Tambah user baru</h6>
  <form method="POST" action="/users/create">
    <div class="mb-2">
      <label class="form-label">Username</label>
      <input type="text" name="username" class="form-control" required>
    </div>
    <div class="mb-2">
      <label class="form-label">Password</label>
      <input type="text" name="password" class="form-control" required>
    </div>
    <div class="mb-2">
      <label class="form-label">Role</label>
      <select name="role" class="form-select" id="roleSelect" onchange="toggleArmada()">
        <option value="admin">Admin (akses penuh)</option>
        <option value="owner">Owner (lihat semua, tanpa kelola user)</option>
        <option value="supir">Supir (lihat armada sendiri saja)</option>
      </select>
    </div>
    <div class="mb-2" id="armadaField">
      <label class="form-label">Armada ID (khusus role supir)</label>
      <input type="text" name="armada_id" class="form-control" placeholder="contoh: ARM-001">
    </div>
    <button type="submit" class="btn btn-primary">Tambah User</button>
  </form>
</div>

<div class="panel">
  <h6>Daftar user</h6>
  <table>
    <thead><tr><th>Username</th><th>Role</th><th>Armada</th></tr></thead>
    <tbody>
    {% for u in users %}
      <tr><td>{{ u.username }}</td><td>{{ u.role }}</td><td>{{ u.armada_id or '-' }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<script>
function toggleArmada() {
  const role = document.getElementById('roleSelect').value;
  document.getElementById('armadaField').style.display = role === 'supir' ? 'block' : 'none';
}
toggleArmada();
</script>
</body>
</html>"""


@app.route("/users")
@login_required
@admin_required
def users_page():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY id").fetchall()
    return render_template_string(USERS_HTML, users=users)


@app.route("/users/create", methods=["POST"])
@login_required
@admin_required
def users_create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "supir")
    armada_id = request.form.get("armada_id", "").strip() or None

    if not username or not password:
        flash("Username dan password wajib diisi.")
        return redirect(url_for("users_page"))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, role, armada_id) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), role, armada_id),
        )
        db.commit()
        flash(f"User '{username}' berhasil dibuat.")
    except sqlite3.IntegrityError:
        flash(f"Username '{username}' sudah dipakai.")

    return redirect(url_for("users_page"))




@app.route("/api/track", methods=["GET", "POST"])
def track():
    armada_id = request.values.get("armada_id", "").strip()
    lat = request.values.get("lat", type=float)
    lon = request.values.get("lon", type=float)
    speed = request.values.get("speed", type=float, default=0)
    waktu = request.values.get("time", "").strip()

    if not armada_id or lat is None or lon is None:
        return jsonify({"status": "error", "message": "armada_id, lat, lon wajib diisi"}), 400

    if not waktu:
        waktu = datetime.utcnow().isoformat()

    db = get_db()

    db.execute(
        "INSERT INTO log_posisi (armada_id, lat, lon, kecepatan, waktu) VALUES (?, ?, ?, ?, ?)",
        (armada_id, lat, lon, speed, waktu),
    )

    row = db.execute("SELECT * FROM armada WHERE armada_id = ?", (armada_id,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO armada (armada_id, nama, last_lat, last_lon, last_update) VALUES (?, ?, ?, ?, ?)",
            (armada_id, armada_id, lat, lon, waktu),
        )
        last_zone_id = None
    else:
        last_zone_id = row["last_zone_id"]
        db.execute(
            "UPDATE armada SET last_lat=?, last_lon=?, last_update=? WHERE armada_id=?",
            (lat, lon, waktu, armada_id),
        )

    current_zone_id, current_zone_name = find_current_zone(db, lat, lon)

    event = None
    if current_zone_id != last_zone_id:
        if current_zone_id is not None:
            db.execute(
                "INSERT INTO event_zona (armada_id, zona_id, jenis_event, waktu) VALUES (?, ?, 'masuk', ?)",
                (armada_id, current_zone_id, waktu),
            )
            event = "masuk " + current_zone_name
        elif last_zone_id is not None:
            db.execute(
                "INSERT INTO event_zona (armada_id, zona_id, jenis_event, waktu) VALUES (?, ?, 'keluar', ?)",
                (armada_id, last_zone_id, waktu),
            )
            event = "keluar zona"

        db.execute(
            "UPDATE armada SET last_zone_id=? WHERE armada_id=?",
            (current_zone_id, armada_id),
        )

    db.commit()

    return jsonify({
        "status": "ok",
        "armada_id": armada_id,
        "zona_sekarang": current_zone_name,
        "event": event,
    })


# ---------- Routes: data buat dashboard ----------

@app.route("/api/armada")
@login_required
def api_armada():
    db = get_db()
    if current_user.role == "supir" and current_user.armada_id:
        rows = db.execute(
            "SELECT * FROM armada WHERE armada_id = ?", (current_user.armada_id,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM armada").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/zona")
@login_required
def api_zona():
    db = get_db()
    rows = db.execute("SELECT * FROM zona").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/history/<armada_id>")
@login_required
def api_history(armada_id):
    if current_user.role == "supir" and current_user.armada_id != armada_id:
        return jsonify({"status": "error", "message": "Tidak punya akses ke armada ini"}), 403

    db = get_db()
    rows = db.execute(
        "SELECT lat, lon, kecepatan, waktu FROM log_posisi WHERE armada_id=? ORDER BY id DESC LIMIT 200",
        (armada_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events")
@login_required
def api_events():
    db = get_db()
    if current_user.role == "supir" and current_user.armada_id:
        rows = db.execute(
            """SELECT e.*, z.nama as nama_zona FROM event_zona e
               JOIN zona z ON e.zona_id = z.zona_id
               WHERE e.armada_id = ?
               ORDER BY e.id DESC LIMIT 50""",
            (current_user.armada_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT e.*, z.nama as nama_zona FROM event_zona e
               JOIN zona z ON e.zona_id = z.zona_id
               ORDER BY e.id DESC LIMIT 50"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------- Dashboard ----------

@app.route("/")
@login_required
def dashboard():
    return render_template_string(DASHBOARD_HTML, username=current_user.username, role=current_user.role)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Tracker Dashboard</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<style>
  :root {
    --sidebar-w: 220px;
    --navbar-h: 56px;
    --bg-main: #1C1A17;
    --bg-panel: #252220;
    --bg-panel-2: #2D2A25;
    --border-c: #423D36;
    --text-main: #F3EFE6;
    --text-dim: #B8AFA1;
    --accent: #FF6A1A;
  }
  body { margin:0; font-family: -apple-system, "Segoe UI", sans-serif; background:var(--bg-main); color:var(--text-main); }

  /* ---------- NAVBAR ATAS ---------- */
  /* GANTI BAGIAN INI kalau mau pakai markup navbar dari template Laravel lu */
  .topnav {
    height: var(--navbar-h);
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border-c);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 1000;
  }
  .topnav .brand { font-weight: 600; font-size: 16px; color: var(--text-main); display:flex; align-items:center; gap:8px; }
  .topnav .brand i { color: var(--accent); }
  .topnav .status-pill { font-size: 12px; color: var(--text-dim); }

  /* ---------- SIDEBAR ---------- */
  /* GANTI BAGIAN INI kalau mau pakai markup sidebar dari template Laravel lu */
  .sidebar {
    width: var(--sidebar-w);
    position: fixed;
    top: var(--navbar-h);
    bottom: 0;
    left: 0;
    background: var(--bg-panel);
    border-right: 1px solid var(--border-c);
    padding: 16px 0;
    overflow-y: auto;
  }
  .sidebar .nav-link {
    color: var(--text-dim);
    padding: 10px 20px;
    font-size: 14px;
    display: flex;
    align-items: center;
    gap: 10px;
    border-left: 3px solid transparent;
    cursor: default;
  }
  .sidebar .nav-link.active {
    color: var(--text-main);
    background: var(--bg-panel-2);
    border-left-color: var(--accent);
  }
  .sidebar .nav-section-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-dim);
    padding: 14px 20px 6px;
  }

  /* ---------- MAIN CONTENT ---------- */
  .main-content {
    margin-left: var(--sidebar-w);
    margin-top: var(--navbar-h);
    padding: 20px;
  }
  .panel {
    background: var(--bg-panel);
    border: 1px solid var(--border-c);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 20px;
  }
  .panel-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    margin-bottom: 12px;
  }
  #map { height: 420px; width: 100%; border-radius: 6px; }

  table { width:100%; border-collapse: collapse; font-size: 13px; color: var(--text-main); }
  th, td { text-align:left; padding: 8px 10px; border-bottom: 1px solid var(--border-c); }
  th { color:var(--text-dim); font-weight:500; text-transform:uppercase; font-size:11px; }
  .badge-zone { padding:2px 8px; border-radius:4px; font-size:11px; }
  .in-zone { background: rgba(107,182,137,0.15); color:#6BB689; }
  .out-zone { background: rgba(138,130,118,0.15); color:#8A8276; }

  @media (max-width: 768px) {
    .sidebar { display:none; }
    .main-content { margin-left: 0; }
  }
</style>
</head>
<body>

<div class="topnav">
  <div class="brand"><i class="bi bi-truck"></i> Fleet Tracker</div>
  <div style="display:flex; align-items:center; gap:16px;">
    <div class="status-pill" id="lastRefresh">Memuat...</div>
    <div style="font-size:13px; color:#B8AFA1;">
      {{ username }} <span style="background:#2D2A25; padding:2px 8px; border-radius:4px; font-size:11px; margin-left:4px;">{{ role }}</span>
    </div>
    <a href="/logout" style="color:#B8AFA1; font-size:13px;"><i class="bi bi-box-arrow-right"></i> Logout</a>
  </div>
</div>

<div class="sidebar">
  <div class="nav-section-title">Menu</div>
  <div class="nav-link active"><i class="bi bi-map"></i> Peta &amp; Armada</div>
  <div class="nav-link"><i class="bi bi-geo-alt"></i> Zona Geofence</div>
  <div class="nav-link"><i class="bi bi-clock-history"></i> Riwayat Event</div>
  {% if role == 'admin' %}
  <div class="nav-section-title">Admin</div>
  <a href="/users" class="nav-link" style="text-decoration:none;"><i class="bi bi-people"></i> Kelola User</a>
  {% endif %}
</div>

<div class="main-content">

  <div class="panel">
    <div class="panel-title">Peta live armada</div>
    <div id="map"></div>
  </div>

  <div class="panel">
    <div class="panel-title">Status armada</div>
    <table id="armadaTable"><thead><tr><th>Armada</th><th>Zona</th><th>Update terakhir</th></tr></thead><tbody></tbody></table>
  </div>

  <div class="panel">
    <div class="panel-title">Event geofence terbaru</div>
    <table id="eventTable"><thead><tr><th>Armada</th><th>Event</th><th>Waktu</th></tr></thead><tbody></tbody></table>
  </div>

</div>

<script>
const map = L.map('map').setView([-6.9, 107.3], 9);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

const markers = {};
const zoneCircles = [];

async function loadZones() {
  const res = await fetch('/api/zona');
  const zones = await res.json();
  zoneCircles.forEach(c => map.removeLayer(c));
  zones.forEach(z => {
    const c = L.circle([z.lat, z.lon], { radius: z.radius_meter, color: '#FF6A1A', weight: 1, fillOpacity: 0.08 })
      .addTo(map).bindPopup(z.nama);
    zoneCircles.push(c);
  });
}

async function loadArmada() {
  const res = await fetch('/api/armada');
  const list = await res.json();
  const tbody = document.querySelector('#armadaTable tbody');
  tbody.innerHTML = '';
  list.forEach(a => {
    if (a.last_lat && a.last_lon) {
      const pos = [a.last_lat, a.last_lon];
      if (markers[a.armada_id]) {
        markers[a.armada_id].setLatLng(pos);
      } else {
        markers[a.armada_id] = L.marker(pos).addTo(map).bindPopup(a.armada_id);
      }
    }
    const zoneLabel = a.last_zone_id
      ? '<span class="badge-zone in-zone">' + a.last_zone_id + '</span>'
      : '<span class="badge-zone out-zone">luar zona</span>';
    tbody.innerHTML += '<tr><td>' + a.armada_id + '</td><td>' + zoneLabel + '</td><td>' + (a.last_update || '-') + '</td></tr>';
  });
}

async function loadEvents() {
  const res = await fetch('/api/events');
  const list = await res.json();
  const tbody = document.querySelector('#eventTable tbody');
  tbody.innerHTML = '';
  list.forEach(e => {
    tbody.innerHTML += '<tr><td>' + e.armada_id + '</td><td>' + e.jenis_event + ' ' + e.nama_zona + '</td><td>' + e.waktu + '</td></tr>';
  });
}

function refreshAll() {
  loadArmada();
  loadEvents();
  document.getElementById('lastRefresh').textContent = 'Update terakhir: ' + new Date().toLocaleTimeString('id-ID');
}

loadZones();
refreshAll();
setInterval(refreshAll, 5000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
else:
    # Saat dijalankan lewat gunicorn (production di Railway), init_db tetap harus jalan
    init_db()