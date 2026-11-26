# AI PTZ — Báo cáo phiên mới nhất 13/11/2026, 09:36:27

Ngày phân tích: **13/11/2026**. Tất cả thời điểm dưới đây dùng **UTC+7**.

## 1. Kết luận

**Hotfix đã giới hạn retry và giúp ứng dụng đóng nhanh, nhưng điều khiển HANET chưa hoạt động thành công trong phiên này.** START zoom-out và cả 5 STOP được gửi đều không qua bước xác nhận ACK. Vì vậy chưa thể nghiệm thu START/STOP hoặc khả năng dừng motor thực tế.

- Lỗi đầu tiên lúc **09:36:49.625**: `RuntimeError: HANET response has no verified success result`. Đây là lỗi kiểm tra nội dung phản hồi trong parser; log không cho thấy timeout hay lỗi mất kết nối là nguyên nhân đầu tiên.
- Có **1 START và 5 STOP thực sự được ghi nhận gửi**, **0 START_ACK / STOP_ACK**. START không bị phát lại. Reconnect mở được kết nối mới nhưng STOP tiếp tục thất bại ở kiểm tra ACK.
- Retry tự động dừng sau khi hết ngân sách, không tiếp tục vô hạn. Chỉ đến thao tác đóng ứng dụng mới có thêm một STOP được gửi.
- Shutdown từ yêu cầu đóng đến dòng ứng dụng kết thúc mất khoảng **0,304 giây**; log đánh dấu rõ **motor chưa được xác nhận đã dừng**. Đây là thành công về vòng đời ứng dụng, chưa phải thành công về dừng motor.
- Tracking có target được chọn **120,508 / 180,065 giây = 66,92%**, có **3 lần HOLD, 1 lần mất khóa hoàn toàn**, và một lần phần mềm xác nhận khuôn mặt. Những kết quả này không chứng minh auto-pan/zoom đang hoạt động: motor bị giữ bởi zoom sau lỗi và không có pan dispatch.

**Ưu tiên tiếp theo:** xác minh schema phản hồi HANET thực tế và sửa điều kiện ACK theo bằng chứng đó. Chưa nên đánh giá chất lượng bám bằng chuyển động camera khi START/STOP vẫn chưa xác nhận được.

## 2. Phạm vi và nguồn bằng chứng

| Mục | Giá trị |
|---|---|
| Phiên ứng dụng | 09:36:27.145 → 09:39:27.514, tổng 180,369 giây |
| Cửa sổ hoạt động dùng tính dwell | 09:36:27.145 → yêu cầu shutdown 09:39:27.210, 180,065 giây |
| Run ID | `6848dde5ba9741249e1b4f3abb59a38d` |
| Camera log | 5.492 sự kiện, 09:36:30.911 → 09:39:27.265 |
| Latency JSONL còn giữ sau rotation | 50.060 dòng, chỉ từ 09:38:12.998 → 09:39:27.270618 |
| Nguồn thống kê toàn phiên | CSV latency và `LATENCY_REPORT` cuối phiên |

Nguồn chính:

- [Application log](../logs/ptz_20261113_093627.log), [application JSONL](../logs/ptz_20261113_093627.jsonl).
- [Camera control log](../logs/camera/camera_control_20261113_093630_907965.log), [first failures](../logs/camera/first_failures_20261113_093630_907965.json).
- [Latency summary](../logs/latency/latency_summary_20261113_093630_907965.csv), latency JSONL cùng stamp và các bản `.1`, `.2`, `.3`.
- [Zoom summary](../logs/zoom/zoom_summary_20261113_093630_907601.csv).
- [Số liệu trích xuất và SHA-256 nguồn](ai_ptz_current_status_20261113_093627_metrics.json).
- [Báo cáo phiên 12/11 12:55](ai_ptz_current_status_20261112_125526.md), [báo cáo hotfix](hanet_transport_fault_fix_20261113.md).

Đã đối chiếu SHA-256 các file trong manifest hotfix với source hiện tại: **không có khác biệt**. Điều này xác nhận source trên đĩa tương ứng artifact hotfix; không thay thế thông tin build được nhúng vào tiến trình.

Không dùng số sự kiện trong phần latency còn giữ để suy ra tổng toàn phiên. Lỗi đầu phiên đã nằm ngoài cửa sổ rotation của latency nhưng vẫn còn trong camera log đầy đủ và `first_failures`. Các nguồn có sự kiện trùng nhau nên không cộng chồng.

## 3. HANET: lỗi tại kiểm tra ACK, chưa có bằng chứng STOP thành công

### Diễn biến

| Thời điểm | Bằng chứng | Ý nghĩa |
|---|---|---|
| 09:36:44.596 | Thay target bằng `target_13_11.jpg` | Đầu vào tracking khác phiên cũ |
| 09:36:45.722 | Bật auto-zoom | Bắt đầu thử recovery |
| 09:36:49.612 | Mở kết nối generation 1 | Kết nối ban đầu thiết lập được |
| 09:36:49.614 | START gửi `kCmdZoomWide` | Zoom-out recovery; không phải pan dù event mang tiền tố `PAN_TILT` |
| 09:36:49.625 | START_UNCONFIRMED, `transmission=sent` | Parser không xác nhận phản hồi là thành công |
| 09:36:49.626 | MOTOR_FAULT_LATCHED, STOP controller không gửi được ngay | Backoff đang chặn; chưa có STOP trên đường truyền tại lần này |
| 09:36:50.162 | STOP gửi qua generation 2 | ACK thất bại lúc 50.174 |
| 09:36:51.198 | STOP gửi qua generation 3 | ACK thất bại lúc 51.205 |
| 09:36:53.233 | STOP gửi qua generation 4 | ACK thất bại lúc 53.239 |
| 09:36:57.274 | STOP gửi qua generation 5 | ACK thất bại lúc 57.276; latch sau khi hết ngân sách retry |
| 09:39:27.235 | STOP khi đóng ứng dụng, generation 6 | ACK thất bại lúc 27.242 |

Request ID của START đầu tiên: `451bf4ea-78fd-4069-b80f-895910836df7`; command ID: `cmd_748ce5c8fe4f4d5686fed551358944ab`.

**Cả 6 thao tác đã gửi đều gặp cùng lỗi ACK.** Không thấy START phát lại sau reconnect. Khoảng chờ tự động tăng gần **0,5 → 1 → 2 → 4 giây**; sau STOP lúc 09:36:57.274 không có STOP gửi mới trong khoảng **150 giây**, đến khi đóng ứng dụng.

### Nguyên nhân xác định được và phần chưa biết

Trong [`HanetRPC._receive_response`](../camera/hanet_rpc.py), phản hồi được đọc thành JSON object, đối chiếu request ID, kiểm tra `error` và `result is False`, rồi chỉ chấp nhận **`result is True`**.

Với đúng thông báo lỗi hiện tại, có thể suy ra parser đã gặp phản hồi:

1. Là JSON object và có ID khớp request.
2. Không có giá trị `error` được đánh giá là true và không có `result` là boolean `False`.
3. Nhưng `result` cũng không phải boolean `True`.

**Log không lưu cấu trúc phản hồi đó.** Chưa biết `result` thiếu, null, object, chuỗi hay một dạng khác; cũng chưa biết camera thực thi lệnh hay từ chối theo schema riêng. Vì vậy không thể kết luận “camera trả lỗi”, “motor đã chạy”, hoặc “motor đã dừng”.

Điều kiện boolean `True` được siết trong hotfix đang là điểm chặn quan sát được. Đây là **nghi vấn tương thích ACK cần xử lý trước**, có khả năng là regression với firmware thực tế; chưa đủ bằng chứng để chọn schema thay thế. Không nên đổi ngược sang cách coi mọi phản hồi khác `False` là thành công.

### Cách đọc đúng các bộ đếm

| Sự kiện | Số lượng | Diễn giải |
|---|---:|---|
| START_SENT | 1 | `kCmdZoomWide` |
| STOP_SENT | 5 | 4 tự động và 1 khi shutdown |
| START_ACK / STOP_ACK | 0 / 0 | Không có xác nhận thành công |
| PAN_TILT_STOP_UNCONFIRMED | 12 | 5 lỗi ở tầng RPC + 7 lần ở tầng controller; **không phải 12 STOP gửi** |
| HANET_CONNECTION_OPEN / CLOSED | 6 / 6 | Các generation được đóng sau lỗi |
| RECONNECT_ATTEMPT / SUCCESS / FAILED | 5 / 5 / 5 | Kết nối lại thành công, nhưng STOP tiếp theo không qua ACK nên recovery thất bại |
| CAMERA_COMMAND_TRANSACTION_ERROR | 10 | Đều là `read_status`; tách khỏi 6 lỗi thao tác motor |

Hai lần controller STOP tại thời điểm backoff không tạo thêm STOP_SENT. `retry_attempt` tăng đến 7 gồm các lần thử khi đóng ứng dụng; không phải bằng chứng vòng lặp retry vô hạn. Các lỗi `read_status` cũng không đồng nghĩa có thêm START/STOP.

## 4. Ownership và shutdown

Sau lỗi zoom, các quyết định pan ghi **owner=`zoom`** và lý do `MOTOR_BUSY`. Trong 319 bản ghi `PAN_TILT_DECISION`, có 240 bản ghi MOTOR_BUSY; bộ đếm toàn phiên ghi 5.734 quyết định MOTOR_BUSY vì worker kiểm tra lặp lại. **5.734 không phải số frame riêng biệt.**

Không có `PAN_TILT_DISPATCH`. Auto-pan vẫn enabled trong thời gian hoạt động nhưng không lấy được quyền điều khiển; đến shutdown mới disabled. Đây là tình huống **zoom lỗi làm pan bị chặn**, khác phiên trước khi pan lỗi làm zoom chờ owner pan/tilt. Các trường owner null trong một số sự kiện tầng RPC không đủ chứng minh quyền motor đã được trả.

Checkpoint motor còn `active_codes=["kCmdZoomWide"]`. Đây là dấu vết một lệnh chưa được STOP xác nhận, không phải telemetry chứng minh motor còn chuyển động. Không được xóa dấu vết này chỉ vì mở được socket mới.

| Mốc shutdown | Thời điểm | Thời gian từ yêu cầu GUI |
|---|---|---:|
| SHUTDOWN_REQUESTED tại GUI | 09:39:27.210 | 0 ms |
| STOP cuối được gửi | 09:39:27.235 | 25 ms |
| Hoàn tất tầng RPC, motor unconfirmed | 09:39:27.244 | 34 ms |
| Hoàn tất controls, `cleanup_failures=[]` | 09:39:27.246 | 36 ms |
| GUI: `controls_finished=true`, `stream_finished=true` | 09:39:27.265 | 55 ms |
| `AI PTZ Camera stopped.` | 09:39:27.514 | **304 ms** |

Không thấy KeyboardInterrupt hay SHUTDOWN_STOP_TIMEOUT trong phiên mới. RTSP_DISCONNECTED xuất hiện trong đường đóng chủ động, không đủ cơ sở coi là sự cố RTSP độc lập. Log có ba `SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR` ở ba tầng, không phải ba lần shutdown.

So với phiên trước mất khoảng **379,7 giây** và kết thúc qua KeyboardInterrupt, vòng đời đóng ứng dụng đã cải thiện rõ. Tuy nhiên dòng `Camera stopped` cuối log chỉ xác nhận ứng dụng kết thúc; trạng thái motor vật lý vẫn **chưa biết**. Log cũng không có bảng kiểm đếm thread sau đóng, nên kết luận giới hạn ở các cờ hoàn tất và lỗi cleanup được ghi nhận.

## 5. Tracking và nhận diện

Thời gian được tính từ chuỗi `[STATE]` trong application log đến yêu cầu shutdown; đoạn trước transition đầu tiên được tính SEARCHING, bao gồm khởi tạo.

| Trạng thái | Dwell (giây) |
|---|---:|
| SEARCHING | 53,968 |
| TARGET_LOCKED | 0,863 |
| PERSON_ZOOM | 71,457 |
| VERIFIED | 48,188 |
| OCCLUDED_HOLD | 5,589 |
| Tổng | **180,065** |

“Có target được chọn” là tổng TARGET_LOCKED + PERSON_ZOOM + VERIFIED: **120,508 giây, 66,92%**. Đây là trạng thái phần mềm, không phải tỷ lệ bám đúng người theo ground truth.

- Khóa đầu tiên **09:37:18.882**, khoảng **34,286 giây sau thay target**. Đoạn từ lần khóa này đến HOLD đầu tiên kéo dài **89,875 giây**.
- HOLD tại **09:38:48.757**, **09:38:49.763**, **09:38:57.830**.
- Mất khóa hoàn toàn **09:39:01.068**, khóa lại **09:39:03.299**; telemetry recovery **2.230 ms**, phù hợp khoảng **2,231 giây** giữa các transition.
- Có 35 kết quả nhận diện: **31 UNKNOWN, 3 CANDIDATE, 1 MATCH**.
- MATCH lúc **09:37:37.825**, nhãn `HoangTuanNgoc`, similarity **0,459**, confirm **2/2**, crop **12×18 px**. Thời gian lock → identity confirmed khoảng **18,984 giây**.
- VERIFIED tồn tại **48,188 giây**, đến **09:38:26.063** rồi quay lại PERSON_ZOOM. Không phải nhận diện được duy trì đến cuối phiên.
- Có **275 recognition rejection**, trong đó **273 FACE_ASSOCIATION_AMBIGUOUS**, 1 stale queue và 1 session mismatch. Đây là các lần gate từ chối, không phải 275 lần ArcFace nhận sai.

Crop mặt rất nhỏ và không có ground truth/video đối chiếu trong phân tích này, nên MATCH chỉ được báo cáo là quyết định của hệ thống. Việc có MATCH không chứng minh auto-zoom thành công: lệnh zoom duy nhất xảy ra trước lần khóa đầu tiên và không có ACK; các session có khóa không gửi thêm zoom.

## 6. Hiệu năng và so sánh có giới hạn

Toàn phiên ghi **5.271 frame nhận**, **1.505 detection frame**, **1.899 GUI unique frame**, **1.242 SCRFD call**, **35 ArcFace call**. FPS cuối từ profiler: camera **29,89**, detection **8,53**, GUI **10,77**. Phiên này không có đuôi shutdown dài làm giảm FPS tổng như phiên trước.

| Stage | Số mẫu | Mean (ms) | p95 (ms) |
|---|---:|---:|---:|
| YOLO total | 1.507 | 39,77 | 71,51 |
| ReID total | 339 | 175,04 | 379,27 |
| ReID selection | 207 | 148,10 | 255,58 |
| ReID periodic verify | 115 | 213,31 | 476,66 |
| GUI render | 1.899 | 35,11 | 61,86 |
| Receive → GUI submit | 1.899 | 71,82 | 114,48 |
| Face recognition total | 35 | 63,53 | 66,45 |

YOLO total có max **1.563,34 ms**; không nên dùng một cực trị để mô tả độ trễ thường xuyên. Số mẫu stage và số detection frame là các bộ đếm khác nhau, không ép chúng bằng nhau.

Pipeline ghi **3.256 dropped frames**, **373 superseded detection frames**, **41 dropped face jobs**, **3 stale inference results**, **1 stale ReID result**. Các bộ đếm thuộc các tầng khác nhau và có thể liên quan cùng công việc; không cộng chúng thành tổng frame mất hoặc quy toàn bộ thành mất gói RTSP.

| Chỉ số | 12/11 12:55 | 13/11 09:36 |
|---|---:|---:|
| Target được chọn / cửa sổ hoạt động | 31,21% | **66,92%** |
| HOLD / mất khóa hoàn toàn | 16 / 6 | **3 / 1** |
| Face identity confirmed | 0 | **1** |
| Detection FPS khi hoạt động | ~9,14 | **8,53** |
| GUI FPS khi hoạt động | ~11,29 | **10,77** |
| ReID total mean / p95 (ms) | 139,67 / 313,89 | **175,04 / 379,27** |
| ReID selection mean (ms) | 81,33 | **148,10** |
| Shutdown | ~379,7 s, KeyboardInterrupt | **0,304 s, motor unconfirmed** |

Tracking tốt hơn theo trạng thái ghi nhận trong phiên này, nhưng ReID tốn thời gian hơn và detection FPS thấp hơn. **Không phải A/B cùng video**: target input, cảnh, thời lượng, số người/overlap và tình trạng điều khiển khác nhau. Không quy các chênh lệch tracking hoặc inference cho hotfix transport.

## 7. Việc cần làm tiếp theo

1. **P0 — Thu phản hồi ACK thực tế ở dạng đã lọc dữ liệu nhạy cảm.** Ghi request ID, code, operation, generation, các key/type của response và trường trạng thái cần thiết; giới hạn kích thước, loại bỏ token/password/session secret. Hiện log đã chỉ đúng nhánh parser nhưng thiếu bằng chứng để thiết kế bộ giải mã tương thích.
2. **P0 — Xác định điều kiện thành công theo schema đã kiểm chứng.** Tạo fixture từ phản hồi thật, phân biệt success/reject/unknown; kiểm tra cả START và STOP. Giữ nguyên nguyên tắc không coi phản hồi chưa rõ là STOP ACK.
3. **P0 — Kiểm chứng lại với camera.** Cần START ACK, STOP ACK và quan sát motor dừng; sau STOP ACK mới xác nhận recovery hoàn tất và quyền điều khiển được trả. Kiểm tra thêm reconnect, không replay START, và khởi động lại với checkpoint motor chưa xác nhận.
4. **P1 — Sau khi transport đạt, chạy cùng video/kịch bản để đánh giá tracking.** Theo dõi hold/lost/recovery, chất lượng crop mặt, association ambiguity và ReID periodic verification. Tránh chỉnh tracking để bù cho motor đang bị fault latch.

**Trạng thái nghiệm thu hiện tại:** retry có giới hạn và shutdown có kết thúc — có bằng chứng đạt trong phiên này; START/STOP ACK, phục hồi quyền điều khiển sau STOP, và dừng motor vật lý — **chưa đạt hoặc chưa xác minh**.

Phạm vi công việc của báo cáo: đọc log/source, trích xuất số liệu và viết tài liệu. Không thay đổi runtime code, không chạy lại ứng dụng và không gửi lệnh xuống camera.
