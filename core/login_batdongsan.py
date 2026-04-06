from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


HOME_URL = "https://batdongsan.com.vn/"
STATE_DIR = Path("browser_state")
USER_DATA_DIR = STATE_DIR / "chrome_profile"


def build_driver() -> webdriver.Chrome:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.add_argument(f"--user-data-dir={USER_DATA_DIR.resolve()}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """
        },
    )
    return driver


def save_login_profile() -> Path:
    driver = build_driver()
    try:
        driver.get(HOME_URL)
        print("Chrome da mo voi persistent profile.")
        print("Hay vuot qua Cloudflare, dang nhap va hoan tat xac minh so dien thoai.")
        print("Khi xong, nhan Enter tai terminal de dong browser.")
        input()
    finally:
        driver.quit()

    return USER_DATA_DIR


if __name__ == "__main__":
    path = save_login_profile()
    print(f"Da luu Chrome profile tai: {path}")
