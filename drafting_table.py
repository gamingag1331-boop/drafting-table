#!/usr/bin/env python3
"""Drafting Table: a desktop drafting surface.

Write notes, draw freehand, draw lines and arrows, highlight, and place images.
Free transform (stretch and turn), zoom, right-click menus, undo/redo,
cut/copy/paste/duplicate, save/open .drafting.json files, and Check for updates.

Needs Python 3 with tkinter (included with the python.org installers).
Pillow is optional. It enables JPEG/WebP/PNG images and pasting images from the clipboard:
    pip install pillow
"""
import base64
import io
import json
import math
import os
import shutil
import sys
import time
import tkinter as tk
import urllib.request
from tkinter import colorchooser, filedialog, messagebox, simpledialog

try:
    from PIL import Image, ImageTk
    try:
        from PIL import ImageGrab
    except Exception:
        ImageGrab = None
    HAS_PIL = True
except Exception:
    Image = ImageTk = ImageGrab = None
    HAS_PIL = False

APP_VERSION = "1.8"
UPDATE_BASE = "https://raw.githubusercontent.com/gamingag1331-boop/drafting-table/main/"
APP_FILE = os.path.abspath(__file__)
BACKUP_DIR = os.path.join(os.path.dirname(APP_FILE), "backups")

BOARD_W, BOARD_H = 3000, 2200
GRID_STEP = 40
ZOOM_MIN, ZOOM_MAX = 0.25, 4.0
HANDLE = 8        # size of a transform handle, in screen pixels
ROT_OFFSET = 28   # distance of the turn handle above the box, in screen pixels
MIN_SIZE = 10     # smallest size a transform can make, in board units
HIGHLIGHT = "#fff07a"
ACCENT = "#2f5bd3"
COLORS = [("Ink", "#1b2b30"), ("Red", "#c8402d"), ("Blue", "#2f5bd3"), ("Green", "#2f8f5b")]
FILLS = [("None", None), ("Yellow", "#fff3a3"), ("Pink", "#ffd6c9"), ("Blue", "#cfe8ff"), ("Green", "#d5f5dc")]
BOARD_DEFAULT = "#eef2f0"
OLD_BOARD_NAMES = {"Paper": "#eef2f0", "White": "#ffffff", "Kraft": "#efe3cc",
                   "Sage": "#e1ebe1", "Sky": "#e3eef8"}
FONT_SIZE = 16
STROKE_TYPES = ("stroke", "highlight")
LINE_TYPES = ("line", "arrow")
TOOL_TO_TYPE = {"pen": "stroke", "highlight": "highlight", "line": "line", "arrow": "arrow"}
TOOL_WIDTH = {"stroke": 3, "highlight": 22, "line": 2.5, "arrow": 2.5}


def grid_for(hexv):
    """A slightly darker shade of the board colour, used for the grid lines."""
    r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(v * 0.9) for v in (r, g, b))


def load_board(value):
    """Accepts a hex colour, or an old preset name from version 1.2 files."""
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            pass
    return OLD_BOARD_NAMES.get(value, BOARD_DEFAULT)


def fetch_update_file(name):
    """Download one file from the project on GitHub. Only https addresses under UPDATE_BASE are used."""
    url = UPDATE_BASE + name
    if not url.startswith("https://raw.githubusercontent.com/"):
        raise ValueError("update address is not allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "DraftingTable"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def version_tuple(v):
    return tuple(int(p) for p in str(v).split("."))


def dist_seg(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    qx, qy = ax + t * dx, ay + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def rotate_pts(pts, center, deg):
    """Turn points clockwise (screen coordinates) around center by deg degrees."""
    cx, cy = center
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return [(cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c) for px, py in pts]


def rotated_corners(x, y, w, h, deg):
    cx, cy = x + w / 2, y + h / 2
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + dx * c - dy * s, cy + dx * s + dy * c))
    return out


def text_line_count(it):
    """How many lines a note takes, counting wrapping when the note has a set width."""
    lines = it["text"].split("\n")
    w = it.get("w")
    if w:
        cpl = max(1, int(w / 9))
        return sum(max(1, -(-len(line) // cpl)) for line in lines)
    return len(lines)


class DraftingTable:
    def __init__(self, root):
        self.root = root
        self.items = []
        self.next_id = 1
        self.undo_stack = []
        self.redo_stack = []
        self.clip = []
        self.paste_count = 0
        self.sel = set()
        self.tool = "select"
        self.color = COLORS[0][1]
        self.fill = None
        self.zoom = 1.0
        self.right_spot = (80, 80)
        self.board = BOARD_DEFAULT
        self.grid_color = grid_for(BOARD_DEFAULT)
        self.drag = None
        self.path = None
        self.dirty = False
        self.status_msg = ""
        if not HAS_PIL:
            self.status_msg = "Pillow not installed: images are disabled. Run: pip install pillow"
        self.build_ui()
        self.bind_keys()
        self.build_menus()
        self.items.append({"id": self.new_id(), "type": "text", "x": 80, "y": 60,
                           "text": "Drafting table.\nPick a tool, or paste an image with Ctrl+V.",
                           "color": COLORS[0][1], "pinned": False})
        self.set_tool("select")
        self.set_color(self.color)
        self.set_board(BOARD_DEFAULT, mark=False)
        self.redraw()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def build_ui(self):
        self.root.title("Drafting Table")
        self.root.geometry("1200x780")
        self.root.minsize(640, 420)

        bar = tk.Frame(self.root, padx=8, pady=6)
        bar.pack(side=tk.TOP, fill=tk.X)
        self.tool_buttons = {}
        for label, key, name in [("Select", "V", "select"), ("Pen", "P", "pen"), ("Line", "L", "line"),
                                 ("Arrow", "A", "arrow"), ("Highlight", "H", "highlight"), ("Note", "T", "note")]:
            b = tk.Button(bar, text=f"{label} ({key})", command=lambda n=name: self.set_tool(n))
            b.pack(side=tk.LEFT, padx=2)
            self.tool_buttons[name] = b
        tk.Frame(bar, width=14).pack(side=tk.LEFT)
        tk.Label(bar, text="Color:").pack(side=tk.LEFT)
        self.color_buttons = {}
        for cname, hexv in COLORS:
            b = tk.Button(bar, text=cname, bg=hexv, fg="white", activebackground=hexv,
                          activeforeground="white", width=6, command=lambda c=hexv: self.set_color(c))
            b.pack(side=tk.LEFT, padx=2)
            self.color_buttons[hexv] = b
        tk.Frame(bar, width=14).pack(side=tk.LEFT)
        tk.Label(bar, text="Note fill:").pack(side=tk.LEFT)
        self.fill_buttons = {}
        for fname, fhex in FILLS:
            b = tk.Button(bar, text=fname, bg=fhex or "#ffffff", width=7, command=lambda c=fhex: self.set_fill(c))
            b.pack(side=tk.LEFT, padx=2)
            self.fill_buttons[fhex] = b
        tk.Frame(bar, width=14).pack(side=tk.LEFT)
        tk.Button(bar, text="Board colour...", command=self.pick_board).pack(side=tk.LEFT, padx=2)

        bar2 = tk.Frame(self.root, padx=8)
        bar2.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        actions = [("Undo", self.undo), ("Redo", self.redo), ("Cut", self.cut), ("Copy", self.copy),
                   ("Paste", self.paste), ("Duplicate", self.duplicate), ("Delete", self.delete_selected),
                   ("Pin / Unpin", self.toggle_pin), ("Add image", self.add_image_dialog),
                   ("Open", self.open_file), ("Save", self.save), ("Clear", self.clear_board),
                   ("Check for updates", self.check_updates)]
        for text, cmd in actions:
            tk.Button(bar2, text=text, command=cmd).pack(side=tk.LEFT, padx=2)

        frame = tk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, bg=BOARD_DEFAULT, highlightthickness=0,
                                scrollregion=(0, 0, BOARD_W, BOARD_H))
        vs = tk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
        hs = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        bottom = tk.Frame(self.root)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        zoomf = tk.Frame(bottom, padx=8)
        zoomf.pack(side=tk.RIGHT)
        tk.Button(zoomf, text="Zoom out", command=self.zoom_out).pack(side=tk.LEFT, padx=2)
        self.zoom_label = tk.Label(zoomf, width=6, text="100%")
        self.zoom_label.pack(side=tk.LEFT)
        tk.Button(zoomf, text="Zoom in", command=self.zoom_in).pack(side=tk.LEFT, padx=2)
        tk.Button(zoomf, text="100%", command=self.zoom_reset).pack(side=tk.LEFT, padx=2)
        self.status = tk.Label(bottom, anchor="w", padx=8, pady=4)
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", lambda e: self.on_wheel(e, 1))
        c.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        # Right-click: Mac uses Button-2 in Tk, Windows and Linux use Button-3
        c.bind("<Button-2>" if sys.platform == "darwin" else "<Button-3>", self.on_right_click)

    def bind_keys(self):
        r = self.root
        ctrl = {"z": self.undo, "Z": self.redo, "y": self.redo, "x": self.cut, "c": self.copy,
                "v": self.paste, "d": self.duplicate, "s": self.save, "o": self.open_file,
                "i": self.add_image_dialog, "k": self.toggle_pin, "a": self.select_all}
        for key, fn in ctrl.items():
            r.bind(f"<Control-{key}>", lambda e, f=fn: f())
        plain = {"v": "select", "p": "pen", "l": "line", "a": "arrow", "h": "highlight", "t": "note"}
        for key, name in plain.items():
            r.bind(f"<KeyPress-{key}>", lambda e, n=name: self.set_tool(n))
        r.bind("<Delete>", lambda e: self.delete_selected())
        r.bind("<BackSpace>", lambda e: self.delete_selected())
        r.bind("<Escape>", lambda e: (self.set_tool("select"), self.select_none()))
        r.bind("<Control-equal>", lambda e: self.zoom_in())
        r.bind("<Control-plus>", lambda e: self.zoom_in())
        r.bind("<Control-minus>", lambda e: self.zoom_out())
        r.bind("<Control-0>", lambda e: self.zoom_reset())
        r.bind("<Control-bracketright>", lambda e: self.layer_move("forward"))
        r.bind("<Control-bracketleft>", lambda e: self.layer_move("backward"))

    def build_menus(self):
        self.lock_var = tk.BooleanVar(self.root, value=True)
        self.board_menu = tk.Menu(self.root, tearoff=0)
        self.board_menu.add_command(label="Paste", accelerator="Ctrl+V",
                                    command=lambda: self.paste(self.right_spot))
        self.board_menu.add_command(label="Add note here", command=lambda: self.new_note(*self.right_spot))
        self.board_menu.add_command(label="Add image...", command=self.add_image_dialog)
        self.board_menu.add_separator()
        self.board_menu.add_command(label="Select all", accelerator="Ctrl+A", command=self.select_all)

        self.item_menu = tk.Menu(self.root, tearoff=0)
        self.item_menu.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy)
        self.item_menu.add_command(label="Cut", accelerator="Ctrl+X", command=self.cut)
        self.item_menu.add_command(label="Duplicate", accelerator="Ctrl+D", command=self.duplicate)
        self.item_menu.add_command(label="Delete", accelerator="Delete", command=self.delete_selected)
        self.item_menu.add_separator()
        self.item_menu.add_command(label="Pin / Unpin", accelerator="Ctrl+K", command=self.toggle_pin)
        self.item_menu.add_command(label="Edit text...", command=self.edit_selected_text)
        self.item_menu.add_separator()
        self.item_menu.add_command(label="Bring to front", command=lambda: self.layer_move("front"))
        self.item_menu.add_command(label="Bring forward", accelerator="Ctrl+]",
                                   command=lambda: self.layer_move("forward"))
        self.item_menu.add_command(label="Send backward", accelerator="Ctrl+[",
                                   command=lambda: self.layer_move("backward"))
        self.item_menu.add_command(label="Send to back", command=lambda: self.layer_move("back"))
        self.item_menu.add_separator()
        pic = tk.Menu(self.item_menu, tearoff=0)
        pic.add_command(label="Flip horizontal", command=lambda: self.picture_edit(self.flip_x))
        pic.add_command(label="Flip vertical", command=lambda: self.picture_edit(self.flip_y))
        pic.add_command(label="Rotate left 90 degrees", command=lambda: self.picture_edit(lambda it: self.turn(it, -90)))
        pic.add_command(label="Rotate right 90 degrees", command=lambda: self.picture_edit(lambda it: self.turn(it, 90)))
        pic.add_separator()
        pic.add_checkbutton(label="Lock proportions", variable=self.lock_var, command=self.toggle_lock)
        pic.add_command(label="Translucency...", command=self.open_translucency)
        self.item_menu.add_cascade(label="Picture", menu=pic)

    def draw_grid(self):
        c = self.canvas
        c.delete("grid")
        step = GRID_STEP * self.zoom
        w, h = BOARD_W * self.zoom, BOARD_H * self.zoom
        for i in range(BOARD_W // GRID_STEP + 1):
            x = i * step
            c.create_line(x, 0, x, h, fill=self.grid_color, tags="grid")
        for j in range(BOARD_H // GRID_STEP + 1):
            y = j * step
            c.create_line(0, y, w, y, fill=self.grid_color, tags="grid")
        c.tag_lower("grid")

    def set_tool(self, name):
        self.tool = name
        for n, b in self.tool_buttons.items():
            b.config(relief=tk.SUNKEN if n == name else tk.RAISED)
        self.canvas.config(cursor="arrow" if name == "select" else ("xterm" if name == "note" else "crosshair"))
        self.update_status()

    def set_color(self, hexv):
        self.color = hexv
        for h, b in self.color_buttons.items():
            b.config(relief=tk.SUNKEN if h == hexv else tk.RAISED)
        targets = [it for it in self.items if it["id"] in self.sel and it["type"] in ("stroke", "line", "arrow", "text")]
        if targets:
            pre = self.snapshot()
            for it in targets:
                it["color"] = hexv
            self.push_undo(pre)
        self.redraw()

    def set_fill(self, hexv):
        self.fill = hexv
        for h, b in self.fill_buttons.items():
            b.config(relief=tk.SUNKEN if h == hexv else tk.RAISED)
        targets = [it for it in self.items if it["id"] in self.sel and it["type"] == "text"]
        if targets:
            pre = self.snapshot()
            for it in targets:
                it["bg"] = hexv
            self.push_undo(pre)
        self.redraw()

    def set_board(self, hexv, mark=True):
        self.board = hexv
        self.grid_color = grid_for(hexv)
        self.canvas.config(bg=hexv)
        self.draw_grid()
        self.redraw()
        if mark:
            self.dirty = True
            self.flash(f"Board colour: {hexv}")
        self.update_status()

    def pick_board(self):
        _rgb, hexv = colorchooser.askcolor(initialcolor=self.board, title="Board colour", parent=self.root)
        if hexv:
            self.set_board(hexv.lower())

    def flash(self, msg):
        self.status_msg = msg
        self.update_status()

    def update_status(self):
        state = "unsaved" if self.dirty else "saved"
        text = f"Tool: {self.tool}   Items: {len(self.items)}   Selected: {len(self.sel)}   {state}"
        if self.status_msg:
            text += f"   |   {self.status_msg}"
        self.status.config(text=text)
        if hasattr(self, "zoom_label"):
            self.zoom_label.config(text=f"{round(self.zoom * 100)}%")

    # ---------- Zoom ----------
    def set_zoom(self, z, anchor=None):
        z = max(ZOOM_MIN, min(ZOOM_MAX, z))
        if abs(z - self.zoom) < 1e-9:
            return
        c = self.canvas
        ax, ay = anchor if anchor else (c.winfo_width() / 2, c.winfo_height() / 2)
        bx = c.canvasx(ax) / self.zoom
        by = c.canvasy(ay) / self.zoom
        self.zoom = z
        c.config(scrollregion=(0, 0, BOARD_W * z, BOARD_H * z))
        self.draw_grid()
        self.redraw()
        # Keep the board point under the pointer (or the centre) where it was
        c.xview_moveto(max(0.0, bx * z - ax) / (BOARD_W * z))
        c.yview_moveto(max(0.0, by * z - ay) / (BOARD_H * z))

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.25)

    def zoom_reset(self):
        self.set_zoom(1.0)

    def on_wheel(self, e, sign=None):
        if sign is None:
            sign = 1 if e.delta > 0 else -1
        if e.state & 0x0004:  # Ctrl is held: zoom around the pointer
            self.set_zoom(self.zoom * (1.15 if sign > 0 else 1 / 1.15), anchor=(e.x, e.y))
        else:
            self.canvas.yview_scroll(-3 * sign, "units")

    # ---------- Model helpers ----------
    def new_id(self):
        i = self.next_id
        self.next_id += 1
        return i

    def by_id(self, iid):
        for it in self.items:
            if it["id"] == iid:
                return it
        return None

    def single_selected(self):
        if len(self.sel) == 1:
            return self.by_id(next(iter(self.sel)))
        return None

    @staticmethod
    def clone(it):
        d = dict(it)
        if "pts" in d:
            d["pts"] = list(d["pts"])
        return d

    def snapshot(self):
        return [self.clone(it) for it in self.items]

    def push_undo(self, pre):
        self.undo_stack.append(pre)
        if len(self.undo_stack) > 200:
            del self.undo_stack[0]
        self.redo_stack.clear()
        self.dirty = True

    @staticmethod
    def move_item(it, dx, dy):
        if "pts" in it:
            it["pts"] = [(px + dx, py + dy) for px, py in it["pts"]]
        else:
            it["x"] += dx
            it["y"] += dy

    def bounds(self, it):
        t = it["type"]
        if t in STROKE_TYPES + LINE_TYPES:
            if not it["pts"]:
                return None
            xs = [p[0] for p in it["pts"]]
            ys = [p[1] for p in it["pts"]]
            return (min(xs), min(ys), max(xs), max(ys))
        if t == "text":
            if it.get("w"):
                width = it["w"]
            else:
                width = max(len(line) for line in it["text"].split("\n")) * 9
            h = text_line_count(it) * 24
            return (it["x"], it["y"], it["x"] + max(width, 24), it["y"] + max(h, 24))
        if t == "image":
            rot = it.get("rot", 0) % 360
            if rot == 0:
                return (it["x"], it["y"], it["x"] + it["w"], it["y"] + it["h"])
            pts = rotated_corners(it["x"], it["y"], it["w"], it["h"], rot)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return (min(xs), min(ys), max(xs), max(ys))
        return None

    def near(self, it, x, y):
        t = it["type"]
        if t in STROKE_TYPES + LINE_TYPES:
            pts = it["pts"]
            if len(pts) < 2:
                return False
            pad = it.get("width", 2) / 2 + 6
            return any(dist_seg((x, y), pts[i], pts[i + 1]) <= pad for i in range(len(pts) - 1))
        b = self.bounds(it)
        return b is not None and b[0] - 4 <= x <= b[2] + 4 and b[1] - 4 <= y <= b[3] + 4

    def hit(self, x, y):
        for it in reversed(self.items):
            if self.near(it, x, y):
                return it
        return None

    # ---------- Free transform ----------
    def handle_spots(self, it):
        """Handle positions in board units. Corners and edges stretch; 'rot' turns."""
        b = self.bounds(it)
        if not b:
            return {}
        x0, y0, x1, y1 = b
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        is_text = it["type"] == "text"
        spots = {}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                if is_text and dy != 0:
                    continue  # notes only stretch sideways; their height follows the text
                px = x0 if dx < 0 else (x1 if dx > 0 else cx)
                py = y0 if dy < 0 else (y1 if dy > 0 else cy)
                spots[(dx, dy)] = (px, py)
        if not is_text:
            spots["rot"] = (cx, y0 - ROT_OFFSET / self.zoom)
        return spots

    def draw_handles(self, it):
        c = self.canvas
        Z = self.zoom
        b = self.bounds(it)
        spots = self.handle_spots(it)
        if not b or not spots:
            return
        if "rot" in spots:
            rx, ry = spots["rot"]
            c.create_line((b[0] + b[2]) / 2 * Z, b[1] * Z, rx * Z, ry * Z, fill=ACCENT, tags="item")
        for key, (px, py) in spots.items():
            if key == "rot":
                r = 6
                c.create_oval(px * Z - r, py * Z - r, px * Z + r, py * Z + r,
                              fill="#ffffff", outline=ACCENT, width=2, tags="item")
            else:
                h = HANDLE / 2
                c.create_rectangle(px * Z - h, py * Z - h, px * Z + h, py * Z + h,
                                   fill="#ffffff", outline=ACCENT, width=1.5, tags="item")

    def start_transform(self, it, key, x, y):
        b = self.bounds(it)
        pre = self.snapshot()
        orig = self.clone(it)
        if key == "rot":
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            self.drag = {"mode": "rotate", "id": it["id"], "orig": orig, "center": (cx, cy),
                         "ang0": math.degrees(math.atan2(y - cy, x - cx)), "pre": pre, "moved": False}
        else:
            self.drag = {"mode": "resize", "id": it["id"], "orig": orig, "box": b, "key": key,
                         "pre": pre, "moved": False}

    @staticmethod
    def transform_item(orig, box0, box1, key=None):
        """New fields for an item when its box changes from box0 to box1."""
        x0, y0, x1, y1 = box0
        X0, Y0, X1, Y1 = box1
        sx = (X1 - X0) / max(1e-6, x1 - x0)
        sy = (Y1 - Y0) / max(1e-6, y1 - y0)
        t = orig["type"]
        if t in STROKE_TYPES + LINE_TYPES:
            return {"pts": [(X0 + (px - x0) * sx, Y0 + (py - y0) * sy) for px, py in orig["pts"]]}
        if t == "text":
            return {"x": X0, "w": max(MIN_SIZE * 4, X1 - X0)}
        if t == "image":
            ccx, ccy = (X0 + X1) / 2, (Y0 + Y1) / 2
            if orig.get("lock", True) and key and key[0] and key[1]:
                # Corner drag with proportions locked: one factor, anchored at the opposite corner
                s = sx if abs(sx - 1) >= abs(sy - 1) else sy
                ax = x1 if key[0] < 0 else x0
                ay = y1 if key[1] < 0 else y0
                c0x, c0y = (x0 + x1) / 2, (y0 + y1) / 2
                ccx, ccy = ax + (c0x - ax) * s, ay + (c0y - ay) * s
                fw = fh = s
            else:
                rot = orig.get("rot", 0) % 180
                if rot == 0:
                    fw, fh = sx, sy
                elif rot == 90:
                    fw, fh = sy, sx          # turned a quarter: the box axes swap
                else:
                    fw = fh = (sx + sy) / 2  # turned at an angle: scale evenly
            nw = max(MIN_SIZE * 2, orig["w"] * fw)
            nh = max(MIN_SIZE * 2, orig["h"] * fh)
            return {"w": nw, "h": nh, "x": ccx - nw / 2, "y": ccy - nh / 2}
        return {}

    # ---------- Drawing ----------
    def draw_item(self, it):
        c = self.canvas
        Z = self.zoom
        tag = ("item", f"i{it['id']}")
        t = it["type"]
        if t in STROKE_TYPES + LINE_TYPES:
            if len(it["pts"]) < 2:
                return
            flat = []
            for px, py in it["pts"]:
                flat += [px * Z, py * Z]
            width = max(1, it["width"] * Z)
            if t == "highlight":
                c.create_line(*flat, fill=HIGHLIGHT, width=width, capstyle=tk.ROUND,
                              joinstyle=tk.ROUND, tags=tag)
            elif t == "stroke":
                c.create_line(*flat, fill=it["color"], width=width, capstyle=tk.ROUND,
                              joinstyle=tk.ROUND, smooth=len(it["pts"]) > 2, tags=tag)
            elif t == "line":
                c.create_line(*flat, fill=it["color"], width=width, capstyle=tk.ROUND, tags=tag)
            elif t == "arrow":
                c.create_line(*flat, fill=it["color"], width=width, capstyle=tk.ROUND,
                              arrow=tk.LAST, arrowshape=(14 * Z, 18 * Z, 6 * Z), tags=tag)
        elif t == "text":
            kw = {"text": it["text"], "anchor": "nw", "font": ("Georgia", max(6, round(FONT_SIZE * Z))),
                  "fill": it["color"], "tags": tag}
            if it.get("w"):
                kw["width"] = it["w"] * Z
            elif it.get("bg"):
                kw["width"] = 240 * Z
            x, y = it["x"] * Z, it["y"] * Z
            if it.get("bg"):
                tid = c.create_text(x, y, **kw)
                bb = c.bbox(tid)
                if bb:
                    rect = c.create_rectangle(bb[0] - 10 * Z, bb[1] - 8 * Z, bb[2] + 10 * Z, bb[3] + 8 * Z,
                                              fill=it["bg"], outline="", tags=tag)
                    c.tag_lower(rect, tid)
            else:
                c.create_text(x, y, **kw)
        elif t == "image":
            W = max(1, round(it["w"] * Z))
            H = max(1, round(it["h"] * Z))
            rot = it.get("rot", 0) % 360
            alpha = it.get("alpha", 100)
            flips = (bool(it.get("flipx")), bool(it.get("flipy")))
            key = (W, H, rot, alpha, flips, self.board)
            if it.get("tkkey") != key:
                it["tk"] = ImageTk.PhotoImage(self.render_picture(it, W, H, rot, alpha, flips))
                it["tkkey"] = key
            cx = (it["x"] + it["w"] / 2) * Z
            cy = (it["y"] + it["h"] / 2) * Z
            c.create_image(cx, cy, image=it["tk"], anchor="center", tags=tag)
        if it.get("pinned"):
            b = self.bounds(it)
            if b:
                c.create_oval(b[0] * Z - 7, b[1] * Z - 7, b[0] * Z + 3, b[1] * Z + 3,
                              fill=ACCENT, outline="", tags=tag)

    def redraw(self):
        c = self.canvas
        Z = self.zoom
        c.delete("item")
        for it in self.items:
            self.draw_item(it)
        for it in self.items:
            if it["id"] in self.sel:
                b = self.bounds(it)
                if b:
                    c.create_rectangle(b[0] * Z - 6, b[1] * Z - 6, b[2] * Z + 6, b[3] * Z + 6,
                                       outline=ACCENT, dash=(4, 3), width=1.5, tags="item")
        one = self.single_selected()
        if one:
            self.draw_handles(one)
        d = self.drag
        if d and d["mode"] == "draw":
            t = TOOL_TO_TYPE[d["tool"]]
            self.draw_item({"id": 0, "type": t, "pts": d["pts"], "width": TOOL_WIDTH[t],
                            "color": self.color})
        elif d and d["mode"] == "marquee":
            c.create_rectangle(d["x0"] * Z, d["y0"] * Z, d["x1"] * Z, d["y1"] * Z,
                               outline=ACCENT, dash=(3, 3), tags="item")
        self.update_status()

    # ---------- Mouse ----------
    def cx(self, e):
        return self.canvas.canvasx(e.x) / self.zoom

    def cy(self, e):
        return self.canvas.canvasy(e.y) / self.zoom

    def on_press(self, e):
        self.canvas.focus_set()
        x, y = self.cx(e), self.cy(e)
        shift = bool(e.state & 0x0001)
        if self.tool == "note":
            self.new_note(x, y)
            return
        if self.tool == "select":
            one = self.single_selected()
            if one:
                ex, ey = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
                for key, (px, py) in self.handle_spots(one).items():
                    if abs(ex - px * self.zoom) <= HANDLE and abs(ey - py * self.zoom) <= HANDLE:
                        self.start_transform(one, key, x, y)
                        self.redraw()
                        return
            hit = self.hit(x, y)
            if hit:
                if shift:
                    self.sel ^= {hit["id"]}
                elif hit["id"] not in self.sel:
                    self.sel = {hit["id"]}
                if hit["id"] in self.sel:
                    self.drag = {"mode": "move", "x0": x, "y0": y, "moved": False, "pre": self.snapshot()}
            else:
                base = set(self.sel) if shift else set()
                if not shift:
                    self.sel = set()
                self.drag = {"mode": "marquee", "x0": x, "y0": y, "x1": x, "y1": y, "base": base}
            self.redraw()
            return
        self.drag = {"mode": "draw", "tool": self.tool, "pts": [(x, y)], "pre": self.snapshot()}

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        x, y = self.cx(e), self.cy(e)
        mode = d["mode"]
        if mode == "move":
            dx, dy = x - d["x0"], y - d["y0"]
            if dx or dy:
                for it in self.items:
                    if it["id"] in self.sel and not it.get("pinned"):
                        self.move_item(it, dx, dy)
                d["x0"], d["y0"] = x, y
                d["moved"] = True
        elif mode == "resize":
            x0, y0, x1, y1 = d["box"]
            kx, ky = d["key"]
            nx0, ny0, nx1, ny1 = x0, y0, x1, y1
            if kx < 0:
                nx0 = min(x, x1 - MIN_SIZE)
            elif kx > 0:
                nx1 = max(x, x0 + MIN_SIZE)
            if ky < 0:
                ny0 = min(y, y1 - MIN_SIZE)
            elif ky > 0:
                ny1 = max(y, y0 + MIN_SIZE)
            it = self.by_id(d["id"])
            if it:
                it.update(self.transform_item(d["orig"], d["box"], (nx0, ny0, nx1, ny1), d["key"]))
            d["moved"] = True
        elif mode == "rotate":
            cx, cy = d["center"]
            delta = math.degrees(math.atan2(y - cy, x - cx)) - d["ang0"]
            it = self.by_id(d["id"])
            orig = d["orig"]
            if it:
                if orig["type"] == "image":
                    it["rot"] = (orig.get("rot", 0) + delta) % 360
                elif orig["type"] in STROKE_TYPES + LINE_TYPES:
                    it["pts"] = rotate_pts(orig["pts"], d["center"], delta)
            d["moved"] = True
        elif mode == "marquee":
            d["x1"], d["y1"] = x, y
        elif mode == "draw":
            if d["tool"] in ("pen", "highlight"):
                d["pts"].append((x, y))
            else:
                d["pts"] = [d["pts"][0], (x, y)]
        self.redraw()

    def on_release(self, e):
        d = self.drag
        self.drag = None
        if not d:
            return
        if d["mode"] in ("move", "resize", "rotate"):
            if d["moved"]:
                self.push_undo(d["pre"])
        elif d["mode"] == "marquee":
            x0, x1 = min(d["x0"], d["x1"]), max(d["x0"], d["x1"])
            y0, y1 = min(d["y0"], d["y1"]), max(d["y0"], d["y1"])
            found = set(d["base"])
            for it in self.items:
                b = self.bounds(it)
                if b and b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1:
                    found.add(it["id"])
            self.sel = found
        elif d["mode"] == "draw":
            pts = d["pts"]
            moved = len(pts) >= 2 and (abs(pts[-1][0] - pts[0][0]) > 2 or abs(pts[-1][1] - pts[0][1]) > 2)
            if moved:
                t = TOOL_TO_TYPE[d["tool"]]
                it = {"id": self.new_id(), "type": t,
                      "pts": [(round(px, 1), round(py, 1)) for px, py in pts],
                      "width": TOOL_WIDTH[t],
                      "color": HIGHLIGHT if t == "highlight" else self.color}
                self.items.append(it)
                self.sel = {it["id"]}
                self.push_undo(d["pre"])
        self.redraw()

    def on_double(self, e):
        if self.tool != "select":
            return
        it = self.hit(self.cx(e), self.cy(e))
        if it and it["type"] == "text":
            self.edit_text(it)

    def edit_text(self, it):
        new = simpledialog.askstring("Edit note", "Text:", initialvalue=it["text"], parent=self.root)
        if new is not None and new != it["text"]:
            pre = self.snapshot()
            it["text"] = new
            self.push_undo(pre)
            self.redraw()

    def edit_selected_text(self):
        it = next((i for i in self.items if i["id"] in self.sel and i["type"] == "text"), None)
        if not it:
            self.flash("Select a note to edit its text")
            return
        self.edit_text(it)

    def new_note(self, x, y):
        text = simpledialog.askstring("New note", "Text:", parent=self.root)
        self.set_tool("select")
        if not text:
            return
        pre = self.snapshot()
        it = {"id": self.new_id(), "type": "text", "x": round(x), "y": round(y), "text": text,
              "color": self.color, "bg": self.fill, "pinned": False}
        self.items.append(it)
        self.sel = {it["id"]}
        self.push_undo(pre)
        self.redraw()

    def on_right_click(self, e):
        x, y = self.cx(e), self.cy(e)
        self.right_spot = (x, y)
        hit = self.hit(x, y)
        if hit:
            if hit["id"] not in self.sel:
                self.sel = {hit["id"]}
                self.redraw()
            menu = self.item_menu
        else:
            menu = self.board_menu
        first = next((i for i in self.items if i["id"] in self.sel and i["type"] == "image"), None)
        self.lock_var.set(first.get("lock", True) if first else True)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    # ---------- Pictures ----------
    def picture_edit(self, fn):
        targets = [it for it in self.items if it["id"] in self.sel and it["type"] == "image"]
        if not targets:
            self.flash("Select a picture first")
            return
        pre = self.snapshot()
        for it in targets:
            fn(it)
        self.push_undo(pre)
        self.redraw()

    @staticmethod
    def flip_x(it):
        it["flipx"] = not it.get("flipx", False)

    @staticmethod
    def flip_y(it):
        it["flipy"] = not it.get("flipy", False)

    @staticmethod
    def turn(it, deg):
        it["rot"] = (it.get("rot", 0) + deg) % 360

    def toggle_lock(self):
        val = self.lock_var.get()
        self.picture_edit(lambda it: it.__setitem__("lock", val))

    def open_translucency(self):
        targets = [it for it in self.items if it["id"] in self.sel and it["type"] == "image"]
        if not targets:
            self.flash("Select a picture first")
            return
        ids = [it["id"] for it in targets]
        pre = self.snapshot()
        before = {it["id"]: it.get("alpha", 100) for it in pre}
        top = tk.Toplevel(self.root)
        top.title("Translucency")
        top.transient(self.root)

        def live(v):
            for i in ids:
                it = self.by_id(i)
                if it:
                    it["alpha"] = int(float(v))
            self.redraw()

        scale = tk.Scale(top, from_=0, to=100, orient=tk.HORIZONTAL, length=260,
                         label="Opacity (%)", command=live)
        scale.set(targets[0].get("alpha", 100))
        scale.pack(padx=16, pady=(12, 4))
        tk.Label(top, text="0 = invisible, 100 = solid").pack()

        def done():
            changed = any(self.by_id(i) and self.by_id(i).get("alpha", 100) != before[i] for i in ids)
            if changed:
                self.push_undo(pre)
                self.redraw()
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", done)
        tk.Button(top, text="Done", command=done).pack(pady=8)

    def board_rgba(self):
        h = self.board
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) + (255,)

    def render_picture(self, it, W, H, rot, alpha, flips):
        img = it["pil"]
        if flips[0]:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if flips[1]:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        img = img.resize((W, H), Image.LANCZOS)
        if rot:
            img = img.rotate(-rot, resample=Image.BICUBIC, expand=True)  # clockwise turn
        if alpha < 100:
            img.putalpha(img.getchannel("A").point(lambda v: v * alpha // 100))
        # Blend over the board colour, so transparent areas and see-through pictures match the board
        return Image.alpha_composite(Image.new("RGBA", img.size, self.board_rgba()), img)

    # ---------- Layers ----------
    def layer_move(self, how):
        """Move the selected items in the stack. Later in the list is drawn on top."""
        ids = {it["id"] for it in self.items if it["id"] in self.sel}
        if not ids:
            return
        pre = self.snapshot()
        if how == "front":
            new = [it for it in self.items if it["id"] not in ids] + [it for it in self.items if it["id"] in ids]
        elif how == "back":
            new = [it for it in self.items if it["id"] in ids] + [it for it in self.items if it["id"] not in ids]
        else:
            new = list(self.items)
            step = 1 if how == "forward" else -1
            order = range(len(new) - 1, -1, -1) if step > 0 else range(len(new))
            for i in order:
                if new[i]["id"] in ids:
                    j = i + step
                    if 0 <= j < len(new) and new[j]["id"] not in ids:
                        new[i], new[j] = new[j], new[i]
        if [it["id"] for it in new] == [it["id"] for it in self.items]:
            return
        self.items = new
        self.push_undo(pre)
        self.redraw()

    # ---------- Selection actions ----------
    def select_all(self):
        self.sel = {it["id"] for it in self.items}
        self.redraw()

    def select_none(self):
        self.sel = set()
        self.redraw()

    def toggle_pin(self):
        targets = [it for it in self.items if it["id"] in self.sel]
        if not targets:
            return
        pre = self.snapshot()
        new_state = not all(it.get("pinned") for it in targets)
        for it in targets:
            it["pinned"] = new_state
        self.push_undo(pre)
        self.redraw()

    def delete_selected(self):
        targets = [it for it in self.items if it["id"] in self.sel]
        if not targets:
            return
        removable = [it for it in targets if not it.get("pinned")]
        if not removable:
            self.flash("Unpin items before deleting them")
            return
        pre = self.snapshot()
        ids = {it["id"] for it in removable}
        self.items = [it for it in self.items if it["id"] not in ids]
        self.sel = set()
        self.push_undo(pre)
        self.redraw()

    # ---------- Undo / redo ----------
    def undo(self):
        if not self.undo_stack:
            self.flash("Nothing to undo")
            return
        self.redo_stack.append(self.snapshot())
        self.items = self.undo_stack.pop()
        self.sel &= {it["id"] for it in self.items}
        self.dirty = True
        self.redraw()

    def redo(self):
        if not self.redo_stack:
            self.flash("Nothing to redo")
            return
        self.undo_stack.append(self.snapshot())
        self.items = self.redo_stack.pop()
        self.sel &= {it["id"] for it in self.items}
        self.dirty = True
        self.redraw()

    # ---------- Clipboard ----------
    def copy(self):
        self.clip = [self.clone(it) for it in self.items if it["id"] in self.sel]
        self.paste_count = 0
        self.flash(f"Copied {len(self.clip)} item(s)")

    def cut(self):
        self.copy()
        self.delete_selected()

    def duplicate(self):
        self.copy()
        self.paste()

    def view_center(self):
        c = self.canvas
        return c.canvasx(c.winfo_width() / 2) / self.zoom, c.canvasy(c.winfo_height() / 2) / self.zoom

    def paste(self, at=None):
        spot = at or self.view_center()
        # 1. An image on the system clipboard (screenshots, images copied from a browser)
        if HAS_PIL and ImageGrab is not None:
            try:
                grabbed = ImageGrab.grabclipboard()
            except Exception:
                grabbed = None
            if isinstance(grabbed, Image.Image):
                self.place_image(grabbed.convert("RGBA"), *spot)
                return
            if isinstance(grabbed, list):
                for path in grabbed:
                    try:
                        self.place_image(Image.open(path).convert("RGBA"), *spot)
                    except Exception:
                        pass
                return
        # 2. Items copied inside this app
        if self.clip:
            self.paste_count += 1
            off = 24 * self.paste_count
            pre = self.snapshot()
            new_sel = set()
            for src in self.clip:
                it = self.clone(src)
                it["id"] = self.new_id()
                it["pinned"] = False
                self.move_item(it, off, off)
                self.items.append(it)
                new_sel.add(it["id"])
            self.sel = new_sel
            self.push_undo(pre)
            self.redraw()
            return
        # 3. Plain text becomes a note
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        if text.strip():
            x, y = spot
            pre = self.snapshot()
            it = {"id": self.new_id(), "type": "text", "x": round(x), "y": round(y), "text": text.strip(),
                  "color": self.color, "bg": self.fill, "pinned": False}
            self.items.append(it)
            self.sel = {it["id"]}
            self.push_undo(pre)
            self.redraw()

    # ---------- Images ----------
    def add_image_dialog(self):
        if not HAS_PIL:
            messagebox.showinfo("Images need Pillow", "Install Pillow to add images:\n\npip install pillow")
            return
        paths = filedialog.askopenfilenames(
            title="Add images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")])
        for p in paths:
            try:
                pil = Image.open(p).convert("RGBA")
            except Exception:
                messagebox.showwarning("Could not open", os.path.basename(p))
                continue
            x, y = self.view_center()
            self.place_image(pil, x, y)

    def place_image(self, pil, x, y):
        w = min(420, pil.width)
        h = pil.height * w / pil.width
        pre = self.snapshot()
        it = {"id": self.new_id(), "type": "image", "x": round(x - w / 2), "y": round(y - h / 2),
              "w": w, "h": h, "rot": 0, "alpha": 100, "flipx": False, "flipy": False, "lock": True, "pil": pil, "pinned": False}
        self.items.append(it)
        self.sel = {it["id"]}
        self.push_undo(pre)
        self.redraw()

    # ---------- Clear ----------
    def clear_board(self):
        if not self.items:
            return
        if not messagebox.askyesno("Clear board", "Remove everything on the board? You can undo this."):
            return
        pre = self.snapshot()
        self.items = []
        self.sel = set()
        self.push_undo(pre)
        self.redraw()

    # ---------- Save / open ----------
    def serialize(self, it):
        d = {k: v for k, v in it.items() if k not in ("tk", "tkkey", "tkw", "pil")}
        if "pts" in d:
            d["pts"] = [list(p) for p in d["pts"]]
        if it["type"] == "image":
            buf = io.BytesIO()
            it["pil"].save(buf, "PNG")
            d["png"] = base64.b64encode(buf.getvalue()).decode("ascii")
        return d

    def deserialize(self, d):
        it = dict(d)
        if "pts" in it:
            it["pts"] = [tuple(p) for p in it["pts"]]
        if it["type"] == "image":
            if not HAS_PIL:
                raise RuntimeError("Pillow is needed to open this file (it contains images).")
            raw = base64.b64decode(it.pop("png"))
            it["pil"] = Image.open(io.BytesIO(raw)).convert("RGBA")
            it.setdefault("rot", 0)
        return it

    def save(self, ask=False):
        if ask or not self.path:
            path = filedialog.asksaveasfilename(
                title="Save drafting table", defaultextension=".drafting.json",
                filetypes=[("Drafting Table", "*.drafting.json"), ("JSON", "*.json")])
            if not path:
                return False
            self.path = path
        data = {"version": 1, "board": self.board, "items": [self.serialize(it) for it in self.items]}
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError as err:
            messagebox.showerror("Save failed", str(err))
            return False
        self.dirty = False
        self.root.title(f"Drafting Table - {os.path.basename(self.path)}")
        self.flash("Saved")
        return True

    def open_file(self):
        if self.dirty and not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open drafting table",
            filetypes=[("Drafting Table", "*.drafting.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            items = [self.deserialize(d) for d in data["items"]]
        except Exception as err:
            messagebox.showerror("Open failed", str(err))
            return
        self.set_board(load_board(data.get("board")), mark=False)
        self.items = items
        self.sel = set()
        self.undo_stack = []
        self.redo_stack = []
        self.path = path
        self.dirty = False
        self.next_id = max([it["id"] for it in items], default=0) + 1
        self.root.title(f"Drafting Table - {os.path.basename(path)}")
        self.redraw()

    def confirm_discard(self):
        ans = messagebox.askyesnocancel("Unsaved changes", "Save your drafting table first?")
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    def on_close(self):
        if self.dirty and not self.confirm_discard():
            return
        self.root.destroy()

    # ---------- Updates ----------
    def check_updates(self):
        try:
            info = json.loads(fetch_update_file("version.json").decode("utf-8"))
            latest = str(info["version"])
            name = str(info.get("file", "drafting_table.py"))
            if name != "drafting_table.py":
                raise ValueError("update names an unexpected file")
            version_tuple(latest)
        except Exception as err:
            messagebox.showerror("Could not check for updates",
                                 "Could not reach the update server. Check your internet connection.\n\n"
                                 f"Details: {err}")
            return
        if version_tuple(latest) <= version_tuple(APP_VERSION):
            messagebox.showinfo("Up to date", f"You have the latest version ({APP_VERSION}).")
            return
        if not messagebox.askyesno(
                "Update available",
                f"Version {latest} is available. You have {APP_VERSION}.\n\n"
                "Install it now? A backup of your current version is saved first, and your boards are not changed. "
                "Close and reopen the app afterwards."):
            return
        try:
            text = fetch_update_file(name).decode("utf-8")
            compile(text, name, "exec")  # checks it is valid Python without running it
            if f'APP_VERSION = "{latest}"' not in text:
                raise ValueError("the downloaded file does not match the version it claims")
        except Exception as err:
            messagebox.showerror("Update stopped",
                                 "The download was not installed. Your current app is unchanged.\n\n"
                                 f"Details: {err}")
            return
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(APP_FILE, os.path.join(BACKUP_DIR, f"drafting_table_{APP_VERSION}_{stamp}.py"))
            tmp = APP_FILE + ".new"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, APP_FILE)
        except OSError as err:
            messagebox.showerror("Update stopped", f"Could not replace the app file. Your current app is unchanged.\n\n{err}")
            return
        messagebox.showinfo("Updated",
                            f"Installed version {latest}. Close and reopen the app to use it.\n\n"
                            "The old version is saved in the backups folder.")

    # ---------- Local API (used by the Claude and ChatGPT bridges) ----------
    def api_board(self):
        out = []
        for it in self.items:
            d = {"id": it["id"], "type": it["type"], "color": it.get("color"), "pinned": bool(it.get("pinned"))}
            if it["type"] == "text":
                d.update(x=round(it["x"]), y=round(it["y"]), text=it["text"], background=it.get("bg"))
            elif it["type"] == "image":
                d.update(x=round(it["x"]), y=round(it["y"]), width=round(it["w"]), height=round(it["h"]))
            else:
                d["points"] = [[round(px), round(py)] for px, py in it["pts"]]
            out.append(d)
        return {"board_size": [BOARD_W, BOARD_H], "items": out}

    def api_add(self, specs):
        if not isinstance(specs, list) or not specs:
            raise ValueError('send a non-empty list under "items"')
        if len(specs) > 200:
            raise ValueError("at most 200 items per request")
        new = [self.build_item(s) for s in specs]
        pre = self.snapshot()
        self.items.extend(new)
        self.push_undo(pre)
        self.flash(f"Added {len(new)} item(s) from the API")
        self.redraw()
        return {"added_ids": [it["id"] for it in new]}

    def api_delete(self, ids):
        if not isinstance(ids, list) or not ids:
            raise ValueError('send a non-empty list of ids under "ids"')
        try:
            wanted = {int(i) for i in ids}
        except (TypeError, ValueError):
            raise ValueError("ids must be integers")
        targets = [it for it in self.items if it["id"] in wanted]
        removable = {it["id"] for it in targets if not it.get("pinned")}
        skipped = sorted(it["id"] for it in targets if it.get("pinned"))
        if removable:
            pre = self.snapshot()
            self.items = [it for it in self.items if it["id"] not in removable]
            self.sel -= removable
            self.push_undo(pre)
            self.flash(f"Removed {len(removable)} item(s) from the API")
        self.redraw()
        return {"removed_ids": sorted(removable), "skipped_pinned_ids": skipped,
                "not_found_ids": sorted(wanted - {it["id"] for it in targets})}

    def build_item(self, s):
        if not isinstance(s, dict):
            raise ValueError("each item must be an object")
        t = s.get("type")
        if t == "note":
            text = str(s.get("text", "")).strip()
            if not text:
                raise ValueError("a note needs text")
            x, y = self.api_xy(s.get("x", 80), s.get("y", 80))
            return {"id": self.new_id(), "type": "text", "x": x, "y": y, "text": text[:500],
                    "color": self.api_color(s.get("color")), "bg": self.api_hex(s.get("background")),
                    "pinned": False}
        if t in ("line", "arrow", "highlight", "stroke"):
            pts = self.api_points(s, t)
            color = HIGHLIGHT if t == "highlight" else self.api_color(s.get("color"))
            return {"id": self.new_id(), "type": t, "pts": pts, "width": TOOL_WIDTH[t],
                    "color": color, "pinned": False}
        raise ValueError(f"unknown type {t!r}: use note, line, arrow, highlight or stroke")

    def api_points(self, s, t):
        try:
            raw = s["points"] if "points" in s else [[s["x1"], s["y1"]], [s["x2"], s["y2"]]]
        except KeyError:
            raise ValueError(f"{t} needs x1, y1, x2, y2 or a points list")
        if not isinstance(raw, list) or len(raw) < 2 or len(raw) > 2000:
            raise ValueError("need between 2 and 2000 points")
        if t in ("line", "arrow") and len(raw) != 2:
            raise ValueError("lines and arrows take exactly two points")
        try:
            return [self.api_xy(p[0], p[1]) for p in raw]
        except (TypeError, IndexError):
            raise ValueError("points must be [x, y] pairs")

    @staticmethod
    def api_xy(x, y):
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            raise ValueError("coordinates must be numbers")
        return (min(max(x, 0.0), BOARD_W - 20.0), min(max(y, 0.0), BOARD_H - 20.0))

    @staticmethod
    def api_hex(value):
        if value is None:
            return None
        if isinstance(value, str) and len(value) == 7 and value[0] == "#":
            try:
                int(value[1:], 16)
                return value.lower()
            except ValueError:
                pass
        raise ValueError(f"colour {value!r} must be a name (Ink, Red, Blue, Green) or a hex like #aabbcc")

    @classmethod
    def api_color(cls, value):
        if value is None:
            return COLORS[0][1]
        names = {n.lower(): h for n, h in COLORS}
        if isinstance(value, str) and value.lower() in names:
            return names[value.lower()]
        return cls.api_hex(value)


def main():
    root = tk.Tk()
    app = DraftingTable(root)
    try:
        from board_api import Bridge
        Bridge(app, root).start()
        app.flash("Local API on 127.0.0.1:8765. Token: ~/.drafting_table_token")
    except OSError as err:
        app.flash(f"Local API not started: {err}")
    root.mainloop()


if __name__ == "__main__":
    main()
