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
import datetime
import sys
from pathlib import Path
from .constants import VALID_BCM_PINS, RES_OPTIONS_RAW, RES_OPTIONS_PREVIEW

SETTINGS_FILE = "config.json"

OPERATION_PRESETS = {
    "fast": {"camera_fps": 12, "preview_fps": 16.0, "ok_output_time": 0.1, "ng_output_time": 0.0, "result_display_time": 1.0},
    "standard": {"camera_fps": 10, "preview_fps": 12.0, "ok_output_time": 0.2, "ng_output_time": 0.0, "result_display_time": 1.5},
    "accurate": {"camera_fps": 8, "preview_fps": 10.0, "ok_output_time": 0.25, "ng_output_time": 0.0, "result_display_time": 2.0},
}

ENVIRONMENT_PRESETS = {
    "windows_dev": {"preview_fps_max": 12.0, "camera_fps_max": 10},
    "raspi_prod": {"preview_fps_max": 20.0, "camera_fps_max": 20},
}

DEFAULT_CONFIG = {
    "camera": {
        "index": 0, "resolution": "1920x1080", "preview_res": "640x480",
        "gain": 70, "exposure": 2500, "brightness": 0,
        "contrast": 50, "saturation": 50, "hue": 0,
        "wb_temp": 4000, "focus": 0, "zoom": 1, "fps": 10
    },
    "gpio": {
        "triggers": [
            {"id": "trig_start", "name": "開始トリガー", "pin": 16}
        ],
        "pattern_pins": [],
        "outputs": {
            "ok": 23, "ng": 24
        }
    },
    "patterns": {},
    "pattern_order": [],
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
        "res_record": "1920x1080", "res_skip": "640x480",
        "max_results_gb": 30, "auto_delete_enabled": False
    },
    "inference": {
        "preview_fps": 12.0,
        "ok_output_time": 0.2,
        "ng_output_time": 0.0,
        "result_display_time": 1.5,
        "max_retries": 0,
        "burst_interval": 0.2
    },
    "runtime": {
        "operation_preset": "standard",
        "environment_profile": "auto",
        "auto_apply_environment": True,
        "compat_cleanup_enabled": False,
        "legacy_key_cleanup_after": "2026-07-01"
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
        """不要になった古い設定キーを削除し、必要に応じて新構造へ移行する"""
        # 1. 画像処理・フラグ系のクリーンアップ
        if "image_processing" in data:
            for k in ["mask_lh_up", "mask_lh_down", "mask_rh_up", "mask_rh_down", "mask_top", "mask_bottom"]:
                data["image_processing"].pop(k, None)
        if "flags" in data:
            for k in ["CLAHE_FLAG", "MASK_SECOND_FLAG", "SIO_FLAG", "LENGTH_FILTER_FLAG", "AREA_FILTER_FLAG", "THRESHOLD_FLAG", "ADAPTIVE_FLAG"]:
                data["flags"].pop(k, None)

        # 2. GPIO 構造の移行 (gpio_pins -> gpio)
        if "gpio_pins" in data and "gpio" not in data:
            old = data.pop("gpio_pins")
            data["gpio"] = {
                "triggers": [{"id": "trig_start", "name": "開始トリガー", "pin": old.get("pin_Start", 16)}],
                "pattern_pins": [
                    {"id": f"sel_{i+1}", "name": f"ピン {i+1}", "pin": p}
                    for i, p in enumerate(old.get("pattern_pins", []))
                ],
                "outputs": {
                    "ok": old.get("pin_OKlog", 23),
                    "ng": old.get("pin_NGlog", 24)
                }
            }

        # 3. パターン構造の移行 (specification_mapping -> patterns & pattern_order)
        if "specification_mapping" in data and "patterns" not in data:
            old_map = data.pop("specification_mapping")
            data["patterns"] = {}
            data["pattern_order"] = []
            # ID順にソートして移行
            sids = sorted(old_map.keys(), key=lambda x: int(x) if str(x).isdigit() else 999)
            for sid in sids:
                pid = f"p_{sid}"
                data["patterns"][pid] = old_map[sid]
                data["pattern_order"].append(pid)

        return data

    def _normalize_settings(self, data):
        """設定値の正規化と最低限の整合性補正"""
        # camera
        cam = data.setdefault("camera", {})
        if not isinstance(cam.get("index", 0), int):
            try:
                cam["index"] = int(cam.get("index", 0))
            except Exception:
                cam["index"] = 0

        valid_res = set(RES_OPTIONS_RAW)
        valid_pre = set(RES_OPTIONS_PREVIEW)
        if cam.get("resolution") not in valid_res:
            cam["resolution"] = "1920x1080"
        if cam.get("preview_res") not in valid_pre:
            cam["preview_res"] = "640x480"

        # gpio
        gpio = data.setdefault("gpio", {})
        trigs = gpio.setdefault("triggers", [])
        if not trigs:
            trigs.append({"id": "trig_start", "name": "開始トリガー", "pin": 16})
        for i, t in enumerate(trigs):
            t.setdefault("id", f"trig_{i+1}")
            t.setdefault("name", f"トリガー{i+1}")
            try:
                t["pin"] = int(t.get("pin", 0))
            except Exception:
                t["pin"] = 0
            if t["pin"] not in VALID_BCM_PINS:
                t["pin"] = 0

        ppins = gpio.setdefault("pattern_pins", [])
        for i, p in enumerate(ppins):
            p.setdefault("id", f"sel_{i+1}")
            p.setdefault("name", f"ピン {i+1}")
            try:
                p["pin"] = int(p.get("pin", 0))
            except Exception:
                p["pin"] = 0
            if p["pin"] not in VALID_BCM_PINS:
                p["pin"] = 0

        outs = gpio.setdefault("outputs", {})
        for key, default_pin in (("ok", 23), ("ng", 24)):
            try:
                pin = int(outs.get(key, default_pin))
            except Exception:
                pin = default_pin
            outs[key] = pin if pin in VALID_BCM_PINS else default_pin

        # patterns/order
        patterns = data.setdefault("patterns", {})
        pat_order = data.setdefault("pattern_order", [])
        pat_order = [pid for pid in pat_order if pid in patterns]
        for pid in patterns.keys():
            if pid not in pat_order:
                pat_order.append(pid)
        data["pattern_order"] = pat_order

        pin_len = len(ppins)
        for pid in pat_order:
            p = patterns.setdefault(pid, {})
            p.setdefault("name", pid)
            cond = p.get("pin_condition", [])
            if not isinstance(cond, list):
                cond = []
            norm = []
            for i in range(pin_len):
                try:
                    norm.append(1 if int(cond[i]) else 0)
                except Exception:
                    norm.append(0)
            p["pin_condition"] = norm

        # inference
        inf = data.setdefault("inference", {})
        try:
            inf["preview_fps"] = max(1.0, float(inf.get("preview_fps", 12.0)))
        except Exception:
            inf["preview_fps"] = 12.0
        try:
            inf["ok_output_time"] = max(0.0, float(inf.get("ok_output_time", 0.2)))
        except Exception:
            inf["ok_output_time"] = 0.2
        try:
            inf["ng_output_time"] = max(0.0, float(inf.get("ng_output_time", 0.0)))
        except Exception:
            inf["ng_output_time"] = 0.0
        try:
            inf["result_display_time"] = max(0.0, float(inf.get("result_display_time", 1.5)))
        except Exception:
            inf["result_display_time"] = 1.5
        try:
            inf["max_retries"] = max(0, int(inf.get("max_retries", 0)))
        except Exception:
            inf["max_retries"] = 0
        try:
            inf["burst_interval"] = max(0.0, float(inf.get("burst_interval", 0.2)))
        except Exception:
            inf["burst_interval"] = 0.2

        # runtime
        rt = data.setdefault("runtime", {})
        op = str(rt.get("operation_preset", "standard"))
        if op not in OPERATION_PRESETS:
            op = "standard"
        rt["operation_preset"] = op

        env = str(rt.get("environment_profile", "auto"))
        if env not in {"auto", "windows_dev", "raspi_prod"}:
            env = "auto"
        rt["environment_profile"] = env
        rt["auto_apply_environment"] = bool(rt.get("auto_apply_environment", True))
        rt["compat_cleanup_enabled"] = bool(rt.get("compat_cleanup_enabled", False))
        rt["legacy_key_cleanup_after"] = str(rt.get("legacy_key_cleanup_after", "2026-07-01"))

        # 実行環境プロファイルの自動適用（上限制御）
        resolved_env = env
        if env == "auto":
            resolved_env = "windows_dev" if sys.platform == "win32" else "raspi_prod"
        rt["resolved_environment"] = resolved_env
        if rt["auto_apply_environment"] and resolved_env in ENVIRONMENT_PRESETS:
            e = ENVIRONMENT_PRESETS[resolved_env]
            inf["preview_fps"] = min(float(inf.get("preview_fps", 12.0)), float(e["preview_fps_max"]))
            cam["fps"] = min(int(cam.get("fps", 10)), int(e["camera_fps_max"]))

        # storage
        st = data.setdefault("storage", {})
        st["results_dir"] = str(st.get("results_dir", "./results")).strip() or "./results"
        if st.get("res_ok") not in valid_res | {"保存しない"}:
            st["res_ok"] = "640x480"
        if st.get("res_ng") not in valid_res | {"保存しない"}:
            st["res_ng"] = "1920x1080"
        if st.get("res_skip") not in valid_res | {"保存しない"}:
            st["res_skip"] = st.get("res_ok", "640x480")
        if st.get("res_record") not in valid_res | {"保存しない"}:
            st["res_record"] = st.get("res_ng", "1920x1080")
        try:
            st["max_results_gb"] = max(1, float(st.get("max_results_gb", 30)))
        except Exception:
            st["max_results_gb"] = 30
        st["auto_delete_enabled"] = bool(st.get("auto_delete_enabled", False))

        # 旧キーの段階的クリーンアップ（明示有効時のみ）
        cleanup_due = False
        if rt["compat_cleanup_enabled"]:
            try:
                border = datetime.date.fromisoformat(rt["legacy_key_cleanup_after"])
                if datetime.date.today() >= border:
                    cleanup_due = True
            except Exception:
                pass
        if cleanup_due:
            st.pop("res_ok", None)
            st.pop("res_ng", None)
        else:
            # 互換期間中は旧キーへ同期
            st["res_ok"] = st["res_skip"]
            st["res_ng"] = st["res_record"]

        return data

    def _load(self):
        """設定ファイルを読み込む"""
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                merged = self._deep_merge(DEFAULT_CONFIG, loaded)
                cleaned = self._clean_legacy_keys(merged)
                return self._normalize_settings(cleaned)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"設定ファイル読み込みエラー ({e})。デフォルト設定を使用します。")
        return self._normalize_settings(self._clean_legacy_keys(copy.deepcopy(DEFAULT_CONFIG)))

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
