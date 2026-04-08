import argparse
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import pandas as pd
from selenium.common.exceptions import WebDriverException

from core.nhatot_detail_crawler import (
    SOURCE_NAME,
    build_driver,
    crawl_detail_with_driver,
    dismiss_cookie_banner,
    get_body_text,
    is_security_verification_page,
    log,
    normalize_search_text,
)


DETAIL_URL_RE = re.compile(r"^https://(?:www\.)?nhatot\.com/[^?#\s]+/\d+\.htm(?:\?.*)?$", re.IGNORECASE)
DETAIL_PATH_RE = re.compile(
    r"https://(?:www\.)?nhatot\.com/[^\"'?#\s]+/\d+\.htm(?:\?[^\"'#\s]*)?",
    re.IGNORECASE,
)
DEFAULT_OUTPUT = Path("nhatot_list_detail.csv")
OUTPUT_COLUMNS = ["STT", "title", "area", "location", "phone", "price", "url", "source", "listing_date", "category_url"]


def normalize_detail_url(href: str | None) -> str | None:
    if not href:
        return None

    absolute_url = urljoin("https://www.nhatot.com", href.strip())
    absolute_url = absolute_url.split("#", 1)[0]
    if not DETAIL_URL_RE.match(absolute_url):
        return None

    parts = urlsplit(absolute_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))


def build_paginated_url(base_url: str, page_number: int) -> str:
    if page_number < 1:
        raise ValueError("page_number phai >= 1")

    parts = urlsplit(base_url.split("#", 1)[0])
    path = re.sub(r"/p\d+/?$", "", parts.path.rstrip("/"))
    query_pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "page"]

    if page_number > 1:
        query_pairs.append(("page", str(page_number)))

    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query_pairs), ""))


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
        page_source = driver.page_source
        for match in DETAIL_PATH_RE.findall(page_source):
            add_url(match)
    except WebDriverException:
        pass

    return links


def count_detail_link_candidates(driver) -> int:
    try:
        return int(
            driver.execute_script(
                r"""
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                return anchors.filter((anchor) => /\\\/\\d+\\.htm(?:[?#]|$)/i.test(anchor.href || anchor.getAttribute('href') || '')).length;
                """
            )
            or 0
        )
    except WebDriverException:
        return 0


def is_listing_content_ready(driver) -> bool:
    try:
        if count_detail_link_candidates(driver) > 0:
            return True
    except WebDriverException:
        pass

    try:
        body_text = get_body_text(driver)
    except WebDriverException:
        return False

    return len(body_text.strip()) > 200 and not is_security_verification_page(driver)


def wait_for_listing_ready(driver) -> None:
    log("[list_crawler] Dang cho trang danh sach san sang.")
    deadline = time.time() + 15
    while time.time() < deadline:
        if is_listing_content_ready(driver):
            log("[list_crawler] Da thay noi dung danh sach.")
            return
        time.sleep(0.25)

    log("[list_crawler] Trang dang o man hinh security verification hoac chua hien danh sach.")
    log("[list_crawler] Hay hoan tat xac minh trong cua so Chrome, sau do nhan Enter de tiep tuc crawl.")
    input()

    deadline = time.time() + 20
    while time.time() < deadline:
        if is_listing_content_ready(driver):
            log("[list_crawler] Da thay noi dung danh sach.")
            return
        time.sleep(0.25)

    raise RuntimeError("Khong the tai noi dung trang danh sach NhaTot.")


def save_results(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_rows: list[dict] = []
    for index, row in enumerate(rows, start=1):
        normalized_row = {column: row.get(column) for column in OUTPUT_COLUMNS}
        normalized_row["STT"] = index
        normalized_rows.append(normalized_row)
    pd.DataFrame(normalized_rows, columns=OUTPUT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")


def attach_source(row: dict) -> dict:
    normalized_row = dict(row)
    normalized_row["source"] = SOURCE_NAME
    normalized_row["listing_date"] = None
    normalized_row["category_url"] = None
    return normalized_row


def is_hanoi_location(location: str | None) -> bool:
    normalized = normalize_search_text(location)
    return "ha noi" in normalized if normalized else False


def discover_listing_links_on_page(driver, start_url: str, page_number: int) -> list[str]:
    page_url = build_paginated_url(start_url, page_number)
    log(f"[list_crawler] Mo trang danh sach {page_number}: {page_url}")
    driver.get(page_url)
    wait_for_listing_ready(driver)
    dismiss_cookie_banner(driver)
    time.sleep(0.35)

    page_links = collect_detail_links_from_page(driver)
    log(f"[list_crawler] Tim thay {len(page_links)} link tin tren trang {page_number}.")
    return page_links


def crawl_listing_page(start_url: str, page_number: int, output_path: Path | None = None) -> list[dict]:
    if page_number < 1:
        raise ValueError("page_number phai >= 1")

    log("[list_crawler] Khoi tao Chrome.")
    driver = build_driver()
    rows: list[dict] = []
    skipped_non_hanoi = 0

    try:
        detail_urls = discover_listing_links_on_page(driver, start_url, page_number)
        log(f"[list_crawler] Tong so tin se crawl o trang {page_number}: {len(detail_urls)}")

        for index, detail_url in enumerate(detail_urls, start=1):
            log(f"[list_crawler] Crawl chi tiet {index}/{len(detail_urls)}: {detail_url}")
            try:
                row = attach_source(crawl_detail_with_driver(driver, detail_url))
                if not is_hanoi_location(row.get("location")):
                    skipped_non_hanoi += 1
                    log(f"[list_crawler] Bo qua tin khong thuoc Ha Noi: {detail_url}")
                    continue
                rows.append(row)
                if output_path is not None:
                    save_results(rows, output_path)
            except Exception as exc:
                log(f"[list_crawler] Loi voi {detail_url}: {exc}")

        log(f"[list_crawler] Tong so tin bi bo qua vi khong thuoc Ha Noi: {skipped_non_hanoi}")
        return rows
    finally:
        driver.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl mot trang danh sach NhaTot va lay thong tin chi tiet.")
    parser.add_argument("url", help="URL danh sach NhaTot.")
    parser.add_argument("--page-number", type=int, default=1, help="So trang danh sach can crawl.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Duong dan file CSV output.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    output_path = Path(args.output)
    rows = crawl_listing_page(args.url, page_number=args.page_number, output_path=output_path)
    log(f"[list_crawler] Hoan tat. Da luu {len(rows)} dong vao {output_path}")
