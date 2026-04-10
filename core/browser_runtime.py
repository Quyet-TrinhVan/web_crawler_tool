import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


STATE_DIR = Path("browser_state")
WINDOW_SIZE = os.getenv("CHROME_WINDOW_SIZE", "1440,1080")


def _running_in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _profile_root() -> Path:
    profile_namespace = os.getenv("BROWSER_PROFILE_NAMESPACE")
    if profile_namespace:
        return STATE_DIR / profile_namespace
    if _running_in_docker():
        return STATE_DIR / "docker"
    return STATE_DIR


PROFILE_ROOT = _profile_root()
BATDONGSAN_USER_DATA_DIR = PROFILE_ROOT / "chrome_profile"
NHATOT_USER_DATA_DIR = PROFILE_ROOT / "nhatot_chrome_profile"

PROFILE_DIRS = {
    "batdongsan.com": BATDONGSAN_USER_DATA_DIR,
    "nhatot.com": NHATOT_USER_DATA_DIR,
}


def get_profile_dir(source: str) -> Path:
    try:
        return PROFILE_DIRS[source]
    except KeyError as exc:
        raise ValueError(f"source khong ho tro browser profile: {source}") from exc


def get_novnc_url(host: str = "localhost", scheme: str = "http") -> str:
    configured = os.getenv("NOVNC_PUBLIC_URL")
    if configured:
        return configured

    port = os.getenv("NOVNC_PORT", "6080")
    return f"{scheme}://{host}:{port}/vnc.html?autoconnect=1&resize=remote"


def cleanup_profile_locks(user_data_dir: Path) -> None:
    # Chromium writes process-singleton lock files that are not portable
    # across host/container boundaries. In this app we allow only one active
    # browser per profile, so stale singleton files can be removed safely.
    for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
        target = user_data_dir / name
        try:
            if target.exists() or target.is_symlink():
                target.unlink()
        except FileNotFoundError:
            pass


def build_driver(user_data_dir: Path, *, page_load_strategy: str = "normal") -> webdriver.Chrome:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    user_data_dir.mkdir(parents=True, exist_ok=True)
    cleanup_profile_locks(user_data_dir)

    options = Options()
    options.page_load_strategy = page_load_strategy
    options.add_argument(f"--user-data-dir={user_data_dir.resolve()}")
    options.add_argument("--profile-directory=Default")
    options.add_argument(f"--window-size={WINDOW_SIZE}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=vi-VN")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary

    service = Service(executable_path=os.getenv("CHROMEDRIVER_PATH")) if os.getenv("CHROMEDRIVER_PATH") else Service()
    driver = webdriver.Chrome(service=service, options=options)
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
