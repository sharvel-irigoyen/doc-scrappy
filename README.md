# CMP Scraper

Scrapea https://aplicaciones.cmp.org.pe/conoce_a_tu_medico/ para un listado de CMP en CSV, guarda estado y especialidades en MySQL y registra fallos.

## Requisitos
- Python 3.12+
- MySQL accesible (tabla se crea si no existe)
- Navegador Playwright Chromium descargado (`python -m playwright install chromium`)
- CSV de entrada (ej. `data.csv`, una columna con CMP)

## Variables de entorno (.env)
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

## Instalación local
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Uso
```
python main.py --csv data.csv --failed-csv failed_cmp.csv --error-log scrap.logs --retries 2 [--headed]
```
- `--failed-csv`: archivo donde se listan CMP fallidos (solo códigos).
- `--error-log`: detalle de errores con timestamp.
- `--retries`: reintentos por CMP.
- `--headed`: abre navegador visible (mejor score en reCAPTCHA v3).

## Docker
Construir y ejecutar:
```
docker compose build
docker compose run --rm scraper
```
Volúmenes ya montan `data.csv` y `failed_cmp.csv`. Para persistir `scrap.logs`, añade en `docker-compose.yml`:
```
    volumes:
      - ./data.csv:/app/data.csv:ro
      - ./failed_cmp.csv:/app/failed_cmp.csv
      - ./scrap.logs:/app/scrap.logs
```

## Notas reCAPTCHA v3
- Es probabilístico; si el sitio rechaza el token, se reintenta según `--retries` y el CMP queda en `failed_cmp.csv`.
- Usa pausas y tipeo con delays para parecer humano. Ejecutar en horarios de baja carga ayuda.
