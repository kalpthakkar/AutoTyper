# receiver.py

import threading
import queue
import time
import string
import re
import math
from collections import Counter
import random
import pyautogui
import pyperclip
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from typing import Literal, List, Dict

broadcast_queue = asyncio.Queue()
typing_queue = queue.Queue()
pause_event = threading.Event()
typing_state: Literal["idle", "preparing", "typing", "paused", "completed"] = "idle"
current_line: int = 0
total_lines: int = 0
original_map: Dict[int, List[str]] = {} # Hold original token maps
normalized_map: Dict[int, List[str]] = {} # Hold normalized token maps
randomize_flag: bool = True
enable_tab_sync: bool = False
code_mode_interrupted: bool = False # Intrupt during ongoing line execution
normalization_interrupted: bool = False # Intrupt during ongoing line execution
tab = None
tab_map: Dict[int, int] = {} # line_number, tab_count
auto_pause_after_line: bool = False
normalize_lines: bool = False

typing_delay_min = 0.5
typing_delay_max = 1.5

# Keep track of connected WebSocket clients
connected_status_sockets: list[WebSocket] = []

class Command(BaseModel):
    action: str
    data: str | int | None = None

def detect_indent_unit(code_str: str) -> str:
    """
    Detects the indentation unit used in a code string.
    Returns:
        '\t'           if tabs are used
        ' ' * n        if spaces are used (n = detected indent size)
        ''             if no indentation is found
    """
    lines = code_str.splitlines()

    leading_whitespace = []
    for line in lines:
        if line.strip():  # ignore empty / whitespace-only lines
            ws = line[:len(line) - len(line.lstrip())]
            if ws:
                leading_whitespace.append(ws)

    if not leading_whitespace:
        return ""

    # If any line uses tabs, assume tabs are the indentation unit
    if any('\t' in ws for ws in leading_whitespace):
        return '\t'

    # Otherwise, count spaces
    space_counts = [
        len(ws) for ws in leading_whitespace if ws.strip() == ""
    ]

    if not space_counts:
        return ""

    # Use GCD of indentation levels to infer unit
    indent_size = space_counts[0]
    for count in space_counts[1:]:
        indent_size = math.gcd(indent_size, count)

    # Fallback: most common indentation size
    if indent_size == 0:
        indent_size = Counter(space_counts).most_common(1)[0][0]

    return " " * indent_size

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

def build_tokens_from_current_line():
    
    source_map = normalized_map if (normalize_lines or enable_tab_sync) else original_map

    all_tokens = []
    for line_no in range(current_line, total_lines + 1):
        all_tokens.extend(source_map.get(line_no, []))

    return all_tokens

def set_typing_queue_from_current_line():
    with typing_queue.mutex:
        typing_queue.queue.clear()

    global code_mode_interrupted, normalization_interrupted
    code_mode_interrupted = False
    normalization_interrupted = False

    tokens = build_tokens_from_current_line()

    for token in tokens:
        typing_queue.put(token)

    _broadcast_status()
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

def on_new_line(line_no):
    if line_no % 10 == 0:
        time.sleep(random.uniform(0.05, 0.15))  # thinking pause

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

def init_strip_newline():

    # Copy inline text
    def copy_left() -> str:
        pyautogui.hotkey('ctrl', 'shift', 'left')
        time.sleep(random.uniform(0.05, 0.15))
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(random.uniform(0.05, 0.15))
        return pyperclip.paste()
    
    copied_padding_text = copy_left()

    if copied_padding_text == '': # Nothing before (Caret on 1st line without text)
        return # Good to start
    elif (copied_padding_text == '\n') or (copied_padding_text.endswith('\n')): # Went above line
        pyautogui.press('right') 
        return # Good to start
    elif copied_padding_text.strip() == '': # Contains tabs and spaces (indent)
        pyautogui.press('backspace') # Strip indent
        return # Good to start
    else: # Something exists before
        pyautogui.press('right') 
        time.sleep(random.uniform(0.05, 0.15))
        pyautogui.press('enter') # Go to new line
        time.sleep(random.uniform(0.05, 0.15))
        init_strip_newline() # Re-fix over clean line

def create_line_below():
    pyautogui.press('enter')
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.press('up')
    time.sleep(random.uniform(0.05, 0.15))

def sync_tab():
    global tab

    if not tab_map: # Prevents failure if tab_map was not set during 'START'. Cannot enable 'Code Mode' in ongoing session.
        return

    # Identify tab alignment and update tab count in `tab_memory`
    pyautogui.hotkey('ctrl', 'shift', 'left')
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(random.uniform(0.05, 0.15))
    pyautogui.press('right')
    copied_padding_text = pyperclip.paste()

    # Detect indentation while processing
    if tab is None: # Tab initialization
        if copied_padding_text and all(c in [' ', '\t'] for c in copied_padding_text):
            tab = copied_padding_text

    if tab:
        desired_tab_count = tab_map.get(current_line, 0)
        
        if copied_padding_text.replace(tab, "") == "": # All tabs exactly aligned.
            tab_count = copied_padding_text.count(tab)
        else: # Warning! Some misalignment (trying best possible fix by matching initial valid tab count)
            tab_count = (len(copied_padding_text) - len(copied_padding_text.lstrip(tab))) // len(tab)
        
        if desired_tab_count-tab_count > 0: # Add tabs
            for _ in range(abs(desired_tab_count-tab_count)):
                pyautogui.press('tab')
                time.sleep(random.uniform(0.05, 0.15))
        elif desired_tab_count-tab_count < 0: # Remove tabs
            for _ in range(abs(desired_tab_count-tab_count)):
                pyautogui.press('backspace')
                time.sleep(random.uniform(0.05, 0.15))

def remove_trailing_chars():

    def shift_right():
        pyautogui.hotkey('ctrl', 'shift', 'right')
        time.sleep(random.uniform(0.05, 0.15))

    def copy_right() -> str:
        shift_right()
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(random.uniform(0.05, 0.15))
        return pyperclip.paste()
    
    delete_span = 0
    prev_copy = None
    while True:
        text_at_right = copy_right()
        if prev_copy == text_at_right:
            pyautogui.press('left')
            break
        if text_at_right == '':
            break
        elif '\n' in text_at_right:
            pyautogui.press('left')
            break
        prev_copy = text_at_right
        delete_span += 1
    
    if delete_span:
        for _ in range(delete_span):
            shift_right()
        pyautogui.press('backspace')        
        time.sleep(random.uniform(0.05, 0.15))

def _broadcast_typing_completed():
    import json
    msg = json.dumps({
        "type": "typing_completed",
        "data": {
            "total_lines": total_lines
        }
    })
    try:
        broadcast_queue.put_nowait(msg)
    except:
        pass

def typing_worker():
    global current_line, tab

    while True:
        if typing_state != 'typing' or pause_event.is_set():
            time.sleep(0.1)
            continue

        try:
            item = typing_queue.get(timeout=0.1)
        except queue.Empty:
            if not pause_event.is_set():
                # DOUBLE CHECK to avoid race
                if typing_queue.empty():
                    _broadcast_typing_completed()
                    set_typing_state("completed")
            continue

        if item == "STOP":
            break

        if isinstance(item, str):
            # Handle special control characters
            if item == "\n":

                if enable_tab_sync:
                    remove_trailing_chars()

                pyautogui.press("enter")
                current_line += 1
                if current_line > total_lines:
                    current_line = total_lines
                _broadcast_status()
                on_new_line(current_line)

                if code_mode_interrupted: # Code mode interupted during ongoing previous line execution. Sync from current line.
                    if enable_tab_sync:
                        # Go to new line. Strip indentation to 0 (complete left).
                        init_strip_newline()
                        # Create a newline below (required behavior to prevent inline copy in some IDE).
                        create_line_below()
                        # If upcoming line contains indentation(s)
                        if tab_map.get(current_line, 0) > 0: # Record tab identifier, and fix the indentation.
                            pyautogui.press('tab')
                            time.sleep(random.uniform(0.05, 0.15))
                            pyautogui.hotkey('ctrl', 'shift', 'left')
                            time.sleep(random.uniform(0.05, 0.15))
                            pyautogui.hotkey('ctrl', 'c')
                            time.sleep(random.uniform(0.05, 0.15))
                            pyautogui.press('right')
                            tab = pyperclip.paste()
                            time.sleep(random.uniform(0.05, 0.15))
                            for _ in range(tab_map.get(current_line, 0)-1):
                                pyautogui.press('tab')
                                time.sleep(random.uniform(0.05, 0.15))
                    set_typing_queue_from_current_line()
                if normalization_interrupted: # Normalization interupted during ongoing previous line execution. Sync from current line.
                    set_typing_queue_from_current_line()

                if enable_tab_sync and current_line <= total_lines:
                    sync_tab()

                if auto_pause_after_line:
                    pause_event.set()
                    set_typing_state("paused")

                _broadcast_status()
                continue

            elif item == "\t":
                if enable_tab_sync:
                    sync_tab()
                else:
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
                        time.sleep(random.uniform(8.0, 18.0))
                    elif choice == 'medium_pause':
                        # print("Pause 5 to 10 seconds")
                        time.sleep(random.uniform(4.0, 8.0))
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

def set_typing_state(state: Literal["idle", "preparing", "typing", "paused", "completed"]):
    global typing_state
    if typing_state != state:
        # print("STATE set to:", state)
        typing_state = state
        _broadcast_status()

def _get_status_dict():
    return {
        "typing_state": typing_state,
        "paused": pause_event.is_set(),
        "randomize": randomize_flag,
        "enable_tab_sync": enable_tab_sync,
        "auto_pause_after_line": auto_pause_after_line,
        "normalize": normalize_lines,
        "queue_size": typing_queue.qsize(),
        "speed_min": typing_delay_min,
        "speed_max": typing_delay_max,
        "current_line": current_line,
        "total_lines": total_lines,
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
    global current_line, total_lines, original_map, normalized_map, randomize_flag, auto_pause_after_line, normalize_lines, enable_tab_sync, code_mode_interrupted, normalization_interrupted, tab_map

    # For toggles, we will broadcast status after the state change
    if cmd.action == "type":
        if not cmd.data or not isinstance(cmd.data, str):
            raise HTTPException(status_code=400, detail="Data must be a string for 'type'")
        
        set_typing_state("preparing")

        pause_event.clear()
        text = cmd.data

        lines = text.splitlines(keepends=True)

        total_lines = len(lines)
        current_line = 1

        # Build tab_map -> line_number, tab_count
        tab_map.clear()
        indent_unit: str = detect_indent_unit(text)
        for line_number, line_text in enumerate(lines, start=1):
            line_text = line_text.rstrip()
            tab_map[line_number] = (len(line_text) - len(line_text.lstrip(indent_unit))) // len(indent_unit) if indent_unit else 0

        original_map = {}
        normalized_map = {}
        for i, line in enumerate(lines, start=1):
            original_map[i] = split_text_to_tokens(line)
            match = re.match(r'^([ \t]*)(.*?)(\r?\n)?$', line)
            if match:
                _, content, newline = match.groups()
                normalized_line = content + (newline or "")
                normalized_map[i] = split_text_to_tokens(normalized_line)

        tokens = set_typing_queue_from_current_line()

        if enable_tab_sync:
            init_strip_newline()
            create_line_below()

        set_typing_state("typing")
        return {"status": "typing started", "tokens": tokens}

    elif cmd.action == "pause":
        pause_event.set()
        set_typing_state("paused")

    elif cmd.action == "resume":
        pause_event.clear()
        set_typing_state("typing")

    elif cmd.action == "toggle_random":
        randomize_flag = not randomize_flag
        # broadcast
        _broadcast_status()
        return {"randomize": randomize_flag}

    elif cmd.action == "toggle_code_mode":
        enable_tab_sync = not enable_tab_sync
        code_mode_interrupted = True
        _broadcast_status()
        return {"enable_tab_sync": enable_tab_sync}
    
    elif cmd.action == "toggle_auto_pause":
        auto_pause_after_line = not auto_pause_after_line
        _broadcast_status()
        return {"auto_pause_after_line": auto_pause_after_line}
    
    elif cmd.action == "toggle_normalize":
        normalize_lines = not normalize_lines
        normalization_interrupted = True
        _broadcast_status()
        return {"normalize": normalize_lines}

    elif cmd.action == "stop":
        with typing_queue.mutex:
            typing_queue.queue.clear()
        current_line = 0
        total_lines = 0
        pause_event.set()
        set_typing_state("idle")
        # _broadcast_status()
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