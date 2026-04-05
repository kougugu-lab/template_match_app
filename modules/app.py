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

    def _setup_hardware(self):
        """GPIO・カメラ・テンプレート読み込み"""
        self._close_hardware()
        try:
            # GPIO
            gpio = self.cfg.data.get("gpio_pins", {})
            start_pin = gpio.get("pin_Start", -1)
            if start_pin > 0:
                dev = DigitalInputDevice(start_pin, pull_up=True)
                dev.when_activated = self._on_trigger
                self.inputs["start"] = dev

            ok_pin = gpio.get("pin_OKlog", -1)
            ng_pin = gpio.get("pin_NGlog", -1)
            if ok_pin > 0:
                self.out_ok = OutputDevice(ok_pin)
            if ng_pin > 0:
                self.out_ng = OutputDevice(ng_pin)

            # カメラ
            self.cap = InspectionEngine.open_camera(self.cfg)
            if not self.cap.isOpened():
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

        # 現在仕様
        tk.Label(pnl, text="現在仕様", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(5, 2))
        spec_frm = tk.Frame(pnl, bg=COLOR_BG_INPUT)
        spec_frm.pack(fill=tk.X, padx=10, pady=3)
        self.v_car_model = tk.StringVar(
            value=self.cfg.get("current_spec", "car_model", default="default"))
        self.v_spec_id = tk.StringVar(
            value=self.cfg.get("current_spec", "specification", default="1"))
        tk.Label(spec_frm, text="車種:", font=FONT_NORMAL,
                 bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT, padx=8)
        self.lbl_car_model = tk.Label(spec_frm, textvariable=self.v_car_model,
                                      font=FONT_BOLD, bg=COLOR_BG_INPUT, fg=COLOR_ACCENT)
        self.lbl_car_model.pack(side=tk.LEFT)
        tk.Label(spec_frm, text="  仕様:", font=FONT_NORMAL,
                 bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB).pack(side=tk.LEFT)
        self.lbl_spec = tk.Label(spec_frm, textvariable=self.v_spec_id,
                                 font=FONT_BOLD, bg=COLOR_BG_INPUT, fg=COLOR_ACCENT)
        self.lbl_spec.pack(side=tk.LEFT, padx=3)

        # 最終判定
        tk.Label(pnl, text="最終判定", font=FONT_BOLD,
                 bg=COLOR_BG_PANEL, fg=COLOR_TEXT_SUB).pack(pady=(10, 2))
        self.v_last_result = tk.StringVar(value="---")
        self.lbl_last_result = tk.Label(pnl, textvariable=self.v_last_result,
                                        font=FONT_LARGE, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_SUB)
        self.lbl_last_result.pack(fill=tk.X, padx=10, pady=3)

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

        btn_clear_hist = tk.Button(pnl, text="履歴リセット", font=FONT_NORMAL,
                  bg="#546E7A", fg="white", relief="flat",
                  command=self._clear_history)
        btn_clear_hist.pack(fill=tk.X, padx=10, pady=5)
        Tooltip(btn_clear_hist, "画面に表示されているNG履歴リストを消去します")

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
        if fg_color is None:
            fg_color = COLOR_ACCENT if bg_color == COLOR_BG_PANEL else (
                "black" if bg_color in (COLOR_OK, COLOR_ACCENT) else "white")
        self.lbl_status.config(text=text, bg=bg_color, fg=fg_color)
        self.header.config(bg=bg_color)
        self.lbl_clock.config(bg=bg_color)

    def _update_clock(self):
        now = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        self.lbl_clock.config(text=now)
        self.root.after(1000, self._update_clock)

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
                self.cap = InspectionEngine.open_camera(self.cfg)
                if not self.cap.isOpened():
                    time.sleep(3.0)
                continue
            try:
                with self.camera_lock:
                    ret, frame = self.cap.read()
                if ret:
                    self.last_frame = frame.copy()
                    self._render_preview(frame)
            except Exception as e:
                self.logger.error(f"Preview error: {e}")
            time.sleep(0.033)  # ~30fps

    def _render_preview(self, frame):
        """フレームを Canvas に描画"""
        try:
            cam_preview_res = self.cfg.get("camera", "preview_res", default="640x480")
            if cam_preview_res == "プレビューなし":
                return
            try:
                pw, ph = map(int, cam_preview_res.split("x"))
            except Exception:
                pw, ph = 640, 480

            resized = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_NEAREST)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)

            def _update(img=pil_img):
                try:
                    cw = self.preview_canvas.winfo_width()
                    ch = self.preview_canvas.winfo_height()
                    if cw < 2 or ch < 2:
                        return
                    scale = min(cw / img.width, ch / img.height)
                    nw = int(img.width * scale)
                    nh = int(img.height * scale)
                    disp = img.resize((nw, nh), Image.Resampling.NEAREST)
                    tk_img = ImageTk.PhotoImage(disp)
                    self.preview_canvas.create_image(
                        cw // 2, ch // 2, anchor=tk.CENTER, image=tk_img)
                    self.preview_canvas.image = tk_img
                except Exception:
                    pass

            self.root.after(0, _update)
        except Exception as e:
            self.logger.error(f"Render preview error: {e}")

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

            try:
                result = self.engine.run(frame, self.template_paths, self.template_images)
            except Exception as e:
                self.logger.error(f"検査エラー: {e}")
                self.root.after(0, lambda: self._update_status("エラー", COLOR_NG))
                continue

            now = datetime.datetime.now().strftime("%H:%M:%S")
            spec_map = self.cfg.data.get("specification_mapping", {})
            current_spec = self.cfg.get("current_spec", "specification", default="1")

            if result is not None:
                # 一致した仕様IDが期待通りか判定
                if result == current_spec:
                    label = f"OK {spec_map.get(result, {}).get('name', result)}"
                    self.root.after(0, lambda l=label: [
                        self._update_status(f"OK  {l}", COLOR_OK),
                        self.v_last_result.set(f"✓ {l}"),
                        self.lbl_last_result.config(fg=COLOR_OK)
                    ])
                    self.engine.save_log("OK", result)
                    self.engine.save_image(frame, "OK", config_manager=self.cfg)
                    if self.out_ok:
                        self.out_ok.on()
                        time.sleep(0.3)
                        self.out_ok.off()
                else:
                    label = f"NG 期待:{current_spec} 検出:{result}"
                    self._handle_ng(label, frame, now)
            else:
                label = f"NG (未検出)"
                self._handle_ng(label, frame, now)

    def _handle_ng(self, label, frame, time_str):
        """NGの際の処理（UI更新・信号出力・履歴追加）"""
        self.root.after(0, lambda l=label: [
            self._update_status(f"NG  {l}", COLOR_NG),
            self.v_last_result.set(f"✗ {l}"),
            self.lbl_last_result.config(fg=COLOR_NG),
            self.lb_history.insert(0, f"{time_str}  {l}")
        ])
        self.ng_history.append({"label": label, "time": time_str})
        self.engine.save_log("NG", label)
        self.engine.save_image(frame, "NG", config_manager=self.cfg)
        if self.out_ng:
            self.out_ng.on()

    # ------------------------------------------------------------------
    # 操作パネルのアクション
    # ------------------------------------------------------------------
    def _stop_buzzer(self):
        if self.out_ng:
            self.out_ng.off()
        self._update_status("検査モード 待機中", COLOR_BG_PANEL)

    def _clear_history(self):
        if messagebox.askyesno("確認", "NG履歴を削除しますか？", parent=self.root):
            self.ng_history.clear()
            self.lb_history.delete(0, tk.END)

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
        self._setup_hardware()

    def _show_help(self):
        HelpWindow(self.root, "操作ヘルプ", {
            "基本操作": (
                "1. 詳細設定でカメラ・GPIO・画像処理パラメータを設定します。\n"
                "2. 検査モードでトリガー入力待ち状態になります。\n"
                "3. トリガー信号が入ると撮影→テンプレートマッチング→OK/NG判定が実行されます。\n"
                "4. 結果はresults/ フォルダに保存されます。"
            ),
            "検査モード": (
                "GPIO の pin_Start にトリガー信号が入ると検査が開始されます。\n"
                "結果はカメラプレビューの下部と操作パネルのステータスに表示されます。\n"
                "NG時は out_NGlog ピンがONになります。「ブザー停止」で解除してください。"
            ),
            "編集モード": (
                "マスター画像の作成・射影変換・フィルタ調整ができます。\n"
                "「データ拡張を実行」でマスター画像からバリエーションを大量生成できます。\n"
                "生成後は検査モードに戻り、詳細設定を保存することでテンプレートが更新されます。"
            ),
            "詳細設定": (
                "「調整」タブでリアルタイムのカメラプレビューを見ながら\n"
                "二値化・マスク・射影変換のパラメータを調整できます。\n"
                "変更後は「保存して閉じる」を押してください。"
            )
        })

    # ------------------------------------------------------------------
    # 容量監視
    # ------------------------------------------------------------------
    def _monitor_storage(self):
        _INTERVAL = 10 * 60 * 1000
        try:
            stor = self.cfg.data.get("storage", {})
            if not stor.get("auto_delete_enabled", False):
                self.root.after(_INTERVAL, self._monitor_storage)
                return
            max_gb = float(stor.get("max_results_gb", 0))
            if max_gb <= 0:
                self.root.after(_INTERVAL, self._monitor_storage)
                return
            import shutil
            from pathlib import Path
            res_dir = Path(stor.get("results_dir", "./results"))
            img_dir = res_dir / "images"
            if not img_dir.exists():
                self.root.after(_INTERVAL, self._monitor_storage)
                return
            files = sorted(
                [f for f in img_dir.rglob("*")
                 if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")],
                key=lambda f: f.stat().st_mtime
            )
            total = sum(f.stat().st_size for f in files)
            max_bytes = max_gb * 1024 ** 3
            if total > max_bytes:
                target = max_bytes * 0.9
                for f in files:
                    if total <= target:
                        break
                    sz = f.stat().st_size
                    f.unlink()
                    total -= sz
                    self.logger.info(f"[容量監視] 削除(画像): {f.name}")

            # ログファイルの監視（30日以上経過したものを削除）
            log_dir = res_dir / "logs"
            if log_dir.exists():
                now_ts = time.time()
                for log_f in log_dir.glob("*.log"):
                    if log_f.is_file():
                        # 30 days = 30 * 24 * 3600 seconds = 2592000
                        if now_ts - log_f.stat().st_mtime > 2592000:
                            log_f.unlink()
                            self.logger.info(f"[容量監視] 削除(旧ログ): {log_f.name}")

        except Exception as e:
            self.logger.error(f"[容量監視] エラー: {e}")
        finally:
            self.root.after(_INTERVAL, self._monitor_storage)

    # ------------------------------------------------------------------
    # 仮想GPIOパネル（Windows デバッグ用）
    # ------------------------------------------------------------------
    def _setup_mock_ui(self):
        try:
            self.mock_root = tk.Toplevel(self.root)
            self.mock_root.title("仮想GPIOパネル")
            self.mock_root.geometry("360x500")
            self.mock_root.configure(bg=COLOR_BG_MAIN)
            self.mock_root.attributes("-topmost", True)
            self.mock_root.resizable(False, False)

            container = tk.Frame(self.mock_root, bg=COLOR_BG_MAIN, padx=15, pady=15)
            container.pack(fill=tk.BOTH, expand=True)

            trig_outer, trig_inner = create_card(container, "仮想入力")
            trig_outer.pack(fill=tk.X, pady=(0, 12))

            btn = tk.Button(
                trig_inner,
                text="撮影開始",
                font=FONT_NORMAL, bg=COLOR_BG_INPUT, fg=COLOR_TEXT_MAIN,
                activebackground=COLOR_ACCENT, activeforeground="black",
                relief="flat", cursor="hand2",
                command=lambda: self._on_trigger())
            btn.pack(fill=tk.X, pady=4)
            Tooltip(btn, "クリックで撮影トリガーを送信します")

            out_outer, out_inner = create_card(container, "仮想出力状態")
            out_outer.pack(fill=tk.X)

            self.mock_indicators = {}
            for name, key, color in [
                ("OK出力", "pin_OKlog", COLOR_OK),
                ("NG出力", "pin_NGlog", COLOR_NG),
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
                
                pin = self.cfg.get("gpio_pins", key, default=-1)
                self.mock_indicators[str(pin)] = (led, circle, color)

            tk.Label(container, text="※Windowsデバッグ専用機能",
                     font=(FONT_FAMILY, 9), bg=COLOR_BG_MAIN, fg=COLOR_TEXT_SUB).pack(pady=10)

            self._update_mock_ui()
        except Exception as e:
            self.logger.error(f"仮想GPIOパネルエラー: {e}")

    def _update_mock_ui(self):
        try:
            if not hasattr(self, "mock_root") or not self.mock_root.winfo_exists():
                return
            for pin, (led, circle, color) in self.mock_indicators.items():
                state = MockManager.get_output_state(pin)
                led.itemconfig(circle, fill=color if state else "#333")
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
