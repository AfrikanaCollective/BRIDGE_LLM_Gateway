# Migrations

Alembic migrations for the durable store (`gateway/db/orm.py`) go here.

Not yet initialized — this is a placeholder. To set up:

```bash
pip install alembic
alembic init gateway/db/migrations
# then point alembic.ini / env.py at gateway.db.orm.Base.metadata
# and gateway.config.settings.database_url
```

Until this is initialized, tests and local dev use
`gateway.db.session.create_all_for_tests()` against SQLite. Do not rely on
that path in production — see CLAUDE.md "Migrations".
