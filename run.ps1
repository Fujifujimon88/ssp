# SSP Platform — Windows PowerShell コマンドランナー
# 使い方: .\run.ps1 <コマンド>
# 例:     .\run.ps1 dev

param([string]$Command = "help")

switch ($Command) {
    "dev"         { uvicorn main:app --reload --port 8000 }
    "test"        { python -m pytest tests/ -v }
    "test-e2e"    { npx playwright test }
    "lint"        { python -m py_compile main.py mdm/router.py db_models.py; Write-Host "Syntax OK" }
    "db-check"    {
        Write-Host "=== Alembic migration dry-run ===" -ForegroundColor Cyan
        alembic upgrade head --sql | Select-Object -First 120
        Write-Host "`n=== Current revision ===" -ForegroundColor Cyan
        alembic current
        Write-Host "`n=== Pending revisions ===" -ForegroundColor Cyan
        alembic history --indicate-current
    }
    "db-migrate"  { alembic upgrade head }
    "db-rollback" { alembic downgrade -1 }
    default {
        Write-Host "使い方: .\run.ps1 <command>" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "コマンド一覧:"
        Write-Host "  dev          開発サーバー起動 (port 8000)"
        Write-Host "  test         Python ユニットテスト"
        Write-Host "  test-e2e     Playwright E2E テスト"
        Write-Host "  lint         構文チェック"
        Write-Host "  db-check     Alembic dry-run (本番適用前に確認)"
        Write-Host "  db-migrate   DB マイグレーション適用"
        Write-Host "  db-rollback  1ステップ ロールバック"
    }
}
