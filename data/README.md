---
pretty_name: CCTV giao thông Đà Nẵng
language:
  - vi
license: other
tags:
  - video
  - cctv
  - traffic
  - vietnam
  - da-nang
  - area-violation-detection
---

# CCTV giao thông Đà Nẵng

Dataset video giao thông phục vụ nghiên cứu và xây dựng bộ đánh giá cho bài toán phát hiện phương tiện chiếm dụng khu vực, đặc biệt là vỉa hè và các vùng hạn chế. Repository hiện được đặt ở chế độ private và đang được cập nhật trong quá trình thu thập, trimming và kiểm tra chất lượng.

> **Trạng thái:** đang xây dựng. Số lượng file và cấu trúc metadata có thể tiếp tục thay đổi cho tới khi dataset được đóng băng thành một phiên bản chính thức.

## Nội dung dataset

Repository có thể chứa hai nhóm video:

```text
.
├── raw/
│   └── youtube/
│       └── <channel_id>/
│           └── <video_id>/
│               ├── source.mp4
│               ├── source_quicktime.mp4       # nếu có
│               └── recovered/...              # nếu có
└── clips/
    ├── <source_id>_C001.mp4
    ├── <source_id>_C002.mp4
    └── ...
```

- `raw/`: video nguồn hoặc video được phục hồi từ một lượt tải bị gián đoạn. Một source có thể có nhiều biến thể; không mặc định xem các biến thể là những quan sát độc lập.
- `clips/`: các đoạn video được trích từ raw video. Mục tiêu hiện tại là khoảng 960 clip hoàn chỉnh.

Chỉ các file `.mp4` hoàn chỉnh được upload. File tải dở, log, cache và file tạm của quá trình trimming không thuộc dataset.

## Quy trình tạo clip

Clip được tạo bằng FFmpeg theo cấu hình hiện tại:

- thời lượng tối thiểu: 30 giây;
- thời lượng mục tiêu: 120 giây;
- thời lượng tối đa: 180 giây;
- overlap dự kiến giữa các đoạn liền kề: 10 giây;
- video được encode H.264 và audio được encode AAC khi track audio tồn tại;
- file chỉ được công nhận hoàn chỉnh sau khi vượt qua kiểm tra thời lượng và khả năng đọc bằng media probe.

Tên clip có dạng:

```text
<source_id>_C<sequence>.mp4
```

Ví dụ:

```text
LF_ZmCAo38w_C001.mp4
```

Hai clip liền kề có thể chứa một phần nội dung trùng nhau theo overlap được cấu hình. Không giả định mỗi clip là một sự kiện độc lập.

## Mục đích sử dụng

Dataset hướng tới các tác vụ:

- đánh giá detector cho các lớp `motorcycle`, `car`, `bus` và `truck`;
- nghiên cứu phát hiện phương tiện đi vào hoặc lưu lại trong vùng quan tâm;
- đánh giá tracking và suy luận sự kiện theo thời gian;
- xây dựng tập kiểm thử cho pipeline Area Violation Detection;
- phân tích các trường hợp khó như che khuất, vật thể nhỏ, chuyển động nhanh, đông phương tiện và điều kiện ánh sáng kém.

Video trong `raw/` và `clips/` là dữ liệu ứng viên. Sự xuất hiện của một video trong repository **không chứng minh rằng video có hành vi vi phạm**, và không được dùng như ground truth nếu chưa qua quy trình review và annotation riêng.

## Truy cập dataset private

Tài khoản phải được cấp quyền truy cập repository. Đăng nhập bằng Hugging Face CLI:

```bash
hf auth login
hf auth whoami
```

Tải toàn bộ video clip:

```bash
hf download nhthau/CCTV-giao-thong-da-nang \
  --repo-type dataset \
  --include "clips/*.mp4" \
  --local-dir ./CCTV-giao-thong-da-nang
```

Tải raw video:

```bash
hf download nhthau/CCTV-giao-thong-da-nang \
  --repo-type dataset \
  --include "raw/**/*.mp4" \
  --local-dir ./CCTV-giao-thong-da-nang
```

Hoặc tải bằng Python:

```python
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="nhthau/CCTV-giao-thong-da-nang",
    repo_type="dataset",
    allow_patterns=["clips/*.mp4"],
    local_dir="./CCTV-giao-thong-da-nang",
)
```

Không đưa access token trực tiếp vào source code. Với server hoặc CI, cung cấp token bằng secret phù hợp của môi trường chạy.

## Giới hạn

- Dataset không phải mẫu đại diện đầy đủ cho mọi tuyến đường, thời điểm hoặc điều kiện giao thông tại Đà Nẵng.
- Dữ liệu có thể chịu selection bias từ vị trí camera, thời gian ghi, trạng thái livestream và quy trình chọn clip.
- Clip liền kề có thể overlap và không nên được chia ngẫu nhiên vào train/test nếu việc đó gây rò rỉ nội dung hoặc source.
- Chất lượng hình ảnh, FPS, độ phân giải, codec và audio có thể khác nhau giữa các source.
- Các tín hiệu lựa chọn tự động, nếu được bổ sung sau này, chỉ dùng để ưu tiên review; chúng không phải nhãn sự thật nền.
- Repository chỉ chứa video nếu uploader được cấu hình chỉ chọn `.mp4`; manifest, annotation và báo cáo QC không được ngầm coi là có mặt.

## Bản quyền, quyền riêng tư và sử dụng có trách nhiệm

`license: other` được sử dụng vì repository không cấp lại một giấy phép chung cho nội dung video nguồn. Quyền đối với video gốc vẫn thuộc về chủ sở hữu tương ứng. Quyền truy cập repository private không đồng nghĩa với quyền công bố, phân phối lại hoặc sử dụng thương mại.

Người sử dụng có trách nhiệm:

- kiểm tra quyền sử dụng và điều khoản của từng nguồn trước khi công bố hoặc chia sẻ dữ liệu;
- tuân thủ quy định về quyền riêng tư và bảo vệ dữ liệu áp dụng tại nơi sử dụng;
- không sử dụng dữ liệu để nhận dạng cá nhân, theo dõi cá nhân hoặc suy diễn thuộc tính nhạy cảm;
- hạn chế hiển thị khuôn mặt, biển số và các thông tin có thể nhận dạng trong báo cáo hoặc sản phẩm công khai;
- không diễn giải dự đoán của mô hình như kết luận pháp lý về một cá nhân hay phương tiện.

## Phiên bản

Dataset đang được cập nhật liên tục. Các thí nghiệm cần ghi lại ít nhất Hugging Face commit revision đã sử dụng để bảo đảm khả năng tái lập. Một phiên bản evaluation chính thức chỉ nên được công bố sau khi hoàn tất QC, metadata, annotation, split và validation.

