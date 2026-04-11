import os
import re
import time
from collections import Counter

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from core.crawl_control import raise_if_stop_requested, sleep_with_stop
from core.browser_runtime import BATDONGSAN_USER_DATA_DIR
from core.browser_runtime import build_driver as build_browser_driver
from core.browser_session import wait_for_user_action


URL = "https://batdongsan.com.vn/ban-nha-biet-thu-lien-ke-duong-tinh-lo-824-xa-an-thanh-3-khu-do-thi-waterpoint/6-8-ty-toan-nhe-nhang-trong-3-nam-so-huu-villa-don-lap-vip-tai-the-pearl-kdt-waterpoint-pr44336055"
USER_DATA_DIR = BATDONGSAN_USER_DATA_DIR
MANUAL_ACTION_TIMEOUT_SECONDS = float(os.getenv("MANUAL_ACTION_TIMEOUT_SECONDS", "900"))

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
MOBILE_PHONE_PATTERN = r"0(?:3[2-9]|5[25689]|7[06789]|8[1-9]|9[0-46-9])\d{7}"


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

    return phone if re.fullmatch(MOBILE_PHONE_PATTERN, phone) else None


def extract_phone_from_text(text: str | None) -> str | None:
    if not text:
        return None

    match = re.search(r"((?:\+84|84|0)(?:3|5|7|8|9)\d(?:[\s.\-]?\d){7})", text)
    if not match:
        return None

    return normalize_phone(match.group(1))


def extract_all_phones_from_text(text: str | None) -> list[str]:
    if not text:
        return []

    matches = re.findall(r"((?:\+84|84|0)(?:3|5|7|8|9)\d(?:[\s.\-]?\d){7})", text)
    phones: list[str] = []
    for match in matches:
        phone = normalize_phone(match)
        if phone:
            phones.append(phone)
    return phones


def extract_phone_from_page(driver: webdriver.Chrome, *, include_page_source: bool = False) -> str | None:
    candidates: list[str] = []
    candidates.extend(extract_all_phones_from_text(get_body_text(driver)))
    if include_page_source:
        try:
            candidates.extend(extract_all_phones_from_text(driver.page_source))
        except WebDriverException:
            pass

    if not candidates:
        return None

    return Counter(candidates).most_common(1)[0][0]


def build_driver() -> webdriver.Chrome:
    return build_browser_driver(USER_DATA_DIR)


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
        sleep_with_stop(1.5)

    log("Trang dang o man hinh security verification.")
    log("Hay hoan tat xac minh trong browser/noVNC, sau do bam nut tiep tuc tren Web UI.")
    wait_for_user_action(
        source="batdongsan.com",
        mode="verification",
        message="Batdongsan dang cho ban hoan tat security verification trong browser/noVNC.",
        timeout_seconds=MANUAL_ACTION_TIMEOUT_SECONDS,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        if is_listing_content_ready(driver):
            return
        if not is_security_verification_page(driver):
            return
        sleep_with_stop(1)

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
                sleep_with_stop(0.5)
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


def visible_elements(driver: webdriver.Chrome, selectors: list[str], max_count: int = 5) -> list:
    elements: list = []
    seen_ids: set[str] = set()

    for selector in selectors:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if not element.is_displayed():
                    continue
                if element.id in seen_ids:
                    continue
                seen_ids.add(element.id)
                elements.append(element)
                if len(elements) >= max_count:
                    return elements
        except WebDriverException:
            pass

    return elements


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


def text_looks_like_masked_phone(text: str | None) -> bool:
    normalized = clean_text(text)
    if not normalized:
        return False
    return "*" in normalized or "hien so" in normalized.lower()


def wait_for_phone_state(element) -> None:
    for _ in range(10):
        raise_if_stop_requested()
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

        sleep_with_stop(0.3)


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
    elements = visible_elements(driver, PHONE_BUTTON_SELECTORS, max_count=5)
    if not elements:
        log("Khong tim thay nut hien so. Thu fallback tu noi dung dang hien tren trang.")
        return extract_phone_from_page(driver)

    for idx, element in enumerate(elements, start=1):
        element_text = None
        try:
            element_text = clean_text(element.text)
        except WebDriverException:
            pass

        phone = extract_phone_from_element(element)
        if phone and not text_looks_like_masked_phone(element_text):
            log("Da lay duoc so dien thoai truoc khi click.")
            return phone

        try:
            log(f"Thu click nut hien so {idx}/{len(elements)}.")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            sleep_with_stop(0.5)
            try:
                element.click()
            except WebDriverException:
                driver.execute_script("arguments[0].click();", element)
        except WebDriverException:
            continue

        log("Da click nut hien so, dang cho trang thai cap nhat.")
        wait_for_phone_state(element)

        phone = extract_phone_from_element(element)
        if phone:
            log("Da lay duoc so dien thoai sau khi click.")
            return phone

        phone = extract_phone_from_ancestors(element)
        if phone:
            log("Da lay duoc so dien thoai tu the cha sau khi click.")
            return phone

        phone = extract_phone_from_page(driver)
        if phone:
            log("Da lay duoc so dien thoai tu noi dung trang sau khi click.")
            return phone

    log("Khong lay duoc so tren button, thu fallback doc toan trang dang hien.")
    return extract_phone_from_page(driver)


def ensure_authenticated_listing_page(driver: webdriver.Chrome, url: str) -> None:
    log(f"Mo URL: {url}")
    driver.get(url)
    wait_for_listing_ready(driver)
    dismiss_cookie_banner(driver)

    if not is_logged_in(driver):
        raise RuntimeError(
            "Chrome profile hien tai chua dang nhap Batdongsan. "
            "Hay mo browser session login tren Web UI, dang nhap xong roi thu lai."
        )


def extract_detail_fields(driver: webdriver.Chrome, url: str) -> dict:
    log("Trang san sang. Bat dau doc thong tin tin dang.")
    sleep_with_stop(2.5)

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
