import sqlite3
import uuid
import os
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meetings.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id TEXT NOT NULL,
        date_str TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        FOREIGN KEY (meeting_id) REFERENCES meetings (id) ON DELETE CASCADE
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id TEXT NOT NULL,
        slot_id INTEGER NOT NULL,
        participant_name TEXT NOT NULL,
        status TEXT CHECK(status IN ('available', 'maybe', 'unavailable')) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (meeting_id) REFERENCES meetings (id) ON DELETE CASCADE,
        FOREIGN KEY (slot_id) REFERENCES slots (id) ON DELETE CASCADE,
        UNIQUE(meeting_id, slot_id, participant_name)
    )
    """)
    conn.commit()
    conn.close()

init_db()

app = Flask(__name__, static_folder="static", static_url_path="")

@app.route("/api/info", methods=["GET"])
def get_info():
    return jsonify({"local_ip": "127.0.0.1", "port": 8000})

@app.route("/api/meetings", methods=["POST"])
def create_meeting():
    data = request.json
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    slots_data = data.get("slots", [])

    if not title:
        return jsonify({"error": "Titre requis"}), 400

    meeting_id = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (id, title, description) VALUES (?, ?, ?)",
                   (meeting_id, title, description))
    
    for s in slots_data:
        cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (?, ?, ?, ?)",
                       (meeting_id, s["date"], s["startTime"], s["endTime"]))
    
    conn.commit()
    conn.close()
    return jsonify({"id": meeting_id, "url": f"/meeting/{meeting_id}"})

@app.route("/api/meetings/<meeting_id>/slots", methods=["POST"])
def add_slot(meeting_id):
    data = request.json
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Réunion non trouvée"}), 404

    cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (?, ?, ?, ?)",
                   (meeting_id, data["date"], data["startTime"], data["endTime"]))
    slot_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "slotId": slot_id})

@app.route("/api/meetings/<meeting_id>", methods=["GET"])
def get_meeting(meeting_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, title, description, created_at FROM meetings WHERE id = ?", (meeting_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Réunion non trouvée"}), 404
    
    meeting = {"id": row[0], "title": row[1], "description": row[2], "created_at": row[3]}
    
    cursor.execute("SELECT id, date_str, start_time, end_time FROM slots WHERE meeting_id = ? ORDER BY date_str, start_time", (meeting_id,))
    slots = [{"id": r[0], "date": r[1], "startTime": r[2], "endTime": r[3]} for r in cursor.fetchall()]
    meeting["slots"] = slots
    
    cursor.execute("SELECT DISTINCT participant_name FROM responses WHERE meeting_id = ?", (meeting_id,))
    participants = [r[0] for r in cursor.fetchall()]
    meeting["participants"] = participants
    
    cursor.execute("SELECT slot_id, participant_name, status FROM responses WHERE meeting_id = ?", (meeting_id,))
    responses_raw = cursor.fetchall()
    
    matrix = {}
    for p in participants:
        matrix[p] = {}
        for s in slots:
            matrix[p][s["id"]] = "unavailable"
            
    for r in responses_raw:
        slot_id, p_name, status = r[0], r[1], r[2]
        if p_name in matrix:
            matrix[p_name][slot_id] = status
            
    meeting["matrix"] = matrix
    
    slot_stats = []
    total_participants = len(participants)
    
    for s in slots:
        s_id = s["id"]
        avail_cnt = sum(1 for p in participants if matrix[p].get(s_id) == "available")
        maybe_cnt = sum(1 for p in participants if matrix[p].get(s_id) == "maybe")
        unavail_cnt = sum(1 for p in participants if matrix[p].get(s_id) == "unavailable")
        
        score = (avail_cnt * 2) + (maybe_cnt * 1)
        max_possible = (total_participants * 2) if total_participants > 0 else 1
        percentage = round((score / max_possible) * 100) if total_participants > 0 else 0
        
        slot_stats.append({
            "slot": s,
            "availableCount": avail_cnt,
            "maybeCount": maybe_cnt,
            "unavailableCount": unavail_cnt,
            "score": score,
            "percentage": percentage
        })
    
    slot_stats.sort(key=lambda x: (x["score"], x["availableCount"]), reverse=True)
    meeting["rankedSlots"] = slot_stats
    meeting["bestSlot"] = slot_stats[0] if slot_stats else None
    
    conn.close()
    return jsonify(meeting)

@app.route("/api/meetings/<meeting_id>/responses", methods=["POST"])
def submit_response(meeting_id):
    data = request.json
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Réunion non trouvée"}), 404
    
    name = data.get("participantName", "").strip()
    if not name:
        conn.close()
        return jsonify({"error": "Nom requis"}), 400
        
    for vote in data.get("votes", []):
        cursor.execute("""
        INSERT INTO responses (meeting_id, slot_id, participant_name, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(meeting_id, slot_id, participant_name) 
        DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP
        """, (meeting_id, vote["slotId"], name, vote["status"]))
        
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/meeting/<path:path>")
def meeting_page(path):
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    app.run(debug=True)
