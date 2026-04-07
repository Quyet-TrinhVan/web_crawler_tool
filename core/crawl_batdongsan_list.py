import argparse
import re
import time
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


DETAIL_URL_RE = re.compile(r"^https://batdongsan\.com\.vn/.+-pr\d+(?:\?.*)?$", re.IGNORECASE)
DETAIL_PATH_RE = re.compile(r"https://batdongsan\.com\.vn/[^\"'?#\s]+-pr\d+(?:\?[^\"'#\s]*)?", re.IGNORECASE)
DEFAULT_OUTPUT = Path("batdongsan_list_detail.csv")
SOURCE_NAME = "batdongsan.com"


def normalize_detail_url(href: str | None) -> str | None:
    if not href:
        return None

    absolute_url = urljoin("https://batdongsan.com.vn", href.strip())
    absolute_url = absolute_url.split("#", 1)[0]
    if not DETAIL_URL_RE.match(absolute_url):
        return None

    parts = urlsplit(absolute_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))


def build_paginated_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url

    parts = urlsplit(base_url)
    path = re.sub(r"/p\d+/?$", "", parts.path.rstrip("/"))

    query_pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "page"]
    paginated_path = f"{path}/p{page_number}"

    return urlunsplit((parts.scheme, parts.netloc, paginated_path, urlencode(query_pairs), ""))


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


def save_results(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_path, index=False, encoding="utf-8-sig")


def attach_source(row: dict) -> dict:
    normalized_row = dict(row)
    normalized_row["source"] = SOURCE_NAME
    return normalized_row


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
