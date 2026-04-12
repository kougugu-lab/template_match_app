#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engine.py - テンプレートマッチング検査エンジン
TM3.py のコアロジックをクラス化したモジュール
"""

import cv2
import numpy as np
import os
import math
import logging
import datetime
import traceback
import time
import platform
from pathlib import Path
from .constants import TEMPLATE_MATCH_SCALE

def cv_imread(file_path, flags=cv2.IMREAD_COLOR):
    """Windows環境で日本語パスを含む画像ファイルを読み込むためのラッパー"""
    try:
        n = np.fromfile(file_path, np.uint8)
        img = cv2.imdecode(n, flags)
        return img
    except Exception:
        return None

def cv_imwrite(file_path, img, params=None):
    """Windows環境で日本語パスを含む画像ファイルを書き込むためのラッパー"""
    try:
        ext = os.path.splitext(file_path)[1]
        result, n = cv2.imencode(ext, img, params)
        if result:
            n.tofile(file_path)
            return True
        else:
            return False
    except Exception:
        return False

class InspectionEngine:
    """テンプレートマッチング検査処理の中核クラス"""

    def __init__(self, config_manager):
        self.cfg = config_manager
        self.logger = logging.getLogger(__name__)
        self.last_matched_file = None
        self.last_score = 0.0
        self._init_dirs()

    def _init_dirs(self):
        """結果保存ディレクトリを初期化"""
        base = self.cfg.get("storage", "results_dir", default="./results")
        dirs = [
            f"{base}/images/OK", f"{base}/images/NG",
            f"{base}/logs", f"{base}/debug/affine/OK",
            f"{base}/debug/affine/NG", f"{base}/debug/affine/Before",
            f"{base}/debug/gray", f"{base}/debug/contours",
            f"{base}/debug/heatmap",
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)
        self.base = base

    def _path(self, subdir, suffix=""):
        """タイムスタンプ付きファイルパスを生成"""
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(self.base, subdir)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{now}{suffix}.jpg")

    # ------------------------------------------------------------------
    # マスター画像管理
    # ------------------------------------------------------------------
    def load_master_images(self, folder):
        """指定フォルダのサブフォルダを巡回し、全画像パスとデータをリスト化"""
        paths = []
        images = []
        if not os.path.exists(folder):
            self.logger.warning(f"マスターフォルダが見つかりません: {folder}")
            return images, paths
        for subfolder in sorted(os.listdir(folder)):
            sub_path = os.path.join(folder, subfolder)
            if not os.path.isdir(sub_path):
                continue
            for fname in sorted(os.listdir(sub_path)):
                fpath = os.path.join(sub_path, fname)
                if os.path.isfile(fpath):
                    img = cv_imread(fpath, 0)
                    if img is not None:
                        images.append(img)
                        paths.append(fpath)
        self.logger.info(f"マスター画像 {len(paths)} 枚を読み込みました: {folder}")
        return images, paths

    def generate_templates(self, template_paths):
        """パスリストから画像リストを生成"""
        templates = []
        for p in template_paths:
            img = cv_imread(p, 0)
            if img is not None:
                templates.append(img)
        return templates

    # ------------------------------------------------------------------
    # 画像前処理
    # ------------------------------------------------------------------
    def apply_mask(self, image, adj_sw, adj_sh, ip):
        """マスク処理（ROI外を黒塗り）"""
        h, w = image.shape[:2]
        roi = ip.get("roi", [0.0, 0.0, 1.0, 1.0])
        # ROI は 0.0 ~ 1.0 の比率
        rx1, ry1, rx2, ry2 = roi
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        
        x1 = int(min(rx1, rx2) * w)
        y1 = int(min(ry1, ry2) * h)
        x2 = int(max(rx1, rx2) * w)
        y2 = int(max(ry1, ry2) * h)
        
        cv2.rectangle(mask, (max(0, x1), max(0, y1)), (min(w, x2), min(h, y2)), 255, -1)
        return cv2.bitwise_and(image, image, mask=mask)

    def apply_preprocessing(self, image, ip):
        """設定に基づき各種画像補正を適用 (OpenCV)"""
        # --- カラー補正 (HSV/LAB空間などでの処理) ---
        # 引数がグレースケールの場合はBGRに変換して処理（後でLに戻す）
        is_gray = (len(image.shape) == 2)
        if is_gray:
            proc = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            proc = image.copy()

        # 1. CLAHE (輝度正規化) - LAB空間で行う
        clahe_clip = ip.get("clahe_clip", 0.0)
        if clahe_clip > 0:
            lab = cv2.cvtColor(proc, cv2.COLOR_BGR2LAB)
            clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(8, 8))
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
            proc = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        # 2. 明るさ・コントラスト
        # PILの方式に合わせる: Brightness=factor, Contrast=factor
        # Brightness: 1.0 = 原画
        b_factor = ip.get("brightness", 1.0)
        c_factor = ip.get("contrast", 1.0)
        if b_factor != 1.0 or c_factor != 1.0:
            # f(x) = c * x + (b-1)*128 (簡易的な近似)
            # より詳細には PIL の ImageEnhance と同様の挙動を目指す
            proc = cv2.convertScaleAbs(proc, alpha=c_factor, beta=(b_factor - 1.0) * 128)

        # 3. 彩度
        s_factor = ip.get("saturation", 1.0)
        if s_factor != 1.0 and not is_gray:
            hsv = cv2.cvtColor(proc, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] *= s_factor
            hsv[:, :, 1] = np.clip(hsv[:, :, 1], 0, 255)
            proc = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        # 4. ガンマ補正
        g_val = ip.get("gamma", 1.0)
        if g_val != 1.0:
            lut = np.array([pow(i / 255.0, 1.0 / g_val) * 255 for i in range(256)]).astype(np.uint8)
            proc = cv2.LUT(proc, lut)

        # 5. ぼかし
        blur_val = ip.get("blur", 0.0)
        if blur_val > 0:
            k = int(blur_val * 2) * 2 + 1
            proc = cv2.GaussianBlur(proc, (k, k), 0)

        # 6. シャープネス
        sharp_val = ip.get("sharpen", 0.0)
        if sharp_val > 0:
            kernel = np.array([[-1, -1, -1], [-1, 9 + sharp_val, -1], [-1, -1, -1]])
            proc = cv2.filter2D(proc, -1, kernel)

        # 最終的にグレースケールを返す（エンジン内の後続処理がグレー前提のため）
        return cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY)

    def dynamic_threshold(self, gray, white_ratio):
        """ヒストグラムを用いて目標白面積比率からの閾値をO(1)で決定"""
        if white_ratio <= 0:
            return 0, 0.0

        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        total_pixels = gray.size
        target_min = int(total_pixels * (white_ratio / 100.0))
        target_max = int(total_pixels * ((white_ratio + 1.0) / 100.0))

        cumulative = 0
        for i in range(255, -1, -1):
            cumulative += hist[i][0]
            thr = max(0, i - 1)
            
            if target_min <= cumulative <= target_max:
                return thr, (cumulative / total_pixels * 100.0)
            
            # もし目標を超えてしまった場合はそこが限界点
            if cumulative > target_max:
                return thr, (cumulative / total_pixels * 100.0)
                
        return 30, 0.0

    def binarize(self, gray, ip):
        """設定に従い一元化された二値化処理を実行"""
        mode = ip.get("threshold_mode", "simple")
        thr = ip.get("threshold", 30)
        
        if mode == "dynamic":
            thr, ratio = self.dynamic_threshold(gray, ip.get("white_ratio", 3))
            _, binarized = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
            return binarized
        elif mode == "adaptive":
            bs = ip.get("ada_block", 11)
            bs = bs + 1 if bs % 2 == 0 else bs
            c = ip.get("ada_c", 2)
            binarized = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, bs, c)
            return binarized
        else: # simple
            _, binarized = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
            return binarized

    # ------------------------------------------------------------------
    # 輪郭抽出・射影変換
    # ------------------------------------------------------------------
    def filter_length_contours(self, contours, min_len, max_len, adj_sw, adj_sh):
        adj = math.sqrt((adj_sw ** 2 + adj_sh ** 2) / 2)
        return [c for c in contours
                if min_len * adj <= cv2.arcLength(c, True) <= max_len * adj]

    def filter_area_contours(self, contours, min_area, max_area, adj_sw, adj_sh):
        adj = adj_sw * adj_sh
        return [c for c in contours
                if min_area * adj <= cv2.contourArea(c) <= max_area * adj]

    def extract_contours(self, gray, ip, adj_sw, adj_sh, filter_quad=False, save_debug=True):
        """輪郭抽出・フィルタリング・デバッグ保存"""
        flags = self.cfg.data.get("flags", {})
        binarized = self.binarize(gray, ip)
        
        do_save = save_debug and flags.get("SAVE_DEBUG_FLAG", False)

        if do_save:
            cv_imwrite(self._path("debug/gray", f"_{ip.get('threshold', 30)}"), binarized)

        contours, _ = cv2.findContours(binarized, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        contours = self.filter_length_contours(
            contours, ip["filter_min_len"], ip["filter_max_len"], adj_sw, adj_sh)
        contours = self.filter_area_contours(
            contours, ip["filter_min_area"], ip["filter_max_area"], adj_sw, adj_sh)

        if not contours:
            return [], None, binarized, None, [], False

        debug_frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        cv2.drawContours(debug_frame, list(contours), -1, (0, 0, 255), 3)
        if do_save:
            cv_imwrite(self._path("debug/contours"), debug_frame)

        areas, card_cnt, approx = [], None, None
        if filter_quad:
            sorted_cnts = sorted(contours, key=cv2.contourArea, reverse=True)
            if len(sorted_cnts) >= 3:
                card_cnt = sorted_cnts[2]
            elif sorted_cnts:
                card_cnt = sorted_cnts[0]
            else:
                return [], None, binarized, None, list(contours), False

            for coef in range(1, 10):
                eps = 0.01 * coef * cv2.arcLength(card_cnt, True)
                approx = cv2.approxPolyDP(card_cnt, eps, True)
                if len(approx) == 4:
                    areas.append(approx)
                    break
            if not areas:
                eps = 0.01 * cv2.arcLength(card_cnt, True)
                approx = cv2.approxPolyDP(card_cnt, eps, True)
                areas.append(approx)
        else:
            card_cnt = max(contours, key=cv2.contourArea)
            eps = 0.01 * cv2.arcLength(card_cnt, True)
            approx = cv2.approxPolyDP(card_cnt, eps, True)
            areas.append(approx)

        return areas, approx, binarized, card_cnt, list(contours), True

    def perspective_transform(self, areas, card_cnt, frame, binarized, h_px, w_px):
        """射影変換処理"""
        flags = self.cfg.data.get("flags", {})
        do_save = flags.get("SAVE_DEBUG_FLAG", False)

        if not areas:
            if do_save:
                cv_imwrite(self._path("debug/affine/NG", "_noarea"), frame)
            return None, None, False
        src = np.float32(areas[0])
        if src.shape != (4, 1, 2):
            if do_save:
                cv_imwrite(self._path("debug/affine/NG", "_badshape"), frame)
            return None, None, False

        x, y, w, h = cv2.boundingRect(card_cnt)
        img_cx = x + w / 2
        if src[0][0][0] < img_cx:
            dst = np.float32([[0, 0], [0, h_px], [w_px, h_px], [w_px, 0]])
        else:
            dst = np.float32([[w_px, 0], [0, 0], [0, h_px], [w_px, h_px]])

        M = cv2.getPerspectiveTransform(src, dst)
        trans_rgb = cv2.warpPerspective(frame, M, (w_px, h_px))
        trans_bin = cv2.warpPerspective(binarized, M, (w_px, h_px))
        if do_save:
            cv_imwrite(self._path("debug/affine/OK"), trans_bin)
        return trans_bin, trans_rgb, True

    # ------------------------------------------------------------------
    # テンプレートマッチング
    # ------------------------------------------------------------------
    def template_match(self, binarized, template_paths, template_list, decision_thr):
        """テンプレートマッチングを実行し、最良一致フォルダ名と最大スコアを返す"""
        SCALE = TEMPLATE_MATCH_SCALE
        matched_names = []
        max_val = 0.0
        best_resized = None
        best_result = None
        best_idx = -1
        best_pt = (0, 0)

        small = cv2.resize(binarized, None, fx=SCALE, fy=SCALE,
                           interpolation=cv2.INTER_NEAREST)

        for i, tmpl in enumerate(template_list):
            if tmpl is None:
                continue
            small_t = cv2.resize(tmpl, None, fx=SCALE, fy=SCALE,
                                 interpolation=cv2.INTER_NEAREST)
            if small_t.shape[0] > small.shape[0] or small_t.shape[1] > small.shape[1]:
                continue
            result = cv2.matchTemplate(small, small_t, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(result)
            if mv > max_val:
                max_val = mv
                best_resized = small_t
                best_result = result
                best_idx = i
                best_pt = ml

        if max_val >= decision_thr and best_idx != -1:
            matched_file = os.path.basename(template_paths[best_idx])
            folder_name = os.path.basename(os.path.dirname(template_paths[best_idx]))
            matched_names.append(folder_name)
            self.last_matched_file = matched_file
            self.last_score = max_val
            self.logger.info(f"マッチング成功: ファイル[{matched_file}] フォルダ[{folder_name}] スコア[{max_val:.3f}]")
        else:
            self.last_matched_file = None
            self.last_score = max_val
            self.logger.info(f"マッチング不一致: 最高スコア[{max_val:.3f}]")

        return matched_names, best_resized, best_result, max_val

    # ------------------------------------------------------------------
    # 全体処理フロー
    # ------------------------------------------------------------------
    def run(self, frame, template_paths, template_list):
        """1フレームのテンプレートマッチング検査を実行し、仕様IDまたは'99'を返す"""
        ip = self.cfg.data.get("image_processing", {})
        flags = self.cfg.data.get("flags", {})
        decision_thr = ip.get("decision_threshold", 0.8)
        h_orig, w_orig = frame.shape[:2]
        adj_sw = w_orig / 640
        adj_sh = h_orig / 480
        h_px = int(ip.get("affine_h_mm", 50)) * 10
        w_px = int(ip.get("affine_w_mm", 40)) * 10

        cv2.imwrite(self._path("debug/gray", "_raw"), frame)

        proc = self.apply_mask(frame.copy(), adj_sw, adj_sh, ip)
        # 共通前処理の適用 (明るさ・コントラスト・CLAHE等)
        gray = self.apply_preprocessing(proc, ip)

        # 二値化の処理とログは extract_contours (経由の binarize) に集約済み。

        matched = []
        if flags.get("CONTOURS_FLAG", True):
            areas, approx, binarized, card_cnt, _, found = self.extract_contours(
                gray, ip, adj_sw, adj_sh, filter_quad=False)

            if found and areas:
                trans_bin, trans_rgb, ok = self.perspective_transform(
                    areas, card_cnt, frame, binarized, h_px, w_px)
                if ok:
                    matched, *_ = self.template_match(
                        trans_bin, template_paths, template_list, decision_thr)

        if not matched:
            # 輪郭なし or 射影変換スキップ: 直接マッチング
            binarized = self.binarize(gray, ip)
            matched, *_ = self.template_match(
                binarized, template_paths, template_list, decision_thr)

        if matched:
            return os.path.basename(matched[0])
        return None


    # ------------------------------------------------------------------
    # カメラユーティリティ
    # ------------------------------------------------------------------
    @staticmethod
    def open_camera(config_manager):
        """設定に従いカメラを開く"""
        cam_cfg = config_manager.data.get("camera", {})
        idx = cam_cfg.get("index", 0)
        res = cam_cfg.get("resolution", "1920x1080")
        try:
            w, h = map(int, res.split("x"))
        except Exception:
            w, h = 1920, 1080

        if platform.system() == "Windows":
            # 1. MSMF (Windows 10/11で最も安定)
            try:
                cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
            except Exception:
                cap = cv2.VideoCapture()

            # 2. DSHOW (MSMFがダメな場合や、詳細設定が必要な古いカメラ用)
            if not cap.isOpened():
                if cap: cap.release()
                try:
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                except Exception:
                    cap = cv2.VideoCapture()

            # 3. 最後にバックエンド指定なしで試行
            if not cap.isOpened():
                if cap: cap.release()
                cap = cv2.VideoCapture(idx)
        else:
            # Linux (V4L2)
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(idx)

        if cap.isOpened():
            backend_name = cap.getBackendName() if hasattr(cap, "getBackendName") else "Unknown"
            print(f"カメラ初期化成功 (Index: {idx}, Backend: {backend_name})")
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            InspectionEngine.apply_camera_settings(cap, cam_cfg)
        return cap

    @staticmethod
    def apply_camera_settings(cap, cam_cfg):
        """カメラパラメータを設定"""
        cap.set(cv2.CAP_PROP_GAIN, cam_cfg.get("gain", 70))
        cap.set(cv2.CAP_PROP_EXPOSURE, cam_cfg.get("exposure", 2500))
        cap.set(cv2.CAP_PROP_BRIGHTNESS, cam_cfg.get("brightness", 0))
        cap.set(cv2.CAP_PROP_CONTRAST, cam_cfg.get("contrast", 50))
        cap.set(cv2.CAP_PROP_SATURATION, cam_cfg.get("saturation", 50))
        cap.set(cv2.CAP_PROP_HUE, cam_cfg.get("hue", 0))
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, cam_cfg.get("wb_temp", 4000))
        autofocus = cam_cfg.get("autofocus", 1) # Default 1 (オートフォーカスON)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, autofocus)
        if hasattr(cv2, "CAP_PROP_FOCUS") and autofocus == 0:
            cap.set(cv2.CAP_PROP_FOCUS, cam_cfg.get("focus", 0))
        cap.set(cv2.CAP_PROP_ZOOM, cam_cfg.get("zoom", 1))
        cap.set(cv2.CAP_PROP_FPS, cam_cfg.get("fps", 30))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # ------------------------------------------------------------------
    # ログ保存
    # ------------------------------------------------------------------
    def save_log(self, label, spec=""):
        """テキストログを日付別ファイルに追記"""
        now = datetime.datetime.now()
        folder = os.path.join(self.base, "logs")
        os.makedirs(folder, exist_ok=True)
        fpath = os.path.join(folder, f"app_{now.strftime('%Y%m%d')}.log")
        line = f"{now.strftime('%Y%m%d_%H%M%S')} {label}: {spec}\n"
        try:
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            self.logger.error(f"ログ保存エラー: {e}")

    def save_image(self, frame, subdir, suffix="", config_manager=None):
        """フレームを指定サブディレクトリに保存"""
        if frame is None:
            return
        if config_manager:
            res_key = "res_ng" if "NG" in subdir.upper() else "res_ok"
            res = config_manager.get("storage", res_key, default="640x480")
            if res == "保存しない":
                return
            try:
                rw, rh = map(int, res.split("x"))
                frame = cv2.resize(frame, (rw, rh))
            except Exception:
                pass
        path = self._path(f"images/{subdir}", suffix)
        cv_imwrite(path, frame)
        return path
