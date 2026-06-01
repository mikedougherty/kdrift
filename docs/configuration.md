# Configuration

kdrift is configured via environment variables. Use a `.env` file for local development (see `.env.example`).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_LOG_LEVEL` | No | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `APP_LOG_FORMAT` | No | `json` | Log format (`json` for production, `console` for local dev) |

## Adding New Config

1. Add the field to `AppConfig` in `src/kdrift/config.py`
2. Add to this table
3. Add to `.env.example` with a placeholder value
4. Use `pydantic.SecretStr` for any credential fields
