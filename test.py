import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://pvpm.practicevelocity.com/26_8/loginpage.aspx?ReturnUrl=%2f26_8%2f")
    page.locator("#txtLogin").click()
    page.locator("#txtLogin").fill("SHIVARAJ@ALL")
    page.get_by_role("button", name="Next").click()
    page.locator("#txtPassword").click()
    page.locator("#txtPassword").click(modifiers=["ControlOrMeta"])
    page.locator("#txtPassword").fill("Experity!9")
    page.get_by_role("button", name="Login").click()
    page.get_by_role("link", name="Reports").click()
    page.get_by_role("button", name="Admin2").click()
    page.get_by_role("link", name="Reports").click()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#subpracticeselect").select_option("ABBY")
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"NavFrame\"]").content_frame.locator("#userSearch").click()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"NavFrame\"]").content_frame.locator("#userSearch").fill("cnt_27")
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"NavFrame\"]").content_frame.get_by_text("Do Search").click()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.get_by_text("Log Book VisitsPractice, CNT").click()
    with page.expect_popup() as page1_info:
        page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#FSDHL").click()
    page1 = page1_info.value
    page1.get_by_role("cell", name="11").click()
    page1.close()
    with page.expect_popup() as page2_info:
        page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#TSDHL").click()
    page2 = page2_info.value
    page2.get_by_role("link", name="18").click()
    page2.close()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeunStatusListcheckall").click()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeStatusListcheck1").check()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeunArrivalStatuscheckall").click()
    page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.locator("#freeArrivalStatuscheck1").check()
    with page.expect_popup() as page3_info:
        page.locator("iframe[name=\"reportMainWindow\"]").content_frame.locator("frame[name=\"PVRC_MainStage\"]").content_frame.get_by_role("button", name="Run Report").click()
    page3 = page3_info.value
    page3.close()
    page.close()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
