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
from .engine import cv_imread, cv_imwrite


class EditorView(tk.Frame):
    """マスター画像作成・編集ビュー（image_editor + data augmentation 統合）"""

    def __init__(self, parent, config_manager):
        super().__init__(parent, bg=COLOR_BG_MAIN)
        self.cfg = config_manager
        self._build_ui()

        # 画像・履歴管理
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
        self.bind_all("<MouseWheel>", self.on_mousewheel)

    def _build_ui(self):
        """UIの骨格を構築"""
        # ---- 左パネル（スクロール可能な設定エリア） ----
        left_outer, left_inner = create_card(self, "編集ツール")
        left_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_outer.configure(width=360)
        left_outer.pack_propagate(False)

        ctrl_canvas = tk.Canvas(left_inner, bg=COLOR_BG_PANEL, highlightthickness=0, width=330)
        ctrl_sb = ttk.Scrollbar(left_inner, orient="vertical", command=ctrl_canvas.yview)
        self.ctrl_frame = tk.Frame(ctrl_canvas, bg=COLOR_BG_PANEL)
        self.ctrl_frame.bind("<Configure>",
                             lambda e: ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all")))
        ctrl_canvas.create_window((0, 0), window=self.ctrl_frame, anchor="nw")
        ctrl_canvas.configure(yscrollcommand=ctrl_sb.set)
        ctrl_canvas.pack(side="left", fill="both", expand=True)
        ctrl_sb.pack(side="right", fill="y")

        def _wheel(event):
            ctrl_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        ctrl_canvas.bind("<MouseWheel>", _wheel)

        # ---- 右パネル（プレビューキャンバス） ----
        right_outer, right_inner = create_card(self, "プレビュー")
        right_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        zoom_bar = tk.Frame(right_inner, bg=COLOR_BG_PANEL)
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

        self.canvas = tk.Canvas(right_inner, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

    def _build_sliders(self):
        """設定パネルのウィジェットを構築"""
        sf = self.ctrl_frame

        # --- セクション1: 読込・射影変換 ---
        self._section(sf, "1. 読込・射影変換")
        tk.Button(sf, text="画像を読み込み", font=FONT_NORMAL, bg=COLOR_ACCENT,
                  fg="black", relief="flat",
                  command=self.load_image).pack(fill=tk.X, pady=3, padx=5)

        mode_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        mode_f.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(mode_f, text="マウス操作:", font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        for txt, val in [("点を選択", "point"), ("範囲切抜", "trim")]:
            tk.Radiobutton(mode_f, text=txt, variable=self.mouse_mode, value=val,
                           font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                           selectcolor=COLOR_BG_INPUT, indicatoron=0,
                           activebackground=COLOR_ACCENT, activeforeground="black",
                           relief="flat", padx=5).pack(side=tk.LEFT, padx=2)

        mm_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        mm_f.pack(fill=tk.X, padx=5, pady=3)
        for lbl, var in [("H(mm):", self.mm_height), ("W(mm):", self.mm_width)]:
            tk.Label(mm_f, text=lbl, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
            tk.Spinbox(mm_f, textvariable=var, from_=1, to=999,
                       width=5, font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                       fg="white", bd=1, relief="solid").pack(side=tk.LEFT, padx=3)

        for lbl, cmd in [("現在の輪郭を採用", self.adopt_contour),
                         ("射影変換を実行", self.apply_perspective),
                         ("座標リセット", self.reset_perspective)]:
            tk.Button(sf, text=lbl, font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                      fg=COLOR_TEXT_MAIN, relief="flat",
                      command=cmd).pack(fill=tk.X, pady=2, padx=5)

        # --- セクション2: 抽出設定 ---
        self._section(sf, "2. 輪郭抽出設定")
        cb_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        cb_f.pack(fill=tk.X, padx=5)
        tk.Checkbutton(cb_f, text="二値化プレビュー", variable=self.enable_binary,
                       font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT, command=self.update_image).pack(side=tk.LEFT)
        for txt, val in [("Simple", "simple"), ("Adaptive", "adaptive")]:
            tk.Radiobutton(cb_f, text=txt, variable=self.threshold_mode, value=val,
                           font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                           selectcolor=COLOR_BG_INPUT, command=self.update_image).pack(side=tk.LEFT)

        self._slider(sf, "Simple閾値", 0, 255, self.binary_threshold)
        self._slider(sf, "Ada Block", 3, 99, self.ada_block)
        self._slider(sf, "Ada C", -30, 30, self.ada_c)
        self._slider(sf, "最小周長", 0, 5000, self.min_len)
        self._slider(sf, "最大周長", 0, 5000, self.max_len)
        self._slider(sf, "最小面積", 0, 100000, self.min_area)
        self._slider(sf, "最大面積", 0, 100000, self.max_area)

        # --- セクション3: 加工調整 ---
        self._section(sf, "3. 加工調整")
        self._slider(sf, "CLAHE", 0.0, 5.0, self.clahe_clip, res=0.1)
        self._slider(sf, "輝度", 0.1, 3.0, self.brightness_var, res=0.05)
        self._slider(sf, "コントラスト", 0.1, 3.0, self.contrast_var, res=0.05)
        self._slider(sf, "彩度", 0.1, 3.0, self.saturation_var, res=0.05)
        self._slider(sf, "ガンマ", 0.1, 5.0, self.gamma_var, res=0.05)
        self._slider(sf, "ぼかし", 0.0, 5.0, self.blur_var, res=0.1)
        self._slider(sf, "シャープ", 0.0, 5.0, self.sharpen_var, res=0.1)

        # --- セクション4: 保存 ---
        self._section(sf, "4. 保存設定")
        tk.Checkbutton(sf, text="縦横比固定", variable=self.keep_aspect,
                       font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                       selectcolor=COLOR_BG_INPUT).pack(anchor=tk.W, padx=5)
        px_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        px_f.pack(fill=tk.X, padx=5, pady=3)
        for lbl, var in [("幅(px):", self.save_width_px), ("高(px):", self.save_height_px)]:
            tk.Label(px_f, text=lbl, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
            tk.Spinbox(px_f, textvariable=var, from_=1, to=9999,
                       width=6, font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                       fg="white", bd=1, relief="solid").pack(side=tk.LEFT, padx=2)
        self.save_width_px.trace_add("write", self.sync_height)

        for lbl, cmd, bg in [
            ("現在のサイズを取得", self.get_current_size, COLOR_BG_INPUT),
            ("汎用画像として保存", self.save_image, COLOR_BG_INPUT),
            ("新マスターとして登録...", self.register_master_image, COLOR_OK),
            ("既存マスターの削除...", self.delete_master_image, COLOR_NG),
            ("戻る(Undo)", self.undo, COLOR_BG_INPUT),
            ("全てリセット", self.reset_all, COLOR_NG),
        ]:
            tk.Button(sf, text=lbl, font=FONT_NORMAL, bg=bg,
                      fg="white" if bg != COLOR_OK else "black",
                      relief="flat", command=cmd).pack(fill=tk.X, pady=2, padx=5)

        # --- セクション5: データ拡張 ---
        self._section(sf, "5. データ拡張 (Augmentation)")
        aug_f = tk.Frame(sf, bg=COLOR_BG_PANEL)
        aug_f.pack(fill=tk.X, padx=5, pady=3)
        for lbl, var, frm, to in [
            ("生成枚数:", self.aug_num, 1, 500),
            ("回転角(deg):", self.aug_angle, 0, 45),
            ("ノイズ:", self.aug_noise, 0, 100),
        ]:
            row = tk.Frame(aug_f, bg=COLOR_BG_PANEL)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=lbl, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                     fg=COLOR_TEXT_SUB, width=14, anchor="w").pack(side=tk.LEFT)
            tk.Spinbox(row, textvariable=var, from_=frm, to=to,
                       width=7, font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                       fg="white", bd=1, relief="solid").pack(side=tk.LEFT)

        src_row = tk.Frame(sf, bg=COLOR_BG_PANEL)
        src_row.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(src_row, text="元画像フォルダ:", font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        self.aug_src_var = tk.StringVar(value=self.cfg.get("augment", "master_dir", default="./master_image/source"))
        tk.Entry(src_row, textvariable=self.aug_src_var, font=FONT_NORMAL,
                 bg=COLOR_BG_INPUT, fg="white", bd=1, relief="solid").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(src_row, text="...", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat",
                  command=lambda: self.aug_src_var.set(
                      filedialog.askdirectory() or self.aug_src_var.get())).pack(side=tk.LEFT)

        out_row = tk.Frame(sf, bg=COLOR_BG_PANEL)
        out_row.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(out_row, text="出力フォルダ:", font=FONT_NORMAL,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        self.aug_out_var = tk.StringVar(value=self.cfg.get("augment", "output_dir", default="./master_image"))
        tk.Entry(out_row, textvariable=self.aug_out_var, font=FONT_NORMAL,
                 bg=COLOR_BG_INPUT, fg="white", bd=1, relief="solid").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(out_row, text="...", font=FONT_NORMAL, bg=COLOR_BG_INPUT,
                  fg=COLOR_TEXT_MAIN, relief="flat",
                  command=lambda: self.aug_out_var.set(
                      filedialog.askdirectory() or self.aug_out_var.get())).pack(side=tk.LEFT)

        self.aug_progress = ttk.Progressbar(sf, orient=tk.HORIZONTAL, mode='determinate')
        self.aug_progress.pack(fill=tk.X, padx=5, pady=3)
        self.aug_status_lbl = tk.Label(sf, text="待機中", font=FONT_NORMAL,
                                       bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        self.aug_status_lbl.pack()
        self.btn_aug = tk.Button(sf, text="データ拡張を実行", font=FONT_BOLD,
                  bg=COLOR_WARNING, fg="black", relief="flat",
                  command=self.run_augmentation)
        self.btn_aug.pack(fill=tk.X, pady=(10, 5), padx=5, ipady=3)

    def _section(self, parent, title):
        tk.Label(parent, text=f"  {title}", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_ACCENT, anchor="w").pack(
            fill=tk.X, pady=(12, 2))
        tk.Frame(parent, bg=COLOR_BORDER, height=1).pack(fill=tk.X, padx=5, pady=2)

    def _slider(self, parent, label, from_, to, var, res=1):
        f = tk.Frame(parent, bg=COLOR_BG_PANEL)
        f.pack(fill=tk.X, padx=5, pady=1)
        tk.Label(f, text=label, font=FONT_NORMAL, bg=COLOR_BG_PANEL,
                 fg=COLOR_TEXT_SUB, width=10, anchor="w").pack(side=tk.LEFT)
        tk.Scale(f, from_=from_, to=to, variable=var, orient=tk.HORIZONTAL,
                 resolution=res, command=self.update_image,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                 troughcolor=COLOR_BG_INPUT, highlightthickness=0,
                 activebackground=COLOR_ACCENT).pack(fill=tk.X, side=tk.LEFT, expand=True)

    # ---------------------------------------------------------------
    # 画像操作
    # ---------------------------------------------------------------
    def load_image(self):
        f = filedialog.askopenfilename(
            filetypes=[("画像ファイル", "*.png *.jpg *.jpeg *.bmp"), ("全て", "*.*")])
        if f:
            self.original_image = Image.open(f).convert("RGB")
            self.history.clear()
            self.is_transformed = False
            self.points = []
            self.mouse_mode.set("point")
            self.save_state()
            self.update_image()
            self.get_current_size()

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
        base = (self.transformed_image.copy() if self.is_transformed
                else self.original_image.copy())
        cv_img = cv2.cvtColor(np.array(base), cv2.COLOR_RGB2BGR)

        if self.clahe_clip.get() > 0:
            lab = cv2.cvtColor(cv_img, cv2.COLOR_BGR2LAB)
            clahe = cv2.createCLAHE(clipLimit=self.clahe_clip.get(), tileGridSize=(8, 8))
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
            cv_img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        if self.blur_var.get() > 0:
            k = int(self.blur_var.get() * 2) * 2 + 1
            cv_img = cv2.GaussianBlur(cv_img, (k, k), 0)
        if self.sharpen_var.get() > 0:
            alpha = self.sharpen_var.get()
            kernel = np.array([[-1, -1, -1], [-1, 9 + alpha, -1], [-1, -1, -1]])
            cv_img = cv2.filter2D(cv_img, -1, kernel)

        img = Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
        if self.brightness_var.get() != 1.0:
            img = ImageEnhance.Brightness(img).enhance(self.brightness_var.get())
        if self.contrast_var.get() != 1.0:
            img = ImageEnhance.Contrast(img).enhance(self.contrast_var.get())
        if self.saturation_var.get() != 1.0:
            img = ImageEnhance.Color(img).enhance(self.saturation_var.get())
        if self.gamma_var.get() != 1.0:
            lut = [int(pow(i / 255.0, 1.0 / self.gamma_var.get()) * 255) for i in range(256)]
            img = img.point(lut * 3)

        if self.enable_binary.get():
            gray_img = img.convert("L")
            if self.threshold_mode.get() == "simple":
                img = gray_img.point(
                    lambda x: 255 if x > self.binary_threshold.get() else 0, mode="1"
                ).convert("RGB")
            else:
                cv_gray = np.array(gray_img)
                bk = self.ada_block.get()
                bk = bk + 1 if bk % 2 == 0 else bk
                cv_bin = cv2.adaptiveThreshold(cv_gray, 255,
                                               cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                               cv2.THRESH_BINARY, bk, self.ada_c.get())
                img = Image.fromarray(cv_bin).convert("RGB")

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
        self.points = []
        self.is_transformed = False
        self.transformed_image = None
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
