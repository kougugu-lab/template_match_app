#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
settings.py - 設定管理 (ConfigManager)
config.json の読み書きを担当する
"""

import json
import os
import copy
import logging

SETTINGS_FILE = "config.json"

DEFAULT_CONFIG = {
    "camera": {
        "index": 0, "resolution": "1920x1080", "preview_res": "640x480",
        "gain": 70, "exposure": 2500, "brightness": 0,
        "contrast": 50, "saturation": 50, "hue": 0,
        "wb_temp": 4000, "focus": 0, "zoom": 1, "fps": 5
    },
    "gpio_pins": {
        "pin_Start": 16, "pin_OKlog": 23, "pin_NGlog": 24
    },
    "image_processing": {
        "threshold": 30, "threshold_mode": "simple",
        "ada_block": 11, "ada_c": 2, "white_ratio": 3,
        "affine_h_mm": 50, "affine_w_mm": 40,
        "filter_min_len": 200, "filter_max_len": 1500,
        "filter_min_area": 10000, "filter_max_area": 35000,
        "roi": [0.0, 0.0, 1.0, 1.0],
        "decision_threshold": 0.8
    },
    "flags": {
        "CONTOURS_FLAG": True,
        "SAVE_DEBUG_FLAG": False
    },
    "storage": {
        "results_dir": "./results",
        "res_ng": "1920x1080", "res_ok": "640x480",
        "max_results_gb": 10, "auto_delete_enabled": False
    },
    "augment": {
        "master_dir": "./master_image/source",
        "output_dir": "./master_image/augmented",
        "num_variants": 50,
        "canvas_h": 640, "canvas_w": 500,
        "angle_range": 5, "scale_range": [0.9, 1.1],
        "brightness_range": [-30, 30], "noise_level": 10
    }
}


class ConfigManager:
    """設定ファイル (config.json) の読み書きを管理するクラス"""

    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self.data = self._load()
        self.logger = logging.getLogger(__name__)

    def _deep_merge(self, base, override):
        """デフォルト設定に保存済み設定をマージ（型安全）"""
        result = copy.deepcopy(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    def _clean_legacy_keys(self, data):
        """不要になった古い設定キーを削除してクリーンな設定を保つ"""
        if "image_processing" in data:
            for k in ["mask_lh_up", "mask_lh_down", "mask_rh_up", "mask_rh_down", "mask_top", "mask_bottom"]:
                data["image_processing"].pop(k, None)
        if "flags" in data:
            for k in ["CLAHE_FLAG", "MASK_SECOND_FLAG", "SIO_FLAG", "LENGTH_FILTER_FLAG", "AREA_FILTER_FLAG", "THRESHOLD_FLAG", "ADAPTIVE_FLAG"]:
                data["flags"].pop(k, None)
        return data

    def _load(self):
        """設定ファイルを読み込む"""
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                merged = self._deep_merge(DEFAULT_CONFIG, loaded)
                return self._clean_legacy_keys(merged)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"設定ファイル読み込みエラー ({e})。デフォルト設定を使用します。")
        return self._clean_legacy_keys(copy.deepcopy(DEFAULT_CONFIG))

    def save(self):
        """設定ファイルに保存（上書き前にバックアップ作成）"""
        try:
            import shutil
            if os.path.exists(self.path):
                bak_path = self.path + ".bak"
                try:
                    shutil.copy2(self.path, bak_path)
                except Exception as e:
                    self.logger.warning(f"バックアップ作成に失敗しました: {e}")

            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"設定保存エラー: {e}")
            return False

    def get(self, *keys, default=None):
        """ネストされたキーを安全に取得 get("camera", "gain")"""
        try:
            v = self.data
            for k in keys:
                v = v[k]
            return v
        except (KeyError, TypeError):
            return default

    def set(self, *keys_and_value):
        """ネストされたキーに値を設定 set("camera", "gain", 70)"""
        keys = keys_and_value[:-1]
        value = keys_and_value[-1]
        d = self.data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    def get_master_folder(self):
        """マスターフォルダパスを返す"""
        master_path = "./master_image/"
        if not os.path.exists(master_path):
            try:
                os.makedirs(master_path, exist_ok=True)
            except OSError:
                pass
        return master_path
