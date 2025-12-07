# CMP Scraper

Scrapes https://aplicaciones.cmp.org.pe/conoce_a_tu_medico/ for a list of CMP numbers in a CSV, stores doctor status and specialties into MySQL, and logs failures for retries.

## Requirements
- Python 3.12+
- MySQL reachable (tables are created if missing)
- Playwright Chromium downloaded (`python -m playwright install chromium`)
- Input CSV (e.g., `data.csv`, one column with CMP codes)

## Environment (.env)
```
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=secret
DB_NAME=doctors

MAIL_HOST=smtp.gmail.com
MAIL_PORT=465
MAIL_USERNAME=...
MAIL_PASSWORD=...
MAIL_ENCRYPTION=ssl
MAIL_FROM_ADDRESS=...
MAIL_FROM_NAME=...
MAIL_TO=...
```

## Local setup
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Run
```
python main.py --csv data.csv --failed-csv failed_cmp.csv --error-log scrap.logs --retries 2 [--headed]
```
- `--failed-csv`: file to list failed CMPs (only codes).
- `--error-log`: detailed error log with timestamps.
- `--retries`: retries per CMP before marking failed.
- `--headed`: launches a visible browser (often improves reCAPTCHA v3 score).

## Docker
Build and run:
```
docker compose build
docker compose run --rm scraper
```
Volumes already mount `data.csv` (read-only) and `failed_cmp.csv` (read/write). To persist `scrap.logs`, add to `docker-compose.yml`:
```
    volumes:
      - ./data.csv:/app/data.csv:ro
      - ./failed_cmp.csv:/app/failed_cmp.csv
      - ./scrap.logs:/app/scrap.logs
```

### Using an existing MySQL container
- Set `DB_HOST` to the MySQL container name (e.g., `comerciantes-db`) and ensure the scraper joins the same Docker network (by default this compose joins `comerciantes_default`; change the name if your network differs).
- Alternatively, if MySQL is published on the host (e.g., port 3307), set `DB_HOST=host.docker.internal` and `DB_PORT=3307`, or use `network_mode: host` on Linux.

## reCAPTCHA v3 notes
- Scoring is probabilistic. If the site rejects the token and returns to the form, the CMP will be retried up to `--retries` times, then listed in `failed_cmp.csv`.
- The script simulates human typing and includes pauses; running in `--headed` and during low-traffic hours can help improve success rates.
