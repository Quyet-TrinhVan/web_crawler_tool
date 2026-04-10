from pathlib import Path
import time

from selenium import webdriver

from core.browser_runtime import BATDONGSAN_USER_DATA_DIR, STATE_DIR, build_driver as build_browser_driver


HOME_URL = "https://batdongsan.com.vn/"
USER_DATA_DIR = BATDONGSAN_USER_DATA_DIR


def build_driver() -> webdriver.Chrome:
    return build_browser_driver(USER_DATA_DIR)


def save_login_profile() -> Path:
    driver = build_driver()
    try:
        driver.get(HOME_URL)
        print("Chrome da mo voi persistent profile.")
        print("Hay vuot qua Cloudflare, dang nhap va hoan tat xac minh so dien thoai.")
        print("Khi xong, nhan Ctrl+C de dong browser va luu session.")
        while True:
            time.sleep(1)
    finally:
        driver.quit()

    return USER_DATA_DIR


if __name__ == "__main__":
    path = save_login_profile()
    print(f"Da luu Chrome profile tai: {path}")
