# sender_web/app.py
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import sys
import uvicorn
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent # sender_web/
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

RECEIVER_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "receiver_url": RECEIVER_URL})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)
