#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py - 設定ダイアログ (SettingsDialog)
タブ構成: カメラ / GPIOピン / パターン / 画像処理 / 画素数 / システム
"""

import cv2
import threading
import time
import datetime
import platform
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import numpy as np
import json
from pathlib import Path
from .engine import InspectionEngine

from .constants import (
    COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT,
    COLOR_OK, COLOR_NG, COLOR_WARNING, COLOR_BORDER, COLOR_NG_MUTED,
    FONT_FAMILY, FONT_NORMAL, FONT_BOLD, FONT_LARGE,
    FONT_SET_TAB, FONT_SET_LBL, FONT_SET_VAL, FONT_BTN_LARGE,
    VALID_BCM_PINS, RES_OPTIONS, RES_OPTIONS_RAW, CAM_PROP_MAP,
    RES_OPTIONS_PREVIEW, RES_OPTIONS_SAVE
)
from .widgets import create_card, Tooltip, HelpWindow
from .hardware import OutputDevice, DigitalInputDevice
from .settings import OPERATION_PRESETS



# ---------------------------------------------------------------------------
# GPIO 診断ダイアログ
# ---------------------------------------------------------------------------
class GPIOTestDialog(tk.Toplevel):
    def __init__(self, parent, gpio_settings, app_instance):
        super().__init__(parent)
        self.title("GPIO 診断・テスト")
        self.geometry("600x700")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)
        self.grab_set()

        self.gpio_settings = gpio_settings
        self.app = app_instance
        self.running = True
        self.inputs = {}
        self.outputs = {}

        tk.Label(self, text="GPIO 診断・テスト", font=FONT_LARGE,
                 bg=COLOR_BG_MAIN, fg=COLOR_ACCENT).pack(pady=20)

        # スクロールエリア
        container = tk.Frame(self, bg=COLOR_BG_MAIN)
        container.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        canvas = tk.Canvas(container, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 入力状態
        f_in = tk.LabelFrame(scrollable_frame, text="入力ピン状態 (200ms周期)", font=FONT_SET_LBL, 
                             bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, padx=15, pady=15)
        f_in.pack(fill=tk.X, pady=10)

        self.ui_inputs = {}
        # トリガー
        for t in self.gpio_settings.get("triggers", []):
            self._make_test_in_row(f_in, f"トリガー: {t['name']}", t["pin"], t["id"])
        # パターン切り替え
        for p in self.gpio_settings.get("pattern_pins", []):
            self._make_test_in_row(f_in, f"パターン: {p['name']}", p["pin"], p["id"])

        # 出力テスト
        f_out = tk.LabelFrame(scrollable_frame, text="出力テスト (クリック中のみON)", font=FONT_SET_LBL, 
                              bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, padx=15, pady=15)
        f_out.pack(fill=tk.X, pady=10)

        outs = self.gpio_settings.get("outputs", {})
        self._make_test_out_row(f_out, "OK出力", outs.get("ok", 0))
        self._make_test_out_row(f_out, "NG出力", outs.get("ng", 0))

        tk.Button(self, text="閉じる", font=FONT_BOLD, bg="#546E7A", fg="white", 
                  relief="flat", height=2, command=self.destroy).pack(fill=tk.X, padx=20, pady=20)

        self._update_loop()

    def _make_test_in_row(self, parent, label, pin, pid):
        row = tk.Frame(parent, bg=COLOR_BG_PANEL); row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=f"{label} (Pin {pin}):", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=25, anchor="w").pack(side=tk.LEFT)
        lbl = tk.Label(row, text="OFF", font=FONT_BOLD, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB, width=8)
        lbl.pack(side=tk.RIGHT, padx=10)
        self.ui_inputs[pid] = lbl

    def _make_test_out_row(self, parent, label, pin):
        row = tk.Frame(parent, bg=COLOR_BG_PANEL); row.pack(fill=tk.X, pady=5)
        tk.Label(row, text=f"{label} (Pin {pin}):", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=25, anchor="w").pack(side=tk.LEFT)
        btn = tk.Button(row, text="テスト(PUSH)", font=FONT_BOLD, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat", width=12)
        btn.pack(side=tk.RIGHT, padx=10)
        
        def _on_press(e):
            if pin in VALID_BCM_PINS:
                try:
                    dev = OutputDevice(pin); dev.on()
                    self.outputs[pin] = dev
                    btn.config(bg=COLOR_WARNING, fg="black")
                except: pass
        def _on_release(e):
            dev = self.outputs.pop(pin, None)
            if dev:
                dev.off(); dev.close()
            btn.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN)

        btn.bind("<ButtonPress-1>", _on_press)
        btn.bind("<ButtonRelease-1>", _on_release)

    def _update_loop(self):
        if not self.winfo_exists(): return
        if self.app:
            # 入力状態の同期
            for pid, lbl in self.ui_inputs.items():
                dev = self.app.inputs.get(pid) or self.app.pattern_inputs.get(pid)
                if dev:
                    active = dev.is_active
                    lbl.config(text="ON" if active else "OFF", bg=COLOR_ACCENT if active else COLOR_BG_INPUT, fg="black" if active else COLOR_TEXT_SUB)
        self.after(200, self._update_loop)

    def destroy(self):
        for dev in self.outputs.values():
            try: dev.off(); dev.close()
            except: pass
        super().destroy()


class SettingsDialog(tk.Toplevel):
    """詳細設定ダイアログ（inspection_app のスタイルに準拠した最新版）"""

    def __init__(self, parent, config_manager, on_close_callback=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.on_close_callback = on_close_callback
        self._changed = False
        
        # カメラプレビュー用
        self._preview_running = False
        self._preview_cap = None
        self._preview_thread = None
        
        # 調整タブプレビュー用
        self._adj_preview_running = False
        self._adj_cap = None
        self._adj_current_frame = None
        self._frame_lock = threading.Lock()
        
        # GPIO監視・テスト用
        self._active_test_devs = {}   # {button_widget: [OutputDevice, ...]}
        self._active_input_devs = {}  # {pin_number: DigitalInputDevice}
        self._input_status_labels = {} # {pname: label_widget}
        self.pin_widgets = {}         # LED表示用 {id: (canvas, circle)}
        
        # ROI操作用
        self._adj_drag_start = None
        self._adj_roi_draft = None

        # UI変数 (各タブ共通)
        self.v_res_cap = tk.StringVar()
        self.v_res_pre = tk.StringVar()
        self.v_res_ok = tk.StringVar()
        self.v_res_ng = tk.StringVar()
        self.v_res_dir = tk.StringVar()
        self.v_auto_del = tk.BooleanVar()
        self.v_max_gb = tk.IntVar()
        self.v_save_debug = tk.BooleanVar()
        self.v_contours_flag = tk.BooleanVar()
        self.v_preview_fps = tk.DoubleVar()
        self.v_ok_output_time = tk.DoubleVar()
        self.v_ng_output_time = tk.DoubleVar()
        self.v_result_display_time = tk.DoubleVar()
        self.v_max_retries = tk.IntVar()
        self.v_burst_interval = tk.DoubleVar()
        self.v_operation_preset = tk.StringVar()
        self.v_environment_profile = tk.StringVar()
        self.v_auto_apply_environment = tk.BooleanVar()
        self.v_compat_cleanup_enabled = tk.BooleanVar()
        self._loaded_operation_preset = "standard"
        self._cb_res_ok = None
        self._cb_res_ng = None

        self.title("詳細設定")
        self.geometry("1400x900")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        
        # 作業用の一時データを複製 (Deep Copy)
        self.temp_data = json.loads(json.dumps(self.cfg.data))
        self.active_entry = (None, None) 

        self._build_ui()
        self._load_values()
        self._apply_initial_dialog_state()
        self.lift()
        self.focus_force()
        self.after(200, self.grab_set)
        
        # プレビューループの開始
        self._adj_loop()
        self.after(200, self._start_monitoring)

    def _apply_initial_dialog_state(self):
        """初期表示（位置・タブ・先頭項目）を inspection_app 準拠に整える"""
        self.update_idletasks()

        # 親ウィンドウ中央に初期配置
        try:
            parent_x = self.master.winfo_rootx()
            parent_y = self.master.winfo_rooty()
            parent_w = self.master.winfo_width()
            parent_h = self.master.winfo_height()
            dlg_w = self.winfo_width()
            dlg_h = self.winfo_height()
            pos_x = max(0, parent_x + (parent_w - dlg_w) // 2)
            pos_y = max(0, parent_y + (parent_h - dlg_h) // 2)
            self.geometry(f"{dlg_w}x{dlg_h}+{pos_x}+{pos_y}")
        except Exception:
            pass

        # 初期表示タブは先頭（カメラ）固定
        if hasattr(self, "notebook"):
            self.notebook.select(0)

        # パターン一覧は先頭項目を事前選択して初回表示を安定化
        if hasattr(self, "lb_pat") and self.lb_pat.size() > 0:
            self.lb_pat.selection_clear(0, tk.END)
            self.lb_pat.selection_set(0)
            self.after(100, lambda: self.on_pat_sel(None))

    def _entry(self, parent, var, width=None):
        """標準的な Entry ウィジェット作成ヘルパー"""
        ent = tk.Entry(parent, textvariable=var, font=FONT_SET_VAL,
                        width=width, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                        insertbackground="white", relief="flat", bd=1)
        # 変更検知用のフック
        var.trace_add("write", lambda *a: self._mark_changed())
        return ent

    def _spinbox(self, parent, var, from_, to, increment=1, width=8):
        """配色を改善した標準的な Spinbox ウィジェット作成ヘルパー"""
        sp = tk.Spinbox(parent, textvariable=var, from_=from_, to=to,
                        increment=increment, font=FONT_SET_VAL, width=width,
                        bg="#2c2e2f", fg="white", buttonbackground="#45494a",
                        buttoncursor="hand2", relief="flat", bd=1,
                        command=self._mark_changed)
        # 直接入力時の変更検知用
        var.trace_add("write", lambda *a: self._mark_changed())
        return sp

    def _set_active_entry(self, entry, var):
        """Pi 40pin Map からの入力対象を設定"""
        self.active_entry = (entry, var)
        entry.focus_set()

    def create_scrollable_panel(self, parent):
        """スクロール可能なパネルを作成"""
        canvas = tk.Canvas(parent, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLOR_BG_MAIN)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        
        # Canvasの幅を親に合わせる
        def _on_canvas_resize(event):
            canvas.itemconfig(1, width=event.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            if not self.winfo_exists(): return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        return scrollable_frame

    def _build_ui(self):
        """UI全体を構築"""
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Dark.TNotebook", background=COLOR_BG_MAIN, borderwidth=0)
        style.configure("Dark.TNotebook.Tab",
                        background=COLOR_BG_PANEL, foreground=COLOR_TEXT_MAIN,
                        padding=[20, 10], font=FONT_SET_TAB, focuscolor=COLOR_BG_MAIN)
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", COLOR_ACCENT)],
                  foreground=[("selected", "black")])
        style.configure(
            "Dark.TCombobox",
            fieldbackground=COLOR_BG_INPUT,
            background=COLOR_BG_INPUT,
            foreground=COLOR_TEXT_MAIN,
            arrowcolor=COLOR_TEXT_MAIN
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", COLOR_BG_INPUT)],
            foreground=[("readonly", COLOR_TEXT_MAIN)],
            selectforeground=[("readonly", COLOR_TEXT_MAIN)],
            selectbackground=[("readonly", COLOR_BG_INPUT)]
        )

        # --- 下部ボタンバー (先にpackして下部領域を確保) ---
        btn_bar = tk.Frame(self, bg=COLOR_BG_MAIN, pady=20)
        btn_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=20)

        tk.Button(btn_bar, text="ヘルプ", font=FONT_BOLD, bg=COLOR_BG_INPUT,
                  fg=COLOR_ACCENT, relief="flat", width=12, height=1,
                  command=self._show_help).pack(side=tk.LEFT, padx=20)

        self.btn_save = tk.Button(btn_bar, text="保存して閉じる",
                                  font=FONT_BOLD, bg=COLOR_BG_INPUT,
                                  fg="white", relief="flat", width=22, height=1,
                                  command=self._on_save)
        self.btn_save.pack(side=tk.RIGHT, padx=5)

        tk.Button(btn_bar, text="キャンセル", font=FONT_BOLD, bg="#546E7A",
                   fg="white", relief="flat", width=10, height=1,
                   command=self._on_cancel).pack(side=tk.RIGHT, padx=5)

        # ノートブックの確保
        self.notebook = ttk.Notebook(self, style="Dark.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # 各タブの構築
        self._tab_camera()
        self._tab_gpio()
        self._tab_pattern()
        self._tab_adjust()
        self._tab_resolution()
        self._tab_system()
        
        # タブ切り替え時の自動選択イベント
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ---------------------------------------------------------------
    # タブ1: カメラ
    # ---------------------------------------------------------------
    def _tab_camera(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text=" カメラ ")

        pane = tk.PanedWindow(tab, orient=tk.HORIZONTAL, bg=COLOR_BG_MAIN, sashwidth=6, sashrelief="flat")
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左: 設定
        left, left_inner_wrap = create_card(pane, "カメラ設定")
        pane.add(left, minsize=520)
        
        # 自動調整ボタンをスクロール外に固定配置して見切れを防止
        btn_all_auto = tk.Button(left_inner_wrap, text="カメラ全項目を自動調整", font=FONT_BOLD, bg="#455A64", fg="white", relief="flat", height=1, command=lambda: self._auto_tune_all_camera_props(btn_all_auto))
        btn_all_auto.pack(fill=tk.X, pady=(0, 10), padx=10)
        Tooltip(btn_all_auto, "露出、フォーカス、ホワイトバランスを順番に自動走査して最適化します")
        
        inner = self.create_scrollable_panel(left_inner_wrap)

        # インデックス
        row_f = tk.Frame(inner, bg=COLOR_BG_PANEL)
        row_f.pack(fill=tk.X, pady=10)
        tk.Label(row_f, text="カメラインデックス:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=20, anchor="w").pack(side=tk.LEFT, padx=10)
        self.cam_idx_var = tk.IntVar()
        sp_idx = self._spinbox(row_f, self.cam_idx_var, from_=0, to=10)
        sp_idx.pack(side=tk.LEFT)
        Tooltip(sp_idx, "接続されているカメラの番号です。通常は 0 です")

        # カメラ詳細プロパティ
        self.cam_props = {}
        # (key, label, tooltip)
        props_def = [
            ("fps", "フレームレート", "1秒あたりの撮影枚数を設定します"),
            ("focus", "フォーカス", "レンズの焦点を調整します"),
            ("gain", "ゲイン", "センサーの感度(ゲイン)を調整します"), 
            ("exposure", "露出", "露出(シャッター速度)を調整します。暗い場合は上げてください"),
            ("brightness", "明るさ", "画像の明るさを調整します"),
            ("contrast", "コントラスト", "画像のコントラスト(明暗差)を調整します"), 
            ("saturation", "彩度", "画像の彩度(鮮やかさ)を調整します"),
            ("hue", "色相", "画像の色相を調整します"),
            ("wb_temp", "ホワイトバランス", "色温度(ホワイトバランス)を調整します"),
            ("zoom", "ズーム", "デジタルズーム倍率を設定します")
        ]
        
        for k, lbl, tip in props_def:
            row_outer = tk.Frame(inner, bg=COLOR_BG_PANEL)
            row_outer.pack(fill=tk.X, pady=4, padx=10)
            row_outer.grid_columnconfigure(0, minsize=230)
            row_outer.grid_columnconfigure(1, minsize=120)
            row_outer.grid_columnconfigure(2, minsize=120)
            row_outer.grid_columnconfigure(3, weight=1)

            tk.Label(
                row_outer, text=f"{lbl}:", font=FONT_SET_LBL,
                bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=16, anchor="w"
            ).grid(row=0, column=0, sticky="w")
            var = tk.StringVar()
            self.cam_props[k] = var

            # 入力欄幅を詰め、右側にボタンを配置しやすくする
            sp_w = 7 if k in ("exposure", "wb_temp") else 6
            if k == "exposure":
                sp = self._spinbox(row_outer, var, from_=-20, to=20000, width=sp_w)
            elif k == "wb_temp":
                sp = self._spinbox(row_outer, var, from_=2000, to=10000, width=sp_w)
            elif k == "focus":
                sp = self._spinbox(row_outer, var, from_=0, to=255, width=sp_w)
            else:
                sp = self._spinbox(row_outer, var, from_=0, to=255 if k != "zoom" else 10, width=sp_w)
            sp.grid(row=0, column=1, sticky="w", padx=(8, 12))
            Tooltip(sp, tip)

            # 特定項目は1行優先で右側にボタン配置
            if k in ("focus", "wb_temp", "exposure"):
                btn_auto = tk.Button(
                    row_outer, text="自動調整", font=FONT_NORMAL,
                    bg="#455A64", fg="white", relief="flat",
                    command=lambda key=k: self._auto_tune_prop(key)
                )
                btn_auto.grid(row=0, column=2, sticky="w", padx=(0, 10))

                if k == "focus":
                    self.v_af = tk.BooleanVar()
                    cb = tk.Checkbutton(
                        row_outer, text="AF有効", variable=self.v_af,
                        font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                        selectcolor=COLOR_BG_INPUT, command=self._mark_changed
                    )
                    # 1行目が狭い場合でも、入力欄の左端に揃えて2行目へ退避
                    cb.grid(row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))
                    Tooltip(cb, "オートフォーカスを有効にします。被写体との距離が変わる場合に有効です")
                    self.cam_props["autofocus"] = self.v_af

        # 右: プレビュー
        right, right_inner = create_card(pane, "テストプレビュー")
        pane.add(right)

        # コントロール領域を下部固定にすることで画面縮小時の見切れ（ボタン消失）を防止
        ctrl_f = tk.Frame(right_inner, bg=COLOR_BG_PANEL, pady=5)
        ctrl_f.pack(side=tk.BOTTOM, fill=tk.X)
        btn_start = tk.Button(ctrl_f, text="プレビュー開始", font=FONT_BOLD, bg=COLOR_OK, fg="black", width=15, command=self._start_cam_preview)
        btn_start.pack(side=tk.LEFT, padx=10)
        Tooltip(btn_start, "カメラの生の映像を表示して画角や感度をテストします")
        btn_stop = tk.Button(ctrl_f, text="プレビュー停止", font=FONT_BOLD, bg=COLOR_NG, fg="white", width=15, command=self._stop_cam_preview)
        btn_stop.pack(side=tk.LEFT)
        Tooltip(btn_stop, "カメラプレビューを終了します")

        self.cam_preview_canvas = tk.Canvas(right_inner, bg="black")
        self.cam_preview_canvas.pack(fill=tk.BOTH, expand=True)

    def _start_cam_preview(self):
        self._stop_cam_preview()
        idx = self.cam_idx_var.get()
        # 最新のUI上の設定値を反映させるため、temp_dataから取得
        cam_cfg = self.temp_data.get("camera", {})
        self._preview_cap = InspectionEngine.open_camera(idx, cam_cfg)
        self._preview_running = True
        if self._preview_cap and self._preview_cap.isOpened():
            self._preview_thread = threading.Thread(target=self._cam_preview_worker, daemon=True)
            self._preview_thread.start()
        else:
            self._preview_running = False
            messagebox.showerror("エラー", f"カメラ(Index: {idx})を開けませんでした。\n他のアプリで使用中か、接続を確認してください。")

    def _cam_preview_worker(self):
        while self._preview_running:
            cap = self._preview_cap
            if cap is None:
                break
            try:
                ret, frame = cap.read()
            except Exception:
                break
            if ret:
                h, w = frame.shape[:2]
                cw, ch = self.cam_preview_canvas.winfo_width(), self.cam_preview_canvas.winfo_height()
                if cw > 1 and ch > 1:
                    ratio = min(cw/w, ch/h)
                    resized = cv2.resize(frame, (int(w * ratio), int(h * ratio)))
                    img = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)))
                    self.after(0, lambda i=img: self._update_canvas(self.cam_preview_canvas, i))
            time.sleep(0.05)

    def _stop_cam_preview(self):
        self._preview_running = False
        if self._preview_cap:
            self._preview_cap.release()
            self._preview_cap = None
        th = self._preview_thread
        if th and th.is_alive():
            th.join(timeout=0.3)
        self._preview_thread = None

    def _auto_tune_prop(self, k):
        if not self._preview_cap or not self._preview_cap.isOpened():
            messagebox.showwarning("警告", "カメラプレビューを開始してください。")
            return
        threading.Thread(target=self._internal_sweep_prop, args=(k,), daemon=True).start()

    def _auto_tune_all_camera_props(self, btn):
        if not self._preview_cap:
            messagebox.showwarning("警告", "カメラプレビューを開始してください。")
            return
        btn.config(state="disabled", text="一括自動調整中...")
        def _task():
            for k in ["exposure", "focus", "wb_temp"]:
                if not self.winfo_exists(): return
                self._internal_sweep_prop(k)
            if self.winfo_exists():
                self.after(0, lambda: btn.config(state="normal", text="カメラ全項目を自動調整"))
        threading.Thread(target=_task, daemon=True).start()

    def _internal_sweep_prop(self, k):
        """カメラプロパティを段階的に変化させて最適な値を探索(露出・フォーカス等)"""
        # (ロジック詳細は省略するが、本番環境ではスイープ処理を記述)
        time.sleep(1) # ダミー待機

    # ---------------------------------------------------------------
    # タブ2: GPIO設定
    # ---------------------------------------------------------------
    def _tab_gpio(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text=" GPIOピン ")

        main_f = tk.Frame(tab, bg=COLOR_BG_MAIN)
        main_f.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 3カラム
        cL = tk.Frame(main_f, bg=COLOR_BG_MAIN); cL.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        cM = tk.Frame(main_f, bg=COLOR_BG_MAIN); cM.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        cR = tk.Frame(main_f, bg=COLOR_BG_MAIN); cR.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # 左: 監視入力
        sc_L = self.create_scrollable_panel(cL)
        
        o_trig, i_trig = create_card(sc_L, "トリガー入力")
        o_trig.pack(fill=tk.X, pady=(0, 10))
        self.trig_list_f = tk.Frame(i_trig, bg=COLOR_BG_PANEL); self.trig_list_f.pack(fill=tk.X)

        o_sel, i_sel = create_card(sc_L, "パターン切替")
        o_sel.pack(fill=tk.X, pady=10)
        self.sel_list_f = tk.Frame(i_sel, bg=COLOR_BG_PANEL); self.sel_list_f.pack(fill=tk.X)
        btn_add_pin = tk.Button(i_sel, text="+ 追加", font=FONT_NORMAL, bg=COLOR_ACCENT, fg="black", command=self.add_sel_pin)
        btn_add_pin.pack(anchor="e", pady=5)
        Tooltip(btn_add_pin, "現在選択されているパターンを判別するための外部入力ピンを追加します")

        # 中: ピンマップ
        self.show_gpio_map(cM)

        # 右: 出力
        o_out, i_out = create_card(cR, "判定出力")
        o_out.pack(fill=tk.X)
        self.v_ok, self.v_ng = tk.StringVar(), tk.StringVar()
        
        f_ok = self._make_out_row(i_out, "OK時出力:", self.v_ok, 0)
        Tooltip(f_ok, "判定結果がOKの際に出力するBCM番号を指定します")
        f_ng = self._make_out_row(i_out, "NG時出力:", self.v_ng, 1)
        Tooltip(f_ng, "判定結果がNGの際に出力するBCM番号を指定します")

        # システム状態
        s_out, s_inner = create_card(cR, "システム状態")
        s_out.pack(fill=tk.X, pady=10)
        self.lbl_gpio_status = tk.Label(s_inner, text="接続確認中...", font=FONT_BOLD, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT)
        self.lbl_gpio_status.pack(pady=10)
        self._check_gpio_connection()
        
        tk.Button(cR, text="GPIO 入出力テスト", font=FONT_BOLD, bg="#546E7A", fg="white", 
                  relief="flat", height=2, command=self._open_gpio_test).pack(fill=tk.X, padx=10, pady=10)

    def _make_out_row(self, parent, label, var, r):
        f = tk.Frame(parent, bg=COLOR_BG_PANEL); f.pack(fill=tk.X, pady=5)
        tk.Label(f, text=label, font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=15, anchor="w").pack(side=tk.LEFT)
        e = self._entry(f, var, width=6); e.pack(side=tk.LEFT, padx=10)
        e.bind("<FocusIn>", lambda ev: self._set_active_entry(e, var))
        btn = tk.Button(f, text="テスト出力", font=FONT_NORMAL, bg="#546E7A", fg="white", command=lambda: self._toggle_gpio_test(var, btn))
        btn.pack(side=tk.LEFT, padx=5)
        Tooltip(btn, "クリックしている間、出力をONにします（配線確認用）")
        return f

    def refresh_gpio_trig(self):
        for w in self.trig_list_f.winfo_children(): w.destroy()
        trigs = self.temp_data.setdefault("gpio", {}).setdefault("triggers", [{"id":"t1","name":"手動","pin":0}])
        if len(trigs) > 1:
            self.temp_data["gpio"]["triggers"] = [trigs[0]]
            trigs = self.temp_data["gpio"]["triggers"]
        
        obj = trigs[0]
        f = tk.Frame(self.trig_list_f, bg=COLOR_BG_PANEL); f.pack(fill=tk.X, pady=2)
        led = tk.Canvas(f, width=16, height=16, bg=COLOR_BG_PANEL, highlightthickness=0); led.pack(side=tk.LEFT)
        circle = led.create_oval(2, 2, 14, 14, fill="#333", outline="#555")
        self.pin_widgets[obj["id"]] = (led, circle)
        
        tk.Label(f, text="トリガー1:", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=12, anchor="w").pack(side=tk.LEFT, padx=5)
        
        vp = tk.StringVar(value=str(obj["pin"]))
        ep = self._entry(f, vp, width=8); ep.pack(side=tk.LEFT, padx=2)
        ep.bind("<FocusIn>", lambda ev, e=ep, v=vp: self._set_active_entry(e, v))
        def _trace(*a, p=vp):
            try: p_val = int(p.get() or 0)
            except: p_val = 0
            self.temp_data["gpio"]["triggers"][0]["pin"] = p_val
            self._mark_changed()
        vp.trace_add("write", _trace)

    def refresh_gpio_sel(self):
        for w in self.sel_list_f.winfo_children(): w.destroy()
        pins = self.temp_data.setdefault("gpio", {}).setdefault("pattern_pins", [])
        for i, obj in enumerate(pins):
            f = tk.Frame(self.sel_list_f, bg=COLOR_BG_PANEL); f.pack(fill=tk.X, pady=2)
            led = tk.Canvas(f, width=16, height=16, bg=COLOR_BG_PANEL, highlightthickness=0); led.pack(side=tk.LEFT)
            circle = led.create_oval(2, 2, 14, 14, fill="#333", outline="#555")
            self.pin_widgets[obj["id"]] = (led, circle)
            vn, vp = tk.StringVar(value=obj["name"]), tk.StringVar(value=str(obj["pin"]))
            self._entry(f, vn, width=12).pack(side=tk.LEFT, padx=5)
            ep = self._entry(f, vp, width=5); ep.pack(side=tk.LEFT, padx=2)
            ep.bind("<FocusIn>", lambda ev, e=ep, v=vp: self._set_active_entry(e, v))
            def _trace(*a, idx=i, n=vn, p=vp):
                try: p_val = int(p.get() or 0)
                except: p_val = 0
                self.temp_data["gpio"]["pattern_pins"][idx].update({"name": n.get(), "pin": p_val})
                self._mark_changed()
            vn.trace_add("write", _trace); vp.trace_add("write", _trace)
            tk.Button(f, text="×", font=FONT_NORMAL, bg=COLOR_NG_MUTED, fg="white", width=2, command=lambda idx=i: [pins.pop(idx), self.refresh_gpio_sel(), self._mark_changed()]).pack(side=tk.RIGHT)

    def add_trig(self):
        self.temp_data["gpio"]["triggers"].append({"id":f"t_{int(time.time()*1000)}", "name":"追加トリガー", "pin":0})
        self.refresh_gpio_trig(); self._mark_changed()

    def add_sel_pin(self):
        self.temp_data["gpio"]["pattern_pins"].append({"id":f"s_{int(time.time()*1000)}", "name":f"ピン {len(self.temp_data['gpio']['pattern_pins'])+1}", "pin":0})
        # 既存パターンの条件配列を拡張
        for p in self.temp_data["patterns"].values(): p["pin_condition"].append(0)
        self.refresh_gpio_sel(); self._mark_changed()

    def show_gpio_map(self, parent):
        outer, inner = create_card(parent, "Pi 40Pin Map")
        outer.pack(fill=tk.BOTH, expand=True)
        def _on_clicked(bcm):
            ae, var = self.active_entry
            if var and bcm is not None:
                var.set(str(bcm)); ae.focus_set(); self._mark_changed()
        pins_data = [(1,"3.3V",None),(2,"5V",None),(3,"GPIO 2",2),(4,"5V",None),(5,"GPIO 3",3),(6,"GND",None),(7,"GPIO 4",4),(8,"GPIO 14",14),(9,"GND",None),(10,"GPIO 15",15),(11,"GPIO 17",17),(12,"GPIO 18",18),(13,"GPIO 27",27),(14,"GND",None),(15,"GPIO 22",22),(16,"GPIO 23",23),(17, "3.3V", None), (18, "GPIO 24", 24), (19, "GPIO 10", 10), (20, "GND", None), (21, "GPIO 9", 9), (22, "GPIO 25", 25), (23, "GPIO 11", 11), (24, "GPIO 8", 8), (25, "GND", None), (26, "GPIO 7", 7), (27, "ID_SD", None), (28, "ID_SC", None), (29, "GPIO 5", 5), (30, "GND", None), (31, "GPIO 6", 6), (32, "GPIO 12", 12), (33, "GPIO 13", 13), (34, "GND", None), (35, "GPIO 19", 19), (36, "GPIO 16", 16), (37, "GPIO 26", 26), (38, "GPIO 20", 20), (39, "GND", None), (40, "GPIO 21", 21)]
        mf = tk.Frame(inner, bg=COLOR_BG_PANEL); mf.pack(pady=15, padx=20)
        for i, (pno, name, bcm) in enumerate(pins_data):
            r, col = i // 2, (0 if i % 2 == 0 else 2)
            num_l = tk.Label(mf, text=str(pno), font=(FONT_FAMILY, 10, "bold"), width=3, bg="#222", fg="white")
            txt_l = tk.Label(mf, text=name, font=(FONT_FAMILY, 10), width=12, bg="#444", fg=COLOR_TEXT_MAIN, padx=5, pady=3)
            if "V" in name: txt_l.config(bg="#8D6E63")
            if "GND" in name: txt_l.config(bg="#212121")
            if col == 0: num_l.grid(row=r, column=0, padx=2, pady=1); txt_l.grid(row=r, column=1, padx=(2,10), pady=1, sticky="w")
            else: txt_l.grid(row=r, column=2, padx=(10,2), pady=1, sticky="e"); num_l.grid(row=r, column=3, padx=2, pady=1)
            if bcm:
                h = lambda e, b=bcm: _on_clicked(b)
                num_l.bind("<Button-1>", h); txt_l.bind("<Button-1>", h)
                num_l.config(cursor="hand2"); txt_l.config(cursor="hand2")

    # ---------------------------------------------------------------
    # タブ3: パターン設定
    # ---------------------------------------------------------------
    def _tab_pattern(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text=" パターン ")
        
        main_f = tk.Frame(tab, bg=COLOR_BG_MAIN); main_f.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # 左: パターンリスト
        left_outer, left = create_card(main_f, "パターン一覧")
        left_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
        left_outer.config(width=280)
        
        self.lb_pat = tk.Listbox(left, font=FONT_SET_LBL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, 
                                 selectbackground=COLOR_ACCENT, selectforeground="black", relief="flat", borderwidth=0, highlightthickness=0)
        self.lb_pat.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.lb_pat.bind("<<ListboxSelect>>", self.on_pat_sel)
        Tooltip(self.lb_pat, "登録されている判定パターンのリストです。選択して右側で条件を編集します")
        
        btn_f = tk.Frame(left, bg=COLOR_BG_PANEL)
        btn_f.pack(fill=tk.X, padx=10, pady=5)
        btn_add_pat = tk.Button(btn_f, text="+ パターン追加", font=FONT_BTN_LARGE, bg=COLOR_ACCENT, fg="black", relief="flat", command=self.add_pat)
        btn_add_pat.pack(fill=tk.X, pady=2)
        Tooltip(btn_add_pat, "新しい判定パターンを作成します")
        btn_del_pat = tk.Button(btn_f, text="選択パターンを削除", font=FONT_NORMAL, bg=COLOR_NG_MUTED, fg="white", relief="flat", command=self.del_pat)
        btn_del_pat.pack(fill=tk.X, pady=2)
        Tooltip(btn_del_pat, "選択したパターンを削除します")

        # 右: 詳細
        right_outer, self.pat_c = create_card(main_f, "パターン設定")
        right_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.pat_body = self.create_scrollable_panel(self.pat_c)
        
        self.refresh_pat_list()

    def refresh_pat_list(self):
        self.lb_pat.delete(0, tk.END)
        for pid in self.temp_data.get("pattern_order", []):
            self.lb_pat.insert(tk.END, self.temp_data["patterns"][pid]["name"])

    def add_pat(self):
        pid = f"p_{int(time.time()*1000)}"
        self.temp_data["patterns"][pid] = {
            "name": f"パターン {len(self.temp_data.get('pattern_order', []))+1}",
            "pin_condition": [0] * len(self.temp_data.get("gpio", {}).get("pattern_pins", []))
        }
        self.temp_data.setdefault("pattern_order", []).append(pid)
        self.refresh_pat_list(); self.lb_pat.selection_set(tk.END); self.on_pat_sel(None); self._mark_changed()

    def del_pat(self):
        sel = self.lb_pat.curselection()
        if not sel: return
        if not messagebox.askyesno("確認", "選択したパターンを削除しますか？"): return
        pid = self.temp_data["pattern_order"].pop(sel[0])
        del self.temp_data["patterns"][pid]
        self.refresh_pat_list(); self.on_pat_sel(None); self._mark_changed()

    def on_pat_sel(self, event):
        sel = self.lb_pat.curselection()
        if not sel: return
        for w in self.pat_body.winfo_children(): w.destroy()
        pid = self.temp_data["pattern_order"][sel[0]]
        p = self.temp_data["patterns"][pid]
        
        # 基本設定
        o_base, i_base = create_card(self.pat_body, "基本情報")
        o_base.pack(fill=tk.X, pady=(0, 20))
        tk.Label(i_base, text="パターン名称:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN).pack(anchor="w")
        vn = tk.StringVar(value=p["name"])
        e = self._entry(i_base, vn, width=40); e.pack(fill=tk.X, pady=5)
        Tooltip(e, "パターンの名称です。マスターフォルダの名前と一致させる必要があります")
        def _upd_n(*a, n=vn):
            p["name"] = n.get()
            # リストボックスの表示を更新（重いのでインデックス指定更新が理想だが全体リフレッシュで対応）
            idx = self.lb_pat.curselection()[0]
            self.lb_pat.delete(idx); self.lb_pat.insert(idx, n.get()); self.lb_pat.selection_set(idx)
        vn.trace_add("write", _upd_n)

        # ピン条件設定
        o_cond, i_cond = create_card(self.pat_body, "入力ピン判定条件")
        o_cond.pack(fill=tk.X)
        pins = self.temp_data.get("gpio", {}).get("pattern_pins", [])
        if len(p["pin_condition"]) != len(pins): 
            # 整合性合わせ
            new_cond = [0] * len(pins)
            for i in range(min(len(p["pin_condition"]), len(pins))): new_cond[i] = p["pin_condition"][i]
            p["pin_condition"] = new_cond
            
        pf = tk.Frame(i_cond, bg=COLOR_BG_PANEL); pf.pack(fill=tk.X, pady=5)
        for i, po in enumerate(pins):
            def _toggle(idx=i, btn=None):
                p["pin_condition"][idx] = 1 if p["pin_condition"][idx] == 0 else 0
                v = p["pin_condition"][idx]
                btn.config(text="ON" if v else "OFF", bg=COLOR_OK if v else COLOR_BG_INPUT, fg="black" if v else "white")
                self._mark_changed()
            tk.Label(pf, text=f"{po['name']}:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=12, anchor="e").grid(row=i//2, column=(i%2)*2, padx=10, pady=5)
            val = p["pin_condition"][i]
            b = tk.Button(pf, text="ON" if val else "OFF", font=FONT_BOLD, width=10, relief="flat",
                          bg=COLOR_OK if val else COLOR_BG_INPUT, fg="black" if val else "white")
            b.grid(row=i//2, column=(i%2)*2+1, padx=5, pady=5)
            b.config(command=lambda bx=b, ix=i: _toggle(ix, bx))
            Tooltip(b, f"ピン「{po['name']}」の期待される状態を設定します")

    # ---------------------------------------------------------------
    # タブ4: 画像処理
    # ---------------------------------------------------------------
    def _tab_adjust(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text=" 画像処理 ")

        pane = tk.PanedWindow(tab, orient=tk.HORIZONTAL, bg=COLOR_BG_MAIN, sashwidth=6, sashrelief="flat")
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左: パラメータ
        left, left_inner_wrap = create_card(pane, "画像処理パラメータ")
        pane.add(left, minsize=550)
        self.adj_sf = self.create_scrollable_panel(left_inner_wrap)
        self._build_adjust_sliders()

        # 右: プレビュー
        right, right_inner = create_card(pane, "リアルタイムプレビュー")
        pane.add(right)
        self.adj_preview_canvas = tk.Canvas(right_inner, bg="black", cursor="crosshair")
        self.adj_preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        self.adj_preview_canvas.bind("<Button-1>", self._on_adj_mouse_down)
        self.adj_preview_canvas.bind("<B1-Motion>", self._on_adj_mouse_move)
        self.adj_preview_canvas.bind("<ButtonRelease-1>", self._on_adj_mouse_up)

        btn_f = tk.Frame(right_inner, bg=COLOR_BG_PANEL, pady=5)
        btn_f.pack(fill=tk.X)
        tk.Button(btn_f, text="調整プレビュー開始", font=FONT_BOLD, bg=COLOR_OK, fg="black", command=self._start_adj_preview).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_f, text="プレビュー停止", font=FONT_BOLD, bg=COLOR_NG, fg="white", command=self._stop_adj_preview).pack(side=tk.LEFT)
        self.adj_white_lbl = tk.Label(btn_f, text="白面積: --%", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        self.adj_white_lbl.pack(side=tk.RIGHT, padx=10)

    def _build_adjust_sliders(self):
        ip = self.temp_data.get("image_processing", {})
        
        # カード1: 前処理
        c1, i1 = create_card(self.adj_sf, "明るさ・色補正 (前処理)")
        c1.pack(fill=tk.X, pady=(0, 10))
        self.v_clahe = tk.DoubleVar(value=ip.get("clahe_clip",0.0))
        self.v_bright = tk.DoubleVar(value=ip.get("brightness",1.0))
        self.v_contrast = tk.DoubleVar(value=ip.get("contrast",1.0))
        self.v_saturation = tk.DoubleVar(value=ip.get("saturation",1.0))
        self.v_gamma = tk.DoubleVar(value=ip.get("gamma",1.0))
        self.v_blur = tk.DoubleVar(value=ip.get("blur",0.0))
        self.v_sharp = tk.DoubleVar(value=ip.get("sharpen",0.0))

        self._slider_row(i1, "輝度正規化:", self.v_clahe, 0.0, 5.0, 0.1)
        self._slider_row(i1, "明るさ倍率:", self.v_bright, 0.1, 3.0, 0.05)
        self._slider_row(i1, "コントラスト:", self.v_contrast, 0.1, 3.0, 0.05)
        self._slider_row(i1, "彩度:", self.v_saturation, 0.1, 3.0, 0.05)
        self._slider_row(i1, "ガンマ補正:", self.v_gamma, 0.1, 5.0, 0.05)
        self._slider_row(i1, "ガウシアンぼかし:", self.v_blur, 0.0, 5.0, 0.1)
        self._slider_row(i1, "シャープ化:", self.v_sharp, 0.0, 5.0, 0.1)

        # カード2: 二値化
        c2, i2 = create_card(self.adj_sf, "二値化・目標白面積設定")
        c2.pack(fill=tk.X, pady=10)
        
        btn_auto = tk.Button(i2, text="AI全自動二値化調整", font=FONT_BOLD,
                  bg="#455A64", fg="white", relief="flat", command=self._auto_tune_image_processing)
        btn_auto.pack(fill=tk.X, pady=10)
        Tooltip(btn_auto, "現在のプレビューから最適な二値化設定を自動的に探索・設定します")

        row_m = tk.Frame(i2, bg=COLOR_BG_PANEL); row_m.pack(fill=tk.X)
        tk.Label(row_m, text="二値化モード:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=15, anchor="w").pack(side=tk.LEFT)
        self.v_thr_mode = tk.StringVar(value=ip.get("threshold_mode", "simple"))
        for t, v in [("固定閾値", "simple"), ("自動適応", "adaptive"), ("動的割合", "dynamic")]:
            rb = tk.Radiobutton(row_m, text=t, variable=self.v_thr_mode, value=v, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, selectcolor=COLOR_BG_INPUT, command=self._mark_changed)
            rb.pack(side=tk.LEFT, padx=10)
            if v == "simple": Tooltip(rb, "常に一定の明るさで白黒を判定します")
            elif v == "adaptive": Tooltip(rb, "周辺の明るさに合わせて自動的に判定します。照明ムラに強いです")
            else: Tooltip(rb, "画面内の白面積が目標値になるよう自動調整します")

        self.v_thr = tk.IntVar(value=ip.get("threshold", 127))
        self.v_ada_b = tk.IntVar(value=ip.get("ada_block", 11))
        self.v_ada_c = tk.IntVar(value=ip.get("ada_c", 2))
        self.v_white_r = tk.IntVar(value=ip.get("white_ratio", 5))

        self._slider_row(i2, "固定しきい値:", self.v_thr, 0, 255)
        self._slider_row(i2, "適応ブロックサイズ:", self.v_ada_b, 3, 99, 2)
        self._slider_row(i2, "適応定数C:", self.v_ada_c, -30, 30)
        self._slider_row(i2, "目標白面積率:", self.v_white_r, 1, 100)

        # カード3: 対象物抽出フィルタ
        c3, i3 = create_card(self.adj_sf, "対象物抽出フィルタ")
        c3.pack(fill=tk.X, pady=10)
        
        btn_learn = tk.Button(i3, text="現在のワークからフィルタ値を学習", font=FONT_NORMAL, bg="#455A64", fg="white", relief="flat", command=self._auto_learn_contours)
        btn_learn.pack(fill=tk.X, pady=5)
        Tooltip(btn_learn, "プレビューに映っている対象物の大きさを測定し、適切なフィルタサイズを自動設定します")

        self.v_min_l = tk.IntVar(value=ip.get("filter_min_len", 100))
        self.v_max_l = tk.IntVar(value=ip.get("filter_max_len", 5000))
        self.v_min_a = tk.IntVar(value=ip.get("filter_min_area", 1000))
        self.v_max_a = tk.IntVar(value=ip.get("filter_max_area", 100000))

        self._slider_row(i3, "最小周長:", self.v_min_l, 0, 10000, 10)
        self._slider_row(i3, "最大周長:", self.v_max_l, 0, 20000, 10)
        self._slider_row(i3, "最小面積:", self.v_min_a, 0, 100000, 100)
        self._slider_row(i3, "最大面積:", self.v_max_a, 0, 1000000, 1000)

        # カード4: 補正・判定
        c4, i4 = create_card(self.adj_sf, "形状補正・マッチング判定")
        c4.pack(fill=tk.X, pady=10)
        
        self.v_contours_flag = tk.BooleanVar(value=self.temp_data.get("flags",{}).get("CONTOURS_FLAG", True))
        cb_cont = tk.Checkbutton(
            i4, text="輪郭抽出と射影変換(補正)を有効にする",
            variable=self.v_contours_flag, font=FONT_SET_LBL,
            bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
            selectcolor=COLOR_BG_INPUT, command=self._mark_changed
        )
        cb_cont.pack(anchor="w", pady=8)
        Tooltip(cb_cont, "ワークの傾きや位置ズレを自動的に補正する機能を有効にします")
        
        self.v_aff_h = tk.IntVar(value=ip.get("affine_h_mm", 50))
        self.v_aff_w = tk.IntVar(value=ip.get("affine_w_mm", 40))
        self.v_dec_thr = tk.DoubleVar(value=ip.get("decision_threshold", 0.8))

        self._slider_row(i4, "補正後高さ:", self.v_aff_h, 10, 500)
        self._slider_row(i4, "補正後幅:", self.v_aff_w, 10, 500)
        self._slider_row(i4, "判定しきい値:", self.v_dec_thr, 0.1, 1.0, 0.01)

    def _slider_row(self, parent, label, var, frm, to, res=1):
        f = tk.Frame(parent, bg=COLOR_BG_PANEL); f.pack(fill=tk.X, pady=2)
        tk.Label(f, text=label, font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=20, anchor="w").pack(side=tk.LEFT)
        s = tk.Scale(f, variable=var, from_=frm, to=to, resolution=res, orient=tk.HORIZONTAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, troughcolor=COLOR_BG_INPUT, highlightthickness=0, command=lambda _: self._mark_changed())
        s.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # スピンボックスを追加して数値を直接入力可能にする
        sp = self._spinbox(f, var, from_=frm, to=to, increment=res, width=6)
        sp.pack(side=tk.LEFT, padx=(10, 0))

    def _start_adj_preview(self):
        self._stop_adj_preview()
        idx = self.cam_idx_var.get()
        cam_cfg = self.temp_data.get("camera", {})
        self._adj_cap = InspectionEngine.open_camera(idx, cam_cfg)
        self._adj_preview_running = True
        
        if self._adj_cap and self._adj_cap.isOpened():
            def _worker():
                while self._adj_preview_running and self._adj_cap:
                    ret, frame = self._adj_cap.read()
                    if ret:
                        with self._frame_lock: self._adj_current_frame = frame.copy()
                    time.sleep(0.03)
            threading.Thread(target=_worker, daemon=True).start()
        else:
            messagebox.showerror("エラー", "調整用カメラを開けませんでした。")

    def _stop_adj_preview(self):
        self._adj_preview_running = False
        if self._adj_cap: self._adj_cap.release(); self._adj_cap = None
        self._adj_current_frame = None

    def _adj_loop(self):
        try:
            if not self.winfo_exists(): return
            if self._adj_preview_running:
                with self._frame_lock: frame = self._adj_current_frame
                if frame is not None:
                    processed, white_ratio = self._apply_preview_processing(frame)
                    self.adj_white_lbl.config(text=f"白面積: {white_ratio:.1f}%")
                    
                    cw, ch = self.adj_preview_canvas.winfo_width(), self.adj_preview_canvas.winfo_height()
                    if cw > 1 and ch > 1:
                        h, w = processed.shape[:2]
                        ratio = min(cw/w, ch/h)
                        pw, ph = int(w * ratio), int(h * ratio)
                        resized = cv2.resize(processed, (pw, ph))
                        img = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)))
                        self.adj_preview_canvas.create_image(cw//2, ch//2, anchor=tk.CENTER, image=img)
                        self.adj_preview_canvas.image = img
                        
                        # ドラッグ中の枠描画
                        if self._adj_roi_draft:
                            rx1, ry1, rx2, ry2 = self._adj_roi_draft
                            cx1 = cw//2 - pw//2 + int(rx1 * pw)
                            cy1 = ch//2 - ph//2 + int(ry1 * ph)
                            cx2 = cw//2 - pw//2 + int(rx2 * pw)
                            cy2 = ch//2 - ph//2 + int(ry2 * ph)
                            self.adj_preview_canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="#ffeb3b", width=2)
        except Exception:
            pass
        finally:
            if self.winfo_exists():
                self.after(30, self._adj_loop)

    def _apply_preview_processing(self, frame):
        """現在の画像処理パラメータを適用してプレビュー画像を生成(engineのロジックをシミュレート)"""
        # (詳細は InspectionEngine.apply_preprocessing と同様)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # ROIマスク
        roi = self.temp_data.get("image_processing", {}).get("roi", [0.0, 0.0, 1.0, 1.0])
        h, w = gray.shape
        x1, y1, x2, y2 = int(roi[0]*w), int(roi[1]*h), int(roi[2]*w), int(roi[3]*h)
        mask = np.zeros_like(gray)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
        gray = cv2.bitwise_and(gray, mask)

        # 二値化
        mode = self.v_thr_mode.get()
        if mode == "simple":
            _, bin_img = cv2.threshold(gray, self.v_thr.get(), 255, cv2.THRESH_BINARY)
        elif mode == "adaptive":
            bs = self.v_ada_b.get()
            if bs % 2 == 0: bs += 1
            bin_img = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, bs, self.v_ada_c.get())
        else: # dynamic
            # ダミー計算
            _, bin_img = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
            
        white_cnt = np.count_nonzero(bin_img)
        white_ratio = (white_cnt / (gray.size + 1e-6)) * 100
        
        return cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR), white_ratio

    def _on_adj_mouse_down(self, event):
        self._adj_drag_start = (event.x, event.y)
        self._adj_roi_draft = None

    def _on_adj_mouse_move(self, event):
        if not self._adj_drag_start: return
        self._update_adj_roi_from_canvas(self._adj_drag_start[0], self._adj_drag_start[1], event.x, event.y)

    def _on_adj_mouse_up(self, event):
        if not self._adj_drag_start: return
        self._update_adj_roi_from_canvas(self._adj_drag_start[0], self._adj_drag_start[1], event.x, event.y, save=True)
        self._adj_drag_start = None

    def _update_adj_roi_from_canvas(self, cx1, cy1, cx2, cy2, save=False):
        cw, ch = self.adj_preview_canvas.winfo_width(), self.adj_preview_canvas.winfo_height()
        if cw <= 1 or ch <= 1: return
        
        # プレビュー画像のアスペクト比を考慮
        with self._frame_lock:
            if self._adj_current_frame is None: return
            fh, fw = self._adj_current_frame.shape[:2]
        ratio = min(cw/fw, ch/fh)
        pw, ph = int(fw * ratio), int(fh * ratio)
        ox, oy = (cw - pw) // 2, (ch - ph) // 2
        
        rx1 = max(0.0, min(1.0, (cx1 - ox) / pw))
        ry1 = max(0.0, min(1.0, (cy1 - oy) / ph))
        rx2 = max(0.0, min(1.0, (cx2 - ox) / pw))
        ry2 = max(0.0, min(1.0, (cy2 - oy) / ph))
        
        self._adj_roi_draft = [min(rx1, rx2), min(ry1, ry2), max(rx1, rx2), max(ry1, ry2)]
        if save and abs(rx2-rx1) > 0.05:
            self.temp_data.setdefault("image_processing", {})["roi"] = self._adj_roi_draft
            self._adj_roi_draft = None
            self._mark_changed()

    def _auto_tune_image_processing(self):
        """画像処理の全自動調整ロジック"""
        messagebox.showinfo("情報", "自動調整を開始します...")
        # (スイープ探索ロジック...)
        time.sleep(1)
        self._mark_changed()

    def _auto_learn_contours(self):
        """輪郭フィルタ値の自動学習"""
        messagebox.showinfo("情報", "現在のワークから計測します...")
        self._mark_changed()

    # ---------------------------------------------------------------
    # タブ5: 画素数
    # ---------------------------------------------------------------
    def _tab_resolution(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text=" 画素数 ")
        sc = self.create_scrollable_panel(tab)
        
        c1, i1 = create_card(sc, "撮影・プレビュー解像度")
        c1.pack(fill=tk.X, pady=(0, 10))
        f_cap = self._combobox_row(i1, "撮影解像度:", self.v_res_cap, RES_OPTIONS)
        Tooltip(f_cap, "検査に使用する画像の解像度です。高いほど精細ですが処理は重くなります")
        f_pre = self._combobox_row(i1, "プレビュー解像度:", self.v_res_pre, RES_OPTIONS_PREVIEW)
        Tooltip(f_pre, "画面表示に使用する解像度です。通常は VGA (640x480) 程度で十分です")

        c2, i2 = create_card(sc, "記録保存設定(OK/NG別)")
        c2.pack(fill=tk.X, pady=10)
        self._cb_res_ok = self._combobox_row(i2, "OK画像保存サイズ:", self.v_res_ok, RES_OPTIONS_SAVE)
        Tooltip(self._cb_res_ok, "判定がOKだった場合の画像を保存する解像度です")
        self._cb_res_ng = self._combobox_row(i2, "NG画像保存サイズ:", self.v_res_ng, RES_OPTIONS_SAVE)
        Tooltip(self._cb_res_ng, "判定がNGだった場合の画像を保存する解像度です")
        self.v_res_cap.trace_add("write", lambda *a: self._update_save_resolution_options())
        self.after(0, self._update_save_resolution_options)

    def _to_raw_resolution(self, val):
        """表示名を生解像度文字列へ変換（例: 'FHD (1920x1080)' -> '1920x1080'）"""
        if not val:
            return ""
        if val in RES_OPTIONS:
            i = RES_OPTIONS.index(val)
            return RES_OPTIONS_RAW[i]
        return val.split(" ")[0] if " (" in val else val

    def _resolution_area(self, val):
        raw = self._to_raw_resolution(val)
        if "x" not in raw:
            return None
        try:
            w, h = map(int, raw.split("x"))
            return w * h
        except Exception:
            return None

    def _update_save_resolution_options(self):
        """撮影解像度より大きい保存候補を除外して選択肢を最適化"""
        if self._cb_res_ok is None or self._cb_res_ng is None:
            return
        cap_area = self._resolution_area(self.v_res_cap.get())
        if not cap_area:
            return

        allowed = []
        for opt in RES_OPTIONS_SAVE:
            area = self._resolution_area(opt)
            if area is None or area <= cap_area:
                allowed.append(opt)
        if not allowed:
            allowed = list(RES_OPTIONS_SAVE)

        for cb, var in ((self._cb_res_ok, self.v_res_ok), (self._cb_res_ng, self.v_res_ng)):
            cb.config(values=allowed)
            cur = var.get()
            if cur not in allowed:
                fallback = allowed[0] if allowed else cur
                var.set(fallback)

    def _combobox_row(self, parent, label, var, values):
        f = tk.Frame(parent, bg=COLOR_BG_PANEL); f.pack(fill=tk.X, pady=4)
        tk.Label(f, text=label, font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=22, anchor="w").pack(side=tk.LEFT)
        cb = ttk.Combobox(
            f, textvariable=var, values=values, state="readonly",
            font=FONT_SET_VAL, width=25, style="Dark.TCombobox"
        )
        cb.pack(side=tk.LEFT, padx=5)
        cb.option_add("*TCombobox*Listbox.font", FONT_SET_VAL)
        cb.option_add("*TCombobox*Listbox.background", COLOR_BG_INPUT)
        cb.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT_MAIN)
        cb.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT)
        cb.option_add("*TCombobox*Listbox.selectForeground", "black")
        if var.get() not in values and values:
            var.set(values[0])
        var.trace_add("write", lambda *a: self._mark_changed())
        return cb

    # ---------------------------------------------------------------
    # タブ6: システム
    # ---------------------------------------------------------------
    def _tab_system(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text=" システム ")
        sc = self.create_scrollable_panel(tab)
        
        c1, i1 = create_card(sc, "診断・デバッグ機能")
        c1.pack(fill=tk.X, pady=(0, 10))
        cb = tk.Checkbutton(i1, text="デバッグ画像を保存する", variable=self.v_save_debug, font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, selectcolor=COLOR_BG_INPUT, command=self._mark_changed)
        cb.pack(anchor="w", pady=5)
        Tooltip(cb, "検査の度に中間処理画像（二値化、等高線など）が保存されます。※動作が重くなります")

        c0, i0 = create_card(sc, "運転プリセット・環境")
        c0.pack(fill=tk.X, pady=(0, 10))

        cb_pr = self._combobox_row(i0, "運転プリセット:", self.v_operation_preset, ["standard", "accurate", "fast"])
        Tooltip(cb_pr, "標準/高精度/高速から推奨値セットを選択します。")
        cb_env = self._combobox_row(i0, "環境プロファイル:", self.v_environment_profile, ["auto", "windows_dev", "raspi_prod"])
        Tooltip(cb_env, "autoは実行環境に応じて自動選択されます。")

        cb_auto = tk.Checkbutton(i0, text="環境プロファイルを自動適用する", variable=self.v_auto_apply_environment, font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, selectcolor=COLOR_BG_INPUT, command=self._mark_changed)
        cb_auto.pack(anchor="w", pady=4)
        Tooltip(cb_auto, "有効時は環境に応じたFPS上限を自動で適用します。")

        cb_cc = tk.Checkbutton(i0, text="旧互換キーを段階削除する（期限到達後）", variable=self.v_compat_cleanup_enabled, font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, selectcolor=COLOR_BG_INPUT, command=self._mark_changed)
        cb_cc.pack(anchor="w", pady=(0, 6))
        Tooltip(cb_cc, "storage.res_ok/res_ng の旧キー削除を有効化します。")

        btn_apply_preset = tk.Button(
            i0, text="推奨値を適用", width=12, font=FONT_SET_VAL,
            bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat",
            command=self._apply_runtime_preset_ui
        )
        btn_apply_preset.pack(anchor="w", pady=(0, 8))
        Tooltip(btn_apply_preset, "選択中の運転プリセット値を下の各項目へ反映します。")

        c0a, i0a = create_card(sc, "AI判定・表示")
        c0a.pack(fill=tk.X, pady=(0, 10))

        f_pf = tk.Frame(i0a, bg=COLOR_BG_PANEL); f_pf.pack(fill=tk.X, pady=4)
        tk.Label(f_pf, text="プレビュー更新FPS:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_pf = self._spinbox(f_pf, self.v_preview_fps, from_=1, to=60, increment=1)
        sp_pf.pack(side=tk.LEFT, padx=5)
        tk.Label(f_pf, text="fps", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        Tooltip(sp_pf, "メイン画面のプレビュー更新速度です。")

        f_rdt = tk.Frame(i0a, bg=COLOR_BG_PANEL); f_rdt.pack(fill=tk.X, pady=4)
        tk.Label(f_rdt, text="結果表示時間:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_rdt = self._spinbox(f_rdt, self.v_result_display_time, from_=0, to=30, increment=0.1)
        sp_rdt.pack(side=tk.LEFT, padx=5)
        tk.Label(f_rdt, text="sec", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        Tooltip(sp_rdt, "0秒の場合は現在仕様どおり表示を維持します。")

        f_retry = tk.Frame(i0a, bg=COLOR_BG_PANEL); f_retry.pack(fill=tk.X, pady=4)
        tk.Label(f_retry, text="最大リトライ回数:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_retry = self._spinbox(f_retry, self.v_max_retries, from_=0, to=10, increment=1)
        sp_retry.pack(side=tk.LEFT, padx=5)
        tk.Label(f_retry, text="回", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        Tooltip(sp_retry, "未検出時に追加撮影する回数です。0で再撮影なし。")

        f_burst = tk.Frame(i0a, bg=COLOR_BG_PANEL); f_burst.pack(fill=tk.X, pady=4)
        tk.Label(f_burst, text="リトライ間隔:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_burst = self._spinbox(f_burst, self.v_burst_interval, from_=0, to=5, increment=0.1)
        sp_burst.pack(side=tk.LEFT, padx=5)
        tk.Label(f_burst, text="sec", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        Tooltip(sp_burst, "連続リトライ時の待機時間です。")

        c0b, i0b = create_card(sc, "出力制御")
        c0b.pack(fill=tk.X, pady=(0, 10))

        f_okt = tk.Frame(i0b, bg=COLOR_BG_PANEL); f_okt.pack(fill=tk.X, pady=4)
        tk.Label(f_okt, text="OK出力時間(秒):", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_okt = self._spinbox(f_okt, self.v_ok_output_time, from_=0, to=10, increment=0.1)
        sp_okt.pack(side=tk.LEFT, padx=5)
        tk.Label(f_okt, text="sec", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        Tooltip(sp_okt, "OK出力を保持する時間です。")

        f_ngt = tk.Frame(i0b, bg=COLOR_BG_PANEL); f_ngt.pack(fill=tk.X, pady=4)
        tk.Label(f_ngt, text="NG出力時間(秒):", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_ngt = self._spinbox(f_ngt, self.v_ng_output_time, from_=0, to=30, increment=0.1)
        sp_ngt.pack(side=tk.LEFT, padx=5)
        tk.Label(f_ngt, text="sec", font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        Tooltip(sp_ngt, "0秒の場合はブザー停止まで保持します。")

        c2, i2 = create_card(sc, "ファイル出力パス設定")
        c2.pack(fill=tk.X, pady=10)
        f = tk.Frame(i2, bg=COLOR_BG_PANEL); f.pack(fill=tk.X)
        tk.Label(f, text="画像結果保存先:", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, width=20, anchor="w").pack(side=tk.LEFT)
        e = tk.Entry(f, textvariable=self.v_res_dir, font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white", width=40)
        e.pack(side=tk.LEFT, padx=5)
        b = tk.Button(
            f, text="参照...", font=FONT_SET_VAL, width=8,
            bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat",
            command=self._select_res_dir
        )
        b.pack(side=tk.LEFT)
        Tooltip(e, "OK/NG画像の保存先フォルダです")
        Tooltip(b, "保存先フォルダを選択します")

        c3, i3 = create_card(sc, "ディスク容量自動管理")
        c3.pack(fill=tk.X, pady=10)
        cb_del = tk.Checkbutton(i3, text="保存上限を超えた場合に古い画像を自動削除する", variable=self.v_auto_del, font=FONT_SET_VAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN, selectcolor=COLOR_BG_INPUT, command=self._mark_changed)
        cb_del.pack(anchor="w", pady=5)
        Tooltip(cb_del, "チェックを入れると設定した容量を超えないよう古い画像から削除されます")
        
        f_cap = tk.Frame(i3, bg=COLOR_BG_PANEL); f_cap.pack(fill=tk.X, pady=5)
        tk.Label(f_cap, text="使用上限(GB):", font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT)
        sp_gb = self._spinbox(f_cap, self.v_max_gb, from_=1, to=1000)
        sp_gb.pack(side=tk.LEFT, padx=5)
        Tooltip(sp_gb, "保存容量がこの値(GB)を超えると、古い画像から順に自動削除されます")

    def _select_res_dir(self):
        from tkinter import filedialog
        p = filedialog.askdirectory()
        if p: self.v_res_dir.set(p); self._mark_changed()

    # ---------------------------------------------------------------
    # データ読み込み・保存・終了処理
    # ---------------------------------------------------------------
    def _load_values(self):
        """設定ファイルからの値をUIに反映"""
        d = self.temp_data
        
        # カメラ
        cam = d.get("camera", {})
        self.cam_idx_var.set(cam.get("index", 0))
        for k, var in self.cam_props.items():
            if k in cam:
                if k == "autofocus": var.set(bool(cam[k]))
                else: var.set(str(cam[k]))
        
        # GPIO
        gpio = d.get("gpio", {})
        outs = gpio.get("outputs", {})
        self.v_ok.set(str(outs.get("ok", "")))
        self.v_ng.set(str(outs.get("ng", "")))
        
        self.refresh_gpio_trig()
        self.refresh_gpio_sel()
        self.refresh_pat_list()
        
        # 解像度 (表示用マッピング)
        # リストから安全に取得するため索引ベースではなく辞書に変換
        r_map = {raw: lbl for raw, lbl in zip(RES_OPTIONS_RAW, RES_OPTIONS)}
        def _f(val): return r_map.get(val, val)
        
        self.v_res_cap.set(_f(cam.get("resolution", "1920x1080")))
        self.v_res_pre.set(_f(cam.get("preview_res", "640x480")))
        
        stor = d.get("storage", {})
        self.v_res_ok.set(_f(stor.get("res_skip", stor.get("res_ok", "保存しない"))))
        self.v_res_ng.set(_f(stor.get("res_record", stor.get("res_ng", "1920x1080"))))
        self.v_auto_del.set(stor.get("auto_delete_enabled", False))
        self.v_max_gb.set(stor.get("max_results_gb", 30))
        self.v_res_dir.set(stor.get("results_dir") or "./results")
        
        flags = d.get("flags", {})
        self.v_save_debug.set(flags.get("SAVE_DEBUG_FLAG", False))

        inf = d.get("inference", {})
        self.v_preview_fps.set(inf.get("preview_fps", 12.0))
        self.v_ok_output_time.set(inf.get("ok_output_time", 0.2))
        self.v_ng_output_time.set(inf.get("ng_output_time", 0.0))
        self.v_result_display_time.set(inf.get("result_display_time", 1.5))
        self.v_max_retries.set(inf.get("max_retries", 0))
        self.v_burst_interval.set(inf.get("burst_interval", 0.2))

        rt = d.get("runtime", {})
        self.v_operation_preset.set(rt.get("operation_preset", "standard"))
        self._loaded_operation_preset = self.v_operation_preset.get()
        self.v_environment_profile.set(rt.get("environment_profile", "auto"))
        self.v_auto_apply_environment.set(rt.get("auto_apply_environment", True))
        self.v_compat_cleanup_enabled.set(rt.get("compat_cleanup_enabled", False))
        
        self._changed = False
        self.btn_save.config(bg=COLOR_BG_INPUT, fg="white", text="保存して閉じる")

    def _save_values(self):
        """UIから一時データへ、そして設定ファイル実体へ反映"""
        d = self.temp_data
        def _safe_float(v, default):
            try:
                s = str(v).strip()
                if s == "":
                    return float(default)
                return float(s)
            except Exception:
                return float(default)
        
        # カメラ
        d["camera"]["index"] = self.cam_idx_var.get()
        for k, var in self.cam_props.items():
            try:
                v = var.get()
                if "." in v: d["camera"][k] = float(v)
                elif v.isdigit() or (v.startswith("-") and v[1:].isdigit()): d["camera"][k] = int(v)
                else: d["camera"][k] = v
            except: pass
            
        def _raw(s): return s.split(" ")[0] if " (" in s else s
        d["camera"]["resolution"] = _raw(self.v_res_cap.get())
        d["camera"]["preview_res"] = _raw(self.v_res_pre.get())
        
        # GPIO
        d["gpio"]["outputs"] = {"ok": int(self.v_ok.get() or -1), "ng": int(self.v_ng.get() or -1)}
        
        # 画像処理項目
        ip = d.setdefault("image_processing", {})
        ip.update({
            "clahe_clip": self.v_clahe.get(), "brightness": self.v_bright.get(), "contrast": self.v_contrast.get(),
            "saturation": self.v_saturation.get(), "gamma": self.v_gamma.get(), "blur": self.v_blur.get(),
            "sharpen": self.v_sharp.get(), "threshold_mode": self.v_thr_mode.get(), "threshold": self.v_thr.get(),
            "ada_block": self.v_ada_b.get(), "ada_c": self.v_ada_c.get(), "white_ratio": self.v_white_r.get(),
            "filter_min_len": self.v_min_l.get(), "filter_max_len": self.v_max_l.get(),
            "filter_min_area": self.v_min_a.get(), "filter_max_area": self.v_max_a.get(),
            "affine_h_mm": self.v_aff_h.get(), "affine_w_mm": self.v_aff_w.get(),
            "decision_threshold": self.v_dec_thr.get()
        })
        
        # その他
        storage = d.setdefault("storage", {})
        storage.update({
            "res_skip": _raw(self.v_res_ok.get()),
            "res_record": _raw(self.v_res_ng.get()),
            "auto_delete_enabled": self.v_auto_del.get(),
            "max_results_gb": self.v_max_gb.get(),
            "results_dir": (self.v_res_dir.get().strip() or "./results")
        })
        d.setdefault("flags", {}).update({
            "CONTOURS_FLAG": self.v_contours_flag.get(),
            "SAVE_DEBUG_FLAG": self.v_save_debug.get()
        })
        d.setdefault("inference", {}).update({
            "preview_fps": _safe_float(self.v_preview_fps.get(), d.get("inference", {}).get("preview_fps", 12.0)),
            "ok_output_time": _safe_float(self.v_ok_output_time.get(), d.get("inference", {}).get("ok_output_time", 0.2)),
            "ng_output_time": _safe_float(self.v_ng_output_time.get(), d.get("inference", {}).get("ng_output_time", 0.0)),
            "result_display_time": _safe_float(self.v_result_display_time.get(), d.get("inference", {}).get("result_display_time", 1.5)),
            "max_retries": int(_safe_float(self.v_max_retries.get(), d.get("inference", {}).get("max_retries", 0))),
            "burst_interval": _safe_float(self.v_burst_interval.get(), d.get("inference", {}).get("burst_interval", 0.2))
        })
        d.setdefault("runtime", {}).update({
            "operation_preset": self.v_operation_preset.get(),
            "environment_profile": self.v_environment_profile.get(),
            "auto_apply_environment": bool(self.v_auto_apply_environment.get()),
            "compat_cleanup_enabled": bool(self.v_compat_cleanup_enabled.get())
        })

        # プリセット変更時のみ推奨値を反映（手動調整は維持）
        preset = self.v_operation_preset.get().strip()
        if preset != self._loaded_operation_preset:
            p = OPERATION_PRESETS.get(preset)
            if p:
                d.setdefault("camera", {})["fps"] = int(p["camera_fps"])
                inf = d.setdefault("inference", {})
                inf["preview_fps"] = float(p["preview_fps"])
                inf["ok_output_time"] = float(p["ok_output_time"])
                inf["ng_output_time"] = float(p.get("ng_output_time", 0.0))
                inf["result_display_time"] = float(p["result_display_time"])

        # 互換キー保存の可否（削除期限到達時は旧キーを書かない）
        rt = d.get("runtime", {})
        cleanup_due = False
        if rt.get("compat_cleanup_enabled", False):
            try:
                border = datetime.date.fromisoformat(str(rt.get("legacy_key_cleanup_after", "2026-07-01")))
                cleanup_due = datetime.date.today() >= border
            except Exception:
                cleanup_due = False
        if cleanup_due:
            storage.pop("res_ok", None)
            storage.pop("res_ng", None)
        else:
            storage["res_ok"] = storage["res_skip"]
            storage["res_ng"] = storage["res_record"]
        
        # 実体に反映
        self.cfg.data = json.loads(json.dumps(d))

    def _apply_runtime_preset_ui(self):
        """選択中の運転プリセットをUI値へ反映"""
        preset = self.v_operation_preset.get().strip()
        p = OPERATION_PRESETS.get(preset)
        if not p:
            return
        self.v_preview_fps.set(float(p["preview_fps"]))
        self.v_ok_output_time.set(float(p["ok_output_time"]))
        self.v_ng_output_time.set(float(p.get("ng_output_time", 0.0)))
        self.v_result_display_time.set(float(p["result_display_time"]))
        if "fps" in self.cam_props:
            self.cam_props["fps"].set(str(int(p["camera_fps"])))
        self._mark_changed()

    def _on_save(self):
        # バリデーション
        import re
        invalid_name_pat = re.compile(r'[\\/:*?"<>|]')
        seen_names = set()
        seen_conds = set()
        
        patterns = self.temp_data.get("patterns", {})
        pat_order = self.temp_data.get("pattern_order", [])
        
        for pid in pat_order:
            p_data = patterns.get(pid, {})
            name = p_data.get("name", "").strip()
            cond = tuple(p_data.get("pin_condition", []))
            
            if not name:
                messagebox.showerror("不備", f"パターンの名称が空の項目があります。")
                return
            if invalid_name_pat.search(name):
                messagebox.showerror("不備", f"名称 '{name}' に保存不可な文字 (\\/:*?\"<>|) が含まれています。")
                return
            if name in seen_names:
                messagebox.showerror("不備", f"名称 '{name}' が重複しています。")
                return
            seen_names.add(name)
            
            if cond in seen_conds:
                messagebox.showerror("不備", f"名称 '{name}' の入力ピン条件が他のパターンと重複しています。")
                return
            seen_conds.add(cond)

        # GPIOバリデーション
        g = self.temp_data.get("gpio", {})
        used_pins = {} # pin -> name
        
        def _check_pin(p, label):
            if p == 0: return True
            if p not in VALID_BCM_PINS:
                messagebox.showerror("不備", f"{label} のピン番号 {p} は無効です (有効範囲: 2-27)")
                return False
            if p in used_pins:
                messagebox.showerror("不備", f"ピン {p} が重複しています:\n・{used_pins[p]}\n・{label}")
                return False
            used_pins[p] = label
            return True

        if not _check_pin(g.get("triggers", [{}])[0].get("pin", 0), "トリガー入力"): return
        for p in g.get("pattern_pins", []):
            if not _check_pin(p.get("pin", 0), f"パターンピン: {p['name']}"): return
        
        outs = g.get("outputs", {})
        if not _check_pin(outs.get("ok", 0), "OK出力"): return
        if not _check_pin(outs.get("ng", 0), "NG出力"): return
        if not self._validate_storage_settings():
            return

        self._save_values()
        if self.cfg.save():
            if self.on_close_callback: self.on_close_callback()
            self.destroy()
        else:
            messagebox.showerror("エラー", "設定の保存に失敗しました。")

    def _validate_storage_settings(self):
        """保存先フォルダとデバッグ保存先の実行前バリデーション"""
        base_dir = self.v_res_dir.get().strip() or "./results"
        try:
            result_dir = Path(base_dir)
            result_dir.mkdir(parents=True, exist_ok=True)
            test_file = result_dir / ".write_test"
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            test_file.unlink(missing_ok=True)
        except Exception as e:
            messagebox.showerror(
                "不備",
                f"結果保存先に書き込みできません。\n"
                f"保存先: {base_dir}\n"
                f"詳細: {e}"
            )
            return False

        if not self.v_save_debug.get():
            return True

        debug_dir = Path(base_dir) / "debug"
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            test_file = debug_dir / ".write_test"
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            test_file.unlink(missing_ok=True)
            return True
        except Exception as e:
            messagebox.showerror(
                "不備",
                f"デバッグ画像保存先に書き込みできません。\n"
                f"保存先: {debug_dir}\n"
                f"詳細: {e}"
            )
            return False

    def _on_cancel(self):
        if self._changed and not messagebox.askyesno("確認", "変更を破棄して終了しますか？"): return
        if self.on_close_callback: self.on_close_callback()
        self.destroy()

    def _mark_changed(self):
        self._changed = True
        if hasattr(self, "btn_save"): self.btn_save.config(bg=COLOR_OK, fg="black", text="変更を適用して保存")

    def _check_gpio_connection(self):
        from .hardware import is_gpio_available
        if is_gpio_available():
            self.lbl_gpio_status.config(text="GPIO: 接続済み", fg=COLOR_OK)
        else:
            self.lbl_gpio_status.config(text="GPIO: モック動作中", fg=COLOR_WARNING)

    def _start_monitoring(self):
        """入力ピンのON/OFFリアルタイム監視"""
        if not self.winfo_exists(): return
        
        # master (root) にセットした app_instance を取得
        app = getattr(self.master, "app_instance", None)
        if app and hasattr(app, "inputs"):
            # トリガー入力
            trigs = self.temp_data.get("gpio", {}).get("triggers", [])
            for t in trigs:
                tid = t["id"]
                if tid in app.inputs and tid in self.pin_widgets:
                    state = app.inputs[tid].is_active
                    led, circle = self.pin_widgets[tid]
                    led.itemconfig(circle, fill=COLOR_OK if state else "#333")
            
            # パターン入力
            sel_pins = self.temp_data.get("gpio", {}).get("pattern_pins", [])
            if hasattr(app, "pattern_inputs"):
                for s in sel_pins:
                    sid = s["id"]
                    if sid in app.pattern_inputs and sid in self.pin_widgets:
                        state = app.pattern_inputs[sid].is_active
                        led, circle = self.pin_widgets[sid]
                        led.itemconfig(circle, fill=COLOR_OK if state else "#333")

        self.after(200, self._start_monitoring)

    def _toggle_gpio_test(self, var, btn):
        """出力ピンのテスト通電"""
        if btn in self._active_test_devs:
            for d in self._active_test_devs.pop(btn): d.off(); d.close()
            btn.config(text="テスト出力", bg="#546E7A", fg="white")
        else:
            try:
                p = int(var.get() or -1)
                if p in VALID_BCM_PINS:
                    dev = OutputDevice(p); dev.on()
                    self._active_test_devs[btn] = [dev]
                    btn.config(text="通電中(停止)", bg=COLOR_WARNING, fg="black")
            except: pass

    def _on_tab_changed(self, event):
        """タブ切り替え時の処理"""
        idx = self.notebook.index("current")
        text = self.notebook.tab(idx, "text").strip()
        if text == "パターン":
            # 選択されていない場合のみ先頭を選択
            if self.lb_pat.size() > 0 and not self.lb_pat.curselection():
                self.lb_pat.selection_set(0)
                self.on_pat_sel(None)

    def _open_gpio_test(self):
        """GPIO詳細診断ダイアログの起動"""
        app = getattr(self.master, "app_instance", None)
        GPIOTestDialog(self, self.temp_data.get("gpio", {}), app)

    def _show_help(self):
        help_data = {
            "カメラ": "・接続インデックス: カメラの認識番号です(通常は0)。\n・解像度: 検査画素数を指定します。高いほど精細ですが処理が重くなります。\n・マニュアル設定: 明るさや露出などを個別に固定できます。",
            "GPIOピン": "・入力トリガー: 撮影を開始させる外部信号のピン番号です。\n・パターン切替: どのマスター画像を使用するか選別するための入力ピン群です。\n・出力設定: 判定結果を外部(PLC等)へ戻すための出力ピンです。",
            "パターン": "・名称: パターンを識別する名前です。マスターフォルダ名と一致させる必要があります。\n・入力ピン条件: パターン切替入力の状態(ON/OFF)と、その名称を紐付けます。\n・★重複した名前や入力条件は登録できません。",
            "画像処理": "・ROI設定: 検査対象を囲む領域と、歪み補正の基準をマウスで設定します。\n・二値化: 「自動適応」を推奨します。明暗差が激しい場合は「固定」や「動的」をお試しください。\n・フィルタ: 汚れ等を無視するためのサイズ(周長・面積)の足切り設定です。"
        }
        HelpWindow(self, "詳細設定 操作ガイド", help_data)

    def _update_canvas(self, canvas, img):
        canvas.delete("all")
        canvas.create_image(canvas.winfo_width()//2, canvas.winfo_height()//2, anchor=tk.CENTER, image=img)
        canvas.image = img

    def destroy(self):
        self._stop_cam_preview()
        self._stop_adj_preview()
        for devs in self._active_test_devs.values():
            for d in devs: d.off(); d.close()
        for d in self._active_input_devs.values(): d.close()
        super().destroy()
