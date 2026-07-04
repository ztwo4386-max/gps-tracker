import sqlite3
import math
from datetime import datetime
from flask import Flask, request, jsonify, g
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = "tracker.db"


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


# ---------- Routes: tracking ----------

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
def api_armada():
    db = get_db()
    rows = db.execute("SELECT * FROM armada").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/zona")
def api_zona():
    db = get_db()
    rows = db.execute("SELECT * FROM zona").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/history/<armada_id>")
def api_history(armada_id):
    db = get_db()
    rows = db.execute(
        "SELECT lat, lon, kecepatan, waktu FROM log_posisi WHERE armada_id=? ORDER BY id DESC LIMIT 200",
        (armada_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/events")
def api_events():
    db = get_db()
    rows = db.execute(
        """SELECT e.*, z.nama as nama_zona FROM event_zona e
           JOIN zona z ON e.zona_id = z.zona_id
           ORDER BY e.id DESC LIMIT 50"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------- Dashboard ----------

@app.route("/")
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Fleet Tracker Dashboard</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<style>
  body { margin:0; font-family: -apple-system, sans-serif; background:#1C1A17; color:#F3EFE6; }
  #map { height: 70vh; width: 100%; }
  #panel { padding: 14px 18px; }
  h2 { font-size: 16px; margin: 0 0 10px; }
  table { width:100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align:left; padding: 6px 8px; border-bottom: 1px solid #423D36; }
  th { color:#8A8276; font-weight:500; text-transform:uppercase; font-size:11px; }
  .badge { padding:2px 8px; border-radius:4px; font-size:11px; }
  .in-zone { background: rgba(107,182,137,0.15); color:#6BB689; }
  .out-zone { background: rgba(138,130,118,0.15); color:#8A8276; }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h2>Status armada</h2>
  <table id="armadaTable"><thead><tr><th>Armada</th><th>Zona</th><th>Update terakhir</th></tr></thead><tbody></tbody></table>
  <h2 style="margin-top:20px;">Event geofence terbaru</h2>
  <table id="eventTable"><thead><tr><th>Armada</th><th>Event</th><th>Waktu</th></tr></thead><tbody></tbody></table>
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
      ? '<span class="badge in-zone">' + a.last_zone_id + '</span>'
      : '<span class="badge out-zone">luar zona</span>';
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
}

loadZones();
refreshAll();
setInterval(refreshAll, 5000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)