"""Always-on-top launcher for the local orchestrator dashboard."""

import sys
import tkinter as tk
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import console
import db


class FloatingOrb:
    SIZE = 56

    def __init__(self, root: tk.Tk, url: str) -> None:
        self.root = root
        self.url = url
        self.drag_start = (0, 0, 0, 0)
        self.dragged = False

        root.title("Codex Orchestrator")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"{self.SIZE}x{self.SIZE}+{root.winfo_screenwidth() - self.SIZE - 24}+72")

        background = "#faf7f2"
        if sys.platform == "win32":
            background = "#ff00ff"
            root.attributes("-transparentcolor", background)
        canvas = tk.Canvas(root, background=background, highlightthickness=0, cursor="hand2")
        canvas.pack(fill="both", expand=True)
        canvas.create_oval(1, 1, 55, 55, fill="#faf7f2", outline="#d8ccbc", width=2)
        canvas.create_polygon(
            28, 13, 32, 24, 43, 28, 32, 32, 28, 43, 24, 32, 13, 28, 24, 24,
            fill="#7358a8",
        )

        menu = tk.Menu(root, tearoff=False)
        menu.add_command(label="打开看板", command=self.open_dashboard)
        menu.add_command(label="退出", command=root.destroy)
        canvas.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))
        canvas.bind("<ButtonPress-1>", self.start_drag)
        canvas.bind("<B1-Motion>", self.move)
        canvas.bind("<ButtonRelease-1>", self.end_drag)
        root.bind("<Escape>", lambda event: root.destroy())

    def start_drag(self, event: tk.Event) -> None:
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self.dragged = False

    def move(self, event: tk.Event) -> None:
        mouse_x, mouse_y, orb_x, orb_y = self.drag_start
        dx, dy = event.x_root - mouse_x, event.y_root - mouse_y
        if self.dragged or max(abs(dx), abs(dy)) >= 4:
            self.dragged = True
            self.root.geometry(f"+{orb_x + dx}+{orb_y + dy}")

    def end_drag(self, event: tk.Event) -> None:
        if not self.dragged:
            self.open_dashboard()

    def open_dashboard(self) -> None:
        console.open_mini_window(self.url)


def launch_orb(db_path: Path) -> None:
    root = tk.Tk()
    db.init_db(db_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), console.make_handler(db_path)) as server:
        FloatingOrb(root, f"http://127.0.0.1:{server.server_port}/")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            root.mainloop()
        finally:
            server.shutdown()
