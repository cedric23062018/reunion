import sqlite3
import uuid
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "meetings.db")
JSON_FILE = os.path.join(BASE_DIR, "meetings_backup.json")
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

def save_json_backup():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description, created_at FROM meetings")
        meetings = cursor.fetchall()
        cursor.execute("SELECT id, meeting_id, date_str, start_time, end_time FROM slots")
        slots = cursor.fetchall()
        cursor.execute("SELECT id, meeting_id, slot_id, participant_name, status FROM responses")
        responses = cursor.fetchall()
        conn.close()
        
        data = {"meetings": meetings, "slots": slots, "responses": responses}
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Backup error:", e)

def load_json_backup():
    if not os.path.exists(JSON_FILE):
        return
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        for m in data.get("meetings", []):
            cursor.execute("INSERT OR IGNORE INTO meetings (id, title, description, created_at) VALUES (?, ?, ?, ?)", (m[0], m[1], m[2], m[3]))
        for s in data.get("slots", []):
            cursor.execute("INSERT OR IGNORE INTO slots (id, meeting_id, date_str, start_time, end_time) VALUES (?, ?, ?, ?, ?)", (s[0], s[1], s[2], s[3], s[4]))
        for r in data.get("responses", []):
            cursor.execute("INSERT OR IGNORE INTO responses (id, meeting_id, slot_id, participant_name, status) VALUES (?, ?, ?, ?, ?)", (r[0], r[1], r[2], r[3], r[4]))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Restore error:", e)

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
    load_json_backup()

init_db()

app = FastAPI(title="Planificateur de Réunions")

class SlotCreate(BaseModel):
    date: str
    startTime: str
    endTime: str

class MeetingCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    slots: List[SlotCreate]

class VoteItem(BaseModel):
    slotId: int
    status: str

class ParticipantResponseCreate(BaseModel):
    participantName: str
    votes: List[VoteItem]

class MeetingRestore(BaseModel):
    id: str
    title: str
    description: Optional[str] = ""
    slots: List[SlotCreate]

@app.post("/api/meetings/restore")
def restore_meeting(meeting: MeetingRestore):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO meetings (id, title, description) VALUES (?, ?, ?)",
                   (meeting.id, meeting.title, meeting.description))
    
    cursor.execute("SELECT COUNT(*) FROM slots WHERE meeting_id = ?", (meeting.id,))
    if cursor.fetchone()[0] == 0:
        for s in meeting.slots:
            cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (?, ?, ?, ?)",
                           (meeting.id, s.date, s.startTime, s.endTime))
    
    conn.commit()
    conn.close()
    save_json_backup()
    return {"status": "success", "id": meeting.id}

@app.post("/api/meetings")
def create_meeting(meeting: MeetingCreate):
    meeting_id = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (id, title, description) VALUES (?, ?, ?)",
                   (meeting_id, meeting.title, meeting.description))
    
    for s in meeting.slots:
        cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (?, ?, ?, ?)",
                       (meeting_id, s.date, s.startTime, s.endTime))
    
    conn.commit()
    conn.close()
    save_json_backup()
    return {"id": meeting_id, "url": f"/meeting/{meeting_id}"}

@app.post("/api/meetings/{meeting_id}/slots")
def add_slot(meeting_id: str, slot: SlotCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Réunion non trouvée")

    cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (?, ?, ?, ?)",
                   (meeting_id, slot.date, slot.startTime, slot.endTime))
    slot_id = cursor.lastrowid
    conn.commit()
    conn.close()
    save_json_backup()
    return {"status": "success", "slotId": slot_id}

@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, title, description, created_at FROM meetings WHERE id = ?", (meeting_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Réunion non trouvée")
    
    meeting = {"id": row[0], "title": row[1], "description": row[2], "created_at": row[3]}
    
    cursor.execute("SELECT id, date_str, start_time, end_time FROM slots WHERE meeting_id = ? ORDER BY date_str, start_time", (meeting_id,))
    slots = [{"id": r[0], "date": r[1], "startTime": r[2], "endTime": r[3]} for r in cursor.fetchall()]
    meeting["slots"] = slots
    
    # Get all participants
    cursor.execute("SELECT DISTINCT participant_name FROM responses WHERE meeting_id = ?", (meeting_id,))
    participants = [r[0] for r in cursor.fetchall()]
    meeting["participants"] = participants
    
    # Get all responses
    cursor.execute("SELECT slot_id, participant_name, status FROM responses WHERE meeting_id = ?", (meeting_id,))
    responses_raw = cursor.fetchall()
    
    # Structure responses matrix: { participant: { slot_id: status } }
    matrix = {}
    for p in participants:
        matrix[p] = {}
        for s in slots:
            matrix[p][s["id"]] = "unavailable" # default fallback
            
    for r in responses_raw:
        slot_id, p_name, status = r[0], r[1], r[2]
        if p_name in matrix:
            matrix[p_name][slot_id] = status
            
    meeting["matrix"] = matrix
    
    # ALGORITHM: Calculate score for each slot
    # Score formula: available = 2 pts, maybe = 1 pt, unavailable = 0 pt
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
    
    # Rank slots by score (desc), then availableCount (desc)
    slot_stats.sort(key=lambda x: (x["score"], x["availableCount"]), reverse=True)
    meeting["rankedSlots"] = slot_stats
    meeting["bestSlot"] = slot_stats[0] if slot_stats else None
    
    conn.close()
    return meeting

@app.post("/api/meetings/{meeting_id}/responses")
def submit_response(meeting_id: str, payload: ParticipantResponseCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM meetings WHERE id = ?", (meeting_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Réunion non trouvée")
    
    name = payload.participantName.strip()
    if not name:
        conn.close()
        raise HTTPException(status_code=400, detail="Le nom du participant est requis")
        
    for vote in payload.votes:
        cursor.execute("""
        INSERT INTO responses (meeting_id, slot_id, participant_name, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(meeting_id, slot_id, participant_name) 
        DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP
        """, (meeting_id, vote.slotId, name, vote.status))
        
    conn.commit()
    conn.close()
    save_json_backup()
    return {"status": "success"}

import socket

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@app.get("/api/info")
def get_info():
    return {"local_ip": get_local_ip(), "port": 8000}

def get_index_path():
    path1 = os.path.join(BASE_DIR, "static", "index.html")
    path2 = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(path1):
        return path1
    if os.path.exists(path2):
        return path2
    return path1

# Serve frontend HTML
@app.get("/", response_class=HTMLResponse)
def index():
    path = get_index_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail=f"Fichier non trouve: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/meeting/{meeting_id}", response_class=HTMLResponse)
def meeting_page(meeting_id: str):
    path = get_index_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail=f"Fichier non trouve: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
