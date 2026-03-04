#!/usr/bin/env python3
"""
Multi-Folder Image Viewer
=========================
A tkinter-based GUI that lets you browse and compare images from multiple
folders side-by-side. Add folders via a dialog, navigate with arrow keys,
zoom with the scroll wheel, and pan by click-dragging.

Usage:
    python viewer.py
"""

import os
import re
import tkinter as tk
from tkinter import filedialog, font as tkfont
from PIL import Image, ImageTk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp",
}
PLACEHOLDER_COLOR = "#2e2e2e"
OVERLAY_BG = "#000000"
OVERLAY_FG = "#ffffff"
RESIZE_DEBOUNCE_MS = 150
MIN_ZOOM = 0.1
MAX_ZOOM = 20.0
ZOOM_STEP = 1.15  # multiply / divide per scroll tick


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def natural_sort_key(s: str):
    """Sort key that handles embedded numbers naturally (img2 < img10)."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", s)
    ]


def scan_folder(path: str) -> list[str]:
    """Return a naturally-sorted list of image file paths in *path*."""
    try:
        entries = os.listdir(path)
    except OSError:
        return []
    images = [
        os.path.join(path, e)
        for e in entries
        if os.path.splitext(e)[1].lower() in IMAGE_EXTENSIONS
           and os.path.isfile(os.path.join(path, e))
    ]
    images.sort(key=lambda p: natural_sort_key(os.path.basename(p)))
    return images


# ---------------------------------------------------------------------------
# ImageTile – single canvas that displays one image with zoom / pan
# ---------------------------------------------------------------------------
class ImageTile(tk.Canvas):
    """Canvas widget that displays an image with optional zoom & pan."""

    def __init__(self, master, folder_path: str, file_list: list[str],
                 on_remove=None, **kw):
        super().__init__(master, bg=PLACEHOLDER_COLOR, highlightthickness=0, **kw)
        self.folder_path = folder_path
        self.file_list = file_list
        self._on_remove = on_remove  # callback to remove this tile's folder

        # State
        self._pil_image: Image.Image | None = None
        self._tk_image: ImageTk.PhotoImage | None = None  # prevent GC
        self._fit_mode: bool = True
        self._zoom: float = 1.0
        self._pan_x: float = 0.0  # offset of image centre vs canvas centre
        self._pan_y: float = 0.0
        self._drag_start: tuple[int, int] | None = None
        self._resize_job: str | None = None
        self._close_btn_ids: list[int] = []  # canvas item ids for close button

        # Bindings
        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_scroll)          # Windows / macOS
        self.bind("<Button-4>", self._on_scroll_up)          # Linux
        self.bind("<Button-5>", self._on_scroll_down)        # Linux

    # -- public API ---------------------------------------------------------

    def show_image(self, pil_image: Image.Image | None, fit_mode: bool):
        """Load a new image (or None for placeholder) and render."""
        self._pil_image = pil_image
        self._fit_mode = fit_mode
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._render()

    def set_fit_mode(self, fit_mode: bool):
        self._fit_mode = fit_mode
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._render()

    # -- internal rendering --------------------------------------------------

    def _render(self):
        self.delete("all")
        cw = self.winfo_width()
        ch = self.winfo_height()
        if cw < 2 or ch < 2:
            return

        if self._pil_image is None:
            self._draw_placeholder(cw, ch)
            return

        img = self._pil_image
        iw, ih = img.size

        if self._fit_mode:
            # Scale to fit canvas, then apply user zoom on top
            base_scale = min(cw / iw, ch / ih, 1.0) if (iw and ih) else 1.0
        else:
            base_scale = 1.0

        scale = base_scale * self._zoom
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))

        resized = img.resize((new_w, new_h), Image.BILINEAR)
        self._tk_image = ImageTk.PhotoImage(resized)

        x = cw / 2 + self._pan_x
        y = ch / 2 + self._pan_y
        self.create_image(x, y, image=self._tk_image, anchor="center")

        self._draw_overlay(cw, ch)

    def _draw_placeholder(self, cw, ch):
        self.create_rectangle(0, 0, cw, ch, fill=PLACEHOLDER_COLOR, outline="")
        self.create_text(
            cw // 2, ch // 2, text="No image", fill="#888888",
            font=("sans-serif", 14),
        )
        # Still draw folder name
        self._draw_overlay(cw, ch, placeholder=True)

    def _draw_overlay(self, cw, ch, placeholder=False):
        """Draw filename overlay at the bottom of the tile."""
        self._close_btn_ids.clear()

        # Folder label (short) at the top
        folder_label = os.path.basename(self.folder_path) or self.folder_path
        self.create_rectangle(0, 0, cw, 24, fill=OVERLAY_BG, stipple="gray50", outline="")
        self.create_text(
            6, 4, text=folder_label, fill=OVERLAY_FG, anchor="nw",
            font=("sans-serif", 10, "bold"),
        )

        # Close button (×) at top-right
        btn_size = 22
        bx = cw - btn_size - 2
        by = 1
        rect_id = self.create_rectangle(
            bx, by, bx + btn_size, by + btn_size,
            fill="#cc4444", outline="",
        )
        text_id = self.create_text(
            bx + btn_size // 2, by + btn_size // 2,
            text="×", fill="white", font=("sans-serif", 13, "bold"),
        )
        self._close_btn_ids = [rect_id, text_id]
        self.tag_bind(rect_id, "<ButtonRelease-1>", self._on_close_click)
        self.tag_bind(text_id, "<ButtonRelease-1>", self._on_close_click)

        if not placeholder and self._pil_image is not None:
            # Current filename at the bottom (inset so it's not clipped)
            fname = getattr(self, "_current_filename", "")
            if fname:
                bar_h = 24
                self.create_rectangle(0, ch - bar_h, cw, ch, fill=OVERLAY_BG, stipple="gray50", outline="")
                self.create_text(
                    6, ch - bar_h + 4, text=fname, fill=OVERLAY_FG, anchor="nw",
                    font=("sans-serif", 10),
                )

    def _on_close_click(self, event):
        """Handle click on the × close button."""
        if self._on_remove:
            self._on_remove(self.folder_path)

    # -- resize debounce -----------------------------------------------------

    def _on_configure(self, event):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(RESIZE_DEBOUNCE_MS, self._render)

    # -- pan -----------------------------------------------------------------

    def _on_press(self, event):
        # Don't start drag if clicking the close button
        items = self.find_overlapping(event.x, event.y, event.x, event.y)
        if any(i in self._close_btn_ids for i in items):
            return
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if self._drag_start is None:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self._pan_x += dx
        self._pan_y += dy
        self._drag_start = (event.x, event.y)
        self._render()

    def _on_release(self, _event):
        self._drag_start = None

    # -- zoom ----------------------------------------------------------------

    def _apply_zoom(self, event, factor):
        old_zoom = self._zoom
        new_zoom = old_zoom * factor
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, new_zoom))
        if new_zoom == old_zoom:
            return

        # Zoom centred on the cursor position
        cx = self.winfo_width() / 2 + self._pan_x
        cy = self.winfo_height() / 2 + self._pan_y
        # mouse position in canvas coords
        mx, my = event.x, event.y
        ratio = new_zoom / old_zoom
        # adjust pan so the point under cursor stays put
        self._pan_x = mx - ratio * (mx - self._pan_x) + (self._pan_x - (mx - self.winfo_width() / 2))
        # simplify: shift = (1 - ratio) * (mx - cw/2 - pan_x)  ... let's keep it straightforward:
        self._pan_x = self._pan_x + (mx - self.winfo_width() / 2 - self._pan_x) * (1 - ratio)
        self._pan_y = self._pan_y + (my - self.winfo_height() / 2 - self._pan_y) * (1 - ratio)
        self._zoom = new_zoom
        self._render()

    def _on_scroll(self, event):
        factor = ZOOM_STEP if event.delta > 0 else 1 / ZOOM_STEP
        self._apply_zoom(event, factor)

    def _on_scroll_up(self, event):
        self._apply_zoom(event, ZOOM_STEP)

    def _on_scroll_down(self, event):
        self._apply_zoom(event, 1 / ZOOM_STEP)


# ---------------------------------------------------------------------------
# App – main application window
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Image Viewer")
        self.geometry("1200x800")
        self.minsize(480, 320)
        self.configure(bg="#1e1e1e")

        # Data
        self._folders: list[dict] = []  # [{path, files}]
        self._tiles: list[ImageTile] = []
        self._current_index: int = 0
        self._max_length: int = 0
        self._fit_mode: bool = True
        self._columns: int = 2
        self._nav_job: str | None = None   # pending after() id for image load
        self._nav_generation: int = 0      # increments on every index change

        # UI
        self._build_toolbar()
        self._build_grid_area()
        self._bind_keys()

    # -- toolbar -------------------------------------------------------------

    def _build_toolbar(self):
        tb = tk.Frame(self, bg="#333333", padx=8, pady=6)
        tb.pack(side="top", fill="x")

        btn_add = tk.Button(
            tb, text="+ Add Folder", command=self._add_folder,
            bg="#4a90d9", fg="white", activebackground="#5aa0e9",
            relief="flat", padx=12, pady=4, font=("sans-serif", 11),
        )
        btn_add.pack(side="left", padx=(0, 16))

        tk.Label(tb, text="Columns:", bg="#333333", fg="white",
                 font=("sans-serif", 11)).pack(side="left")
        self._col_var = tk.IntVar(value=self._columns)
        self._col_spin = tk.Spinbox(
            tb, from_=1, to=10, width=3, textvariable=self._col_var,
            command=self._on_columns_changed,
            font=("sans-serif", 11), justify="center",
        )
        self._col_spin.pack(side="left", padx=(4, 16))
        self._col_spin.bind("<Return>", lambda e: self._on_columns_changed())

        self._fit_btn = tk.Button(
            tb, text="Mode: Fit", command=self._toggle_fit_mode,
            bg="#555555", fg="white", activebackground="#666666",
            relief="flat", padx=10, pady=4, font=("sans-serif", 11),
        )
        self._fit_btn.pack(side="left", padx=(0, 16))

        self._index_label = tk.Label(
            tb, text="0 / 0", bg="#333333", fg="#cccccc",
            font=("sans-serif", 12, "bold"),
        )
        self._index_label.pack(side="right")

        # Navigation buttons for mouse users
        btn_next = tk.Button(
            tb, text="▶", command=self._go_next,
            bg="#555555", fg="white", activebackground="#666666",
            relief="flat", padx=8, pady=4, font=("sans-serif", 13),
        )
        btn_next.pack(side="right", padx=2)
        btn_prev = tk.Button(
            tb, text="◀", command=self._go_prev,
            bg="#555555", fg="white", activebackground="#666666",
            relief="flat", padx=8, pady=4, font=("sans-serif", 13),
        )
        btn_prev.pack(side="right", padx=2)

    # -- grid area -----------------------------------------------------------

    def _build_grid_area(self):
        self._grid_frame = tk.Frame(self, bg="#1e1e1e")
        self._grid_frame.pack(side="top", fill="both", expand=True)
        self._vpane: tk.PanedWindow | None = None  # vertical paned window
        self._hpanes: list[tk.PanedWindow] = []     # one per row
        self._show_welcome()

    def _show_welcome(self):
        """Show a welcome message when no folders are loaded."""
        for w in self._grid_frame.winfo_children():
            w.destroy()
        self._vpane = None
        self._hpanes.clear()
        lbl = tk.Label(
            self._grid_frame,
            text='Click "+ Add Folder" to begin\n\n'
                 "Navigate with ← → arrow keys\n"
                 "Scroll to zoom · Drag to pan",
            bg="#1e1e1e", fg="#888888",
            font=("sans-serif", 16), justify="center",
        )
        lbl.place(relx=0.5, rely=0.5, anchor="center")

    # -- key bindings --------------------------------------------------------

    def _bind_keys(self):
        self.bind("<Left>", lambda e: self._go_prev())
        self.bind("<Right>", lambda e: self._go_next())
        self.bind("<Home>", lambda e: self._go_to(0))
        self.bind("<End>", lambda e: self._go_to(self._max_length - 1))

    # -- folder management ---------------------------------------------------

    def _add_folder(self):
        path = filedialog.askdirectory(title="Select an image folder")
        if not path:
            return
        files = scan_folder(path)
        if not files:
            tk.messagebox = __import__("tkinter.messagebox", fromlist=["messagebox"])
            tk.messagebox.showwarning(
                "No images found",
                f"No supported image files found in:\n{path}",
            )
            return
        self._folders.append({"path": path, "files": files})
        self._max_length = max(len(f["files"]) for f in self._folders)
        self._columns = min(len(self._folders), max(self._columns, 1))
        self._col_var.set(self._columns)
        self._rebuild_grid()
        self._show_current()

    # -- grid rebuild --------------------------------------------------------

    def _rebuild_grid(self):
        # Destroy existing
        for w in self._grid_frame.winfo_children():
            w.destroy()
        self._tiles.clear()
        self._hpanes.clear()
        self._vpane = None

        cols = max(1, min(self._columns, len(self._folders)))
        total_rows = (len(self._folders) + cols - 1) // cols

        # Vertical PanedWindow (splits rows)
        vpane = tk.PanedWindow(
            self._grid_frame, orient="vertical",
            bg="#555555", sashwidth=5, sashpad=0,
            borderwidth=0, handlesize=0,
        )
        vpane.pack(fill="both", expand=True)
        self._vpane = vpane

        for row_idx in range(total_rows):
            # Horizontal PanedWindow for this row (splits columns)
            hpane = tk.PanedWindow(
                vpane, orient="horizontal",
                bg="#555555", sashwidth=5, sashpad=0,
                borderwidth=0, handlesize=0,
            )
            vpane.add(hpane, stretch="always")
            self._hpanes.append(hpane)

            start = row_idx * cols
            end = min(start + cols, len(self._folders))
            for folder_idx in range(start, end):
                folder = self._folders[folder_idx]
                tile = ImageTile(
                    hpane, folder["path"], folder["files"],
                    on_remove=self._remove_folder,
                )
                hpane.add(tile, stretch="always")
                self._tiles.append(tile)

    # -- folder removal ------------------------------------------------------

    def _remove_folder(self, folder_path: str):
        """Remove the folder identified by *folder_path* and refresh."""
        self._folders = [f for f in self._folders if f["path"] != folder_path]
        if not self._folders:
            self._max_length = 0
            self._current_index = 0
            self._tiles.clear()
            self._show_welcome()
            self._update_index_label()
            return
        self._max_length = max(len(f["files"]) for f in self._folders)
        self._current_index = min(self._current_index, self._max_length - 1)
        self._columns = min(self._columns, len(self._folders))
        self._col_var.set(self._columns)
        self._rebuild_grid()
        self._show_current()

    # -- navigation ----------------------------------------------------------

    def _go_next(self):
        if self._max_length == 0:
            return
        self._current_index = min(self._current_index + 1, self._max_length - 1)
        self._schedule_load()

    def _go_prev(self):
        if self._max_length == 0:
            return
        self._current_index = max(self._current_index - 1, 0)
        self._schedule_load()

    def _go_to(self, index: int):
        if self._max_length == 0:
            return
        self._current_index = max(0, min(index, self._max_length - 1))
        self._schedule_load()

    def _schedule_load(self):
        """Update the index label immediately and debounce the expensive image load.

        Each call cancels any pending load and schedules a new one with after(1).
        Because after() callbacks only fire once all pending events have been
        processed, rapid key-repeat events naturally collapse into a single
        image load — eliminating the backlog that causes scrolling to continue
        after the key is released.
        """
        self._nav_generation += 1
        gen = self._nav_generation
        self._update_index_label()
        if self._nav_job is not None:
            self.after_cancel(self._nav_job)
        self._nav_job = self.after(1, lambda: self._do_load(gen))

    def _do_load(self, gen: int):
        """Actually load images, but only if no newer navigation has occurred."""
        self._nav_job = None
        if gen != self._nav_generation:
            return  # a newer key event already superseded this one
        self._show_current()

    def _show_current(self):
        idx = self._current_index
        for tile, folder in zip(self._tiles, self._folders):
            files = folder["files"]
            if idx < len(files):
                try:
                    img = Image.open(files[idx])
                    img.load()  # force read
                    # Handle EXIF orientation
                    img = self._apply_exif_rotation(img)
                except Exception:
                    img = None
                tile._current_filename = os.path.basename(files[idx]) if idx < len(files) else ""
            else:
                img = None
                tile._current_filename = ""
            tile.show_image(img, self._fit_mode)
        self._update_index_label()

    @staticmethod
    def _apply_exif_rotation(img: Image.Image) -> Image.Image:
        """Auto-rotate based on EXIF orientation tag."""
        try:
            from PIL import ExifTags
            exif = img.getexif()
            for tag, val in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    if val == 3:
                        img = img.rotate(180, expand=True)
                    elif val == 6:
                        img = img.rotate(270, expand=True)
                    elif val == 8:
                        img = img.rotate(90, expand=True)
                    break
        except Exception:
            pass
        return img

    def _update_index_label(self):
        if self._max_length == 0:
            self._index_label.config(text="0 / 0")
        else:
            self._index_label.config(
                text=f"{self._current_index + 1} / {self._max_length}"
            )

    # -- columns spinner -----------------------------------------------------

    def _on_columns_changed(self):
        try:
            val = self._col_var.get()
        except (tk.TclError, ValueError):
            return
        val = max(1, min(10, val))
        if val == self._columns and self._tiles:
            return
        self._columns = val
        if self._folders:
            self._rebuild_grid()
            self._show_current()

    # -- fit / original toggle -----------------------------------------------

    def _toggle_fit_mode(self):
        self._fit_mode = not self._fit_mode
        self._fit_btn.config(text=f"Mode: {'Fit' if self._fit_mode else 'Original'}")
        for tile in self._tiles:
            tile.set_fit_mode(self._fit_mode)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
