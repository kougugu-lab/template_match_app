#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
constants.py - フォント・カラー・パス定数
inspection_app/modules/constants.py と同一定義
"""

import os
from pathlib import Path
import cv2

# --- ファイル・パス ---
SETTINGS_FILE = "config.json"
RESULTS_DIR = Path("./results")

# --- 有効なBCMピン番号 (Raspberry Pi 40ピンヘッダ) ---
VALID_BCM_PINS = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                  16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27}

# --- 解像度オプション ---
RES_OPTIONS = ["320x240 (QVGA)", "640x480 (VGA)", "1280x720 (HD)",
               "1920x1080 (Full HD)", "3840x2160 (4K)"]
RES_OPTIONS_RAW = ["320x240", "640x480", "1280x720", "1920x1080", "3840x2160"]
RES_OPTIONS_PREVIEW = ["プレビューなし", "320x240 (QVGA)", "640x480 (VGA)", "1280x720 (HD)"]
RES_OPTIONS_SAVE = RES_OPTIONS + ["保存しない"]

# --- バージョン情報 ---
APP_VERSION = "1.4.0"
APP_BUILD_DATE = "2026-04-14"
APP_NAME = "テンプレートマッチング検査システム"

# --- フォント定義 ---
FONT_FAMILY = "Meiryo UI"
FONT_NORMAL = (FONT_FAMILY, 14)
FONT_BOLD = (FONT_FAMILY, 16, "bold")
FONT_LARGE = (FONT_FAMILY, 24, "bold")
FONT_HUGE = (FONT_FAMILY, 48, "bold")

# 設定画面用
FONT_SET_TAB = (FONT_FAMILY, 18, "bold")
FONT_SET_LBL = (FONT_FAMILY, 16, "bold")
FONT_SET_VAL = (FONT_FAMILY, 16)
FONT_BTN_LARGE = (FONT_FAMILY, 16, "bold")

# --- カラーパレット (Dark Gray Theme) ---
COLOR_BG_MAIN = "#2b2b2b"
COLOR_BG_PANEL = "#3c3f41"
COLOR_BG_INPUT = "#45494a"
COLOR_TEXT_MAIN = "#FFFFFF"
COLOR_TEXT_SUB = "#B0BEC5"
COLOR_ACCENT = "#4FC3F7"
COLOR_ACCENT_HOVER = "#81D4FA"

# ステータスカラー
COLOR_OK = "#66BB6A"
COLOR_NG = "#FF5252"
COLOR_NG_MUTED = "#B06666"
COLOR_WARNING = "#FFB74D"

# ハイライト色
COLOR_HIGHLIGHT = "#3F494F"

# ボーダー色
COLOR_BORDER = "#505050"

# --- CV2 カメラプロパティ マッピング ---
CAM_PROP_MAP = {
    "fps": cv2.CAP_PROP_FPS, "focus": cv2.CAP_PROP_FOCUS,
    "autofocus": cv2.CAP_PROP_AUTOFOCUS,
    "gain": cv2.CAP_PROP_GAIN, "exposure": cv2.CAP_PROP_EXPOSURE,
    "brightness": cv2.CAP_PROP_BRIGHTNESS, "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION, "hue": cv2.CAP_PROP_HUE,
    "wb_temp": cv2.CAP_PROP_TEMPERATURE, "zoom": cv2.CAP_PROP_ZOOM
}

# --- テンプレートマッチング用スケール ---
TEMPLATE_MATCH_SCALE = 0.25
