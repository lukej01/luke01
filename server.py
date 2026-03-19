from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Form, Header
from fastapi.responses import FileResponse, HTMLResponse
from dotenv import load_dotenv
import httpx
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import aiofiles

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create uploads directory and games directory
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

GAMES_DIR = ROOT_DIR / "games"
GAMES_DIR.mkdir(exist_ok=True)

# Admin password for uploads
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Furman04!F')

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Console type mappings
CONSOLE_EXTENSIONS = {
    "nes": [".nes"],
    "snes": [".smc", ".sfc"],
    "ps1": [".bin", ".iso", ".cue", ".img"],
    "gba": [".gba"],
    "gb": [".gb", ".gbc"],
    "n64": [".n64", ".z64", ".v64"],
    "genesis": [".md", ".gen", ".bin"],
    "nds": [".nds"],
}

def detect_console(filename: str) -> str:
    """Detect console type from file extension"""
    ext = Path(filename).suffix.lower()
    for console, extensions in CONSOLE_EXTENSIONS.items():
        if ext in extensions:
            return console
    return "unknown"

# Define Models
class Game(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    console: str
    filename: Optional[str] = None
    file_path: Optional[str] = None
    game_url: Optional[str] = None  # External game URL
    game_file: Optional[str] = None  # Local HTML game file
    image_url: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class GameCreate(BaseModel):
    name: str
    console: Optional[str] = None
    image_url: Optional[str] = None
    game_url: Optional[str] = None

class GameUpdate(BaseModel):
    name: Optional[str] = None
    image_url: Optional[str] = None
    game_url: Optional[str] = None

class SaveState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    game_id: str
    state_data: str
    slot: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SaveStateCreate(BaseModel):
    game_id: str
    state_data: str
    slot: int = 1

class PasswordVerify(BaseModel):
    password: str

# Routes
@api_router.get("/")
async def root():
    return {"message": "Retro Arcade API"}

# Password verification endpoint
@api_router.post("/verify-password")
async def verify_password(data: PasswordVerify):
    """Verify admin password for uploads"""
    if data.password == ADMIN_PASSWORD:
        return {"valid": True}
    return {"valid": False}

# Game endpoints
@api_router.get("/games", response_model=List[Game])
async def get_games(console: Optional[str] = None, search: Optional[str] = None):
    """Get all games with optional filtering"""
    query = {}
    if console and console != "all":
        query["console"] = console
    if search:
        query["name"] = {"$regex": search, "$options": "i"}
    
    # Sort by created_at to preserve insertion order
    games = await db.games.find(query, {"_id": 0}).sort("created_at", 1).to_list(1000)
    
    for game in games:
        if isinstance(game.get('created_at'), str):
            game['created_at'] = datetime.fromisoformat(game['created_at'])
    
    return games

@api_router.get("/games/{game_id}", response_model=Game)
async def get_game(game_id: str):
    """Get a specific game by ID"""
    game = await db.games.find_one({"id": game_id}, {"_id": 0})
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if isinstance(game.get('created_at'), str):
        game['created_at'] = datetime.fromisoformat(game['created_at'])
    
    return game

@api_router.post("/games", response_model=Game)
async def create_game(
    file: UploadFile = File(None),
    name: str = Form(...),
    console: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    game_url: Optional[str] = Form(None),
    password: str = Form(...)
):
    """Upload a new game ROM or add external game link"""
    # Verify password
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password")
    
    file_path_str = None
    filename = None
    detected_console = console or "html5"
    
    if file and file.filename:
        # Detect console type if not provided
        detected_console = console or detect_console(file.filename)
        
        # Generate unique filename
        file_ext = Path(file.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = UPLOAD_DIR / unique_filename
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        file_path_str = str(unique_filename)
        filename = file.filename
    
    # Create game document
    game = Game(
        name=name,
        console=detected_console,
        filename=filename,
        file_path=file_path_str,
        game_url=game_url,
        image_url=image_url
    )
    
    doc = game.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.games.insert_one(doc)
    
    return game

@api_router.post("/games/add-external", response_model=Game)
async def add_external_game(
    name: str = Form(...),
    console: str = Form(...),
    game_url: str = Form(...),
    image_url: Optional[str] = Form(None),
    password: str = Form(...)
):
    """Add an external game (HTML file link)"""
    # Verify password
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password")
    
    game = Game(
        name=name,
        console=console,
        game_url=game_url,
        image_url=image_url
    )
    
    doc = game.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.games.insert_one(doc)
    
    return game

@api_router.put("/games/{game_id}", response_model=Game)
async def update_game(game_id: str, update: GameUpdate):
    """Update game details"""
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided")
    
    result = await db.games.update_one(
        {"id": game_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Game not found")
    
    game = await db.games.find_one({"id": game_id}, {"_id": 0})
    if isinstance(game.get('created_at'), str):
        game['created_at'] = datetime.fromisoformat(game['created_at'])
    
    return game

@api_router.delete("/games/{game_id}")
async def delete_game(game_id: str, password: str = None):
    """Delete a game"""
    # For now, allow deletion without password for easier management
    game = await db.games.find_one({"id": game_id}, {"_id": 0})
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Delete file if exists
    if game.get('file_path'):
        file_path = UPLOAD_DIR / game['file_path']
        if file_path.exists():
            file_path.unlink()
    
    await db.games.delete_one({"id": game_id})
    
    return {"message": "Game deleted successfully"}

@api_router.get("/games/{game_id}/rom")
async def get_game_rom(game_id: str):
    """Get the ROM file for a game"""
    game = await db.games.find_one({"id": game_id}, {"_id": 0})
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if not game.get('file_path'):
        raise HTTPException(status_code=404, detail="This game has no ROM file")
    
    file_path = UPLOAD_DIR / game['file_path']
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="ROM file not found")
    
    return FileResponse(
        path=file_path,
        filename=game.get('filename', 'game.rom'),
        media_type='application/octet-stream'
    )

# Save state endpoints
@api_router.get("/games/{game_id}/saves", response_model=List[SaveState])
async def get_save_states(game_id: str):
    """Get all save states for a game"""
    saves = await db.save_states.find(
        {"game_id": game_id}, 
        {"_id": 0}
    ).sort("slot", 1).to_list(100)
    
    for save in saves:
        for field in ['created_at', 'updated_at']:
            if isinstance(save.get(field), str):
                save[field] = datetime.fromisoformat(save[field])
    
    return saves

@api_router.post("/saves", response_model=SaveState)
async def create_save_state(save_data: SaveStateCreate):
    """Create or update a save state"""
    existing = await db.save_states.find_one({
        "game_id": save_data.game_id,
        "slot": save_data.slot
    })
    
    now = datetime.now(timezone.utc)
    
    if existing:
        await db.save_states.update_one(
            {"game_id": save_data.game_id, "slot": save_data.slot},
            {"$set": {
                "state_data": save_data.state_data,
                "updated_at": now.isoformat()
            }}
        )
        save = await db.save_states.find_one({
            "game_id": save_data.game_id,
            "slot": save_data.slot
        }, {"_id": 0})
    else:
        save = SaveState(
            game_id=save_data.game_id,
            state_data=save_data.state_data,
            slot=save_data.slot
        )
        doc = save.model_dump()
        doc['created_at'] = doc['created_at'].isoformat()
        doc['updated_at'] = doc['updated_at'].isoformat()
        await db.save_states.insert_one(doc)
        save = doc
    
    for field in ['created_at', 'updated_at']:
        if isinstance(save.get(field), str):
            save[field] = datetime.fromisoformat(save[field])
    
    return save

@api_router.delete("/saves/{save_id}")
async def delete_save_state(save_id: str):
    """Delete a save state"""
    result = await db.save_states.delete_one({"id": save_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Save state not found")
    return {"message": "Save state deleted"}

# Get available consoles
@api_router.get("/consoles")
async def get_consoles():
    """Get list of available console types"""
    return {
        "consoles": [
            {"id": "nes", "name": "NES", "extensions": [".nes"]},
            {"id": "snes", "name": "SNES", "extensions": [".smc", ".sfc"]},
            {"id": "ps1", "name": "PlayStation", "extensions": [".bin", ".iso", ".cue", ".img"]},
            {"id": "gba", "name": "Game Boy Advance", "extensions": [".gba"]},
            {"id": "gb", "name": "Game Boy", "extensions": [".gb", ".gbc"]},
            {"id": "n64", "name": "Nintendo 64", "extensions": [".n64", ".z64", ".v64"]},
            {"id": "nds", "name": "Nintendo DS", "extensions": [".nds"]},
            {"id": "genesis", "name": "Sega Genesis", "extensions": [".md", ".gen", ".bin"]},
            {"id": "html5", "name": "HTML5 Games", "extensions": [".html"]},
        ]
    }

# Download and cache HTML game from Google Drive
async def download_gdrive_file(file_id: str, game_id: str) -> str:
    """Download HTML file from Google Drive and cache it locally"""
    cache_path = GAMES_DIR / f"{game_id}.html"
    
    # If already cached, return path
    if cache_path.exists():
        return str(cache_path)
    
    # Download from Google Drive
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        response = await client.get(download_url)
        if response.status_code == 200:
            async with aiofiles.open(cache_path, 'wb') as f:
                await f.write(response.content)
            return str(cache_path)
    
    return None

# Serve cached HTML game
@api_router.get("/games/{game_id}/play")
async def play_game(game_id: str):
    """Serve the HTML game file for playing in browser"""
    game = await db.games.find_one({"id": game_id}, {"_id": 0})
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Check if we have a local HTML file for this game
    game_file = game.get('game_file')
    if game_file:
        file_path = GAMES_DIR / game_file
        if file_path.exists():
            # Read and return HTML content directly - don't trigger download
            async with aiofiles.open(file_path, 'r') as f:
                html_content = await f.read()
            return HTMLResponse(content=html_content, media_type='text/html')
    
    raise HTTPException(status_code=404, detail="Game file not found")

# Seed initial games - Using local HTML game files
@api_router.post("/seed-games")
async def seed_games(password: str = Form(...)):
    """Seed the database with initial games using local HTML files"""
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password")
    
    # Check if games already exist
    existing_count = await db.games.count_documents({})
    if existing_count > 0:
        return {"message": f"Database already has {existing_count} games", "seeded": False}
    
    # Games ordered by user preference with proper cover images
    initial_games = [
        {
            "name": "Super Mario 64",
            "console": "n64",
            "game_file": "super_mario_64.html",
            "image_url": "https://www.mobygames.com/images/covers/l/3124-super-mario-64-nintendo-64-front-cover.jpg"
        },
        {
            "name": "Gran Turismo 2",
            "console": "ps1",
            "game_file": "gran_turismo_2.html",
            "image_url": "https://www.mobygames.com/images/covers/l/8089-gran-turismo-2-playstation-front-cover.jpg"
        },
        {
            "name": "Lego Batman 2: DC Super Heroes",
            "console": "nds",
            "game_file": "lego_batman_2.html",
            "image_url": "https://www.mobygames.com/images/covers/l/248029-lego-batman-2-dc-super-heroes-nintendo-ds-front-cover.jpg"
        },
        {
            "name": "The Legend of Zelda: Ocarina of Time",
            "console": "n64",
            "game_file": "zelda_ocarina.html",
            "image_url": "https://www.mobygames.com/images/covers/l/15836-the-legend-of-zelda-ocarina-of-time-nintendo-64-front-cover.jpg"
        },
        {
            "name": "NBA Live 2003",
            "console": "gba",
            "game_file": "nba_live_2003.html",
            "image_url": "https://www.mobygames.com/images/covers/l/26989-nba-live-2003-game-boy-advance-front-cover.jpg"
        },
        {
            "name": "Madden NFL 2000",
            "console": "n64",
            "game_file": "madden_2000.html",
            "image_url": "https://www.mobygames.com/images/covers/l/58108-madden-nfl-2000-nintendo-64-front-cover.jpg"
        },
        {
            "name": "Minecraft",
            "console": "html5",
            "game_file": "minecraft.html",
            "image_url": "https://www.mobygames.com/images/covers/l/303023-minecraft-xbox-one-front-cover.jpg"
        },
        {
            "name": "Pokemon Emerald",
            "console": "gba",
            "game_file": "pokemon_emerald.html",
            "image_url": "https://www.mobygames.com/images/covers/l/50044-pokemon-emerald-version-game-boy-advance-front-cover.jpg"
        },
        {
            "name": "Mario Kart 64",
            "console": "n64",
            "game_file": "mario_kart_64.html",
            "image_url": "https://www.mobygames.com/images/covers/l/58063-mario-kart-64-nintendo-64-front-cover.jpg"
        },
        {
            "name": "Sonic the Hedgehog 2",
            "console": "genesis",
            "game_file": "sonic_2.html",
            "image_url": "https://www.mobygames.com/images/covers/l/6186-sonic-the-hedgehog-2-genesis-front-cover.jpg"
        },
        {
            "name": "Super Smash Bros",
            "console": "n64",
            "game_file": "smash_bros.html",
            "image_url": "https://www.mobygames.com/images/covers/l/58101-super-smash-bros-nintendo-64-front-cover.jpg"
        },
        {
            "name": "GoldenEye 007",
            "console": "n64",
            "game_file": "goldeneye.html",
            "image_url": "https://www.mobygames.com/images/covers/l/4657-goldeneye-007-nintendo-64-front-cover.jpg"
        }
    ]
    
    for game_data in initial_games:
        game = Game(
            name=game_data["name"],
            console=game_data["console"],
            game_file=game_data["game_file"],
            image_url=game_data["image_url"]
        )
        doc = game.model_dump()
        doc['created_at'] = doc['created_at'].isoformat()
        await db.games.insert_one(doc)
    
    return {"message": f"Seeded {len(initial_games)} games", "seeded": True}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
