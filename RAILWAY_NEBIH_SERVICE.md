# NEBIH SQL Railway service

Create this as a separate Railway service from the same GitHub repository.
Do not change the existing diagnostic service.

## Service settings

- Source repository: `salacs1-wq/plant-diagnosis`
- Branch: `main`
- Root directory: repository root
- Start command:

```text
uvicorn api_nebih:app --host 0.0.0.0 --port $PORT
```

- Health check path: `/health`

The SQLite database is opened read-only by `nebih_api.py`. If a custom
database location is needed, set `NEBIH_MASTER_DB` to the database path.

## Verification

After deployment, verify:

```text
/health
/docs
/products/search?q=Adengo
/usage/search?product_name=Adengo&limit=3
```

Use `openapi_nebih_sql_only_actions.json` for the separate Custom GPT
Action after replacing its server URL with the generated service domain.
