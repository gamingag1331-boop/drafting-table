#!/usr/bin/env python3
"""Drafting Table: a desktop drafting surface.

Write notes, draw freehand, draw lines and arrows, highlight, and place images.
Undo/redo, cut/copy/paste/duplicate, and save/open .drafting.json files.

Needs Python 3 with tkinter (included with the python.org installers).
Pillow is optional. It enables JPEG/WebP/PNG images and pasting images from the clipboard:
    pip install pillow
"""
import base64
import io
import json
import os
import shutil
import tkinter as tk
import urllib.request
import time
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

BOARD_W, BOARD_H = 3000, 2200
GRID_STEP = 40
PAPER = "#eef2f0"
GRID = "#d3dbd8"
HIGHLIGHT = "#fff07a"
ACCENT = "#2f5bd3"
COLORS = [("Ink", "#1b2b30"), ("Red", "#c8402d"), ("Blue", "#2f5bd3"), ("Green", "#2f8f5b")]
FILLS = [("None", None), ("Yellow", "#fff3a3"), ("Pink", "#ffd6c9"), ("Blue", "#cfe8ff"), ("Green", "#d5f5dc")]
APP_VERSION = "1.4"
UPDATE_BASE = "https://raw.githubusercontent.com/gamingag1331-boop/drafting-table/main/"
APP_FILE = os.path.abspath(__file__)
BACKUP_DIR = os.path.join(os.path.dirname(APP_FILE), "backups")
BOARD_DEFAULT = "#eef2f0"
OLD_BOARD_NAMES = {"Paper": "#eef2f0", "White": "#ffffff", "Kraft": "#efe3cc",
                   "Sage": "#e1ebe1", "Sky": "#e3eef8"}


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
FONT = ("Georgia", 16)
STROKE_TYPES = ("stroke", "highlight")
LINE_TYPES = ("line", "arrow")
TOOL_TO_TYPE = {"pen": "stroke", "highlight": "highlight", "line": "line", "arrow": "arrow"}
TOOL_WIDTH = {"stroke": 3, "highlight": 22, "line": 2.5, "arrow": 2.5}


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
        self.board = BOARD_DEFAULT
        self.drag = None
        self.path = None
        self.dirty = False
        self.status_msg = ""
        if not HAS_PIL:
            self.status_msg = "Pillow not installed: images are disabled. Run: pip install pillow"
        self.build_ui()
        self.bind_keys()
        self.draw_grid()
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
            b = tk.Button(bar, text=fname, bg=fhex or "#ffffff", width=7,
                          command=lambda c=fhex: self.set_fill(c))
            b.pack(side=tk.LEFT, padx=2)
            self.fill_buttons[fhex] = b
        tk.Frame(bar, width=14).pack(side=tk.LEFT)
        tk.Button(bar, text="Board colour...", command=self.pick_board).pack(side=tk.LEFT, padx=2)

        bar2 = tk.Frame(self.root, padx=8)
        bar2.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        actions = [("Undo", self.undo), ("Redo", self.redo), ("Cut", self.cut), ("Copy", self.copy),
                   ("Paste", self.paste), ("Duplicate", self.duplicate), ("Delete", self.delete_selected),
                   ("Pin / Unpin", self.toggle_pin), ("Add image", self.add_image_dialog),
                   ("Bigger", lambda: self.scale_selected(1.1)), ("Smaller", lambda: self.scale_selected(0.9)),
                   ("Open", self.open_file), ("Save", self.save), ("Clear", self.clear_board),
                   ("Check for updates", self.check_updates)]
        for text, cmd in actions:
            tk.Button(bar2, text=text, command=cmd).pack(side=tk.LEFT, padx=2)

        frame = tk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, bg=PAPER, highlightthickness=0,
                                scrollregion=(0, 0, BOARD_W, BOARD_H))
        vs = tk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
        hs = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.status = tk.Label(self.root, anchor="w", padx=8, pady=4)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<MouseWheel>", lambda e: c.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        c.bind("<Button-4>", lambda e: c.yview_scroll(-3, "units"))
        c.bind("<Button-5>", lambda e: c.yview_scroll(3, "units"))

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
        for key in ("<plus>", "<equal>"):
            r.bind(key, lambda e: self.scale_selected(1.1))
        r.bind("<minus>", lambda e: self.scale_selected(0.9))

    def draw_grid(self):
        c = self.canvas
        for x in range(0, BOARD_W + 1, GRID_STEP):
            c.create_line(x, 0, x, BOARD_H, fill=GRID, tags="grid")
        for y in range(0, BOARD_H + 1, GRID_STEP):
            c.create_line(0, y, BOARD_W, y, fill=GRID, tags="grid")

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

    def set_board(self, hexv, mark=True):
        self.board = hexv
        self.canvas.config(bg=hexv)
        self.canvas.itemconfig("grid", fill=grid_for(hexv))
        if mark:
            self.dirty = True
            self.flash(f"Board colour: {hexv}")
        self.update_status()

    def pick_board(self):
        _rgb, hexv = colorchooser.askcolor(initialcolor=self.board, title="Board colour", parent=self.root)
        if hexv:
            self.set_board(hexv.lower())

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

    def flash(self, msg):
        self.status_msg = msg
        self.update_status()

    def update_status(self):
        state = "unsaved" if self.dirty else "saved"
        text = f"Tool: {self.tool}   Items: {len(self.items)}   Selected: {len(self.sel)}   {state}"
        if self.status_msg:
            text += f"   |   {self.status_msg}"
        self.status.config(text=text)

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
            lines = it["text"].split("\n")
            w = max(len(line) for line in lines) * 9
            h = len(lines) * 24
            return (it["x"], it["y"], it["x"] + max(w, 24), it["y"] + max(h, 24))
        if t == "image":
            return (it["x"], it["y"], it["x"] + it["w"], it["y"] + it["h"])
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

    # ---------- Drawing ----------
    def draw_item(self, it):
        c = self.canvas
        tag = ("item", f"i{it['id']}")
        t = it["type"]
        if t in STROKE_TYPES + LINE_TYPES:
            if len(it["pts"]) < 2:
                return
            flat = [v for p in it["pts"] for v in p]
            if t == "highlight":
                c.create_line(*flat, fill=HIGHLIGHT, width=it["width"], capstyle=tk.ROUND,
                              joinstyle=tk.ROUND, tags=tag)
            elif t == "stroke":
                c.create_line(*flat, fill=it["color"], width=it["width"], capstyle=tk.ROUND,
                              joinstyle=tk.ROUND, smooth=len(it["pts"]) > 2, tags=tag)
            elif t == "line":
                c.create_line(*flat, fill=it["color"], width=it["width"], capstyle=tk.ROUND, tags=tag)
            elif t == "arrow":
                c.create_line(*flat, fill=it["color"], width=it["width"], capstyle=tk.ROUND,
                              arrow=tk.LAST, arrowshape=(14, 18, 6), tags=tag)
        elif t == "text":
            if it.get("bg"):
                tid = c.create_text(it["x"], it["y"], text=it["text"], anchor="nw", font=FONT,
                                    fill=it["color"], width=240, tags=tag)
                bb = c.bbox(tid)
                if bb:
                    rect = c.create_rectangle(bb[0] - 10, bb[1] - 8, bb[2] + 10, bb[3] + 8,
                                              fill=it["bg"], outline="", tags=tag)
                    c.tag_lower(rect, tid)
            else:
                c.create_text(it["x"], it["y"], text=it["text"], anchor="nw", font=FONT,
                              fill=it["color"], tags=tag)
        elif t == "image":
            if it.get("tk") is None or it.get("tkw") != it["w"]:
                w = max(1, int(round(it["w"])))
                h = max(1, int(round(it["h"])))
                it["tk"] = ImageTk.PhotoImage(it["pil"].resize((w, h), Image.LANCZOS))
                it["tkw"] = it["w"]
            c.create_image(it["x"], it["y"], image=it["tk"], anchor="nw", tags=tag)
        if it.get("pinned"):
            b = self.bounds(it)
            if b:
                c.create_oval(b[0] - 7, b[1] - 7, b[0] + 3, b[1] + 3, fill=ACCENT, outline="", tags=tag)

    def redraw(self):
        c = self.canvas
        c.delete("item")
        for it in sorted(self.items, key=lambda i: 0 if i["type"] == "highlight" else 1):
            self.draw_item(it)
        for it in self.items:
            if it["id"] in self.sel:
                b = self.bounds(it)
                if b:
                    c.create_rectangle(b[0] - 6, b[1] - 6, b[2] + 6, b[3] + 6, outline=ACCENT,
                                       dash=(4, 3), width=1.5, tags="item")
        d = self.drag
        if d and d["mode"] == "draw":
            t = TOOL_TO_TYPE[d["tool"]]
            self.draw_item({"id": 0, "type": t, "pts": d["pts"], "width": TOOL_WIDTH[t],
                            "color": self.color})
        elif d and d["mode"] == "marquee":
            c.create_rectangle(d["x0"], d["y0"], d["x1"], d["y1"], outline=ACCENT, dash=(3, 3), tags="item")
        self.update_status()

    # ---------- Mouse ----------
    def cx(self, e):
        return self.canvas.canvasx(e.x)

    def cy(self, e):
        return self.canvas.canvasy(e.y)

    def on_press(self, e):
        self.canvas.focus_set()
        x, y = self.cx(e), self.cy(e)
        shift = bool(e.state & 0x0001)
        if self.tool == "note":
            self.new_note(x, y)
            return
        if self.tool == "select":
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
        if d["mode"] == "move":
            dx, dy = x - d["x0"], y - d["y0"]
            if dx or dy:
                for it in self.items:
                    if it["id"] in self.sel and not it.get("pinned"):
                        self.move_item(it, dx, dy)
                d["x0"], d["y0"] = x, y
                d["moved"] = True
        elif d["mode"] == "marquee":
            d["x1"], d["y1"] = x, y
        elif d["mode"] == "draw":
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
        if d["mode"] == "move":
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
            new = simpledialog.askstring("Edit note", "Text:", initialvalue=it["text"], parent=self.root)
            if new is not None and new != it["text"]:
                pre = self.snapshot()
                it["text"] = new
                self.push_undo(pre)
                self.redraw()

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

    def scale_selected(self, f):
        imgs = [it for it in self.items if it["id"] in self.sel and it["type"] == "image"]
        if not imgs:
            return
        pre = self.snapshot()
        for it in imgs:
            it["w"] = max(40, round(it["w"] * f))
            it["h"] = it["pil"].height * it["w"] / it["pil"].width
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
        return c.canvasx(c.winfo_width() / 2), c.canvasy(c.winfo_height() / 2)

    def paste(self):
        # 1. An image on the system clipboard (screenshots, images copied from a browser)
        if HAS_PIL and ImageGrab is not None:
            try:
                grabbed = ImageGrab.grabclipboard()
            except Exception:
                grabbed = None
            if isinstance(grabbed, Image.Image):
                self.place_image(grabbed.convert("RGBA"), *self.view_center())
                return
            if isinstance(grabbed, list):
                for path in grabbed:
                    try:
                        self.place_image(Image.open(path).convert("RGBA"), *self.view_center())
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
            x, y = self.view_center()
            pre = self.snapshot()
            it = {"id": self.new_id(), "type": "text", "x": round(x), "y": round(y), "text": text.strip(),
                  "color": self.color, "pinned": False}
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
              "w": w, "h": h, "pil": pil, "pinned": False}
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
        d = {k: v for k, v in it.items() if k not in ("tk", "tkw", "pil")}
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
