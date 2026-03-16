# SSP Platform — よく使うコマンド集

.PHONY: dev test lint db-check db-migrate

# ── 開発サーバー ──────────────────────────────────────────────────────
dev:
	uvicorn main:app --reload --port 8000

# ── テスト ────────────────────────────────────────────────────────────
test:
	python -m pytest tests/ -v

test-e2e:
	npx playwright test

# ── DB マイグレーション ───────────────────────────────────────────────
## dry-run: 本番適用前に必ずこれで差分を確認する
db-check:
	@echo "=== Alembic migration dry-run (SQL preview) ==="
	alembic upgrade head --sql | head -120
	@echo ""
	@echo "=== Current revision ==="
	alembic current
	@echo ""
	@echo "=== Pending revisions ==="
	alembic history --indicate-current

## 実際に DB へ適用
db-migrate:
	alembic upgrade head

## ロールバック（1ステップ）
db-rollback:
	alembic downgrade -1

# ── コードチェック ────────────────────────────────────────────────────
lint:
	python -m py_compile main.py mdm/router.py db_models.py
	@echo "Syntax OK"
