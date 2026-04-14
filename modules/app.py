#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - TMApp メインアプリケーションクラス
"""

import cv2
import threading
import time
import datetime
import logging
import os
import queue
import platform
import sys
from pathlib import Path

import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

from .constants import (
    COLOR_BG_MAIN, COLOR_BG_PANEL, COLOR_BG_INPUT,
    COLOR_TEXT_MAIN, COLOR_TEXT_SUB, COLOR_ACCENT,
    COLOR_OK, COLOR_NG, COLOR_WARNING, COLOR_BORDER,
    FONT_FAMILY, FONT_NORMAL, FONT_BOLD, FONT_LARGE, FONT_HUGE,
    APP_NAME, APP_VERSION
)
from .widgets import create_card, Tooltip, HelpWindow
from .hardware import DigitalInputDevice, OutputDevice, is_gpio_available, MockManager
from .settings import ConfigManager
from .engine import InspectionEngine
from .dialogs import SettingsDialog
from .editor import EditorView


class TMApp:
    """テンプレートマッチング検査統合アプリ メインクラス"""

    def __init__(self):
        self.cfg = ConfigManager()
        self._setup_logging()
        self._init_state()
        self._setup_hardware()
        self._setup_gui()

        # Windows モックモードの仮想GPIOパネル
        if sys.platform == "win32" and not is_gpio_available():
            self._setup_mock_ui()

        # 起動後に容量監視開始 (30秒後、以降10分おき)
        self.root.after(30_000, self._monitor_storage)

    # ------------------------------------------------------------------
    # 初期化
    # ------------------------------------------------------------------
    def _setup_logging(self):
        base = self.cfg.get("storage", "results_dir", default="./results")
        log_dir = os.path.join(base, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_f = os.path.join(log_dir,
                             f"app_{datetime.datetime.now().strftime('%Y%m%d')}.log")
        for h in logging.root.handlers[:]:
            logging.root.removeHandler(h)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(message)s",
            handlers=[
                logging.FileHandler(log_f, encoding="utf-8"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"{APP_NAME} v{APP_VERSION} 起動")

    def _init_state(self):
        self.running = True
        self.ng_history = []
        self.camera_lock = threading.Lock()
        self.trigger_queue = queue.Queue()
        self.cap = None
        self.last_frame = None
        self.inputs = {}
        self.out_ok = None
        self.out_ng = None
        self.engine = None
        self.template_images = []
        self.template_paths = []
        self.preview_paused = False
        self._current_mode = "inspection"
        self.is_rendering = False
        self.pattern_inputs = {}
        self._spec_initialized = False
        self._status_reset_after_id = None

    def _setup_hardware(self):
        """GPIO・カメラ・テンプレート読み込み"""
        self._close_hardware()
        try:
            # GPIO
            gpio_cfg = self.cfg.get("gpio", default={})
            
            # トリガー入力
            for t in gpio_cfg.get("triggers", []):
                pin = t.get("pin", 0)
                if pin > 0:
                    dev = DigitalInputDevice(pin, pull_up=True)
                    # 最初のトリガーを検査トリガーとして扱う
                    if not self.inputs:
                        dev.when_activated = self._on_trigger
                    self.inputs[t["id"]] = dev

            # パターン入力
            self.pattern_inputs = {}
            for p in gpio_cfg.get("pattern_pins", []):
                pin = p.get("pin", 0)
                if pin > 0:
                    self.pattern_inputs[p["id"]] = DigitalInputDevice(pin, pull_up=True)

            # 出力
            outs = gpio_cfg.get("outputs", {})
            ok_pin = outs.get("ok", -1)
            ng_pin = outs.get("ng", -1)
            if ok_pin > 0:
                self.out_ok = OutputDevice(ok_pin)
            if ng_pin > 0:
                self.out_ng = OutputDevice(ng_pin)

            # カメラ
            cam_cfg = self.cfg.get("camera", default={})
            self.cap = InspectionEngine.open_camera(cam_cfg.get("index", 0), cam_cfg)
            if self.cap is None or not self.cap.isOpened():
                self.logger.warning("カメラを開けませんでした。モックに切り替えます。")
                self.cap = None

            # エンジン
            self.engine = InspectionEngine(self.cfg)

            # テンプレート
            folder = self.cfg.get_master_folder()
            self.template_images, self.template_paths = \
                self.engine.load_master_images(folder)

        except Exception as e:
            self.logger.error(f"ハードウェア初期化エラー: {e}")

    def _close_hardware(self):
        """GPIOとカメラの解放（設定ダイアログ遷移用）"""
        try:
            if hasattr(self, 'inputs'):
                for dev in self.inputs.values():
                    if hasattr(dev, "close"): dev.close()
                self.inputs.clear()
            if hasattr(self, 'pattern_inputs'):
                for dev in self.pattern_inputs.values():
                    if hasattr(dev, "close"): dev.close()
                self.pattern_inputs.clear()
            for dev_attr in ['out_ok', 'out_ng']:
                dev = getattr(self, dev_attr, None)
                if dev and hasattr(dev, "close"):
                    dev.close()
                setattr(self, dev_attr, None)
            if self.cap:
                self.cap.release()
                self.cap = None
        except Exception as e:
            self.logger.error(f"ハードウェア解放エラー: {e}")

    def _on_trigger(self):
        """GPIO トリガー発生時のコールバック"""
        self.trigger_queue.put("trigger")

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------
    def _setup_gui(self):
        self.root = tk.Tk()
        self.root.app_instance = self
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.configure(bg=COLOR_BG_MAIN)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # 最大化
        self.root.update_idletasks()
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                w = self.root.winfo_screenwidth()
                h = self.root.winfo_screenheight()
                self.root.geometry(f"{w}x{h}+0+0")

        self._build_header()
        self._build_content_area()
        self._update_mode_ui()
        self._sync_expected_spec_display(initial=True)

        # バックグラウンドスレッド起動
        threading.Thread(target=self._preview_loop, daemon=True).start()
        threading.Thread(target=self._inspection_loop, daemon=True).start()

    def _build_header(self):
        """ヘッダー (inspection_app 準拠)"""
        self.header = tk.Frame(self.root, bg=COLOR_BG_PANEL, height=80)
        self.header.pack(fill=tk.X)
        self.header.pack_propagate(False)

        # ステータス (左)
        self.lbl_status = tk.Label(
            self.header, text="システム待機中",
            font=FONT_LARGE, bg=COLOR_BG_PANEL, fg=COLOR_ACCENT)
        self.lbl_status.pack(side=tk.LEFT, padx=30, pady=15)

        self.lbl_clock = tk.Label(
            self.header, text="", font=FONT_BOLD,
            bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN)
        self.lbl_clock.pack(side=tk.RIGHT, padx=30)
        self._update_clock()
        Tooltip(self.lbl_clock, "現在のシステム時刻です")

        # ヘルプ (右2)
        btn_help = tk.Button(
            self.header, text="？", font=FONT_BOLD,
            bg=COLOR_BG_INPUT, fg=COLOR_ACCENT, relief="flat", width=3,
            command=self._show_help)
        btn_help.pack(side=tk.RIGHT, padx=10)
        Tooltip(btn_help, "操作ヘルプを表示します")

        # モード切替ボタン (右3)
        mode_frm = tk.Frame(self.header, bg=COLOR_BG_PANEL)
        mode_frm.pack(side=tk.RIGHT, padx=20)

        self.btn_insp = tk.Button(
            mode_frm, text="検査モード", font=FONT_BOLD,
            width=12, relief="flat",
            command=lambda: self._set_mode("inspection"))
        self.btn_insp.pack(side=tk.LEFT, padx=5)
        Tooltip(self.btn_insp, "カメラ映像の検査実行モードに切り替えます")

        self.btn_edit = tk.Button(
            mode_frm, text="編集モード", font=FONT_BOLD,
            width=12, relief="flat",
            command=lambda: self._set_mode("editor"))
        self.btn_edit.pack(side=tk.LEFT, padx=5)
        Tooltip(self.btn_edit, "マスター画像の登録・編集・データ拡張モードに切り替えます")

    def _build_content_area(self):
        """メインコンテンツエリア（モード切替ベース）"""
        self.content_area = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        self.content_area.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # 検査ビュー
        self._build_inspection_view()

        # エディタビュー
        self.editor_view = EditorView(self.content_area, self.cfg, app=self)

    def _build_inspection_view(self):
        """検査モード用ビュー（カメラプレビュー + 操作パネル）"""
        self.inspection_frame = tk.Frame(self.content_area, bg=COLOR_BG_MAIN)

        # カメラプレビュー (左)
        cam_outer, cam_inner = create_card(self.inspection_frame, "カメラプレビュー")
        cam_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.preview_canvas = tk.Canvas(cam_inner, bg="black", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)

        # 操作パネル (右)
        pnl_outer, pnl = create_card(self.inspection_frame, "操作パネル")
        pnl_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(20, 0))
        pnl_outer.configure(width=420)
        pnl_outer.pack_propagate(False)

        # 期待仕様
        tk.Label(pnl, text="期待仕様", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(5, 2))
        spec_frm = tk.Frame(pnl, bg=COLOR_BG_INPUT)
        spec_frm.pack(fill=tk.X, padx=10, pady=3)
        self.v_spec_id = tk.StringVar(value="期待: --")
        tk.Label(spec_frm, text="仕様:", font=FONT_NORMAL,
                 bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT, padx=15)
        self.lbl_spec = tk.Label(spec_frm, textvariable=self.v_spec_id,
                                 font=FONT_BOLD, bg=COLOR_BG_INPUT, fg=COLOR_ACCENT)
        self.lbl_spec.pack(side=tk.LEFT, padx=5)

        # 最終判定
        tk.Label(pnl, text="最終判定", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(10, 2))
        self.v_last_result = tk.StringVar(value="---")
        self.lbl_last_result = tk.Label(pnl, textvariable=self.v_last_result,
                                        font=FONT_LARGE, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
        self.lbl_last_result.pack(fill=tk.X, padx=10, pady=3)
        
        self.v_match_info = tk.StringVar(value="")
        self.lbl_match_info = tk.Label(pnl, textvariable=self.v_match_info,
                                       font=FONT_NORMAL, bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB)
        self.lbl_match_info.pack(fill=tk.X, padx=10, pady=(0, 5))

        # NG履歴
        tk.Label(pnl, text="NG履歴", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(10, 2))
        hist_frm = tk.Frame(pnl, bg=COLOR_BG_PANEL)
        hist_frm.pack(fill=tk.BOTH, expand=True, padx=10)
        self.lb_history = tk.Listbox(
            hist_frm, font=(FONT_FAMILY, 14),
            bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
            selectbackground=COLOR_ACCENT, selectforeground="black",
            relief="flat")
        self.lb_history.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(hist_frm, orient=tk.VERTICAL, command=self.lb_history.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb_history.configure(yscrollcommand=sb.set)
        
        # NG履歴ダブルクリックで画像表示 (inspection_app準拠)
        self.lb_history.bind("<Double-1>", self._on_history_double_click)

        hist_btn_frm = tk.Frame(pnl, bg=COLOR_BG_PANEL)
        hist_btn_frm.pack(fill=tk.X, padx=10, pady=5)

        btn_clear_hist = tk.Button(hist_btn_frm, text="履歴リセット", font=FONT_NORMAL,
                  bg="#546E7A", fg="white", relief="flat",
                  command=self._clear_history)
        btn_clear_hist.pack(side=tk.LEFT, fill=tk.X, expand=True)
        Tooltip(btn_clear_hist, "画面に表示されているNG履歴リストを消去します")

        btn_open_results = tk.Button(hist_btn_frm, text="結果フォルダ", font=FONT_NORMAL,
                  bg="#546E7A", fg="white", relief="flat",
                  command=self._open_results_folder)
        btn_open_results.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        Tooltip(btn_open_results, "画像が保存されている結果フォルダを開きます")

        btn_buzzer_stop = tk.Button(pnl, text="ブザー停止", font=FONT_BOLD,
                  bg=COLOR_NG, fg="white", height=2, relief="flat",
                  command=self._stop_buzzer)
        btn_buzzer_stop.pack(fill=tk.X, padx=10, pady=(15, 5))
        Tooltip(btn_buzzer_stop, "NG時の信号出力を停止し、待機状態へ戻します")

        btn_goto_settings = tk.Button(pnl, text="詳細設定", font=FONT_BOLD,
                  bg="#455A64", fg="white", height=2, relief="flat",
                  command=self._open_settings)
        btn_goto_settings.pack(fill=tk.X, padx=10, pady=5)
        Tooltip(btn_goto_settings, "カメラ、GPIO、画像処理、保存設定などの詳細を変更します")

    # ------------------------------------------------------------------
    # モード切替
    # ------------------------------------------------------------------
    def _set_mode(self, mode):
        """検査/編集モードを切り替える"""
        self._current_mode = mode

        if mode == "inspection":
            self.editor_view.pack_forget()
            self.inspection_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_insp.config(bg=COLOR_ACCENT, fg="black")
            self.btn_edit.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
            self._update_status("検査モード 待機中", COLOR_BG_PANEL)
        else:
            self.editor_view.sync_settings()
            self.inspection_frame.pack_forget()
            self.editor_view.pack(fill=tk.BOTH, expand=True)
            self.btn_insp.config(bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
            self.btn_edit.config(bg=COLOR_WARNING, fg="black")
            self._update_status("編集モード", COLOR_BG_PANEL)

    def _update_mode_ui(self):
        self._set_mode(self._current_mode)

    # ------------------------------------------------------------------
    # ステータス表示
    # ------------------------------------------------------------------
    def _update_status(self, text, bg_color, fg_color=None):
        """ステータス表示とヘッダー色の更新 (inspection_app準拠)"""
        if fg_color is None:
            # bg_colorがデフォルトパネル色の場合はアクセントカラー、それ以外(OK/NG)は白か黒
            fg_color = COLOR_ACCENT if bg_color == COLOR_BG_PANEL else (
                "black" if bg_color in (COLOR_OK, COLOR_ACCENT) else "white")
        
        self.lbl_status.config(text=text, bg=bg_color, fg=fg_color)
        self.header.config(bg=bg_color)
        
        # ヘッダー内の全ウィジェットの背景を合わせる（ボタン以外）
        for w in self.header.winfo_children():
            try:
                # mode_frmなどのコンテナも含む全ラベル・フレームを同期
                if not isinstance(w, tk.Button):
                    w.config(bg=bg_color)
                    # 子要素（mode_frm内のラベル等）があれば再帰的に or 直接指定
                    for sub in w.winfo_children():
                        if not isinstance(sub, tk.Button):
                            sub.config(bg=bg_color)
            except Exception:
                pass

    def _update_clock(self):
        now = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        self.lbl_clock.config(text=now)
        self.root.after(1000, self._update_clock)

    def _schedule_status_reset_if_needed(self):
        """設定に応じて結果表示を一定時間後に待機状態へ戻す"""
        try:
            sec = float(self.cfg.get("inference", "result_display_time", default=5.0))
        except Exception:
            sec = 5.0
        if sec <= 0:
            return
        if self._status_reset_after_id is not None:
            try:
                self.root.after_cancel(self._status_reset_after_id)
            except Exception:
                pass
            self._status_reset_after_id = None

        def _reset():
            self._status_reset_after_id = None
            self._update_status("検査モード 待機中", COLOR_BG_PANEL)

        self._status_reset_after_id = self.root.after(int(sec * 1000), _reset)

    # ------------------------------------------------------------------
    # カメラプレビューループ
    # ------------------------------------------------------------------
    def _preview_loop(self):
        """バックグラウンドでカメラフレームを取得し、メインViewに反映"""
        while self.running:
            if self.preview_paused or self._current_mode != "inspection":
                time.sleep(0.05)
                continue
            if self.cap is None or not self.cap.isOpened():
                self.logger.warning("カメラ切断を検出。再接続を試みます...")
                cam_cfg = self.cfg.get("camera", default={})
                self.cap = InspectionEngine.open_camera(cam_cfg.get("index", 0), cam_cfg)
                if self.cap is None or not self.cap.isOpened():
                    time.sleep(3.0)
                continue
            try:
                with self.camera_lock:
                    # 取得と取り出しを分離してロック保持時間を短縮
                    # grab失敗時はそのフレームを諦めて次周回で再試行
                    if not self.cap.grab():
                        ret, frame = False, None
                    else:
                        ret, frame = self.cap.retrieve()
                if ret:
                    self.last_frame = frame.copy()
                    # 描画処理が追いついていない場合はスキップして最新化を優先
                    if not self.is_rendering:
                        self.is_rendering = True
                        self._render_preview(frame)
            except Exception as e:
                self.logger.error(f"Preview error: {e}")
            try:
                fps = float(self.cfg.get("inference", "preview_fps", default=2.0))
                fps = max(1.0, min(60.0, fps))
                interval = 1.0 / fps
            except Exception:
                interval = 1.0 / 2.0
            time.sleep(interval)

    def _render_preview(self, frame):
        """フレームを Canvas に描画 (設定解像度を尊重)"""
        dispatch_success = False
        try:
            cam_preview_res = self.cfg.get("camera", "preview_res", default="640x480")
            if cam_preview_res == "プレビューなし":
                return

            try:
                sw, sh = map(int, cam_preview_res.split('x'))
            except Exception:
                sw, sh = 640, 480

            # キャンバスの現在サイズを取得
            cw = self.preview_canvas.winfo_width()
            ch = self.preview_canvas.winfo_height()
            if cw < 2 or ch < 2:
                cw, ch = 640, 480

            # 設定解像度とキャンバス解像度の小さい方を上限とする
            tw = min(cw, sw)
            th = min(ch, sh)

            # 元の縦横比を維持してフィットさせる計算
            h_orig, w_orig = frame.shape[:2]
            scale = min(tw / w_orig, th / h_orig)
            nw = int(w_orig * scale)
            nh = int(h_orig * scale)

            if nw < 1 or nh < 1:
                self.is_rendering = False
                return

            # リサイズ (設定解像度に合わせて縮小)
            resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_NEAREST)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)

            def _update(img=pil_img):
                try:
                    tk_img = ImageTk.PhotoImage(img)
                    # 前の画像を削除してメモリリーク防止
                    self.preview_canvas.delete("all")
                    self.preview_canvas.create_image(
                        cw // 2, ch // 2, anchor=tk.CENTER, image=tk_img)
                    self.preview_canvas.image = tk_img
                except Exception:
                    pass
                finally:
                    # 描画完了を通知
                    self.is_rendering = False

            self.root.after(0, _update)
        except Exception as e:
            self.logger.error(f"Render preview error: {e}")
            self.is_rendering = False

    def _get_expected_spec(self):
        """GPIO入力状態から期待するパターン名（＝フォルダ名）を取得する"""
        patterns = self.cfg.get("patterns", default={})
        pat_order = self.cfg.get("pattern_order", default=[])
        if not pat_order:
            return "なし"
        
        # 現在のピン状態を取得 (configの定義順)
        pat_pins = self.cfg.get("gpio", "pattern_pins", default=[])
        current_state = []
        for p in pat_pins:
            dev = self.pattern_inputs.get(p["id"])
            current_state.append(1 if (dev and dev.is_active) else 0)
        
        for pid in pat_order:
            p_data = patterns.get(pid, {})
            cond = p_data.get("pin_condition", [])
            # 全てのピン条件が一致するものを探す
            if len(cond) == len(current_state):
                if all(c == s for c, s in zip(cond, current_state)):
                    # パターン名を返す（＝フォルダ名として扱われる）
                    return p_data.get("name", pid)
        
        return "不一致"

    def _sync_expected_spec_display(self, initial=False):
        """期待仕様ラベルを現在状態に同期（初期時の不一致表示を抑制）"""
        spec = self._get_expected_spec()
        if (initial or not self._spec_initialized) and spec == "不一致":
            self.v_spec_id.set("期待: --")
        else:
            self.v_spec_id.set(f"期待: {spec}")
            self._spec_initialized = True

    # ------------------------------------------------------------------
    # 検査ロジックループ
    # ------------------------------------------------------------------
    def _inspection_loop(self):
        """GPIOトリガー待機 → 検査実行ループ"""
        while self.running:
            try:
                self.trigger_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not self.engine or not self.template_paths:
                self.logger.warning("エンジンまたはテンプレート未初期化")
                continue

            frame = self.last_frame
            if frame is None:
                self.logger.warning("フレームなし")
                continue

            self.root.after(0, lambda: self._update_status("検査中...", COLOR_ACCENT))

            inference_cfg = self.cfg.get("inference", default={})
            max_retries = int(inference_cfg.get("max_retries", 0))
            retry_interval = float(inference_cfg.get("burst_interval", 0.2))
            retry_interval = max(0.0, retry_interval)

            result = None
            try:
                for attempt in range(max_retries + 1):
                    frame = self.last_frame
                    if frame is None:
                        break
                    result = self.engine.run(frame, self.template_paths, self.template_images)
                    if result is not None:
                        break
                    if attempt < max_retries and retry_interval > 0:
                        time.sleep(retry_interval)
            except Exception as e:
                self.logger.error(f"検査エラー: {e}")
                self.root.after(0, lambda: self._update_status("エラー", COLOR_NG))
                continue
            finally:
                # チャタリング等で滞留した古いトリガーは破棄し、最新サイクルを優先
                while True:
                    try:
                        self.trigger_queue.get_nowait()
                    except queue.Empty:
                        break

            now_full = datetime.datetime.now().strftime("%m/%d %H:%M")
            now_time = datetime.datetime.now().strftime("%H:%M:%S") # ログ保存用
            spec_map = self.cfg.data.get("specification_mapping", {})
            current_spec = self._get_expected_spec()
            
            # UI表示用に同期（既にタイマーで更新されているが、判定直後の確実な反映のため）
            self.root.after(0, lambda cs=current_spec: self.v_spec_id.set(f"期待: {cs}"))

            if result is not None:
                # 一致した仕様IDが期待通りか判定
                if result == current_spec:
                    label = f"OK {spec_map.get(result, {}).get('name', result)}"
                    self.root.after(0, lambda l=label: [
                        self._update_status(f"OK  {l}", COLOR_OK),
                        self.v_last_result.set(f"✓ {l}"),
                        self.lbl_last_result.config(fg=COLOR_OK),
                        self.v_match_info.set(f"一致: {self.engine.last_matched_file} (スコア: {self.engine.last_score:.2f})") if self.engine.last_matched_file else self.v_match_info.set("")
                    ])
                    self.engine.save_log("OK", result)
                    self._save_csv_log("OK", result, f"Score: {self.engine.last_score:.2f}")
                    self.engine.save_image(frame, "OK", config_manager=self.cfg)
                    self._schedule_status_reset_if_needed()
                    if self.out_ok:
                        ok_t = float(self.cfg.get("inference", "ok_output_time", default=0.3))
                        ok_t = max(0.0, ok_t)
                        self.out_ok.on()
                        time.sleep(ok_t)
                        self.out_ok.off()
                else:
                    label = f"NG 期待:{current_spec} 検出:{result}"
                    self._handle_ng(label, frame, now_full)
            else:
                label = f"NG (未検出)"
                self._handle_ng(label, frame, now_full)

    def _handle_ng(self, label, frame, time_str):
        """NGの際の処理（UI更新・信号出力・履歴追加）"""
        score_info = f"最高スコア: {self.engine.last_score:.2f}"
        # 履歴表示用に文言を短縮 (E:期待, D:検出, Sc:スコア)
        short_label = label.replace("期待:", "E:").replace("検出:", "D:").replace(" (未検出)", "(-)")
        lb_text = f"[{time_str}] {short_label} Sc:{self.engine.last_score:.2f}"
        
        self.root.after(0, lambda l=label, info=score_info, lt=lb_text: [
            self._update_status(f"NG  {l}", COLOR_NG),
            self.v_last_result.set(f"✗ {l}"),
            self.lbl_last_result.config(fg=COLOR_NG),
            self.v_match_info.set(info),
            self.lb_history.insert(0, lt)
        ])
        self.engine.save_log("NG", label)
        self._save_csv_log("NG", label, score_info)
        path = self.engine.save_image(frame, "NG", config_manager=self.cfg)
        self.ng_history.insert(0, {"label": label, "time": time_str, "img_path": path})
        self._schedule_status_reset_if_needed()
        if self.out_ng:
            self.out_ng.on()
            ng_raw = self.cfg.get("inference", "ng_output_time", default="")
            if ng_raw in ("", None):
                ng_t = 0.0
            else:
                try:
                    ng_t = float(ng_raw)
                except Exception:
                    ng_t = 0.0
            if ng_t > 0:
                threading.Thread(target=self._auto_off_ng_output, args=(ng_t,), daemon=True).start()

    def _auto_off_ng_output(self, seconds):
        """指定秒後にNG出力を自動OFF"""
        try:
            time.sleep(max(0.0, float(seconds)))
            if self.out_ng:
                self.out_ng.off()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 操作パネルのアクション
    # ------------------------------------------------------------------
    def _stop_buzzer(self):
        if self.out_ng:
            self.out_ng.off()
        self._update_status("検査モード 待機中", COLOR_BG_PANEL)

    def _save_csv_log(self, result_type, detail_primary, detail_secondary):
        """CSV形式で検査履歴を書き出す"""
        try:
            import csv
            base = self.cfg.get("storage", "results_dir", default="./results")
            csv_dir = Path(base) / "csv"
            csv_dir.mkdir(parents=True, exist_ok=True)
            
            now = datetime.datetime.now()
            fpath = csv_dir / f"app_{now.strftime('%Y%m%d')}.csv"
            
            file_exists = fpath.exists()
            with open(fpath, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Result", "PrimaryDetail", "SecondaryDetail"])
                writer.writerow([now.strftime('%Y-%m-%d %H:%M:%S'), result_type, detail_primary, detail_secondary])
        except Exception as e:
            self.logger.error(f"CSVログ保存エラー: {e}")

    def _on_history_double_click(self, event):
        """履歴ダブルクリックで保存されたNG画像をポップアップ表示"""
        sel = self.lb_history.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.ng_history):
            hist_item = self.ng_history[idx]
            img_path = hist_item.get("img_path")
            if img_path and os.path.exists(img_path):
                self._show_ng_image(img_path, hist_item["time"], hist_item["label"])

    def _show_ng_image(self, path, time_str, label_text):
        """NG画像のビューワー表示 (inspection_app準拠の縦スクロール形式)"""
        top = tk.Toplevel(self.root)
        top.title(f"NG画像確認 - {time_str}")
        top.configure(bg=COLOR_BG_MAIN)
        top.transient(self.root)
        
        # ウィンドウサイズを画面の85%に設定
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = int(sw * 0.85)
        win_h = int(sh * 0.85)
        top.geometry(f"{win_w}x{win_h}")

        lbl_info = tk.Label(top, text=f"{time_str} / {label_text}", font=FONT_LARGE, bg=COLOR_BG_MAIN, fg=COLOR_NG)
        lbl_info.pack(pady=10)
        
        # Scrollable area
        frame_outer = tk.Frame(top, bg=COLOR_BG_MAIN)
        frame_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(frame_outer, bg=COLOR_BG_MAIN, highlightthickness=0)
        scrollbar = tk.Scrollbar(frame_outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        inner = tk.Frame(canvas, bg=COLOR_BG_MAIN)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        
        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner_configure)

        # マウスホイールでスクロール
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        top.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        try:
            pil_img = Image.open(path)
            # Resize based on window width
            img_max_w = int(win_w * 0.82)
            img_max_h = int(sh * 0.65)
            pil_img.thumbnail((img_max_w, img_max_h), Image.Resampling.LANCZOS)
            
            # Filename label
            fname = os.path.basename(path)
            tk.Label(inner, text=fname, font=FONT_NORMAL, bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB).pack(anchor="w", padx=10, pady=(10, 2))
            
            tk_img = ImageTk.PhotoImage(pil_img)
            lbl_img = tk.Label(inner, image=tk_img, bg=COLOR_BG_MAIN)
            lbl_img.image = tk_img # prevent GC
            lbl_img.pack(padx=10, pady=(0, 5))
        except Exception as e:
            tk.Label(inner, text=f"画像読み込みエラー:\n{e}", bg=COLOR_BG_MAIN, fg=COLOR_NG).pack()

        # 閉じるボタン
        tk.Button(top, text="閉じる", font=FONT_BOLD, bg="#546E7A", fg="white",
                  relief="flat", padx=20,
                  command=top.destroy).pack(pady=10)

    def _clear_history(self):
        if messagebox.askyesno("確認", "NG履歴を削除しますか？", parent=self.root):
            self.ng_history.clear()
            self.lb_history.delete(0, tk.END)

    def _open_results_folder(self):
        """結果画像フォルダをOSのファイルマネージャーで開く"""
        base = self.cfg.get("storage", "results_dir", default="./results")
        folder = Path(base) / "images"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "linux":
                import subprocess
                subprocess.Popen(["xdg-open", str(folder)])
            else:
                import subprocess
                subprocess.Popen(["open", str(folder)])
        except Exception as e:
            self.logger.error(f"フォルダを開けませんでした: {e}")
            messagebox.showerror("エラー", f"フォルダを開けませんでした:\n{folder}", parent=self.root)

    def _open_settings(self):
        """設定ダイアログを開く"""
        self.preview_paused = True
        self._update_status("設定変更中", COLOR_WARNING, "black")
        self._close_hardware()
        SettingsDialog(self.root, self.cfg, self._on_settings_closed)

    def _on_settings_closed(self):
        """設定保存後の再初期化"""
        self.preview_paused = False
        self._update_status("検査モード 待機中", COLOR_BG_PANEL)
        self.editor_view.sync_settings()
        threading.Thread(target=self._setup_hardware, daemon=True).start()
        self._sync_expected_spec_display(initial=True)

    def _show_help(self):
        help_data = {
            "概要": "テンプレートマッチングを用いた部品・外観検査システムです。\n\n【基本的な流れ】\n1. 設定画面でカメラや判定条件（ROI/二値化など）を整える。\n2. パターン設定で、GPIO入力ピンとマスター画像の対応を紐付ける。\n3. トリガー待ち状態になります。外部信号または仮想ボタンで撮影が行われます。",
            "検査モード": "自動判定を行う通常モードです。\n・現在のGPIOピンの状態から「期待されるパターン」を特定し、撮影した画像とマスター画像を照合します。\n・一致スコアがしきい値を超えればOK信号、下回ればNG信号を出力します。\n・判定結果は指定フォルダに画像保存されます。",
            "編集モード": "検査に使用するマスター画像（テンプレート）を登録・編集するモードです。\n・現在のカメラ映像をキャプチャし、必要な部分を切り抜いて登録します。\n・射影変換（歪み補正）の基準となる四角形枠（ROI）の設定もここで行います。\n・マスター登録時には、パターン設定で使用する「名称」と同じフォルダ名で保存してください。",
            "期待仕様": "メイン画面上部に表示されます。\n・GPIO入力（または仮想GPIOパネルのチェック）によって、リアルタイムに変化します。\n・「不一致」と表示される場合は、現在のピンの組み合わせがパターン設定に登録されていません。",
            "NG履歴": "最近のNG判定がリスト表示されます。\n・期待したパターン名と、実際に検出されたパターン名、および最高スコアが表示されます。\n・【ダブルクリック】で保存された画像を確認できます。"
        }
        HelpWindow(self.root, "操作ヘルプ", help_data)

    # ------------------------------------------------------------------
    # 容量監視
    # ------------------------------------------------------------------
    def _monitor_storage(self):
        """保存フォルダの容量を確認し、上限超過時に古いファイルを削除する (10分おき・非同期)"""
        _INTERVAL_MS = 10 * 60 * 1000  # 10分
        
        def _thread_task():
            try:
                st = self.cfg.get("storage", default={})
                if not st.get("auto_delete_enabled", False):
                    return

                max_gb = float(st.get("max_results_gb", 30))
                if max_gb <= 0:
                    return

                res_dir = Path(self.cfg.get("storage", "results_dir", default="./results"))
                images_dir = res_dir / "images"
                debug_dir = res_dir / "debug"
                logs_dir = res_dir / "logs"
                
                # 画像ファイルを更新日時昇順（古い順）でリストアップ
                files_to_check = []
                for d in [images_dir, debug_dir]:
                    if d.exists():
                        files_to_check.extend([
                            f for f in d.rglob("*")
                            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
                        ])
                
                if not files_to_check:
                    return

                files_to_check.sort(key=lambda f: f.stat().st_mtime)
                total_size = sum(f.stat().st_size for f in files_to_check)

                import shutil
                usage = shutil.disk_usage(res_dir)
                free_gb = usage.free / (1024 ** 3)
                max_bytes = max_gb * (1024 ** 3)

                needs_deletion = False
                target_bytes = total_size

                if total_size > max_bytes:
                    needs_deletion = True
                    target_bytes = max_bytes * 0.9  # 上限の90%まで減らす
                elif free_gb < 1.0:
                    needs_deletion = True
                    target_bytes = max(0, total_size - (1.0 * 1024**3))

                if not needs_deletion:
                    return

                self.logger.info(f"[容量監視] 削除開始。現在サイズ: {total_size/(1024**3):.2f} GB / 空き: {free_gb:.2f} GB")

                deleted_count = 0
                for f in files_to_check:
                    if total_size <= target_bytes:
                        break
                    try:
                        file_size = f.stat().st_size
                        f.unlink()
                        total_size -= file_size
                        deleted_count += 1
                    except Exception as e:
                        self.logger.warning(f"[容量監視] 削除失敗: {f.name} - {e}")

                if deleted_count > 0:
                    self.logger.info(f"[容量監視] {deleted_count} 件削除完了。残サイズ: {total_size/(1024**3):.2f} GB")

                # ログファイルは30日超過分を削除
                if logs_dir.exists():
                    now_ts = time.time()
                    for log_f in logs_dir.glob("*.log"):
                        if log_f.is_file() and now_ts - log_f.stat().st_mtime > 2592000:
                            try:
                                log_f.unlink()
                            except Exception:
                                pass
            except Exception as e:
                self.logger.error(f"[容量監視] エラー: {e}")
            finally:
                if self.running:
                    self.root.after(_INTERVAL_MS, self._monitor_storage)

        threading.Thread(target=_thread_task, daemon=True).start()

    # ------------------------------------------------------------------
    # 仮想GPIOパネル（Windows デバッグ用）
    # ------------------------------------------------------------------
    def _setup_mock_ui(self):
        try:
            self.mock_root = tk.Toplevel(self.root)
            self.mock_root.title("仮想GPIOパネル")
            self.mock_root.geometry("360x650")
            self.mock_root.configure(bg=COLOR_BG_MAIN)
            self.mock_root.attributes("-topmost", True)
            self.mock_root.resizable(False, False)

            container = tk.Frame(self.mock_root, bg=COLOR_BG_MAIN, padx=15, pady=15)
            container.pack(fill=tk.BOTH, expand=True)

            trig_outer, trig_inner = create_card(container, "仮想入力")
            trig_outer.pack(fill=tk.X, pady=(0, 12))

            # 最初のトリガー
            triggers = self.cfg.get("gpio", "triggers", default=[])
            start_pin = triggers[0]["pin"] if triggers else -1
            btn = tk.Button(
                trig_inner,
                text=f"撮影開始 (ピン {start_pin})",
                font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                activebackground=COLOR_ACCENT, activeforeground="black",
                relief="flat", cursor="hand2",
                command=lambda: self._pulse_mock_input(start_pin))
            btn.pack(fill=tk.X, pady=4)
            Tooltip(btn, "ボタンを押した瞬間だけ入力がONになります")

            # パターン入力ピン
            self.mock_selectors = {}
            pat_pins = self.cfg.get("gpio", "pattern_pins", default=[])
            if pat_pins:
                pat_outer, pat_inner = create_card(container, "仮想入力 (パターン切替)")
                pat_outer.pack(fill=tk.X, pady=(0, 12))
                for p in pat_pins:
                    pin = p.get("pin", 0)
                    if pin <= 0: continue
                    f = tk.Frame(pat_inner, bg=COLOR_BG_PANEL)
                    f.pack(fill=tk.X, pady=2)
                    var = tk.BooleanVar(value=MockManager.get_input_state(pin))
                    cb = tk.Checkbutton(f, text=f"{p['name']} (ピン {pin})",
                                        font=FONT_NORMAL, variable=var,
                                        bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                                        selectcolor=COLOR_BG_INPUT, activebackground=COLOR_BG_PANEL,
                                        activeforeground=COLOR_TEXT_MAIN, relief="flat",
                                        command=lambda pn=pin, v=var: MockManager.set_input(pn, v.get()))
                    cb.pack(side=tk.LEFT)
                    self.mock_selectors[str(pin)] = var

            out_outer, out_inner = create_card(container, "仮想出力状態")
            out_outer.pack(fill=tk.X)

            self.mock_indicators = {}
            outs = self.cfg.get("gpio", "outputs", default={})
            for name, key, color in [
                ("OK出力", "ok", COLOR_OK),
                ("NG出力", "ng", COLOR_NG),
            ]:
                f = tk.Frame(out_inner, bg=COLOR_BG_PANEL)
                f.pack(fill=tk.X, pady=6)
                tk.Label(f, text=name, font=FONT_NORMAL,
                         bg=COLOR_BG_PANEL, fg=COLOR_TEXT_MAIN,
                         width=22, anchor="w").pack(side=tk.LEFT)
                
                # Canvas round LED
                led = tk.Canvas(f, width=16, height=16, bg=COLOR_BG_PANEL, highlightthickness=0)
                led.pack(side=tk.RIGHT, padx=5)
                circle = led.create_oval(2, 2, 14, 14, fill="#333", outline="#555")
                
                pin = outs.get(key, -1)
                self.mock_indicators[str(pin)] = (led, circle, color)

            tk.Label(container, text="※Windowsデバッグ専用機能",
                     font=(FONT_FAMILY, 9), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB).pack(pady=10)
            self._update_mock_ui()
        except Exception as e:
            self.logger.error(f"仮想GPIOパネルエラー: {e}")

    def _pulse_mock_input(self, pin):
        """仮想入力を一瞬ONにする"""
        if pin <= 0: return
        def _pulse():
            MockManager.set_input(pin, True)
            time.sleep(0.15)
            MockManager.set_input(pin, False)
        threading.Thread(target=_pulse, daemon=True).start()

    def _update_mock_ui(self):
        try:
            if not hasattr(self, "mock_root") or not self.mock_root.winfo_exists():
                return
            
            # 出力 (LED) 更新
            for pin, (led, circle, color) in self.mock_indicators.items():
                state = MockManager.get_output_state(pin)
                led.itemconfig(circle, fill=color if state else "#333")
            
            # 入力 (パターン) 同期: 外部（MockManager）の変化を UI に反映
            # ただし、UI側で Checkbutton を操作した直後に上書きされないよう、
            # 状態が異なるときのみ適用する
            if hasattr(self, "mock_selectors"):
                for pin_str, var in self.mock_selectors.items():
                    current = MockManager.get_input_state(pin_str)
                    if var.get() != current:
                        var.set(current)

            # メインUIの「期待仕様」ラベルをリアルタイム同期更新
            current_spec = self._get_expected_spec()
            self._sync_expected_spec_display(initial=not self._spec_initialized)

            self.mock_root.after(200, self._update_mock_ui)
        except Exception as e:
            self.logger.error(f"仮想GPIOパネル更新エラー: {e}")

    # ------------------------------------------------------------------
    # 終了
    # ------------------------------------------------------------------
    def _on_closing(self):
        if messagebox.askokcancel("終了", "アプリケーションを終了しますか？",
                                  parent=self.root):
            self.running = False
            self._close_hardware()
            if hasattr(self, "mock_root"):
                try:
                    self.mock_root.destroy()
                except Exception:
                    pass
            self.root.destroy()
            self.logger.info("シャットダウン完了")

    def run(self):
        self.root.mainloop()
