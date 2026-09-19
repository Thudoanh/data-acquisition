# YouTube data acquisition

Pipeline này theo dõi các YouTube channel public, thu thập video và livestream vào raw candidate pool cho **Area Violation Detection**. Nó chỉ thực hiện **Discover → Download → Record → Recover → Validate → Catalog**. Video trong pool chưa được xác nhận là dữ liệu vi phạm.

Luồng xử lý tiếp theo:

```text
YouTube raw data → acquisition → candidate pool → manual review → trim
→ ROI annotation → event GT annotation → VN-SIDEWALK-EVAL-v1
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

Máy macOS Intel không có Homebrew có thể lấy hai bản ZIP `ffmpeg` và `ffprobe` từ [trang static build được FFmpeg dẫn tới](https://evermeet.cx/ffmpeg/), rồi giải nén hai executable vào `.venv/bin/`. Môi trường ảo hiện tại của project đã có sẵn cả hai. Sau khi kích hoạt `.venv`, kiểm tra bằng `ffmpeg -version` và `ffprobe -version`. Nếu tạo lại `.venv`, cần cài lại hai binary này.

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

Trên máy macOS Intel hiện tại, các đường dẫn nên trỏ tới `data_acquisition/.venv/bin/`. `ffmpeg -version` và `ffprobe -version` xác nhận hai chương trình chạy được. Mỗi **terminal mới** cần kích hoạt `.venv` riêng bằng `source .venv/bin/activate`; việc kích hoạt ở terminal thứ nhất không tự áp dụng cho terminal thứ hai. Sau đó có thể để watcher chạy ở một terminal và chạy `download_video.py --file videos.txt --channel 0511_vietnam` ở terminal khác.

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

### G. MP4 báo không tương thích với QuickTime

Đuôi `.mp4` là container; codec hình bên trong có thể là AV1. QuickTime trên một số máy Mac không phát được AV1. Lượt tải mới ưu tiên H.264/AAC nếu YouTube có định dạng này; nếu không có, downloader vẫn dùng định dạng khác để giữ video. File đã tải trước khi đổi code không tự thay đổi.

Để tạo bản xem bằng QuickTime cho video đã tải, chạy lệnh sau từ thư mục project (đổi video ID nếu cần):

```bash
ffmpeg -i data/raw/youtube/0511_vietnam/YppQ6BEHAKY/source.mp4 \
  -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart \
  data/raw/youtube/0511_vietnam/YppQ6BEHAKY/source_quicktime.mp4
```

Quá trình chuyển mã có thể mất khá lâu và cần thêm dung lượng đĩa. File `source.mp4` gốc được giữ nguyên; bản QuickTime nằm cùng thư mục với tên `source_quicktime.mp4`. Bản sao lưu ở mục F chỉ lấy `source.mp4` gốc.

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

Quy trình: video raw → lập inventory → kiểm tra video raw → lọc theo metadata → tự tạo khoảng clip ứng viên → review từng khoảng → cắt clip → gán nhãn scenario → đăng ký camera/view → vẽ ROI thủ công → chú thích event → chia dev/test theo nhóm → kiểm tra chéo → đóng băng phiên bản.

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
python scripts/evaluation/register_camera.py --clip-id CLIP_ID --camera-id CAMERA_ID --view-id VIEW_ID --session-id SESSION_ID
python scripts/evaluation/label_scenario.py --clip-id CLIP_ID --scenario pass_through --reviewer NAME
python scripts/evaluation/define_roi.py --clip-id CLIP_ID
python scripts/evaluation/annotate_events.py --clip-id CLIP_ID
python scripts/evaluation/split_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/validate_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/freeze_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
python scripts/evaluation/inspect_evaluation_set.py --config configs/evaluation/evaluation_dataset.yaml
```

Đăng ký mỗi góc nhìn camera cố định theo độ phân giải; tạo `view_id` mới nếu góc quay hoặc độ phân giải thay đổi. Phím vẽ ROI: 1–4 chọn loại vùng, nhấp chuột để đặt đỉnh, U hoàn tác, R xóa các điểm đang vẽ, S lưu đa giác, Left/Right tua video, Q thoát. Phím chú thích event: S đánh dấu lúc episode bắt đầu, V đánh dấu lúc đủ điều kiện vi phạm, E đánh dấu lúc episode kết thúc, A thêm event, Left/Right tua video, Q thoát. Không đánh dấu E nếu episode vẫn đang diễn ra khi clip kết thúc. Với clip âm tính, để `data/annotations/events.csv` trống hoặc không tạo file. Nếu cần `bbox_keyframes`, sửa trường tương ứng trong CSV thành mảng JSON gồm các đối tượng `{time_sec, bbox}`. Các dòng scenario và event được thêm bằng CLI có trạng thái `approved`; quy trình review độc lập vẫn cần do nhóm dự án thực hiện.

Các file trung gian chính: `data/inventory/video_catalog.csv` lưu định danh nguồn, metadata acquisition, thông tin video đọc bằng ffprobe và SHA-256; `data/review/review_candidates.csv` lưu quyết định review; `data/review/clip_candidates.csv` lưu các khoảng ứng viên; `data/clips/clips_manifest.csv` ánh xạ clip tới video nguồn và camera/view; `data/annotations/scenarios.csv`, `events.csv` và `split_manifest.csv` lưu nhãn scenario, event GT và phân chia dữ liệu; `data/zones/camera_registry.csv` cùng một file JSON cho mỗi camera/view lưu ROI; `data/validation/evaluation_validation.json` và `.csv` lưu lỗi/cảnh báo theo mức độ. Thư mục cuối cùng gồm bản sao video, event dạng JSONL, scenario, manifest split và nguồn, zone, báo cáo kiểm tra, config, checksum và freeze manifest.

Lệnh freeze kiểm tra các nguồn được dùng, clip, nhãn đã duyệt, thời gian event, ROI, checksum và rò rỉ dữ liệu giữa các split. Lệnh sẽ thất bại nếu có bất kỳ lỗi `ERROR` nào hoặc phiên bản đích đã tồn tại. Để thay đổi bộ dữ liệu đã đóng băng, hãy đổi `dataset.version` trong config và tạo phiên bản mới. Mặc định, dữ liệu được chia theo nhóm `source_video_id`; đặt `split.grouping_key` thành `camera_id + session_id` khi hai trường này được gán đầy đủ và đáng tin cậy.
