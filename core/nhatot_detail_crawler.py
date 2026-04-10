import argparse
import os
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from core.browser_runtime import NHATOT_USER_DATA_DIR
from core.browser_runtime import build_driver as build_browser_driver
from core.browser_session import wait_for_user_action


DEFAULT_URL = "https://www.nhatot.com/mua-ban-can-ho-chung-cu-quan-7-tp-ho-chi-minh/131656948.htm"
USER_DATA_DIR = NHATOT_USER_DATA_DIR
DEFAULT_OUTPUT = Path("nhatot_detail.csv")
SOURCE_NAME = "nhatot.com"
MANUAL_ACTION_TIMEOUT_SECONDS = float(os.getenv("MANUAL_ACTION_TIMEOUT_SECONDS", "900"))
SECURITY_PAGE_MARKERS = (
    "performing security verification",
    "verifying you are not a bot",
    "just a moment",
    "cloudflare",
)

PHONE_SCAN_SELECTORS = [
    "a[href^='tel:']",
    "[data-selenium*='phone']",
    "[data-selenium*='contact']",
    "button",
    "a",
    "[role='button']",
    "[class*='phone']",
    "[class*='call']",
    "[class*='contact']",
]


def log(message: str) -> None:
    print(f"[nhatot_detail] {message}", flush=True)


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()


def normalize_search_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return clean_text(text).lower() if clean_text(text) else ""


def split_text_lines(text: str | None) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[\r\n]+", text)
    return [line for line in (clean_text(part) for part in parts) if line]


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None

    phone = phone.replace("tel:", "").strip()
    phone = re.sub(r"[^\d+]", "", phone)

    if phone.startswith("+84"):
        phone = "0" + phone[3:]
    elif phone.startswith("84") and len(phone) == 11:
        phone = "0" + phone[2:]

    mobile_pattern = r"0(?:3[2-9]|5[25689]|7[06789]|8[1-9]|9[0-46-9])\d{7}"
    return phone if re.fullmatch(mobile_pattern, phone) else None


def extract_phone_from_text(text: str | None) -> str | None:
    if not text:
        return None

    match = re.search(
        r"((?:\+84|84|0)(?:3[2-9]|5[25689]|7[06789]|8[1-9]|9[0-46-9])(?:[\s.\-]?\d){7})",
        text,
    )
    if not match:
        return None

    return normalize_phone(match.group(1))


def extract_all_phones_from_text(text: str | None) -> list[str]:
    if not text:
        return []

    matches = re.findall(
        r"((?:\+84|84|0)(?:3[2-9]|5[25689]|7[06789]|8[1-9]|9[0-46-9])(?:[\s.\-]?\d){7})",
        text,
    )
    phones: list[str] = []
    for match in matches:
        phone = normalize_phone(match)
        if phone:
            phones.append(phone)
    return phones


def looks_like_location_line(text: str | None, title: str | None = None) -> bool:
    text = clean_text(text)
    if not text:
        return False

    normalized = normalize_search_text(text)
    if len(text) < 12 or len(text) > 140:
        return False
    if "," not in text:
        return False
    if "nha tot" in normalized or "mua ban" in normalized:
        return False
    if title and normalize_search_text(title) == normalized:
        return False
    if extract_phone_from_text(text):
        return False
    if any(token in normalized for token in (" ty", " trieu", "/m2", " m2", "dien tich", "gia/m2")):
        return False
    return any(token in normalized for token in ("quan", "huyen", "phuong", "xa", "tp ", "thanh pho"))


def build_driver() -> webdriver.Chrome:
    return build_browser_driver(USER_DATA_DIR, page_load_strategy="eager")


def wait_for_page_ready(driver: webdriver.Chrome) -> None:
    log("Dang cho trang chi tiet san sang.")
    deadline = time.time() + 15
    while time.time() < deadline:
        title = get_title(driver)
        if title and not is_security_verification_page(driver) and title.lower() != "www.nhatot.com":
            log("Da thay title chi tiet.")
            return
        time.sleep(0.25)

    log("Trang dang o man hinh security verification.")
    log("Hay hoan tat xac minh trong browser/noVNC, sau do bam nut tiep tuc tren Web UI.")
    wait_for_user_action(
        source="nhatot.com",
        mode="verification",
        message="NhaTot dang cho ban hoan tat security verification trong browser/noVNC.",
        timeout_seconds=MANUAL_ACTION_TIMEOUT_SECONDS,
    )

    deadline = time.time() + 20
    while time.time() < deadline:
        title = get_title(driver)
        if title and not is_security_verification_page(driver) and title.lower() != "www.nhatot.com":
            log("Da thay title chi tiet.")
            return
        time.sleep(0.25)

    raise RuntimeError("Khong vuot qua duoc security verification hoac khong thay title tren trang NhaTot.")


def is_security_verification_page(driver: webdriver.Chrome) -> bool:
    title = normalize_search_text(driver.title)
    body_text = normalize_search_text(get_body_text(driver))
    haystack = f"{title}\n{body_text}"
    return any(marker in haystack for marker in SECURITY_PAGE_MARKERS)


def dismiss_cookie_banner(driver: webdriver.Chrome) -> None:
    xpaths = [
        "//button[contains(., 'Dong y')]",
        "//button[contains(., 'Dong y')]",
        "//button[contains(., 'Dong')]",
    ]
    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            for element in elements:
                if not element.is_displayed():
                    continue
                driver.execute_script("arguments[0].click();", element)
                log("Da dong cookie banner.")
                time.sleep(0.15)
                return
        except WebDriverException:
            pass


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


def has_visible_phone_button(driver: webdriver.Chrome) -> bool:
    try:
        for selector in (
            "a[href^='tel:']",
            "[data-selenium*='phone']",
            "[class*='phone']",
            "[class*='call']",
        ):
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed() and clean_text(element.text):
                    return True
    except WebDriverException:
        pass
    return False


def get_body_text(driver: webdriver.Chrome) -> str:
    try:
        return driver.execute_script(
            "return (document.body && (document.body.innerText || document.body.textContent)) || '';"
        )
    except WebDriverException:
        return ""


def get_title(driver: webdriver.Chrome) -> str | None:
    return get_text(driver, ["h1", "[data-selenium='ad-title']", "[class*='AdTitle']"])


def get_top_lines(driver: webdriver.Chrome, limit: int = 25) -> list[str]:
    body_text = get_body_text(driver)
    lines = [clean_text(line) for line in body_text.splitlines()]
    return [line for line in lines if line][:limit]


def get_price(driver: webdriver.Chrome) -> str | None:
    price_pattern = re.compile(r"\d+(?:[.,]\d+)?\s*(?:ty|trieu|nghin|dong|tr|ty)", re.IGNORECASE)
    for line in get_top_lines(driver, limit=15):
        normalized = normalize_search_text(line)
        if "/m2" in normalized or "/m²" in normalized:
            continue
        match = price_pattern.search(normalized)
        if not match:
            continue
        original_match = re.search(r"\d+(?:[.,]\d+)?\s*(?:tỷ|triệu|nghìn|đ|tr|ty)", line, re.IGNORECASE)
        if original_match:
            return clean_text(original_match.group(0))
    return None


def get_area(driver: webdriver.Chrome) -> str | None:
    text = get_text(driver, ["[class*='size']", "[class*='area']", "[data-selenium='ad-area']"])
    phone = extract_phone_from_text(text)
    if text and not phone:
        match = re.search(r"\d+(?:[.,]\d+)?\s*(?:m\u00b2|m2)", text, re.IGNORECASE)
        if match:
            return match.group(0)

    body_text = get_body_text(driver)
    match = re.search(r"\b\d+(?:[.,]\d+)?\s*(?:m\u00b2|m2)\b", body_text, re.IGNORECASE)
    return match.group(0) if match else None


def get_location(driver: webdriver.Chrome) -> str | None:
    title = get_title(driver)
    selectors = [
        "[data-selenium='ad-address']",
        "[class*='address']",
        "[class*='Address']",
    ]
    for selector in selectors:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if not element.is_displayed():
                    continue
                for line in split_text_lines(element.text):
                    if looks_like_location_line(line, title=title):
                        return line
        except WebDriverException:
            pass

    for line in split_text_lines(get_body_text(driver)):
        if looks_like_location_line(line, title=title):
            return line
    return None


def extract_phone_from_element(element) -> str | None:
    attributes = ("href", "aria-label", "title", "data-phone", "data-value", "value")
    for attr_name in attributes:
        try:
            attr_value = element.get_attribute(attr_name)
            phone = normalize_phone(attr_value) or extract_phone_from_text(attr_value)
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

    return None


def score_phone_candidate(element, phone: str) -> int:
    score = 0
    try:
        text = clean_text(element.text) or ""
        class_name = clean_text(element.get_attribute("class")) or ""
        href = clean_text(element.get_attribute("href")) or ""
        aria_label = clean_text(element.get_attribute("aria-label")) or ""
    except WebDriverException:
        return 0

    text_phone = normalize_phone(text)
    haystack = normalize_search_text(" ".join([text, class_name, href, aria_label]))

    if href.lower().startswith("tel:"):
        score += 10
    if text_phone == phone:
        score += 8
    if phone in re.sub(r"[^\d]", "", text):
        score += 6
    if any(token in haystack for token in ("phone", "call", "contact", "seller", "lien he", "goi")):
        score += 4
    if element.tag_name.lower() in {"a", "button"}:
        score += 2
    if "*" not in text:
        score += 2

    return score


def collect_visible_phone_candidates(driver: webdriver.Chrome) -> list[tuple[str, int]]:
    candidates: list[tuple[str, int]] = []

    for selector in PHONE_SCAN_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue

        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                phone = extract_phone_from_element(element)
                if phone:
                    candidates.append((phone, score_phone_candidate(element, phone)))
            except WebDriverException:
                pass

    for phone in extract_all_phones_from_text(get_body_text(driver)):
        candidates.append((phone, 1))

    return candidates


def pick_best_phone(candidates: list[tuple[str, int]]) -> str | None:
    if not candidates:
        return None
    scores: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    for phone, score in candidates:
        scores[phone] += score
        counts[phone] += 1

    return sorted(scores.keys(), key=lambda phone: (-scores[phone], -counts[phone], phone))[0]


def scan_phone_from_dom(driver: webdriver.Chrome) -> str | None:
    return pick_best_phone(collect_visible_phone_candidates(driver))


def looks_like_phone_reveal_button(element) -> bool:
    try:
        text = clean_text(element.text) or ""
        class_name = clean_text(element.get_attribute("class")) or ""
        href = clean_text(element.get_attribute("href")) or ""
        aria_label = clean_text(element.get_attribute("aria-label")) or ""
    except WebDriverException:
        return False

    normalized_text = normalize_search_text(text)
    normalized_class = normalize_search_text(class_name)
    normalized_href = normalize_search_text(href)
    normalized_aria = normalize_search_text(aria_label)
    haystack = " ".join([normalized_text, normalized_class, normalized_aria, normalized_href])

    if href and not href.startswith("#"):
        if any(href.lower().startswith(prefix) for prefix in ("tel:", "sms:", "mailto:", "intent:")):
            return False
        if "zalo" in normalized_href:
            return False

    if any(token in haystack for token in ("chat", "zalo", "gui tin", "nhan tin")):
        return False

    if any(token in haystack for token in ("hien so", "bam de hien so", "show phone", "showphone")):
        return True
    if "*" in text and re.search(r"\d{3,}", text):
        return True
    if re.search(r"0\d{2,}.*\*", text):
        return True
    return False


def phone_reveal_click_score(element) -> int:
    score = 0
    try:
        text = clean_text(element.text) or ""
        class_name = clean_text(element.get_attribute("class")) or ""
        aria_label = clean_text(element.get_attribute("aria-label")) or ""
        href = clean_text(element.get_attribute("href")) or ""
        tag_name = element.tag_name.lower()
    except WebDriverException:
        return 0

    normalized = normalize_search_text(" ".join([text, class_name, aria_label, href]))
    if any(token in normalized for token in ("hien so", "bam de hien so", "show phone", "showphone")):
        score += 10
    if "*" in text and re.search(r"\d{3,}", text):
        score += 8
    if tag_name in {"button", "div", "span"}:
        score += 4
    if tag_name == "a":
        score += 1
    if not href:
        score += 3
    if any(token in normalized for token in ("phone", "call", "contact", "lien he", "goi")):
        score += 2
    return score


def get_phone_reveal_candidates(driver: webdriver.Chrome) -> list:
    candidates = []
    seen: set[str] = set()
    for selector in PHONE_SCAN_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue

        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                if not looks_like_phone_reveal_button(element):
                    continue
                element_id = element.id
                if element_id in seen:
                    continue
                seen.add(element_id)
                candidates.append(element)
            except WebDriverException:
                pass

    return sorted(candidates, key=phone_reveal_click_score, reverse=True)


def click_show_phone_and_get(driver: webdriver.Chrome) -> str | None:
    phone = scan_phone_from_dom(driver)
    if phone:
        log("Da tim thay so dien thoai tren DOM truoc khi click.")
        return phone

    for element in get_phone_reveal_candidates(driver)[:4]:
        try:
            log("Dang click nut hien so.")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            time.sleep(0.15)
            try:
                element.click()
            except WebDriverException:
                driver.execute_script("arguments[0].click();", element)
            time.sleep(0.35)

            phone = scan_phone_from_dom(driver)
            if phone:
                log("Da lay duoc so dien thoai sau khi click.")
                return phone
        except WebDriverException:
            pass

    log("Khong lay duoc so dien thoai.")
    return None


def ensure_detail_page_ready(driver: webdriver.Chrome, url: str) -> None:
    log(f"Mo URL: {url}")
    driver.get(url)
    wait_for_page_ready(driver)
    dismiss_cookie_banner(driver)
    deadline = time.time() + 4
    while time.time() < deadline:
        if get_title(driver) and (get_price(driver) or get_area(driver) or has_visible_phone_button(driver)):
            return
        time.sleep(0.2)


def extract_detail_fields(driver: webdriver.Chrome, url: str) -> dict:
    title = get_title(driver)
    log(f"Title: {title!r}")
    price = get_price(driver)
    log(f"Price: {price!r}")
    area = get_area(driver)
    log(f"Area: {area!r}")
    location = get_location(driver)
    log(f"Location: {location!r}")
    phone = click_show_phone_and_get(driver)
    log(f"Phone: {phone!r}")

    return {
        "title": title,
        "area": area,
        "location": location,
        "phone": phone,
        "price": price,
        "url": url,
    }


def crawl_detail_with_driver(driver: webdriver.Chrome, url: str) -> dict:
    ensure_detail_page_ready(driver, url)
    return extract_detail_fields(driver, url)


def crawl_detail(url: str) -> dict:
    log("Khoi tao Chrome.")
    driver = build_driver()
    try:
        return crawl_detail_with_driver(driver, url)
    finally:
        driver.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl chi tiet tin dang NhaTot.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="URL chi tiet NhaTot.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Duong dan file CSV output.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    item = crawl_detail(args.url)
    print(item)
    pd.DataFrame([item]).to_csv(args.output, index=False, encoding="utf-8-sig")
