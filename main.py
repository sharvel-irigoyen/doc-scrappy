#!/usr/bin/env python3
"""
Scrapes CMP doctor data from https://aplicaciones.cmp.org.pe/conoce_a_tu_medico/
for a list of CMP numbers in a CSV, storing status and specialties in MySQL.

Notes:
- The site protects the form with reCAPTCHA v3. This script triggers the site's
  own grecaptcha flow via Playwright; it does not bypass or disable it. If the
  site rejects the token or blocks automation you will need to provide a valid
  token or perform the lookup manually.
- On failure that stops the run, an email alert is sent using the SMTP settings
  in the environment.
"""

import argparse
import asyncio
import csv
import logging
import os
import random
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, List, Tuple
from urllib.parse import urljoin

import pymysql
from bs4 import BeautifulSoup
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# Load .env if python-dotenv is available.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False

BASE_URL = "https://aplicaciones.cmp.org.pe/conoce_a_tu_medico/"
SITE_KEY = "6LcYiNwrAAAAAB2vkiot46ogkFJj0MRakLVZTQRa"


@dataclass
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class MailConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    from_name: str
    to_address: str
    use_ssl: bool = True


def load_cmp_list(csv_path: str) -> List[str]:
    cmp_values: List[str] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            cmp_str = row[0].strip()
            if cmp_str:
                cmp_values.append(cmp_str)
    return cmp_values


def load_db_config(args: argparse.Namespace) -> DbConfig:
    return DbConfig(
        host=args.db_host or os.getenv("DB_HOST", "localhost"),
        port=int(args.db_port or os.getenv("DB_PORT", "3306")),
        user=args.db_user or os.getenv("DB_USER", "root"),
        password=args.db_password or os.getenv("DB_PASSWORD", ""),
        database=args.db_name or os.getenv("DB_NAME", "doctors"),
    )


def load_mail_config() -> MailConfig:
    host = os.getenv("MAIL_HOST", "smtp.gmail.com")
    port = int(os.getenv("MAIL_PORT", "465"))
    username = os.getenv("MAIL_USERNAME", "")
    password = os.getenv("MAIL_PASSWORD", "")
    from_address = os.getenv("MAIL_FROM_ADDRESS", username)
    from_name = os.getenv("MAIL_FROM_NAME", "Scraper")
    to_address = os.getenv("MAIL_TO", from_address)
    use_ssl = os.getenv("MAIL_ENCRYPTION", "ssl").lower() == "ssl"
    return MailConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        from_address=from_address,
        from_name=from_name,
        to_address=to_address,
        use_ssl=use_ssl,
    )


def connect_db(cfg: DbConfig) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        autocommit=False,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_tables(conn: pymysql.connections.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                cmp VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                PRIMARY KEY (cmp)
            ) CHARACTER SET utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS doctor_specialties (
                id BIGINT NOT NULL AUTO_INCREMENT,
                cmp VARCHAR(10) NOT NULL,
                name VARCHAR(255) NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uniq_cmp_name (cmp, name),
                CONSTRAINT fk_doctor_specialties_cmp
                    FOREIGN KEY (cmp) REFERENCES doctors(cmp)
                    ON DELETE CASCADE
            ) CHARACTER SET utf8mb4
            """
        )
    conn.commit()


def save_doctor(
    conn: pymysql.connections.Connection, cmp_value: str, status: str, specialties: Iterable[str]
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO doctors (cmp, status)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE status = VALUES(status)
            """,
            (cmp_value, status),
        )
        for spec in specialties:
            cur.execute(
                """
                INSERT IGNORE INTO doctor_specialties (cmp, name)
                VALUES (%s, %s)
                """,
                (cmp_value, spec),
            )
    conn.commit()


def extract_details(html: str) -> Tuple[str, List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    status = ""
    specialties: List[str] = []

    def normalize(text: str) -> str:
        mapping = str.maketrans(
            {
                "Á": "A",
                "À": "A",
                "Â": "A",
                "Ä": "A",
                "Ã": "A",
                "É": "E",
                "È": "E",
                "Ê": "E",
                "Ë": "E",
                "Í": "I",
                "Ì": "I",
                "Ï": "I",
                "Î": "I",
                "Ó": "O",
                "Ò": "O",
                "Ö": "O",
                "Ô": "O",
                "Õ": "O",
                "Ú": "U",
                "Ù": "U",
                "Ü": "U",
                "Û": "U",
                "Ñ": "N",
            }
        )
        return text.upper().translate(mapping).strip()

    status_values = {
        "HABIL",
        "INHABIL",
        "NO HABIL",
        "NOHABIL",
        "SUSPENDIDO",
        "SUSPENSION",
        "FALLECIDO",
        "INACTIVO",
        "BAJA",
        "RETIRADO",
        "CANCELADO",
    }
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        # Status table: often a single-row, single-cell table; accept two rows too.
        if not status:
            if len(rows) == 1 and len(rows[0].find_all("td")) == 1:
                cell_text = rows[0].get_text(strip=True)
                if normalize(cell_text) in status_values:
                    status = cell_text
            elif len(rows) == 2 and len(rows[1].find_all("td")) == 1:
                cell_text = rows[1].get_text(strip=True)
                if normalize(cell_text) in status_values:
                    status = cell_text
        # Specialties table: header includes "Registro".
        header_cells = [td.get_text(strip=True).upper() for td in rows[0].find_all("td")]
        if any("REGISTRO" in cell for cell in header_cells):
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if cols:
                    spec = cols[0]
                    if spec:
                        specialties.append(spec)
    return status, specialties


def send_error_email(mail_cfg: MailConfig, subject: str, body: str) -> None:
    if not mail_cfg.username or not mail_cfg.password or not mail_cfg.to_address:
        logging.warning("Mail settings incomplete; skipping alert email.")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{mail_cfg.from_name} <{mail_cfg.from_address}>"
    msg["To"] = mail_cfg.to_address
    msg.set_content(body)

    context = ssl.create_default_context()
    try:
        if mail_cfg.use_ssl:
            with smtplib.SMTP_SSL(mail_cfg.host, mail_cfg.port, context=context) as server:
                server.login(mail_cfg.username, mail_cfg.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(mail_cfg.host, mail_cfg.port) as server:
                server.starttls(context=context)
                server.login(mail_cfg.username, mail_cfg.password)
                server.send_message(msg)
        logging.info("Error alert email sent to %s", mail_cfg.to_address)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to send alert email: %s", exc)


def append_failed_cmp(path: str, cmp_value: str) -> None:
    """Guarda solo el CMP fallido en un CSV para reintentos posteriores."""
    try:
        with open(path, "a", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([cmp_value])
        logging.warning("CMP %s agregado a %s", cmp_value, path)
    except Exception as exc:  # noqa: BLE001
        logging.error("No se pudo guardar CMP fallido %s en %s: %s", cmp_value, path, exc)


def append_error_log(path: str, cmp_value: str, reason: str) -> None:
    """Registra el error completo para diagnóstico."""
    if os.path.isdir(path):
        path = os.path.join(path, "scrap.logs")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CMP {cmp_value}: {reason}\n")
        logging.warning("Error de CMP %s registrado en %s", cmp_value, path)
    except Exception as exc:  # noqa: BLE001
        logging.error("No se pudo escribir en %s para CMP %s: %s", path, cmp_value, exc)


async def dump_debug(page, cmp_value: str, label: str) -> None:
    """Dump page HTML and screenshot to /tmp for troubleshooting."""
    safe_label = label.replace(" ", "_")
    html_path = f"/tmp/cmp_{cmp_value}_{safe_label}.html"
    img_path = f"/tmp/cmp_{cmp_value}_{safe_label}.png"
    try:
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        await page.screenshot(path=img_path, full_page=True)
        logging.warning("Debug saved: %s and %s", html_path, img_path)
    except Exception as exc:  # noqa: BLE001
        logging.error("Could not save debug artifacts for CMP %s: %s", cmp_value, exc)


async def fetch_detail_html(page, cmp_value: str) -> str:
    home_url = urljoin(BASE_URL, "index.php")
    # Retry loading the home form a couple of times in case resources hang.
    for attempt in range(2):
        await page.goto(home_url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector('input[name="cmp"]', timeout=20000, state="visible")
            break
        except PlaywrightTimeoutError:
            if attempt == 1:
                await dump_debug(page, cmp_value, "home_timeout")
                raise
            await page.wait_for_timeout(2000)

    async def human_type(selector: str, text: str) -> None:
        await page.click(selector, timeout=20000)
        if text:
            await page.fill(selector, "")  # ensure empty
            await page.keyboard.type(text, delay=random.randint(50, 120))
        else:
            await page.fill(selector, "")

    await human_type('input[name="cmp"]', cmp_value)
    await human_type('input[name="appaterno"]', "")
    await human_type('input[name="apmaterno"]', "")
    await human_type('input[name="nombres"]', "")

    # Trigger the site's own reCAPTCHA flow to obtain a token.
    await page.wait_for_function("() => window.grecaptcha && grecaptcha.execute", timeout=10000)
    token = await page.evaluate(
        "(siteKey) => grecaptcha.execute(siteKey, { action: 'colegiados_busqueda' })",
        SITE_KEY,
    )
    await page.evaluate(
        "(token) => { const el = document.getElementById('g-recaptcha-response'); if (el) { el.value = token; } }",
        token,
    )

    try:
        async with page.expect_navigation(wait_until="networkidle", timeout=15000):
            await page.click('input[type="submit"][value="Buscar"]')
    except PlaywrightTimeoutError:
        raise RuntimeError(f"No navigation after submitting CMP {cmp_value}")

    # Pausa breve tras el submit para dar tiempo a generar el enlace de detalle.
    await asyncio.sleep(3.0 + random.uniform(0.5, 1.5))

    # Espera a que aparezca el enlace de detalle; si no aparece, es fallo.
    detail_el = await page.wait_for_selector(
        'a[href*="datos-colegiado-detallado.php"]', timeout=20000, state="visible"
    )
    detail_href = await detail_el.get_attribute("href")
    if not detail_href:
        raise RuntimeError(f"Enlace de detalle vacio para CMP {cmp_value}")
    detail_url = urljoin(BASE_URL, detail_href)

    await page.goto(detail_url, wait_until="networkidle")
    return await page.content()


async def scrape_cmp_numbers(
    cmp_values: List[str],
    conn,
    mail_cfg: MailConfig,
    headless: bool,
    failed_csv: str,
    error_log: str,
    retries: int,
) -> Tuple[int, int]:
    total_start = time.perf_counter()
    successes = 0
    failures = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
        )

        for cmp_value in cmp_values:
            attempt = 0
            success = False
            while attempt <= retries and not success:
                attempt += 1
                page = await context.new_page()
                page.set_default_timeout(60000)
                logging.info("Processing CMP %s (intento %d/%d)", cmp_value, attempt, retries + 1)
                cmp_start = time.perf_counter()
                try:
                    detail_html = await fetch_detail_html(page, cmp_value)
                    status, specialties = extract_details(detail_html)
                    if not status:
                        await dump_debug(page, cmp_value, "missing_status")
                        raise RuntimeError(f"No se pudo extraer estado para CMP {cmp_value}")
                    save_doctor(conn, cmp_value, status, specialties)
                    logging.info(
                        "Saved CMP %s with status '%s' and %d specialties",
                        cmp_value,
                        status,
                        len(specialties),
                    )
                    successes += 1
                    success = True
                except Exception as exc:  # noqa: BLE001
                    logging.exception("Error con CMP %s: %s", cmp_value, exc)
                    await dump_debug(page, cmp_value, "error")
                    append_error_log(error_log, cmp_value, str(exc))
                    if attempt > retries:
                        append_failed_cmp(failed_csv, cmp_value)
                        failures += 1
                finally:
                    elapsed = time.perf_counter() - cmp_start
                    logging.info("CMP %s finalizado en %.2f segundos", cmp_value, elapsed)
                    await page.close()
                if not success and attempt <= retries:
                    await asyncio.sleep(3)

        await browser.close()
    total_elapsed = time.perf_counter() - total_start
    logging.info(
        "Scraping completado en %.2f segundos (%d ok, %d fallidos, archivo fallidos: %s)",
        total_elapsed,
        successes,
        failures,
        failed_csv,
    )
    return successes, failures

def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape CMP doctor data into MySQL.")
    parser.add_argument("--csv", required=True, help="Ruta del CSV con una columna CMP.")
    parser.add_argument("--db-host", help="Host MySQL (por defecto DB_HOST o localhost).")
    parser.add_argument("--db-port", help="Puerto MySQL (por defecto DB_PORT o 3306).")
    parser.add_argument("--db-user", help="Usuario MySQL (por defecto DB_USER o root).")
    parser.add_argument("--db-password", help="Clave MySQL (por defecto DB_PASSWORD).")
    parser.add_argument("--db-name", help="Base de datos (por defecto DB_NAME o doctors).")
    parser.add_argument(
        "--failed-csv",
        default="failed_cmp.csv",
        help="Ruta del CSV donde guardar CMP fallidos (por defecto failed_cmp.csv).",
    )
    parser.add_argument(
        "--error-log",
        default="scrap.logs",
        help="Archivo de log para detallar errores de scraping (por defecto scrap.logs).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Reintentos por CMP antes de marcarlo como fallido (por defecto 1).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Ejecutar navegador con interfaz (headless por defecto).",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    load_dotenv()
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cmp_values = load_cmp_list(args.csv)
    if not cmp_values:
        logging.error("No hay CMP en el CSV %s", args.csv)
        sys.exit(1)

    db_cfg = load_db_config(args)
    mail_cfg = load_mail_config()
    conn = connect_db(db_cfg)
    ensure_tables(conn)

    try:
        asyncio.run(
            scrape_cmp_numbers(
                cmp_values,
                conn,
                mail_cfg,
                headless=not args.headed,
                failed_csv=args.failed_csv,
                error_log=args.error_log,
                retries=max(0, args.retries),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("Scraping detenido: %s", exc)
        send_error_email(
            mail_cfg,
            subject="Scraper detenido",
            body=f"El scraping se detuvo por un error fatal: {exc}",
        )
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
