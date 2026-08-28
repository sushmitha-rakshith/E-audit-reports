"""
ERA File Downloader & S3 Uploader
==================================

Logs into the Practice Velocity portal, navigates to the Documents section,
selects the ERA doc type/subtype, applies a date range derived from the
current weekday, downloads every ERA file found, and uploads the results
to S3.

Setup
-----
    pip install playwright boto3
    playwright install chromium

Environment variables (recommended over hardcoding credentials):
    PV_USERNAME   - portal login
    PV_PASSWORD   - portal password
    S3_BUCKET     - target S3 bucket name
    S3_PREFIX     - key prefix inside the bucket (default: "era-files")
    AWS credentials picked up normally by boto3 (env vars, ~/.aws/credentials, etc.)

Adding a new practice
----------------------
Just add its dropdown value to the PRACTICES list below.
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import boto3
from playwright.sync_api import Playwright, sync_playwright, Page


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

LOGIN_URL = "https://pvpm.practicevelocity.com/26_8/loginpage.aspx?ReturnUrl=%2f26_8%2f"
DOC_LIST_URL = "https://pvpm.practicevelocity.com/26_8/DocList.aspx"

USERNAME = os.environ.get("PV_USERNAME", "")
PASSWORD = os.environ.get("PV_PASSWORD", "")

DOC_TYPE_VALUE = "4"        # dropdown value for the ERA/EOB doc type
DOC_SUBTYPE_VALUE = "ERA"   # dropdown value for the ERA subtype

OUTPUT_DIR = Path("output")

S3_BUCKET = os.environ.get("S3_BUCKET", "exdionh-dev-cash")
S3_PREFIX = os.environ.get("S3_PREFIX", "CQCFP")  # default prefix inside the bucket

# List of practices to process (values used by #ddlPractice).
# Add a new practice by adding a line here -- nothing else needs to change.
PRACTICES = [
    "CQCFP",
     "LOWUC",
]


# ---------------------------------------------------------------------------
# DATE RANGE LOGIC (as specified)
# ---------------------------------------------------------------------------

def get_date_range() -> tuple[str, str]:
    """Returns (from_date, to_date) as MM/DD/YYYY strings based on weekday."""
    today = datetime.now()

    weekday_offsets = {
        0: (6, 3),  # Monday    -> window: 6 days back through 3 days back
        1: (3, 3),  # Tuesday   -> single date, 3 days back
        2: (3, 3),  # Wednesday -> single date, 3 days back
        3: (3, 3),  # Thursday  -> single date, 3 days back
        4: (3, 3),  # Friday    -> single date, 3 days back
    }
    from_days_back, to_days_back = weekday_offsets.get(today.weekday(), (1, 1))

    from_date = today - timedelta(days=from_days_back)
    to_date = today - timedelta(days=to_days_back)
    return from_date.strftime("%m/%d/%Y"), to_date.strftime("%m/%d/%Y")


# ---------------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------------

def login(page: Page) -> None:
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "Set PV_USERNAME and PV_PASSWORD environment variables before running."
        )

    page.goto(LOGIN_URL)
    page.locator("#txtLogin").click()
    page.locator("#txtLogin").fill(USERNAME)
    page.get_by_role("button", name="Next").click()
    page.locator("#txtPassword").fill(PASSWORD)
    page.get_by_role("button", name="Login").click()
    page.get_by_role("button", name="Admin2").click()
    page.get_by_role("link", name="Documents").click()


# ---------------------------------------------------------------------------
# DOWNLOAD ERA FILES FOR ONE PRACTICE
# ---------------------------------------------------------------------------

def download_era_files_for_practice(
    page: Page, practice_code: str, from_date: str, to_date: str
) -> list[Path]:
    downloaded_files: list[Path] = []

    practice_output_dir = OUTPUT_DIR / practice_code / datetime.now().strftime("%Y%m%d")
    practice_output_dir.mkdir(parents=True, exist_ok=True)

    # Select practice
    page.goto(DOC_LIST_URL)
    page.locator("#ddlPractice").select_option(practice_code)

    # Select doc type
    page.goto(DOC_LIST_URL)
    page.locator("#ddlType").select_option(DOC_TYPE_VALUE)

    # Select subtype + date range
    page.goto(DOC_LIST_URL)
    page.locator("#ddlSubtype").select_option(DOC_SUBTYPE_VALUE)
    page.locator("#txtFromDate").click()
    page.locator("#txtFromDate").fill(from_date)
    page.locator("#txtToDate").click()
    page.locator("#txtToDate").fill(to_date)
    page.get_by_role("button", name="Refresh").click()
    page.wait_for_load_state("networkidle")

    # NOTE: adjust this selector to match the actual results grid on your
    # DocList.aspx page. It should resolve to every clickable ERA document
    # link in the table (e.g. "SB810.225405.20260802.").
    doc_links = page.locator("table#gvDocList a, a[id*='lnkDoc'], a[id*='lnkFile']")
    count = doc_links.count()

    if count == 0:
        print(f"[{practice_code}] No ERA documents found for {from_date} - {to_date}")
        return downloaded_files

    for i in range(count):
        link = doc_links.nth(i)
        link_text = (link.inner_text() or f"doc_{i}").strip()
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", link_text)
        if not safe_name.lower().endswith(".txt"):
            safe_name += ".txt"
        target_path = practice_output_dir / safe_name

        # Case 1: click triggers a native file download
        try:
            with page.expect_download(timeout=5000) as download_info:
                link.click()
            download = download_info.value
            download.save_as(str(target_path))
            downloaded_files.append(target_path)
            print(f"[{practice_code}] Downloaded (native): {target_path}")
            continue
        except Exception:
            pass

        # Case 2: click opens a new tab containing the ERA text
        try:
            with page.expect_popup(timeout=8000) as popup_info:
                link.click()
            popup = popup_info.value
            popup.wait_for_load_state("networkidle")

            pre = popup.locator("pre").first
            text = pre.inner_text() if pre.count() > 0 else popup.locator("body").inner_text()

            target_path.write_text(text, encoding="utf-8")
            downloaded_files.append(target_path)
            print(f"[{practice_code}] Downloaded (popup text): {target_path}")
            popup.close()
        except Exception as e:
            print(f"[{practice_code}] Failed to download '{link_text}': {e}")

    return downloaded_files


# ---------------------------------------------------------------------------
# S3 UPLOAD
# ---------------------------------------------------------------------------

def upload_to_s3(files: list[Path], practice_code: str) -> None:
    if not files:
        return

    s3 = boto3.client("s3")
    date_str = datetime.now().strftime("%Y%m%d")

    for file_path in files:
        key = f"{S3_PREFIX}/{practice_code}/{date_str}/{file_path.name}"
        s3.upload_file(str(file_path), S3_BUCKET, key)
        print(f"Uploaded to s3://{S3_BUCKET}/{key}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    login(page)

    from_date, to_date = get_date_range()
    print(f"Fetching ERA files for date range: {from_date} - {to_date}")

    for practice_code in PRACTICES:
        print(f"\n=== Processing practice: {practice_code} ===")
        files = download_era_files_for_practice(page, practice_code, from_date, to_date)
        upload_to_s3(files, practice_code)

    page.close()
    context.close()
    browser.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright)