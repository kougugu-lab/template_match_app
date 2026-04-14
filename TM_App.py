#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TM_App.py - テンプレートマッチング検査統合アプリ エントリーポイント

UI依拠: common_ui_requirements.md (Dark Gray Theme)
"""

import sys
import os

# WindowsでのOpenCVカメラ読み込みエラー(MSMF警告等)を抑制
os.environ["OPENCV_VIDEOIO_PRIORITY_MSMF"] = "0"
os.environ["OPENCV_LOG_LEVEL"] = "OFF"

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.app import TMApp


def main():
    app = TMApp()

    app.run()


if __name__ == "__main__":
    main()
    