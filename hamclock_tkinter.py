"""HamClock Lite native Tkinter client.

A minimal-dependency native GUI that replaces the browser-based HamClock Lite
dashboard on Raspberry Pi 1 Model B (700 MHz ARMv6, 512 MB RAM). Fetches data
from the existing HamClock server at http://localhost:8080/api/* via the
shared hamclock_data.HamClockData class and renders the dashboard using
native Tkinter widgets, saving significant RAM/CPU vs. a browser stack.

Apt dependencies (Raspberry Pi OS):
    sudo apt install python3-tk python3-pil python3-pil.imagetk

Tkinter's built-in PhotoImage handles GIF/PGM/PNG but NOT JPEG, so Pillow
(PIL) is used for image decoding. If Pillow is unavailable, the image panels
are hidden gracefully and the rest of the dashboard still works.

Usage:
    python3 hamclock_tkinter.py

Press Escape to exit fullscreen.

Target viewport: 1440x900 fullscreen (scales gracefully on smaller screens).
"""

import io
import time
import tkinter as tk
from tkinter import ttk

from hamclock_data import HamClockData

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:  # Pillow missing — degrade image panels gracefully
    HAS_PIL = False


# ---------- Theme (K-State royal purple + gold) ----------
BG = '#2a1450'
CARD = '#3a1d65'
BORDER = '#512888'
TEXT = '#e8ddf5'
LABEL = '#b8a0d8'
BRIGHT = '#ffffff'
ACCENT_GOLD = '#f4c55c'

COND_COLORS = {
    'Good': '#22c55e',
    'Fair': '#eab308',
    'Poor': '#ef4444',
    'N/A': '#4a5568',
}

BAND_COLORS = {
    '160m': '#ff6b6b', '80m': '#f06595', '60m': '#cc5de8', '40m': '#845ef7',
    '30m': '#5c7cfa', '20m': '#339af0', '17m': '#22b8cf', '15m': '#20c997',
    '12m': '#51cf66', '10m': '#94d82d',
}
BAND_ORDER = ['160m', '80m', '60m', '40m', '30m', '20m', '17m', '15m', '12m', '10m']

# Fonts — DejaVu Sans Mono is standard on Raspberry Pi OS.
FONT_TITLE = ('DejaVu Sans Mono', 12, 'bold')
FONT_BODY = ('DejaVu Sans Mono', 11)
FONT_VALUE = ('DejaVu Sans Mono', 11, 'bold')
FONT_LABEL = ('DejaVu Sans Mono', 9)
FONT_HEADER = ('DejaVu Sans Mono', 18, 'bold')
FONT_CLOCK = ('DejaVu Sans Mono', 13, 'bold')


def _safe(v, default='—'):
    """Return str(v) or placeholder if v is empty/None/'N/A'."""
    if v is None:
        return default
    s = str(v).strip()
    if not s or s.upper() == 'N/A':
        return default
    return s


def _make_panel(parent, title):
    """Create a titled card Frame; return (outer, body) where body holds content."""
    outer = tk.Frame(
        parent, bg=CARD, bd=1, relief='solid',
        highlightbackground=BORDER, highlightthickness=1,
    )
    header = tk.Label(
        outer, text=title, bg=BORDER, fg=ACCENT_GOLD,
        font=FONT_TITLE, anchor='w', padx=8, pady=3,
    )
    header.pack(side='top', fill='x')
    body = tk.Frame(outer, bg=CARD, padx=8, pady=6)
    body.pack(side='top', fill='both', expand=True)
    return outer, body


def _kv_row(body, row, label, initial='—'):
    """Place a label/value pair in a 2-column grid row. Returns the value Label."""
    tk.Label(
        body, text=label, bg=CARD, fg=LABEL, font=FONT_LABEL,
        anchor='w',
    ).grid(row=row, column=0, sticky='w', padx=(0, 6))
    val = tk.Label(
        body, text=initial, bg=CARD, fg=BRIGHT, font=FONT_VALUE,
        anchor='e',
    )
    val.grid(row=row, column=1, sticky='e')
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=0)
    return val


class HamClockTkApp:
    """Native Tkinter HamClock Lite dashboard."""

    def __init__(self, root):
        self.root = root
        self.data = HamClockData()
        self.data.start_background()

        root.configure(bg=BG)
        root.title('HamClock Lite')
        root.geometry('1440x900')
        try:
            root.attributes('-fullscreen', True)
        except Exception:
            pass
        root.bind('<Escape>', lambda _e: root.destroy())
        root.bind('<F11>', self._toggle_fullscreen)

        # ttk theme for Treeview / Notebook
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure(
            'HC.Treeview',
            background=CARD, foreground=TEXT, fieldbackground=CARD,
            rowheight=18, borderwidth=0, font=FONT_LABEL,
        )
        style.configure(
            'HC.Treeview.Heading',
            background=BORDER, foreground=ACCENT_GOLD, font=FONT_LABEL,
        )
        style.map('HC.Treeview', background=[('selected', BORDER)])
        style.configure('HC.TNotebook', background=CARD, borderwidth=0)
        style.configure(
            'HC.TNotebook.Tab',
            background=CARD, foreground=LABEL,
            padding=[8, 3], font=FONT_LABEL,
        )
        style.map(
            'HC.TNotebook.Tab',
            background=[('selected', BORDER)],
            foreground=[('selected', ACCENT_GOLD)],
        )

        self._value_labels = {}
        self._last_image_ts = 0
        self._image_refs = {}  # hold refs to prevent GC

        self._build_ui()
        self._update_ui()

    def _toggle_fullscreen(self, _e=None):
        try:
            cur = bool(self.root.attributes('-fullscreen'))
            self.root.attributes('-fullscreen', not cur)
        except Exception:
            pass

    # ----- UI construction -----
    def _build_ui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        for c in range(3):
            self.root.grid_columnconfigure(c, weight=1, uniform='col')

        # --- Header bar ---
        header = tk.Frame(self.root, bg=BORDER, bd=0)
        header.grid(row=0, column=0, columnspan=3, sticky='ew', padx=4, pady=(4, 2))
        tk.Label(
            header, text='HAMCLOCK LITE', bg=BORDER, fg=ACCENT_GOLD,
            font=FONT_HEADER, padx=10, pady=6,
        ).pack(side='left')
        tk.Label(
            header, text='W0QQQ', bg=BORDER, fg=TEXT, font=FONT_BODY,
        ).pack(side='left', padx=(4, 10))
        self.status_dot = tk.Label(
            header, text='\u25cf', bg=BORDER, fg='#ef4444',
            font=FONT_HEADER,
        )
        self.status_dot.pack(side='right', padx=8)
        self.local_lbl = tk.Label(
            header, text='LOCAL --:--:--', bg=BORDER, fg=TEXT, font=FONT_CLOCK,
        )
        self.local_lbl.pack(side='right', padx=10)
        self.utc_lbl = tk.Label(
            header, text='UTC --:--:--', bg=BORDER, fg=BRIGHT, font=FONT_CLOCK,
        )
        self.utc_lbl.pack(side='right', padx=10)

        # --- Columns ---
        col_left = tk.Frame(self.root, bg=BG)
        col_mid = tk.Frame(self.root, bg=BG)
        col_right = tk.Frame(self.root, bg=BG)
        col_left.grid(row=1, column=0, sticky='nsew', padx=4, pady=2)
        col_mid.grid(row=1, column=1, sticky='nsew', padx=4, pady=2)
        col_right.grid(row=1, column=2, sticky='nsew', padx=4, pady=2)

        self._build_left_column(col_left)
        self._build_middle_column(col_mid)
        self._build_right_column(col_right)

        # --- Status bar ---
        self.status_bar = tk.Label(
            self.root, text='Solar:— Bands:— DX:—',
            bg=BORDER, fg=LABEL, font=FONT_LABEL, anchor='w', padx=8, pady=2,
        )
        self.status_bar.grid(row=2, column=0, columnspan=3, sticky='ew', padx=4, pady=(2, 4))

    def _build_left_column(self, col):
        # SOLAR
        solar_p, solar_b = _make_panel(col, 'SOLAR')
        solar_p.pack(fill='x', pady=(0, 4))
        for i, (k, lbl) in enumerate([
            ('sfi', 'SFI'), ('ssn', 'SSN'), ('aIndex', 'A-Index'),
            ('kIndex', 'K-Index'), ('xray', 'X-Ray'), ('solarWind', 'Solar Wind'),
            ('protonFlux', 'Proton Flux'), ('aurora', 'Aurora'),
        ]):
            self._value_labels['solar_' + k] = _kv_row(solar_b, i, lbl)

        # BANDS
        bands_p, bands_b = _make_panel(col, 'BANDS')
        bands_p.pack(fill='x', pady=4)
        tk.Label(bands_b, text='BAND', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='w').grid(row=0, column=0, sticky='w', padx=(0, 8))
        tk.Label(bands_b, text='DAY', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='center').grid(row=0, column=1, sticky='ew', padx=4)
        tk.Label(bands_b, text='NIGHT', bg=CARD, fg=LABEL, font=FONT_LABEL,
                 anchor='center').grid(row=0, column=2, sticky='ew', padx=4)
        bands_b.grid_columnconfigure(0, weight=1)
        bands_b.grid_columnconfigure(1, weight=0, minsize=60)
        bands_b.grid_columnconfigure(2, weight=0, minsize=60)
        self._band_rows = {}
        for i, band in enumerate(['80m-40m', '30m-20m', '17m-15m', '12m-10m'], start=1):
            tk.Label(bands_b, text=band, bg=CARD, fg=TEXT, font=FONT_BODY,
                     anchor='w').grid(row=i, column=0, sticky='w', padx=(0, 8), pady=1)
            day = tk.Label(bands_b, text='—', bg=COND_COLORS['N/A'], fg=BRIGHT,
                           font=FONT_LABEL, width=7)
            day.grid(row=i, column=1, sticky='ew', padx=2, pady=1)
            night = tk.Label(bands_b, text='—', bg=COND_COLORS['N/A'], fg=BRIGHT,
                             font=FONT_LABEL, width=7)
            night.grid(row=i, column=2, sticky='ew', padx=2, pady=1)
            self._band_rows[band] = (day, night)

        # SDO IMAGE
        sdo_p, sdo_b = _make_panel(col, 'SDO IMAGE')
        sdo_p.pack(fill='x', pady=4)
        self.sdo_label = tk.Label(
            sdo_b, text='(image unavailable)' if not HAS_PIL else '(loading...)',
            bg=CARD, fg=LABEL, font=FONT_LABEL,
        )
        self.sdo_label.pack()

        # GEOMAGNETIC (Kp bar)
        geo_p, geo_b = _make_panel(col, 'GEOMAGNETIC')
        geo_p.pack(fill='x', pady=4)
        self.kp_value = tk.Label(geo_b, text='Kp —', bg=CARD, fg=BRIGHT,
                                 font=FONT_VALUE)
        self.kp_value.pack(anchor='w')
        self.kp_canvas = tk.Canvas(geo_b, height=14, bg=CARD, bd=0,
                                   highlightthickness=0)
        self.kp_canvas.pack(fill='x', pady=(2, 0))

        # X-RAY bar
        xray_p, xray_b = _make_panel(col, 'X-RAY')
        xray_p.pack(fill='x', pady=4)
        self.xray_value = tk.Label(xray_b, text='—', bg=CARD, fg=BRIGHT,
                                   font=FONT_VALUE)
        self.xray_value.pack(anchor='w')
        self.xray_canvas = tk.Canvas(xray_b, height=14, bg=CARD, bd=0,
                                     highlightthickness=0)
        self.xray_canvas.pack(fill='x', pady=(2, 0))

        # OPEN BANDS
        open_p, open_b = _make_panel(col, 'OPEN BANDS')
        open_p.pack(fill='x', pady=(4, 0))
        self.open_lbl = tk.Label(
            open_b, text='OPEN: —', bg=CARD, fg='#22c55e', font=FONT_BODY,
            anchor='w', justify='left', wraplength=360,
        )
        self.open_lbl.pack(anchor='w', fill='x')
        self.closed_lbl = tk.Label(
            open_b, text='CLOSED: —', bg=CARD, fg='#ef4444', font=FONT_BODY,
            anchor='w', justify='left', wraplength=360,
        )
        self.closed_lbl.pack(anchor='w', fill='x')

    def _build_middle_column(self, col):
        muf_p, muf_b = _make_panel(col, 'MUF STATUS')
        muf_p.pack(fill='x', pady=(0, 4))
        for i, (k, lbl) in enumerate([
            ('fof2', 'foF2 (MHz)'),
            ('geomagField', 'Geomag Field'),
            ('kIndex', 'K-Index'),
            ('sfi', 'SFI'),
            ('ssn', 'SSN'),
            ('heliumLine', 'Helium Line'),
            ('signalNoise', 'Signal/Noise'),
            ('magneticField', 'Magnetic Field'),
        ]):
            self._value_labels['muf_' + k] = _kv_row(muf_b, i, lbl)

        # Info / update panel
        info_p, info_b = _make_panel(col, 'STATION')
        info_p.pack(fill='both', expand=True, pady=4)
        self.updated_lbl = tk.Label(
            info_b, text='Updated: —', bg=CARD, fg=LABEL, font=FONT_LABEL,
            anchor='w', justify='left', wraplength=360,
        )
        self.updated_lbl.pack(anchor='w', fill='x', pady=(0, 4))
        self.server_lbl = tk.Label(
            info_b, text='Server: ' + self.data.server_url, bg=CARD, fg=LABEL,
            font=FONT_LABEL, anchor='w',
        )
        self.server_lbl.pack(anchor='w', fill='x')
        self.errors_lbl = tk.Label(
            info_b, text='', bg=CARD, fg='#ef4444', font=FONT_LABEL,
            anchor='nw', justify='left', wraplength=360,
        )
        self.errors_lbl.pack(anchor='w', fill='x', pady=(6, 0))

    def _build_right_column(self, col):
        # DX SPOTS (Treeview)
        dx_p, dx_b = _make_panel(col, 'DX SPOTS')
        dx_p.pack(fill='x', pady=(0, 4))
        cols = ('freq', 'band', 'dx', 'de', 'utc')
        self.dx_tree = ttk.Treeview(
            dx_b, columns=cols, show='headings', height=8, style='HC.Treeview',
        )
        widths = {'freq': 70, 'band': 50, 'dx': 90, 'de': 90, 'utc': 50}
        for c in cols:
            self.dx_tree.heading(c, text=c.upper())
            self.dx_tree.column(c, width=widths[c], anchor='w', stretch=True)
        self.dx_tree.pack(fill='both', expand=True)

        # BAND ACTIVITY — Canvas bars
        act_p, act_b = _make_panel(col, 'BAND ACTIVITY')
        act_p.pack(fill='x', pady=4)
        self.activity_canvas = tk.Canvas(
            act_b, height=180, bg=CARD, bd=0, highlightthickness=0,
        )
        self.activity_canvas.pack(fill='x')

        # PROPAGATION — ttk.Notebook with tabs for DRAP/AURORA/ENLIL
        prop_p, prop_b = _make_panel(col, 'PROPAGATION')
        prop_p.pack(fill='both', expand=True, pady=(4, 0))
        self.prop_nb = ttk.Notebook(prop_b, style='HC.TNotebook')
        self.prop_nb.pack(fill='both', expand=True)
        self.prop_tabs = {}
        for key, title in [('real-drap', 'DRAP'), ('drap', 'AURORA'),
                           ('enlil', 'ENLIL')]:
            frame = tk.Frame(self.prop_nb, bg=CARD)
            lbl = tk.Label(
                frame, text='(loading...)' if HAS_PIL else '(PIL missing)',
                bg=CARD, fg=LABEL, font=FONT_LABEL,
            )
            lbl.pack(expand=True)
            self.prop_nb.add(frame, text=title)
            self.prop_tabs[key] = lbl

    # ----- Image helpers -----
    def _load_image(self, data_bytes, max_w, max_h):
        if not data_bytes or not HAS_PIL:
            return None
        try:
            img = Image.open(io.BytesIO(data_bytes))
            img.thumbnail((max_w, max_h), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _set_image(self, label, key, photo):
        """Assign photo to label; hold ref to prevent GC."""
        if photo is None:
            return
        self._image_refs[key] = photo
        label.configure(image=photo, text='')
        label.image_ref = photo  # belt and suspenders

    # ----- Update loop -----
    def _update_ui(self):
        try:
            self._update_clocks()
            self._update_solar()
            self._update_muf()
            self._update_bands()
            self._update_dxspots()
            self._update_band_activity()
            self._update_open_closed()
            self._update_images()
            self._update_status()
        except Exception as e:
            try:
                self.status_bar.configure(text='update error: {}'.format(e))
            except Exception:
                pass
        self.root.after(1000, self._update_ui)

    def _update_clocks(self):
        now = time.time()
        self.utc_lbl.configure(text='UTC ' + time.strftime('%H:%M:%S', time.gmtime(now)))
        self.local_lbl.configure(text='LOCAL ' + time.strftime('%H:%M:%S', time.localtime(now)))
        ok = bool(self.data.last_data_refresh) and (now - self.data.last_data_refresh) < 180
        self.status_dot.configure(fg='#22c55e' if ok else '#ef4444')

    def _update_solar(self):
        s = self.data.solar or {}
        for key in ['sfi', 'ssn', 'aIndex', 'kIndex', 'xray', 'solarWind',
                    'protonFlux', 'aurora']:
            self._value_labels['solar_' + key].configure(text=_safe(s.get(key)))

        # Kp bar (0-9 scale)
        kp_raw = s.get('kIndex')
        try:
            kp = float(kp_raw)
        except (TypeError, ValueError):
            kp = None
        self.kp_value.configure(text='Kp ' + (_safe(kp_raw)))
        self._draw_bar(self.kp_canvas, kp, 9.0,
                       ['#22c55e', '#22c55e', '#22c55e', '#22c55e',
                        '#eab308', '#eab308', '#ef4444', '#ef4444',
                        '#ef4444', '#ef4444'])

        # X-Ray bar
        xray_raw = s.get('xray') or ''
        self.xray_value.configure(text=_safe(xray_raw))
        xv = self._xray_to_scalar(xray_raw)
        self._draw_bar(self.xray_canvas, xv, 5.0,
                       ['#22c55e', '#84cc16', '#eab308', '#f97316', '#ef4444'])

    def _xray_to_scalar(self, xray):
        """Convert NOAA xray class (e.g. 'B4.0', 'M1.5', 'X2.0') to 0..5 scalar."""
        if not xray or len(xray) < 2:
            return None
        cls = xray[0].upper()
        try:
            mag = float(xray[1:])
        except ValueError:
            mag = 1.0
        # Normalize within class: 1-9 → 0..1
        frac = max(0.0, min(1.0, (mag - 1.0) / 8.0))
        base = {'A': 0, 'B': 1, 'C': 2, 'M': 3, 'X': 4}.get(cls, 0)
        return base + frac

    def _draw_bar(self, canvas, value, max_val, gradient_colors):
        canvas.delete('all')
        w = int(canvas.winfo_width()) or 360
        h = int(canvas.winfo_height()) or 14
        canvas.create_rectangle(0, 0, w, h, fill='#1a0a30', outline=BORDER)
        if value is None or max_val <= 0:
            return
        frac = max(0.0, min(1.0, value / max_val))
        fill_w = int(w * frac)
        if fill_w < 1:
            return
        idx = min(len(gradient_colors) - 1, int(frac * len(gradient_colors)))
        canvas.create_rectangle(0, 0, fill_w, h,
                                fill=gradient_colors[idx], outline='')

    def _update_muf(self):
        s = self.data.solar or {}
        for key in ['fof2', 'geomagField', 'kIndex', 'sfi', 'ssn',
                    'heliumLine', 'signalNoise', 'magneticField']:
            self._value_labels['muf_' + key].configure(text=_safe(s.get(key)))
        self.updated_lbl.configure(text='Updated: ' + _safe(s.get('updated')))

    def _update_bands(self):
        b = self.data.bands or {}
        for band, (day_lbl, night_lbl) in self._band_rows.items():
            entry = b.get(band) or {}
            day = entry.get('day') or 'N/A'
            night = entry.get('night') or 'N/A'
            day_lbl.configure(text=day, bg=COND_COLORS.get(day, COND_COLORS['N/A']))
            night_lbl.configure(text=night, bg=COND_COLORS.get(night, COND_COLORS['N/A']))

    def _update_dxspots(self):
        spots = self.data.dxspots or []
        existing = self.dx_tree.get_children()
        if len(existing) != min(len(spots), 12):
            self.dx_tree.delete(*existing)
            existing = ()
        rows = spots[:12]
        if not existing:
            for sp in rows:
                utc = (sp.get('time') or '')[:4]
                self.dx_tree.insert('', 'end', values=(
                    _safe(sp.get('frequency')),
                    _safe(sp.get('band')),
                    _safe(sp.get('dx')),
                    _safe(sp.get('spotter')),
                    utc,
                ))
        else:
            for iid, sp in zip(existing, rows):
                utc = (sp.get('time') or '')[:4]
                self.dx_tree.item(iid, values=(
                    _safe(sp.get('frequency')),
                    _safe(sp.get('band')),
                    _safe(sp.get('dx')),
                    _safe(sp.get('spotter')),
                    utc,
                ))

    def _update_band_activity(self):
        canvas = self.activity_canvas
        canvas.delete('all')
        spots = self.data.dxspots or []
        counts = {}
        for sp in spots:
            band = sp.get('band')
            if band in BAND_COLORS:
                counts[band] = counts.get(band, 0) + 1
        max_count = max(counts.values()) if counts else 1

        w = int(canvas.winfo_width()) or 380
        h = int(canvas.winfo_height()) or 180
        rows = len(BAND_ORDER)
        row_h = max(12, h // rows)
        label_w = 44
        bar_x0 = label_w + 4
        bar_max = max(40, w - bar_x0 - 40)
        for i, band in enumerate(BAND_ORDER):
            y = i * row_h + 2
            canvas.create_text(
                4, y + row_h / 2 - 2, text=band, anchor='w',
                fill=LABEL, font=FONT_LABEL,
            )
            count = counts.get(band, 0)
            frac = count / max_count if max_count else 0
            bar_w = int(bar_max * frac)
            if bar_w > 0:
                canvas.create_rectangle(
                    bar_x0, y, bar_x0 + bar_w, y + row_h - 4,
                    fill=BAND_COLORS[band], outline='',
                )
            canvas.create_text(
                bar_x0 + bar_w + 4, y + row_h / 2 - 2,
                text=str(count), anchor='w', fill=TEXT, font=FONT_LABEL,
            )

    def _update_open_closed(self):
        b = self.data.bands or {}
        open_list = []
        closed_list = []
        for band, entry in b.items():
            if not isinstance(entry, dict):
                continue
            day = entry.get('day') or 'N/A'
            night = entry.get('night') or 'N/A'
            if day == 'Good' or night == 'Good':
                open_list.append(band)
            elif day == 'Poor' and night == 'Poor':
                closed_list.append(band)
        self.open_lbl.configure(
            text='OPEN: ' + (', '.join(open_list) if open_list else '—'),
        )
        self.closed_lbl.configure(
            text='CLOSED: ' + (', '.join(closed_list) if closed_list else '—'),
        )

    def _update_images(self):
        ts = self.data.last_image_refresh
        if ts == self._last_image_ts:
            return
        self._last_image_ts = ts
        imgs = self.data.images or {}

        sdo = self._load_image(imgs.get('solar-image'), 360, 220)
        if sdo is not None:
            self._set_image(self.sdo_label, 'sdo', sdo)
        elif not HAS_PIL:
            self.sdo_label.configure(text='(PIL missing)')

        for key, label in self.prop_tabs.items():
            photo = self._load_image(imgs.get(key), 380, 260)
            if photo is not None:
                self._set_image(label, 'prop_' + key, photo)
            elif not HAS_PIL:
                label.configure(text='(PIL missing)')
            else:
                label.configure(text='(no image)')

    def _update_status(self):
        now = time.time()
        d_age = int(now - self.data.last_data_refresh) if self.data.last_data_refresh else -1
        i_age = int(now - self.data.last_image_refresh) if self.data.last_image_refresh else -1
        def fmt(a):
            return '{}s'.format(a) if a >= 0 else '—'
        errs = [k for k, v in (self.data.errors or {}).items() if v]
        status = 'Data:{}  Images:{}  Spots:{}  Errors:{}'.format(
            fmt(d_age), fmt(i_age),
            len(self.data.dxspots or []), len(errs),
        )
        self.status_bar.configure(text=status)
        if errs:
            self.errors_lbl.configure(text='Errors: ' + ', '.join(errs[:3]))
        else:
            self.errors_lbl.configure(text='')


def main():
    root = tk.Tk()
    HamClockTkApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
