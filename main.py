import argparse
from pathlib import Path

import pandas as pd

from core.crawl_batdongsan_list import crawl_listing_page as crawl_batdongsan_listing_page
from core.crawl_nhatot_list import crawl_listing_page as crawl_nhatot_listing_page


ROW_COLUMNS = ["title", "area", "location", "phone", "price", "url", "source"]


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


def normalize_rows(rows: list[dict], source: str) -> list[dict]:
    normalized_rows: list[dict] = []
    for row in rows:
        normalized_row = {column: row.get(column) for column in ROW_COLUMNS}
        normalized_row["source"] = row.get("source") or source
        normalized_rows.append(normalized_row)
    return normalized_rows


def save_rows(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=ROW_COLUMNS)
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLI tong de crawl Batdongsan va NhaTot theo tung trang danh sach.")
    parser.add_argument("--source", required=True, choices=["batdongsan.com", "nhatot.com"], help="Nguon du lieu can crawl.")
    parser.add_argument("--page-url", required=True, help="Parent URL cua trang danh sach.")
    parser.add_argument("--page-number", required=True, type=positive_int, help="So trang danh sach can crawl.")
    parser.add_argument("--output", required=True, help="Duong dan file CSV output.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
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
