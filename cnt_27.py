import asyncio
import io
import logging
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import Playwright, async_playwright
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.mime.text import MIMEText
from datetime import timedelta
load_dotenv()  # Load environment variables from .env file if present

# ============================
# LOGGING SETUP
# ============================
LOG_DIR = Path("./logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"cnt_27_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("cnt_27")

USERNAME = os.environ.get("USERNAME")
PASSWORD = os.environ.get("PASSWORD")
URL = os.environ.get("URL")

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT"))

# Sender credentials (pulled from .env)
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SENDER_EMAIL)

# Recipients - hardcoded list (edit as needed)
RECIPIENT_EMAILS = [
    "pradeep_kn@exdionhealth.com",
    "sushmitha_rakshith@exdionhealth.com",
    "ponneri_sunilkumar@exdionhealth.com",
    "sudhansu_sekhar@exdionhealth.com",
    "chirag_kr@exdionhealth.com",
    "hanudeepkumar_b@exdionhealth.com",
    "zeeshan_m@exdionhealth.com",
    "shivaraj_l@exdionhealth.com",
    "tatavarti_omkar@exdionhealth.com"
]

# Report date range (adjust as needed, or pull from env/args too)
TO_DATE   = datetime.today().strftime("%m/%d/%Y")
FROM_DATE = (datetime.today() - timedelta(days=8)).strftime("%m/%d/%Y")


DOWNLOAD_DIR = Path("./downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

PRACTICES = [
    "COMUC", "EGUC", "AUCLA", "GA037", "SACUC", "OK209", "IL203", "TX008", "IL204", "CA023", "SUPER", "TX124", "VA202", "CA025", "SD201", "SOMER", "OK203", "CO200", "NM202", "TCPFL", "BECAR", "NATO", "ABBY", "EAST", "ZNC08",
    "HI200", "QCARE", "TUCNM", "QUC", "WTRFT", "TX111", "ZMS01", "FOLSM", "WA201", "WY003", "AMSXP", "VIP", "LA204", "CKAQC", "LA205", "IL008", "KY201", "NC026", "MD204", "TX222", "PUCNA", "PHYUC", "NC205", "FL030", "FL020",
    "LFAC", "CA212", "GA205", "TX120", "OK204", "TX040", "STEEL", "WY007", "ZCA07", "WV200",
    "IN203", "TX119", "LA004", "SMOKE", "WFE", "HVNS", "IN204", "TX226", "SRC", "DAVIS", "LIFE", "BIGBR", "CA213", "FL222", "GFM", "IL009", "CO202", "NY201", "OK016", "PA022", "OH213", "AK201", "CA232",
    "CO204", "PA200", "PEACH", "TX212", "ACMA", "TN203", "PNUC", "IL210", "MS206", "CA228", "OK201", "IN206", "TX203", "MS019", "NE200", "SSM", "LONG", "OURUC",
    "MLUC", "LA006", "BAYOU", "IL205", "NM201", "TX239",
    "AZ202", "ZIL03", "FL213", "PA201", "OH009", "ZCA15", "GA206", "WA200", "TX229", "FL215", "OH025", "OH204", "TX240", "TX067", "TX244", "TX206", "MD203", "NE203", "AL024", "OH201", "CA221", "MS205",
    "CA208", "AL209", "CA230", "TX107", "MHLTH",
    "FL218", "CT007", "FL201", "PA202", "OH212", "TN205", "MS203", "GA207", "PILA", "CA235", "TN207", "MN203", "IL217", "IL221", "OH207", "LA209", "GA214", "PICNA", "TX254",
    "IN002", "TN011", "MO001", "OH206", "SC201", "CQCFP", "VA204", "LOWUC", "REDMD", "UCMC",
    "HCANC", "SUCC", "CRUC", "LA055", "PUCLA", "LA207", "EONE", "UCJ", "DNWIC", "NE005", "ICC", "AL204",
]

# How many practices to process concurrently. Each practice gets its own fresh
# login/browser session, and this many run side-by-side at once.
CONCURRENCY = 20

# How many times to retry a practice that hasn't produced a downloaded file yet,
# before giving up on it for this run. Set MAX_ATTEMPTS_PER_PRACTICE=0 in your
# .env for effectively unlimited retries (keeps retrying every failed practice
# forever until it downloads or you kill the process).
MAX_ATTEMPTS_PER_PRACTICE = int(os.environ.get("MAX_ATTEMPTS_PER_PRACTICE", 5))


async def login(playwright: Playwright):
    """Launch a fresh browser, log in, and navigate to the Reports section.
    Returns (browser, context, page)."""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(accept_downloads=True)
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


async def run_report_for_practice(page, practice: str) -> Path:
    """Run the PAY 40 report for a single practice (on an already-logged-in
    page), download the Excel export, and return the path it was saved to."""
    report_frame, nav_frame = get_frames(page)

    # --- Select subpractice and search for report ---
    await report_frame.locator("#subpracticeselect").select_option(practice)
    await asyncio.sleep(3)  # let the frame/report list reload after switching practice
    report_frame, nav_frame = get_frames(page)  # re-grab frames in case they reloaded

    await nav_frame.locator("#userSearch").click()
    await nav_frame.locator("#userSearch").fill("cnt_27")
    await nav_frame.locator("#userSearch").press("Enter")
    await asyncio.sleep(3)  # let the search results load

    await report_frame.get_by_text(
        "Log Book VisitsPractice, CNT"
    ).click()

    # --- Dismiss the info popup that appears ---
    async with page.expect_popup(timeout=80000) as info_popup:
        await report_frame.locator("#FSDHL").click()
    info_page = await info_popup.value
    await info_page.close()

    # --- Fill in report filters ---
    await report_frame.locator("#FromServiceDate").click()
    await report_frame.locator("#FromServiceDate").fill(FROM_DATE)
    await report_frame.locator("#ToServiceDate").click()
    await report_frame.locator("#ToServiceDate").fill(TO_DATE)
    await page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeunStatusListcheckall").click()
    await page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeStatusListcheck1").check()
    await page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeunArrivalStatuscheckall").click()
    await page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeArrivalStatuscheck1").check()

    # --- Run the report (opens in a new tab) ---
    async with page.expect_popup(timeout=90000) as report_popup:
        await report_frame.get_by_role("button", name="Run Report").click()
    report_page = await report_popup.value

    # --- Export to Excel ---
    await report_page.wait_for_load_state()
    await report_page.wait_for_selector(
        "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImg",
        state="visible",
        timeout=240000,
    )
    await asyncio.sleep(60)  # give the report + export control time to fully render

    await report_page.click(
        "#ReportViewerControl_ctl05_ctl04_ctl00_ButtonImg",
        force=True,
    )

    excel_xpath = "//a[contains(., 'Excel') or contains(., 'EXCEL')]"

    async with report_page.expect_download(timeout=360000) as download_info:
        try:
            await report_page.click(f"xpath={excel_xpath}")
        except Exception:
            logger.warning(f"[{practice}] Excel link click failed, falling back to JS click")
            await report_page.evaluate("""
                [...document.querySelectorAll('a')].find(a =>
                    a.innerText.toLowerCase().includes('excel')
                )?.click();
            """)

    download = await download_info.value

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = DOWNLOAD_DIR / f"cnt27_reports_{practice}_report_{timestamp}.xlsx"
    await download.save_as(save_path)
    logger.info(f"[{practice}] Report saved to: {save_path.resolve()}")

    await report_page.close()
    return save_path


async def process_practice(playwright: Playwright, practice: str, semaphore: asyncio.Semaphore):
    """Log in fresh for this practice, run the report, and download it.
    Bounded by the semaphore so at most CONCURRENCY practices run at once."""
    async with semaphore:
        browser = None
        try:
            logger.info(f"[{practice}] Logging in...")
            browser, context, page = await login(playwright)
            save_path = await run_report_for_practice(page, practice)
            return practice, True, None, save_path
        except Exception as e:
            logger.error(f"[{practice}] FAILED: {e}")
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
) -> tuple[list[str], list[str], list[Path]]:
    """
    Run every practice, and keep retrying any that failed (or never produced a
    download) in subsequent rounds until they succeed. A practice stops being
    retried once it either succeeds, or hits MAX_ATTEMPTS_PER_PRACTICE attempts
    (set MAX_ATTEMPTS_PER_PRACTICE=0 in .env for unlimited retries).

    Returns (succeeded, gave_up, saved_paths).
    """
    remaining = list(practices)
    attempts = {p: 0 for p in practices}
    succeeded: list[str] = []
    gave_up: list[str] = []
    saved_paths: list[Path] = []

    round_num = 0
    while remaining:
        round_num += 1
        logger.info(f"=== Round {round_num}: {len(remaining)} practice(s) to run ===")
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
                saved_paths.append(save_path)
            else:
                unlimited = MAX_ATTEMPTS_PER_PRACTICE <= 0
                if unlimited or attempts[practice] < MAX_ATTEMPTS_PER_PRACTICE:
                    logger.warning(
                        f"[{practice}] not downloaded yet "
                        f"(attempt {attempts[practice]}"
                        f"{'' if unlimited else f'/{MAX_ATTEMPTS_PER_PRACTICE}'}), retrying..."
                    )
                    next_round.append(practice)
                else:
                    logger.error(
                        f"[{practice}] giving up after {attempts[practice]} "
                        f"attempt(s): {err}"
                    )
                    gave_up.append(practice)
        remaining = next_round

    return succeeded, gave_up, saved_paths


def combine_reports(file_paths: list[Path]) -> tuple[Path | None, pd.DataFrame | None]:
    """Read every downloaded per-practice Excel file, tag each row with its
    practice code, and concatenate everything into a single combined
    workbook. Returns (combined_path, combined_df), or (None, None) if
    nothing could be combined."""
    if not file_paths:
        logger.warning("No successful downloads to combine.")
        return None, None

    frames = []
    for path in file_paths:
        try:
            # Practice code is embedded in the filename:
            # cnt27_reports_<PRACTICE>_report_<timestamp>.xlsx
            practice = path.stem.split("_")[2]
            df = pd.read_excel(path)
            df.insert(0, "Practice", practice)
            frames.append(df)
        except Exception as e:
            logger.error(f"Could not read {path.name} for combining: {e}")

    if not frames:
        logger.warning("No readable files to combine.")
        return None, None

    combined = pd.concat(frames, ignore_index=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    combined_path = DOWNLOAD_DIR / f"combined_report_{timestamp}.xlsx"
    combined.to_excel(combined_path, index=False, engine="openpyxl")

    logger.info(f"Combined report saved to: {combined_path.resolve()}")
    logger.info(f"Total rows combined: {len(combined)} from {len(frames)} practice file(s)")
    return combined_path, combined


# ============================
# EMAIL FUNCTION (ported from the PAY_30/CNT_27/PCD_38 script)
# ============================
def send_email_with_tables(
    tables_dict=None,
    attachments_data=None,
    sender_email=None,
    sender_password=None,
    recipient_emails=None,
    subject="CNT-27 Combined Report",
):
    # -----------------------------
    # Basic validation
    # -----------------------------
    if not sender_email or not sender_password:
        raise ValueError("Sender credentials are required")

    if not recipient_emails:
        raise ValueError("At least one recipient is required")

    if isinstance(recipient_emails, str):
        recipient_emails = [recipient_emails]

    # -----------------------------
    # Normalize attachments
    # -----------------------------
    attachments_dict = {}
    timestamp = pd.Timestamp.now().strftime("%Y%m%d")

    if attachments_data is not None:
        if isinstance(attachments_data, pd.DataFrame):
            attachments_dict = {f"data_{timestamp}.xlsx": attachments_data}

        elif isinstance(attachments_data, dict):
            for filename, df in attachments_data.items():
                filename = filename.replace(".xlsx", "") + ".xlsx"
                attachments_dict[filename] = df

        elif isinstance(attachments_data, list):
            for i, item in enumerate(attachments_data):
                if isinstance(item, tuple) and len(item) == 2:
                    filename, df = item
                    filename = filename.replace(".xlsx", "") + ".xlsx"
                    attachments_dict[filename] = df
                elif isinstance(item, pd.DataFrame):
                    filename = f"data_{i + 1}_{timestamp}.xlsx"
                    attachments_dict[filename] = item
                else:
                    raise ValueError("List must contain DataFrames or (filename, DataFrame) tuples")
        else:
            raise ValueError("attachments_data must be DataFrame, dict, or list")

    # -----------------------------
    # Email setup
    # -----------------------------
    msg = MIMEMultipart("alternative")
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipient_emails)
    msg["Subject"] = subject

    # -----------------------------
    # HTML Styling
    # -----------------------------
    html_style = """
    <style>
        table {border-collapse: collapse; width: 100%; margin-bottom: 30px; font-family: Arial;}
        th {background-color: #4CAF50; color: white; padding: 10px; border: 1px solid #ddd;}
        td {padding: 8px; border: 1px solid #ddd;}
        tr:nth-child(even) {background-color: #f2f2f2;}
        h2 {font-family: Arial;}
    </style>
    """

    # -----------------------------
    # Generate tables (optional)
    # -----------------------------
    tables_html = ""
    if tables_dict:
        for title, df in tables_dict.items():
            if df is not None and not df.empty:
                table_html = df.to_html(index=False, border=0, escape=False)
                tables_html += f"<h2>{title}</h2>{table_html}"

    # -----------------------------
    # Final HTML body
    # -----------------------------
    if tables_html:
        html_body = f"""
        <html>
        <head>{html_style}</head>
        <body>
            {tables_html}
            <p style="margin-top:20px; color:#666;">
                <i>Attached are detailed Excel files.</i>
            </p>
        </body>
        </html>
        """
    else:
        html_body = """
        <html>
        <body>
            <p>Please find the attached files.</p>
        </body>
        </html>
        """

    msg.attach(MIMEText(html_body, "html"))

    # -----------------------------
    # Attach Excel files
    # -----------------------------
    for filename, df in attachments_dict.items():
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)

        attachment = MIMEBase(
            "application",
            "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        attachment.set_payload(buffer.read())
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
        msg.attach(attachment)

    # -----------------------------
    # Send Email
    # -----------------------------
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        logger.info(f"Email sent to {', '.join(recipient_emails)}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def send_combined_report_email(
    combined_path: Path | None,
    combined_df: pd.DataFrame | None,
    succeeded: list[str],
    failed: list[str],
) -> None:
    """Build the summary table + combined-report attachment and send them
    with send_email_with_tables. If there is no combined report to attach,
    no email is sent at all."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning("Skipping email: SENDER_EMAIL or SENDER_PASSWORD not set.")
        return

    if not RECIPIENT_EMAILS:
        logger.warning("Skipping email: RECIPIENT_EMAILS is empty.")
        return

    # If there's no combined report to attach, don't send anything.
    if combined_path is None or combined_df is None:
        logger.warning("Skipping email: no combined report was generated, so there's nothing to send.")
        return

    # Summary table shown inline in the email body
    summary_df = pd.DataFrame(
        {
            "Practice": succeeded + failed,
            "Status": (["Succeeded"] * len(succeeded)) + (["Failed"] * len(failed)),
        }
    )

    # Combined report attached as an Excel file (built from the DataFrame,
    # not re-read off disk)
    attachments_data = {combined_path.name: combined_df}

    send_email_with_tables(
        attachments_data=attachments_data,
        sender_email=SENDER_EMAIL,
        sender_password=SENDER_PASSWORD,
        recipient_emails=RECIPIENT_EMAILS,
        subject=f"CNT-27 Combined Report - {datetime.now().strftime('%Y-%m-%d')}",
    )


async def run(playwright: Playwright) -> None:
    logger.info(f"Starting CNT-27 run. Log file: {LOG_FILE.resolve()}")
    logger.info(f"Date range: {FROM_DATE} to {TO_DATE}")
    logger.info(f"Practices to process ({len(PRACTICES)}): {PRACTICES}")

    if not USERNAME or not PASSWORD or not URL:
        raise RuntimeError(
            "Missing config. Set USERNAME, PASSWORD, and URL environment variables."
        )

    semaphore = asyncio.Semaphore(CONCURRENCY)

    succeeded, gave_up, saved_paths = await process_practices_with_retry(
        playwright, PRACTICES, semaphore
    )

    logger.info("--- Summary ---")
    logger.info(f"Succeeded ({len(succeeded)}): {succeeded}")
    logger.info(f"Gave up ({len(gave_up)}): {gave_up}")

    # --- Combine every practice's downloaded report into one workbook ---
    combined_path, combined_df = combine_reports(saved_paths)

    # --- Email the combined report (HTML summary table + xlsx attachment).
    # If nothing was combined, send_combined_report_email will skip sending. ---
    send_combined_report_email(combined_path, combined_df, succeeded, gave_up)


async def main() -> None:
    try:
        async with async_playwright() as playwright:
            await run(playwright)
    except Exception:
        logger.exception("Run failed with an unhandled exception.")
        raise
    finally:
        logger.info(f"Run finished. Full log: {LOG_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())