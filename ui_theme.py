"""Dark UI theme shared with the other Fathom game tools.

The palette, type scale and custom chrome come from fathomhellmapper1 and
FathomMiniMap: a near-black background (#14181d), slightly lighter panels
(#1c222a), a darker title/status bar (#101418), Segoe UI labels, Consolas
for data, a thin custom title bar with drag + close, flat buttons, and slim
canvas scrollbars instead of the native Windows ones. Keeping Lute on the
same tokens makes the three tools look like one family.

Only tk (not ttk) widgets are styled here except the two Treeviews, which
are ttk and restyled through the "clam" theme (the Windows themes ignore
most colour options).
"""
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ----------------------------------------------------------------- palette
BG = "#14181d"          # window / canvas background
PANEL = "#1c222a"       # list and panel background
BAR = "#101418"         # title bar, status bar, inputs
FG = "#e8e8e8"          # primary text
DIM = "#8d99a6"         # secondary text / labels
HOVER = "#2d3640"       # button background, borders
ACCENT = "#3a4a2a"      # active toggle / selection
FOCUS = "#5a7a4a"       # focused border, playable-range outline
ERROR = "#ff8080"
WARN = "#ffb347"
GOOD = "#7CFC00"        # playing / progress
TITLE = "#ffd24a"       # emphasised titles and the play-start marker
SELECT = "#ffffff"      # selected row text

SCROLL_THICK = 10
SCROLL_PAD = 2
SCROLL_THUMB_MIN = 24
SCROLL_TRACK = BAR
SCROLL_THUMB = "#3a4a5b"
SCROLL_THUMB_HOVER = "#5d748c"

# --------------------------------------------------------------- type scale
FONT = ("Segoe UI", 9)
SMALL = ("Segoe UI", 8)
TINY = ("Segoe UI", 7)
HEADING = ("Segoe UI", 7, "bold")
BOLD = ("Segoe UI", 8, "bold")
DISPLAY = ("Segoe UI", 12, "bold")
BIG = ("Segoe UI", 14, "bold")
MONO = ("Consolas", 8)
MONO_SMALL = ("Consolas", 7)

TITLE_HEIGHT = 22
STATUS_HEIGHT = 20


# ------------------------------------------------------------------ helpers
def rgb(widget, color):
    """A Tk colour name as 8-bit (r, g, b)."""
    return tuple(v // 257 for v in widget.winfo_rgb(color))


def darken(widget, color, factor):
    r, g, b = rgb(widget, color)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def blend(widget, color, other, amount):
    """Mix `color` toward `other`; amount 0 keeps color, 1 gives other."""
    a = rgb(widget, color)
    b = rgb(widget, other)
    t = max(0.0, min(1.0, amount))
    return "#%02x%02x%02x" % tuple(
        int(round(x + (y - x) * t)) for x, y in zip(a, b))


def bind_drag(win, *widgets):
    """Make the given title-bar widgets drag the borderless window."""
    def press(event):
        win._bar_off = (event.x_root - win.winfo_x(),
                        event.y_root - win.winfo_y())

    def drag(event):
        ox, oy = win._bar_off
        win.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    for widget in widgets:
        widget.bind("<ButtonPress-1>", press)
        widget.bind("<B1-Motion>", drag)


def title_bar(win, title, on_close, subtitle=None):
    """Build the custom title bar.

    Returns (bar, bar_bg, add_button). Buttons added through `add_button`
    sit to the left of the close glyph, in call order.
    """
    bar_bg = darken(win, BG, 0.82)
    bar = tk.Frame(win, bg=bar_bg, height=TITLE_HEIGHT)
    bar.pack(fill="x")
    bar.pack_propagate(False)

    name = tk.Label(bar, text=title, bg=bar_bg, fg=FG, font=BOLD, padx=8)
    name.pack(side="left")
    drag_widgets = [bar, name]
    if subtitle:
        sub = tk.Label(bar, text=subtitle, bg=bar_bg, fg=DIM, font=TINY,
                       padx=2)
        sub.pack(side="left")
        drag_widgets.append(sub)

    close = tk.Label(bar, text="✕", bg=bar_bg, fg=FG, font=SMALL, padx=9)
    close.pack(side="right", fill="y")
    close.bind("<Button-1>", lambda _e: on_close())
    close.bind("<Enter>", lambda _e: close.config(bg="#c42b1c", fg="white"))
    close.bind("<Leave>", lambda _e: close.config(bg=bar_bg, fg=FG))

    def add_button(text, command, is_on=None):
        btn = tk.Label(bar, text=text, bg=bar_bg, fg=FG, font=TINY, padx=8)
        btn.pack(side="right", fill="y")
        btn.is_on = is_on
        btn.bind("<Button-1>", lambda _e: command())
        btn.bind("<Enter>", lambda _e: btn.config(bg=HOVER))
        btn.bind("<Leave>", lambda _e: restyle(btn))
        restyle(btn)
        return btn

    def restyle(btn):
        on = btn.is_on() if callable(btn.is_on) else bool(btn.is_on)
        btn.config(bg=ACCENT if on else bar_bg)

    bind_drag(win, *drag_widgets)
    return bar, bar_bg, add_button


def panel(master, title=None):
    """A flat dark panel with a small uppercase heading.

    Returns (frame, head, body): pack/grid `frame`, put controls in `head`
    and content in `body`.
    """
    frame = tk.Frame(master, bg=PANEL)
    head = tk.Frame(frame, bg=PANEL)
    head.pack(fill="x", padx=8, pady=(7, 3))
    if title:
        tk.Label(head, text=title.upper(), bg=PANEL, fg=DIM, font=HEADING,
                 anchor="w").pack(side="left")
    body = tk.Frame(frame, bg=PANEL)
    body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    return frame, head, body


def flat_button(parent, text, command, width=None, font=SMALL, bg=HOVER,
                padx=8, pady=2):
    """Reference-style flat button (dark face, olive active state)."""
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=FG,
                    activebackground=ACCENT, activeforeground=FG,
                    relief="flat", bd=0, highlightthickness=0,
                    cursor="hand2", font=font, padx=padx, pady=pady)
    if width is not None:
        btn.configure(width=width)
    hover = blend(btn, bg, FG, 0.10)
    btn.bind("<Enter>", lambda _e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda _e: btn.config(bg=bg))
    return btn


class Toggle(tk.Label):
    """Flat on/off toggle in the title-bar toggle style.

    Tk's native checkbutton indicator keeps its light system colours on
    Windows, so the tools use labels: olive accent while on, panel colour
    while off, hover highlight, and a box glyph for the state.
    """

    def __init__(self, master, text, variable, command=None, bg=PANEL,
                 **kwargs):
        super().__init__(master, text=text, bg=bg, fg=DIM, font=SMALL,
                         padx=8, pady=1, cursor="hand2", **kwargs)
        self.variable = variable
        self.command = command
        self._label = text
        self._base = bg
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self._paint(True))
        self.bind("<Leave>", lambda _e: self._paint(False))
        self._paint(False)

    def _click(self, _event):
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()
        self._paint(True)

    def _paint(self, hover):
        on = bool(self.variable.get())
        text = ("☑ " if on else "☐ ") + self._label
        if on:
            self.config(bg=ACCENT, fg=FG, text=text)
        elif hover:
            self.config(bg=HOVER, fg=FG, text=text)
        else:
            self.config(bg=self._base, fg=DIM, text=text)


def resize_grip(parent, on_press, on_drag, bg=BAR):
    """Bottom-right resize grip for borderless windows."""
    grip = tk.Label(parent, text="◢", bg=bg, fg=DIM, font=SMALL,
                    cursor="size_nw_se", bd=0, padx=4, pady=0)
    grip.place(relx=1.0, rely=1.0, anchor="se")
    grip.bind("<ButtonPress-1>", on_press)
    grip.bind("<B1-Motion>", on_drag)
    return grip


# ------------------------------------------------------------------ treeview
def style_widgets(root):
    """Apply the dark Treeview styles and the base fonts."""
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                 "TkHeadingFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family="Segoe UI", size=9)
        except tk.TclError:
            pass

    style.configure("Treeview",
                    background=PANEL, fieldbackground=PANEL, foreground=FG,
                    bordercolor=HOVER, lightcolor=PANEL, darkcolor=PANEL,
                    borderwidth=1, relief="flat",
                    rowheight=22, font=MONO)
    style.map("Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", SELECT)])
    style.configure("Treeview.Heading",
                    background=BAR, foreground=DIM,
                    bordercolor=HOVER, lightcolor=BAR, darkcolor=BAR,
                    relief="flat", padding=(4, 3), font=HEADING)
    style.map("Treeview.Heading",
              background=[("active", HOVER)],
              foreground=[("active", FG)])
    style.configure("Parts.Treeview", rowheight=21, font=MONO)
    style.map("Parts.Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", SELECT)])

    # Spinboxes come from ttk (the tk one keeps light system arrow buttons
    # on Windows); clam honours these colours.
    style.configure("Dark.TSpinbox",
                    fieldbackground=BAR, foreground=FG, background=HOVER,
                    arrowcolor=DIM, bordercolor=HOVER, lightcolor=BAR,
                    darkcolor=BAR, insertcolor=FG, padding=(2, 1),
                    font=MONO)
    style.map("Dark.TSpinbox",
              fieldbackground=[("readonly", BAR), ("disabled", PANEL)],
              foreground=[("disabled", DIM)],
              arrowcolor=[("active", FG)],
              bordercolor=[("focus", FOCUS)],
              lightcolor=[("focus", FOCUS)],
              darkcolor=[("focus", FOCUS)])


# ------------------------------------------------------------------ scrollbar
class ThemedScrollbar(tk.Canvas):
    """Slim rounded scrollbar, drop-in for a tk/ttk Scrollbar.

    Ported from fathomhellmapper1/ui_widgets.ThemedScrollbar and extended
    with `orient="horizontal"` for the parts table. The trough is the bar
    colour; the thumb is a rounded capsule that brightens on hover/drag and
    is not drawn at all when the content already fits.
    """

    def __init__(self, master, orient="vertical", command=None,
                 thickness=SCROLL_THICK, bg=SCROLL_TRACK, **kwargs):
        self.orient = orient
        self._vertical = orient == "vertical"
        if self._vertical:
            super().__init__(master, width=thickness, height=1, bg=bg,
                             highlightthickness=0, bd=0, **kwargs)
        else:
            super().__init__(master, width=1, height=thickness, bg=bg,
                             highlightthickness=0, bd=0, **kwargs)
        self._command = command if command is not None else (lambda *a: None)
        self._first = 0.0
        self._last = 1.0
        self._hover = False
        self._dragging = False
        self._drag_offset = 0.0
        self._drag_len = 0.0
        self._drag_span = 1.0

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self._draw()

    # ------------------------------------------------------------- scroll API
    def set(self, first, last):
        try:
            self._first = min(max(float(first), 0.0), 1.0)
            self._last = min(max(float(last), 0.0), 1.0)
        except (TypeError, ValueError):
            return
        self._draw()

    def _scrollable(self):
        return not (self._first <= 0.001 and self._last >= 0.999)

    def _length(self):
        return max(1, int(self.winfo_height() if self._vertical
                          else self.winfo_width()))

    def _cross(self):
        return max(1, int(self.winfo_width() if self._vertical
                          else self.winfo_height()))

    def _coord(self, event):
        return event.y if self._vertical else event.x

    # --------------------------------------------------------------- events
    def _on_enter(self, _event):
        self._hover = True
        self._draw()

    def _on_leave(self, _event):
        self._hover = False
        self._draw()

    def _on_wheel(self, event):
        units = -1 if event.delta > 0 else 1
        self._command("scroll", units, "units")
        return "break"

    def _on_press(self, event):
        if not self._scrollable():
            return
        pos = self._coord(event)
        p0, p1 = self._thumb_geometry()
        if p0 <= pos <= p1:
            self._dragging = True
            self._drag_offset = pos - p0
            self._drag_len = max(1.0, p1 - p0)
            self._drag_span = max(1.0, self._length() - 2 * SCROLL_PAD)
            self._draw()
        else:
            self._command("scroll", -1 if pos < p0 else 1, "pages")

    def _on_drag(self, event):
        if not self._dragging:
            return
        span = max(1.0, self._drag_span - self._drag_len)
        frac = (self._coord(event) - self._drag_offset - SCROLL_PAD) / span
        self._command("moveto", min(max(frac, 0.0), 1.0))

    def _on_release(self, _event):
        if self._dragging:
            self._dragging = False
            self._draw()

    # -------------------------------------------------------------- drawing
    def _thumb_geometry(self):
        length = self._length()
        top = float(SCROLL_PAD)
        bottom = max(top + 1.0, length - SCROLL_PAD)
        span = bottom - top
        if self._last <= self._first:
            p0 = p1 = top
        else:
            p0 = top + self._first * span
            p1 = top + self._last * span
        smallest = min(span, float(SCROLL_THUMB_MIN))
        if p1 - p0 < smallest:
            mid = (p0 + p1) / 2.0
            p0 = mid - smallest / 2.0
            p1 = mid + smallest / 2.0
            if p0 < top:
                p1 += top - p0
                p0 = top
            if p1 > bottom:
                p0 -= p1 - bottom
                p1 = bottom
        return p0, max(p0 + 2.0, p1)

    def _draw(self):
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if width <= 2 or height <= 2:
            return
        self.create_rectangle(0, 0, width, height, fill=SCROLL_TRACK,
                              outline="")
        if not self._scrollable():
            return
        p0, p1 = self._thumb_geometry()
        thick = max(2.0, self._cross() - 4.0)
        mid = self._cross() / 2.0
        color = (SCROLL_THUMB_HOVER
                 if (self._dragging or self._hover) else SCROLL_THUMB)
        if p1 - p0 <= thick:
            c = (p0 + p1) / 2.0
            if self._vertical:
                self.create_oval(mid - thick / 2.0, c - thick / 2.0,
                                 mid + thick / 2.0, c + thick / 2.0,
                                 fill=color, outline="")
            else:
                self.create_oval(c - thick / 2.0, mid - thick / 2.0,
                                 c + thick / 2.0, mid + thick / 2.0,
                                 fill=color, outline="")
        elif self._vertical:
            self.create_line(mid, p0 + thick / 2.0, mid, p1 - thick / 2.0,
                             fill=color, width=thick, capstyle="round")
        else:
            self.create_line(p0 + thick / 2.0, mid, p1 - thick / 2.0, mid,
                             fill=color, width=thick, capstyle="round")


# ------------------------------------------------------------- taskbar role
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int,
                                       ctypes.c_long]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.SetWindowPos.restype = wintypes.BOOL


def _apply_taskbar(win, add, remove):
    if sys.platform != "win32":
        return
    try:
        hwnd = win.winfo_id()
        wrapper = _user32.GetParent(hwnd) or hwnd
        style = _user32.GetWindowLongW(wrapper, _GWL_EXSTYLE) & 0xFFFFFFFF
        style = (style | add) & ~remove
        _user32.SetWindowLongW(wrapper, _GWL_EXSTYLE, style)
        _user32.SetWindowPos(wrapper, None, 0, 0, 0, 0, _SWP)
    except (tk.TclError, OSError):
        pass


def pin_taskbar(win):
    """Let the shell list a borderless window on the taskbar/Alt+Tab.

    Tk creates borderless toplevels as tool windows, which the shell hides.
    The style is re-applied on every map because Tk resets it then.
    """
    def on_map(event):
        if event.widget is win:
            _apply_taskbar(win, _WS_EX_APPWINDOW, _WS_EX_TOOLWINDOW)

    win.bind("<Map>", on_map, add="+")
    if win.winfo_ismapped():
        _apply_taskbar(win, _WS_EX_APPWINDOW, _WS_EX_TOOLWINDOW)
