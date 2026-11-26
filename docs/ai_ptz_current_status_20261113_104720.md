# AI PTZ — kiểm tra phiên 13/11/2026, 10:47:20

Thời gian trong báo cáo: **UTC+7**. Phiên mới nhất được kiểm tra: **10:47:20.572 → 10:48:45.512**, dài **84,940 giây**. Run ID: `daef19b24aaf401eab37ed6135e1dd7c`.

## 1. Kết luận chính

**HANET pan/tilt vẫn chưa phục hồi trong phiên này.** Có **6 STOP được gửi, 0 STOP_ACK, 0 START_SENT và 0 PAN_TILT_DISPATCH**. Fault từ phiên trước được nạp ngay lúc khởi tạo; zoom tiếp tục giữ quyền motor sau khi STOP không được xác nhận.

Bản sửa mới đã xuất hiện trong log: có `MOTOR_RECOVERY_STARTED/FAILED` và parser reason **`EMPTY_RESULT_UNVERIFIED`**. Cả 5 mẫu schema được lưu đều là STOP `kCmdZoomWide`, response ID khớp request, `result` là object rỗng `{}` và được phân loại `ACK_UNKNOWN`. Chưa có bằng chứng firmware xác nhận thực thi STOP thành công hay motor đã đứng yên.

**Ứng dụng đóng trong 316 ms**, controls và stream hoàn tất; trạng thái motor vật lý vẫn `null` (chưa xác nhận). Tracking tiếp tục hoạt động nhưng hiệu năng quan sát được thấp hơn phiên 10:20; không thể quy nguyên nhân cho bản sửa HANET chỉ từ hai phiên khác cảnh.

## 2. HANET, recovery và ownership

| Thời điểm | Bằng chứng | Diễn giải |
|---|---|---|
| 10:47:23.874 | `MOTOR_FAULT_LATCHED`, `UNCONFIRMED_PREVIOUS_RUN` | Nạp fault tồn tại trước phiên này |
| 10:47:39.263 | `MOTOR_RECOVERY_STARTED`, attempt 0 | Bắt đầu thử STOP để phục hồi |
| 10:47:39.277 | STOP generation 1 | `kCmdZoomWide` |
| 10:47:39.285 | Schema `{}`, `ACK_UNKNOWN / EMPTY_RESULT_UNVERIFIED` | Không đủ điều kiện xác nhận STOP |
| 10:47:39.809 | STOP generation 2 | `HanetACKUnknown`; không có schema riêng |
| 10:47:40.840 | STOP generation 3 | Schema `{}`, ACK_UNKNOWN |
| 10:47:42.876 | STOP generation 4 | Schema `{}`, ACK_UNKNOWN |
| 10:47:46.936–46.939 | STOP generation 5 rồi fault latch attempt 5 | Hết retry tự động |
| 10:48:45.196 | GUI yêu cầu shutdown | Bắt đầu đóng |
| 10:48:45.229–45.238 | STOP generation 6 rồi recovery failed | Schema `{}`, ACK_UNKNOWN |
| 10:48:45.241 | Recovery attempt cuối tại camera close | Không có STOP mới được gửi |

Mẫu đầu có request/response ID cùng `de995253-18d1-4d14-a88c-3141cdf3ca69`; response keys là `id`, `jsonrpc`, `result`. Result shape có `fields={}`, `omitted_keys=0`, `truncated=false`: đây là object rỗng trong diagnostics, không phải các trường bị che hết. Có mẫu schema tại generation **1, 3, 4, 5, 6**. Generation 2 nằm trong cửa sổ giới hạn một giây của diagnostics; không suy đoán nội dung response không được lưu.

Tổng từ **2.327 camera events**:

- **0 START_SENT / START_ACK; 6 STOP_SENT; 0 STOP_ACK; 0 PAN_TILT_DISPATCH; 0 MOTOR_FAULT_RECOVERED**.
- 7 recovery started và 7 recovery failed là số lần xử lý ở controller, không phải 7 STOP trên transport.
- 13 STOP_UNCONFIRMED gồm 6 bản ghi RPC và 7 bản ghi controller, không phải 13 lệnh gửi.
- 5 reconnect attempt, 5 reconnect success, 5 reconnect failed: transport mở lại được, nhưng bước recovery không xác nhận được STOP. Không diễn giải thành 5 lần không kết nối được mạng.
- 5 STOP tự động và 1 STOP lúc đóng; khoảng cách từ STOP tự động cuối đến STOP shutdown là **58,293 giây**. Không thấy vòng gửi STOP vô hạn.
- 7 `CAMERA_TRANSACTION_ERROR` đều thuộc `read_status`; đây là nhóm lỗi riêng, không phải 7 ACK lỗi bổ sung.

Trong 152 bản ghi PAN_TILT_DECISION, **112 MOTOR_BUSY với owner `zoom`**, 19 TARGET_UNCERTAIN với owner `zoom`, 21 TARGET_UNCERTAIN khi owner chưa có. Bộ đếm cuối phiên ghi **2.424 MOTOR_BUSY / 141 TARGET_UNCERTAIN**; vòng worker kiểm tra lặp nên đây không phải số frame độc lập. Bản ghi cuối lúc 10:48:44.796 vẫn `auto_pan_enabled=true`, hướng dự kiến `tilt_up`, nhưng bị chặn bởi MOTOR_BUSY.

Một `ZOOM_STARTED` là sự kiện vòng đời tác vụ; không chứng minh đã gửi START cho camera. `motor_owner=null` ở context RPC/controller cũng không chứng minh ownership được giải phóng: event controls lúc đóng vẫn ghi **owner `zoom`**.

## 3. Checkpoint và phạm vi bản sửa mới đã được kiểm chứng

Hash SHA-256 của cả **6 file** trong manifest bản sửa gần nhất vẫn khớp tại thời điểm audit. Các marker mới trong runtime log phù hợp với bản sửa đó; kiểm tra hash hiện tại không thay thế bản chụp mã nguồn tại thời điểm process khởi chạy.

Checkpoint hiện tại `logs/camera/motor_state/c761c95e9887b30d9adb.json` vẫn chứa:

```json
{"version": 1, "active_codes": ["kCmdZoomWide"]}
```

Không có file guard cùng basename `.pending` tại thời điểm đọc. Không có event `MOTOR_CHECKPOINT_COMMIT_FAILED` hoặc `MOTOR_CHECKPOINT_CLEANUP_PENDING` trong camera log.

**Chưa thể kết luận nhánh commit/clear checkpoint mới đã được kiểm chứng với thiết bị:** phiên này không có STOP_ACK, nên chưa đi tới nhánh ghi guard rồi clear checkpoint sau ACK. Reason chung `STOP_OR_CHECKPOINT_UNCONFIRMED` không tự chứng minh lỗi ghi đĩa; bằng chứng cụ thể của phiên là `HanetACKUnknown`. Các kết quả kiểm thử mô phỏng của bản sửa nằm trong [báo cáo triển khai trước](hanet_pan_tilt_restoration_20261113.md).

## 4. Shutdown

| Mốc | Thời điểm | Tính từ yêu cầu GUI |
|---|---|---:|
| GUI SHUTDOWN_REQUESTED | 10:48:45.196 | 0 ms |
| RPC close, motor unconfirmed | 10:48:45.241 | 45 ms |
| Controls hoàn tất, cleanup_failures rỗng | 10:48:45.242 | 46 ms |
| GUI: controls_finished=true, stream_finished=true | 10:48:45.259 | 63 ms |
| Application stopped | 10:48:45.512 | **316 ms** |

Ba event `SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR` thuộc RPC/controls/GUI của cùng một lần đóng. Không có `SHUTDOWN_STOP_TIMEOUT` trong camera log. Log ứng dụng ghi rõ STOP chưa xác nhận. Hoàn tất đóng phần mềm không chứng minh motor vật lý đã dừng; cũng không đủ bằng chứng để kết luận motor còn quay.

## 5. Tracking, nhận diện và hiệu năng

Dwell được tính từ dòng ứng dụng đầu đến yêu cầu shutdown (**84,624 giây**, gồm khởi tạo); trước transition đầu tiên được quy vào SEARCHING. Tỷ lệ có target là thời gian TARGET_LOCKED + PERSON_ZOOM, không phải độ chính xác bám đúng người theo ground truth.

| Chỉ số | Phiên 10:20 | Phiên mới 10:47 |
|---|---:|---:|
| SEARCHING | 21,853 s | 22,988 s |
| TARGET_LOCKED + PERSON_ZOOM | 61,721 s — 68,88% | **55,464 s — 65,54%** |
| OCCLUDED_HOLD | 6,027 s | 6,172 s |
| Số lần HOLD / mất khóa hoàn toàn | 2 / 1 | **4 / 1** |
| Recovery sau mất khóa, telemetry | 431,88 ms | **2.088,28 ms** |
| Kết quả nhận diện | 26 UNKNOWN, 0 MATCH | **11 UNKNOWN, 0 CANDIDATE, 0 MATCH** |
| Frame nhận / detection / GUI unique | 2.571 / 973 / 1.103 | 2.422 / 589 / 778 |
| Camera FPS | 29,75 | 29,76 |
| Detection FPS | 11,26 | **7,24** |
| GUI unique FPS | 12,76 | **9,56** |
| YOLO total mean / p95 | 31,02 / 49,95 ms | **48,62 / 90,09 ms** |
| ReID total mean / p95 | 116,04 / 270,75 ms | **200,25 / 477,00 ms** |
| ReID selection mean / p95 | 94,53 / 134,12 ms | 134,44 / 196,18 ms |
| Receive → GUI submit mean / p95 | 56,93 / 73,10 ms | **81,62 / 113,18 ms** |
| Shutdown đến application stopped | 296 ms | 316 ms |

Khóa đầu lúc **10:47:41.470**; mất khóa hoàn toàn lúc **10:48:00.616**, khóa lại **10:48:02.706**. Chênh lệch timestamp ứng dụng là 2,090 s; telemetry đo 2.088,28 ms bằng đồng hồ monotonic tại các điểm đo khác. Ba lần HOLD sau hồi về TARGET_LOCKED, phiên kết thúc ở PERSON_ZOOM.

Có **148 recognition rejection**: 147 FACE_ASSOCIATION_AMBIGUOUS và 1 SESSION_MISMATCH. `recognition_accepted_count=11` là chấp nhận xử lý kết quả, không phải 11 MATCH. Cả 11 kết quả ứng dụng đều UNKNOWN; similarity cao nhất **0,447**, crop của mẫu đó **17×21 px**. Chưa có bằng chứng nhận diện thành công trong phiên.

Camera nhận frame gần 30 FPS nhưng detection và GUI chậm hơn phiên trước. Bộ đếm ghi 1.622 dropped_frames, 168 superseded_detection_frames và 27 dropped_face_jobs; không cộng các counter này thành tỷ lệ mất hình vì thuộc các bước pipeline khác nhau. Hai phiên khác thời lượng, cảnh và workload nên **chưa đủ cơ sở kết luận bản sửa HANET làm giảm FPS hoặc thay đổi chất lượng tracking**.

## 6. Việc còn cần xử lý

1. Nút chặn P0 vẫn là **xác minh semantics phản hồi `{}` của firmware cho STOP**, cùng xác nhận motor thực sự dừng. Chưa có căn cứ mở rộng success rule chỉ từ schema; xem [quy trình xác minh thiết bị](hanet_ack_hardware_validation_20261113.md).
2. Khi có STOP được xác nhận hợp lệ, cần kiểm chứng chuỗi clear checkpoint → MOTOR_FAULT_RECOVERED → release ownership → pan/tilt dispatch. Chuỗi này chưa xuất hiện trong phiên mới.
3. Chưa có mẫu START/pan/tilt thực tế mới; không suy rộng kết quả STOP zoom sang các hướng khác.
4. Nếu tiếp tục điều tra hiệu năng, cần cùng video/kịch bản và cấu hình để so sánh YOLO, ReID và GUI; số liệu hiện tại chỉ mô tả hai phiên.

Lượt kiểm tra này chỉ đọc log/source và tạo báo cáo, metrics; không sửa runtime, không thay checkpoint, không khởi chạy ứng dụng hay gửi lệnh camera.

## 7. Nguồn và giới hạn

- [Application log](../logs/ptz_20261113_104720.log).
- [Camera log](../logs/camera/camera_control_20261113_104723_870476.log).
- [First failures](../logs/camera/first_failures_20261113_104723_870476.json).
- [Latency summary CSV](../logs/latency/latency_summary_20261113_104723_870476.csv).
- [Metrics, timeline và SHA-256 nguồn](ai_ptz_current_status_20261113_104720_metrics.json).
- [Báo cáo phiên trước](ai_ptz_current_status_20261113_102026.md).

Đã đọc cả latency JSONL hiện tại và 3 file rotation: **48.661 dòng**, từ **10:47:23.874 → 10:48:45.262186**, bao phủ từ khởi tạo profiler đến flush cuối. Thống kê lấy LATENCY_REPORT cuối: aggregate từ đầu profiler, percentile theo cửa sổ giới hạn; các stage được dẫn có count bằng percentile_window_count. Không cộng lại camera events xuất hiện đồng thời trong latency trace. Báo cáo dựa trên telemetry phần mềm, không có quan sát trực tiếp chuyển động camera hoặc ground truth tracking.
