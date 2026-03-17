@echo off
chcp 65001 > nul
cd /d %~dp0

echo ========================================
echo   boto3 インストール
echo ========================================
echo.

vscenv\Scripts\pip.exe install boto3 --upgrade

echo.
echo ✅ 完了
pause
