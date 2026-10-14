# LinkedIn Profile Scraper

Hệ thống thu thập thông tin **trang cá nhân LinkedIn** từ danh sách URL. Dữ liệu được chuẩn hóa và xuất ra Excel/CSV để đưa vào CRM.

**Dữ liệu lấy được:** họ tên, headline, chức danh và công ty hiện tại, địa điểm, số followers/kết nối, phần giới thiệu (About), kinh nghiệm (gồm nhiều vị trí trong cùng một công ty), học vấn, kỹ năng, ngôn ngữ và chứng chỉ.

---

## 🔎 Tự tìm người dùng tại Việt Nam (không cần danh sách có sẵn)

Lệnh `discover` tự tìm trên LinkedIn theo các từ khoá về bảo hộ lao động, lọc theo quốc gia **Việt Nam**, và **lấy tất cả kết quả** (không chấm điểm, không lọc). Có 2 nguồn:
- **Tìm người:** "bảo hộ lao động", "an toàn lao động", EHS, HSE, Safety Officer/Manager, Purchasing Manager, Procurement, Buyer, GA/Admin Manager, Warehouse Manager, Plant Manager...
- **Tìm bài viết:** tác giả các bài đăng về "bảo hộ lao động", "găng tay bảo hộ", "giày bảo hộ", EHS Vietnam...

```bash
python -m linkedin_scraper login                                   # 1 lần
python -m linkedin_scraper discover                                # bộ từ khoá mặc định, 5 trang/từ khoá
python -m linkedin_scraper discover --pages 10 --max-new 500       # nhiều hơn, dừng khi đủ 500 người mới
python -m linkedin_scraper discover -k "EHS Manager" -k "an toàn lao động" --no-posts   # từ khoá riêng
```

**Kết quả:**
- `data/leads.xlsx`: họ tên, headline (chức danh và công ty), địa điểm, cấp kết nối (1st/2nd/3rd+), link hồ sơ, từ khoá đã tìm ra người đó, trích đoạn bài viết.
- `data/profiles.txt`: danh sách link, dùng tiếp cho lệnh `contacts` để lấy email/SĐT:
  ```bash
  python -m linkedin_scraper contacts -i data/profiles.txt -o data/contacts.xlsx
  ```
- Chạy nhiều lần sẽ **gộp dữ liệu, không trùng người**.

**Giới hạn thực tế:**
- Mỗi trang kết quả có 10 người, và LinkedIn cho xem tối đa 100 trang mỗi từ khoá.
- Tài khoản miễn phí có **giới hạn lượt tìm người mỗi tháng** (commercial use limit). Khi hết lượt, tool tự dừng và giữ dữ liệu đã có. Muốn tìm nhiều hơn thì cần LinkedIn Premium hoặc Sales Navigator.
- Email/SĐT chỉ lấy được với người **đã kết nối** với tài khoản của anh. Với người lạ, file chỉ có tên, chức danh, công ty và link hồ sơ.

---

## ⭐ Chế độ LẤY LIÊN HỆ: tên + email + SĐT (khuyên dùng cho sales)

Chế độ này chỉ lấy những gì cần để liên hệ bán hàng: **họ tên, email, số điện thoại, chức danh, công ty, địa điểm, website, ngày kết nối**. Email doanh nghiệp được ưu tiên hơn gmail/yahoo. Số điện thoại được chuẩn hoá về dạng `+84...`, kèm cột `0912 345 678` để gọi.

### 3 nguồn dữ liệu (dùng kết hợp)

| Nguồn | Lệnh | Rủi ro khóa tài khoản | Ghi chú |
|---|---|---|---|
| **A. File xuất dữ liệu chính thức của LinkedIn** | `import-connections` | **Không có** | Nhanh và an toàn nhất. Có email của mọi kết nối cấp 1 cho phép tải email |
| **B. Hộp "Contact info" trên hồ sơ** | `contacts` | Có, nên giữ dưới 80 hồ sơ/ngày | Lấy được email **và SĐT** mà người đó chia sẻ, thường chỉ với kết nối cấp 1 |
| **C. Phần About / headline** | (tự động trong B) | — | Email/SĐT người đó tự công khai, kể cả kiểu viết né bot như `abc [at] xyz [dot] com` |

> **Kỳ vọng thực tế:** LinkedIn **không** cho xem email/SĐT của người **chưa kết nối**. Với người lạ, hệ thống chỉ lấy được tên, chức danh và công ty, trừ khi họ tự ghi email/SĐT trong About. Với kết nối cấp 1, thường khoảng 30–60% có email, còn SĐT ít hơn nhiều.
> → **Quy trình hiệu quả:** gửi lời mời kết nối tới Purchasing/EHS/Admin của các nhà máy mục tiêu, và khi họ chấp nhận thì chạy `contacts` hoặc `import-connections`.

### A. Lấy từ file xuất dữ liệu của LinkedIn (khuyên làm đầu tiên)
1. LinkedIn → **Settings & Privacy → Data privacy → Get a copy of your data** → chọn **Connections** → Request archive.
2. Khoảng 10 phút sau LinkedIn gửi email kèm link tải file zip, trong đó có `Connections.csv`.
3. Chạy:
```bash
python -m linkedin_scraper import-connections Connections.csv --merge -o data/contacts.xlsx
```

### B. Lấy từ hồ sơ (email + SĐT + thông tin trong hộp Contact info)
```bash
python -m linkedin_scraper login                        # 1 lần
python -m linkedin_scraper contacts -i data/profiles.txt -o data/contacts.xlsx
python -m linkedin_scraper contacts -u https://www.linkedin.com/in/xxx/ --headful   # chạy thử 1 hồ sơ
```

### Xuất danh bạ
```bash
python -m linkedin_scraper export-contacts -o data/contacts.xlsx --only-with-contact   # chỉ người có email/SĐT
python -m linkedin_scraper export-contacts -o data/contacts.csv                        # import CRM / MISA / Sapo
```

**Cột trong file:** `full_name, first_name, last_name, email, email_type (business/personal), emails_all, phone_local, phone, phone_type, phones_all, title, company, headline, location, websites, twitter, address, connected_on, linkedin_url, source, scraped_at, error`

Với tên Việt Nam, `first_name` là tên gọi: "Nguyễn Văn An" cho ra `first_name = An`, tiện cho mail merge kiểu "Chào anh An".

### Cơ chế an toàn của chế độ liên hệ
- Tự dừng ngay khi bị chuyển sang trang đăng nhập/checkpoint, khi gặp mã 429/999, hoặc sau **3 lỗi liên tiếp**.
- Hồ sơ không tồn tại được ghi lại và bỏ qua. Hồ sơ lỗi sẽ được thử lại ở lần chạy sau. Hồ sơ đã lấy xong không bị lấy lại.

---

## 1. Kiến trúc

```
linkedin_scraper/
├── config.py    # Cấu hình từ .env (delay, giới hạn, session)
├── browser.py   # Playwright: đăng nhập (cookie li_at / email+mật khẩu / thủ công), lưu session
├── scraper.py   # Điều phối: mở hồ sơ, cuộn trang, lấy trang /details/, rate limit, chạy tiếp từ chỗ dừng
├── parser.py    # HTML -> dữ liệu có cấu trúc (tách riêng nên test offline được)
├── models.py    # Profile / Experience / Education
├── discovery.py # Tự tìm người dùng VN qua tìm kiếm người + bài viết
├── contacts.py  # Email/SĐT: trích xuất, chuẩn hoá (+84), hộp Contact info, đọc Connections.csv
├── storage.py   # Lưu JSONL (append, chống trùng) + xuất CSV / XLSX / JSON
└── cli.py       # Lệnh: login | scrape | export | parse-html
tests/           # Unit test parser + test end-to-end Playwright với LinkedIn giả lập
```

Luồng xử lý: `danh sách URL → đăng nhập (dùng lại session) → mở từng hồ sơ → cuộn để tải lazy-load → (tuỳ chọn) trang chi tiết → parse → data/profiles.jsonl → export Excel/CSV`

## 2. Cài đặt

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## 3. Đăng nhập (chỉ cần làm 1 lần)

```bash
python -m linkedin_scraper login
```
Một **cửa sổ Chrome riêng của tool** sẽ mở ra. Anh đăng nhập LinkedIn trong cửa sổ đó như bình thường, kể cả nhập mã xác minh nếu LinkedIn hỏi. Khi vào tới trang Feed, tool tự nhận, lưu phiên và đóng cửa sổ.

- Phiên đăng nhập được lưu trong `auth/browser-profile`. Các lần chạy sau **không cần đăng nhập lại**.
- **Không cần cookie `li_at` nữa.** Chép cookie từ Chrome của anh sang tool làm cùng một phiên chạy ở 2 trình duyệt khác nhau, và LinkedIn sẽ huỷ phiên đó (lỗi `ERR_TOO_MANY_REDIRECTS`). Nên để trống dòng `LINKEDIN_LI_AT` trong `.env`.
- Gặp lỗi `ERR_TOO_MANY_REDIRECTS` hoặc thông báo "Phiên đăng nhập đã bị huỷ" thì chạy lại `login`, rồi nghỉ 15–30 phút trước khi chạy tiếp.

> Nên dùng **tài khoản phụ**, không dùng tài khoản chính của công ty hay của CEO, vì tài khoản dùng để scrape có thể bị LinkedIn hạn chế.

## 4. Thu thập dữ liệu

```bash
# Danh sách URL trong file (mỗi dòng 1 link, xem data/profiles.example.txt)
python -m linkedin_scraper scrape -i data/profiles.txt -o data/profiles.xlsx

# Một vài URL lẻ
python -m linkedin_scraper scrape -u https://www.linkedin.com/in/xxx/ -u https://www.linkedin.com/in/yyy/

# Đầy đủ: lấy thêm toàn bộ kinh nghiệm/kỹ năng từ các trang /details/ (chậm hơn khoảng 5 lần)
python -m linkedin_scraper scrape -i data/profiles.txt --details

# Xuất lại bất kỳ lúc nào
python -m linkedin_scraper export -o data/profiles.xlsx    # hoặc .csv / .json
```

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--details` | Lấy trang chi tiết: kinh nghiệm, học vấn, kỹ năng, ngôn ngữ, chứng chỉ đầy đủ |
| `--limit N` | Tối đa N hồ sơ cho lần chạy này |
| `--no-resume` | Thu thập lại cả hồ sơ đã có (mặc định sẽ bỏ qua) |
| `--save-html` | Lưu HTML gốc vào `data/html/` để debug khi LinkedIn đổi giao diện |
| `--headful` | Hiện trình duyệt để theo dõi |

**File Excel đầu ra** có 3 sheet: `Profiles` (1 dòng/người, sẵn để import CRM), `Experiences` và `Educations`.

## 5. Cơ chế an toàn và chống bị khóa tài khoản

- Delay ngẫu nhiên 8–20 giây giữa các hồ sơ (`MIN/MAX_DELAY_SECONDS`), cuộn trang như người dùng thật.
- Giới hạn `MAX_PROFILES_PER_RUN=80` hồ sơ mỗi lần chạy. Khuyến nghị **không vượt quá 80–100 hồ sơ/ngày/tài khoản**.
- Tự **dừng ngay** khi gặp authwall, checkpoint hoặc lỗi 429. Tiến độ đã lưu, lần sau chạy tiếp từ chỗ dừng.
- Lỗi ở một hồ sơ không làm dừng cả lượt chạy; hồ sơ lỗi được ghi lại kèm nguyên nhân và sẽ được thử lại ở lần sau.

## 5b. Lỗi kết nối: `ERR_CONNECTION_RESET`, `ERR_TIMED_OUT`, `ERR_TUNNEL_CONNECTION_FAILED`

Các lỗi này do **mạng hoặc phần mềm trên máy**, không phải do cookie. Tool tự thử lại 3 lần, sau đó in hướng dẫn. Kiểm tra lần lượt:
1. Mở Chrome trên cùng máy, vào `https://www.linkedin.com/feed/`. Nếu **không vào được** thì đổi mạng (WiFi khác hoặc 4G phát từ điện thoại) và tắt VPN.
2. Nếu Chrome vào được bình thường, cho tool dùng Chrome/Edge có sẵn trên máy bằng cách thêm vào `.env`:
   ```
   BROWSER_CHANNEL=chrome
   ```
   hoặc `BROWSER_CHANNEL=msedge`.
3. **Antivirus** (Kaspersky, ESET, Avast, Bitdefender...) có thể chặn trình duyệt tự động: tạm tắt "quét HTTPS/Web Shield" hoặc thêm ngoại lệ cho `python.exe`.
4. Mạng công ty dùng proxy: thêm `PROXY_SERVER=http://ip:port` vào `.env`. Nếu proxy có đăng nhập thì thêm cả `PROXY_USERNAME` và `PROXY_PASSWORD`.

## 6. Khi LinkedIn thay đổi giao diện

1. Chạy lại với `--save-html` để lưu trang gốc.
2. Kiểm tra parser offline: `python -m linkedin_scraper parse-html data/html/<id>.html --url <link>`
3. Chỉnh `parser.py` (selector đặt tập trung ở đầu file) rồi chạy `pytest`.

## 7. Test

```bash
pytest -q
# Nếu dùng Chrome có sẵn thay cho Chromium của Playwright:
CHROMIUM_EXECUTABLE=/path/to/chrome pytest -q
```

Test end-to-end chạy Playwright thật nhưng chặn mọi request tới linkedin.com và trả về HTML mẫu, nên không cần tài khoản và không gửi request tới LinkedIn.

## 8. Lưu ý pháp lý (bắt buộc đọc)

- **Điều khoản LinkedIn** (User Agreement §8.2) cấm thu thập dữ liệu bằng công cụ tự động. Tài khoản dùng để scrape có thể bị hạn chế hoặc khóa vĩnh viễn. Bạn tự chịu trách nhiệm khi sử dụng.
- **Nghị định 13/2023/NĐ-CP** về bảo vệ dữ liệu cá nhân (và Luật Bảo vệ dữ liệu cá nhân 2025): họ tên, nơi làm việc, quá trình công tác là *dữ liệu cá nhân*. Doanh nghiệp phải có mục đích xử lý rõ ràng, bảo mật dữ liệu, cho phép chủ thể yêu cầu xoá, và **không được mua bán dữ liệu**. Với dữ liệu cá nhân của công dân EU, GDPR cũng áp dụng.
- Hệ thống chỉ lấy email/SĐT mà **tài khoản của bạn được LinkedIn cho phép xem**: do người đó chia sẻ với kết nối, hoặc tự công khai. Nó không dò hay đoán email và không vượt qua giới hạn quyền xem.
- Email/SĐT cá nhân là dữ liệu cá nhân theo NĐ 13/2023. Chỉ dùng để liên hệ công việc B2B, ghi rõ nguồn khi liên hệ lần đầu, và xoá ngay khi người đó yêu cầu. Không bán hay chia sẻ cho bên thứ ba.
- Kênh hợp lệ thay thế hoặc bổ sung: **LinkedIn Sales Navigator** (lưu lead, InMail) và **Apollo.io / Lusha** (email doanh nghiệp đã xác minh, có API tích hợp CRM).
