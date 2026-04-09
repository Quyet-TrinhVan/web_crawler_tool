import argparse
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import pandas as pd
from selenium.common.exceptions import WebDriverException

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
ABSOLUTE_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
DEFAULT_OUTPUT = Path("batdongsan_list_detail.csv")
SOURCE_NAME = "batdongsan.com"
OUTPUT_COLUMNS = ["STT", "title", "area", "location", "phone", "price", "listing_date", "category_url"]

FALLBACK_CATEGORY_PATHS = [
    "/ban-can-ho-chung-cu",
    "/ban-chung-cu-mini-can-ho-dich-vu",
    "/ban-nha-rieng",
    "/ban-nha-biet-thu-lien-ke",
    "/ban-nha-mat-pho",
    "/ban-shophouse-nha-pho-thuong-mai",
    "/ban-dat-nen-du-an",
    "/ban-dat",
    "/ban-trang-trai-khu-nghi-duong",
    "/ban-condotel",
    "/ban-kho-nha-xuong",
    "/ban-loai-bat-dong-san-khac",
    "/cho-thue-can-ho-chung-cu",
    "/cho-thue-chung-cu-mini-can-ho-dich-vu",
    "/cho-thue-nha-rieng",
    "/cho-thue-nha-biet-thu-lien-ke",
    "/cho-thue-nha-mat-pho",
    "/cho-thue-shophouse-nha-pho-thuong-mai",
    "/cho-thue-nha-tro-phong-tro",
    "/cho-thue-van-phong",
    "/cho-thue-sang-nhuong-cua-hang-ki-ot",
    "/cho-thue-kho-nha-xuong-dat",
    "/cho-thue-loai-bat-dong-san-khac",
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
    if page_number <= 1:
        return base_url

    parts = urlsplit(base_url)
    path = re.sub(r"/p\d+/?$", "", parts.path.rstrip("/"))
    query_pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "page"]
    paginated_path = f"{path}/p{page_number}"

    return urlunsplit((parts.scheme, parts.netloc, paginated_path, urlencode(query_pairs), ""))


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
    normalized_row["listing_date"] = listing_date
    normalized_row["category_url"] = category_url
    return normalized_row


def is_hanoi_location(location: str | None) -> bool:
    normalized = normalize_search_text(location)
    return "ha noi" in normalized if normalized else False


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

    page_url = build_paginated_url(start_url, page_number)
    log(f"[list_crawler] Mo trang danh sach {page_number}: {page_url}")
    driver.get(page_url)
    wait_for_listing_ready(driver)
    dismiss_cookie_banner(driver)
    time.sleep(2)

    page_links = collect_detail_links_from_page(driver)
    log(f"[list_crawler] Tim thay {len(page_links)} link tin tren trang {page_number}.")
    return page_links


def discover_listing_links(driver, start_url: str, max_pages: int | None) -> list[str]:
    discovered: list[str] = []
    seen_links: set[str] = set()
    seen_page_urls: set[str] = set()
    page_number = 1

    while True:
        if max_pages is not None and page_number > max_pages:
            break

        page_url = build_paginated_url(start_url, page_number)
        log(f"[list_crawler] Mo trang danh sach {page_number}: {page_url}")
        driver.get(page_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)
        time.sleep(2)

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


def collect_listing_cards_with_date(driver, target_date: date | None = None) -> list[dict]:
    target_date = target_date or date.today()
    listing_cards: dict[str, dict] = {}

    try:
        raw_cards = driver.execute_script(
            """
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            return anchors.map((anchor) => {
                const href = anchor.href || anchor.getAttribute('href') || '';
                if (!/-pr\\d+(?:[?#]|$)/i.test(href)) {
                    return null;
                }

                let node = anchor;
                let bestText = (anchor.innerText || anchor.textContent || '').trim();
                for (let level = 0; node && level < 6; level += 1) {
                    const candidate = (node.innerText || node.textContent || '').trim();
                    if (candidate.length > bestText.length) {
                        bestText = candidate;
                    }
                    if (candidate.length >= 200) {
                        break;
                    }
                    node = node.parentElement;
                }

                return { href, text: bestText };
            }).filter(Boolean);
            """
        )
    except WebDriverException:
        raw_cards = []

    for raw_card in raw_cards or []:
        href = raw_card.get("href") if isinstance(raw_card, dict) else None
        normalized_url = normalize_detail_url(href)
        if not normalized_url:
            continue

        listing_date = extract_today_listing_label(raw_card.get("text"), target_date=target_date)
        if not listing_date:
            continue

        current = listing_cards.get(normalized_url)
        if current is None or len(raw_card.get("text", "")) > len(current.get("raw_text", "")):
            listing_cards[normalized_url] = {
                "url": normalized_url,
                "listing_date": listing_date,
                "raw_text": raw_card.get("text", ""),
            }

    return [
        {"url": entry["url"], "listing_date": entry["listing_date"]}
        for entry in listing_cards.values()
    ]


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
    skipped_non_hanoi = 0

    while True:
        page_url = build_paginated_url(category_url, page_number)
        log(f"[list_crawler] Mo category page {page_number}: {page_url}")
        driver.get(page_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)
        time.sleep(1)

        today_entries = collect_listing_cards_with_date(driver, target_date=target_date)
        new_entries = [entry for entry in today_entries if entry["url"] not in seen_detail_urls]

        log(
            f"[list_crawler] Category {category_url} page {page_number}: "
            f"{len(today_entries)} tin hom nay, {len(new_entries)} link moi."
        )

        if not today_entries:
            log(f"[list_crawler] Category {category_url} khong con tin hom nay, dung.")
            break

        for entry in new_entries:
            seen_detail_urls.add(entry["url"])
            log(f"[list_crawler] Crawl tin hom nay: {entry['url']}")
            try:
                row = attach_source(
                    crawl_detail_with_driver(driver, entry["url"]),
                    listing_date=entry["listing_date"],
                    category_url=category_url,
                )
                if not is_hanoi_location(row.get("location")):
                    skipped_non_hanoi += 1
                    log(f"[list_crawler] Bo qua tin khong thuoc Ha Noi: {entry['url']}")
                    continue
                rows.append(row)
                crawled_count += 1
                if output_path is not None:
                    save_results(rows, output_path)
            except Exception as exc:
                log(f"[list_crawler] Loi voi {entry['url']}: {exc}")

        page_number += 1

    return {
        "crawled_count": crawled_count,
        "skipped_non_hanoi": skipped_non_hanoi,
    }


def crawl_categories_for_today(output_path: Path | None = None) -> list[dict]:
    log("[list_crawler] Khoi tao Chrome cho che do --date today.")
    driver = build_driver()
    rows: list[dict] = []
    seen_detail_urls: set[str] = set()
    total_skipped_non_hanoi = 0

    try:
        category_urls = discover_batdongsan_category_urls(driver)

        for index, category_url in enumerate(category_urls, start=1):
            log(f"[list_crawler] Crawl category {index}/{len(category_urls)}: {category_url}")
            try:
                stats = crawl_category_for_today(
                    driver,
                    category_url=category_url,
                    seen_detail_urls=seen_detail_urls,
                    rows=rows,
                    output_path=output_path,
                )
                total_skipped_non_hanoi += stats["skipped_non_hanoi"]
                log(
                    f"[list_crawler] Hoan tat category {category_url}. "
                    f"Da crawl {stats['crawled_count']} tin hom nay, "
                    f"bo qua {stats['skipped_non_hanoi']} tin ngoai Ha Noi."
                )
            except Exception as exc:
                log(f"[list_crawler] Loi category {category_url}: {exc}")

        log(f"[list_crawler] Tong so tin bi bo qua vi khong thuoc Ha Noi: {total_skipped_non_hanoi}")
        return rows
    finally:
        driver.quit()


def crawl_listing_page(start_url: str, page_number: int, output_path: Path | None = None) -> list[dict]:
    if page_number < 1:
        raise ValueError("page_number phai >= 1")

    log("[list_crawler] Khoi tao Chrome.")
    driver = build_driver()
    rows: list[dict] = []

    try:
        log(f"[list_crawler] Mo URL bat dau: {start_url}")
        driver.get(start_url)
        wait_for_listing_ready(driver)
        dismiss_cookie_banner(driver)

        if not is_logged_in(driver):
            raise RuntimeError(
                "Chrome profile hien tai chua dang nhap Batdongsan. "
                "Hay chay `uv run -m core.login_batdongsan` bang cung browser profile roi thu lai."
            )

        detail_urls = discover_listing_links_on_page(driver, start_url, page_number)
        log(f"[list_crawler] Tong so tin se crawl o trang {page_number}: {len(detail_urls)}")

        for index, detail_url in enumerate(detail_urls, start=1):
            log(f"[list_crawler] Crawl chi tiet {index}/{len(detail_urls)}: {detail_url}")
            try:
                row = attach_source(crawl_detail_with_driver(driver, detail_url))
                rows.append(row)
                if output_path is not None:
                    save_results(rows, output_path)
            except Exception as exc:
                log(f"[list_crawler] Loi voi {detail_url}: {exc}")

        return rows
    finally:
        driver.quit()


def crawl_listing(start_url: str, output_path: Path, max_pages: int | None, limit: int | None) -> list[dict]:
    log("[list_crawler] Khoi tao Chrome.")
    driver = build_driver()
    rows: list[dict] = []

    try:
        log(f"[list_crawler] Mo URL bat dau: {start_url}")
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
            log(f"[list_crawler] Crawl chi tiet {index}/{len(detail_urls)}: {detail_url}")
            try:
                row = attach_source(crawl_detail_with_driver(driver, detail_url))
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
