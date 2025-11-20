import os
from datetime import datetime, timedelta
from typing import List, Optional, Literal
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

app = FastAPI(title="Flames.Blue Warehouse API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic request/response models for endpoints
class LoadIn(BaseModel):
    code: str
    category: str
    date: datetime
    quantity: float = Field(ge=0)
    side: Literal["sinistra", "centro", "destra"]
    section: str
    level: Literal["sopra", "sotto"]
    notes: Optional[str] = None
    expiry_date: Optional[datetime] = None

class MoveRequest(BaseModel):
    load_id: str
    side: Literal["sinistra", "centro", "destra"]
    section: str
    level: Literal["sopra", "sotto"]
    notes: Optional[str] = None

class OutRequest(BaseModel):
    load_id: str
    notes: Optional[str] = None

class MapSectionLevel(BaseModel):
    level: Literal["sopra", "sotto"]

class MapSection(BaseModel):
    side: Literal["sinistra", "centro", "destra"]
    section: str
    levels: List[MapSectionLevel] = Field(default_factory=list)

class WarehouseMapIn(BaseModel):
    name: str = "Default Map"
    sections: List[MapSection] = Field(default_factory=list)
    active: bool = True

# Helpers

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="ID non valido")


def ensure_active_map():
    existing = list(db["warehousemap"].find({"active": True})) if db else []
    if not existing and db:
        # create a default map with three sections and two levels each
        default_map = {
            "name": "Default Map",
            "sections": [
                {"side": "sinistra", "section": "S1", "levels": [{"level": "sopra"}, {"level": "sotto"}]},
                {"side": "centro", "section": "C1", "levels": [{"level": "sopra"}, {"level": "sotto"}]},
                {"side": "destra", "section": "D1", "levels": [{"level": "sopra"}, {"level": "sotto"}]},
            ],
            "active": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        db["warehousemap"].insert_one(default_map)


# API routes
@app.get("/")
def root():
    ensure_active_map()
    return {"message": "Warehouse API attivo"}


# Schema endpoint for Flames viewer (optional)
@app.get("/schema")
def get_schema():
    return {"collections": ["load", "movement", "warehousemap"]}


# 1. Gestione carichi: registrare carichi in entrata
@app.post("/loads")
def create_load(payload: LoadIn):
    ensure_active_map()
    data = payload.model_dump()
    data.update({
        "status": "present",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })

    # Insert load
    inserted_id = db["load"].insert_one(data).inserted_id

    # Movement record
    movement = {
        "load_id": str(inserted_id),
        "type": "in",
        "timestamp": datetime.utcnow(),
        "from_position": None,
        "to_position": {
            "side": payload.side,
            "section": payload.section,
            "level": payload.level,
        },
        "notes": payload.notes,
        "performed_by": "system",
    }
    db["movement"].insert_one(movement)

    return {"id": str(inserted_id)}


# Elenco carichi, con filtri e priorità
@app.get("/loads")
def list_loads(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    ensure_active_map()
    q = {}
    if status:
        q["status"] = status
    if search:
        q["$or"] = [
            {"code": {"$regex": search, "$options": "i"}},
            {"category": {"$regex": search, "$options": "i"}},
            {"section": {"$regex": search, "$options": "i"}},
        ]
    items = list(db["load"].find(q).sort([
        ("level", 1),  # "sopra" before "sotto" lexicographically if we invert; we'll map manually
    ]))

    # compute priority: sopra first, then by date asc, then by expiry
    def prio_key(doc):
        lvl = 0 if doc.get("level") == "sopra" else 1
        expiry = doc.get("expiry_date") or datetime(2999, 1, 1)
        return (lvl, doc.get("date", datetime.utcnow()), expiry)

    items.sort(key=prio_key)

    for it in items:
        it["id"] = str(it["_id"]) ; del it["_id"]

    return {"items": items}


# 2. Posizionamento dinamico: spostare carichi
@app.post("/loads/move")
def move_load(payload: MoveRequest):
    ensure_active_map()
    _id = oid(payload.load_id)
    load = db["load"].find_one({"_id": _id})
    if not load:
        raise HTTPException(404, "Carico non trovato")
    if load.get("status") != "present":
        raise HTTPException(400, "Il carico non è disponibile per lo spostamento")

    db["load"].update_one(
        {"_id": _id},
        {"$set": {
            "side": payload.side,
            "section": payload.section,
            "level": payload.level,
            "updated_at": datetime.utcnow(),
        }}
    )

    movement = {
        "load_id": payload.load_id,
        "type": "move",
        "timestamp": datetime.utcnow(),
        "from_position": {
            "side": load.get("side"),
            "section": load.get("section"),
            "level": load.get("level"),
        },
        "to_position": {
            "side": payload.side,
            "section": payload.section,
            "level": payload.level,
        },
        "notes": payload.notes,
        "performed_by": "system",
    }
    db["movement"].insert_one(movement)

    return {"ok": True}


# 3. Uscita carico con priorità "sopra" prima
@app.post("/loads/out")
def out_load(payload: OutRequest):
    ensure_active_map()
    _id = oid(payload.load_id)
    load = db["load"].find_one({"_id": _id})
    if not load:
        raise HTTPException(404, "Carico non trovato")

    db["load"].update_one({"_id": _id}, {"$set": {"status": "out", "updated_at": datetime.utcnow()}})

    movement = {
        "load_id": payload.load_id,
        "type": "out",
        "timestamp": datetime.utcnow(),
        "from_position": {
            "side": load.get("side"),
            "section": load.get("section"),
            "level": load.get("level"),
        },
        "to_position": None,
        "notes": payload.notes,
        "performed_by": "system",
    }
    db["movement"].insert_one(movement)

    return {"ok": True}


# 4. Dashboard: riepiloghi
@app.get("/dashboard")
def dashboard():
    ensure_active_map()
    present_count = db["load"].count_documents({"status": "present"})
    out_count = db["load"].count_documents({"status": "out"})

    # Priorità prelievo: tutti i present, sopra prima, poi per data-entrata
    present_items = list(db["load"].find({"status": "present"}))
    def prio_key(doc):
        lvl = 0 if doc.get("level") == "sopra" else 1
        expiry = doc.get("expiry_date") or datetime(2999, 1, 1)
        return (lvl, doc.get("date", datetime.utcnow()), expiry)
    present_items.sort(key=prio_key)
    pick_priority = [
        {
            "id": str(d["_id"]),
            "code": d.get("code"),
            "section": d.get("section"),
            "side": d.get("side"),
            "level": d.get("level"),
            "quantity": d.get("quantity"),
        }
        for d in present_items[:20]
    ]

    # Movimenti recenti
    recent_moves = list(db["movement"].find().sort([("timestamp", -1)]).limit(20))
    for m in recent_moves:
        m["id"] = str(m["_id"]) ; del m["_id"]

    # Avvisi scadenze/spostamenti: scadenza entro 7 giorni, o livello sotto ma con out imminente
    now = datetime.utcnow()
    soon = now + timedelta(days=7)
    expiring = list(db["load"].find({
        "status": "present",
        "expiry_date": {"$lte": soon}
    }))
    alerts = [
        {
            "type": "expiry",
            "message": f"Carico {e.get('code')} in scadenza",
            "id": str(e["_id"]),
        }
        for e in expiring
    ]

    return {
        "present": present_count,
        "out": out_count,
        "pick_priority": pick_priority,
        "recent_moves": recent_moves,
        "alerts": alerts,
    }


# 5. Mappa magazzino flessibile
@app.get("/map")
def get_map():
    ensure_active_map()
    m = db["warehousemap"].find_one({"active": True})
    if not m:
        raise HTTPException(404, "Mappa non trovata")
    m["id"] = str(m["_id"]) ; del m["_id"]
    return m


@app.post("/map")
def set_map(payload: WarehouseMapIn):
    # deactivate others and upsert active map
    db["warehousemap"].update_many({}, {"$set": {"active": False}})
    doc = payload.model_dump()
    doc.update({"active": True, "updated_at": datetime.utcnow(), "created_at": datetime.utcnow()})
    res = db["warehousemap"].insert_one(doc)
    return {"id": str(res.inserted_id)}


# Ricerca avanzata
@app.get("/search")
def search(q: Optional[str] = Query(None), side: Optional[str] = None, level: Optional[str] = None, section: Optional[str] = None, status: Optional[str] = None):
    ensure_active_map()
    flt = {}
    if q:
        flt["$or"] = [
            {"code": {"$regex": q, "$options": "i"}},
            {"category": {"$regex": q, "$options": "i"}},
            {"notes": {"$regex": q, "$options": "i"}},
        ]
    if side:
        flt["side"] = side
    if level:
        flt["level"] = level
    if section:
        flt["section"] = section
    if status:
        flt["status"] = status
    results = list(db["load"].find(flt).limit(100))
    for r in results:
        r["id"] = str(r["_id"]) ; del r["_id"]
    return {"items": results}


# Test endpoint remains
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
            response["connection_status"] = "Connected"
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
