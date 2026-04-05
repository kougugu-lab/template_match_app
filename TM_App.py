#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TM_App.py - テンプレートマッチング検査統合アプリ エントリーポイント

統合対象:
  - TM3.py              (検査エンジン・GPIOコントロール)
  - realtime_camera_ver7.py  (カメラプレビュー・調整UI)
  - image_editor_gui_ver3.py (マスター画像エディタ)
  - maseter_image.py    (データ拡張バッチ処理)

UI依拠: common_ui_requirements.md (Dark Gray Theme)
"""

import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.app import TMApp


def main():
    app = TMApp()
    app.run()


if __name__ == "__main__":
    main()
    