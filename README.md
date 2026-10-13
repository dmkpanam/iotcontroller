# Home IoT Controller

A local-first FastAPI service for provisioned home IoT devices. It provides device authentication, capability-checked telemetry ingestion, indoor environment readings, weather and waste data, and a dashboard aggregation endpoint. It does not include firmware or a touchscreen UI.

## Requirements and setup

Python 3.11 or newer is required (fully compatible with Raspberry Pi OS Bookworm's default Python 3.11).

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

The default bind address is `127.0.0.1`. Start the service with:

```sh
alembic upgrade head
home-controller serve
```

`home-controller serve` reads `API_HOST` and `API_PORT` from `.env`. For access from trusted devices on the local network, bind to the Raspberry Pi's LAN interface (or `0.0.0.0`) and restrict access with the host firewall. Do not expose the service directly to the internet. Bearer credentials must only be sent over HTTPS outside an isolated development network; terminate TLS at a trusted reverse proxy if required.

The SQLite file defaults to `./home_controller.db`. Back it up while the service is stopped, or use SQLite's online backup API. Keep backups access-restricted and test restoration periodically.

### Raspberry Pi OS deployment

The service runs directly on Raspberry Pi OS Bookworm (which includes Python 3.11 by default). Run the service from a dedicated, non-login OS account with read access to `.env` and write access only to the application/database directory. Set `API_HOST` to the Pi's LAN address (or `0.0.0.0` behind a firewall), keep `API_PORT` on the trusted LAN, and do not forward it from the internet.

A minimal systemd unit for an installation under `/opt/home-controller` can use:

```ini
[Unit]
Description=Home IoT Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=home-controller
Group=home-controller
WorkingDirectory=/opt/home-controller
EnvironmentFile=/opt/home-controller/.env
ExecStart=/opt/home-controller/.venv/bin/home-controller serve
Restart=on-failure
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/home-controller

[Install]
WantedBy=multi-user.target
```

Restrict `.env` and the SQLite database to the service/admin accounts. Review systemd logs with `journalctl -u home-controller`; back up the database before migrations.

## Device provisioning

Provisioning is an administrator-only CLI operation, not a public API:

```sh
home-controller create \
  --device-id esp32-kitchen-environment-01 \
  --name "Kitchen environment sensor" \
  --device-type environment_sensor \
  --location Kitchen \
  --hardware-model ESP32-S3 \
  --capability telemetry.temperature.publish \
  --capability telemetry.humidity.publish
```

The generated credential is printed once. Store it securely and send it as `Authorization: Bearer <credential>`. Credentials are stored as salted scrypt hashes; neither the plaintext token nor authorization headers are logged.

```sh
home-controller disable --device-id esp32-kitchen-environment-01
home-controller enable --device-id esp32-kitchen-environment-01
home-controller revoke --device-id esp32-kitchen-environment-01
home-controller rotate --device-id esp32-kitchen-environment-01
home-controller show --device-id esp32-kitchen-environment-01
home-controller capabilities --device-id esp32-kitchen-environment-01
home-controller add-capability --device-id esp32-kitchen-environment-01 --capability telemetry.environment.read
home-controller remove-capability --device-id esp32-kitchen-environment-01 --capability telemetry.environment.read
```

Rotation revokes the prior credentials and prints the new credential once. A disabled or revoked device cannot authenticate. Assign touchscreen read capabilities only to touchscreen devices; the sensor publishing capabilities do not grant unrelated access.

### Managing device capabilities

Use the CLI to inspect and adjust the capabilities granted to an existing device:

- **List capabilities**:
  ```sh
  home-controller capabilities --device-id esp32-kitchen-environment-01
  ```
- **Add one or more capabilities**:
  ```sh
  home-controller add-capability \
    --device-id esp32-kitchen-environment-01 \
    --capability telemetry.environment.read
  ```
  Multiple `--capability` arguments can be passed to add several at once:
  ```sh
  home-controller add-capability \
    --device-id esp32-kitchen-environment-01 \
    --capability weather.read \
    --capability waste_collection.read
  ```
- **Remove a capability**:
  ```sh
  home-controller remove-capability \
    --device-id esp32-kitchen-environment-01 \
    --capability telemetry.environment.read
  ```

### Supported capabilities

Capabilities are explicitly granted when a device is provisioned. The API currently recognizes:

| Capability | Permitted operation |
| --- | --- |
| `telemetry.temperature.publish` | Submit temperature measurements (`Cel`) to `POST /api/v1/telemetry`. |
| `telemetry.humidity.publish` | Submit relative humidity measurements (`%`) to `POST /api/v1/telemetry`. |
| `telemetry.environment.read` | Read configured indoor conditions from `GET /api/v1/environment/current` and the authenticated device's latest measurements from `GET /api/v1/devices/me/telemetry/latest`. |
| `weather.read` | Read current conditions and forecasts from `GET /api/v1/weather/current` and `GET /api/v1/weather/forecast`. |
| `waste_collection.read` | Read the next configured bin collection from `GET /api/v1/waste/next`. |

`GET /api/v1/dashboard` requires all three read capabilities: `telemetry.environment.read`, `weather.read`, and `waste_collection.read`. The dashboard reports integration failures independently, but a device missing any required capability is denied access. Capabilities are checked by the server; listing one in a request does not grant it.

For example, provision a touchscreen with its required read permissions:

```sh
home-controller create \
  --device-id kitchen-touchscreen-01 \
  --name "Kitchen touchscreen" \
  --device-type touchscreen \
  --capability telemetry.environment.read \
  --capability weather.read \
  --capability waste_collection.read
```

### Supported device types

Device types are stored in the `device_types` database catalog and seeded by `alembic upgrade head`. The current migration seeds:

| Type | Intended use |
| --- | --- |
| `environment_sensor` | Sensor devices that publish environmental measurements. |
| `touchscreen` | Display devices that read environment, weather, waste, and dashboard data. |

List the types seeded in the database with:

```sh
home-controller device-types
```

Provisioning validates `--device-type` against this catalog. An unseeded or unknown type is rejected; add a deliberate migration to introduce another supported type rather than using arbitrary type names.

Set `ENVIRONMENT_SOURCE_DEVICE_ID` to the provisioned sensor ID. The sensor is expected to submit readings roughly every ten minutes, but missing intervals are tolerated. Readings older than `TELEMETRY_FRESHNESS_SECONDS` are marked stale.

## API and protocol

All application endpoints are under `/api/v1` except liveness/readiness at `/health/live` and `/health/ready`. Interactive OpenAPI is at `/docs` during development.

```sh
curl http://127.0.0.1:8000/health/ready
curl -H "Authorization: Bearer $DEVICE_TOKEN" http://127.0.0.1:8000/api/v1/devices/me
curl -X POST http://127.0.0.1:8000/api/v1/telemetry \
  -H "Authorization: Bearer $DEVICE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"protocol_version":"1.0","message_id":"e0a87b47-3f19-4c55-94d8-5f50e1e9c68a","device_id":"esp32-kitchen-environment-01","message_type":"telemetry.publish","timestamp":"2026-10-12T12:00:00Z","payload":{"measurements":[{"metric":"temperature","value":22.4,"unit":"Cel"},{"metric":"relative_humidity","value":56.2,"unit":"%"}]}}'
curl -H "Authorization: Bearer $TOUCHSCREEN_TOKEN" http://127.0.0.1:8000/api/v1/environment/current
curl -H "Authorization: Bearer $TOUCHSCREEN_TOKEN" http://127.0.0.1:8000/api/v1/weather/current
curl -H "Authorization: Bearer $TOUCHSCREEN_TOKEN" http://127.0.0.1:8000/api/v1/weather/forecast
curl -H "Authorization: Bearer $TOUCHSCREEN_TOKEN" http://127.0.0.1:8000/api/v1/waste/next
curl -H "Authorization: Bearer $TOUCHSCREEN_TOKEN" http://127.0.0.1:8000/api/v1/dashboard
```

`message_id` is a per-device idempotency key. Repeating the exact message is acknowledged as a duplicate without another telemetry insert; reusing its ID with different content returns HTTP 409. Timestamps must contain a timezone and may not be more than five minutes in the future. Telemetry is limited to -50..100 °C (`Cel`) and 0..100% relative humidity. Invalid requests return non-2xx responses with a machine-readable error.

The server derives identity from the bearer credential and rejects an envelope `device_id` that differs from the authenticated device. Capabilities are assigned during provisioning and checked by each operation.

## Integrations and configuration

Weather supports both **OpenWeatherMap** and **Open-Meteo**:
- Set `WEATHER_PROVIDER=openweathermap` and provide `WEATHER_API_KEY` (e.g. your OpenWeatherMap API key).
- Set `WEATHER_PROVIDER=open_meteo` for keyless non-commercial Open-Meteo forecasts.

Configure location and tuning via `WEATHER_LATITUDE`, `WEATHER_LONGITUDE`, `WEATHER_CACHE_TTL_SECONDS`, and `WEATHER_TIMEOUT_SECONDS`. Timestamps are handled in UTC; all response temperatures and wind speeds are normalized to Celsius and km/h. Results are cached; when an upstream request fails, a cached result may be returned with `stale: true`. Without cache, weather endpoints return HTTP 503.

No supported public Bury Council API is configured in this project. Waste collection uses an explicit manual-schedule adapter, not a fabricated council endpoint. After confirming dates for the configured address, set `WASTE_ADDRESS` and, for example:

```sh
WASTE_MANUAL_SCHEDULE='[{"collection_date":"2026-10-16","bins":["General waste","Recycling"]}]'
```

Dates remain local calendar dates in `LOCATION_TIMEZONE` (default `Europe/London`); the default reminder date is one day before the actual next collection. It therefore flags Thursday for a Friday collection without assuming all collections are on Friday. Use an adapter implementing `WasteProvider` when a supported source is selected.

## Development, tests and OpenAPI

```sh
pytest
ruff check .
ruff format --check .
python scripts/export_openapi.py
```

Tests use an isolated temporary SQLite database and never access the configured developer database. External provider calls are mocked in tests. Apply schema changes with `alembic revision --autogenerate -m "description"` and `alembic upgrade head`; the checked-in initial migration creates all tables and indexes.

The OpenAPI 3.1 document is generated from FastAPI, rather than maintained separately:

```sh
python scripts/export_openapi.py
```

## Scope and security notes

This is a foundation, not a complete home automation platform. There is no browser CORS policy, self-registration route, built-in administrator web UI, automatic Bury collection lookup, or ESP32 retry implementation. Devices should retry transient telemetry failures using bounded exponential backoff with jitter. Protect the CLI host and database file as administrator resources. The local-development default is plaintext HTTP on loopback; use TLS for any bearer credential crossing an untrusted network.
