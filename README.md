# LinkedIn Profile Scraper

Hệ thống thu thập thông tin **trang cá nhân LinkedIn** từ danh sách URL. Dữ liệu được chuẩn hóa và xuất ra Excel/CSV để đưa vào CRM.

**Dữ liệu lấy được:** họ tên, headline, chức danh và công ty hiện tại, địa điểm, số followers/kết nối, phần giới thiệu (About), kinh nghiệm (gồm nhiều vị trí trong cùng một công ty), học vấn, kỹ năng, ngôn ngữ và chứng chỉ.

---

## 1. Kiến trúc

```
linkedin_scraper/
├── config.py    # Cấu hình từ .env (delay, giới hạn, session)
├── browser.py   # Playwright: đăng nhập (cookie li_at / email+mật khẩu / thủ công), lưu session
├── scraper.py   # Điều phối: mở hồ sơ, cuộn trang, lấy trang /details/, rate limit, chạy tiếp từ chỗ dừng
├── parser.py    # HTML -> dữ liệu có cấu trúc (tách riêng nên test offline được)
├── models.py    # Profile / Experience / Education
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

## 3. Đăng nhập (chỉ cần làm 1 lần, session lưu ở `auth/state.json`)

**Cách 1, khuyến nghị: dùng cookie `li_at`**
1. Đăng nhập LinkedIn trên Chrome.
2. Mở F12 → Application → Cookies → `https://www.linkedin.com` và copy giá trị `li_at`.
3. Dán vào `.env`: `LINKEDIN_LI_AT=...`
4. Chạy `python -m linkedin_scraper login`

**Cách 2: dùng email/mật khẩu** (điền vào `.env`) rồi chạy `python -m linkedin_scraper login --headful`. Nếu LinkedIn hỏi captcha/2FA, bạn tự xử lý trong cửa sổ trình duyệt (có 3 phút).

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
- Chỉ thu thập **thông tin công khai trên hồ sơ**. Hệ thống này không lấy email hay số điện thoại.
- Kênh hợp lệ thay thế hoặc bổ sung: **LinkedIn Sales Navigator** (lưu lead, InMail) và **Apollo.io / Lusha** (email doanh nghiệp đã xác minh, có API tích hợp CRM).
