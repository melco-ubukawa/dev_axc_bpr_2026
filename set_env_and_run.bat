@echo off
chcp 65001 > nul
echo ========================================
echo   技術文書自動生成ツール 起動準備
echo ========================================
echo.

cd /d %~dp0

REM ========================================
REM 環境変数の設定
REM ========================================
echo [1] 環境変数を設定しています...
echo.

REM VSCode設定ファイルから読み込み
set CLAUDE_API_PROVIDER=bedrock
set AWS_REGION=us-west-2
set BEDROCK_MODEL_ID=us.anthropic.claude-opus-4-6-v1

echo [OK] CLAUDE_API_PROVIDER = %CLAUDE_API_PROVIDER%
echo [OK] AWS_REGION = %AWS_REGION%
echo [OK] BEDROCK_MODEL_ID = %BEDROCK_MODEL_ID%

REM システム環境変数からAWS_BEARER_TOKEN_BEDROCKを取得
echo.
echo [2] システム環境変数をチェック中...

REM レジストリから直接読み取り（システム環境変数）
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v AWS_BEARER_TOKEN_BEDROCK 2^>nul') do set AWS_BEARER_TOKEN_BEDROCK=%%b

REM ユーザー環境変数も確認
if not defined AWS_BEARER_TOKEN_BEDROCK (
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v AWS_BEARER_TOKEN_BEDROCK 2^>nul') do set AWS_BEARER_TOKEN_BEDROCK=%%b
)

if defined AWS_BEARER_TOKEN_BEDROCK (
    echo [OK] AWS_BEARER_TOKEN_BEDROCK が設定されています
    call :show_token_preview
) else (
    echo [エラー] AWS_BEARER_TOKEN_BEDROCK が見つかりません
    echo.
    echo システム環境変数に AWS_BEARER_TOKEN_BEDROCK を設定してください:
    echo   1. Win + R を押す
    echo   2. sysdm.cpl を入力して Enter
    echo   3. 「環境変数」ボタンをクリック
    echo   4. 新規作成で以下を設定:
    echo      変数名: AWS_BEARER_TOKEN_BEDROCK
    echo      変数値: ^(ベアラートークン^)
    echo   5. このスクリプトを再実行
    echo.
    pause
    exit /b 1
)

echo.
echo [3] Streamlitアプリを起動しています...
echo ブラウザが自動的に開きます...
echo.

REM Streamlitアプリを起動（環境変数を引き継ぐ）
vscenv\Scripts\streamlit.exe run tech_doc_generator.py --server.headless false

pause
exit /b 0

:show_token_preview
REM トークンの先頭20文字を表示（エラーを回避）
setlocal enabledelayedexpansion
set "token=%AWS_BEARER_TOKEN_BEDROCK%"
set "preview=!token:~0,20!"
echo     トークンプレビュー: !preview!...
endlocal
exit /b 0
