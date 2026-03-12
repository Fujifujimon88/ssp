@echo off
echo SSP Platform 起動スクリプト
echo ================================

REM .env が存在しない場合はコピー
if not exist ".env" (
    copy .env.example .env
    echo .env ファイルを作成しました。SECRET_KEY を変更してください。
)

echo Docker Compose で起動中...
docker compose up -d

echo.
echo ================================
echo 起動完了！
echo ダッシュボード : http://localhost:8000/dashboard
echo APIドキュメント: http://localhost:8000/docs
echo ================================
