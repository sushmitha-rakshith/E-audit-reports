import asyncio
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import Playwright, async_playwright
import pandas as pd
from sqlalchemy import create_engine, text

load_dotenv()  # Load environment variables from .env file if present

# --- Site login config ---
USERNAME = os.environ.get("USERNAME")
PASSWORD = os.environ.get("PASSWORD")
URL = os.environ.get("URL")

# --- Report date range (adjust as needed, or pull from env/args too) ---
FROM_SERVICE_DATE = os.environ.get("FROM_DATE", "07/01/2026")
TO_SERVICE_DATE = os.environ.get("TO_DATE", "8/25/26")

# --- Database config (set these in your .env file do NOT hardcode credentials) ---
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_SERVER = os.environ.get("DB_SERVER")
DB_NAME = os.environ.get("DB_NAME")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_TABLE = os.environ.get("DB_TABLE", "DenialAnalysis_Details")

# --- Denial code lookup template ---
DENIAL_CODE_TEMPLATE = os.environ.get(
    "DENIAL_CODE_TEMPLATE", "template/Pay_40_Denail code.xlsx"
)

REPORT_SHEET_NAME = "PAY_40_ERAPayerProcessingReport"

DOWNLOAD_DIR = Path("./downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
CONCURRENCY = 25

# --- Logging config ---
LOG_DIR = Path(os.environ.get("LOG_DIR", "./logs"))
LOG_DIR.mkdir(exist_ok=True)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# One log file per run (timestamped) plus a rotating "latest" file that
# survives across runs, so you can tail a stable filename in production.
_run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_LOG_FILE = LOG_DIR / f"pay_40_{_run_timestamp}.log"
LATEST_LOG_FILE = LOG_DIR / "pay_40.log"

logger = logging.getLogger("pay_40")
logger.setLevel(LOG_LEVEL)
logger.propagate = False

_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if not logger.handlers:
    # Console handler - what you see when running interactively
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    logger.addHandler(_console_handler)

    # Per-run log file - one full record of this run
    _run_file_handler = logging.FileHandler(RUN_LOG_FILE, encoding="utf-8")
    _run_file_handler.setFormatter(_formatter)
    logger.addHandler(_run_file_handler)

    # Rotating "latest" log file - stable filename, keeps last 5 x 10MB
    _rotating_handler = logging.handlers.RotatingFileHandler(
        LATEST_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _rotating_handler.setFormatter(_formatter)
    logger.addHandler(_rotating_handler)

logger.info("Logging initialized. Run log: %s", RUN_LOG_FILE.resolve())


def clear_download_dir() -> None:
    """Delete any existing files in DOWNLOAD_DIR before starting a fresh run."""
    count = 0
    for f in DOWNLOAD_DIR.iterdir():
        if f.is_file():
            f.unlink()
            count += 1
    logger.info("Cleared %d old file(s) from %s", count, DOWNLOAD_DIR.resolve())


PRACTICES =[
    "COMUC", "EGUC", "AUCLA", "GA037", "SACUC", "OK209", "IL203", "TX008", "IL204", "CA023", "SUPER", "TX124", "VA202", "CA025", "SD201", "SOMER", "OK203", "CO200", "NM202", "TCPFL", "BECAR", "NATO", "ABBY", "EAST", "ZNC08",
    "HI200", "QCARE", "TUCNM", "QUC", "WTRFT", "TX111", "ZMS01", "FOLSM", "WA201", "WY003", "AMSXP", "VIP", "LA204", "CKAQC", "LA205", "IL008", "KY201", "NC026", "MD204", "TX222", "PUCNA", "PHYUC", "NC205", "FL030", "FL020",
    "LFAC", "CA212", "GA205", "TX120", "OK204", "TX040", "STEEL", "WY007", "ZCA07", "WV200",
    "IN203", "TX119", "LA004", "SMOKE", "WFE", "HVNS", "IN204", "TX226", "SRC", "DAVIS", "LIFE", "BIGBR", "CA213", "FL222", "GFM", "IL009", "CO202", "NY201", "OK016", "PA022", "OH213", "AK201", "CA232",
    "CO204", "PA200", "PEACH", "TX212", "ACMA", "TN203", "PNUC", "IL210", "MS206", "CA228", "OK201", "IN206", "TX203", "MS019", "NE200", "SSM", "LONG", "OURUC", "MLUC", "LA006", "BAYOU", "IL205", "NM201", "TX239",
    "AZ202", "ZIL03", "FL213", "PA201", "OH009", "ZCA15", "GA206", "WA200", "TX229", "FL215", "OH025", "OH204", "TX240", "TX067", "TX244", "TX206", "MD203", "NE203", "AL024", "OH201", "CA221", "MS205",
    "CA208", "AL209", "CA230", "TX107", "MHLTH",
    "FL218", "CT007", "FL201", "PA202", "OH212", "TN205", "MS203", "GA207", "PILA", "CA235", "TN207", "MN203", "IL217", "IL221", "OH207", "LA209", "GA214", "PICNA", "TX254",
    "IN002", "TN011", "MO001", "OH206", "SC201", "CQCFP", "VA204", "LOWUC", "REDMD", "UCMC",
    "HCANC", "SUCC", "CRUC", "LA055", "PUCLA", "LA207", "EONE", "UCJ", "DNWIC", "NE005", "ICC", "AL204",
]

# How many practices to process before restarting the browser session.
# Long-lived sessions on sites like this can get flaky after a while;
# tune this down if you see failures partway through a run.
RESTART_EVERY = 25

# How many times to retry a practice that hasn't produced a downloaded file yet,
# before giving up on it for this run. Set MAX_ATTEMPTS_PER_PRACTICE=0 in your
# .env for effectively unlimited retries (keeps retrying every failed practice
# forever until it downloads or you kill the process).
MAX_ATTEMPTS_PER_PRACTICE = int(os.environ.get("MAX_ATTEMPTS_PER_PRACTICE", 5))

# If true (default), the DB table is truncated before the cleaned data is
# pushed, so each run replaces prior data instead of appending to it.
# Set TRUNCATE_BEFORE_UPLOAD=false in your .env to disable and append instead.
TRUNCATE_BEFORE_UPLOAD = os.environ.get(
    "TRUNCATE_BEFORE_UPLOAD", "true"
).strip().lower() in ("1", "true", "yes")

# Column rename map (matches the raw PAY 40 export headers to clean DB column names).
# Note: some source headers have stray whitespace (incl. newlines) - we strip
# column names after reading, so keys here should be the *stripped* versions.
COLUMN_RENAME_MAP = {
    'ERA Date':                 'ERA_Date',
    'Era Num':                  'ERA_Number',
    'Claim Num':                'Claim_Number',
    'Pt Account':                'Patient_Account',
    'Last Name':                'Patient_LastName',
    'First Name':               'Patient_FirstName',
    'DOS':                      'Service_Date',
    'Inv Num':                  'Invoice_Number',
    'Date Of Remit':            'Remit_Date',
    'Insurance Name':           'Insurance_Name',
    'Financial Class':          'Financial_Class',
    'Member Id':                'Member_ID',
    'Group Id':                 'Group_ID',
    'Proc Code':                'Procedure_Code',
    'Charge Amt':               'Charge_Amount',
    'Adj Code':                 'Adj_Code',
    'ERAReason Description':    'ERAReason_Description',
    'Adj Description':          'Adj_Description',
    'Error Message':            'Error_Message',
    'Remittance Code 1':        'Remittance_Code1',
    'Remittance Description 1': 'Remittance_Description1',
    'Remittance Code 2':        'Remittance_Code2',
    'Remittance Description 2': 'Remittance_Description2',
    'Remittance Code 3':        'Remittance_Code3',
    'Remittance Description 3': 'Remittance_Description3',
    'Remittance Code 4':        'Remittance_Code4',
    'Remittance Description 4': 'Remittance_Description4',
    'Remittance Code 5':        'Remittance_Code5',
    'Remittance Description 5': 'Remittance_Description5',
    'Remittance  Code 6':       'Remittance_Code6',
    'Remittance Description 6': 'Remittance_Description6',
    'Remittance  Code 7':       'Remittance_Code7',
    'Remittance Description 7': 'Remittance_Description7',
    'Remittance Code 8':        'Remittance_Code8',
    'Remittance Description 8': 'Remittance_Description8',
    'Remittance  Code 9':       'Remittance_Code9',
    'Remittance Description 9': 'Remittance_Description9',
    'Remittance  Code 10':      'Remittance_Code10',
    'Remittance Description 10': 'Remittance_Description10',
}

FINAL_COLUMNS = [
    'ERA_Date', 'ERA_Number', 'Claim_Number', 'Patient_Account',
    'Patient_LastName', 'Patient_FirstName', 'Service_Date',
    'Invoice_Number', 'Remit_Date', 'Insurance_Name', 'CPID',
    'Financial_Class', 'Member_ID', 'Group_ID', 'Procedure_Code',
    'Charge_Amount', 'NPI', 'Adj_Code', 'ERAReason_Description',
    'Adj_Description', 'Amount', 'Error_Message', 'Remittance_Code1',
    'Remittance_Description1', 'Remittance_Code2', 'Remittance_Description2',
    'Remittance_Code3', 'Remittance_Description3', 'Remittance_Code4',
    'Remittance_Description4', 'Remittance_Code5', 'Remittance_Description5',
]


async def login(playwright: Playwright):
    """Log in and navigate to the Reports section. Returns (browser, context, page)."""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()

    await page.goto(URL)
    await page.locator("#txtLogin").click()
    await page.locator("#txtLogin").fill(USERNAME)
    await page.get_by_role("button", name="Next").click()
    await page.locator("#txtPassword").click()
    await page.locator("#txtPassword").fill(PASSWORD)
    await page.get_by_role("button", name="Login").click()
    await page.get_by_role("button", name="Admin2").click()
    await page.get_by_role("link", name="Reports").click()

    return browser, context, page


def get_frames(page):
    report_frame = (
        page.locator("iframe[name=\"reportMainWindow\"]")
        .content_frame.locator("frame[name=\"PVRC_MainStage\"]")
        .content_frame
    )
    nav_frame = (
        page.locator("iframe[name=\"reportMainWindow\"]")
        .content_frame.locator("frame[name=\"NavFrame\"]")
        .content_frame
    )
    return report_frame, nav_frame


async def ensure_reports_ready(page, timeout: int = 60000) -> tuple:
    """
    Make sure the Reports iframe/subpractice dropdown is actually present and
    interactable before we try to use it. Re-clicks the Reports link if needed.
    Returns fresh (report_frame, nav_frame).
    """
    report_frame, nav_frame = get_frames(page)
    try:
        await report_frame.locator("#subpracticeselect").wait_for(state="visible", timeout=timeout)
    except Exception:
        # Frame didn't come up in time try clicking Reports again to force a reload
        logger.warning("Reports frame not ready, re-clicking Reports link...")
        await page.get_by_role("link", name="Reports").click()
        await asyncio.sleep(2)
        report_frame, nav_frame = get_frames(page)
        await report_frame.locator("#subpracticeselect").wait_for(state="visible", timeout=timeout)
    return report_frame, nav_frame


async def run_report_for_practice(page, practice: str) -> Path:
    """Run the PAY 40 report for a single practice and download the Excel export."""
    report_frame, nav_frame = await ensure_reports_ready(page)

    # --- Select subpractice and search for report ---
    await report_frame.locator("#subpracticeselect").select_option(practice, timeout=60000)
    await asyncio.sleep(3)  # let the frame/report list reload after switching practice
    report_frame, nav_frame = get_frames(page)  # re-grab frames in case they reloaded

    await nav_frame.locator("#userSearch").click()
    await nav_frame.locator("#userSearch").fill("pay_40")
    await nav_frame.locator("#userSearch").press("Enter")

    await report_frame.get_by_text(
        "ERA Payment Processing NewFinancials, PAY 40 Report will show data returned by"
    ).click()

    # --- Dismiss the info popup that appears ---
    async with page.expect_popup(timeout=60000) as info_popup:
        await report_frame.locator("#FSDHL").click()
    info_page = await info_popup.value
    await info_page.close()

    # --- Fill in report filters ---
    await report_frame.locator("#FromServiceDate").click()
    await report_frame.locator("#FromServiceDate").fill(FROM_SERVICE_DATE)
    await report_frame.locator("#ToServiceDate").click()
    await report_frame.locator("#ToServiceDate").fill(TO_SERVICE_DATE)
    await report_frame.locator("input[name=\"freeIncludeOA\"]").check()
    await report_frame.locator("input[name=\"freeIncludePR\"]").check()

    # --- Run the report (opens in a new tab) ---
    async with page.expect_popup(timeout=90000) as report_popup:
        await report_frame.get_by_role("button", name="Run Report").click()
    report_page = await report_popup.value

    # --- Export to Excel ---
    # Wait for the report viewer's export button to actually render, force-click
    # it, then click the "Excel" link with a JS-click fallback in case the
    # normal Playwright click doesn't register.
    await report_page.wait_for_load_state()
    await report_page.wait_for_selector(
        "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImg",
        state="visible",
        timeout=240000,
    )
    await asyncio.sleep(180)  # give the report + export control time to fully render

    await report_page.click(
        "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImg",
        force=True,
    )

    excel_xpath = "//a[contains(., 'Excel') or contains(., 'EXCEL')]"

    async with report_page.expect_download(timeout=640000) as download_info:
        try:
            await report_page.click(f"xpath={excel_xpath}")
        except Exception:
            logger.warning("[%s] Excel link click failed, falling back to JS click", practice)
            await report_page.evaluate("""
                [...document.querySelectorAll('a')].find(a =>
                    a.innerText.toLowerCase().includes('excel')
                )?.click();
            """)

    download = await download_info.value

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = DOWNLOAD_DIR / f"pay_40_{practice}_report_{timestamp}.xlsx"
    await download.save_as(save_path)
    logger.info("[%s] Report saved to: %s", practice, save_path.resolve())

    await report_page.close()
    return save_path


async def process_practice(playwright: Playwright, practice: str, semaphore: asyncio.Semaphore):
    """Log in fresh for this practice, run the report, and download it.
    Bounded by the semaphore so at most CONCURRENCY practices run at once."""
    async with semaphore:
        browser = None
        try:
            logger.info("[%s] Logging in...", practice)
            browser, context, page = await login(playwright)
            save_path = await run_report_for_practice(page, practice)
            return practice, True, None, save_path
        except Exception as e:
            logger.error("[%s] FAILED: %s", practice, e, exc_info=True)
            return practice, False, str(e), None
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass


async def process_practices_with_retry(
    playwright: Playwright,
    practices: list[str],
    semaphore: asyncio.Semaphore,
) -> tuple[list[str], list[str], list[tuple[str, Path]]]:
    """
    Run every practice, and keep retrying any that failed (or never produced a
    download) in subsequent rounds until they succeed. A practice stops being
    retried once it either succeeds, or hits MAX_ATTEMPTS_PER_PRACTICE attempts
    (set MAX_ATTEMPTS_PER_PRACTICE=0 in .env for unlimited retries).

    Returns (succeeded, gave_up, practice_files).
    """
    remaining = list(practices)
    attempts = {p: 0 for p in practices}
    succeeded: list[str] = []
    gave_up: list[str] = []
    practice_files: list[tuple[str, Path]] = []

    round_num = 0
    while remaining:
        round_num += 1
        logger.info("=== Round %d: %d practice(s) to run ===", round_num, len(remaining))
        tasks = [
            asyncio.create_task(process_practice(playwright, practice, semaphore))
            for practice in remaining
        ]
        results = await asyncio.gather(*tasks)

        next_round = []
        for practice, ok, err, save_path in results:
            attempts[practice] += 1
            if ok and save_path is not None:
                succeeded.append(practice)
                practice_files.append((practice, save_path))
            else:
                unlimited = MAX_ATTEMPTS_PER_PRACTICE <= 0
                if unlimited or attempts[practice] < MAX_ATTEMPTS_PER_PRACTICE:
                    logger.warning(
                        "[%s] not downloaded yet (attempt %d%s), retrying...",
                        practice,
                        attempts[practice],
                        "" if unlimited else f"/{MAX_ATTEMPTS_PER_PRACTICE}",
                    )
                    next_round.append(practice)
                else:
                    logger.error(
                        "[%s] giving up after %d attempt(s): %s",
                        practice, attempts[practice], err,
                    )
                    gave_up.append(practice)
        remaining = next_round

    return succeeded, gave_up, practice_files


def load_and_clean_reports(practice_files: list[tuple[str, Path]]) -> pd.DataFrame | None:
    """
    Read every downloaded per-practice PAY 40 Excel file, clean/rename columns,
    enrich denial descriptions from the lookup template, and return a single
    combined dataframe with the final DB-ready columns.

    practice_files: list of (practice_code, file_path) tuples, e.g. as produced
    by the scrape step. Using the practice code we already know (rather than
    re-parsing it out of the filename) avoids fragile string-splitting.
    """
    if not practice_files:
        logger.warning("No successful downloads to process.")
        return None

    frames = []
    for practice, path in practice_files:
        try:
            data = pd.read_excel(path, sheet_name=REPORT_SHEET_NAME)
            # The real header row is embedded as the first data row; promote it.
            data.columns = data.iloc[0]
            data = data[1:].reset_index(drop=True)
            # Normalize column names (strips stray whitespace/newlines from the export).
            data.columns = [str(c).strip() for c in data.columns]
            data.insert(0, "Practice", practice)
            frames.append(data)
        except Exception as e:
            logger.error("[%s] Could not read %s: %s", practice, path.name, e, exc_info=True)

    if not frames:
        logger.warning("No readable files to process.")
        return None

    combined_data = pd.concat(frames, ignore_index=True)

    # --- Clean and rename columns ---
    combined_data['ERA Date'] = pd.to_datetime(combined_data['ERA Date']).dt.strftime('%Y-%m-%d')
    combined_data['DOS'] = pd.to_datetime(combined_data['DOS']).dt.strftime('%Y-%m-%d')
    combined_data['Date Of Remit'] = pd.to_datetime(combined_data['Date Of Remit']).dt.strftime('%Y-%m-%d')

    combined_data = combined_data.rename(columns=COLUMN_RENAME_MAP)

    # --- Enrich denial descriptions from lookup template ---
    lookup = pd.read_excel(DENIAL_CODE_TEMPLATE, sheet_name='Sheet1')
    merged_df = combined_data.merge(lookup, on='Adj_Code', how='left', suffixes=('', '_df2'))
    combined_data['ERAReason_Description'] = merged_df['ERAReason_Description_df2'].fillna('Others')
    combined_data['Adj_Description'] = merged_df['Adj_Description_df2'].fillna('Others')

    # --- Select final columns for the DB table ---
    denial_final = combined_data[FINAL_COLUMNS]

    logger.info("Processed %d rows from %d practice file(s)", len(denial_final), len(frames))
    return denial_final


def upload_to_database(df: pd.DataFrame) -> None:
    """
    Upload the cleaned dataframe to the configured MySQL table.

    If TRUNCATE_BEFORE_UPLOAD is enabled (the default), the target table is
    truncated first so this run's data replaces whatever was there before,
    instead of piling up on top of it via append.
    """
    if df is None or df.empty:
        logger.warning("Nothing to upload.")
        return

    if not all([DB_USER, DB_PASSWORD, DB_SERVER, DB_NAME]):
        raise RuntimeError(
            "Missing DB config. Set DB_USER, DB_PASSWORD, DB_SERVER, and DB_NAME "
            "environment variables (e.g. in a .env file)."
        )

    engine = create_engine(
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_SERVER}:{DB_PORT}/{DB_NAME}"
    )
    try:
        if TRUNCATE_BEFORE_UPLOAD:
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE {DB_TABLE}"))
            logger.info("Truncated table: %s", DB_TABLE)

        df.to_sql(DB_TABLE, engine, if_exists='append', index=False)
        logger.info("Done: %d row(s) uploaded to %s", len(df), DB_TABLE)
    finally:
        engine.dispose()


async def run(playwright: Playwright) -> None:
    if not USERNAME or not PASSWORD or not URL:
        raise RuntimeError(
            "Missing config. Set USERNAME, PASSWORD, and URL environment variables."
        )

    clear_download_dir()

    semaphore = asyncio.Semaphore(CONCURRENCY)

    succeeded, gave_up, practice_files = await process_practices_with_retry(
        playwright, PRACTICES, semaphore
    )

    logger.info("--- Summary ---")
    logger.info("Succeeded (%d): %s", len(succeeded), succeeded)
    logger.info("Gave up (%d): %s", len(gave_up), gave_up)

    # --- Clean, enrich, and upload every practice's downloaded report ---
    cleaned_df = load_and_clean_reports(practice_files)
    upload_to_database(cleaned_df)


async def main() -> None:
    try:
        async with async_playwright() as playwright:
            await run(playwright)
    except Exception:
        logger.critical("Run failed with an unhandled exception", exc_info=True)
        raise
    else:
        logger.info("Run completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())