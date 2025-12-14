# receiver.py

import threading
import queue
import time
import string
import re
import random
import pyautogui
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import asyncio
from contextlib import asynccontextmanager

broadcast_queue = asyncio.Queue()
typing_queue = queue.Queue()
pause_event = threading.Event()
randomize_flag = True
auto_pause_after_line = False
normalize_lines = False

typing_delay_min = 0.5
typing_delay_max = 1.5

# Keep track of connected WebSocket clients
connected_status_sockets: list[WebSocket] = []

class Command(BaseModel):
    action: str
    data: str | int | None = None

def split_text_to_tokens(text: str) -> list[str]:
    """
    Tokenize text preserving:
    - Newlines
    - Tabs
    - Multiple spaces
    - Punctuation (including underscores)
    - Words
    """
    token_pattern = r'(\r\n|\n|\t| +|[^a-zA-Z0-9\s]|[a-zA-Z0-9]+)'
    tokens = re.findall(token_pattern, text)
    return tokens

def random_typo_char(correct_char: str) -> str:
    """Return a random nearby char to simulate a typo."""

    # Neighbor keys based on US QWERTY layout (simplified)
    qwerty_neighbors = {
        # Letters
        'a': ['q', 'w', 's', 'z'],
        'b': ['v', 'g', 'h', 'n'],
        'c': ['x', 'd', 'f', 'v'],
        'd': ['s', 'e', 'r', 'f', 'c', 'x'],
        'e': ['w', 's', 'd', 'r'],
        'f': ['d', 'r', 't', 'g', 'v', 'c'],
        'g': ['f', 't', 'y', 'h', 'b', 'v'],
        'h': ['g', 'y', 'u', 'j', 'n', 'b'],
        'i': ['u', 'j', 'k', 'o'],
        'j': ['h', 'u', 'i', 'k', 'm', 'n'],
        'k': ['j', 'i', 'o', 'l', 'm'],
        'l': ['k', 'o', 'p'],
        'm': ['n', 'j', 'k'],
        'n': ['b', 'h', 'j', 'm'],
        'o': ['i', 'k', 'l', 'p'],
        'p': ['o', 'l'],
        'q': ['a', 's', 'w'],
        'r': ['e', 'd', 'f', 't'],
        's': ['a', 'q', 'w', 'e', 'd', 'x', 'z'],
        't': ['r', 'f', 'g', 'y'],
        'u': ['y', 'h', 'j', 'i'],
        'v': ['c', 'f', 'g', 'b'],
        'w': ['q', 'a', 's', 'e'],
        'x': ['z', 's', 'd', 'c'],
        'y': ['t', 'g', 'h', 'u'],
        'z': ['a', 's', 'x'],

        # Digits (top row neighbors)
        '1': ['2', 'q'],
        '2': ['1', '3', 'q', 'w'],
        '3': ['2', '4', 'w', 'e'],
        '4': ['3', '5', 'e', 'r'],
        '5': ['4', '6', 'r', 't'],
        '6': ['5', '7', 't', 'y'],
        '7': ['6', '8', 'y', 'u'],
        '8': ['7', '9', 'u', 'i'],
        '9': ['8', '0', 'i', 'o'],
        '0': ['9', 'p', '-'],

        # Punctuation (simplified)
        '-': ['0', '=', '_'],
        '=': ['-', '[', '+'],
        '[': [']', 'p', '{'],
        ']': ['[', '\\', '}'],
        ';': ['l', "'", ':'],
        "'": [';', '"'],
        ',': ['m', '.', '<'],
        '.': [',', '/', '>'],
        '/': ['.', '?'],
        '\\': [']', '|'],
        '`': ['1', '~'],
    }

    # For alphabet characters
    if correct_char.lower() in qwerty_neighbors:
        replacement = random.choice(qwerty_neighbors[correct_char.lower()])
        return replacement.upper() if correct_char.isupper() else replacement

    # For digits
    elif correct_char in qwerty_neighbors:
        return random.choice(qwerty_neighbors[correct_char])

    # For punctuation
    elif correct_char in string.punctuation:
        neighbors = qwerty_neighbors.get(correct_char)
        if neighbors:
            return random.choice(neighbors)
        else:
            return correct_char  # Fallback: no typo

    # For space or others
    else:
        return correct_char  # No typo applied

def human_delay(char):
    """Simulate human typing delays based on character type."""
    base_delay = random.uniform(0.05, 0.25)  # base delay

    if char in ",;:":
        base_delay += random.uniform(0.2, 0.6)
    elif char in ".!?":
        base_delay += random.uniform(0.3, 0.7)
    elif char == " ":
        base_delay += random.uniform(0.08, 0.16)  # pause on spaces

    # 9% chance for hesitation (extra pause)
    if random.random() < 0.09:
        base_delay += random.uniform(0.3, 1.0)

    time.sleep(base_delay)

def type_word(word: str, allow_typo=True):
    """
    Types a word character by character, simulating up to 2 realistic typos and corrections
    if the word is long enough and allow_typo is True.
    """
    typo_probability = 0.25
    simulate_typo = random.choices([True, False], weights=[typo_probability, 1-typo_probability])[0]
    min_typo_word_len = 3

    if not allow_typo or len(word) < min_typo_word_len or simulate_typo is False:
        # Just type normally
        for ch in word:
            pyautogui.write(ch)
            human_delay(ch)
        return

    # Determine how many typos to simulate
    typo_count = 1
    if len(word) > 8:
        typo_count = random.choice([1, 2])

    # Determine typo positions and lengths (1 or 2 characters)
    typo_spans = []
    attempts = 0
    while len(typo_spans) < typo_count and attempts < 10:
        attempts += 1
        start = random.randint(0, len(word) - 2)
        length = random.choices([1, 2], weights=[0.66, 0.34])[0]
        if start + length <= len(word):
            # Ensure no overlapping typos
            if all(not (start < s + l and start + length > s) for s, l in typo_spans):
                typo_spans.append((start, length))

    i = 0
    while i < len(word):
        # Check if a typo starts at this position
        typo = next(((s, l) for s, l in typo_spans if s == i), None)

        if typo:
            start, length = typo
            original = word[start:start + length]
            typo_text = ''.join(random_typo_char(c) for c in original)

            # Type wrong characters
            pyautogui.write(typo_text)
            for c in typo_text:
                human_delay(c)

            # Small pause to simulate human realization
            time.sleep(random.uniform(0.2, 0.5))

            # Backspace to remove wrong characters
            for _ in range(len(typo_text)):
                pyautogui.press('backspace')
                time.sleep(random.uniform(0.05, 0.15))

            # Re-type correct characters
            pyautogui.write(original)
            for c in original:
                human_delay(c)

            i += length
        else:
            pyautogui.write(word[i])
            human_delay(word[i])
            i += 1

def typing_worker():
    while True:
        if pause_event.is_set():
            time.sleep(0.1)
            continue

        try:
            item = typing_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        if item == "STOP":
            break

        if isinstance(item, str):
            # Handle special control characters
            if item == "\n":
                pyautogui.press("enter")
                if auto_pause_after_line:
                    pause_event.set()
                _broadcast_status()
                continue

            elif item == "\t":
                pyautogui.press("tab")
                _broadcast_status()
                continue

            if randomize_flag:

                # Now item is either a word or punctuation or space
                if item.startswith(" "):
                    # Spaces: type normally with slight delay
                    for _ in item:
                        pyautogui.write(" ")
                        human_delay(" ")
                elif item.isalpha() or item.isdigit():
                    # For words (letters or digits), type with typo chance
                    type_word(item, allow_typo=True)
                    time.sleep(random.uniform(typing_delay_min, typing_delay_max))
                else:
                    # Punctuation or other chars: type normally
                    pyautogui.write(item)
                    human_delay(item)

                if any(c.isalpha() for c in item) and len(item) >= 3:
                    # Random chance to pause/thinking after some tokens
                    options = ['long_pause', 'medium_pause', 'short_pause', 'no_pause']
                    weights = [0.02, 0.05, 0.03, 0.90]  # 2%, 5%, 3%, 90%
                    choice = random.choices(options, weights=weights, k=1)[0]

                    if choice == 'long_pause':
                        # print("Pause 10 to 20 seconds")
                        time.sleep(random.uniform(10.0, 20.0))
                    elif choice == 'medium_pause':
                        # print("Pause 5 to 10 seconds")
                        time.sleep(random.uniform(5.0, 10.0))
                    elif choice == 'short_pause':
                        # print("Pause 0.5 to 2 seconds")
                        time.sleep(random.uniform(0.5, 2.0))
                    else:
                        pass  # No pause

                # Another random tiny hesitation inside word typing is handled by human_delay()
            else:
                # Non-randomized: type each character with fixed delay
                for ch in item:
                    pyautogui.write(ch)
                    time.sleep(0.1)  # fixed small delay

            _broadcast_status()

def _get_status_dict():
    return {
        "paused": pause_event.is_set(),
        "randomize": randomize_flag,
        "auto_pause_after_line": auto_pause_after_line,
        "normalize": normalize_lines,
        "queue_size": typing_queue.qsize(),
        "speed_min": typing_delay_min,
        "speed_max": typing_delay_max,
    }

def _broadcast_status():
    import json
    msg = json.dumps({"type": "status", "data": _get_status_dict()})
    try:
        broadcast_queue.put_nowait(msg)
    except Exception as e:
        print("Failed to queue broadcast:", e)

async def broadcast_loop():
    while True:
        msg = await broadcast_queue.get()
        disconnected = []
        for ws in connected_status_sockets:
            try:
                await ws.send_text(msg)
            except:
                disconnected.append(ws)

        # Remove any disconnected sockets
        for ws in disconnected:
            try:
                connected_status_sockets.remove(ws)
            except:
                pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    pause_event.clear()

    t = threading.Thread(target=typing_worker, daemon=True)
    t.start()

    broadcaster = asyncio.create_task(broadcast_loop())

    yield  # App runs here

    # Shutdown
    typing_queue.put("STOP")
    broadcaster.cancel()

app = FastAPI(lifespan=lifespan)

# CORS (for fetch requests from UI)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # you can restrict
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

'''
-------------------------------------------------------------------------------------
Deprecated Code • Fix Already Applied in `lifespan`
-------------------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    pause_event.clear()

    # Start typing thread (non-async)
    t = threading.Thread(target=typing_worker, daemon=True)
    t.start()

    # Start async broadcaster loop
    asyncio.create_task(broadcast_loop())

@app.on_event("shutdown")
def shutdown_event():
    typing_queue.put("STOP")
'''

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "AutoType Receiver",
        "ws": "/ws/status"
    }

@app.websocket("/ws/status")
async def status_ws_endpoint(ws: WebSocket):
    await ws.accept()
    connected_status_sockets.append(ws)
    try:
        # Send initial status
        init = _get_status_dict()
        await ws.send_json({"type": "status", "data": init})
        while True:
            # Keep connection alive; we don't expect messages from client for now
            # But we need to receive, or else disconnect
            msg = await ws.receive_text()
            # Optionally you can allow client to request status explicitly
            if msg == "get_status":
                await ws.send_json({"type": "status", "data": _get_status_dict()})
    except WebSocketDisconnect:
        # Remove from list
        try:
            connected_status_sockets.remove(ws)
        except ValueError:
            pass
    except Exception as e:
        print("WebSocket error:", e)
        try:
            connected_status_sockets.remove(ws)
        except ValueError:
            pass

@app.post("/set_speed")
async def set_speed_range(data: dict):
    global typing_delay_min, typing_delay_max

    try:
        min_val = float(data.get("min", 0.5))
        max_val = float(data.get("max", 1.5))
        if not (0 < min_val <= max_val):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid speed values")

    typing_delay_min = round(min_val, 1)
    typing_delay_max = round(max_val, 1)

    _broadcast_status()
    return {"min": typing_delay_min, "max": typing_delay_max}

@app.post("/command")
async def receive_command(cmd: Command):
    global randomize_flag, auto_pause_after_line, normalize_lines

    # For toggles, we will broadcast status after the state change
    if cmd.action == "type":
        if not cmd.data or not isinstance(cmd.data, str):
            raise HTTPException(status_code=400, detail="Data must be a string for 'type'")

        pause_event.clear()
        text = cmd.data

        if normalize_lines:
            # Normalize each line by stripping leading spaces/tabs only
            lines = text.splitlines(keepends=True)
            normalized = ""
            for line in lines:
                match = re.match(r'^([ \t]*)(.*?)(\r?\n)?$', line)
                if match:
                    _, content, newline = match.groups()
                    normalized += content + (newline or "")
            text = normalized

        # Now tokenize
        tokens = split_text_to_tokens(text)
        for token in tokens:
            typing_queue.put(token)

        _broadcast_status()
        return {"status": "typing started", "tokens": tokens}


    elif cmd.action == "pause":
        pause_event.set()

    elif cmd.action == "resume":
        pause_event.clear()

    elif cmd.action == "toggle_random":
        randomize_flag = not randomize_flag
        # broadcast
        _broadcast_status()
        return {"randomize": randomize_flag}

    elif cmd.action == "toggle_auto_pause":
        auto_pause_after_line = not auto_pause_after_line
        _broadcast_status()
        return {"auto_pause_after_line": auto_pause_after_line}
    
    elif cmd.action == "toggle_normalize":
        normalize_lines = not normalize_lines
        _broadcast_status()
        return {"normalize": normalize_lines}

    elif cmd.action == "stop":
        with typing_queue.mutex:
            typing_queue.queue.clear()
        pause_event.set()
        _broadcast_status()
        return {"status": "typing stopped"}

    else:
        raise HTTPException(status_code=400, detail="Unknown action")

    _broadcast_status()
    return {"status": "command received"}

@app.get("/status")
def get_status():
    return _get_status_dict()

if __name__ == "__main__":
    uvicorn.run("receiver:app", host="0.0.0.0", port=8000, reload=False)