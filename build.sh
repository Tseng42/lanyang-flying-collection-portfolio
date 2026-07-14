#!/usr/bin/env bash
# Render 建置指令：安裝相依、收集靜態檔、套用資料庫遷移。
set -o errexit   # 任一步驟失敗就中止建置

pip install -r requirements.txt

# 收集靜態檔（WhiteNoise 於正式環境提供，CSS/JS 才正常顯示）
python manage.py collectstatic --no-input

# 套用資料庫遷移（同時會自動建立/校正權限群組）
python manage.py migrate
