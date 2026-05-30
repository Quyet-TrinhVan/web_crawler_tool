import argparse
import random
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import pandas as pd
from selenium.common.exceptions import WebDriverException

from core.crawl_control import raise_if_stop_requested, sleep_with_stop
from core.detail_crawler import (
    build_driver,
    clean_text,
    crawl_detail_with_driver,
    dismiss_cookie_banner,
    is_logged_in,
    log,
    wait_for_listing_ready,
)


HOME_URL = "https://batdongsan.com.vn/"
DETAIL_URL_RE = re.compile(r"^https://(?:www\.)?batdongsan\.com\.vn/[^?#\s]*-pr\d+(?:\?.*)?$", re.IGNORECASE)
DETAIL_PATH_RE = re.compile(r"https://(?:www\.)?batdongsan\.com\.vn/[^\"'?#\s]*-pr\d+(?:\?[^\"'#\s]*)?", re.IGNORECASE)
DETAIL_RELATIVE_PATH_RE = re.compile(r"/[^\"'?#\s]*-pr\d+(?:\?[^\"'#\s]*)?", re.IGNORECASE)
RELATIVE_TODAY_RE = re.compile(r"\b\d+\s*(phut|gio)\s*truoc\b", re.IGNORECASE)
RELATIVE_OLDER_RE = re.compile(r"\b(hom qua|\d+\s*ngay\s*truoc)\b", re.IGNORECASE)
ABSOLUTE_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
DEFAULT_OUTPUT = Path("batdongsan_list_detail.csv")
SOURCE_NAME = "batdongsan.com"
OUTPUT_COLUMNS = ["STT", "title", "area", "location", "phone", "price", "listing_date", "category_url", "url"]
PAGE_SETTLE_DELAY_RANGE_SECONDS = (15.0, 20.0)
DETAIL_DELAY_RANGE_SECONDS = (15.0, 20.0)
DETAIL_RETRY_DELAY_SECONDS = 10.0
MAX_DETAIL_ATTEMPTS = 3

FALLBACK_CATEGORY_PATHS = [
    "/ban-can-ho-chung-cu-ha-noi",
    "/ban-chung-cu-mini-can-ho-dich-vu-ha-noi",
    "/ban-nha-rieng-ha-noi",
    "/ban-nha-biet-thu-lien-ke-ha-noi",
    "/ban-nha-mat-pho-ha-noi",
    "/ban-shophouse-nha-pho-thuong-mai-ha-noi",
    "/ban-dat-nen-du-an-ha-noi",
    "/ban-dat-ha-noi",
    "/ban-trang-trai-khu-nghi-duong-ha-noi",
    "/ban-condotel-ha-noi",
    "/ban-kho-nha-xuong-ha-noi",
    "/ban-loai-bat-dong-san-khac-ha-noi",
    "/cho-thue-can-ho-chung-cu-ha-noi",
    "/cho-thue-chung-cu-mini-can-ho-dich-vu-ha-noi",
    "/cho-thue-nha-rieng-ha-noi",
    "/cho-thue-nha-biet-thu-lien-ke-ha-noi",
    "/cho-thue-nha-mat-pho-ha-noi",
    "/cho-thue-shophouse-nha-pho-thuong-mai-ha-noi",
    "/cho-thue-nha-tro-phong-tro-ha-noi",
    "/cho-thue-van-phong-ha-noi",
    "/cho-thue-sang-nhuong-cua-hang-ki-ot-ha-noi",
    "/cho-thue-kho-nha-xuong-dat-ha-noi",
    "/cho-thue-loai-bat-dong-san-khac-ha-noi",
]


def normalize_search_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    cleaned = clean_text(text)
    return cleaned.lower() if cleaned else ""


def normalize_detail_url(href: str | None) -> str | None:
    if not href:
        return None

    absolute_url = urljoin(HOME_URL, href.strip())
    absolute_url = absolute_url.split("#", 1)[0]
    if not DETAIL_URL_RE.match(absolute_url):
        return None

    parts = urlsplit(absolute_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))


def normalize_category_url(href: str | None, allowed_prefixes: tuple[str, ...] = ("/ban-", "/cho-thue-")) -> str | None:
    if not href:
        return None

    absolute_url = urljoin(HOME_URL, href.strip())
    absolute_url = absolute_url.split("#", 1)[0]
    parts = urlsplit(absolute_url)
    path = parts.path.rstrip("/")

    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        return None
    if "-pr" in path.lower():
        return None

    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def build_paginated_url(base_url: str, page_number: int) -> str:
    if page_number < 1:
        raise ValueError("page_number phai >= 1")

    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    query_pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "page"]

    if page_number == 1 and not re.search(r"/p\d+/?$", path):
        return base_url

    path = re.sub(r"/p\d+/?$", "", path)
    if page_number == 1:
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query_pairs, safe=","), ""))

    paginated_path = f"{path}/p{page_number}"

    return urlunsplit((parts.scheme, parts.netloc, paginated_path, urlencode(query_pairs, safe=","), ""))


def save_results(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_rows: list[dict] = []
    for index, row in enumerate(rows, start=1):
        normalized_row = {column: row.get(column) for column in OUTPUT_COLUMNS}
        normalized_row["STT"] = index
        normalized_rows.append(normalized_row)
    pd.DataFrame(normalized_rows, columns=OUTPUT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")


def attach_source(row: dict, listing_date: str | None = None, category_url: str | None = None) -> dict:
    normalized_row = dict(row)
    normalized_row["source"] = SOURCE_NAME
    normalized_row["listing_date"] = listing_date if listing_date is not None else row.get("listing_date")
    normalized_row["category_url"] = category_url
    return normalized_row


def anti_spam_pause(label: str, delay_range: tuple[float, float]) -> None:
    delay = random.uniform(*delay_range)
    log(f"[list_crawler] Tam dung {delay:.1f}s de giam tan suat request ({label}).")
    sleep_with_stop(delay)


def has_meaningful_detail_data(row: dict | None) -> bool:
    if not row:
        return False

    title = clean_text(row.get("title"))
    if not title:
        return False

    return any(clean_text(row.get(field)) for field in ("location", "price", "area", "phone"))


def should_retry_detail(row: dict | None) -> bool:
    if not has_meaningful_detail_data(row):
        return True

    # So dien thoai la truong quan trong, neu chua co thi thu lai them de giam miss.
    return clean_text((row or {}).get("phone")) is None


def crawl_detail_with_retries(driver, detail_url: str, max_attempts: int = MAX_DETAIL_ATTEMPTS) -> dict | None:
    last_row: dict | None = None

    for attempt in range(1, max_attempts + 1):
        raise_if_stop_requested()
        try:
            row = crawl_detail_with_driver(driver, detail_url)
            last_row = row

            if not should_retry_detail(row):
                return row

            if attempt < max_attempts:
                log(
                    f"[list_crawler] Tin {detail_url} du lieu chua day du"
                    f" (attempt {attempt}/{max_attempts}), thu lai sau {DETAIL_RETRY_DELAY_SECONDS:.0f}s."
                )
                sleep_with_stop(DETAIL_RETRY_DELAY_SECONDS)
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            log(
                f"[list_crawler] Loi tam thoi voi {detail_url} (attempt {attempt}/{max_attempts}): {exc}."
                f" Thu lai sau {DETAIL_RETRY_DELAY_SECONDS:.0f}s."
            )
            sleep_with_stop(DETAIL_RETRY_DELAY_SECONDS)

    return last_row if has_meaningful_detail_data(last_row) else None


def collect_detail_links_from_page(driver) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []

    def add_url(raw_url: str | None) -> None:
        normalized = normalize_detail_url(raw_url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        links.append(normalized)

    try:
        hrefs = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('a[href]'), (anchor) => anchor.href || anchor.getAttribute('href'));
            """
        )
        for href in hrefs or []:
            if isinstance(href, str):
                add_url(href)
    except WebDriverException:
        pass

    try:
        attribute_values = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('*')).flatMap(
                (element) => Array.from(element.attributes || [], (attr) => attr.value)
            );
            """
        )
        for value in attribute_values or []:
            if isinstance(value, str):
                add_url(value)
    except WebDriverException:
        pass

    try:
        page_source = driver.page_source
        for match in DETAIL_PATH_RE.findall(page_source):
            add_url(match)
        for match in DETAIL_RELATIVE_PATH_RE.findall(page_source):
            add_url(match)
    except WebDriverException:
        pass

    return links


def discover_listing_links_on_page(driver, start_url: str, page_number: int) -> list[str]:
    if page_number < 1:
        raise ValueError("page_number phai >= 1")

    raise_if_stop_requested()
    page_url = build_paginated_url(start_url, page_number)
    log(f"[list_crawler] Mo trang danh sach {page_number}: {page_url}")
    driver.get(page_url)
    wait_for_listing_ready(driver)
    dismiss_cookie_banner(driver)
    anti_spam_pause("sau khi mo trang danh sach", PAGE_SETTLE_DELAY_RANGE_SECONDS)

    page_links = collect_detail_links_from_page(driver)
    log(f"[list_crawler] Tim thay {len(page_links)} link tin tren trang {page_number}.")
    return page_links


def discover_listing_links(driver, start_url: str, max_pages: int | None) -> list[str]:
    discovered: list[str] = []
    seen_links: set[str] = set()
    seen_page_urls: set[str] = set()
    page_number = 1

    while True:
        raise_if_stop_requested()
        if max_pages is not None and page_number > max_pages:
            break

        page_url = build_paginated_url(start_url, page_number)
        log(f"[list_crawler] Mo trang danh sach {page_number}: {page_url}")
        driver.get(page_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)
        anti_spam_pause("sau khi mo trang danh sach", PAGE_SETTLE_DELAY_RANGE_SECONDS)

        current_url = driver.current_url
        if current_url in seen_page_urls:
            log(f"[list_crawler] Trang {current_url} lap lai, dung phan trang.")
            break
        seen_page_urls.add(current_url)

        page_links = collect_detail_links_from_page(driver)
        new_links = [link for link in page_links if link not in seen_links]

        log(
            f"[list_crawler] Tim thay {len(page_links)} link tin, "
            f"co {len(new_links)} link moi tren trang {page_number}."
        )

        if not page_links:
            break

        for link in new_links:
            seen_links.add(link)
            discovered.append(link)

        if page_number > 1 and not new_links:
            log("[list_crawler] Khong con link moi, dung phan trang.")
            break

        page_number += 1

    return discovered


def get_fallback_category_urls() -> list[str]:
    return [urljoin(HOME_URL, path) for path in FALLBACK_CATEGORY_PATHS]


def discover_batdongsan_category_urls(driver) -> list[str]:
    log("[list_crawler] Mo homepage Batdongsan de khoi tao session.")
    raise_if_stop_requested()
    driver.get(HOME_URL)
    wait_for_listing_ready(driver)
    dismiss_cookie_banner(driver)

    if not is_logged_in(driver):
        raise RuntimeError(
            "Chrome profile hien tai chua dang nhap Batdongsan. "
            "Hay chay `uv run -m core.login_batdongsan` bang cung browser profile roi thu lai."
        )

    discovered: list[str] = []
    seen: set[str] = set()
    for category_url in get_fallback_category_urls():
        normalized = normalize_category_url(category_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        discovered.append(normalized)

    log("[list_crawler] Bo qua buoc hover menu, dung truc tiep FALLBACK_CATEGORY_PATHS.")
    log(f"[list_crawler] Tong so category Batdongsan se crawl: {len(discovered)}")
    return discovered


def extract_today_listing_label(text: str | None, target_date: date | None = None) -> str | None:
    if not text:
        return None

    target_date = target_date or date.today()
    normalized = normalize_search_text(text)

    if "hom nay" in normalized:
        return "hom nay"

    relative_match = RELATIVE_TODAY_RE.search(normalized)
    if relative_match:
        return relative_match.group(0)

    absolute_match = ABSOLUTE_DATE_RE.search(normalized)
    if not absolute_match:
        return None

    try:
        parsed = datetime.strptime(absolute_match.group(1), "%d/%m/%Y").date()
    except ValueError:
        return None

    if parsed == target_date:
        return absolute_match.group(1)
    return None


def has_older_listing_label(text: str | None, target_date: date | None = None) -> bool:
    if not text:
        return False

    target_date = target_date or date.today()
    normalized = normalize_search_text(text)

    if RELATIVE_OLDER_RE.search(normalized):
        return True

    absolute_match = ABSOLUTE_DATE_RE.search(normalized)
    if not absolute_match:
        return False

    try:
        parsed = datetime.strptime(absolute_match.group(1), "%d/%m/%Y").date()
    except ValueError:
        return False

    return parsed < target_date


def collect_listing_cards_with_date(driver, target_date: date | None = None) -> dict:
    target_date = target_date or date.today()
    listing_cards: dict[str, dict] = {}
    has_older_listing = False

    try:
        raw_cards = driver.execute_script(
            """
            const dateNodes = Array.from(document.querySelectorAll(
                '[class*="published-at"][aria-label], [aria-label][role="tooltip"]'
            ));

            return dateNodes.map((dateNode) => {
                const dateLabel = dateNode.getAttribute('aria-label') || '';
                let node = dateNode.parentElement;
                let bestCard = null;

                for (let level = 0; node && level < 8; level += 1) {
                    const anchors = Array.from(node.querySelectorAll('a[href]')).filter((anchor) => {
                        const href = anchor.href || anchor.getAttribute('href') || '';
                        return /-pr\\d+(?:[?#]|$)/i.test(href);
                    });

                    if (anchors.length > 0 && anchors.length <= 5) {
                        bestCard = { node, anchors };
                        break;
                    }

                    node = node.parentElement;
                }

                if (!bestCard) {
                    return null;
                }

                const anchor = bestCard.anchors[0];
                const href = anchor.href || anchor.getAttribute('href') || '';
                const text = (bestCard.node.innerText || bestCard.node.textContent || '').trim();
                return { href, text, dateLabel };
            }).filter(Boolean);
            """
        )
    except WebDriverException:
        raw_cards = []

    for raw_card in raw_cards or []:
        card_text = raw_card.get("text", "") if isinstance(raw_card, dict) else ""
        date_label = raw_card.get("dateLabel", "") if isinstance(raw_card, dict) else ""
        date_text = f"{date_label}\n{card_text}"
        if has_older_listing_label(date_text, target_date=target_date):
            has_older_listing = True

        href = raw_card.get("href") if isinstance(raw_card, dict) else None
        normalized_url = normalize_detail_url(href)
        if not normalized_url:
            continue

        listing_date = extract_today_listing_label(date_text, target_date=target_date)
        if not listing_date:
            continue

        current = listing_cards.get(normalized_url)
        if current is None or len(card_text) > len(current.get("raw_text", "")):
            listing_cards[normalized_url] = {
                "url": normalized_url,
                "listing_date": listing_date,
                "raw_text": card_text,
            }

    return {
        "today_entries": [
            {"url": entry["url"], "listing_date": entry["listing_date"]}
            for entry in listing_cards.values()
        ],
        "has_older_listing": has_older_listing,
    }


def crawl_category_for_today(
    driver,
    category_url: str,
    seen_detail_urls: set[str],
    rows: list[dict],
    output_path: Path | None = None,
) -> dict:
    target_date = date.today()
    page_number = 1
    crawled_count = 0

    while True:
        raise_if_stop_requested()
        page_url = build_paginated_url(category_url, page_number)
        log(f"[list_crawler] Mo category page {page_number}: {page_url}")
        driver.get(page_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)
        anti_spam_pause("sau khi mo category page", PAGE_SETTLE_DELAY_RANGE_SECONDS)

        page_listing_state = collect_listing_cards_with_date(driver, target_date=target_date)
        today_entries = page_listing_state["today_entries"]
        has_older_listing = page_listing_state["has_older_listing"]
        new_entries = [entry for entry in today_entries if entry["url"] not in seen_detail_urls]

        log(
            f"[list_crawler] Category {category_url} page {page_number}: "
            f"{len(today_entries)} tin hom nay theo aria-label, {len(new_entries)} link moi, "
            f"co tin cu hon hom nay={has_older_listing}."
        )

        if not today_entries:
            log(f"[list_crawler] Category {category_url} khong con tin hom nay theo aria-label, dung.")
            break

        for entry in new_entries:
            raise_if_stop_requested()
            detail_url = entry["url"]
            seen_detail_urls.add(detail_url)
            anti_spam_pause("truoc khi vao detail", DETAIL_DELAY_RANGE_SECONDS)
            log(f"[list_crawler] Crawl tin hom nay theo aria-label: {detail_url}")
            try:
                detail_row = crawl_detail_with_retries(driver, detail_url)
                if not has_meaningful_detail_data(detail_row):
                    log(f"[list_crawler] Bo qua tin vi du lieu rong/khong on dinh: {detail_url}")
                    continue
                if detail_row is None:
                    continue

                row = attach_source(detail_row, listing_date=entry["listing_date"], category_url=category_url)
                rows.append(row)
                crawled_count += 1
                if output_path is not None:
                    save_results(rows, output_path)
            except Exception as exc:
                log(f"[list_crawler] Loi voi {detail_url}: {exc}")

        page_number += 1
        if has_older_listing:
            log(f"[list_crawler] Category {category_url} da gap tin cu hon hom nay theo aria-label, dung phan trang.")
            break

    return {
        "crawled_count": crawled_count,
    }


def crawl_categories_for_today(output_path: Path | None = None, driver=None) -> list[dict]:
    owns_driver = driver is None
    if owns_driver:
        log("[list_crawler] Khoi tao Chrome cho che do --date today.")
        driver = build_driver()
    else:
        log("[list_crawler] Dung browser login hien tai cho che do --date today.")
    rows: list[dict] = []
    seen_detail_urls: set[str] = set()

    try:
        category_urls = discover_batdongsan_category_urls(driver)

        for index, category_url in enumerate(category_urls, start=1):
            raise_if_stop_requested()
            log(f"[list_crawler] Crawl category {index}/{len(category_urls)}: {category_url}")
            try:
                stats = crawl_category_for_today(
                    driver,
                    category_url=category_url,
                    seen_detail_urls=seen_detail_urls,
                    rows=rows,
                    output_path=output_path,
                )
                log(
                    f"[list_crawler] Hoan tat category {category_url}. "
                    f"Da crawl {stats['crawled_count']} tin hom nay."
                )
            except Exception as exc:
                log(f"[list_crawler] Loi category {category_url}: {exc}")

        return rows
    finally:
        if owns_driver:
            driver.quit()


def crawl_listing_page(
    start_url: str,
    page_number: int,
    output_path: Path | None = None,
    driver=None,
    today_only: bool = False,
    page_end: int | None = None,
) -> list[dict]:
    if page_number < 1:
        raise ValueError("page_number phai >= 1")
    if page_end is not None and page_end < page_number:
        raise ValueError("page_end phai >= page_number")

    owns_driver = driver is None
    if owns_driver:
        log("[list_crawler] Khoi tao Chrome.")
        driver = build_driver()
    else:
        log("[list_crawler] Dung browser login hien tai.")
    rows: list[dict] = []

    try:
        log(f"[list_crawler] Mo URL bat dau: {start_url}")
        raise_if_stop_requested()
        driver.get(start_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)

        if not is_logged_in(driver):
            raise RuntimeError(
                "Chrome profile hien tai chua dang nhap Batdongsan. "
                "Hay chay `uv run -m core.login_batdongsan` bang cung browser profile roi thu lai."
            )

        page_end = page_end or page_number

        for current_page in range(page_number, page_end + 1):
            raise_if_stop_requested()
            if today_only:
                page_url = build_paginated_url(start_url, current_page)
                log(f"[list_crawler] Mo trang danh sach hom nay {current_page}: {page_url}")
                driver.get(page_url)
                wait_for_listing_ready(driver)
                dismiss_cookie_banner(driver)
                anti_spam_pause("sau khi mo trang danh sach hom nay", PAGE_SETTLE_DELAY_RANGE_SECONDS)
                page_listing_state = collect_listing_cards_with_date(driver)
                detail_entries = page_listing_state["today_entries"]
                has_older_listing = page_listing_state["has_older_listing"]
                log(
                    f"[list_crawler] Trang {current_page}: tim thay {len(detail_entries)} tin hom nay theo aria-label, "
                    f"co tin cu hon hom nay={has_older_listing}. Van tiep tuc den end page neu co."
                )
                if not detail_entries:
                    log(f"[list_crawler] Trang {current_page} khong con tin hom nay theo aria-label, dung page range.")
                    break
            else:
                detail_entries = [
                    {"url": detail_url, "listing_date": None}
                    for detail_url in discover_listing_links_on_page(driver, start_url, current_page)
                ]
            log(f"[list_crawler] Tong so tin se crawl o trang {current_page}: {len(detail_entries)}")

            for index, entry in enumerate(detail_entries, start=1):
                raise_if_stop_requested()
                detail_url = entry["url"]
                anti_spam_pause("truoc khi vao detail", DETAIL_DELAY_RANGE_SECONDS)
                log(f"[list_crawler] Crawl chi tiet trang {current_page} {index}/{len(detail_entries)}: {detail_url}")
                try:
                    detail_row = crawl_detail_with_retries(driver, detail_url)
                    if not has_meaningful_detail_data(detail_row):
                        log(f"[list_crawler] Bo qua tin vi du lieu rong/khong on dinh: {detail_url}")
                        continue
                    if detail_row is None:
                        continue

                    row = attach_source(detail_row, listing_date=entry.get("listing_date"))
                    rows.append(row)
                    if output_path is not None:
                        save_results(rows, output_path)
                except Exception as exc:
                    log(f"[list_crawler] Loi voi {detail_url}: {exc}")

        return rows
    finally:
        if owns_driver:
            driver.quit()


def crawl_listing(start_url: str, output_path: Path, max_pages: int | None, limit: int | None) -> list[dict]:
    log("[list_crawler] Khoi tao Chrome.")
    driver = build_driver()
    rows: list[dict] = []

    try:
        log(f"[list_crawler] Mo URL bat dau: {start_url}")
        raise_if_stop_requested()
        driver.get(start_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)

        if not is_logged_in(driver):
            raise RuntimeError(
                "Chrome profile hien tai chua dang nhap Batdongsan. "
                "Hay chay `uv run -m core.login_batdongsan` bang cung browser profile roi thu lai."
            )

        detail_urls = discover_listing_links(driver, start_url, max_pages=max_pages)
        if limit is not None:
            detail_urls = detail_urls[:limit]

        log(f"[list_crawler] Tong so tin se crawl: {len(detail_urls)}")

        for index, detail_url in enumerate(detail_urls, start=1):
            raise_if_stop_requested()
            anti_spam_pause("truoc khi vao detail", DETAIL_DELAY_RANGE_SECONDS)
            log(f"[list_crawler] Crawl chi tiet {index}/{len(detail_urls)}: {detail_url}")
            try:
                detail_row = crawl_detail_with_retries(driver, detail_url)
                if not has_meaningful_detail_data(detail_row):
                    log(f"[list_crawler] Bo qua tin vi du lieu rong/khong on dinh: {detail_url}")
                    continue
                if detail_row is None:
                    continue

                row = attach_source(detail_row)
                rows.append(row)
                save_results(rows, output_path)
            except Exception as exc:
                log(f"[list_crawler] Loi voi {detail_url}: {exc}")

        return rows
    finally:
        driver.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl danh sach Batdongsan va lay thong tin chi tiet.")
    parser.add_argument("url", help="URL danh sach, vi du https://batdongsan.com.vn/nha-dat-ban")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Duong dan file CSV output.")
    parser.add_argument("--max-pages", type=int, default=None, help="So trang danh sach toi da can quet.")
    parser.add_argument("--limit", type=int, default=None, help="So tin chi tiet toi da can crawl.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    output_path = Path(args.output)
    rows = crawl_listing(args.url, output_path=output_path, max_pages=args.max_pages, limit=args.limit)
    log(f"[list_crawler] Hoan tat. Da luu {len(rows)} dong vao {output_path}")
