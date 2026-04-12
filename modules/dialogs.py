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
from .hardware import OutputDevice


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
        self._active_test_devs = {}  # GPIOテスト中のデバイス保持 {button_widget: [OutputDevice, ...]}
        self._active_input_devs = {} # GPIO入力モニタリング用デバイス保持 {pin_number: DigitalInputDevice}
        self._input_status_labels = {} # UIのラベル保持 {var_name: tk.Label}

        self.title("詳細設定")
        self.geometry("1400x900")
        self.configure(bg=COLOR_BG_MAIN)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._build_ui()
        self._load_values()
        self._adj_loop()
        
        # 入力ピンのモニタリング開始
        self.after(200, self._poll_inputs)

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
        Tooltip(self.btn_save, "全ての変更を確定して保存し、メイン画面に反映します")

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
        pane.add(left, minsize=550)
        
        # Scrollable panel for camera settings
        scroll_c = tk.Canvas(left_inner_wrap, bg=COLOR_BG_PANEL, highlightthickness=0, width=530)
        vsb = ttk.Scrollbar(left_inner_wrap, orient="vertical", command=scroll_c.yview)
        left_inner = tk.Frame(scroll_c, bg=COLOR_BG_PANEL)
        
        # キャンバス内のウィンドウIDを保持
        win_id = scroll_c.create_window((0, 0), window=left_inner, anchor="nw")
        
        def _on_frame_cfg(event):
            scroll_c.configure(scrollregion=scroll_c.bbox("all"))
        def _on_canvas_cfg(event):
            scroll_c.itemconfig(win_id, width=event.width)

        left_inner.bind("<Configure>", _on_frame_cfg)
        scroll_c.bind("<Configure>", _on_canvas_cfg)
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
        tk.Button(row_f, text="自動探索", font=FONT_NORMAL, bg="#455A64", fg="white", relief="flat", padx=8,
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
                tk.Button(props_f, text="自動探索", font=FONT_NORMAL, bg="#455A64", fg="white", relief="flat", padx=8,
                          command=action).grid(row=r, column=2, padx=(0, 10), pady=2)
            
            if k == "focus":
                af_var = tk.IntVar()
                self.cam_props["autofocus"] = af_var
                af_var.trace_add("write", lambda *a, v=af_var: _apply_cam_prop("autofocus", v))
                chk_af = tk.Checkbutton(props_f, text="オートフォーカス", variable=af_var, font=FONT_SET_VAL,
                                        bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                                        selectcolor=COLOR_BG_INPUT, activebackground=COLOR_BG_PANEL, activeforeground=COLOR_TEXT_MAIN)
                chk_af.grid(row=r, column=3, padx=10, pady=2, sticky="w")
                Tooltip(chk_af, "カメラ本体のオートフォーカス機能を有効にします。ON時はマニュアル値は無視されます。")
            
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
                    try:
                        # 1. DSHOW
                        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                        if not cap.isOpened():
                            # 2. MSMF
                            cap = cv2.VideoCapture(i, cv2.CAP_MSMF)
                        if not cap.isOpened():
                            # 3. CAP_ANY
                            cap = cv2.VideoCapture(i)
                    except Exception:
                        try:
                            cap = cv2.VideoCapture(i, cv2.CAP_MSMF)
                            if not cap.isOpened(): cap = cv2.VideoCapture(i)
                        except Exception:
                            cap = cv2.VideoCapture(i)
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
            try:
                self._preview_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if not self._preview_cap.isOpened(): self._preview_cap = cv2.VideoCapture(idx)
            except Exception:
                self._preview_cap = cv2.VideoCapture(idx)
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

        outer, inner = create_card(left_f, "GPIOピン設定")
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
                                     relief="flat", padx=8)
                btn_test.config(command=lambda v=var, b=btn_test: self._toggle_gpio_test(v, b))
                btn_test.grid(row=row, column=2, padx=5, pady=4)
                Tooltip(btn_test, "クリックするとこのピンを出力(ON)にし続けます。もう一度押すと停止します。")
            elif "start" in pname.lower() or "in" in pname.lower():
                # 入力ピンにはステータスモニターを表示
                lbl_status = tk.Label(sf, text="OFF", font=FONT_SET_VAL,
                                      bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB, width=8)
                lbl_status.grid(row=row, column=2, padx=5, pady=4)
                self._input_status_labels[pname] = lbl_status
                Tooltip(lbl_status, "現在の入力状態をリアルタイムで表示します")

        # 仕様マッピングセクション
        self._build_spec_mapping(sf, len(pin_descriptions), tab)

    def _build_spec_mapping(self, parent, start_row, tab_frame):
        sep = tk.Frame(parent, bg=COLOR_BORDER, height=2)
        sep.grid(row=start_row, column=0, columnspan=3,
                 sticky="ew", padx=5, pady=15)

        tk.Label(parent, text="仕様マッピング",
                 font=FONT_SET_LBL, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT).grid(
            row=start_row + 1, column=0, columnspan=3, padx=15, pady=(0, 8), sticky="w")

        hdr = tk.Frame(parent, bg=COLOR_BG_PANEL)
        hdr.grid(row=start_row + 2, column=0, columnspan=3, padx=15, sticky="w")
        for txt, w in [("仕様ID", 10), ("名前", 12), ("使用ピン", 25)]:
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
                                 relief="flat", padx=6)
            btn_test.config(command=lambda v=pins_var, b=btn_test: self._toggle_gpio_test(v, b))
            btn_test.pack(side=tk.LEFT, padx=3)
            Tooltip(btn_test, "現在入力されているピンを出力(ON)にし続けます。もう一度押すと停止します。")

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
        outer, inner = create_card(parent, "Pi 40ピン配置図")
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
        pane.add(left, minsize=550)

        ctrl_canvas = tk.Canvas(left_inner, bg=COLOR_BG_PANEL, highlightthickness=0, width=530)
        vsb = ttk.Scrollbar(left_inner, orient="vertical", command=ctrl_canvas.yview)
        self.adj_sf = tk.Frame(ctrl_canvas, bg=COLOR_BG_PANEL)
        
        win_id = ctrl_canvas.create_window((0, 0), window=self.adj_sf, anchor="nw")
        
        def _on_frame_cfg(event):
            ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all"))
        def _on_canvas_cfg(event):
            ctrl_canvas.itemconfig(win_id, width=event.width)

        self.adj_sf.bind("<Configure>", _on_frame_cfg)
        ctrl_canvas.bind("<Configure>", _on_canvas_cfg)
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

        # モード連動用のスライダー保持用
        self.bin_widgets = {}

    def _update_thr_ui(self, *args):
        """二値化モードに応じてスライダーの有効・無効を切り替える"""
        mode = self.thr_mode_var.get()
        # 有効にする項目の定義
        targets = {
            "simple": ["threshold"],
            "adaptive": ["ada_block", "ada_c"],
            "dynamic": ["white_ratio"]
        }
        active = targets.get(mode, [])
        for name, w_list in self.bin_widgets.items():
            state = tk.NORMAL if name in active else tk.DISABLED
            for w in w_list:
                try:
                    w.config(state=state)
                except tk.TclError:
                    pass

    def _build_adjust_sliders(self):
        sf = self.adj_sf
        ip = self.cfg.data.get("image_processing", {})

        def slider(parent, lbl, var, frm, to, res=1, tip="", tooltip=""):
            f = tk.Frame(parent, bg=COLOR_BG_PANEL)
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
            msg = tip or tooltip
            if msg:
                Tooltip(s, msg)
            return s

        # 1. 前処理設定
        c1, c1_in = create_card(sf, "1. 前処理設定")
        c1.pack(fill=tk.X, pady=5, padx=5)

        self.v_clahe = tk.DoubleVar(value=ip.get("clahe_clip", 0.0))
        self.v_bright = tk.DoubleVar(value=ip.get("brightness", 1.0))
        self.v_contrast = tk.DoubleVar(value=ip.get("contrast", 1.0))
        self.v_saturation = tk.DoubleVar(value=ip.get("saturation", 1.0))
        self.v_gamma = tk.DoubleVar(value=ip.get("gamma", 1.0))
        self.v_blur = tk.DoubleVar(value=ip.get("blur", 0.0))
        self.v_sharp = tk.DoubleVar(value=ip.get("sharpen", 0.0))

        slider(c1_in, "輝度正規化", self.v_clahe, 0.0, 5.0, 0.1, tip="コントラストを均一化し、影や反射の影響を抑えます。")
        slider(c1_in, "明るさ", self.v_bright, 0.1, 3.0, 0.05, tip="画像全体の明るさを調整します。")
        slider(c1_in, "コントラスト", self.v_contrast, 0.1, 3.0, 0.05, tip="明暗の差を強調します。")
        slider(c1_in, "彩度", self.v_saturation, 0.1, 3.0, 0.05, tip="色の鮮やかさを変えます。")
        slider(c1_in, "ガンマ", self.v_gamma, 0.1, 5.0, 0.05, tip="中間色の明るさを補正します。")
        slider(c1_in, "ぼかし", self.v_blur, 0.0, 5.0, 0.1, tip="ノイズを低減します。")
        slider(c1_in, "シャープ", self.v_sharp, 0.0, 5.0, 0.1, tip="輪郭を強調します。")

        # 2. 検査領域の設定
        c2, c2_in = create_card(sf, "2. 検査領域の設定")
        c2.pack(fill=tk.X, pady=5, padx=5)
        tk.Label(c2_in, text="※右側のプレビュー画面でマウスをドラッグし、\n  検査をおこなう範囲（黄色枠）を指定してください。",
                 font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, justify=tk.LEFT).pack(anchor="w", padx=15, pady=5)

        # 3. 二値化設定
        c3, c3_in = create_card(sf, "3. 二値化設定")
        c3.pack(fill=tk.X, pady=5, padx=5)

        btn_auto_all = tk.Button(c3_in, text="AI全自動調整", font=FONT_BOLD,
                    bg=COLOR_OK, fg="black", relief="flat",
                    command=self._auto_tune_image_processing)
        btn_auto_all.pack(fill=tk.X, padx=10, pady=5)
        Tooltip(btn_auto_all, "二値化モードからしきい値、フィルタまで全てを自動走査して最適な処理を探します。")

        tk.Label(c3_in, text="二値化モード:", font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(anchor="w", padx=10)
        self.thr_mode_var = tk.StringVar(value=ip.get("threshold_mode", "simple"))
        mode_f = tk.Frame(c3_in, bg=COLOR_BG_PANEL)
        mode_f.pack(fill=tk.X, padx=10, pady=2)
        for txt, val in [("固定しきい値", "simple"), ("自動適応", "adaptive"), ("動的割合", "dynamic")]:
            tk.Radiobutton(mode_f, text=txt, variable=self.thr_mode_var, value=val,
                           font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                           selectcolor=COLOR_BG_INPUT,
                           command=self._mark_changed).pack(side=tk.LEFT, padx=5)

        self.v_threshold = tk.IntVar(value=ip.get("threshold", 30))
        self.v_ada_block = tk.IntVar(value=ip.get("ada_block", 11))
        self.v_ada_c = tk.IntVar(value=ip.get("ada_c", 2))
        self.v_white_ratio = tk.IntVar(value=ip.get("white_ratio", 3))

        f_thr, s_thr = self._slider_with_label(c3_in, "固定しきい値", self.v_threshold, 0, 255, tip="固定モード時：対象を浮き上がらせる境界の明るさ。")
        f_ada_b, s_ada_b = self._slider_with_label(c3_in, "自動適応: 範囲", self.v_ada_block, 3, 99, 2, tip="自動適応モード時：明るさを計算する範囲（奇数指定）。")
        f_ada_c, s_ada_c = self._slider_with_label(c3_in, "自動適応: 調整", self.v_ada_c, -30, 30, tip="自動適応モード時：しきい値からの微調整オフセット。")
        f_white, s_white = self._slider_with_label(c3_in, "目標白面積率(%)", self.v_white_ratio, 1, 100, tip="動的割合モード時：白くしたい部分の割合。")

        self.bin_widgets = {
            "threshold": [s_thr],
            "ada_block": [s_ada_b],
            "ada_c": [s_ada_c],
            "white_ratio": [s_white]
        }
        self.thr_mode_var.trace_add("write", self._update_thr_ui)
        self._update_thr_ui()

        # 4. 対象抽出設定
        c4, c4_in = create_card(sf, "4. 対象抽出設定")
        c4.pack(fill=tk.X, pady=5, padx=5)

        learn_btn_f = tk.Frame(c4_in, bg=COLOR_BG_PANEL)
        learn_btn_f.pack(fill=tk.X, padx=10, pady=2)
        btn_learn = tk.Button(learn_btn_f, text="現在の映像から自動学習", font=FONT_NORMAL,
                   bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat",
                   command=self._auto_learn_contours)
        btn_learn.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        Tooltip(btn_learn, "現在の二値化結果から最大の輪郭を計測し、面積・周長のフィルタ範囲を自動設定します。")

        self.v_min_len = tk.IntVar(value=ip.get("filter_min_len", 200))
        self.v_max_len = tk.IntVar(value=ip.get("filter_max_len", 1500))
        self.v_min_area = tk.IntVar(value=ip.get("filter_min_area", 10000))
        self.v_max_area = tk.IntVar(value=ip.get("filter_max_area", 35000))
        slider(c4_in, "最小周長", self.v_min_len, 0, 5000, 10, tooltip="これより短い小さい輪郭（ノイズ）を無視します。")
        slider(c4_in, "最大周長", self.v_max_len, 0, 10000, 10, tooltip="大きすぎる輪郭を無視します。")
        slider(c4_in, "最小面積", self.v_min_area, 0, 100000, 100, tooltip="これより小さい面積を無視します。")
        slider(c4_in, "最大面積", self.v_max_area, 0, 500000, 100, tooltip="大きすぎる面積を無視します。")

        # 5. 形状補正
        c5, c5_in = create_card(sf, "5. 形状補正")
        c5.pack(fill=tk.X, pady=5, padx=5)

        flags = self.cfg.data.get("flags", {})
        self.v_contours_flag = tk.BooleanVar(value=flags.get("CONTOURS_FLAG", True))
        cb_contours = tk.Checkbutton(c5_in, text="輪郭抽出と射影変換を有効にする", variable=self.v_contours_flag,
                                     font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                                     selectcolor=COLOR_BG_INPUT, command=self._mark_changed)
        cb_contours.pack(anchor="w", padx=15, pady=2)
        Tooltip(cb_contours, "ワークの輪郭を見つけ、長方形に切り出して正面を向くように補正します。OFFの場合はカメラ映像全体でマッチングします。")

        self.v_affine_h = tk.IntVar(value=ip.get("affine_h_mm", 50))
        self.v_affine_w = tk.IntVar(value=ip.get("affine_w_mm", 40))
        slider(c5_in, "変換高さ(mm)", self.v_affine_h, 1, 200, tooltip="切り出し後の垂直方向の実寸(mm)目安。縦横比を正しく補正します。")
        slider(c5_in, "変換幅(mm)", self.v_affine_w, 1, 200, tooltip="切り出し後の水平方向の実寸(mm)目安。")

        # 6. マッチング設定
        c6, c6_in = create_card(sf, "6. マッチング設定")
        c6.pack(fill=tk.X, pady=5, padx=5)

        self.v_decision_thr = tk.DoubleVar(value=ip.get("decision_threshold", 0.8))
        slider(c6_in, "マッチング判定値", self.v_decision_thr, 0.0, 1.0, 0.01,
               tooltip="マスター画像との類似度（スコア）がこの値を上回れば『一致(OK)』と判定します。")

    def _start_adj_preview(self):
        self._stop_adj_preview()
        idx = self.cfg.get("camera", "index", default=0)
        if platform.system() == "Windows":
            try:
                self._adj_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if not self._adj_cap.isOpened(): self._adj_cap = cv2.VideoCapture(idx)
            except Exception:
                self._adj_cap = cv2.VideoCapture(idx)
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
        """調整タブの設定を適用したプレビュー用フレームを返す (engine.pyとロジックを同期)"""
        ip = {
            "clahe_clip": self.v_clahe.get(),
            "brightness": self.v_bright.get(),
            "contrast": self.v_contrast.get(),
            "saturation": self.v_saturation.get(),
            "gamma": self.v_gamma.get(),
            "blur": self.v_blur.get(),
            "sharpen": self.v_sharp.get(),
            "threshold": self.v_threshold.get(),
            "threshold_mode": self.thr_mode_var.get(),
            "ada_block": self.v_ada_block.get(),
            "ada_c": self.v_ada_c.get(),
            "white_ratio": self.v_white_ratio.get(),
        }
        
        # 共通エンジンの前処理を適用
        from .engine import InspectionEngine
        dummy_engine = InspectionEngine(self.cfg) # 最小限のインスタンス
        gray = dummy_engine.apply_preprocessing(frame.copy(), ip)
        
        # ROIマスク適用
        h, w = frame.shape[:2]
        roi = self.cfg.data.get("image_processing", {}).get("roi", [0.0, 0.0, 1.0, 1.0])
        rx1, ry1, rx2, ry2 = roi
        mask = np.zeros((h, w), dtype=np.uint8)
        cx1, cy1 = int(min(rx1, rx2) * w), int(min(ry1, ry2) * h)
        cx2, cy2 = int(max(rx1, rx2) * w), int(max(ry1, ry2) * h)
        cv2.rectangle(mask, (max(0, cx1), max(0, cy1)), (min(w, cx2), min(h, cy2)), 255, -1)
        gray = cv2.bitwise_and(gray, gray, mask=mask)

        # 二値化
        binarized = dummy_engine.binarize(gray, ip)
        
        return cv2.cvtColor(binarized, cv2.COLOR_GRAY2BGR)

    def _auto_tune_image_processing(self):
        """二値化モードを含め、画像処理設定を全自動で最適化する"""
        if getattr(self, '_adj_current_frame', None) is None:
            messagebox.showwarning("警告", "プレビューを開始してください。")
            return

        with self._frame_lock:
            frame = self._adj_current_frame.copy()
        
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # アルゴリズム: 
        # 1. 動的(Histogram)モード 3% ~ 10% をテスト
        # 2. Simple(固定)モード 30 ~ 150 をテスト
        # 3. 最も大きく、かつ極端に大きすぎない輪郭が見つかる設定を採用する
        
        candidates = []
        
        # Test Dynamic
        for wr in [3, 5, 8]:
            # engine.dynamic_threshold 相当の計算
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            total = gray.size
            t_min = int(total * (wr / 100.0))
            t_max = int(total * ((wr + 1.0) / 100.0))
            c = 0 ; thr = 30
            for i in range(255, -1, -1):
                c += hist[i][0]
                thr = max(0, i - 1)
                if t_min <= c <= t_max or c > t_max: break
            _, b = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
            cnts, _ = cv2.findContours(b, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            if cnts:
                largest = max(cnts, key=cv2.contourArea)
                candidates.append({
                    "mode": "dynamic", "wr": wr, "thr": thr,
                    "area": cv2.contourArea(largest), "len": cv2.arcLength(largest, True)
                })

        # Test Simple
        for thr in [30, 50, 70, 100, 130]:
            _, b = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
            cnts, _ = cv2.findContours(b, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            if cnts:
                largest = max(cnts, key=cv2.contourArea)
                candidates.append({
                    "mode": "simple", "thr": thr,
                    "area": cv2.contourArea(largest), "len": cv2.arcLength(largest, True)
                })

        if not candidates:
            messagebox.showerror("エラー", "ワークが見つかりません。ライティングやカメラ位置を確認してください。")
            return

        # 「画面の5%〜60%を占める最大の輪郭」を優先
        best = None
        for c in sorted(candidates, key=lambda x: x["area"], reverse=True):
            if 0.05 * gray.size < c["area"] < 0.6 * gray.size:
                best = c
                break
        
        if not best:
            best = max(candidates, key=lambda x: x["area"])

        # 設定の反映
        self.thr_mode_var.set(best["mode"])
        if best["mode"] == "dynamic":
            self.v_white_ratio.set(best["wr"])
        else:
            self.v_threshold.set(best["thr"])
            
        # フィルタ値の決定 (基準解像度 640x480 スケール)
        adj_sw = w / 640.0
        adj_sh = h / 480.0
        adj_area = adj_sw * adj_sh
        adj_len = np.sqrt((adj_sw ** 2 + adj_sh ** 2) / 2.0)
        
        base_area = best["area"] / adj_area
        base_len = best["len"] / adj_len
        
        self.v_min_area.set(int(base_area * 0.6))
        self.v_max_area.set(int(base_area * 1.5))
        self.v_min_len.set(int(base_len * 0.7))
        self.v_max_len.set(int(base_len * 1.4))
        
        self._mark_changed()
        messagebox.showinfo("自動設定完了", 
            f"最適な設定を適用しました。\n\n"
            f"モード: { '動的(Histogram)' if best['mode']=='dynamic' else '固定閾値' }\n"
            f"閾値/白面積: { best['wr'] if best['mode']=='dynamic' else best['thr'] }\n"
            f"基準面積: {int(base_area)}")

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

        # Scrollable panel for resolution/storage settings
        scroll_c = tk.Canvas(tab, bg=COLOR_BG_MAIN, highlightthickness=0)
        vsb = ttk.Scrollbar(tab, orient="vertical", command=scroll_c.yview)
        inner = tk.Frame(scroll_c, bg=COLOR_BG_MAIN)
        inner.bind("<Configure>", lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_c.create_window((0, 0), window=inner, anchor="nw")
        scroll_c.configure(yscrollcommand=vsb.set)
        scroll_c.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        vsb.pack(side="right", fill="y", pady=10)
        scroll_c.bind("<MouseWheel>", lambda e: scroll_c.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        res_full = ["320x240 (QVGA)", "640x480 (VGA)", "1280x720 (HD)",
                    "1920x1080 (Full HD)", "3840x2160 (4K)"]
        res_save = res_full + ["保存しない"]

        def row(parent, label, var, options, tooltip=""):
            f = tk.Frame(parent, bg=COLOR_BG_PANEL)
            f.pack(fill=tk.X, pady=4)
            tk.Label(f, text=label, font=FONT_SET_LBL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT, padx=15)
            cb = ttk.Combobox(f, textvariable=var, values=options,
                              state="readonly", font=FONT_SET_VAL, width=24)
            cb.pack(side=tk.LEFT, padx=10)
            var.trace_add("write", lambda *a: self._mark_changed())
            if tooltip:
                Tooltip(cb, tooltip)

        cap_card, cap_inner = create_card(inner, "基本撮影設定")
        cap_card.pack(fill=tk.X, pady=5, padx=5)
        self.res_capture_var = tk.StringVar()
        row(cap_inner, "撮影解像度:", self.res_capture_var, res_full,
            "カメラから取得する元画像サイズ。全処理の最大値となります。")

        prev_card, prev_inner = create_card(inner, "プレビュー設定")
        prev_card.pack(fill=tk.X, pady=5, padx=5)
        self.res_preview_var = tk.StringVar()
        preview_opts = ["プレビューなし", "320x240 (QVGA)", "640x480 (VGA)", "1280x720 (HD)"]
        row(prev_inner, "プレビュー解像度:", self.res_preview_var, preview_opts,
            "メイン画面のプレビューサイズ。小さくするほどCPU負荷が下がります。")

        save_card, save_inner = create_card(inner, "保存設定")
        save_card.pack(fill=tk.X, pady=5, padx=5)
        self.res_ng_var = tk.StringVar()
        self.res_ok_var = tk.StringVar()
        row(save_inner, "NG保存解像度:", self.res_ng_var, res_save,
            "NG判定時の保存サイズ。通常は最大解像度を推奨します。")
        row(save_inner, "OK保存解像度:", self.res_ok_var, res_save,
            "OK判定時の保存サイズ。容量節約のため小さめに設定できます。")

        auto_card, auto_inner = create_card(inner, "自動容量管理")
        auto_card.pack(fill=tk.X, pady=5, padx=5)
        self.auto_delete_var = tk.BooleanVar()
        cb_auto = tk.Checkbutton(auto_inner, text="上限超過時に古い画像を自動削除",
                       variable=self.auto_delete_var, font=FONT_NORMAL,
                       bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT,
                       command=self._mark_changed)
        cb_auto.pack(anchor="w", padx=15, pady=5)
        Tooltip(cb_auto, "ディスク容量が一杯になった際、古い日付の画像フォルダから順に自動削除します。")

        f_gb = tk.Frame(auto_inner, bg=COLOR_BG_PANEL)
        f_gb.pack(fill=tk.X, pady=4)
        tk.Label(f_gb, text="保存上限(GB):", font=FONT_SET_LBL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=22, anchor="w").pack(side=tk.LEFT, padx=15)
        self.max_gb_var = tk.IntVar()
        sp_gb = tk.Spinbox(f_gb, textvariable=self.max_gb_var, from_=1, to=1000,
                    font=FONT_SET_VAL, bg=COLOR_BG_INPUT, fg="white",
                    buttonbackground="#78909C", bd=1, relief="solid", width=8,
                    command=self._mark_changed)
        sp_gb.pack(side=tk.LEFT, padx=10)
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
            "SAVE_DEBUG_FLAG": ("デバッグ画像保存", "処理過程の画像を保存します（ストレージ容量に注意）。"),
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

        # 車種設定部分は不要なため削除されました
        pass

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
        self.v_clahe.set(ip.get("clahe_clip", 0.0))
        self.v_bright.set(ip.get("brightness", 1.0))
        self.v_contrast.set(ip.get("contrast", 1.0))
        self.v_saturation.set(ip.get("saturation", 1.0))
        self.v_gamma.set(ip.get("gamma", 1.0))
        self.v_blur.set(ip.get("blur", 0.0))
        self.v_sharp.set(ip.get("sharpen", 0.0))
        
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

        # 車種設定の読込は不要
        pass

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
                val = var.get()
                if isinstance(val, str):
                    # 小数点が含まれる場合はfloat、そうでなければintとして保存
                    self.cfg.set("camera", k, float(val) if "." in val else int(val))
                else:
                    # すでに数値型(IntVarなど)の場合はそのままセット
                    self.cfg.set("camera", k, val)
            except (ValueError, TypeError):
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
            "clahe_clip": self.v_clahe.get(),
            "brightness": self.v_bright.get(),
            "contrast": self.v_contrast.get(),
            "saturation": self.v_saturation.get(),
            "gamma": self.v_gamma.get(),
            "blur": self.v_blur.get(),
            "sharpen": self.v_sharp.get(),
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
        flags["CONTOURS_FLAG"] = self.v_contours_flag.get()
        for fname, var in self.flag_vars.items():
            flags[fname] = var.get()

        # 車種設定の保存は不要なため削除
        pass

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
            "カメラ・GPIOタブ": (
                "カメラの初期化と基本設定を行います。\n\n"
                "・[インデックス]: 接続されているカメラの番号です。認識されない場合は変更してください。\n"
                "・[プレビュー]: 検査中の映像表示サイズです。「プレビューなし」にすると負荷が軽減されます。\n"
                "・[自動調整]: 露出やフォーカスを周囲の明るさに合わせて一括最適化します。\n"
                "・[GPIO制御]: 全ての入出力ピンのBCM番号を設定します。「テスト」ボタンで配線チェックが可能です。"
            ),
            "画像処理タブ (最重要)": (
                "検査精度に直結する、画像の「見え方」を調整します。\n\n"
                "・**二値化モード**:\n"
                "  - [固定しきい値]: 常に一定の明るさ(閾値)で白黒を区分します。照明が常に一定な環境に有効です。\n"
                "  - [自動適応]: 周辺の明るさを考慮して二値化します。影やムラがある環境に強いモードです。\n"
                "  - [動的割合]: 画面内の白面積が一定になるよう自動調整します。ワークの汚れ等の影響を受けにくいです。\n"
                "・**画像補正**: 明るさ、コントラスト、シャープ、CLAHE(輝度補正)等を使い、検査対象が最も鮮明に見えるよう調整してください。"
            ),
            "画素数・保存タブ": (
                "・[保存解像度]: 判定ログとして残す画像のサイズを決定します。\n"
                "・[容量管理]: ディスクが一杯にならないよう、保存期間や最大容量に基づき古い画像を自動削除する設定が可能です。"
            ),
            "システム・ログタブ": (
                "・[デバッグ画像保存]: 判定時の途中経過画像（二値化後など）を保存し、NG原因の解析に役立てます。\n"
                "・[バックアップ]: 設定保存時に前回の config.json を自動でコピーし保存します。"
            )
        })

    def _toggle_gpio_test(self, var, btn):
        """GPIOテスト出力のON/OFFを切り替える"""
        if btn in self._active_test_devs:
            # 停止処理
            devs = self._active_test_devs.pop(btn)
            for d in devs:
                try:
                    d.off()
                    d.close()
                except:
                    pass
            btn.configure(text="テスト", bg="#546E7A", fg="white")
        else:
            # 開始処理
            try:
                val_raw = var.get().strip()
                if not val_raw: return
                
                pins = []
                for p_s in val_raw.split(","):
                    p_s = p_s.strip()
                    if p_s.isdigit():
                        p = int(p_s)
                        if 0 < p <= 40: pins.append(p)
                if not pins: return

                devs = []
                for p in pins:
                    devs.append(OutputDevice(p))
                
                for d in devs:
                    d.on()
                
                self._active_test_devs[btn] = devs
                # inspection_app スタイル: 出力中はオレンジ色の「停止」ボタン
                btn.configure(text="停止", bg=COLOR_WARNING, fg="black")
                
            except Exception as e:
                print(f"GPIOテスト失敗: {e}")
                messagebox.showerror("テストエラー", f"GPIOの操作に失敗しました: {e}", parent=self)

    def _poll_inputs(self):
        """設定画面表示中に有効な入力ピンのON/OFF状態を監視してUIに反映する"""
        if not self.winfo_exists():
            return
            
        from .hardware import DigitalInputDevice
        
        # 必要なピンを洗い出す
        needed_pins = {} # {var_name: pin_num}
        for pname, lbl in self._input_status_labels.items():
            if pname in self.gpio_vars:
                val_raw = self.gpio_vars[pname].get().strip()
                if val_raw.isdigit():
                    pin = int(val_raw)
                    if 0 < pin <= 40:
                        needed_pins[pname] = pin

        # 古いデバイスのクリーンアップ
        current_active_pins = set(self._active_input_devs.keys())
        needed_pin_nums = set(needed_pins.values())
        
        for pin in list(current_active_pins - needed_pin_nums):
            self._active_input_devs[pin].close()
            del self._active_input_devs[pin]
            
        # UIの更新と新規デバイスの生成
        for pname, lbl in self._input_status_labels.items():
            if pname in needed_pins:
                pin = needed_pins[pname]
                if pin not in self._active_input_devs:
                    try:
                        self._active_input_devs[pin] = DigitalInputDevice(pin, pull_up=True)
                    except Exception as e:
                        print(f"GPIO 入力モニタに失敗 (Pin {pin}): {e}")
                
                # 状態を読み取る
                if pin in self._active_input_devs:
                    try:
                        state = self._active_input_devs[pin].is_active
                        if state:
                            lbl.config(text="ON", bg=COLOR_ACCENT, fg="black")
                        else:
                            lbl.config(text="OFF", bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
                    except:
                        pass
            else:
                # 未設定状態
                lbl.config(text="---", bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB)
        
        self.after(100, self._poll_inputs)

    def _slider_with_label(self, parent, lbl, var, frm, to, res=1, tip=""):
        """スライダーとラベルを含むフレームを作成し、スライダーを返す"""
        f = tk.Frame(parent, bg=COLOR_BG_PANEL)
        f.pack(fill=tk.X, padx=10, pady=2)
        l = tk.Label(f, text=lbl, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                 fg=COLOR_TEXT_SUB, width=16, anchor="w")
        l.pack(side=tk.LEFT)
        s = tk.Scale(f, variable=var, from_=frm, to=to,
                     orient=tk.HORIZONTAL, resolution=res,
                     bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                     troughcolor=COLOR_BG_INPUT, highlightthickness=0,
                     activebackground=COLOR_ACCENT,
                     command=lambda _: self._mark_changed())
        s.pack(fill=tk.X, side=tk.LEFT, expand=True)
        if tip:
            Tooltip(s, tip)
        return f, s

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
        # 通電中のテスト出力があれば停止
        for btn, devs in self._active_test_devs.items():
            for d in devs:
                try: d.off(); d.close()
                except: pass
        self._active_test_devs.clear()
        
        # モニタリング用の入力デバイスを解放
        for d in self._active_input_devs.values():
            try: d.close()
            except: pass
        self._active_input_devs.clear()

        self._stop_cam_preview()
        self._stop_adj_preview()
        # いかなる手段で画面が閉じられても、親画面へカメラ・GPIOの再起動を通知する
        if hasattr(self, 'on_close_callback') and self.on_close_callback:
            self.on_close_callback()
            self.on_close_callback = None
        super().destroy()
