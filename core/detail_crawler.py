import re
import time
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


URL = "https://batdongsan.com.vn/ban-nha-biet-thu-lien-ke-duong-tinh-lo-824-xa-an-thanh-3-khu-do-thi-waterpoint/6-8-ty-toan-nhe-nhang-trong-3-nam-so-huu-villa-don-lap-vip-tai-the-pearl-kdt-waterpoint-pr44336055"
STATE_DIR = Path("browser_state")
USER_DATA_DIR = STATE_DIR / "chrome_profile"

PHONE_BUTTON_SELECTORS = [
    "[lead-tracking-id='lead-phone-ldp']",
    ".js__phone",
    "[class*='phoneEvent']",
    "[class*='btn-phone-icon']",
    "[class*='phone-event']",
]

PHONE_ATTRIBUTE_NAMES = (
    "mobile",
    "data-phone",
    "data-mobile",
    "data-full-phone",
    "href",
    "aria-label",
    "title",
)

SECURITY_PAGE_MARKERS = (
    "performing security verification",
    "verifying you are not a bot",
    "just a moment",
)

LISTING_READY_SELECTORS = [
    "h1",
    "[lead-tracking-id='lead-phone-ldp']",
    ".js__phone",
]


def log(message: str) -> None:
    print(f"[detail_crawler] {message}", flush=True)


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None

    phone = phone.replace("tel:", "").strip()
    phone = re.sub(r"[^\d+]", "", phone)

    if phone.startswith("+84"):
        phone = "0" + phone[3:]
    elif phone.startswith("84") and len(phone) == 11:
        phone = "0" + phone[2:]

    return phone if re.fullmatch(r"0\d{9}", phone) else None


def extract_phone_from_text(text: str | None) -> str | None:
    if not text:
        return None

    match = re.search(r"((?:\+84|84|0)(?:3|5|7|8|9)\d(?:[\s.\-]?\d){7})", text)
    if not match:
        return None

    return normalize_phone(match.group(1))


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


def get_text(driver: webdriver.Chrome, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if not element.is_displayed():
                    continue
                text = clean_text(element.text)
                if text:
                    return text
        except WebDriverException:
            pass

    return None


def is_security_verification_page(driver: webdriver.Chrome) -> bool:
    try:
        title = (driver.title or "").lower()
    except WebDriverException:
        title = ""

    try:
        body = get_body_text(driver).lower()
    except WebDriverException:
        body = ""

    haystack = f"{title}\n{body}"
    return any(marker in haystack for marker in SECURITY_PAGE_MARKERS)


def is_listing_content_ready(driver: webdriver.Chrome) -> bool:
    try:
        if get_text(driver, ["h1"]):
            return True
    except Exception:
        pass

    try:
        for selector in LISTING_READY_SELECTORS:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    text = clean_text(element.text)
                    if text:
                        return True
        return False
    except WebDriverException:
        return False


def wait_for_listing_ready(driver: webdriver.Chrome) -> None:
    log("Dang cho trang vuot qua security verification neu co.")
    deadline = time.time() + 25
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if is_listing_content_ready(driver):
            log("Da thay noi dung listing, tiep tuc crawl.")
            return
        if not is_security_verification_page(driver):
            log("Khong con security verification, tiep tuc crawl.")
            return
        if attempt % 5 == 0:
            try:
                current_title = driver.title
            except WebDriverException:
                current_title = ""
            log(f"Van dang cho verification. title={current_title!r}")
        time.sleep(1.5)

    log("Trang dang o man hinh security verification.")
    log("Hay hoan tat xac minh trong cua so Chrome, sau do nhan Enter de tiep tuc crawl.")
    input()

    deadline = time.time() + 30
    while time.time() < deadline:
        if is_listing_content_ready(driver):
            return
        if not is_security_verification_page(driver):
            return
        time.sleep(1)

    raise RuntimeError("Khong vuot qua duoc security verification page.")


def dismiss_cookie_banner(driver: webdriver.Chrome) -> None:
    xpaths = [
        "//button[normalize-space()='Đồng ý']",
        "//button[normalize-space()='Dong y']",
        "//button[normalize-space()='Đóng']",
        "//button[normalize-space()='Dong']",
    ]
    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            for element in elements:
                if not element.is_displayed():
                    continue
                driver.execute_script("arguments[0].click();", element)
                log("Da dong cookie banner.")
                time.sleep(0.5)
                return
        except WebDriverException:
            pass


def is_logged_in(driver: webdriver.Chrome) -> bool:
    try:
        login_buttons = driver.find_elements(
            By.XPATH,
            "//a[normalize-space()='Đăng nhập'] | //button[normalize-space()='Đăng nhập']",
        )
        for button in login_buttons:
            if button.is_displayed():
                return False
    except WebDriverException:
        pass

    return True


def get_body_text(driver: webdriver.Chrome) -> str:
    try:
        return driver.execute_script(
            "return (document.body && (document.body.innerText || document.body.textContent)) || '';"
        )
    except WebDriverException:
        return ""


def get_labeled_value(driver: webdriver.Chrome, labels: list[str]) -> str | None:
    try:
        body_text = get_body_text(driver)
        for label in labels:
            match = re.search(rf"{re.escape(label)}\s+([^\n]+)", body_text, re.IGNORECASE)
            if match:
                return clean_text(match.group(1))
    except WebDriverException:
        pass

    return None


def get_title(driver: webdriver.Chrome) -> str | None:
    return get_text(driver, ["h1"])


def get_location(driver: webdriver.Chrome) -> str | None:
    location = get_text(
        driver,
        [
            "[class*='js__pr-address']",
            "[class*='re__pr-short-info'] [class*='address']",
        ],
    )
    if location:
        return location

    try:
        siblings = driver.execute_script(
            """
            const h1 = document.querySelector('h1');
            if (!h1) return [];
            const out = [];
            let node = h1.nextElementSibling;
            while (node && out.length < 10) {
                out.push((node.innerText || node.textContent || '').trim());
                node = node.nextElementSibling;
            }
            return out;
            """
        )
        for candidate in siblings:
            text = clean_text(candidate)
            if not text or len(text) > 220:
                continue
            if "," in text and any(city in text for city in ("Hà Nội", "Hồ Chí Minh", "Đà Nẵng")):
                return text
    except WebDriverException:
        pass

    return None


def first_visible_element(driver: webdriver.Chrome, selectors: list[str]):
    for selector in selectors:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    return element
        except WebDriverException:
            pass
    return None


def extract_phone_from_element(element) -> str | None:
    for attr_name in PHONE_ATTRIBUTE_NAMES:
        try:
            value = element.get_attribute(attr_name)
            phone = normalize_phone(value) or extract_phone_from_text(value)
            if phone:
                return phone
        except WebDriverException:
            pass

    try:
        text = clean_text(element.text)
        phone = extract_phone_from_text(text)
        if phone:
            return phone
    except WebDriverException:
        pass

    try:
        attributes = element.parent.execute_script(
            """
            const element = arguments[0];
            return Object.fromEntries([...element.attributes].map(attr => [attr.name, attr.value]));
            """,
            element,
        )
        for value in attributes.values():
            if not isinstance(value, str):
                continue
            phone = normalize_phone(value) or extract_phone_from_text(value)
            if phone:
                return phone
    except WebDriverException:
        pass

    return None


def wait_for_phone_state(element) -> None:
    for _ in range(10):
        phone = extract_phone_from_element(element)
        if phone:
            return

        try:
            data_click = element.get_attribute("data-click")
            class_name = element.get_attribute("class") or ""
            text = (clean_text(element.text) or "").lower()
            if data_click == "true" or "showHotline" in class_name or "sao ch" in text or "copy" in text:
                return
        except WebDriverException:
            pass

        time.sleep(0.3)


def extract_phone_from_ancestors(element) -> str | None:
    driver = element.parent
    for level in range(1, 5):
        try:
            container = driver.execute_script(
                """
                let node = arguments[0];
                let level = arguments[1];
                while (node && level > 0) {
                    node = node.parentElement;
                    level--;
                }
                return node;
                """,
                element,
                level,
            )
            if container is None:
                continue
            text = clean_text(container.text)
            phone = extract_phone_from_text(text)
            if phone:
                return phone
        except WebDriverException:
            pass

    return None


def click_show_phone_and_get(driver: webdriver.Chrome) -> str | None:
    log("Dang tim nut hien so.")
    element = first_visible_element(driver, PHONE_BUTTON_SELECTORS)
    if element is None:
        log("Khong tim thay nut hien so.")
        return None

    phone = extract_phone_from_element(element)
    if phone:
        log("Da lay duoc so dien thoai truoc khi click.")
        return phone

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.5)
        try:
            element.click()
        except WebDriverException:
            driver.execute_script("arguments[0].click();", element)
    except WebDriverException:
        log("Click nut hien so that bai.")
        return None

    log("Da click nut hien so, dang cho trang thai cap nhat.")
    wait_for_phone_state(element)

    phone = extract_phone_from_element(element)
    if phone:
        log("Da lay duoc so dien thoai sau khi click.")
        return phone

    log("Khong thay so tren button, thu doc tu cac the cha.")
    return extract_phone_from_ancestors(element)


def ensure_authenticated_listing_page(driver: webdriver.Chrome, url: str) -> None:
    log(f"Mo URL: {url}")
    driver.get(url)
    wait_for_listing_ready(driver)
    dismiss_cookie_banner(driver)

    if not is_logged_in(driver):
        raise RuntimeError(
            "Chrome profile hien tai chua dang nhap Batdongsan. "
            "Hay chay `uv run -m core.login_batdongsan` bang cung browser profile roi thu lai."
        )


def extract_detail_fields(driver: webdriver.Chrome, url: str) -> dict:
    log("Trang san sang. Bat dau doc thong tin tin dang.")
    time.sleep(2.5)

    log("Dang doc title.")
    title = get_title(driver)
    log(f"Title: {title!r}")

    log("Dang doc location.")
    location = get_location(driver)
    log(f"Location: {location!r}")

    log("Dang doc price.")
    price = get_labeled_value(driver, ["Khoảng giá", "Mức giá"])
    log(f"Price: {price!r}")

    log("Dang doc area.")
    area = get_labeled_value(driver, ["Diện tích"])
    log(f"Area: {area!r}")

    log("Dang doc phone.")
    phone = click_show_phone_and_get(driver)
    log(f"Ket qua tam thoi: title={title!r}, phone={phone!r}")

    if price and "~" in price:
        price = price.split("~")[0].strip()

    return {
        "title": title,
        "area": area,
        "location": location,
        "phone": phone,
        "price": price,
        "url": url,
    }


def crawl_detail_with_driver(driver: webdriver.Chrome, url: str) -> dict:
    ensure_authenticated_listing_page(driver, url)
    return extract_detail_fields(driver, url)


def crawl_detail(url: str) -> dict:
    log("Khoi tao Chrome.")
    driver = build_driver()
    try:
        return crawl_detail_with_driver(driver, url)
    finally:
        driver.quit()


if __name__ == "__main__":
    item = crawl_detail(URL)
    print(item)
    pd.DataFrame([item]).to_csv("batdongsan_detail.csv", index=False, encoding="utf-8-sig")
