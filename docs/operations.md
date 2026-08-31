# Operations checklist

## Database

- New deployments run `alembic upgrade head` through `init_db`.
- Back up before upgrades and test restoring a copy.
- Existing unversioned databases are **not** stamped as `head` automatically. A database
  matching the known initial schema is baselined at `403e80d4c7c9` and upgraded through
  every later revision; unknown or partially migrated schemas fail closed and require a
  backup plus an explicit operator-led repair.

## Safe first run

- New feature flags are synchronized after migrations. `peer_marketplace`,
  `trusted_seller_auto_approve`, `escrow_engine`, and `bulk_numbers` (plus the
  other provider-purchasing/financial automation flags in the registry) start
  **disabled**. Enable them only after a staging run and an explicit admin decision.
- All deposit methods and SYP withdrawals are seeded **disabled**. Add and verify
  the relevant wallet/API credentials first, then enable one method from the admin
  settings. Do not send real funds while validating callbacks, duplicate updates,
  refunds, and provider failures.
- Existing database overrides are operator state; review them before exposing the bot
  after an upgrade. The preflight below reports risky flags that are already enabled.

## Secrets

Keep `BOT_TOKEN`, provider keys, payment keys, `SETUP_KEY`, and
`INVENTORY_ENCRYPTION_KEY` in environment secrets. Never commit `.env`.

## Monitoring

- `/health/live` checks process liveness.
- `/health/ready` checks database readiness.
- `/api/v1/admin/health/deep` checks configured SMS, API, Sam, and Plisio integrations.
- Set `SENTRY_DSN` to enable error reporting without sending personal data by default.

## Hosting preflight and release checks

Run the preflight before the first start, then run it again after the API has started:

```bash
pip install -r requirements-dev.txt
python scripts/preflight.py
python scripts/preflight.py --base-url https://your-host.example --require-schema
```

The command is non-destructive. It validates required environment variables, URL and
Fernet-key formats, database connectivity, migration head, the transaction
`payment_reference` column, risky feature flags, and (when `--base-url` is supplied)
`/health/live` and `/health/ready`. Use `--strict` when optional operational warnings
such as missing Redis, provider credentials, or inventory encryption should block a
release. It never prints secret values.

```bash
python -m compileall -q .
ruff check services/balance_service.py services/feature_registry.py services/feature_service.py \
  services/inventory_service.py services/points_service.py services/bot_command_service.py \
  services/checkout_service.py services/key_swap_service.py handlers/deposit.py \
  handlers/deposit_methods.py handlers/numbers.py handlers/games.py \
  handlers/admin/number_orders.py handlers/admin/orders.py tasks/order_monitor.py \
  tasks/unified_order_monitor.py api/app.py api/reseller.py scripts/preflight.py \
  tests/test_preflight.py tests/test_api.py tests/test_core_services.py \
  tests/test_number_handlers.py tests/test_new_features.py
pytest -q
alembic check
python scripts/security_audit.py
python scripts/i18n_audit.py
```

For `POST /api/v1/checkout` and reseller orders, send an optional
`Idempotency-Key: <stable-key>` and reuse it only with the same payload when retrying.
The key is scoped to the authenticated user and the ledger reference is unique. If a
process stops after charging but before linking the provider order, retries fail closed
and require reconciliation instead of risking a second provider call or debit.

The project contains older files with existing style findings outside the hardening
surface; the targeted lint command above is the release gate for these changes. A full
project-wide lint cleanup is intentionally outside the MVP scope.
