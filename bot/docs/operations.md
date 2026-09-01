# Operations checklist

## Database

- New deployments run `alembic upgrade head` through `init_db`.
- Back up before upgrades and test restoring a copy.
- Existing unversioned databases are **not** stamped as `head` automatically. A database
  matching the known initial schema is baselined at `403e80d4c7c9` and upgraded through
  every later revision; unknown or partially migrated schemas fail closed and require a
  backup plus an explicit operator-led repair.

## Secrets

Keep `BOT_TOKEN`, provider keys, payment keys, `SETUP_KEY`, and
`INVENTORY_ENCRYPTION_KEY` in environment secrets. Never commit `.env`.

## Monitoring

- `/health/live` checks process liveness.
- `/health/ready` checks database readiness.
- `/api/v1/admin/health/deep` checks configured SMS, API, Sam, and Plisio integrations.
- Set `SENTRY_DSN` to enable error reporting without sending personal data by default.

## Release

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
alembic check
```
