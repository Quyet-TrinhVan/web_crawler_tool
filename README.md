# Web Crawler Tool

Selenium crawler cho 2 nguon:

- `batdongsan.com.vn`
- `nhatot.com`

Repo hien tai ho tro:

- crawl chi tiet 1 tin
- crawl 1 trang danh sach roi tu lay tat ca link con de crawl detail
- xuat CSV `utf-8-sig` de mo tot bang Excel

## Yeu cau

- Python `>= 3.12`
- Google Chrome da cai tren may
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

`main.py` crawl dung mot trang danh sach cu the.

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
uv run main.py --source nhatot.com --page-url "https://www.nhatot.com/mua-ban-can-ho-chung-cu-tp-ho-chi-minh" --page-number 1 --output nhatot.csv
```

Schema CSV hien tai:

- `title`
- `area`
- `location`
- `phone`
- `price`
- `url`
- `source`

## Batdongsan: dang nhap truoc khi crawl

Batdongsan can profile Chrome da dang nhap va da xac minh so dien thoai. Hien tai `main.py` khong tu login; can chay script login truoc.

```powershell
uv run -m core.login_batdongsan
```

Flow:

1. Chrome mo voi persistent profile tai `browser_state/chrome_profile`
2. Ban tu vuot qua Cloudflare, dang nhap, xac minh so dien thoai neu can
3. Quay lai terminal va nhan `Enter`
4. Sau do chay `main.py`

Profile nay se duoc tai su dung cho cac lan crawl Batdongsan sau.

## Chay tung script rieng

### Batdongsan detail

```powershell
uv run -m core.detail_crawler
```

Script nay dang dung san URL mau trong file.

### Batdongsan list

```powershell
uv run -m core.crawl_batdongsan_list "https://batdongsan.com.vn/nha-dat-ban" --max-pages 2 --output batdongsan_list_detail.csv
```

### NhaTot detail

```powershell
uv run -m core.nhatot_detail_crawler "https://www.nhatot.com/mua-ban-can-ho-chung-cu-quan-7-tp-ho-chi-minh/131656948.htm" --output nhatot_detail.csv
```

### NhaTot list

```powershell
uv run -m core.crawl_nhatot_list "https://www.nhatot.com/mua-ban-can-ho-chung-cu-tp-ho-chi-minh" --page-number 1 --output nhatot_list_detail.csv
```

## Browser profile

Project dung persistent Chrome profile de giu session:

- Batdongsan: `browser_state/chrome_profile`
- NhaTot: `browser_state/nhatot_chrome_profile`

Neu profile bi loi hoac dinh session cu, co the xoa thu muc profile tuong ung roi chay lai.

## Luu y

- Cac site co the hien Cloudflare hoac security verification. Khi do script se cho ban xu ly trong cua so Chrome.
- Selector HTML cua cac site co the thay doi, nen neu dang crawl tot ma dot nhien loi thi thuong la do layout/site thay doi.
- Batdongsan co the can tai khoan da xac minh so dien thoai de hien full phone.
- NhaTot thuong cho click hien so ma khong can dang nhap, nhung van co the bi security verification.

## Output

CSV duoc ghi voi encoding `utf-8-sig`.

Vi du doc ket qua:

```powershell
Get-Content .\batdongsan.csv | Select-Object -First 5
```
