# Alembic 迁移目录

## 常用命令

```bash
# 基于当前 ORM 自动生成新迁移脚本（首次用）
alembic revision --autogenerate -m "initial schema"

# 应用到最新版本
alembic upgrade head

# 回滚一步
alembic downgrade -1

# 查看历史
alembic history --verbose
```

## 注意

- 数据库连接串由 `env.py` 从 `config.settings.db_url` 动态注入
- 所有 ORM 变更应先改 `src/mo_stock/storage/models.py`，再跑 `autogenerate`
- 生成的 migration 要人工 review 后再 `upgrade head`
