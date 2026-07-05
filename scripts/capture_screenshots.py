#!/usr/bin/env python3
"""Capture public-safe README screenshots from a local preview server."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]


PAGES = [
    ("overview", "/webapp/index.html?view=overview", "overview"),
    ("listening", "/webapp/index.html?test=celpip1-test1&section=listening&part=1", "listening"),
    ("reading", "/webapp/index.html?test=celpip1-test1&section=reading&part=1", "reading"),
    ("writing", "/webapp/index.html?test=celpip1-test1&section=writing&part=1", "writing"),
    ("speaking", "/webapp/index.html?test=celpip1-test1&section=speaking&part=1&intro=1", "speaking"),
    ("history", "/webapp/index.html?view=history", "history"),
]


def chrome_options() -> Options:
    options = Options()
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--hide-scrollbars")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    return options


def driver_service(driver_path: Path | None = None) -> Service:
    if driver_path:
        return Service(executable_path=str(driver_path))
    return Service()


def prepare_page(driver, wait, mode: str) -> None:
    if mode == "overview":
        wait.until(EC.presence_of_element_located((By.ID, "overviewView")))
        return

    if mode == "history":
        wait.until(EC.presence_of_element_located((By.ID, "historyView")))
        return

    if mode == "listening":
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "listening-gate")))
        start = wait.until(EC.element_to_be_clickable((By.ID, "startPassageBtn")))
        start.click()
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "question-card")))
        return

    if mode == "reading":
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "part-media")))
        return

    if mode == "writing":
        textarea = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "long-response")))
        textarea.click()
        textarea.send_keys(
            "Dear Building Manager,\n\n"
            "I am writing about the planned bicycle storage update. The current room is crowded, "
            "so adding labelled racks and better lighting would make it easier for residents to use.\n\n"
            "Sincerely,\nA resident"
        )
        return

    if mode == "speaking":
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "section-start-panel")))
        wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "start-speaking-section"))).click()
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "question-card")))
        time.sleep(0.5)
        return


def capture(base_url: str, output_dir: Path, driver_path: Path | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SE_CACHE_PATH", str(ROOT / ".cache" / "selenium"))
    driver = webdriver.Chrome(service=driver_service(driver_path), options=chrome_options())
    wait = WebDriverWait(driver, 12)
    try:
        for name, path, mode in PAGES:
            driver.get(f"{base_url.rstrip('/')}{path}")
            wait.until(lambda browser: browser.execute_script("return document.readyState") == "complete")
            prepare_page(driver, wait, mode)
            driver.save_screenshot(str(output_dir / f"{name}.png"))
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8790", help="Preview server base URL")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "screenshots", help="Screenshot output directory")
    parser.add_argument("--driver", type=Path, help="Optional ChromeDriver path; defaults to Selenium Manager")
    args = parser.parse_args()

    capture(args.base_url, args.output_dir, args.driver)
    print(f"Captured screenshots in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
