# feature_store.sqlite migrations

Versioned DDL for settlement columns. Ledger table: `schema_migrations`.

| Version | Name | Purpose |
|---------|------|---------|
| `001` | `settlement_columns` | `finish_position` + model score column (`score` / alias) |

## Apply

```bash
HIBS_RACING_FEATURE_STORE=/opt/hibs-racing/data/feature_store.sqlite \
  python scripts/migrate_feature_store.py

# Ledger only
python scripts/migrate_feature_store.py --status
```

Settlement and `apply_results_batch()` run pending migrations automatically under the shared `feature_store.sqlite.lock`.

## Adding migration `002`

1. Add `_upgrade_002_...` in `metrics/feature_store_migrations.py`
2. Append `MigrationSpec("002", "name", _upgrade_002_...)` to `FEATURE_STORE_MIGRATIONS`
3. Never change checksum of applied migrations — add a new version instead

## Rollback

SQLite `ADD COLUMN` is forward-only. Roll back operationally by restoring a DB backup or shipping a corrective forward migration (`003_...`).
