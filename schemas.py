"""
Database Schemas for Warehouse Management

Each Pydantic model corresponds to a MongoDB collection (lowercased class name):
- Load -> "load"
- Movement -> "movement"
- WarehouseMap -> "warehousemap"
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

# Core domain schemas

class Load(BaseModel):
    """
    Carichi (loads) collection schema
    - code: codice del carico
    - category: categoria merce
    - date: data di ingresso (ISO string or datetime)
    - quantity: quantità
    - side: lato di posizionamento (sinistra/centro/destra)
    - section: sezione o coordinata (es. H1, C2, S3)
    - level: livello "sopra" o "sotto"
    - notes: note opzionali
    - status: "present" per in magazzino, "out" per in uscita/uscito
    - expiry_date: data di scadenza opzionale per avvisi
    """
    code: str = Field(..., description="Codice")
    category: str = Field(..., description="Categoria")
    date: datetime = Field(..., description="Data")
    quantity: float = Field(..., ge=0, description="Quantità")
    side: Literal["sinistra", "centro", "destra"] = Field(..., description="Posizione laterale")
    section: str = Field(..., description="Sezione/Coordinata")
    level: Literal["sopra", "sotto"] = Field(..., description="Livello")
    notes: Optional[str] = Field(None, description="Note")
    status: Literal["present", "out"] = Field("present", description="Stato del carico")
    expiry_date: Optional[datetime] = Field(None, description="Scadenza opzionale")

class Movement(BaseModel):
    """
    Storico movimenti dei carichi
    - type: "in", "move", "out"
    - from_position / to_position: dizionari con side/section/level
    - performed_by: utente o sistema
    """
    load_id: str = Field(..., description="ID del carico")
    type: Literal["in", "move", "out"]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    from_position: Optional[dict] = None
    to_position: Optional[dict] = None
    notes: Optional[str] = None
    performed_by: Optional[str] = Field("system")

class PositionLevel(BaseModel):
    level: Literal["sopra", "sotto"]

class Position(BaseModel):
    side: Literal["sinistra", "centro", "destra"]
    section: str
    levels: List[PositionLevel] = Field(default_factory=list)

class WarehouseMap(BaseModel):
    """
    Mappa del magazzino dinamica e modificabile
    - sections: elenco di sezioni/coordinate configurabili
    """
    name: str = Field("Default Map")
    sections: List[Position] = Field(default_factory=list)
    active: bool = Field(True)

# Example legacy schemas kept for reference (not used by the app directly)
class User(BaseModel):
    name: str
    email: str
    address: str
    age: Optional[int] = None
    is_active: bool = True

class Product(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    category: str
    in_stock: bool = True
