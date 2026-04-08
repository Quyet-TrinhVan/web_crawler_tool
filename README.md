# Web Crawler Tool

Tool crawl tin bat dong san bang Selenium cho 2 nguon:

- `batdongsan.com.vn`
- `nhatot.com`

Project hien tai ho tro:

- crawl chi tiet 1 tin
- crawl 1 trang danh sach va tu lay link detail trong trang do
- crawl Batdongsan theo che do `today` tren nhieu category
- xuat CSV encoding `utf-8-sig` de mo bang Excel

## Yeu cau

- Python `>= 3.12`
- Google Chrome
- `uv`

## Cai dat

Tai thu muc project:

```powershell
uv sync
```

Neu can tao lai lock file:

```powershell
uv lock
uv sync
```

## Cau truc chinh

- [main.py](/d:/web_crawler_tool/main.py): CLI tong de crawl theo `source`
- [core/detail_crawler.py](/d:/web_crawler_tool/core/detail_crawler.py): detail crawler cho Batdongsan
- [core/crawl_batdongsan_list.py](/d:/web_crawler_tool/core/crawl_batdongsan_list.py): list crawler cho Batdongsan
- [core/login_batdongsan.py](/d:/web_crawler_tool/core/login_batdongsan.py): mo Chrome profile de login Batdongsan
- [core/nhatot_detail_crawler.py](/d:/web_crawler_tool/core/nhatot_detail_crawler.py): detail crawler cho NhaTot
- [core/crawl_nhatot_list.py](/d:/web_crawler_tool/core/crawl_nhatot_list.py): list crawler cho NhaTot

## Chay bang `main.py`

`main.py` la entry point chinh. Co 2 mode:

- crawl 1 trang danh sach cu the
- crawl Batdongsan theo `--date today`

### 1. Crawl 1 trang danh sach

Argument:

- `--source`: `batdongsan.com` hoac `nhatot.com`
- `--page-url`: URL danh sach goc
- `--page-number`: so trang can crawl, phai `>= 1`
- `--output`: duong dan file CSV output

Vi du Batdongsan:

```powershell
uv run main.py --source batdongsan.com --page-url "https://batdongsan.com.vn/nha-dat-ban" --page-number 1 --output batdongsan.csv
```

Vi du NhaTot:

```powershell
uv run main.py --source nhatot.com --page-url "<url-danh-sach-ha-noi>" --page-number 1 --output nhatot.csv
```

Luu y voi `nhatot.com`: crawler hien chi giu cac tin co `location` thuoc Ha Noi. Neu URL danh sach khong phai khu vuc Ha Noi thi output co the rong.

### 2. Crawl Batdongsan theo ngay hien tai

Mode nay chi ho tro cho `--source batdongsan.com`.

Argument:

- `--source batdongsan.com`
- `--date today`
- `--output`

Vi du:

```powershell
uv run main.py --source batdongsan.com --date today --output batdongsan_today.csv
```

Trong mode nay:

- script duyet danh sach category co san trong code
- chi giu cac tin dang trong ngay hien tai
- chi giu tin co `location` thuoc Ha Noi
- `--page-url` va `--page-number` khong can truyen

## Batdongsan: login truoc khi crawl

Batdongsan can Chrome profile da dang nhap. De tao profile persistent:

```powershell
uv run -m core.login_batdongsan
```

Flow:

1. Chrome mo voi profile tai `browser_state/chrome_profile`
2. Ban tu vuot qua Cloudflare, dang nhap, xac minh neu can
3. Quay lai terminal va nhan `Enter`
4. Sau do chay crawler

Profile nay se duoc tai su dung cho cac lan crawl Batdongsan sau.

## Chay tung script rieng

### Batdongsan detail

```powershell
uv run -m core.detail_crawler
```

Script nay dang dung URL mau hard-code trong file [core/detail_crawler.py](/d:/web_crawler_tool/core/detail_crawler.py).

### Batdongsan list

```powershell
uv run -m core.crawl_batdongsan_list "https://batdongsan.com.vn/nha-dat-ban" --max-pages 2 --limit 20 --output batdongsan_list_detail.csv
```

Argument chinh:

- `url`: URL danh sach
- `--max-pages`: so trang toi da can quet
- `--limit`: gioi han so tin detail can crawl
- `--output`: file CSV output

### NhaTot detail

```powershell
uv run -m core.nhatot_detail_crawler "https://www.nhatot.com/mua-ban-can-ho-chung-cu-quan-7-tp-ho-chi-minh/131656948.htm" --output nhatot_detail.csv
```

Neu khong truyen URL, script se dung `DEFAULT_URL` trong file [core/nhatot_detail_crawler.py](/d:/web_crawler_tool/core/nhatot_detail_crawler.py).

### NhaTot list

```powershell
uv run -m core.crawl_nhatot_list "<url-danh-sach-ha-noi>" --page-number 1 --output nhatot_list_detail.csv
```

## Schema CSV

Tat ca output deu ghi theo cot:

- `STT`
- `title`
- `area`
- `location`
- `phone`
- `price`
- `url`
- `source`
- `listing_date`
- `category_url`

Ghi chu:

- `source` se la `batdongsan.com` hoac `nhatot.com`
- `listing_date` va `category_url` chu yeu co y nghia voi mode Batdongsan `--date today`
- voi NhaTot, `listing_date` va `category_url` hien tai de trong
- voi `nhatot.com`, output hien chi giu cac tin co `location` thuoc Ha Noi

## Browser profile

Project dung persistent Chrome profile de giu session:

- Batdongsan: `browser_state/chrome_profile`
- NhaTot: `browser_state/nhatot_chrome_profile`

Neu profile bi loi hoac dinh session cu, co the xoa thu muc profile tuong ung roi chay lai.

## Luu y

- Site co the hien Cloudflare hoac security verification. Khi do script se doi ban xu ly trong cua so Chrome va nhan `Enter` de tiep tuc.
- Selector HTML co the thay doi. Neu dang crawl binh thuong ma dot nhien loi, kha nang cao la layout trang da doi.
- Batdongsan thuong can tai khoan da dang nhap de lay so dien thoai.
- NhaTot khong can login trong code hien tai, nhung van co the gap security verification.

## Xem ket qua

CSV duoc ghi voi encoding `utf-8-sig`.

Vi du doc nhanh 5 dong dau:

```powershell
Get-Content .\batdongsan.csv | Select-Object -First 5
```
