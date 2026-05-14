import argparse
from pathlib import Path

import pandas as pd

from core.crawl_batdongsan_list import (
    crawl_categories_for_today as crawl_batdongsan_categories_for_today,
    crawl_listing_page as crawl_batdongsan_listing_page,
)
from core.crawl_nhatot_list import crawl_listing_page as crawl_nhatot_listing_page


ROW_COLUMNS = ["STT", "title", "area", "location", "phone", "price", "listing_date", "category_url", "url"]


def log(message: str) -> None:
    print(f"[main] {message}", flush=True)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--page-number phai >= 1")
    return parsed


def get_crawler(source: str):
    crawlers = {
        "batdongsan.com": crawl_batdongsan_listing_page,
        "nhatot.com": crawl_nhatot_listing_page,
    }
    return crawlers[source]


def get_date_crawler(source: str, date_filter: str | None):
    if date_filter != "today":
        return None
    if source != "batdongsan.com":
        raise ValueError("--date today hien chi ho tro cho source batdongsan.com")
    return crawl_batdongsan_categories_for_today


def normalize_rows(rows: list[dict], source: str) -> list[dict]:
    normalized_rows: list[dict] = []
    for index, row in enumerate(rows, start=1):
        normalized_row = {column: row.get(column) for column in ROW_COLUMNS}
        normalized_row["STT"] = index
        normalized_rows.append(normalized_row)
    return normalized_rows


def save_rows(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=ROW_COLUMNS)
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLI tong de crawl Batdongsan va NhaTot theo tung trang danh sach.")
    parser.add_argument("--source", required=True, choices=["batdongsan.com", "nhatot.com"], help="Nguon du lieu can crawl.")
    parser.add_argument("--page-url", help="Parent URL cua trang danh sach.")
    parser.add_argument("--page-number", type=positive_int, help="So trang danh sach can crawl.")
    parser.add_argument("--date", choices=["today"], help="Che do crawl theo ngay.")
    parser.add_argument("--output", required=True, help="Duong dan file CSV output.")
    args = parser.parse_args()

    if args.date == "today":
        if args.source != "batdongsan.com":
            parser.error("--date today hien chi ho tro cho --source batdongsan.com")
        return args

    if not args.page_url:
        parser.error("--page-url la bat buoc neu khong dung --date today")
    if args.page_number is None:
        parser.error("--page-number la bat buoc neu khong dung --date today")

    return args


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)

    if args.date == "today":
        crawler = get_date_crawler(args.source, args.date)
        log(f"Bat dau crawl source={args.source}, date={args.date}")
        rows = crawler(output_path=output_path)
    else:
        crawler = get_crawler(args.source)
        log(f"Bat dau crawl source={args.source}, page={args.page_number}")
        log(f"Page URL: {args.page_url}")
        rows = crawler(args.page_url, page_number=args.page_number, output_path=output_path)

    normalized_rows = normalize_rows(rows, source=args.source)
    save_rows(normalized_rows, output_path)

    log(f"Hoan tat. Crawl thanh cong {len(normalized_rows)} tin.")
    log(f"Da luu CSV vao {output_path}")


if __name__ == "__main__":
    main()
