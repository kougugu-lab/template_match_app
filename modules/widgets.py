#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
widgets.py - 共通UIウィジェット
  create_card, Tooltip, HelpWindow
inspection_app/modules/widgets.py から流用・TM_App向けに調整
"""

import tkinter as tk
from tkinter import ttk

from .constants import (
    COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT, COLOR_BORDER,
    FONT_FAMILY, FONT_NORMAL, FONT_BOLD, FONT_LARGE, FONT_HUGE,
    APP_VERSION, APP_BUILD_DATE
)


def create_card(parent, title=None):
    """共通デザインのカードフレームを作成"""
    frame = tk.Frame(parent, bg=COLOR_BG_PANEL, bd=1, relief="flat")
    inner = tk.Frame(frame, bg=COLOR_BG_PANEL, padx=15, pady=15,
                     highlightbackground=COLOR_BORDER, highlightthickness=1)
    inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    if title:
        lbl = tk.Label(inner, text=title, font=FONT_BOLD,
                       bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, anchor="w")
        lbl.pack(fill=tk.X, pady=(0, 10))
    return frame, inner


class Tooltip:
    """カーソル位置ベースのツールチップ（方向依存なし）"""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self._after_id = None
        self.widget.bind("<Enter>", self._schedule)
        self.widget.bind("<Leave>", self.hide_tip)
        self.widget.bind("<Motion>", self._update_pos)

    def _schedule(self, event=None):
        self._last_event = event
        if self._after_id:
            self.widget.after_cancel(self._after_id)
        self._after_id = self.widget.after(500, self._show)

    def _update_pos(self, event=None):
        self._last_event = event
        if self.tip_window:
            self._reposition(event)

    def _show(self):
        if self.tip_window or not self.text:
            return
        ev = getattr(self, '_last_event', None)
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(1)
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#1e2a35", fg="#e0e8f0",
                         relief=tk.SOLID, borderwidth=1,
                         font=(FONT_FAMILY, 10), padx=8, pady=6)
        label.pack(ipadx=1)
        tw.update_idletasks()
        self._reposition(ev)

    def _reposition(self, event=None):
        tw = self.tip_window
        if not tw:
            return
        tw.update_idletasks()
        w_tip = tw.winfo_width()
        h_tip = tw.winfo_height()
        if event:
            cx, cy = event.x_root, event.y_root
        else:
            cx = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            cy = self.widget.winfo_rooty() + self.widget.winfo_height()
        scr_h = self.widget.winfo_screenheight()
        scr_w = self.widget.winfo_screenwidth()
        x = min(cx + 16, scr_w - w_tip - 4)
        if cy + h_tip + 20 < scr_h:
            y = cy + 16
        else:
            y = cy - h_tip - 10
        tw.wm_geometry(f"+{x}+{y}")

    def hide_tip(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()


class HelpWindow(tk.Toplevel):
    def __init__(self, parent, title, help_dict):
        super().__init__(parent)
        self.title(title)
        self.geometry("700x650")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)

        header = tk.Frame(self, bg=COLOR_BG_PANEL, pady=15)
        header.pack(fill=tk.X)
        tk.Label(header, text=title, font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).pack()

        container = tk.Frame(self, bg=COLOR_BG_MAIN, padx=20, pady=20)
        container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(container, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)

        scrollable_frame.bind("<Configure>",
                              lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for section, content in help_dict.items():
            tk.Label(scrollable_frame, text=f"【{section}】", font=FONT_BOLD,
                     bg=COLOR_BG_MAIN, fg=COLOR_ACCENT, anchor="w",
                     justify=tk.LEFT).pack(fill=tk.X, pady=(10, 5))
            tk.Label(scrollable_frame, text=content, font=FONT_NORMAL,
                     bg=COLOR_BG_MAIN, fg=COLOR_TEXT_MAIN, anchor="w",
                     justify=tk.LEFT, wraplength=600).pack(fill=tk.X, pady=(0, 15))

        # バージョン情報
        tk.Label(scrollable_frame, text=f"Ver {APP_VERSION}  ({APP_BUILD_DATE})",
                 font=(FONT_FAMILY, 11), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB).pack(pady=(20, 5))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _cleanup(e):
            canvas.unbind_all("<MouseWheel>")

        self.bind("<Destroy>", _cleanup)

        tk.Button(self, text="閉じる", font=FONT_BOLD, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", pady=10,
                  command=self.destroy).pack(fill=tk.X)
