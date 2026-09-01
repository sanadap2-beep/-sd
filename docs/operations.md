# Operations checklist

## Database

- New deployments run `alembic upgrade head` through `init_db`.
- Back up before upgrades and test restoring a copy.
- Existing pre-Alembic databases are stamped as a baseline and pass the compatibility checks.

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
