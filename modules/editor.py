#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
editor.py - 編集+拡張ビュー (EditorView)
image_editor_gui_ver3.py + maseter_image.py を統合したビュー
"""

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk, ImageEnhance
import os
import glob
import random
import threading
import time
from collections import deque

from .constants import (
    COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT,
    COLOR_OK, COLOR_NG, COLOR_WARNING, COLOR_BORDER,
    FONT_FAMILY, FONT_NORMAL, FONT_BOLD, FONT_LARGE, FONT_SET_LBL
)
from .widgets import create_card, Tooltip
from .engine import cv_imread, cv_imwrite, InspectionEngine


class EditorView(tk.Frame):
    """マスター画像作成・編集ビュー（image_editor + data augmentation 統合）"""

    def __init__(self, parent, config_manager, app=None):
        super().__init__(parent, bg=COLOR_BG_MAIN)
        self.cfg = config_manager
        self.app = app
        
        # 保存先を車種フォルダに自動同期
        self._sync_aug_paths()
        
        self._build_ui()

        # 画像・履歴管理
        self.raw_original_image = None
        self.original_image = None
        self.transformed_image = None
        self.current_image = None
        self.history = deque(maxlen=20)

        # 状態管理
        self.points = []
        self.is_transformed = False
        self.selected_point_idx = None
        self.scale_factor = 1.0
        self.zoom_level = 1.0
        self.crop_start = None
        self.crop_end = None
        self.is_cropping = False
        self.mouse_mode = tk.StringVar(value="point")
        self.image_x = 0
        self.image_y = 0

        # 調整パラメータ
        self.brightness_var = tk.DoubleVar(value=1.0)
        self.contrast_var = tk.DoubleVar(value=1.0)
        self.saturation_var = tk.DoubleVar(value=1.0)
        self.gamma_var = tk.DoubleVar(value=1.0)
        self.blur_var = tk.DoubleVar(value=0.0)
        self.sharpen_var = tk.DoubleVar(value=0.0)
        self.clahe_clip = tk.DoubleVar(value=0.0)
        self.mm_width = tk.IntVar(value=40)
        self.mm_height = tk.IntVar(value=50)
        self.save_width_px = tk.IntVar(value=400)
        self.save_height_px = tk.IntVar(value=500)
        self.keep_aspect = tk.BooleanVar(value=True)
        self.enable_binary = tk.BooleanVar(value=False)
        self.threshold_mode = tk.StringVar(value="simple")
        self.binary_threshold = tk.IntVar(value=127)
        self.ada_block = tk.IntVar(value=11)
        self.ada_c = tk.IntVar(value=2)
        self.min_len = tk.IntVar(value=100)
        self.max_len = tk.IntVar(value=2000)
        self.min_area = tk.IntVar(value=500)
        self.max_area = tk.IntVar(value=50000)

        # データ拡張パラメータ
        augcfg = self.cfg.data.get("augment", {})
        self.aug_num = tk.IntVar(value=augcfg.get("num_variants", 50))
        self.aug_angle = tk.DoubleVar(value=augcfg.get("angle_range", 7))
        self.aug_noise = tk.IntVar(value=augcfg.get("noise_level", 10))

        self._build_sliders()
        self.canvas.bind("<Button-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        # 画像エリア上ではズームに使用
        self.canvas.bind("<Enter>", lambda _: self.canvas.bind_all("<MouseWheel>", self.on_mousewheel))
        self.canvas.bind("<Leave>", lambda _: self.canvas.unbind_all("<MouseWheel>"))

        self.sync_settings()
        self.update_button_states()

    def _sync_aug_paths(self):
        """設定からデフォルトフォルダを取得し反映する"""
        master_folder = self.cfg.get_master_folder()
        self.aug_src_var = tk.StringVar(value=self.cfg.get("augment", "master_dir", default=os.path.join(master_folder, "source")))
        self.aug_out_var = tk.StringVar(value=self.cfg.get("augment", "output_dir", default=os.path.join(master_folder, "augmented")))

    def sync_settings(self):
        """詳細設定の画像処理パラメータとスライダーを同期する"""
        ip = self.cfg.data.get("image_processing", {})
        self.brightness_var.set(ip.get("brightness", 1.0))
        self.contrast_var.set(ip.get("contrast", 1.0))
        self.saturation_var.set(ip.get("saturation", 1.0))
        self.gamma_var.set(ip.get("gamma", 1.0))
        self.blur_var.set(ip.get("blur", 0.0))
        self.sharpen_var.set(ip.get("sharpen", 0.0))
        self.clahe_clip.set(ip.get("clahe_clip", 0.0))
        self.threshold_mode.set(ip.get("threshold_mode", "simple"))
        self.binary_threshold.set(ip.get("threshold", 127))
        self.ada_block.set(ip.get("ada_block", 11))
        self.ada_c.set(ip.get("ada_c", 2))
        self.min_len.set(ip.get("filter_min_len", 200))
        self.max_len.set(ip.get("filter_max_len", 1500))
        self.min_area.set(ip.get("filter_min_area", 10000))
        self.max_area.set(ip.get("filter_max_area", 35000))
        self.mm_width.set(ip.get("affine_w_mm", 40))
        self.mm_height.set(ip.get("affine_h_mm", 50))
        # 可能であればプレビューを更新する
        if hasattr(self, "update_image"):
            self.update_image()

    def _build_ui(self):
        """UIの骨格を構築"""
        # ---- 右パネル（スクロール可能な設定エリア） ----
        right_outer, right_inner = create_card(self, "編集ツール")
        right_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_outer.configure(width=550)
        right_outer.pack_propagate(False)

        ctrl_canvas = tk.Canvas(right_inner, bg=COLOR_BG_PANEL, highlightthickness=0, width=530)
        ctrl_sb = ttk.Scrollbar(right_inner, orient="vertical", command=ctrl_canvas.yview)
        self.ctrl_frame = tk.Frame(ctrl_canvas, bg=COLOR_BG_PANEL)
        
        # キャンバス内のウィンドウIDを保持
        self.ctrl_window = ctrl_canvas.create_window((0, 0), window=self.ctrl_frame, anchor="nw")
        
        def _on_frame_configure(event):
            # フレームのサイズに合わせてスクロール領域を更新
            ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all"))
        def _on_canvas_configure(event):
            # キャンバス幅も合わせる
            ctrl_canvas.itemconfig(self.ctrl_window, width=event.width)

        self.ctrl_frame.bind("<Configure>", _on_frame_configure)
        ctrl_canvas.bind("<Configure>", _on_canvas_configure)
        ctrl_canvas.configure(yscrollcommand=ctrl_sb.set)
        ctrl_canvas.pack(side="left", fill="both", expand=True)
        ctrl_sb.pack(side="right", fill="y")

        def _wheel(event):
            # サイドバー内でのみ垂直スクロール
            ctrl_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        # サイドバー領域に入ったときだけマウスホイールをバインド
        self.ctrl_frame.bind("<Enter>", lambda _: self.ctrl_frame.bind_all("<MouseWheel>", _wheel))
        self.ctrl_frame.bind("<Leave>", lambda _: self.ctrl_frame.unbind_all("<MouseWheel>"))

        # ---- 左パネル（プレビューキャンバス） ----
        left_outer, left_inner = create_card(self, "プレビュー")
        left_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        zoom_bar = tk.Frame(left_inner, bg=COLOR_BG_PANEL)
        zoom_bar.pack(fill=tk.X, pady=(0, 5))
        tk.Button(zoom_bar, text="拡大", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", width=5,
                  command=self.zoom_in).pack(side=tk.LEFT, padx=2)
        tk.Button(zoom_bar, text="縮小", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", width=5,
                  command=self.zoom_out).pack(side=tk.LEFT, padx=2)
        self.zoom_lbl = tk.Label(zoom_bar, text="Zoom: 100%", font=FONT_NORMAL,
                                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        self.zoom_lbl.pack(side=tk.LEFT, padx=10)

        self.canvas = tk.Canvas(left_inner, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

    def _build_sliders(self):
        """設定パネルのウィジェットを構築"""
        sf = self.ctrl_frame
        
        self.step2_widgets = []
        self.step3_widgets = []

        # --- セクション1: 画像取得 ---
        self._section(sf, "① 画像の取得")
        btn_cam = tk.Button(sf, text="カメラから撮影", font=FONT_BOLD, bg=COLOR_OK,
                  fg="black", relief="flat", command=self.capture_from_camera)
        btn_cam.pack(fill=tk.X, pady=3, padx=5, ipady=3)
        Tooltip(btn_cam, "現在カメラに映っている画像をキャプチャします。")

        btn_load = tk.Button(sf, text="PCから画像を読み込み", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", command=self.load_image)
        btn_load.pack(fill=tk.X, pady=3, padx=5)
        Tooltip(btn_load, "PC内の画像ファイルを読み込みます。")

        # --- セクション2: 抽出・変形 ---
        self._section(sf, "② 切り抜き・変形")
        mode_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        mode_f.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(mode_f, text="マウス操作:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
                 
        rb_pt = tk.Radiobutton(mode_f, text="点を選択", variable=self.mouse_mode, value="point",
                       font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT, indicatoron=0,
                       activebackground=COLOR_ACCENT, activeforeground="black", relief="flat", padx=5)
        rb_pt.pack(side=tk.LEFT, padx=2)
        Tooltip(rb_pt, "画像上で4点をクリックし、射影変換（歪み補正）の基準点を指定します。")
        
        rb_tr = tk.Radiobutton(mode_f, text="範囲切抜", variable=self.mouse_mode, value="trim",
                       font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT, indicatoron=0,
                       activebackground=COLOR_ACCENT, activeforeground="black", relief="flat", padx=5)
        rb_tr.pack(side=tk.LEFT, padx=2)
        Tooltip(rb_tr, "ドラッグして四角形で範囲を切り抜きます。")

        btn_adopt = tk.Button(sf, text="自動的に輪郭を採用", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", command=self.adopt_contour)
        btn_adopt.pack(fill=tk.X, pady=2, padx=5)
        Tooltip(btn_adopt, "AIが最大の輪郭を自動抽出し、変形基準の4点としてセットします。")

        btn_apply = tk.Button(sf, text="射影変換（真っ直ぐに補正）", font=FONT_NORMAL, bg=COLOR_OK,
                  fg="black", relief="flat", command=self.apply_perspective)
        btn_apply.pack(fill=tk.X, pady=2, padx=5)
        Tooltip(btn_apply, "指定した4点を元に、画像を真正面から見た形に変形します。")
        
        btn_undo = tk.Button(sf, text="戻る (Undo)", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat", command=self.undo)
        btn_undo.pack(fill=tk.X, pady=2, padx=5)
        
        btn_reset = tk.Button(sf, text="全てリセット", font=FONT_NORMAL, bg=COLOR_NG,
                  fg="black", relief="flat", command=self.reset_all)
        btn_reset.pack(fill=tk.X, pady=2, padx=5)

        cb_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        cb_f.pack(fill=tk.X, padx=5, pady=5)
        chk_bin = tk.Checkbutton(cb_f, text="二値化領域を確認", variable=self.enable_binary,
                       font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT, command=self.update_image)
        chk_bin.pack(side=tk.LEFT)
        Tooltip(chk_bin, "本番の検査時と同じ二値化処理をプレビューします。設定値は「詳細設定」の「画像処理タブ」のものが適用されます。")

        self.step2_widgets.extend([rb_pt, rb_tr, btn_adopt, btn_apply, btn_undo, btn_reset, chk_bin])

        # --- セクション3: 保存 ---
        self._section(sf, "③ 保存・マスター登録")
        btn_reg = tk.Button(sf, text="新マスターとして登録...", font=FONT_BOLD, bg=COLOR_OK, fg="black", relief="flat", command=self.register_master_image)
        btn_reg.pack(fill=tk.X, pady=3, padx=5, ipady=3)
        Tooltip(btn_reg, "現在のプレビュー画像を検査用マスター画像として登録します。")
        
        btn_del = tk.Button(sf, text="既存マスターの削除...", font=FONT_NORMAL, bg=COLOR_NG, fg="black", relief="flat", command=self.delete_master_image)
        btn_del.pack(fill=tk.X, pady=2, padx=5)
        Tooltip(btn_del, "現在登録されているマスター画像の中から不要なものを削除します。")
        
        btn_sv = tk.Button(sf, text="汎用画像として別名保存...", font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat", command=self.save_image)
        btn_sv.pack(fill=tk.X, pady=2, padx=5)
        Tooltip(btn_sv, "プレビューの画像をPC内の任意の場所に保存します。")

        self.step3_widgets.extend([btn_reg, btn_del, btn_sv])

        # --- セクション4: データ拡張 ---
        self._section(sf, "4. データ拡張")
        aug_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        aug_f.pack(fill=tk.X, padx=5, pady=3)
        
        row1 = tk.Frame(aug_f, bg=COLOR_BG_PANEL)
        row1.pack(fill=tk.X, pady=2)
        tk.Label(row1, text="生成枚数:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=14, anchor="w").pack(side=tk.LEFT)
        sp_an = tk.Spinbox(row1, textvariable=self.aug_num, from_=1, to=500, width=7, font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, bd=1, relief="solid")
        sp_an.pack(side=tk.LEFT)
        Tooltip(sp_an, "1枚の元画像から何枚のバリエーションを生成するかを指定します。")
        
        row2 = tk.Frame(aug_f, bg=COLOR_BG_PANEL)
        row2.pack(fill=tk.X, pady=2)
        tk.Label(row2, text="回転角(deg):", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=14, anchor="w").pack(side=tk.LEFT)
        sp_aa = tk.Spinbox(row2, textvariable=self.aug_angle, from_=0, to=45, width=7, font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, bd=1, relief="solid")
        sp_aa.pack(side=tk.LEFT)
        Tooltip(sp_aa, "最大で何ピクセルまでランダムに回転させるかの目安を指定します。")
        
        row3 = tk.Frame(aug_f, bg=COLOR_BG_PANEL)
        row3.pack(fill=tk.X, pady=2)
        tk.Label(row3, text="ノイズ:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB, width=14, anchor="w").pack(side=tk.LEFT)
        sp_ano = tk.Spinbox(row3, textvariable=self.aug_noise, from_=0, to=100, width=7, font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, bd=1, relief="solid")
        sp_ano.pack(side=tk.LEFT)
        Tooltip(sp_ano, "画像に付加するランダムノイズの強度。")

        src_row = tk.Frame(sf, bg=COLOR_BG_PANEL)
        src_row.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(src_row, text="元画像:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        tk.Entry(src_row, textvariable=self.aug_src_var, font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, bd=1, relief="solid").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        btn_src = tk.Button(src_row, text="...", font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat", command=lambda: self.aug_src_var.set(filedialog.askdirectory() or self.aug_src_var.get()))
        btn_src.pack(side=tk.LEFT)
        Tooltip(btn_src, "データ拡張のベースとなる画像が入ったフォルダを選択します。")

        out_row = tk.Frame(sf, bg=COLOR_BG_PANEL)
        out_row.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(out_row, text="出力先:", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        tk.Entry(out_row, textvariable=self.aug_out_var, font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, bd=1, relief="solid").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        btn_out = tk.Button(out_row, text="...", font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN, relief="flat", command=lambda: self.aug_out_var.set(filedialog.askdirectory() or self.aug_out_var.get()))
        btn_out.pack(side=tk.LEFT)
        Tooltip(btn_out, "生成した画像を保存するフォルダを選択します。")

        self.aug_progress = ttk.Progressbar(sf, orient=tk.HORIZONTAL, mode='determinate')
        self.aug_progress.pack(fill=tk.X, padx=5, pady=3)
        self.aug_status_lbl = tk.Label(sf, text="待機中", font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        self.aug_status_lbl.pack()
        self.btn_aug = tk.Button(sf, text="データ拡張を実行", font=FONT_BOLD, bg=COLOR_OK, fg="black", relief="flat", command=self.run_augmentation)
        self.btn_aug.pack(fill=tk.X, pady=(10, 5), padx=5, ipady=3)
        Tooltip(self.btn_aug, "元画像フォルダ内の全画像に対してデータ拡張を実行し、出力フォルダに保存します。")

    def _section(self, parent, title):
        tk.Label(parent, text=f"  {title}", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, anchor="w").pack(
            fill=tk.X, pady=(12, 2))
        tk.Frame(parent, bg=COLOR_BORDER, height=1).pack(fill=tk.X, padx=5, pady=2)

    def _slider(self, parent, label, from_, to, var, res=1, tip=None):
        f = tk.Frame(parent, bg=COLOR_BG_PANEL)
        f.pack(fill=tk.X, padx=5, pady=1)
        tk.Label(f, text=label, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                 fg=COLOR_TEXT_SUB, width=10, anchor="w").pack(side=tk.LEFT)
        sc = tk.Scale(f, from_=from_, to=to, variable=var, orient=tk.HORIZONTAL,
                 resolution=res, command=self.update_image,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                 troughcolor=COLOR_BG_INPUT, highlightthickness=0,
                 activebackground=COLOR_ACCENT)
        sc.pack(fill=tk.X, side=tk.LEFT, expand=True)
        if tip:
            Tooltip(sc, tip)

    # ---------------------------------------------------------------
    # 画像操作
    # ---------------------------------------------------------------
    def load_image(self):
        f = filedialog.askopenfilename(
            filetypes=[("画像ファイル", "*.png *.jpg *.jpeg *.bmp"), ("全て", "*.*")])
        if f:
            self.raw_original_image = Image.open(f).convert("RGB")
            self.original_image = self.raw_original_image.copy()
            self.history.clear()
            self.is_transformed = False
            self.points = []
            self.mouse_mode.set("point")
            self.image = self.original_image.copy()
            self.save_state()
            self.update_button_states()
            self.update_image()
            self.get_current_size()

    def capture_from_camera(self):
        if self.app and getattr(self.app, "last_frame", None) is not None:
            rgb = cv2.cvtColor(self.app.last_frame, cv2.COLOR_BGR2RGB)
            self.raw_original_image = Image.fromarray(rgb)
            self.original_image = self.raw_original_image.copy()
            self.history.clear()
            self.is_transformed = False
            self.points = []
            self.mouse_mode.set("point")
            self.image = self.original_image.copy()
            self.save_state()
            self.update_button_states()
            self.update_image()
            self.get_current_size()
        else:
            messagebox.showwarning("警告", "カメラから画像を取得できません。\nカメラが接続されているか、または待機状態か確認してください。")

    def save_image(self):
        if self.current_image is None:
            messagebox.showwarning("警告", "画像がありません。")
            return
        f = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("全て", "*.*")])
        if f:
            out = self.current_image.resize(
                (self.save_width_px.get(), self.save_height_px.get()),
                Image.Resampling.LANCZOS)
            out.save(f)
            messagebox.showinfo("保存完了", f"{os.path.basename(f)}")

    def adopt_contour(self):
        if self.original_image is None:
            return
        cv_img = cv2.cvtColor(np.array(self.original_image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        ip = self.cfg.data.get("image_processing", {})
        dummy_engine = InspectionEngine(self.cfg)
        b = dummy_engine.binarize(gray, ip)
        cnts, _ = cv2.findContours(b, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        valid = [c for c in cnts
                 if self.max_len.get() >= cv2.arcLength(c, True) >= self.min_len.get()
                 and self.max_area.get() >= cv2.contourArea(c) >= self.min_area.get()]
        if not valid:
            return
        largest = max(valid, key=cv2.contourArea)
        approx = cv2.approxPolyDP(largest, 0.02 * cv2.arcLength(largest, True), True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(float)
            s = pts.sum(axis=1)
            diff = np.diff(pts, axis=1)
            rect = np.zeros((4, 2), dtype="float32")
            rect[0] = pts[np.argmin(s)]
            rect[2] = pts[np.argmax(s)]
            rect[1] = pts[np.argmin(diff)]
            rect[3] = pts[np.argmax(diff)]
            self.points = [(p[0], p[1]) for p in rect]
            self.mouse_mode.set("point")
            self.update_image()

    def apply_perspective(self):
        if len(self.points) != 4:
            messagebox.showwarning("警告", "4点を選択してください。")
            return
        dw = self.mm_width.get() * 10
        dh = self.mm_height.get() * 10
        src = np.float32(self.points)
        dst = np.float32([[0, 0], [dw, 0], [dw, dh], [0, dh]])
        M = cv2.getPerspectiveTransform(src, dst)
        cv_img = cv2.cvtColor(np.array(self.original_image), cv2.COLOR_RGB2BGR)
        warped = cv2.warpPerspective(cv_img, M, (dw, dh))
        self.transformed_image = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        self.is_transformed = True
        self.save_state()
        self.get_current_size()
        self.update_image()

    def apply_crop(self):
        if not self.crop_start or not self.crop_end:
            return
        x1 = min(self.crop_start[0], self.crop_end[0])
        y1 = min(self.crop_start[1], self.crop_end[1])
        x2 = max(self.crop_start[0], self.crop_end[0])
        y2 = max(self.crop_start[1], self.crop_end[1])
        base = self.transformed_image if self.is_transformed else self.original_image
        cropped = base.crop((x1, y1, x2, y2))
        if self.is_transformed:
            self.transformed_image = cropped
        else:
            self.original_image = cropped
            self.points = []
        self.crop_start = self.crop_end = None
        self.save_state()
        self.get_current_size()
        self.update_image()

    def update_image(self, *args):
        if self.original_image is None:
            return
        
        # 射影変換済みかどうかに基づきベース画像を選択
        base = (self.transformed_image.copy() if self.is_transformed
                else self.original_image.copy())
        
        # 設定値を収集
        ip = {
            "clahe_clip": self.clahe_clip.get(),
            "brightness": self.brightness_var.get(),
            "contrast": self.contrast_var.get(),
            "saturation": self.saturation_var.get(),
            "gamma": self.gamma_var.get(),
            "blur": self.blur_var.get(),
            "sharpen": self.sharpen_var.get(),
            "threshold": self.binary_threshold.get(),
            "threshold_mode": self.threshold_mode.get(),
            "ada_block": self.ada_block.get(),
            "ada_c": self.ada_c.get(),
        }

        # 共通エンジンの前処理を適用 (OpenCV)
        from .engine import InspectionEngine
        dummy_engine = InspectionEngine(self.cfg)
        cv_img = cv2.cvtColor(np.array(base), cv2.COLOR_RGB2BGR)
        gray = dummy_engine.apply_preprocessing(cv_img, ip)

        # 二値化テストが有効な場合
        if self.enable_binary.get():
            bin_img = dummy_engine.binarize(gray, ip)
            img = Image.fromarray(cv2.cvtColor(bin_img, cv2.COLOR_GRAY2RGB))
        else:
            # 二値化しない場合は補正後のグレートーンをカラーに戻して表示（プレビュー用）
            img = Image.fromarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))

        self.current_image = img
        self.display_on_canvas()

    def display_on_canvas(self):
        if self.current_image is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1:
            self.after(100, self.display_on_canvas)
            return
        iw, ih = self.current_image.size
        self.scale_factor = min(cw / iw, ch / ih, 0.95) * self.zoom_level
        dw = int(iw * self.scale_factor)
        dh = int(ih * self.scale_factor)
        display_img = self.current_image.resize((dw, dh), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(display_img)
        self.image_x = (cw - dw) // 2
        self.image_y = (ch - dh) // 2
        self.canvas.delete("all")
        self.canvas.create_image(self.image_x, self.image_y, anchor=tk.NW, image=self.photo)
        self.zoom_lbl.config(text=f"Zoom: {int(self.zoom_level * 100)}%")

        if not self.is_transformed:
            self._draw_contour_preview()
            for i, p in enumerate(self.points):
                px = p[0] * self.scale_factor + self.image_x
                py = p[1] * self.scale_factor + self.image_y
                c = "red" if i == self.selected_point_idx else "lime"
                self.canvas.create_oval(px - 6, py - 6, px + 6, py + 6, fill=c, outline="white")
                if i > 0:
                    pp = self.points[i - 1]
                    self.canvas.create_line(
                        pp[0] * self.scale_factor + self.image_x,
                        pp[1] * self.scale_factor + self.image_y,
                        px, py, fill="yellow", width=2)
                if i == 3 and len(self.points) == 4:
                    p0 = self.points[0]
                    self.canvas.create_line(
                        px, py,
                        p0[0] * self.scale_factor + self.image_x,
                        p0[1] * self.scale_factor + self.image_y,
                        fill="yellow", width=2)

    def _draw_contour_preview(self):
        if self.original_image is None:
            return
        cv_img = cv2.cvtColor(np.array(self.original_image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        if self.threshold_mode.get() == "simple":
            _, b = cv2.threshold(gray, self.binary_threshold.get(), 255, cv2.THRESH_BINARY)
        else:
            bk = self.ada_block.get()
            bk = bk + 1 if bk % 2 == 0 else bk
            b = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, bk, self.ada_c.get())
        cnts, _ = cv2.findContours(b, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        valid = [c for c in cnts
                 if self.max_len.get() >= cv2.arcLength(c, True) >= self.min_len.get()
                 and self.max_area.get() >= cv2.contourArea(c) >= self.min_area.get()]
        sf = self.scale_factor
        for cnt in valid:
            pts = (cnt * sf + [self.image_x, self.image_y]).astype(int).reshape(-1, 2).tolist()
            if len(pts) > 1:
                self.canvas.create_polygon(pts, outline="green", fill="", width=2)

    # ---------------------------------------------------------------
    # マウス操作
    # ---------------------------------------------------------------
    def on_mouse_down(self, event):
        if self.original_image is None:
            return
        ix = (event.x - self.image_x) / self.scale_factor
        iy = (event.y - self.image_y) / self.scale_factor
        if self.mouse_mode.get() == "point" and not self.is_transformed:
            for i, p in enumerate(self.points):
                if np.sqrt((p[0] - ix) ** 2 + (p[1] - iy) ** 2) < 15 / self.scale_factor:
                    self.selected_point_idx = i
                    return
            if len(self.points) < 4:
                self.points.append((ix, iy))
                self.update_image()
        else:
            self.crop_start = (int(ix), int(iy))
            self.is_cropping = True

    def on_mouse_move(self, event):
        ix = (event.x - self.image_x) / self.scale_factor
        iy = (event.y - self.image_y) / self.scale_factor
        if self.selected_point_idx is not None:
            self.points[self.selected_point_idx] = (ix, iy)
            self.update_image()
        if self.is_cropping:
            self.crop_end = (int(ix), int(iy))
            self.canvas.delete("crop_rect")
            if self.crop_start:
                x1 = self.crop_start[0] * self.scale_factor + self.image_x
                y1 = self.crop_start[1] * self.scale_factor + self.image_y
                x2 = ix * self.scale_factor + self.image_x
                y2 = iy * self.scale_factor + self.image_y
                self.canvas.create_rectangle(x1, y1, x2, y2,
                                             outline="red", width=2, tags="crop_rect")

    def on_mouse_up(self, event):
        self.selected_point_idx = None
        self.is_cropping = False
        if self.crop_start and self.crop_end:
            self.apply_crop()

    def zoom_in(self):
        self.zoom_level = min(self.zoom_level * 1.2, 5.0)
        self.display_on_canvas()

    def zoom_out(self):
        self.zoom_level = max(self.zoom_level / 1.2, 0.2)
        self.display_on_canvas()

    def on_mousewheel(self, event):
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    # ---------------------------------------------------------------
    # ユーティリティ
    # ---------------------------------------------------------------
    def update_button_states(self):
        state = tk.NORMAL if self.original_image is not None else tk.DISABLED
        for w in self.step2_widgets + self.step3_widgets:
            try:
                w.config(state=state)
            except tk.TclError:
                pass

    def sync_height(self, *args):
        if self.keep_aspect.get() and (self.transformed_image or self.original_image):
            try:
                base = self.transformed_image if self.is_transformed else self.original_image
                w = self.save_width_px.get()
                aspect = base.height / base.width
                self.save_height_px.set(int(w * aspect))
            except Exception:
                pass

    def get_current_size(self):
        base = self.transformed_image if self.is_transformed else self.original_image
        if base:
            self.save_width_px.set(base.width)
            self.save_height_px.set(base.height)

    def save_state(self):
        state = (self.is_transformed,
                 self.transformed_image.copy() if self.transformed_image else None,
                 list(self.points))
        self.history.append(state)

    def undo(self):
        if len(self.history) > 1:
            self.history.pop()
            self.is_transformed, self.transformed_image, self.points = self.history[-1]
            self.update_image()

    def reset_perspective(self):
        # 読込直後の状態に復元
        if self.raw_original_image:
            self.original_image = self.raw_original_image.copy()
        self.points = []
        self.is_transformed = False
        self.transformed_image = None
        self.update_button_states()
        self.save_state()
        self.update_image()

    def reset_all(self):
        self.brightness_var.set(1.0)
        self.contrast_var.set(1.0)
        self.saturation_var.set(1.0)
        self.gamma_var.set(1.0)
        self.clahe_clip.set(0.0)
        self.blur_var.set(0.0)
        self.sharpen_var.set(0.0)
        self.reset_perspective()

    def register_master_image(self):
        """現在の状態をマスター画像として登録する"""
        from tkinter import simpledialog
        img = self.transformed_image if self.is_transformed else self.original_image
        if img is None:
            messagebox.showinfo("情報", "登録する画像がありません。")
            return

        class_name = simpledialog.askstring("マスター登録", "クラス名 (例: A, J00_S, etc...) を入力してください:")
        if not class_name:
            return  # キャンセル

        src_dir = self.aug_src_var.get()
        class_dir = os.path.join(src_dir, class_name)
        os.makedirs(class_dir, exist_ok=True)
        
        timestamp = int(time.time() * 1000)
        filename = f"{class_name}_{timestamp}.png"
        filepath = os.path.join(class_dir, filename)

        try:
            cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            if cv_imwrite(filepath, cv_img):
                messagebox.showinfo("登録完了", f"マスター画像として登録されました:\n{filepath}")
            else:
                messagebox.showerror("エラー", f"保存処理に失敗しました:\n{filepath}")
        except Exception as e:
            messagebox.showerror("エラー", f"登録に失敗しました:\n{e}")

    def delete_master_image(self):
        """既存のマスター画像を削除するダイアログ"""
        src_dir = self.aug_src_var.get()
        if not os.path.exists(src_dir):
            messagebox.showerror("エラー", "登録フォルダが存在しません。")
            return
            
        file_path = filedialog.askopenfilename(
            initialdir=src_dir,
            title="削除するマスター画像を選択",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp")]
        )
        if file_path:
            if messagebox.askyesno("削除確認", f"以下のマスター画像を完全に削除しますか？\n{os.path.basename(file_path)}"):
                try:
                    os.remove(file_path)
                    messagebox.showinfo("削除完了", "マスター画像を削除しました。")
                except Exception as e:
                    messagebox.showerror("エラー", f"削除に失敗しました:\n{e}")

    # ---------------------------------------------------------------
    # データ拡張
    # ---------------------------------------------------------------
    def run_augmentation(self):
        """データ拡張バッチ処理をスレッドで実行"""
        src = self.aug_src_var.get()
        out = self.aug_out_var.get()
        if not os.path.exists(src):
            messagebox.showerror("エラー", f"元画像フォルダが見つかりません:\n{src}")
            return
        os.makedirs(out, exist_ok=True)
        n = self.aug_num.get()
        angle = self.aug_angle.get()
        noise = self.aug_noise.get() / 1000.0

        # ボタン無効化して多重実行防止
        if hasattr(self, 'btn_aug'):
            self.btn_aug.config(state=tk.DISABLED, text="実行中...")

        def _worker():
            try:
                subfolders = [f.path for f in os.scandir(src) if f.is_dir()]
                if not subfolders:
                    subfolders = [src]

                total_work = len(subfolders)
                global_count = 0
                for i, folder_path in enumerate(subfolders):
                    char_label = os.path.basename(folder_path)
                    char_out_dir = os.path.join(out, char_label) if folder_path != src else out
                    os.makedirs(char_out_dir, exist_ok=True)
                    image_files = glob.glob(os.path.join(folder_path, "*.*"))

                    for img_path in image_files:
                        master_img = cv_imread(img_path)
                        if master_img is None:
                            continue
                        for j in range(n):
                            aug = self._augment(master_img, angle, noise)
                            fname = f"{char_label}_{global_count}.png"
                            cv_imwrite(os.path.join(char_out_dir, fname), aug)
                            global_count += 1

                    progress_val = int((i + 1) / total_work * 100)
                    self.after(0, lambda v=progress_val, lbl=char_label:
                               self._update_aug_progress(v, f"処理中: {lbl} ({v}%)"))

                self.after(0, lambda: self._update_aug_progress(
                    100, f"完了: {global_count} 枚を {out} に保存"))
                self.after(0, lambda: messagebox.showinfo("完了", "データ拡張が完了しました。"))
            except Exception as e:
                self.after(0, lambda err=str(e): messagebox.showerror("エラー", f"データ拡張中にエラーが発生しました:\n{err}"))
                self.after(0, lambda: self._update_aug_progress(0, "エラーにより停止"))
            finally:
                if hasattr(self, 'btn_aug'):
                    self.after(0, lambda: self.btn_aug.config(state=tk.NORMAL, text="データ拡張を実行"))

        self.aug_progress["value"] = 0
        self._update_aug_progress(0, "実行中...")
        threading.Thread(target=_worker, daemon=True).start()

    def _update_aug_progress(self, value, text):
        self.aug_progress["value"] = value
        self.aug_status_lbl.config(text=text,
                                   fg=COLOR_OK if value >= 100 else COLOR_TEXT_SUB)

    @staticmethod
    def _augment(img, angle_range=7, noise_prob=0.01):
        """1枚の画像に対してデータ拡張パイプラインを実行"""
        h, w = img.shape[:2]
        angle = random.uniform(-angle_range, angle_range)
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        variant = cv2.warpAffine(img, M, (w, h), borderValue=(255, 255, 255))

        # スケーリング
        scale = random.uniform(0.85, 1.15)
        nw, nh = int(w * scale), int(h * scale)
        rescaled = cv2.resize(variant, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((h, w, 3), 255, dtype=np.uint8)
        yo = max(0, (h - nh) // 2)
        xo = max(0, (w - nw) // 2)
        canvas[yo:yo + min(nh, h - yo), xo:xo + min(nw, w - xo)] = \
            rescaled[:min(nh, h - yo), :min(nw, w - xo)]
        variant = canvas

        # 透視変換
        if random.random() > 0.5:
            dist = 3
            pts1 = np.float32([[0, 0], [w, 0], [0, h], [w, h]])
            pts2 = np.float32([
                [random.uniform(0, dist), random.uniform(0, dist)],
                [w - random.uniform(0, dist), random.uniform(0, dist)],
                [random.uniform(0, dist), h - random.uniform(0, dist)],
                [w - random.uniform(0, dist), h - random.uniform(0, dist)]])
            M2 = cv2.getPerspectiveTransform(pts1, pts2)
            variant = cv2.warpPerspective(variant, M2, (w, h), borderValue=(255, 255, 255))

        # 明るさ・コントラスト・ガンマ
        gamma = random.uniform(0.6, 1.6)
        lut = np.array([np.clip(pow(i / 255.0, gamma) * 255.0, 0, 255)
                        for i in range(256)], np.uint8)
        variant = cv2.LUT(variant, lut)
        alpha = random.uniform(0.8, 1.2)
        beta = random.uniform(-20, 20)
        variant = cv2.convertScaleAbs(variant, alpha=alpha, beta=beta)

        # 膨張・収縮
        kernel = np.ones((2, 2), np.uint8)
        rv = random.random()
        if rv < 0.35:
            variant = cv2.erode(variant, kernel, iterations=1)
        elif rv < 0.7:
            variant = cv2.dilate(variant, kernel, iterations=1)

        # ノイズ・ぼかし
        if random.random() > 0.7:
            noisy = variant.copy()
            for i in range(variant.shape[0]):
                for j in range(variant.shape[1]):
                    r = random.random()
                    if r < noise_prob:
                        noisy[i][j] = [0, 0, 0]
                    elif r > 1 - noise_prob:
                        noisy[i][j] = [255, 255, 255]
            variant = noisy
        if random.random() > 0.7:
            k = random.choice([3, 5])
            variant = cv2.GaussianBlur(variant, (k, k), 0)

        # 二値化
        gray = cv2.cvtColor(variant, cv2.COLOR_BGR2GRAY)
        thr = random.randint(100, 180)
        _, binary = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        return binary
