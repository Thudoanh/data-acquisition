# Data acquisition và chuẩn bị evaluation dataset

Repo gồm hai pipeline cho **Area Violation Detection**:

- **Acquisition** theo dõi các YouTube channel public, tải video/livestream vào raw candidate pool theo luồng **Discover → Download/Record → Recover → Validate → Catalog**.
- **Evaluation data** kiểm kê, review, cắt và QC clip; chuẩn hóa metadata; chọn shortlist; gán ROI/event; chia split, kiểm tra và đóng băng **VN-SIDEWALK-EVAL-v1**.

Video trong raw pool và shortlist chỉ là candidate, chưa được xác nhận là dữ liệu vi phạm. Tín hiệu selection không thay thế nhãn do người review.

Luồng tổng thể:

```text
YouTube/public raw data → acquisition → candidate pool → review + trim
→ technical QC → canonical metadata → shortlist → manual review
→ ROI + event GT → split + validation → VN-SIDEWALK-EVAL-v1
```

## Kiến trúc

```text
Channel registry (YAML) → watcher (yt-dlp metadata) → normalization → SQLite catalog
                                                               ↓
                                                state comparison / jobs
                                                  ↓        ↓         ↓
                                               VOD      live     upcoming
                                               worker   worker   waiting
                                                  └── validation + SHA-256 ──→ raw pool
```

Watcher chỉ lấy metadata và xếp job. `ThreadPoolExecutor` chạy tối đa 4 worker độc lập; mỗi worker gọi `yt-dlp` qua subprocess. Vì vậy watcher có thể tiếp tục quét khi nhiều livestream đang ghi và video thường đang tải. SQLite dùng WAL, transaction `BEGIN IMMEDIATE`, khóa duy nhất trên `video_id` và unique index cho job đang active để tránh xếp trùng. Một instance watcher là cách vận hành được hỗ trợ trong MVP.

Mặc định `selection.mode: allowlist`: watcher chỉ xử lý URL/ID trong `channels[].video_ids` hoặc tiêu đề khớp `title_keywords`, `live_keywords`, `vod_keywords`. Tất cả danh sách rỗng nghĩa là không tải/ghi gì. Khi có từ khóa, watcher xem 50 mục mới nhất trên mỗi tab `/videos` và `/streams`, lọc tiêu đề từ danh sách phẳng trước khi lấy metadata chi tiết. Lỗi ở một video hoặc tab được log và watcher tiếp tục; video không lấy được metadata chi tiết không được xếp tải. `selection.mode: all` là lựa chọn chủ động để xử lý mọi mục trong phạm vi 50 mục gần nhất mỗi tab. Truy vấn tab và media worker có retry hữu hạn. Khi khởi động lại, job `RUNNING` được đưa về hàng đợi; item đang `DOWNLOADING`, `RECORDING`, `VALIDATING` được đưa về `QUEUED` hoặc `RETRY`. Job cũ ngoài bộ lọc bị `CANCELLED`, còn metadata item được giữ lại.

## Cài đặt

Yêu cầu Python 3.10+, `yt-dlp`, PyYAML, `certifi`, `ffmpeg` và `ffprobe` trên PATH. `ffprobe` đi cùng bản cài `ffmpeg`; `certifi` cung cấp CA bundle cho các bản Python không có sẵn chứng chỉ TLS.

macOS (Homebrew):

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Máy macOS Intel không có Homebrew có thể lấy hai bản ZIP `ffmpeg` và `ffprobe` từ [trang static build được FFmpeg dẫn tới](https://evermeet.cx/ffmpeg/), rồi giải nén hai executable vào `.venv/bin/`. Sau khi kích hoạt `.venv`, kiểm tra bằng `ffmpeg -version` và `ffprobe -version`. Nếu tạo lại `.venv`, cần cài lại hai binary này.

Ubuntu/Debian:

```bash
sudo apt update && sudo apt install ffmpeg python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell (cài ffmpeg bằng `winget` hoặc package manager tương đương; mở terminal mới để PATH có hiệu lực):

```powershell
winget install Gyan.FFmpeg
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Môi trường ảo khi dùng nhiều terminal

Nếu `.venv` của project **đã được kích hoạt trong terminal hiện tại**, cứ chạy các script; không cần `deactivate` rồi kích hoạt lại. Kiểm tra trên macOS/Linux:

```bash
which python
which ffmpeg
which ffprobe
```

Các lệnh `which` nên trỏ tới executable trong `.venv/bin/` hoặc bản `ffmpeg`/`ffprobe` đã cài trên hệ thống. `ffmpeg -version` và `ffprobe -version` xác nhận hai chương trình chạy được. Mỗi **terminal mới** cần kích hoạt `.venv` riêng bằng `source .venv/bin/activate`; việc kích hoạt ở terminal thứ nhất không tự áp dụng cho terminal thứ hai. Sau đó có thể để watcher chạy ở một terminal và chạy `download_video.py --file videos.txt --channel 0511_vietnam` ở terminal khác.

Trên Windows PowerShell, kiểm tra đường dẫn bằng `Get-Command python, ffmpeg, ffprobe`. Nếu `ffmpeg`/`ffprobe` được cài toàn hệ thống thay vì trong `.venv`, đường dẫn của chúng có thể nằm ngoài project; điều cần thiết là cả hai lệnh chạy được trong terminal đang sử dụng.

## Sử dụng

Các lệnh dưới đây chạy tại thư mục `data_acquisition/`. Kích hoạt `.venv` nếu terminal đó chưa kích hoạt; nếu đã kích hoạt thì chạy luôn.

Lần đầu sử dụng, tạo SQLite catalog bằng `python scripts/init_catalog.py`.

### A. Tải một video theo URL hoặc ID

```bash
python scripts/download_video.py 'https://www.youtube.com/watch?v=VIDEO_ID' --channel 0511_vietnam
```

Thay `VIDEO_ID` bằng ID thật; cũng có thể truyền thẳng ID thay cho URL. Script tải **chỉ item này** và chờ hoàn tất; nếu là live đang chạy, nó ghi đến khi live kết thúc. Không cần thêm URL vào YAML. Với nhiều channel trong config, `--channel` xác định channel cho video chưa có trong catalog.

### B. Tải một danh sách URL/ID

Tạo `videos.txt` (UTF-8), mỗi dòng một URL hoặc ID; dòng trống và dòng bắt đầu bằng `#` được bỏ qua:

```text
# VOD cần tải
https://www.youtube.com/watch?v=VIDEO_ID_1
VIDEO_ID_2
```

Thay `VIDEO_ID_1` và `VIDEO_ID_2` bằng ID thật trước khi chạy:

```bash
python scripts/download_video.py --file videos.txt --channel 0511_vietnam
```

Script xử lý **lần lượt**, bỏ qua ID trùng trong tệp và item đã hoàn tất, tiếp tục sau từng lỗi, rồi in tổng kết. Nếu một mục là live đang chạy, script chờ live kết thúc trước khi tới dòng tiếp theo. Danh sách thủ công chạy độc lập với bộ lọc từ khóa.

### C. Theo dõi các URL/ID đã chọn trong YAML

Ví dụ chỉ theo dõi hai video/live của channel mẫu:

```yaml
selection:
  mode: allowlist
channels:
  - id: 0511_vietnam
    url: https://www.youtube.com/@0511.VietNam
    enabled: true
    video_ids:
      - NUGc3nLuGsI
      - https://www.youtube.com/watch?v=oC8ttZHG50I
```

Sau khi sửa `configs/youtube_sources.yaml`, chạy:

```bash
python scripts/scan_channel.py --channel 0511_vietnam
python scripts/inspect_catalog.py --status QUEUED
python scripts/run_watcher.py --config configs/youtube_sources.yaml
```

`scan_channel.py` chỉ cập nhật metadata và xếp job, **không tải media**. `run_watcher.py` mới bắt đầu tải/ghi và tiếp tục kiểm tra các live sắp diễn ra. Watcher lấy trạng thái `not_live`, `is_live`, `is_upcoming`, `was_live` cho từng ID và chọn download, record, wait hoặc replay recovery tương ứng. Muốn xem ID gần đây trước khi chọn, chạy `python scripts/scan_channel.py --channel 0511_vietnam --discover --limit 20`; lệnh `--discover` không xếp job. Sau khi sửa YAML trong lúc watcher đang chạy, khởi động lại watcher để nhận cấu hình mới.

### D. Theo dõi theo từ khóa tiêu đề

Bạn cũng có thể chọn theo từ khóa, không cần dán URL:

```yaml
selection:
  mode: allowlist
channels:
  - id: 0511_vietnam
    url: https://www.youtube.com/@0511.VietNam
    enabled: true
    video_ids: []
    title_keywords: []
    live_keywords:
      - "cau rong"
      - "tam ky"
    vod_keywords:
      - "benh vien c"
```

Mỗi list dùng điều kiện **tiêu đề chứa ít nhất một cụm từ**, so sánh không phân biệt hoa thường và dấu tiếng Việt. `live_keywords` nhận live đang chạy, live sắp diễn ra và replay; `vod_keywords` chỉ nhận video thường; `title_keywords` nhận cả hai. `video_ids` vẫn chọn chính xác từng URL/ID bất kể tiêu đề. Chạy cùng ba lệnh ở mục C để xem trước job rồi bắt đầu tải. Tất cả item khớp từ khóa có thể được xếp job; muốn quét toàn channel không lọc, đổi `selection.mode` thành `all` một cách chủ động.

### E. Chạy watcher và tải thủ công ở hai terminal

Terminal thứ nhất chạy `python scripts/run_watcher.py --config configs/youtube_sources.yaml`. Mở terminal thứ hai, vào thư mục project, kích hoạt `.venv` trong terminal đó nếu cần, rồi chạy `python scripts/download_video.py --file videos.txt --channel 0511_vietnam`. Job thủ công được giữ riêng; catalog ngăn cùng một video được tải/ghi trùng.

### F. Sao lưu video để dùng trên máy khác

`.gitignore` bỏ qua `data/raw/youtube/` và `state/catalog.db`; Git chỉ chứa code. Chờ các lệnh tải và watcher kết thúc, rồi tạo bản sao vào **một thư mục mới** trên ổ ngoài hoặc thư mục đồng bộ đám mây:

```bash
python scripts/transfer_data.py backup '/Volumes/MyDrive/data_acquisition_backup'
```

Lệnh dùng SQLite backup để sao chép catalog và chỉ lấy `source.mp4` cùng `metadata.json` của các video có trạng thái `COMPLETED`. File `staging/` và `partial/` không được sao lưu. Video được kiểm tra dung lượng và SHA-256 trong khi sao chép. Nếu có job `RUNNING`, lệnh yêu cầu đợi đến khi job xong. Dùng đường dẫn thư mục backup mới cho mỗi lần sao lưu; lệnh không ghi đè bản cũ.

Trên máy mới, clone code, cài dependencies, sửa `configs/youtube_sources.yaml` nếu cần, rồi khôi phục **trước khi chạy `init_catalog.py` hoặc downloader**:

```bash
python scripts/transfer_data.py restore '/Volumes/MyDrive/data_acquisition_backup'
python scripts/inspect_catalog.py --status COMPLETED
```

Lệnh restore chép video về `output.root_dir` của project mới và cập nhật `local_path` trong catalog. Nó yêu cầu catalog chưa tồn tại và thư mục output còn trống để tránh ghi đè dữ liệu hiện có. Nếu dùng dịch vụ đám mây, hãy đợi thư mục backup đồng bộ xong rồi mới restore. Các item chưa hoàn tất vẫn có trong catalog, nhưng media dở dang không được chuyển. Giữ bản backup riêng; lệnh restore không xóa nó.

### G. Chuyển toàn bộ repo và dữ liệu sang máy khác

Cách này chuyển cả source code, Git history, raw video, clip đã cắt, manifest, annotation, ROI, validation và SQLite catalog. Máy mới có thể tiếp tục acquisition hoặc trim từ checkpoint hiện tại mà không cần crawl và cắt lại dữ liệu đã hoàn thành.

Trước khi copy, dừng `trim_candidates.py`, downloader và watcher bằng `Ctrl+C`. Chờ các tiến trình thoát hoàn toàn để `clips_manifest.csv` và `state/catalog.db` ở trạng thái nhất quán. Từ máy hiện tại, chạy:

```bash
rsync -a --partial --info=progress2 \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='.DS_Store' \
  /PATH/TO/data_acquisition/ \
  USER@MAY_MOI:/DUONG_DAN/data_acquisition/
```

Thay `USER@MAY_MOI` bằng tài khoản và hostname/IP của máy mới; thay `/DUONG_DAN/data_acquisition/` bằng thư mục đích. Dấu `/` cuối đường dẫn nguồn có nghĩa là copy nội dung repo vào đúng thư mục đích. Có thể chạy lại cùng lệnh nếu kết nối bị ngắt; `rsync` tiếp tục truyền các file còn thiếu. Không thêm `--delete` nếu chưa chủ động muốn xóa file chỉ có trên máy đích.

Lệnh copy toàn bộ repo, trong đó có:

```text
.git/
configs/
scripts/
src/
tests/
state/catalog.db
data/raw/
data/inventory/
data/review/
data/clips/
data/annotations/
data/zones/
data/validation/
data/evaluation/
```

`.venv` không được copy vì có thể phụ thuộc hệ điều hành, kiến trúc CPU và đường dẫn tuyệt đối của máy cũ. Trên máy mới, tạo lại môi trường và cài dependency:

```bash
cd /DUONG_DAN/data_acquisition
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Cài `ffmpeg` và `ffprobe` trên máy mới nếu hai lệnh này chưa có, rồi kiểm tra dữ liệu clip đã chuyển:

```bash
python scripts/evaluation/trim_candidates.py \
  --config configs/evaluation/evaluation_dataset.yaml \
  --reconcile-only
```

Nếu còn clip chưa hoàn thành, tiếp tục trim mà không encode lại clip đã hợp lệ:

```bash
caffeinate -i python scripts/evaluation/trim_candidates.py \
  --config configs/evaluation/evaluation_dataset.yaml \
  2>&1 | tee data/clips/trim.log
```

`caffeinate` dùng trên macOS để giữ máy thức. Trên hệ điều hành khác, bỏ phần `caffeinate -i`. Các manifest sử dụng đường dẫn tương đối với project root nên repo trên máy mới không cần nằm tại cùng đường dẫn tuyệt đối như máy cũ.

### H. MP4 báo không tương thích với QuickTime

Đuôi `.mp4` là container; codec hình bên trong có thể là AV1. QuickTime trên một số máy Mac không phát được AV1. Lượt tải mới ưu tiên H.264/AAC nếu YouTube có định dạng này; nếu không có, downloader vẫn dùng định dạng khác để giữ video. File đã tải trước khi đổi code không tự thay đổi.

Để tạo bản xem bằng QuickTime cho video đã tải, chạy lệnh sau từ thư mục project (đổi video ID nếu cần):

```bash
ffmpeg -i data/raw/youtube/0511_vietnam/YppQ6BEHAKY/source.mp4 \
  -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart \
  data/raw/youtube/0511_vietnam/YppQ6BEHAKY/source_quicktime.mp4
```

Quá trình chuyển mã có thể mất khá lâu và cần thêm dung lượng đĩa. File `source.mp4` gốc được giữ nguyên; bản QuickTime nằm cùng thư mục với tên `source_quicktime.mp4`. Bản sao lưu ở mục F chỉ lấy `source.mp4` gốc.

### I. Ghép các track `.mp4.part` của livestream bị gián đoạn

Khi watcher bị dừng trước khi livestream kết thúc, `yt-dlp` có thể để lại các track đã tải trong `staging/` hoặc `partial/`. Ví dụ thường gặp là `source.f137.mp4.part` chứa hình H.264 và `source.f140.mp4.part` chứa tiếng AAC. Số format có thể khác giữa các livestream, vì vậy có thể dùng `ffprobe` để xác định track trước khi ghép:

```bash
ffprobe -v error \
  -show_entries stream=index,codec_type,codec_name,width,height \
  -of compact=p=0 \
  data/raw/youtube/0511_vietnam/VIDEO_ID/staging/source.f137.mp4.part
```

Trước tiên dừng watcher bằng `Ctrl+C` và chờ tiến trình thoát hoàn toàn để các file không còn bị ghi. Sau đó thay `VIDEO_ID` bằng ID thật và ghép track hình với track tiếng mà không chuyển mã:

```bash
LIVE_VIDEO_ID=NUGc3nLuGsI
mkdir -p "data/raw/youtube/0511_vietnam/$LIVE_VIDEO_ID/recovered"

ffmpeg \
  -i "data/raw/youtube/0511_vietnam/$LIVE_VIDEO_ID/staging/source.f137.mp4.part" \
  -i "data/raw/youtube/0511_vietnam/$LIVE_VIDEO_ID/staging/source.f140.mp4.part" \
  -map 0:v:0 -map 1:a:0 \
  -c copy -shortest -movflags +faststart \
  "data/raw/youtube/0511_vietnam/$LIVE_VIDEO_ID/recovered/recovered.mp4"
```

`-shortest` kết thúc output theo track ngắn hơn khi phần hình và tiếng có độ dài khác nhau. `-c copy` chỉ mux hai track nên nhanh và không làm giảm chất lượng. Nếu file nằm trong `partial/`, đổi `staging/` thành `partial/` và giữ đúng cặp có cùng prefix, ví dụ `from-start-source.f137.mp4.part` đi với `from-start-source.f140.mp4.part`; không ghép track `from-start-*` với `interrupted-*`.

Không đưa `*.ytdl` hoặc `*.part-Frag*.part` vào lệnh. Đây là metadata tải và fragment đang tải dở, có thể chưa hoàn chỉnh. Nếu chỉ còn một track video và không có track audio tương ứng, có thể đóng gói video không tiếng:

```bash
ffmpeg -i source.f614.mp4.part -map 0:v:0 -c copy recovered_video_only.mp4
```

Kiểm tra file phục hồi trước khi sử dụng:

```bash
ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=codec_type,codec_name,width,height \
  -of default=noprint_wrappers=1 \
  "data/raw/youtube/0511_vietnam/$LIVE_VIDEO_ID/recovered/recovered.mp4"
```

File tạo thủ công trong `recovered/` chỉ là bản cứu dữ liệu và không tự cập nhật catalog hay `metadata.json`. Nếu replay trên YouTube vẫn truy cập được, nên để watcher thực hiện replay recovery để tạo `source.mp4` hoàn chỉnh và cập nhật catalog đúng quy trình.

Kiểm tra kết quả bằng `python scripts/inspect_catalog.py`, hoặc lọc `--status RECORDING` và `--live-status is_live`. File hoàn tất nằm ở `data/raw/youtube/<channel_id>/<video_id>/source.mp4` cùng `metadata.json`. PowerShell dùng cùng lệnh `python ...` sau khi kích hoạt `.venv` bằng lệnh PowerShell ở phần cài đặt.

Thêm channel bằng một mục trong `configs/youtube_sources.yaml`:

```yaml
channels:
  - id: another_channel
    url: https://www.youtube.com/@AnotherChannel
    enabled: true
    video_ids: []
    title_keywords: []
    live_keywords: []
    vod_keywords: []
```

`id` là tên thư mục an toàn, duy nhất. `enabled: false` tạm ngưng scan channel đó. Config và output path là tương đối với project root; không có channel/path cố định trong source code. `poll_interval_sec`, giới hạn độ phân giải, recovery và retry có thể đổi trong cùng file.

## Output và state

```text
data/raw/youtube/<channel_id>/<video_id>/
  source.mp4
  metadata.json
  staging/       # file tạm của lần chạy hiện tại
  partial/       # bản cũ/chưa hoàn chỉnh, nếu có
```

`metadata.json` chứa video ID, tên channel, tiêu đề, URL canonical `https://www.youtube.com/watch?v=<id>`, trạng thái live, nguồn capture, timestamp UTC, duration, SHA-256 và `capture_complete`. Không dùng URL media tạm của YouTube làm canonical URL. `state/catalog.db` là source of truth; bảng `youtube_items` giữ metadata và state, bảng `jobs` giữ lịch sử job, retry và thời gian có thể chạy. Video, DB, log không được đưa vào Git.

Trạng thái VOD: `DISCOVERED → QUEUED → DOWNLOADING → VALIDATING → COMPLETED`. Live sắp diễn ra: `DISCOVERED → WAITING → QUEUED → RECORDING → VALIDATING → COMPLETED`. Lỗi đi qua `RETRY` hoặc `FAILED`; chỉ sau kiểm tra file tồn tại, size dương, `ffprobe` đọc được, có video stream, duration hợp lệ (nếu có), và SHA-256 mới thành `COMPLETED`.

Khi `is_live`, worker ưu tiên `--live-from-start`. Nếu tùy chọn này thất bại, worker thử ghi từ thời điểm hiện tại. Nếu recorder bắt đầu sau `actual_start` hoặc không biết thời điểm bắt đầu, `capture_complete=false` theo cách bảo thủ. Khi YouTube báo `was_live` và replay truy cập được, scheduler tạo `REPLAY_RECOVERY`; replay được tải vào staging và validate trước khi thay `source.mp4`. Bản record cũ nằm trong `partial/` cho đến khi thay thành công. Sau recovery thành công, `capture_source=replay_recovery`, `capture_complete=true`, rồi xóa partial cũ để tránh giữ hai bản trùng.

## Kiểm thử

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

Unit tests không tải video thật.

## Giới hạn

- Chỉ xử lý nội dung public mà `yt-dlp` truy cập thông thường; không vượt qua auth, private video, age restriction, DRM hoặc access control.
- Chế độ lọc từ khóa và `all` xem 50 mục mới nhất trên mỗi tab mỗi vòng poll; các mục cũ hơn cần được chọn bằng URL/ID. ID chọn trực tiếp không bị giới hạn bởi 50 mục này.
- Một bản ghi live bắt đầu muộn chỉ được xem là đầy đủ khi replay recovery thành công. Replay có thể chưa được YouTube tạo ngay sau khi live kết thúc; watcher sẽ thử ở các vòng poll tiếp theo, tối đa số retry cấu hình cho một đợt recovery.
- `yt-dlp --live-from-start` là tính năng thử nghiệm và khả năng lấy từ đầu tùy stream. Các fragment bị mất do mạng hoặc replay bị thiếu không thể được pipeline tự chứng minh là hoàn toàn đầy đủ.
- Phần acquisition không thực hiện segmenting, dashboard, inference, ROI/event annotation, video trimming, RTSP, Kaggle hay tìm kiếm toàn YouTube; các bước chuẩn bị dữ liệu đánh giá được mô tả ở mục tiếp theo.

## Chuẩn bị dữ liệu đánh giá: VN-SIDEWALK-EVAL-v1

Repo có hai phần xử lý dữ liệu. Phần acquisition thu thập video YouTube vào `data/raw/youtube`, kèm catalog SQLite và các file `metadata.json`. Module evaluation quét dữ liệu này cùng các thư mục raw Kaggle/local tùy chọn để chuẩn bị bộ dữ liệu đánh giá toàn pipeline. Module tái sử dụng metadata acquisition hiện có mà không thay đổi CLI hay output của acquisition.

Quy trình: video raw → lập inventory → kiểm tra video raw → lọc theo metadata → tự tạo khoảng clip ứng viên → review từng khoảng → cắt clip → QC kỹ thuật → metadata canonical → shortlist → review thủ công → gán nhãn scenario → đăng ký camera/view → vẽ ROI → chú thích event → chia dev/test theo nhóm → kiểm tra chéo → đóng băng phiên bản.

Roboflow Vietnam Vehicle Detection dùng cho huấn luyện và đánh giá detector. VN-SIDEWALK-EVAL-v1 dùng để đánh giá toàn pipeline phát hiện hành vi chiếm dụng khu vực. Không dùng tập test đã đóng băng để huấn luyện, fine-tune hoặc điều chỉnh ngưỡng. Các lớp có thể tạo event vi phạm là `motorcycle`, `car`, `bus` và `truck`; `person` chỉ cung cấp ngữ cảnh. Clip âm tính vẫn có scenario âm tính đã duyệt và ROI, nhưng không có event.

Cài thư viện Python bằng `pip install -r requirements.txt`; cài `ffmpeg` và `ffprobe` trên máy để đọc thông tin và cắt video. Các cửa sổ OpenCV cần môi trường desktop có giao diện đồ họa. Video raw được giữ nguyên. Các file trung gian trong `data/` không được đưa vào Git.

Bắt đầu với dữ liệu raw đã có:

```bash
python scripts/evaluation/inventory_videos.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/validate_raw_videos.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/filter_review_candidates.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/review_videos.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/review_clip_candidates.py --config configs/evaluation/evaluation_dataset.yaml
```

Khi cùng một `source_video_id` có nhiều file (ví dụ `source.mp4` và bản chuyển mã `source_quicktime.mp4`), bước lọc đánh dấu các bản còn lại là `duplicate_source_video_id` và ưu tiên bản `source.mp4` hợp lệ. `review_videos.py` làm mới cờ lọc metadata rồi tự tạo các khoảng thời gian ứng viên trong `data/review/clip_candidates.csv` từ video đã qua kiểm tra và lọc metadata; lệnh này chưa tạo file clip và không mở giao diện review. Mặc định, clip dài tối đa 180 giây, mục tiêu 120 giây và chồng lấn 10 giây. Đoạn cuối ngắn hơn 30 giây được gộp vào clip trước nếu tổng độ dài vẫn không quá 180 giây. Video ngắn hơn 30 giây không sinh clip. Các giá trị nằm trong mục `clip` của config. Chạy lại lệnh giữ nguyên quyết định `keep`/`reject` của những khoảng không đổi.

`review_clip_candidates.py` dùng ffmpeg để giải mã từng khoảng ứng viên và OpenCV để hiển thị, nên vẫn xem được các video mà `cv2.VideoCapture` không mở được. Lệnh này cần cả ffmpeg và môi trường desktop có giao diện đồ họa. Phím trong công cụ review: Space phát/tạm dừng; Left/Right tua 5 giây; K giữ clip ứng viên; R loại clip ứng viên; N/P chuyển sang clip kế tiếp/trước đó; Q thoát. Quyết định `keep`/`reject` được lưu ngay sau mỗi thao tác; khoảng mới có trạng thái `pending`. Chỉ các khoảng `keep` mới được cắt. Có thể sửa cột `status` trong `data/review/clip_candidates.csv` nếu không dùng giao diện OpenCV. Khi review, cần tránh giữ các clip chồng lấn không cần thiết; nếu cùng một event xuất hiện ở hai clip được giữ, phải chú thích event tương ứng trên từng clip. Sau đó chạy:

```bash
python scripts/evaluation/trim_candidates.py --config configs/evaluation/evaluation_dataset.yaml
# Chỉ kiểm kê lại tiến độ sau khi một lần cắt bị ngắt:
python scripts/evaluation/trim_candidates.py --config configs/evaluation/evaluation_dataset.yaml --reconcile-only
# P1: QC kỹ thuật và inventory cho toàn bộ clip đã cắt:
python scripts/qc_trimmed_clips.py --config configs/evaluation/evaluation_dataset.yaml
# Hoàn tất metadata canonical sau B1-02:
python scripts/build_clip_metadata.py --config configs/evaluation/evaluation_dataset.yaml
# B1-03: chỉ phân tích cache miss rồi merge và rerank toàn bộ candidate:
python scripts/analyze_evaluation_candidates.py --config configs/evaluation/evaluation_dataset.yaml
# B1-04: chọn review pool và tạo keyframe contact sheet:
python scripts/select_evaluation_shortlist.py --config configs/evaluation/evaluation_dataset.yaml
# Review keyframe trong candidate_review_pool.csv, sau đó chốt 75-90 clip:
python scripts/finalize_evaluation_shortlist.py --config configs/evaluation/evaluation_dataset.yaml
# Gán nhãn/ROI/event cho selected_shortlist.csv đã chốt.
python scripts/evaluation/register_camera.py --clip-id CLIP_ID --camera-id CAMERA_ID --view-id VIEW_ID --session-id SESSION_ID
python scripts/evaluation/label_scenario.py --clip-id CLIP_ID --scenario pass_through --reviewer NAME
python scripts/evaluation/define_roi.py --clip-id CLIP_ID
python scripts/evaluation/annotate_events.py --clip-id CLIP_ID
python scripts/evaluation/split_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/validate_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/freeze_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/inspect_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
```

`trim_candidates.py` ghi `clips_manifest.csv` sau mỗi clip hoàn chỉnh. Khi chạy lại sau khi bị ngắt, lệnh kiểm tra checksum và thời lượng của file đã hoàn thành rồi tiếp tục từ clip chưa xong; file dở dang chỉ bị thay sau khi bản cắt mới vượt qua kiểm tra. Tùy chọn `--reconcile-only` chỉ phục hồi manifest và báo file dở dang, không chạy ffmpeg để cắt thêm.

### Khi bổ sung clip mới vào dataset

Trạng thái hoàn thành của các bước chuẩn bị dữ liệu chỉ áp dụng cho snapshot clip tại thời điểm nghiệm thu. Khi có clip mới, không trim lại hoặc sửa các clip cũ đã khóa. Chạy lại `trim_candidates.py` là an toàn: lệnh xác minh rồi reuse clip đã hoàn thành, và chỉ trim các candidate `keep` mới hoặc file chưa hoàn chỉnh.

Nếu clip mới đến từ video raw mới, trước hết cập nhật inventory, validation và danh sách candidate, rồi review các khoảng mới:

```bash
python scripts/evaluation/inventory_videos.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/validate_raw_videos.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/filter_review_candidates.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/review_videos.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/review_clip_candidates.py --config configs/evaluation/evaluation_dataset.yaml
```

Sau khi các khoảng mới đã có `status=keep`, chạy chuỗi cập nhật sau:

```bash
# B1-01: reuse clip cũ và chỉ trim candidate mới/chưa hoàn chỉnh.
python scripts/evaluation/trim_candidates.py \
  --config configs/evaluation/evaluation_dataset.yaml

# B1-02/P1: tạo lại QC và inventory trên toàn bộ tập clip hiện tại.
python scripts/qc_trimmed_clips.py \
  --config configs/evaluation/evaluation_dataset.yaml

# Hoàn tất metadata canonical từ manifest và inventory mới.
python scripts/build_clip_metadata.py \
  --config configs/evaluation/evaluation_dataset.yaml

# B1-03: extract feature cho cache miss, merge và rerank toàn bộ candidate.
python scripts/analyze_evaluation_candidates.py \
  --config configs/evaluation/evaluation_dataset.yaml

# B1-04: chọn review pool và tạo 3-5 keyframe/clip.
python scripts/select_evaluation_shortlist.py \
  --config configs/evaluation/evaluation_dataset.yaml

# Sau khi review toàn bộ pool và gán KEEP/REJECT, chốt 75-90 clip.
python scripts/finalize_evaluation_shortlist.py \
  --config configs/evaluation/evaluation_dataset.yaml
```

QC phải chạy lại trên toàn bộ clip vì duplicate, overlap và phân bố duration/FPS/resolution có thể thay đổi khi thêm dữ liệu. Metadata canonical cũng phải được tạo lại để chứa clip mới. B1-03 chỉ decode clip mới, clip có checksum thay đổi hoặc clip bị invalid cache; feature hợp lệ của clip cũ được reuse. Sau khi merge, diversity, score và ranking luôn được tính lại trên toàn bộ candidate vì đây là các giá trị phụ thuộc toàn tập. B1-04 tính lại candidate pool theo quota và giữ các trường review thủ công của clip vẫn còn trong pool. Prediction chỉ dùng để chọn candidate, không phải ground truth.

Trước B1-03, cập nhật `selection.expected_candidates` trong `configs/evaluation/evaluation_dataset.yaml` thành tổng số candidate thực tế. Nếu bổ sung source mới, cập nhật cả `selection.expected_sources` và kiểm tra lại `source_minimum`, `source_maximum_fraction` cùng `candidate_pool_size`. Nếu dataset đã được freeze, tăng `dataset.version` và tạo phiên bản mới; không sửa snapshot đã đóng băng.

Không chép trực tiếp một file `.mp4` vào `data/clips` rồi bỏ qua B1-01: clip phải có bản ghi nhất quán trong `clips_manifest.csv`, checksum và khoảng thời gian nguồn. Cách an toàn là đăng ký khoảng trong `clip_candidates.csv`, review thành `keep`, rồi để `trim_candidates.py` tạo hoặc reconcile manifest. Nếu file hay boundary của clip cũ thay đổi, dừng pipeline và điều tra checksum thay vì âm thầm chạy lại các bước sau.

### Upload clip `.mp4` lên Hugging Face

Dùng một Hugging Face **dataset repository** để lưu clip. Dataset repo hiện tại là [nhthau/CCTV-giao-thong-da-nang](https://huggingface.co/datasets/nhthau/CCTV-giao-thong-da-nang) và được đặt ở chế độ private. Máy upload phải đăng nhập bằng access token có quyền `Write`, hoặc fine-grained token có quyền ghi vào đúng repo. Tạo token tại [Hugging Face Access Tokens](https://huggingface.co/settings/tokens), sau đó chạy từ thư mục gốc của project:

- Repo ID: `nhthau/CCTV-giao-thong-da-nang`
- Repo type: `dataset`
- Visibility: `private`
- Local upload root: `data/`
- Dataset card: `data/README.md`, upload thành `README.md` ở root của Hugging Face repo

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
hf auth login
hf auth whoami
```

Nếu vừa cài package mà shell chưa nhận lệnh `hf`, chạy `hash -r`; cũng có thể gọi trực tiếp `./.venv/bin/hf`. Không ghi token vào script hoặc commit token lên Git. Đăng nhập chỉ cần thực hiện một lần trên máy; mỗi terminal mới vẫn cần `source .venv/bin/activate` để dùng CLI trong `.venv`.

Khai báo repo đích:

```bash
export HF_REPO_ID="nhthau/CCTV-giao-thong-da-nang"
```

Nếu dataset repo chưa tồn tại, tạo repo private bằng:

```bash
hf repo create "$HF_REPO_ID" --repo-type dataset --private
```

Các lệnh bên dưới chạy với local root là `data`, vì vậy file trên Hub giữ đường dẫn `clips/<clip_id>.mp4`. Pattern `clips/*_C[0-9][0-9][0-9].mp4` chỉ chọn clip cuối cùng theo quy ước tên của pipeline; nó không upload `clips_manifest.csv`, `trim.log` hay file tạm `.tmp.mp4`.

#### Trường hợp 1: vừa trimming vừa upload

Chạy trimming ở terminal thứ nhất:

```bash
cd /PATH/TO/data_acquisition
source .venv/bin/activate
caffeinate -i python scripts/evaluation/trim_candidates.py \
  --config configs/evaluation/evaluation_dataset.yaml \
  2>&1 | tee data/clips/trim.log
```

`caffeinate -i` chỉ dùng trên macOS; trên hệ điều hành khác, bỏ phần này. Trimmer ghi vào `.tmp.mp4`, kiểm tra file, rồi mới atomically đổi sang tên clip cuối cùng. Vì vậy uploader không đọc file đang được encode.

Chạy uploader ở terminal thứ hai. `--every 5` giữ tiến trình hoạt động, quét thay đổi và tạo commit mới mỗi 5 phút; chu kỳ này gom nhiều clip vào một lượt thay vì tạo quá nhiều commit nhỏ:

```bash
cd /PATH/TO/data_acquisition
source .venv/bin/activate

export HF_REPO_ID="nhthau/CCTV-giao-thong-da-nang"

hf upload "$HF_REPO_ID" data \
  --repo-type dataset \
  --include "clips/*_C[0-9][0-9][0-9].mp4" \
  --every 5
```

Có thể dừng uploader bằng `Ctrl+C` sau khi trimming hoàn tất, rồi chạy lệnh một lần không có `--every` để bảo đảm đồng bộ clip cuối cùng. Chạy lại lệnh an toàn vì Hugging Face so sánh nội dung local với repository và chỉ commit thay đổi cần thiết.

#### Trường hợp 2: chỉ upload sau khi trimming đã xong

Sau khi xác nhận trimming hoàn tất, chạy một lần:

```bash
cd /PATH/TO/data_acquisition
source .venv/bin/activate

export HF_REPO_ID="nhthau/CCTV-giao-thong-da-nang"

hf upload "$HF_REPO_ID" data \
  --repo-type dataset \
  --include "clips/*_C[0-9][0-9][0-9].mp4"
```

Nếu mạng bị ngắt, chạy lại đúng lệnh để đồng bộ phần còn thiếu. Có thể kiểm tra số file local trước hoặc sau khi upload bằng:

```bash
find data/clips -maxdepth 1 -type f \
  -name '*_C[0-9][0-9][0-9].mp4' | wc -l
```

### P1: QC kỹ thuật và inventory clip đã cắt

P1 chỉ đọc file; không sửa, cắt lại, đổi tên hay xóa clip, và không chạy detector/tracker. Mặc định input là `data/clips` và output là `data/processed/qc`, theo mục `qc` trong config. Có thể ghi đè bằng CLI:

```bash
python scripts/qc_trimmed_clips.py \
  --config configs/evaluation/evaluation_dataset.yaml \
  --input data/clips \
  --output-dir data/processed/qc
```

Lần chạy nhanh dùng ffprobe cho metadata và ffmpeg giải mã ba mẫu ở đầu, giữa và cuối clip. `--full-decode` giữ cách kiểm tra này và giải mã tuần tự toàn bộ clip, không nạp toàn bộ video vào RAM, nên chậm hơn nhiều. Nếu không tìm thấy ffprobe/ffmpeg, P1 thử fallback OpenCV và ghi cảnh báo rõ trong report; nên cài media tools và chạy lại để có kết quả chính xác.

P1 sinh hai file:

- `data/processed/qc/clips_inventory.csv`: một dòng mỗi clip, gồm ID clip/source/session; path, tên, dung lượng và SHA-256; timestamp nguồn; duration/FPS/frame count/resolution/codec; kết quả decode; nhóm duplicate/overlap; `qc_status` và `qc_notes`.
- `data/processed/qc/trimming_qc_report.csv`: một dòng mỗi vấn đề, với issue ID ổn định, clip/source, loại, mức độ, chi tiết, clip liên quan và hành động thủ công đề xuất.

Các cột inventory:

- `clip_id`, `source_id`, `session_id`: ID ổn định và quan hệ nguồn/session từ manifest P0; file ngoài manifest có ID ổn định từ tên theo quy ước hoặc relative path.
- `file_path`, `file_name`, `file_size_mb`, `sha256`: vị trí portable, tên, dung lượng và content hash hiện tại. Hash lệch với P0 manifest là `FAIL`.
- `source_start_sec`, `source_end_sec`: khoảng thời gian P0 trong video nguồn; để trống nếu không có ground truth đáng tin cậy.
- `duration_sec`, `fps`, `frame_count`, `width`, `height`, `codec`, `metadata_source`: metadata video và tool đã cung cấp metadata.
- `decode_ok`, `decode_error_count`, `first_frame_ok`, `middle_frame_ok`, `last_frame_ok`, `full_decode_ok`: kết quả decode; `full_decode_ok` để trống trong chế độ nhanh.
- `duplicate_group`: nhóm file có SHA-256 giống hệt; để trống khi file là duy nhất.
- `overlap_group`, `overlap_status`, `overlap_max_sec`, `overlap_max_ratio`: thành phần overlap, trạng thái `CHECKED_NO_OVERLAP`/`CHECKED_OVERLAP`/`NOT_CHECKED`, và overlap lớn nhất của clip.
- `qc_status`, `qc_notes`: kết luận `PASS`/`WARNING`/`FAIL` và tóm tắt các vấn đề cần review.

Các cột report là `issue_id`, `clip_id`, `source_id`, `issue_type`, `severity`, `details`, `related_clip_id` và `action`. `related_clip_id` nối cặp duplicate/overlap; `details` lưu `overlap_sec` và `overlap_ratio` cho từng cặp có overlap. `action` chỉ là hướng dẫn review; P1 không tự sửa dữ liệu.

`PASS` nghĩa là không có lỗi/cảnh báo; `WARNING` dùng cho duration bất thường, FPS/resolution lệch so với mode của cùng source, frame count không khớp, file trùng hệt, fallback media tool, hoặc overlap vượt quá mức dự kiến; `FAIL` dùng cho metadata/checksum không hợp lệ, file không đọc được hoặc decode thất bại. Khoảng overlap đúng `clip.overlap_sec` là overlap có chủ đích: vẫn được gán `overlap_group`, lưu `overlap_max_sec`/`overlap_max_ratio` và có dòng `INFO` trong report, nhưng không tự biến thành warning. Tiếp xúc đúng ranh giới không phải overlap. Clip thiếu timestamp P0 có `overlap_status=NOT_CHECKED`; P1 không suy diễn timestamp từ hình ảnh.

Chỉ xem P1 hoàn tất sau khi lệnh đã chạy trên toàn bộ dataset thật và cả hai CSV đã được review thủ công.

### Metadata canonical sau B1-02

Bước metadata chỉ đọc các input đã có và tạo artifact mới; không sửa manifest P0, không chọn subset, không annotate và không chạy model. Source of truth cho định danh clip, source alias, timestamp và checksum là `data/clips/clips_manifest.csv`. File này do `trim_candidates.py` tạo từ các khoảng `keep` trong `data/review/clip_candidates.csv`: `clip_id` có dạng `<source_video_id>_Cnnn`, còn `source_start_sec`/`source_end_sec` lấy từ `start_sec`/`end_sec` của candidate. `data/processed/qc/clips_inventory.csv` là bằng chứng kỹ thuật B1-02 được đối chiếu với P0, không thay thế P0.

Chạy lại idempotent bằng:

```bash
python scripts/build_clip_metadata.py \
  --config configs/evaluation/evaluation_dataset.yaml
```

CLI tính lại SHA-256 trực tiếp từ filesystem và trả exit code khác 0 nếu có metadata `FAIL`. Tùy chọn `--skip-file-checksums` chỉ dành cho chẩn đoán nhanh trong lúc phát triển, không đủ để nghiệm thu metadata. Input/output và tolerance nằm trong mục `metadata` của config.

Các output trong `data/processed/metadata/`:

- `clips_metadata.csv`: bảng canonical một dòng mỗi clip cho selection, ROI/camera assignment, annotation, split và freeze về sau.
- `clip_selection_candidates.csv`: bảng candidate đầu vào cho B1-03, gồm identity/metadata/QC cùng schema scene-diversity, hard-case, event-likelihood, score và ranking. Trước B1-03, các tín hiệu/score/recommendation để trống với `signal_status=NOT_COMPUTED`; `prediction_status` nhắc rõ prediction không phải ground truth.
- `source_registry.csv`: một dòng mỗi `source_id`, gồm video/path nguồn, số clip, timeline, tổng duration và trạng thái camera.
- `camera_resolution_report.csv`: danh sách source chưa có camera mapping được xác minh cùng bằng chứng/policy.
- `metadata_validation_report.csv`: các vấn đề `INFO`/`WARNING`/`FAIL` theo clip/source/field và issue code.
- `metadata_summary.json`: tổng số clip/source/camera, phân bố clip và duration theo source, status, checksum mismatch và orphan.

`source_id` giữ alias trong P0 để audit ổn định. `source_video_id` có thể khác alias khi có bằng chứng trực tiếp từ cấu trúc acquisition path; ví dụ một recovered recording có alias theo filename nhưng video ID gốc nằm trong thư mục nguồn. Không suy nguồn từ thứ tự file. Camera chỉ được coi là resolved khi `camera_id` đã có trong source catalog hoặc `metadata.source_camera_mapping` chứa mapping đã xác minh. Title, source ID và thứ tự file không tự động trở thành camera ID; thiếu bằng chứng thì `camera_id=UNRESOLVED`, tạo `WARNING` nhưng không tạo `FAIL`.

Với config hiện tại, metadata chỉ đạt acceptance khi có đủ số clip trong snapshot, `clip_id` và path duy nhất, source mapping đúng, checksum khớp P0, không có orphan hay `FAIL`, và mọi warning được giải thích. Overlap đúng `clip.overlap_sec=10` giây là chủ đích và không tạo warning. Camera `UNRESOLVED` hiện là warning đã giải thích; phải được xử lý bằng bằng chứng camera thật trước khi dùng camera để group/split hoặc gán ROI.

### B1-03: automatic candidate analysis — incremental

B1-03 extract feature/signal cho từng clip, reuse feature hợp lệ từ cache, merge với candidate mới rồi tính lại diversity, candidate score, source rank và global rank trên toàn bộ bảng. Chạy bằng:

```bash
python scripts/analyze_evaluation_candidates.py \
  --config configs/evaluation/evaluation_dataset.yaml
```

Cache mặc định nằm tại `data/processed/metadata/clip_feature_cache.csv`. Một dòng cache chỉ được reuse khi `clip_id`, SHA-256 nội dung và `analysis_fingerprint` đều khớp. Fingerprint bao gồm phiên bản thuật toán, số frame lấy mẫu, kích thước analysis, rule chọn keyframe, threshold và weight; thay đổi một trong các giá trị này sẽ chủ động invalidate feature liên quan. `candidate_analysis_summary.json` ghi số clip được reuse, số cache miss, số clip decode trong lượt hiện tại, lỗi và số candidate được rerank.

Lần chạy đầu tiên sau khi nâng cấp phải phân tích toàn bộ để tạo cache có provenance đầy đủ. Từ lượt sau, clip cũ hợp lệ không bị decode lại. Dù không có cache miss, B1-03 vẫn merge và rerank toàn bộ candidate vì diversity/ranking phụ thuộc phân bố hiện tại. `--skip-file-checksums` chỉ dành cho phát triển; lượt nghiệm thu phải xác minh checksum.

Mỗi clip mới được lấy tối đa 12 frame đại diện tại tâm các khoảng thời gian đều nhau. Từ các frame này, B1-03 lưu 3–5 timestamp theo vai trò `OVERVIEW`, `EVENT_LIKELIHOOD`, `HARD_CASE` và `SCENE_DIVERSITY`. Repo không đóng gói object detector: các signal dùng thống kê OpenCV về motion/foreground region, brightness, blur, boundary activity và visual variation. `vehicle_density_score` và `object_count_estimate` chỉ là moving-region proxy; `observed_classes=NOT_INFERRED_NO_DETECTOR` làm giới hạn này rõ ràng. Tất cả prediction chỉ dùng để chọn candidate, không phải ground truth.

### B1-04: keyframe review và shortlist

Chạy B1-04 sau khi B1-03 đã hoàn tất:

```bash
python scripts/select_evaluation_shortlist.py \
  --config configs/evaluation/evaluation_dataset.yaml
```

`--skip-file-checksums` chỉ phù hợp cho chẩn đoán nhanh khi phát triển; không dùng khi nghiệm thu. `--no-previews` bỏ qua bước tạo keyframe contact sheet và để trống `preview_path` trong review index.

B1-04 không tính lại feature toàn bộ. Lệnh đọc candidate table đã merge/rerank, chọn pool 100–120 clip theo quota source, LOW/MEDIUM/HIGH event likelihood, hard/normal case, feature novelty và perceptual similarity. Với config mặc định, pool có 110 clip. Sau đó lệnh chỉ decode các frame đại diện của clip trong pool và tạo contact sheet 3–5 keyframe; không yêu cầu xem full video ở vòng shortlist.

`event_likelihood_score`, hard-case heuristic và mọi ranking chỉ dùng để chọn candidate. Chúng không phải `ground_truth`, không xác nhận clip có vi phạm và không được dùng làm label. Weight, threshold, số frame, số worker, source quota và ngưỡng redundancy nằm trong mục `selection` của config.

Không có nhãn scenario thật trước review, nên quota tự động chỉ dùng source, event-likelihood và hard-case proxy; không suy diễn scenario ground truth từ signal. Reviewer dùng keyframe và metadata để đánh dấu `KEEP`/`REJECT`, chốt 75–90 clip. Nếu keyframe không đủ kết luận, có thể xem full video như một ngoại lệ và ghi lý do trong `manual_review_notes`. Hai clip kề nhau không bị loại chỉ vì overlap; redundancy cần thêm visual similarity. Kích thước pool, khoảng shortlist cuối, quota và số keyframe đều nằm trong mục `selection` của config.

Output:

- `data/processed/metadata/clip_selection_candidates.csv`: candidate canonical được merge signal, rerank và bổ sung trạng thái selection.
- `data/processed/metadata/clip_feature_cache.csv`: feature cache theo clip/checksum/fingerprint cho incremental analysis.
- `data/processed/metadata/candidate_analysis_summary.json`: thống kê cache hit/miss, decode và rerank của B1-03.
- `data/processed/selection/candidate_review_pool.csv`: pool 110 clip chờ review; lần chạy lại giữ review cũ cho clip vẫn còn trong pool.
- `data/processed/selection/keyframe_contact_sheets/<clip_id>.jpg`: contact sheet 3–5 keyframe có timestamp và selection role.
- `data/processed/selection/shortlist_review_index.csv`: hàng đợi review kèm preview và selection reason.
- `data/processed/selection/selection_summary.json`: summary machine-readable, distribution, failure và redundancy exclusion.
- `data/processed/selection/selected_shortlist.csv`: chỉ gồm 75–90 clip `KEEP` sau khi finalize.
- `data/processed/selection/final_shortlist_summary.json`: số clip giữ/loại và kiểm tra khoảng kích thước cuối.

Để review, mở từng `preview_path` trong `shortlist_review_index.csv`, rồi chỉ điền ba cột `manual_review_status`, `manual_review_label`, `manual_review_notes` trong `candidate_review_pool.csv`. Đặt `manual_review_status=REVIEWED`; dùng `manual_review_label=KEEP` cho 75–90 clip cuối và `REJECT` cho phần còn lại. Chạy `finalize_evaluation_shortlist.py` để validate mọi dòng và tạo `selected_shortlist.csv`. `candidate_review_pool.csv` là nguồn mà lần chạy B1-04 tiếp theo dùng để giữ quyết định cũ; chỉ sửa `shortlist_review_index.csv` thì nội dung có thể bị ghi đè. Không đổi các signal thành ground truth; nhãn event thật chỉ được tạo ở bước annotation sau.

Đăng ký mỗi góc nhìn camera cố định theo độ phân giải; tạo `view_id` mới nếu góc quay hoặc độ phân giải thay đổi. Phím vẽ ROI: 1–4 chọn loại vùng, nhấp chuột để đặt đỉnh, U hoàn tác, R xóa các điểm đang vẽ, S lưu đa giác, Left/Right tua video, Q thoát. Phím chú thích event: S đánh dấu lúc episode bắt đầu, V đánh dấu lúc đủ điều kiện vi phạm, E đánh dấu lúc episode kết thúc, A thêm event, Left/Right tua video, Q thoát. Không đánh dấu E nếu episode vẫn đang diễn ra khi clip kết thúc. Với clip âm tính, để `data/annotations/events.csv` trống hoặc không tạo file. Nếu cần `bbox_keyframes`, sửa trường tương ứng trong CSV thành mảng JSON gồm các đối tượng `{time_sec, bbox}`. Các dòng scenario và event được thêm bằng CLI có trạng thái `approved`; quy trình review độc lập vẫn cần do nhóm dự án thực hiện.

Các file trung gian chính: `data/inventory/video_catalog.csv` lưu định danh nguồn, metadata acquisition, thông tin video đọc bằng ffprobe và SHA-256; `data/review/review_candidates.csv` lưu quyết định review; `data/review/clip_candidates.csv` lưu các khoảng ứng viên; `data/clips/clips_manifest.csv` ánh xạ clip tới video nguồn và camera/view; `data/annotations/scenarios.csv`, `events.csv` và `split_manifest.csv` lưu nhãn scenario, event GT và phân chia dữ liệu; `data/zones/camera_registry.csv` cùng một file JSON cho mỗi camera/view lưu ROI; `data/validation/evaluation_validation.json` và `.csv` lưu lỗi/cảnh báo theo mức độ. Thư mục cuối cùng gồm bản sao video, event dạng JSONL, scenario, manifest split và nguồn, zone, báo cáo kiểm tra, config, checksum và freeze manifest.

Lệnh freeze kiểm tra các nguồn được dùng, clip, nhãn đã duyệt, thời gian event, ROI, checksum và rò rỉ dữ liệu giữa các split. Lệnh sẽ thất bại nếu có bất kỳ lỗi `ERROR` nào hoặc phiên bản đích đã tồn tại. Để thay đổi bộ dữ liệu đã đóng băng, hãy đổi `dataset.version` trong config và tạo phiên bản mới. Mặc định, dữ liệu được chia theo nhóm `source_video_id`; đặt `split.grouping_key` thành `camera_id + session_id` khi hai trường này được gán đầy đủ và đáng tin cậy.
