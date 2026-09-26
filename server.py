import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Récupération de l'URL PostgreSQL fournie par Render
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise Exception("La variable d'environnement DATABASE_URL est manquante.")
    # On ouvre une connexion vers la base PostgreSQL distante de Render
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Adaptation de la syntaxe SQL de SQLite vers PostgreSQL
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id VARCHAR(50) PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS slots (
        id SERIAL PRIMARY KEY,
        meeting_id VARCHAR(50) NOT NULL,
        date_str TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        FOREIGN KEY (meeting_id) REFERENCES meetings (id) ON DELETE CASCADE
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS responses (
        id SERIAL PRIMARY KEY,
        meeting_id VARCHAR(50) NOT NULL,
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
    cursor.close()
    conn.close()

# Initialisation des tables à l'allumage du serveur
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (id, title, description) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                   (meeting.id, meeting.title, meeting.description))
    
    cursor.execute("SELECT COUNT(*) FROM slots WHERE meeting_id = %s", (meeting.id,))
    if cursor.fetchone()[0] == 0:
        for s in meeting.slots:
            cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (%s, %s, %s, %s)",
                           (meeting.id, s.date, s.startTime, s.endTime))
    
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success", "id": meeting.id}

@app.post("/api/meetings")
def create_meeting(meeting: MeetingCreate):
    meeting_id = str(uuid.uuid4())[:8]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (id, title, description) VALUES (%s, %s, %s)",
                   (meeting_id, meeting.title, meeting.description))
    
    for s in meeting.slots:
        cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (%s, %s, %s, %s)",
                       (meeting_id, s.date, s.startTime, s.endTime))
    
    conn.commit()
    cursor.close()
    conn.close()
    return {"id": meeting_id, "url": f"/meeting/{meeting_id}"}

@app.post("/api/meetings/{meeting_id}/slots")
def add_slot(meeting_id: str, slot: SlotCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM meetings WHERE id = %s", (meeting_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Réunion non trouvée")

    cursor.execute("INSERT INTO slots (meeting_id, date_str, start_time, end_time) VALUES (%s, %s, %s, %s) RETURNING id",
                   (meeting_id, slot.date, slot.startTime, slot.endTime))
    slot_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success", "slotId": slot_id}

@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, title, description, created_at FROM meetings WHERE id = %s", (meeting_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Réunion non trouvée")
    
    meeting = {"id": row[0], "title": row[1], "description": row[2], "created_at": str(row[3])}
    
    cursor.execute("SELECT id, date_str, start_time, end_time FROM slots WHERE meeting_id = %s ORDER BY date_str, start_time", (meeting_id,))
    slots = [{"id": r[0], "date": r[1], "startTime": r[2], "endTime": r[3]} for r in cursor.fetchall()]
    meeting["slots"] = slots
    
    cursor.execute("SELECT DISTINCT participant_name FROM responses WHERE meeting_id = %s", (meeting_id,))
    participants = [r[0] for r in cursor.fetchall()]
    meeting["participants"] = participants
    
    cursor.execute("SELECT slot_id, participant_name, status FROM responses WHERE meeting_id = %s", (meeting_id,))
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
    
    cursor.close()
    conn.close()
    return meeting

@app.post("/api/meetings/{meeting_id}/responses")
def submit_response(meeting_id: str, payload: ParticipantResponseCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM meetings WHERE id = %s", (meeting_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Réunion non trouvée")
    
    name = payload.participantName.strip()
    if not name:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Le nom du participant est requis")
        
    for vote in payload.votes:
        cursor.execute("""
        INSERT INTO responses (meeting_id, slot_id, participant_name, status)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(meeting_id, slot_id, participant_name) 
        DO UPDATE SET status=EXCLUDED.status, updated_at=CURRENT_TIMESTAMP
        """, (meeting_id, vote.slotId, name, vote.status))
        
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

def get_index_path():
    path1 = os.path.join(BASE_DIR, "static", "index.html")
    path2 = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(path1):
        return path1
    if os.path.exists(path2):
        return path2
    return path1

@app.get("/", response_class=HTMLResponse)
def index():
    path = get_index_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail=f"Fichier non trouve: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/meeting/{meeting_id}", response_class=HTMLResponse)
@app.get("/m/{meeting_id}", response_class=HTMLResponse)
def meeting_page(meeting_id: str):
    path = get_index_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail=f"Fichier non trouve: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
