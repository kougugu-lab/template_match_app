#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dialogs.py - 設定ダイアログ (SettingsDialog)
タブ構成: カメラ / GPIOピン / 調整 / 画素数・保存 / システム
"""

import cv2
import threading
import time
import platform
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import numpy as np

from .constants import (
    COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT,
    COLOR_OK, COLOR_NG, COLOR_WARNING, COLOR_BORDER, COLOR_NG_MUTED,
    FONT_FAMILY, FONT_NORMAL, FONT_BOLD, FONT_LARGE,
    FONT_SET_TAB, FONT_SET_LBL, FONT_SET_VAL, FONT_BTN_LARGE,
    VALID_BCM_PINS, RES_OPTIONS_RAW, CAM_PROP_MAP
)
from .widgets import create_card, Tooltip, HelpWindow


class SettingsDialog(tk.Toplevel):
    """詳細設定ダイアログ（inspection_app のスタイルに準拠）"""

    def __init__(self, parent, config_manager, on_close_callback=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.on_close_callback = on_close_callback
        self._changed = False
        self._preview_running = False
        self._preview_cap = None
        self._preview_thread = None
        
        self._adj_preview_running = False
        self._adj_cap = None
        self._adj_current_frame = None
        self._frame_lock = threading.Lock()

        self.title("詳細設定")
        self.geometry("1400x900")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._build_ui()
        self._load_values()
        self._adj_loop()

    def _build_ui(self):
        """UI全体を構築"""
        # --- タブ ---
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Dark.TNotebook", background=COLOR_BG_MAIN, borderwidth=0)
        style.configure("Dark.TNotebook.Tab",
                        background=COLOR_BG_PANEL, foreground=COLOR_TEXT_MAIN,
                        padding=[20, 10], font=FONT_SET_TAB, focuscolor=COLOR_BG_MAIN)
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", COLOR_ACCENT)],
                  foreground=[("selected", "black")])

        self.notebook = ttk.Notebook(self, style="Dark.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # タブ作成
        self._tab_camera()
        self._tab_gpio()
        self._tab_adjust()
        self._tab_resolution()
        self._tab_system()

        # --- 下部ボタン ---
        btn_bar = tk.Frame(self, bg=COLOR_BG_MAIN, pady=8)
        btn_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=10)

        # 左端: ヘルプ
        tk.Button(btn_bar, text="ヘルプ", font=FONT_BOLD, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", width=10,
                  command=self._show_help).pack(side=tk.LEFT, padx=5)

        # 右端: キャンセル、保存
        self.btn_save = tk.Button(btn_bar, text="保存して閉じる",
                                  font=FONT_BOLD, bg=COLOR_BG_INPUT,
                                  fg=COLOR_TEXT_MAIN, relief="flat", width=18,
                                  command=self._on_save)
        self.btn_save.pack(side=tk.RIGHT, padx=5)
        Tooltip(self.btn_save, "全ての変更を確定して保存し、メイン画面に反映します（変更時は緑色に強調されます）")

        btn_cncl = tk.Button(btn_bar, text="キャンセル", font=FONT_BOLD, bg=COLOR_BG_INPUT,
                   fg=COLOR_TEXT_MAIN, relief="flat", width=12,
                   command=self._on_cancel)
        btn_cncl.pack(side=tk.RIGHT, padx=5)
        Tooltip(btn_cncl, "設定を保存せずに終了します")

    # ---------------------------------------------------------------
    # タブ: カメラ
    # ---------------------------------------------------------------
    def _tab_camera(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text="カメラ")

        pane = tk.PanedWindow(tab, orient=tk.HORIZONTAL, bg=COLOR_BG_MAIN,
                              sashwidth=6, sashrelief="flat")
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左: 設定
        left, left_inner_wrap = create_card(pane, "カメラ設定")
        pane.add(left, minsize=360)
        
        # Scrollable panel for camera settings
        scroll_c = tk.Canvas(left_inner_wrap, bg=COLOR_BG_PANEL, highlightthickness=0)
        vsb = ttk.Scrollbar(left_inner_wrap, orient="vertical", command=scroll_c.yview)
        left_inner = tk.Frame(scroll_c, bg=COLOR_BG_PANEL)
        left_inner.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_c.create_window((0, 0), window=left_inner, anchor="nw")
        scroll_c.configure(yscrollcommand=vsb.set)
        scroll_c.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        scroll_c.bind("<MouseWheel>", lambda e: scroll_c.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # カメラインデックス
        row_f = tk.Frame(left_inner, bg=COLOR_BG_PANEL)
        row_f.pack(fill=tk.X, pady=5)
        tk.Label(row_f, text="カメラインデックス:", font=FONT_SET_LBL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=18, anchor="w").grid(row=0, column=0, pady=5)
        self.cam_idx_var = tk.IntVar()
        tk.Spinbox(row_f, textvariable=self.cam_idx_var, from_=0, to=10,
                   font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white",
                   buttonbackground="#78909C", bd=1, relief="solid", width=8,
                   command=self._mark_changed).grid(row=0, column=1, padx=10, pady=5)
        tk.Button(row_f, text="自動", font=FONT_NORMAL, bg="#455A64", fg="white", relief="flat", padx=8,
                  command=self._search_cameras).grid(row=0, column=2, padx=(0, 10), pady=5)
        
        # --- 全項目自動設定ボタン ---
        btn_all_auto = tk.Button(left_inner, text="✨ 全項目を自動設定(露出/Focus/WB)", font=FONT_BOLD,
                                 bg=COLOR_ACCENT, fg="black", relief="flat", height=2,
                                 command=lambda: self._auto_tune_all_camera_props(btn_all_auto))
        btn_all_auto.pack(fill=tk.X, pady=(0, 15), padx=5)
        Tooltip(btn_all_auto, "露出・フォーカス・ホワイトバランス等を順番に自動走査して最適化します（約20秒かかります）")

        # --- 詳細プロパティ ---
        props_f = tk.Frame(left_inner, bg=COLOR_BG_PANEL)
        props_f.pack(fill=tk.X, pady=5)

        self.cam_props = {}
        prop_defs = [
            ("fps", "FPS", 1, 120),
            ("focus", "フォーカス", 0, 1023),
            ("gain", "ゲイン", 0, 255),
            ("exposure", "露出", -10, 10000),
            ("brightness", "明るさ", -255, 255),
            ("contrast", "コントラスト", 0, 255),
            ("saturation", "彩度", 0, 255),
            ("hue", "色相", -180, 180),
            ("wb_temp", "ホワイトバランス", 2000, 10000),
            ("zoom", "ズーム", 1, 10)
        ]

        # プレビューに即時反映するためのコールバック
        def _apply_cam_prop(k, val_var):
            self._mark_changed()
            if self._preview_cap and self._preview_cap.isOpened():
                try:
                    val = float(val_var.get())
                    if k in CAM_PROP_MAP:
                        self._preview_cap.set(CAM_PROP_MAP[k], val)
                except ValueError:
                    pass

        prop_tips = {
            "fps": "1秒あたりのフレーム数。通常は5～30。低いほどCPU負荷軽減。",
            "focus": "カメラのフォーカス位置 (0-1023)。UVCカメラ機能。",
            "gain": "センサー感度 (0-255)。暗い場合に上げますがノイズも増えます。",
            "exposure": "露出時間。Windowsでは負の値(-11等)、Linuxでは大きな正の値。",
            "brightness": "画像の明るさ補正 (-255 to 255)。",
            "contrast": "コントラスト補正 (0-255)。",
            "saturation": "彩度（色の濃さ）補正 (0-255)。",
            "hue": "色相補正 (-180 to 180)。",
            "wb_temp": "ホワイトバランス色温度 (2000-10000)。",
            "zoom": "デジタルズーム倍率 (1-10)。"
        }

        r = 0
        for k, lbl, min_v, max_v in prop_defs:
            tk.Label(props_f, text=lbl+":", font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=18, anchor="w").grid(row=r, column=0, pady=2)
            var = tk.StringVar()
            self.cam_props[k] = var
            var.trace_add("write", lambda *a, key=k, v=var: _apply_cam_prop(key, v))
            sp = tk.Spinbox(props_f, textvariable=var, from_=min_v, to=max_v,
                       font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white",
                       buttonbackground="#78909C", bd=1, relief="solid", width=10)
            sp.grid(row=r, column=1, padx=10, pady=2, sticky="w")
            Tooltip(sp, prop_tips.get(k, ""))
            
            if k in ("focus", "wb_temp", "hue", "exposure"):
                action = self._auto_search_exposure if k == "exposure" else lambda key=k: self._auto_tune_prop(key)
                tk.Button(props_f, text="自動", font=FONT_NORMAL, bg="#455A64", fg="white", relief="flat", padx=8,
                          command=action).grid(row=r, column=2, padx=(0, 10), pady=2)
            
            r += 1

        self.cam_search_result = tk.Label(left_inner, text="", font=FONT_NORMAL,
                                          bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, wraplength=320)
        self.cam_search_result.pack(pady=5)

        # 右: プレビュー
        right, right_inner = create_card(pane, "テストプレビュー")
        pane.add(right)
        self.cam_preview_canvas = tk.Canvas(right_inner, bg="black", width=640, height=480)
        self.cam_preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        preview_btn_f = tk.Frame(right_inner, bg=COLOR_BG_PANEL)
        preview_btn_f.pack(fill=tk.X, pady=5)
        
        tk.Button(preview_btn_f, text="プレビュー開始", font=FONT_BOLD,
                  bg=COLOR_OK, fg="black", relief="flat",
                  command=self._start_cam_preview).pack(side=tk.LEFT, padx=5)
        tk.Button(preview_btn_f, text="プレビュー停止", font=FONT_BOLD,
                  bg=COLOR_NG, fg="white", relief="flat",
                  command=self._stop_cam_preview).pack(side=tk.LEFT, padx=5)

    def _search_cameras(self, event_or_btn=None):
        self.cam_search_result.config(text="カメラを検索中...", fg=COLOR_WARNING)
        # Optional: Disable button if passed
        if isinstance(event_or_btn, tk.Widget):
            event_or_btn.config(state=tk.DISABLED, text="検索中...")

        def _worker():
            found = []
            for i in range(5):
                if platform.system() == "Windows":
                    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                else:
                    cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    found.append(i)
                    cap.release()
            
            def _apply():
                if isinstance(event_or_btn, tk.Widget):
                    event_or_btn.config(state=tk.NORMAL, text="自動")
                if found:
                    self.cam_search_result.config(text=f"検出: インデックス {found}", fg=COLOR_OK)
                    if self.cam_idx_var.get() not in found:
                        self.cam_idx_var.set(found[0])
                        self._mark_changed()
                else:
                    self.cam_search_result.config(text="カメラが検出されませんでした", fg=COLOR_NG)
            self.after(0, _apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _start_cam_preview(self):
        self._stop_cam_preview()
        self._preview_running = True
        idx = self.cam_idx_var.get()
        if platform.system() == "Windows":
            self._preview_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        else:
            self._preview_cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        self._preview_thread = threading.Thread(
            target=self._cam_preview_worker, daemon=True)
        self._preview_thread.start()
        
        # 起動直後に現在の設定値を適用
        def _apply_init_props():
            if self._preview_cap and self._preview_cap.isOpened():
                for k, p in CAM_PROP_MAP.items():
                    try:
                        v = float(self.cam_props[k].get())
                        self._preview_cap.set(p, v)
                    except ValueError:
                        pass
        self.after(500, _apply_init_props)

    def _cam_preview_worker(self):
        while self._preview_running and self._preview_cap:
            ret, frame = self._preview_cap.read()
            if ret:
                frame_rgb = cv2.cvtColor(cv2.resize(frame, (640, 480)), cv2.COLOR_BGR2RGB)
                img = ImageTk.PhotoImage(Image.fromarray(frame_rgb))
                self.after(0, lambda i=img: self._update_canvas(
                    self.cam_preview_canvas, i))
            time.sleep(0.05)

    def _stop_cam_preview(self):
        self._preview_running = False
        if self._preview_cap:
            self._preview_cap.release()
            self._preview_cap = None

    def _auto_tune_all_camera_props(self, btn_widget=None):
        """全てのプロパティ（露出 → フォーカス → WB → 色相）を順番に自動調整する"""
        if getattr(self, '_preview_cap', None) is None or not self._preview_cap.isOpened():
            messagebox.showwarning("警告", "プレビューを開始してから実行してください。")
            return
            
        if btn_widget:
            btn_widget.config(state=tk.DISABLED, text="一括調整中...")

        def _worker():
            try:
                # 1. 露出
                self._internal_sweep_exposure()
                # 2. フォーカス
                self._internal_sweep_prop("focus")
                # 3. ホワイトバランス
                self._internal_sweep_prop("wb_temp")
                # 4. 色相
                self._internal_sweep_prop("hue")
                
                self.after(0, lambda: messagebox.showinfo("完了", "全項目の自動設定が完了しました。"))
            except Exception as e:
                self.after(0, lambda err=str(e): messagebox.showerror("エラー", f"一括調整中にエラーが発生しました:\n{err}"))
            finally:
                if btn_widget:
                    self.after(0, lambda: btn_widget.config(state=tk.NORMAL, text="✨ 全項目を自動設定(露出/Focus/WB)"))

        threading.Thread(target=_worker, daemon=True).start()

    def _internal_sweep_prop(self, prop_key):
        """プロパティ単体のスイープ（内部用スレッドセーフ）"""
        self.after(0, lambda: self.cam_search_result.config(text=f"{prop_key} を自動調整中...", fg=COLOR_WARNING))
        best_val = float(self.cam_props[prop_key].get())
        
        prop_cv2 = {
            "focus": cv2.CAP_PROP_FOCUS,
            "wb_temp": cv2.CAP_PROP_TEMPERATURE,
            "hue": cv2.CAP_PROP_HUE
        }[prop_key]
        
        if prop_key == "focus":
            test_vals = list(range(0, 300, 10))
            best_score = -1.0
        elif prop_key == "wb_temp":
            test_vals = list(range(2000, 8000, 200))
            best_score = 9999.0
        elif prop_key == "hue":
            test_vals = list(range(-180, 180, 20))
            best_score = 9999.0
        else:
            return

        for val in test_vals:
            if not self._preview_running: return
            self._preview_cap.set(prop_cv2, float(val))
            time.sleep(0.3)
            self._preview_cap.read()
            ret, frame = self._preview_cap.read()
            if ret:
                if prop_key == "focus":
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    score = cv2.Laplacian(gray, cv2.CV_64F).var()
                    if score > best_score:
                        best_score, best_val = score, val
                else:
                    b, g, r = cv2.mean(frame)[:3]
                    if prop_key == "wb_temp":
                        score = abs(r - b) + abs(g - (r+b)/2)
                    else: # hue
                        score = np.std([b, g, r])
                    if score < best_score:
                        best_score, best_val = score, val

        self._preview_cap.set(prop_cv2, float(best_val))
        self.after(0, lambda v=best_val: [self.cam_props[prop_key].set(str(v)), self._mark_changed()])

    def _internal_sweep_exposure(self):
        """露出のスイープ（内部用）"""
        self.after(0, lambda: self.cam_search_result.config(text="適正露出を探索中...", fg=COLOR_WARNING))
        target_brightness = 130.0
        min_diff = 999.0
        best_exp = float(self.cam_props["exposure"].get())
        
        test_vals = list(range(-11, 2)) if platform.system() == "Windows" else list(range(10, 500, 50))
        for exp in test_vals:
            if not self._preview_running: return
            self._preview_cap.set(cv2.CAP_PROP_EXPOSURE, float(exp))
            time.sleep(0.4)
            self._preview_cap.read()
            ret, frame = self._preview_cap.read()
            if ret:
                mean_val = np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
                diff = abs(mean_val - target_brightness)
                if diff < min_diff:
                    min_diff, best_exp = diff, exp
                    if diff < 10: break

        self._preview_cap.set(cv2.CAP_PROP_EXPOSURE, float(best_exp))
        self.after(0, lambda v=best_exp: [self.cam_props["exposure"].set(str(v)), self._mark_changed()])

    def _auto_tune_prop(self, prop_key, btn_widget=None):
        """ボタンから呼び出される単体調整"""
        if getattr(self, '_preview_cap', None) is None or not self._preview_cap.isOpened():
            messagebox.showwarning("警告", "プレビューを開始してから実行してください。")
            return
        if btn_widget:
            btn_widget.config(state=tk.DISABLED, text="調整中...")
        def _sweep():
            self._internal_sweep_prop(prop_key)
            self.after(0, lambda: [btn_widget.config(state=tk.NORMAL, text="自動") if btn_widget else None,
                                   self.cam_search_result.config(text=f"{prop_key} 探索完了", fg=COLOR_OK)])
        threading.Thread(target=_sweep, daemon=True).start()

    def _auto_search_exposure(self, btn_widget=None):
        """ボタンから呼び出される単体露出調整"""
        if getattr(self, '_preview_cap', None) is None or not self._preview_cap.isOpened():
            messagebox.showwarning("警告", "プレビューを開始してから実行してください。")
            return
        if btn_widget:
            btn_widget.config(state=tk.DISABLED, text="探索中...")
        def _sweep():
            self._internal_sweep_exposure()
            self.after(0, lambda: [btn_widget.config(state=tk.NORMAL, text="自動") if btn_widget else None,
                                   self.cam_search_result.config(text="露出 探索完了", fg=COLOR_OK),
                                   messagebox.showinfo("完了", "露出の自動設定が完了しました。")])
        threading.Thread(target=_sweep, daemon=True).start()

    # ---------------------------------------------------------------
    # タブ: GPIOピン
    # ---------------------------------------------------------------
    def _tab_gpio(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text="GPIOピン")

        # Split into Left and Right (Left side gets much more space to prevent clipping)
        tab.columnconfigure(0, weight=4)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)

        # Left: Settings
        left_f = tk.Frame(tab, bg=COLOR_BG_MAIN)
        left_f.grid(row=0, column=0, sticky="nsew", padx=5, pady=10)

        outer, inner = create_card(left_f, "GPIOピン設定 (BCM番号)")
        outer.pack(fill=tk.BOTH, expand=True)

        scroll_c = tk.Canvas(inner, bg=COLOR_BG_PANEL, highlightthickness=0)
        vsb = ttk.Scrollbar(inner, orient="vertical", command=scroll_c.yview)
        sf = tk.Frame(scroll_c, bg=COLOR_BG_PANEL)
        sf.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_c.create_window((0, 0), window=sf, anchor="nw")
        scroll_c.configure(yscrollcommand=vsb.set)
        scroll_c.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        scroll_c.bind("<MouseWheel>", lambda e: scroll_c.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        pin_descriptions = {
            "pin_Start": "トリガー入力",
            "pin_OKlog": "OK出力",
            "pin_NGlog": "NG出力",
        }
        self.gpio_vars = {}
        for row, (pname, desc) in enumerate(pin_descriptions.items()):
            tk.Label(sf, text=desc, font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=22, anchor="w").grid(
                row=row, column=0, padx=15, pady=4, sticky="w")
            var = tk.StringVar()
            self.gpio_vars[pname] = var
            sp = tk.Spinbox(sf, textvariable=var, from_=0, to=40,
                            font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white",
                            buttonbackground="#78909C", bd=1, relief="solid", width=8,
                            command=self._mark_changed)
            sp.grid(row=row, column=1, padx=10, pady=4)
            sp.bind("<FocusIn>", lambda e, v=var: setattr(self, "active_entry", v))
            Tooltip(sp, f"BCMピン番号 (0-40)。未使用は 0 のままにしてください。")

            # 出力ピン（OK/NG）のみテストボタンを表示
            if "out" in pname.lower() or "log" in pname.lower():
                btn_test = tk.Button(sf, text="テスト", font=(FONT_FAMILY, 10), bg="#546E7A", fg="white",
                                     relief="flat", padx=8, command=lambda v=var: self._test_gpio_pulse(v))
                btn_test.grid(row=row, column=2, padx=5, pady=4)
                Tooltip(btn_test, "クリックするとこのピンを0.5秒間だけON（信号出力）にします")

        # 仕様マッピングセクション
        self._build_spec_mapping(sf, len(pin_descriptions), tab)

    def _build_spec_mapping(self, parent, start_row, tab_frame):
        sep = tk.Frame(parent, bg=COLOR_BORDER, height=2)
        sep.grid(row=start_row, column=0, columnspan=3,
                 sticky="ew", padx=5, pady=15)

        tk.Label(parent, text="仕様マッピング (仕様ID → ピン番号・名前)",
                 font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).grid(
            row=start_row + 1, column=0, columnspan=3, padx=15, pady=(0, 8), sticky="w")

        hdr = tk.Frame(parent, bg=COLOR_BG_PANEL)
        hdr.grid(row=start_row + 2, column=0, columnspan=3, padx=15, sticky="w")
        for txt, w in [("仕様ID", 10), ("名前", 12), ("使用ピン (カンマ区切り)", 25)]:
            tk.Label(hdr, text=txt, font=FONT_BOLD, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=w, anchor="w").pack(side=tk.LEFT)

        self.spec_list_f = tk.Frame(parent, bg=COLOR_BG_PANEL)
        self.spec_list_f.grid(row=start_row + 3, column=0, columnspan=3, padx=15, sticky="w")

        self.spec_vars = []

        def _next_spec_id():
            """Auto-increment spec ID from existing entries"""
            used = set()
            for rd in self.spec_vars:
                try:
                    used.add(int(rd["id"].get()))
                except ValueError:
                    pass
            i = 1
            while i in used:
                i += 1
            return str(i)

        def _add_spec_row(sid="", name="", pins_str=""):
            f = tk.Frame(self.spec_list_f, bg=COLOR_BG_PANEL)
            f.pack(fill=tk.X, pady=2)

            id_var = tk.StringVar(value=sid)
            name_var = tk.StringVar(value=name)
            pins_var = tk.StringVar(value=pins_str)

            # 仕様IDは読み取り専用
            ent_id = tk.Entry(f, textvariable=id_var, font=FONT_SET_VAL, bg="#334", fg="#aaa",
                              bd=1, relief="solid", width=10, state="readonly")
            ent_id.pack(side=tk.LEFT, padx=1)
            Tooltip(ent_id, "仕様IDは自動番号で付与されます。変更不可。")
            
            ent_name = tk.Entry(f, textvariable=name_var, font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white", bd=1, relief="solid", width=12)
            ent_name.pack(side=tk.LEFT, padx=3)
            Tooltip(ent_name, "仕様の表示名（例: S, キ, エラー）")
            
            ent_pins = tk.Entry(f, textvariable=pins_var, font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white", bd=1, relief="solid", width=22)
            ent_pins.pack(side=tk.LEFT, padx=3)
            ent_pins.bind("<FocusIn>", lambda e, v=pins_var: setattr(self, "active_entry", v))
            Tooltip(ent_pins, "使用BCMピン番号をカンマ区切りで記入 (例: 4,5,6)。右のピンマップをクリックで入力できます。")

            row_data = {"id": id_var, "name": name_var, "pins": pins_var, "frame": f}

            def _delete(rd=row_data):
                rd["frame"].destroy()
                if rd in self.spec_vars:
                    self.spec_vars.remove(rd)
                self._mark_changed()

            btn_test = tk.Button(f, text="テスト", font=(FONT_FAMILY, 9), bg="#546E7A", fg="white",
                                 relief="flat", padx=6, command=lambda v=pins_var: self._test_gpio_pulse(v))
            btn_test.pack(side=tk.LEFT, padx=3)
            Tooltip(btn_test, "現在入力されているピンを0.5秒間テスト発火させます")

            tk.Button(f, text="削除", font=(FONT_FAMILY, 9), bg=COLOR_NG, fg="white",
                      relief="flat", padx=6, command=_delete).pack(side=tk.LEFT, padx=5)

            self.spec_vars.append(row_data)
            name_var.trace_add("write", lambda *a: self._mark_changed())
            pins_var.trace_add("write", lambda *a: self._mark_changed())

        spec_map = self.cfg.data.get("specification_mapping", {})
        # ID（文字列または数値）を数値として昇順ソートして表示
        sorted_sids = sorted(spec_map.keys(), key=lambda x: int(x) if str(x).isdigit() else 999)
        for sid in sorted_sids:
            data = spec_map[sid]
            _add_spec_row(sid, data.get("name", ""), ",".join(str(p) for p in data.get("pins", [])))

        btn_add = tk.Button(parent, text="＋ 追加", font=FONT_NORMAL, bg="#455A64", fg="white", relief="flat", padx=10,
                  command=lambda: [_add_spec_row(_next_spec_id(), "", ""), self._mark_changed()])
        btn_add.grid(row=start_row + 4, column=0, columnspan=3, pady=(5, 10), padx=15, sticky="w")
        Tooltip(btn_add, "新しい仕様定義（ID, 名前, 出力ピン）を追加します")

        # Right: Map
        right_f = tk.Frame(tab_frame, bg=COLOR_BG_MAIN)
        right_f.grid(row=0, column=1, sticky="nsew", padx=5, pady=10)
        self.show_gpio_map(right_f)

    def show_gpio_map(self, parent):
        """Raspberry Pi 40ピンヘッダのマップを表示する（クリックでピン番号入力）"""
        outer, inner = create_card(parent, "Pi 40Pin Map")
        outer.pack(fill=tk.BOTH, expand=True)

        def _on_pin_clicked(bcm_val):
            var = getattr(self, "active_entry", None)
            if var and bcm_val is not None:
                # Append if it's a comma-separated list (like spec pins), replace if spinbox
                curr = str(var.get()).strip()
                if "," in curr or curr == "":
                    if curr == "0" or curr == "":
                        var.set(str(bcm_val))
                    else:
                        var.set(f"{curr},{bcm_val}")
                else:
                    var.set(str(bcm_val))

        pins = [
            (1, "3.3V", None),   (2, "5V", None),
            (3, "GPIO 2", 2),    (4, "5V", None),
            (5, "GPIO 3", 3),    (6, "GND", None),
            (7, "GPIO 4", 4),    (8, "GPIO 14", 14),
            (9, "GND", None),    (10, "GPIO 15", 15),
            (11, "GPIO 17", 17), (12, "GPIO 18", 18),
            (13, "GPIO 27", 27), (14, "GND", None),
            (15, "GPIO 22", 22), (16, "GPIO 23", 23),
            (17, "3.3V", None),  (18, "GPIO 24", 24),
            (19, "GPIO 10", 10), (20, "GND", None),
            (21, "GPIO 9", 9),   (22, "GPIO 25", 25),
            (23, "GPIO 11", 11), (24, "GPIO 8", 8),
            (25, "GND", None),   (26, "GPIO 7", 7),
            (27, "ID_SD", None), (28, "ID_SC", None),
            (29, "GPIO 5", 5),   (30, "GND", None),
            (31, "GPIO 6", 6),   (32, "GPIO 12", 12),
            (33, "GPIO 13", 13), (34, "GND", None),
            (35, "GPIO 19", 19), (36, "GPIO 16", 16),
            (37, "GPIO 26", 26), (38, "GPIO 20", 20),
            (39, "GND", None),   (40, "GPIO 21", 21)
        ]

        mf = tk.Frame(inner, bg=COLOR_BG_PANEL)
        mf.pack(pady=15, padx=20) 

        for i, (pno, name, bcm) in enumerate(pins):
            col_idx = 0 if i % 2 == 0 else 2
            row_idx = i // 2
            
            lbl_no = tk.Label(mf, text=str(pno), font=(FONT_FAMILY, 10, "bold"),
                              width=3, bg="#222", fg="white")
            
            lbl_color = "#444"
            if "V" in name: lbl_color = "#8D6E63"
            if "GND" in name: lbl_color = "#212121"
            
            lbl_name = tk.Label(mf, text=name, font=(FONT_FAMILY, 10),
                                width=12, bg=lbl_color, fg=COLOR_TEXT_MAIN,
                                padx=5, pady=3, relief="flat")

            if i % 2 == 0:
                lbl_no.grid(row=row_idx, column=0, padx=2, pady=1)
                lbl_name.grid(row=row_idx, column=1, padx=(2, 10), pady=1, sticky="w")
            else:
                lbl_name.grid(row=row_idx, column=2, padx=(10, 2), pady=1, sticky="e")
                lbl_no.grid(row=row_idx, column=3, padx=2, pady=1)

            if bcm is not None:
                def make_handler(b=bcm): return lambda e: _on_pin_clicked(b)
                lbl_no.bind("<Button-1>", make_handler())
                lbl_name.bind("<Button-1>", make_handler())
                lbl_no.config(cursor="hand2")
                lbl_name.config(cursor="hand2")
                Tooltip(lbl_name, "クリックで選択中の入力欄にこのピン番号をセットします")

    # ---------------------------------------------------------------
    # タブ: 画像処理 (リアルタイムプレビュー付き)
    # ---------------------------------------------------------------
    def _tab_adjust(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text="画像処理")

        pane = tk.PanedWindow(tab, orient=tk.HORIZONTAL, bg=COLOR_BG_MAIN,
                              sashwidth=6, sashrelief="flat")
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左: スライダー群
        left, left_inner = create_card(pane, "画像処理パラメータ")
        pane.add(left, minsize=400)

        ctrl_canvas = tk.Canvas(left_inner, bg=COLOR_BG_PANEL, highlightthickness=0)
        vsb = ttk.Scrollbar(left_inner, orient="vertical", command=ctrl_canvas.yview)
        self.adj_sf = tk.Frame(ctrl_canvas, bg=COLOR_BG_PANEL)
        self.adj_sf.bind("<Configure>",
                         lambda e: ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all")))
        ctrl_canvas.create_window((0, 0), window=self.adj_sf, anchor="nw")
        ctrl_canvas.configure(yscrollcommand=vsb.set)
        ctrl_canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        ctrl_canvas.bind("<MouseWheel>",
                         lambda e: ctrl_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._build_adjust_sliders()

        # 右: リアルタイムプレビュー
        right, right_inner = create_card(pane, "リアルタイムプレビュー")
        pane.add(right)

        self.adj_preview_canvas = tk.Canvas(right_inner, bg="black", cursor="crosshair")
        self.adj_preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        self._adj_drag_start = None
        self._adj_roi_draft = None

        self.adj_preview_canvas.bind("<Button-1>", self._on_adj_mouse_down)
        self.adj_preview_canvas.bind("<B1-Motion>", self._on_adj_mouse_move)
        self.adj_preview_canvas.bind("<ButtonRelease-1>", self._on_adj_mouse_up)

        cam_btn_f = tk.Frame(right_inner, bg=COLOR_BG_PANEL)
        cam_btn_f.pack(fill=tk.X, pady=5)
        tk.Button(cam_btn_f, text="プレビュー開始", font=FONT_BOLD,
                  bg=COLOR_OK, fg="black", relief="flat",
                  command=self._start_adj_preview).pack(side=tk.LEFT, padx=5)
        tk.Button(cam_btn_f, text="プレビュー停止", font=FONT_BOLD,
                  bg=COLOR_NG, fg="white", relief="flat",
                  command=self._stop_adj_preview).pack(side=tk.LEFT, padx=5)
        
        self.adj_white_lbl = tk.Label(cam_btn_f, text="白面積: --",
                                      font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        self.adj_white_lbl.pack(side=tk.RIGHT, padx=10)

    def _build_adjust_sliders(self):
        sf = self.adj_sf

        def section(title):
            tk.Label(sf, text=f"  {title}", font=FONT_SET_LBL,
                     bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, anchor="w").pack(
                fill=tk.X, pady=(12, 2))
            tk.Frame(sf, bg=COLOR_BORDER, height=1).pack(fill=tk.X, padx=5, pady=2)

        def slider(lbl, var, frm, to, res=1, tooltip=""):
            f = tk.Frame(sf, bg=COLOR_BG_PANEL)
            f.pack(fill=tk.X, padx=10, pady=2)
            tk.Label(f, text=lbl, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=16, anchor="w").pack(side=tk.LEFT)
            s = tk.Scale(f, variable=var, from_=frm, to=to,
                         orient=tk.HORIZONTAL, resolution=res,
                         bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                         troughcolor=COLOR_BG_INPUT, highlightthickness=0,
                         activebackground=COLOR_ACCENT,
                         command=lambda _: self._mark_changed())
            s.pack(fill=tk.X, side=tk.LEFT, expand=True)
            if tooltip:
                Tooltip(s, tooltip)
            return s

        ip = self.cfg.data.get("image_processing", {})

        section("二値化設定")
        tk.Label(sf, text="二値化モード:", font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(anchor="w", padx=10)
        self.thr_mode_var = tk.StringVar(value=ip.get("threshold_mode", "simple"))
        mode_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        mode_f.pack(fill=tk.X, padx=10, pady=2)
        for txt, val in [("Simple固定閾値", "simple"), ("適応二値化", "adaptive")]:
            tk.Radiobutton(mode_f, text=txt, variable=self.thr_mode_var, value=val,
                           font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                           selectcolor=COLOR_BG_INPUT,
                           command=self._mark_changed).pack(side=tk.LEFT, padx=5)

        self.v_threshold = tk.IntVar(value=ip.get("threshold", 30))
        self.v_ada_block = tk.IntVar(value=ip.get("ada_block", 11))
        self.v_ada_c = tk.IntVar(value=ip.get("ada_c", 2))
        self.v_white_ratio = tk.IntVar(value=ip.get("white_ratio", 3))
        slider("Simple閾値", self.v_threshold, 0, 255,
               tooltip="Simple固定二値化のしきい値。背景と対象を分ける輝度境界。")
        slider("Ada Block", self.v_ada_block, 3, 99, 2,
               tooltip="適応二値化の計算範囲サイズ。局所的な明暗に対応します。")
        slider("Ada C", self.v_ada_c, -30, 30,
               tooltip="適応二値化で算出されたしきい値から差し引く定数。")
        slider("白面積率(%)", self.v_white_ratio, 1, 100,
               tooltip="動的しきい値(Histogram)で目標とするワークの白面積比率。")

        section("輪郭フィルタ")
        learn_btn_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        learn_btn_f.pack(fill=tk.X, padx=10, pady=2)
        btn_learn = tk.Button(learn_btn_f, text="現在のワークから自動学習 (面積・周長)", font=FONT_NORMAL,
                   bg=COLOR_OK, fg="black", relief="flat",
                   command=self._auto_learn_contours)
        btn_learn.pack(side=tk.LEFT)
        Tooltip(btn_learn, "プレビューに映っている最大の輪郭を計測し、フィルタ範囲を自動設定します。")

        self.v_min_len = tk.IntVar(value=ip.get("filter_min_len", 200))
        self.v_max_len = tk.IntVar(value=ip.get("filter_max_len", 1500))
        self.v_min_area = tk.IntVar(value=ip.get("filter_min_area", 10000))
        self.v_max_area = tk.IntVar(value=ip.get("filter_max_area", 35000))
        slider("最小周長", self.v_min_len, 0, 5000, 10, tooltip="これより短い輪郭はノイズとして無視します。")
        slider("最大周長", self.v_max_len, 0, 10000, 10, tooltip="これより長い輪郭は無視します。")
        slider("最小面積", self.v_min_area, 0, 100000, 100, tooltip="これより小さい面積の輪郭は無視します。")
        slider("最大面積", self.v_max_area, 0, 500000, 100, tooltip="これより大きい面積の輪郭は無視します。")

        section("射影変換")
        self.v_affine_h = tk.IntVar(value=ip.get("affine_h_mm", 50))
        self.v_affine_w = tk.IntVar(value=ip.get("affine_w_mm", 40))
        slider("変換高さ(mm)", self.v_affine_h, 1, 200, tooltip="切り出し後の垂直方向の実寸(mm)目安。")
        slider("変換幅(mm)", self.v_affine_w, 1, 200, tooltip="切り出し後の水平方向の実寸(mm)目安。")

        section("マッチング閾値")
        self.v_decision_thr = tk.DoubleVar(value=ip.get("decision_threshold", 0.8))
        slider("判定閾値", self.v_decision_thr, 0.0, 1.0, 0.01,
               tooltip="テンプレートマッチングの類似度スコアがこの値を上回れば『一致(OK)』と判定します。")

    def _start_adj_preview(self):
        self._stop_adj_preview()
        idx = self.cfg.get("camera", "index", default=0)
        if platform.system() == "Windows":
            self._adj_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        else:
            self._adj_cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        self._adj_preview_running = True

        def _worker():
            while self._adj_preview_running and self._adj_cap:
                ret, frame = self._adj_cap.read()
                if ret:
                    with self._frame_lock:
                        self._adj_current_frame = frame.copy()
                time.sleep(0.03)

        threading.Thread(target=_worker, daemon=True).start()

    def _stop_adj_preview(self):
        self._adj_preview_running = False
        if self._adj_cap:
            self._adj_cap.release()
            self._adj_cap = None
        self._adj_current_frame = None

    def _adj_loop(self):
        """調整タブのプレビューループ（30ms更新）"""
        if not self.winfo_exists():
            return
            
        if not self._adj_preview_running:
            self.after(200, self._adj_loop)
            return

        try:
            with self._frame_lock:
                frame = self._adj_current_frame
            if frame is not None:
                processed = self._apply_preview_processing(frame)
                cw = self.adj_preview_canvas.winfo_width()
                ch = self.adj_preview_canvas.winfo_height()
                if cw > 1 and ch > 1:
                    pw = min(cw, int(ch * processed.shape[1] / processed.shape[0]))
                    ph = int(pw * processed.shape[0] / processed.shape[1])
                    resized = cv2.resize(processed, (pw, ph))
                    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                    img = ImageTk.PhotoImage(Image.fromarray(rgb))
                    self.adj_preview_canvas.create_image(
                        cw // 2, ch // 2, anchor=tk.CENTER, image=img)
                    self.adj_preview_canvas.image = img

                    # 白面積率
                    gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
                    ratio = np.count_nonzero(gray > 128) / gray.size * 100
                    self.adj_white_lbl.config(text=f"白面積: {ratio:.1f}%")
                    
                    # ドラッグ中の枠描画
                    if hasattr(self, "_adj_roi_draft") and self._adj_roi_draft:
                        rx1, ry1, rx2, ry2 = self._adj_roi_draft
                        # ratio -> relative canvas coordinate
                        cx1 = cw // 2 - pw // 2 + min(rx1, rx2) * pw
                        cy1 = ch // 2 - ph // 2 + min(ry1, ry2) * ph
                        cx2 = cw // 2 - pw // 2 + max(rx1, rx2) * pw
                        cy2 = ch // 2 - ph // 2 + max(ry1, ry2) * ph
                        self.adj_preview_canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="#ffeb3b", width=2)
                        
        except Exception:
            pass
        self.after(30, self._adj_loop)

    def _on_adj_mouse_down(self, event):
        self._adj_drag_start = (event.x, event.y)
        self._adj_roi_draft = None

    def _on_adj_mouse_move(self, event):
        if not self._adj_drag_start:
            return
        x0, y0 = self._adj_drag_start
        self._update_adj_roi_from_canvas(x0, y0, event.x, event.y)

    def _on_adj_mouse_up(self, event):
        if not self._adj_drag_start:
            return
        x0, y0 = self._adj_drag_start
        self._adj_drag_start = None
        self._update_adj_roi_from_canvas(x0, y0, event.x, event.y, save=True)

    def _update_adj_roi_from_canvas(self, cx1, cy1, cx2, cy2, save=False):
        cw = self.adj_preview_canvas.winfo_width()
        ch = self.adj_preview_canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
            
        with self._frame_lock:
            frame = self._adj_current_frame
        if frame is None:
            return
            
        pw = min(cw, int(ch * frame.shape[1] / frame.shape[0]))
        ph = int(pw * frame.shape[0] / frame.shape[1])
        
        offset_x = (cw - pw) / 2
        offset_y = (ch - ph) / 2
        
        # キャンバス座標から画像内の 0.0 ~ 1.0 相対比率へ
        rx1 = max(0.0, min(1.0, (cx1 - offset_x) / pw))
        ry1 = max(0.0, min(1.0, (cy1 - offset_y) / ph))
        rx2 = max(0.0, min(1.0, (cx2 - offset_x) / pw))
        ry2 = max(0.0, min(1.0, (cy2 - offset_y) / ph))
        
        # 変位がわずかな場合はドラッグとみなさない（0.01 = 約1%）
        if abs(rx2 - rx1) > 0.01 and abs(ry2 - ry1) > 0.01:
            self._adj_roi_draft = [min(rx1,rx2), min(ry1,ry2), max(rx1,rx2), max(ry1,ry2)]
            if save:
                ip = self.cfg.data.setdefault("image_processing", {})
                ip["roi"] = list(self._adj_roi_draft)
                self._adj_roi_draft = None
                self._mark_changed()

    def _apply_preview_processing(self, frame):
        """調整タブのスライダー設定を適用したフレームを返す"""
        h, w = frame.shape[:2]
        preview = frame.copy()

        # ROI適用 (範囲外を黒く塗りつぶす)
        roi = self.cfg.data.get("image_processing", {}).get("roi", [0.0, 0.0, 1.0, 1.0])
        rx1, ry1, rx2, ry2 = roi
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        
        cx1 = int(min(rx1, rx2) * w)
        cy1 = int(min(ry1, ry2) * h)
        cx2 = int(max(rx1, rx2) * w)
        cy2 = int(max(ry1, ry2) * h)
        
        cv2.rectangle(mask, (max(0, cx1), max(0, cy1)), (min(w, cx2), min(h, cy2)), 255, -1)
        preview = cv2.bitwise_and(preview, preview, mask=mask)

        gray = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
        mode = self.thr_mode_var.get()
        if mode == "simple":
            thr = self.v_threshold.get()
            _, binarized = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        else:
            bs = self.v_ada_block.get()
            bs = bs + 1 if bs % 2 == 0 else bs
            binarized = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, bs, self.v_ada_c.get())

        return cv2.cvtColor(binarized, cv2.COLOR_GRAY2BGR)

    def _auto_learn_contours(self):
        """現在のプレビューから輪郭の面積・周長を計測し、フィルタ値を自動設定する"""
        if getattr(self, '_adj_current_frame', None) is None:
            messagebox.showwarning("警告", "プレビューを開始してください。")
            return

        with self._frame_lock:
            frame = self._adj_current_frame.copy()
        
        h, w = frame.shape[:2]
        adj_sw = w / 640.0
        adj_sh = h / 480.0

        roi = self.cfg.data.get("image_processing", {}).get("roi", [0, 0, 640, 480])
        x1, y1, x2, y2 = roi
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        cx1 = int(min(x1, x2) * adj_sw)
        cy1 = int(min(y1, y2) * adj_sh)
        cx2 = int(max(x1, x2) * adj_sw)
        cy2 = int(max(y1, y2) * adj_sh)
        cv2.rectangle(mask, (max(0, cx1), max(0, cy1)), (min(w, cx2), min(h, cy2)), 255, -1)
        frame = cv2.bitwise_and(frame, frame, mask=mask)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        mode = self.thr_mode_var.get()
        if mode == "simple":
            thr = self.v_threshold.get()
            _, binarized = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        else:
            bs = self.v_ada_block.get()
            bs = bs + 1 if bs % 2 == 0 else bs
            binarized = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, bs, self.v_ada_c.get())
                
        contours, _ = cv2.findContours(binarized, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            messagebox.showerror("エラー", "輪郭が1つも検出されませんでした。環境や二値化設定を見直してください。")
            return
            
        # 最大の輪郭を取得
        card_cnt = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(card_cnt)
        length = cv2.arcLength(card_cnt, True)
        
        # 画面解像度のスケーリングをキャンセル (基準640x480に合わせる)
        adj_area = adj_sw * adj_sh
        adj_len = np.sqrt((adj_sw ** 2 + adj_sh ** 2) / 2.0)
        
        base_area = area / adj_area if adj_area > 0 else area
        base_len = length / adj_len if adj_len > 0 else length
        
        if base_area < 100:
            messagebox.showwarning("警告", "検出された輪郭が小さすぎます。対象物が正しく映っているか確認してください。")
            return
            
        # ±30%マージンで値を更新
        self.v_min_area.set(int(base_area * 0.7))
        self.v_max_area.set(int(base_area * 1.3))
        self.v_min_len.set(int(base_len * 0.7))
        self.v_max_len.set(int(base_len * 1.3))
        
        self._mark_changed()
        messagebox.showinfo("学習完了", 
                            f"最大の対象物からスライダーを自動設定しました(±30%マージン)。\n\n"
                            f"計測面積: {int(base_area)}\n計測周長: {int(base_len)}")

    # ---------------------------------------------------------------
    # タブ: 画素数・保存
    # ---------------------------------------------------------------
    def _tab_resolution(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text="画素数・保存")

        outer, inner_wrap = create_card(tab, "解像度・保存設定")
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Scrollable panel for resolution/storage settings
        scroll_c = tk.Canvas(inner_wrap, bg=COLOR_BG_PANEL, highlightthickness=0)
        vsb = ttk.Scrollbar(inner_wrap, orient="vertical", command=scroll_c.yview)
        inner = tk.Frame(scroll_c, bg=COLOR_BG_PANEL)
        inner.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_c.create_window((0, 0), window=inner, anchor="nw")
        scroll_c.configure(yscrollcommand=vsb.set)
        scroll_c.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        scroll_c.bind("<MouseWheel>", lambda e: scroll_c.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        res_full = ["320x240 (QVGA)", "640x480 (VGA)", "1280x720 (HD)",
                    "1920x1080 (Full HD)", "3840x2160 (4K)"]
        res_save = res_full + ["保存しない"]

        def row(parent, label, var, options, r, tooltip=""):
            tk.Label(parent, text=label, font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=22, anchor="w").grid(
                row=r, column=0, padx=15, pady=8, sticky="w")
            cb = ttk.Combobox(parent, textvariable=var, values=options,
                              state="readonly", font=FONT_SET_VAL, width=24)
            cb.grid(row=r, column=1, padx=10, pady=8)
            var.trace_add("write", lambda *a: self._mark_changed())
            if tooltip:
                Tooltip(cb, tooltip)

        grid_f = tk.Frame(inner, bg=COLOR_BG_PANEL)
        grid_f.pack(fill=tk.X)

        tk.Label(grid_f, text="【基本撮影設定】", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).grid(
            row=0, column=0, padx=15, pady=(15, 5), sticky="w")
        self.res_capture_var = tk.StringVar()
        row(grid_f, "撮影解像度:", self.res_capture_var, res_full, 1,
            "カメラから取得する元画像サイズ。全処理の最大値となります。")

        tk.Label(grid_f, text="【プレビュー設定】", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).grid(
            row=2, column=0, padx=15, pady=(15, 5), sticky="w")
        self.res_preview_var = tk.StringVar()
        preview_opts = ["プレビューなし", "320x240 (QVGA)", "640x480 (VGA)", "1280x720 (HD)"]
        row(grid_f, "プレビュー解像度:", self.res_preview_var, preview_opts, 3,
            "メイン画面のプレビューサイズ。小さくするほどCPU負荷が下がります。")

        tk.Label(grid_f, text="【保存設定】", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).grid(
            row=4, column=0, padx=15, pady=(15, 5), sticky="w")
        self.res_ng_var = tk.StringVar()
        self.res_ok_var = tk.StringVar()
        row(grid_f, "NG保存解像度:", self.res_ng_var, res_save, 5,
            "NG判定時の保存サイズ。通常は最大解像度を推奨します。")
        row(grid_f, "OK保存解像度:", self.res_ok_var, res_save, 6,
            "OK判定時の保存サイズ。容量節約のため小さめに設定できます。")

        tk.Label(grid_f, text="【自動容量管理】", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).grid(
            row=7, column=0, padx=15, pady=(15, 5), sticky="w")
        self.auto_delete_var = tk.BooleanVar()
        cb_auto = tk.Checkbutton(grid_f, text="上限超過時に古い画像を自動削除",
                       variable=self.auto_delete_var, font=FONT_NORMAL,
                       bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT,
                       command=self._mark_changed)
        cb_auto.grid(row=8, column=0, padx=15, pady=5, sticky="w")
        Tooltip(cb_auto, "ディスク容量が一杯になった際、古い日付の画像フォルダから順に自動削除します。")

        tk.Label(grid_f, text="保存上限(GB):", font=FONT_SET_LBL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").grid(
            row=9, column=0, padx=15, pady=5, sticky="w")
        self.max_gb_var = tk.IntVar()
        sp_gb = tk.Spinbox(grid_f, textvariable=self.max_gb_var, from_=1, to=1000,
                    font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white",
                    buttonbackground="#78909C", bd=1, relief="solid", width=8,
                    command=self._mark_changed)
        sp_gb.grid(row=9, column=1, padx=10, pady=5)
        Tooltip(sp_gb, "自動削除を発動するストレージ使用量の上限（ギガバイト単位）。")

    # ---------------------------------------------------------------
    # タブ: システム
    # ---------------------------------------------------------------
    def _tab_system(self):
        tab = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(tab, text="システム")

        outer, inner_wrap = create_card(tab, "システム設定")
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Scrollable panel
        scroll_c = tk.Canvas(inner_wrap, bg=COLOR_BG_PANEL, highlightthickness=0)
        vsb = ttk.Scrollbar(inner_wrap, orient="vertical", command=scroll_c.yview)
        inner = tk.Frame(scroll_c, bg=COLOR_BG_PANEL)
        inner.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_c.create_window((0, 0), window=inner, anchor="nw")
        scroll_c.configure(yscrollcommand=vsb.set)
        scroll_c.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        scroll_c.bind("<MouseWheel>", lambda e: scroll_c.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # 処理フラグ
        flag_f, flag_inner = create_card(inner, "処理フラグ設定")
        flag_f.pack(fill=tk.X, pady=5)
        
        # Grid用の中間フレームを追加して TclError を回避
        grid_f = tk.Frame(flag_inner, bg=COLOR_BG_PANEL)
        grid_f.pack(fill=tk.X, pady=5)

        flag_descriptions = {
            "CLAHE_FLAG": ("輝度正規化 (CLAHE)", "画像全体の明暗を平均化し、ライティングのムラを抑えます。"),
            "THRESHOLD_FLAG": ("動的二値化", " Histogram解析に基づき、目標白面積率になるよう閾値を自動調整します。"),
            "ADAPTIVE_FLAG": ("適応二値化モード", "周辺画素の平均をもとに輝度境界を決定します。"),
            "CONTOURS_FLAG": ("輪郭抽出・射影変換", "ワークの輪郭を見つけ、正面を向くように幾何変換します。"),
            "MASK_SECOND_FLAG": ("2回目トライ", "1回目で失敗した場合にマスク条件を緩めて再試行します。"),
            "SIO_FLAG": ("SiO信号待ち", "GPIOからのトリガー信号が入るまで、メインループを停止させます。"),
            "LENGTH_FILTER_FLAG": ("周長フィルタ", "輪郭の長さ（ピクセル）によるノイズ除去を行います。"),
            "AREA_FILTER_FLAG": ("面積フィルタ", "輪郭の内部面積によるノイズ除去を行います。"),
            "SAVE_DEBUG_FLAG": ("デバッグ画像保存", "輪郭や射影変換の過程を保存します。（※ストレージ容量に注意）"),
        }
        self.flag_vars = {}
        for r, (fname, (title, tip)) in enumerate(flag_descriptions.items()):
            var = tk.BooleanVar()
            self.flag_vars[fname] = var
            cb = tk.Checkbutton(grid_f, text=title, variable=var,
                                font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                                selectcolor=COLOR_BG_INPUT,
                                command=self._mark_changed)
            cb.grid(row=r // 2, column=r % 2, padx=15, pady=5, sticky="w")
            Tooltip(cb, tip)

        # 結果保存先設定
        path_f, path_inner = create_card(inner, "保存先設定")
        path_f.pack(fill=tk.X, pady=5)
        
        path_row = tk.Frame(path_inner, bg=COLOR_BG_PANEL)
        path_row.pack(fill=tk.X, pady=5)
        tk.Label(path_row, text="結果出力先ディレクトリ:", font=FONT_SET_LBL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        self.res_dir_var = tk.StringVar()
        tk.Entry(path_row, textvariable=self.res_dir_var, font=FONT_SET_VAL,
                 bg=COLOR_BG_INPUT, fg="white", bd=1, relief="solid").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        tk.Button(path_row, text="選択...", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat",
                  command=self._select_res_dir).pack(side=tk.LEFT)

        # 車種設定
        model_f, model_inner = create_card(inner, "車種設定 (改行区切り)")
        model_f.pack(fill=tk.X, pady=5)
        self.model_text = tk.Text(model_inner, height=5, font=FONT_NORMAL,
                                  bg=COLOR_BG_INPUT, fg="white", bd=1, relief="solid")
        self.model_text.pack(fill=tk.X)
        self.model_text.bind("<<Modified>>", lambda e: self._mark_changed())

    def _select_res_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory()
        if path:
            self.res_dir_var.set(path)
            self._mark_changed()

    # ---------------------------------------------------------------
    # 値の読み込み・保存
    # ---------------------------------------------------------------
    def _load_values(self):
        """config.json の内容をウィジェットへ反映"""
        cam = self.cfg.data.get("camera", {})
        self.cam_idx_var.set(cam.get("index", 0))

        def res_with_label(r):
            m = {"320x240": "QVGA", "640x480": "VGA", "1280x720": "HD",
                 "1920x1080": "Full HD", "3840x2160": "4K"}
            for k, v in m.items():
                if r.startswith(k):
                    return f"{k} ({v})"
            return r

        for k in self.cam_props:
            if k in cam:
                self.cam_props[k].set(str(cam[k]))

        gpio = self.cfg.data.get("gpio_pins", {})
        for pname, var in self.gpio_vars.items():
            v = gpio.get(pname, 0)
            var.set("" if v <= 0 else str(v))

        ip = self.cfg.data.get("image_processing", {})
        self.v_threshold.set(ip.get("threshold", 30))
        self.thr_mode_var.set(ip.get("threshold_mode", "simple"))
        self.v_ada_block.set(ip.get("ada_block", 11))
        self.v_ada_c.set(ip.get("ada_c", 2))
        self.v_white_ratio.set(ip.get("white_ratio", 3))
        self.v_min_len.set(ip.get("filter_min_len", 200))
        self.v_max_len.set(ip.get("filter_max_len", 1500))
        self.v_min_area.set(ip.get("filter_min_area", 10000))
        self.v_max_area.set(ip.get("filter_max_area", 35000))
        self.v_affine_h.set(ip.get("affine_h_mm", 50))
        self.v_affine_w.set(ip.get("affine_w_mm", 40))
        self.v_decision_thr.set(ip.get("decision_threshold", 0.8))

        stor = self.cfg.data.get("storage", {})
        self.res_capture_var.set(res_with_label(cam.get("resolution", "1920x1080")))
        self.res_preview_var.set(res_with_label(cam.get("preview_res", "640x480")))
        self.res_ng_var.set(res_with_label(stor.get("res_ng", "1920x1080")))
        self.res_ok_var.set(res_with_label(stor.get("res_ok", "640x480")))
        self.auto_delete_var.set(stor.get("auto_delete_enabled", False))
        self.max_gb_var.set(stor.get("max_results_gb", 10))
        self.res_dir_var.set(stor.get("results_dir", "./results"))

        flags = self.cfg.data.get("flags", {})
        for fname, var in self.flag_vars.items():
            var.set(flags.get(fname, True))

        models = self.cfg.data.get("car_models", ["default"])
        self.model_text.delete("1.0", tk.END)
        self.model_text.insert("1.0", "\n".join(models))

        self._changed = False
        self.btn_save.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, text="保存して閉じる")

    def _save_values(self):
        """ウィジェットの値を config に書き戻す"""

        def raw_res(s):
            """通称付きの解像度文字列から純粋な "WxH" を返す"""
            if s == "プレビューなし" or s == "保存しない":
                return s
            return s.split(" ")[0] if s else "640x480"

        self.cfg.data.setdefault("camera", {})
        self.cfg.set("camera", "index", int(self.cam_idx_var.get()))
        self.cfg.set("camera", "resolution", raw_res(self.res_capture_var.get()))
        
        for k, var in self.cam_props.items():
            try:
                self.cfg.set("camera", k, float(var.get()) if "." in var.get() else int(var.get()))
            except ValueError:
                pass

        for pname, var in self.gpio_vars.items():
            v = var.get().strip()
            self.cfg.data.setdefault("gpio_pins", {})[pname] = int(v) if v.isdigit() else -1

        self.cfg.data["specification_mapping"] = {}
        for row in self.spec_vars:
            sid = row["id"].get().strip()
            if sid:
                pins_str = row["pins"].get().strip()
                pins = []
                if pins_str:
                    for p in pins_str.split(","):
                        p = p.strip()
                        if p.isdigit():
                            pins.append(int(p))
                self.cfg.data["specification_mapping"][sid] = {
                    "name": row["name"].get().strip(),
                    "pins": pins
                }

        ip = self.cfg.data.setdefault("image_processing", {})
        ip.update({
            "threshold": self.v_threshold.get(),
            "threshold_mode": self.thr_mode_var.get(),
            "ada_block": self.v_ada_block.get(),
            "ada_c": self.v_ada_c.get(),
            "white_ratio": self.v_white_ratio.get(),
            "filter_min_len": self.v_min_len.get(),
            "filter_max_len": self.v_max_len.get(),
            "filter_min_area": self.v_min_area.get(),
            "filter_max_area": self.v_max_area.get(),
            "affine_h_mm": self.v_affine_h.get(),
            "affine_w_mm": self.v_affine_w.get(),
            "decision_threshold": self.v_decision_thr.get(),
        })

        stor = self.cfg.data.setdefault("storage", {})
        stor.update({
            "res_ng": raw_res(self.res_ng_var.get()),
            "res_ok": raw_res(self.res_ok_var.get()),
            "auto_delete_enabled": self.auto_delete_var.get(),
            "max_results_gb": self.max_gb_var.get(),
            "results_dir": self.res_dir_var.get(),
        })
        self.cfg.set("camera", "preview_res", raw_res(self.res_preview_var.get()))

        flags = self.cfg.data.setdefault("flags", {})
        for fname, var in self.flag_vars.items():
            flags[fname] = var.get()

        models = [m.strip() for m in self.model_text.get("1.0", tk.END).split("\n") if m.strip()]
        self.cfg.data["car_models"] = models if models else ["default"]

    def _mark_changed(self):
        self._changed = True
        if hasattr(self, "btn_save"):
            self.btn_save.config(bg=COLOR_OK, fg="black", text="変更を適用して保存")

    def _on_save(self):
        # バリデーション
        core_pins = {} # pname -> pin
        
        # 1. 共通ピン（トリガー/OK/NG）の重複チェック
        for pname, var in self.gpio_vars.items():
            v = var.get().strip()
            if v.isdigit():
                p = int(v)
                if p > 0:
                    if p in core_pins.values():
                        messagebox.showwarning("保存エラー", f"共通ピン番号 BCM {p} が他の共通設定と重複しています。", parent=self)
                        return
                    if p not in VALID_BCM_PINS:
                        messagebox.showwarning("保存エラー", f"ピン番号 BCM {p} は有効なGPIOピンではありません。\n※使用可能なピン: {sorted(list(VALID_BCM_PINS))}", parent=self)
                        return
                    core_pins[pname] = p

        # 2. 仕様マッピングの重複チェック
        # 「すべてのピンセットが完全に一致するID」がある場合のみ警告
        spec_sets = {} # sid -> set of pins
        for row in self.spec_vars:
            sid = row["id"].get().strip()
            p_str = row["pins"].get().strip()
            pins_in_row = []
            if p_str:
                for p_s in p_str.split(","):
                    p_s = p_s.strip()
                    if p_s.isdigit():
                        p = int(p_s)
                        if p > 0:
                            if p not in VALID_BCM_PINS:
                                messagebox.showwarning("保存エラー", f"ID {sid}: ピン番号 BCM {p} は無効なピンです。", parent=self)
                                return
                            pins_in_row.append(p)
            
            p_set = tuple(sorted(pins_in_row))
            if p_set in spec_sets.values():
                dup_sid = [k for k, v in spec_sets.items() if v == p_set][0]
                messagebox.showwarning("保存エラー", f"仕様ID {sid} のピン設定が、ID {dup_sid} と完全に一致しています。\n少なくとも1つは異なるピン設定にしてください。", parent=self)
                return
            spec_sets[sid] = p_set

        self._save_values()
        if not self.cfg.save():
            messagebox.showerror("エラー", "設定の保存に失敗しました。", parent=self)
            return
        self.destroy()

    def _on_cancel(self):
        if self._changed:
            if not messagebox.askyesno("確認", "変更を破棄して閉じますか？", parent=self):
                return
        self.destroy()

    def _show_help(self):
        HelpWindow(self, "設定ヘルプ", {
            "カメラタブ": "カメラのインデックス番号と解像度を設定します。\n「プレビュー開始」で実際の映像を確認できます。",
            "GPIOピンタブ": "全ての入出力ピンのBCM番号を設定します。\n仕様マッピングは仕様IDとGPIOピンの対応表です。\n「テスト」ボタンで配線チェックが可能です。",
            "画像処理タブ": "二値化、輪郭フィルタ、射影変換などのパラメータを調整します。\nプレビューで設定結果をリアルタイムに確認できます。",
            "画素数・保存タブ": "画像保存時の解像度やディスク容量管理の設定を行います。",
            "システムタブ": "処理フラグのON/OFFや結果出力先、車種名を変更します。"
        })

    def _test_gpio_pulse(self, var):
        """GPIO出力を0.5秒間テスト発火させる"""
        try:
            val_raw = var.get().strip()
            if not val_raw:
                return
                
            # カンマ区切りの場合は複数を順次/同時発火（今回は先頭のみ or 全て）
            pins = []
            for p_s in val_raw.split(","):
                p_s = p_s.strip()
                if p_s.isdigit():
                    p = int(p_s)
                    if 0 < p <= 40:
                        pins.append(p)
            
            if not pins:
                return
            
            self.logger.info(f"GPIOテスト出力開始: BCM {pins}")
            devs = []
            for p in pins:
                devs.append(OutputDevice(p))
            
            for d in devs:
                d.on()
            
            # 500ms後にOFF
            def _off():
                for d in devs:
                    try:
                        d.off()
                        d.close()
                    except:
                        pass
                self.logger.info(f"GPIOテスト出力終了: BCM {pins}")

            self.after(500, _off)
            
        except Exception as e:
            self.logger.error(f"GPIOテスト失敗: {e}")
            messagebox.showerror("テストエラー", f"GPIOの操作に失敗しました: {e}", parent=self)

    # ---------------------------------------------------------------
    # ユーティリティ
    # ---------------------------------------------------------------
    def _update_canvas(self, canvas, img):
        """Canvasに画像を更新"""
        try:
            canvas.create_image(0, 0, anchor=tk.NW, image=img)
            canvas.image = img
        except Exception:
            pass

    def destroy(self):
        self._stop_cam_preview()
        self._stop_adj_preview()
        # いかなる手段で画面が閉じられても、親画面へカメラ・GPIOの再起動を通知する
        if hasattr(self, 'on_close_callback') and self.on_close_callback:
            self.on_close_callback()
            self.on_close_callback = None
        super().destroy()
