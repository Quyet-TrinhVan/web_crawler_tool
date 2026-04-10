# Web Crawler Tool

Tool crawl tin bat dong san bang Selenium cho 2 nguon:

- `batdongsan.com.vn`
- `nhatot.com`

Project hien tai ho tro:

- crawl chi tiet 1 tin
- crawl 1 trang danh sach va tu lay link detail trong trang do
- crawl Batdongsan theo che do `today` tren nhieu category
- xuat CSV encoding `utf-8-sig` de mo bang Excel
- chay Web UI de login/crawl trong browser noVNC va tai CSV truc tiep

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

## Chay bang Docker + noVNC

Project da co san stack Docker de chay FastAPI + Selenium + Chromium trong cung mot container.

Chay:

```powershell
docker compose up --build
```

Sau khi container len:

- Web UI: `http://localhost:8000`
- Browser noVNC: `http://localhost:6080/vnc.html?autoconnect=1&resize=remote`

Flow Batdongsan moi:

1. Mo Web UI
2. Bam `Mo browser login`
3. Dang nhap/xac minh trong khung noVNC
4. Bam `Toi da hoan tat, tiep tuc`
5. Bat dau crawl
6. Khi job xong, bam `Tai CSV` de tai file output

Thu muc `browser_state/` duoc mount vao container de giu Chrome profile qua cac lan restart.
Docker se dung profile rieng trong `browser_state/docker/` de tranh conflict voi Chrome/Chromium chay local tren Windows.

## Web UI

Web UI hien tai ho tro:

- mo browser login Batdongsan trong container
- theo doi log crawl realtime
- resume sau khi tu xu ly Cloudflare/security verification
- tai file CSV output truc tiep tu UI sau khi crawl xong

Luu y:

- nut `Tai CSV` chi xuat hien sau khi job crawl thanh cong
- file duoc tai tu duong dan `output` ma ban nhap tren form
- chi cac file nam trong thu muc project moi duoc phep download qua API

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

Flow local khong qua Docker:

1. Chrome mo voi profile tai `browser_state/chrome_profile`
2. Ban tu vuot qua Cloudflare, dang nhap, xac minh neu can
3. Quay lai terminal va nhan `Ctrl+C`
4. Sau do chay crawler

Profile nay se duoc tai su dung cho cac lan crawl Batdongsan sau.
Neu chay bang Docker, session se duoc luu rieng trong `browser_state/docker/chrome_profile`.

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
- `listing_date`
- `category_url`

Ghi chu:

- `listing_date` va `category_url` chu yeu co y nghia voi mode Batdongsan `--date today`
- voi NhaTot, `listing_date` va `category_url` hien tai de trong
- voi `nhatot.com`, output hien chi giu cac tin co `location` thuoc Ha Noi

## Browser profile

Project dung persistent Chrome profile de giu session:

- local Batdongsan: `browser_state/chrome_profile`
- local NhaTot: `browser_state/nhatot_chrome_profile`
- Docker Batdongsan: `browser_state/docker/chrome_profile`
- Docker NhaTot: `browser_state/docker/nhatot_chrome_profile`

Neu profile bi loi hoac dinh session cu, co the xoa thu muc profile tuong ung roi chay lai.

## Luu y

- Site co the hien Cloudflare hoac security verification. Khi do Web UI se doi ban xu ly trong browser/noVNC va bam nut tiep tuc.
- Selector HTML co the thay doi. Neu dang crawl binh thuong ma dot nhien loi, kha nang cao la layout trang da doi.
- Batdongsan thuong can tai khoan da dang nhap de lay so dien thoai.
- NhaTot khong can login trong code hien tai, nhung van co the gap security verification.

## Xem ket qua

CSV duoc ghi voi encoding `utf-8-sig`.

Neu chay bang Web UI:

- sau khi crawl xong, UI se hien ten file output
- bam `Tai CSV` de download file ve may

Neu chay bang CLI, co the doc nhanh 5 dong dau:

```powershell
Get-Content .\batdongsan.csv | Select-Object -First 5
```
