# AI PTZ — kiểm tra phiên 13/11/2026, 10:20:26

Thời gian trong báo cáo: **UTC+7**. Phiên mới nhất: **10:20:26.768 → 10:21:56.665**, dài 89,897 giây.

## 1. Kết luận chính

**Diagnostics mới đã thu được cấu trúc phản hồi STOP: `result` là object rỗng `{}`, không phải boolean `true`.** Cả 5 mẫu schema được ghi có ID khớp request và cùng cấu trúc này. Parser phân loại `ACK_UNKNOWN / UNVERIFIED_RESULT_SCHEMA`.

**Motor control vẫn chưa phục hồi:** không gửi START mới, 6 STOP được gửi nhưng không có STOP_ACK; zoom giữ ownership và pan không dispatch. Ứng dụng đã nạp fault từ phiên trước ngay khi khởi tạo, nên lần này không phải một START mới gây fault.

Shutdown vẫn hữu hạn: **0,296 giây** từ yêu cầu GUI đến dòng kết thúc ứng dụng. Trạng thái motor vật lý vẫn chưa được xác nhận.

## 2. Bằng chứng mới về response

Từ `HANET_RESPONSE_SCHEMA` đầu tiên lúc **10:20:44.084**:

| Trường | Giá trị |
|---|---|
| Method | `Control.DoPtz` |
| Operation / code | `STOP` / `kCmdZoomWide` |
| Generation | 1 |
| Request ID và response ID | Cùng `7b105f63-22f1-4311-bc62-a551d8bd3f43` |
| Response keys | `id`, `jsonrpc`, `result` |
| Result type | `dict` |
| Result fields / keys | `{}` / `[]` |
| Result omitted_keys | `0` |
| Result truncated | `false` |
| Classification / reason | `ACK_UNKNOWN` / `UNVERIFIED_RESULT_SCHEMA` |

Đối chiếu hàm `response_schema` đang chạy: `fields={}`, `omitted_keys=0`, `truncated=false` ở cấp result, với ngân sách chưa cạn, cho thấy đây là **object rỗng thực sự**, không phải object có field bị bộ lọc che hết.

Cấu trúc tương đương tái dựng từ diagnostics và validator — **không phải bản dump raw response**:

```json
{"id": "<request UUID khớp>", "jsonrpc": "2.0", "result": {}}
```

Có cùng schema tại generation **1, 3, 4, 5, 6**. Generation 2 cũng có `HanetACKUnknown`, nhưng không có schema riêng vì nằm trong cửa sổ rate limit một giây; không khẳng định nội dung của mẫu không được ghi.

First schema STOP đã được lưu trong `first_failures`, nên không mất dù latency trace đầu phiên đã bị rotation. Diagnostics bổ sung ở lượt trước hoạt động đúng mục đích.

**Đã biết cấu trúc, chưa biết semantics:** phản hồi `{}` có thể được firmware dùng để biểu thị kết quả một phương thức không trả dữ liệu, nhưng log này chưa chứng minh đó là ACK thực thi STOP thành công hay motor đã đứng yên. Chưa có START response của phiên này và chưa có quan sát vật lý trong dữ liệu được phân tích. Không thể tự đổi “empty object” thành success chỉ từ mẫu trên.

## 3. Timeline motor và ownership

| Thời điểm | Sự kiện | Ý nghĩa |
|---|---|---|
| 10:20:30.067 | MOTOR_FAULT_LATCHED, `UNCONFIRMED_PREVIOUS_RUN` | Nạp uncertainty từ checkpoint trước; không phải lỗi START mới |
| 10:20:44.076 | Zoom STOP_REQUEST, `START_FAILED` | Ý định zoom không vượt được backend fault guard |
| 10:20:44.082 | STOP_SENT generation 1 | STOP hướng còn ghi nhớ `kCmdZoomWide` |
| 10:20:44.084–44.085 | Schema empty result, ACK_UNKNOWN | Không có STOP_ACK |
| 10:20:44.605 | STOP generation 2 | ACK_UNKNOWN |
| 10:20:45.625 | STOP generation 3 | ACK_UNKNOWN, schema `{}` |
| 10:20:47.652 | STOP generation 4 | ACK_UNKNOWN, schema `{}` |
| 10:20:51.686–51.689 | STOP generation 5, fault latch tại attempt 5 | Hết retry tự động |
| 10:21:56.369 | GUI yêu cầu shutdown | Bắt đầu đóng |
| 10:21:56.395–56.401 | STOP generation 6 khi đóng | ACK_UNKNOWN, schema `{}` |

Tổng toàn camera log:

- **0 START_SENT, 0 START_ACK, 6 STOP_SENT, 0 STOP_ACK**.
- **0 PAN_TILT_DISPATCH, 0 MOTOR_FAULT_RECOVERED**.
- 5 reconnect attempt, 5 reconnect success và 5 reconnect failed: mở lại transport thành công, nhưng recovery không xác nhận được STOP.
- 5 STOP tự động, 1 STOP khi shutdown; khoảng 64,7 giây giữa STOP tự động cuối và STOP khi đóng. Không có retry vô hạn.
- 13 STOP_UNCONFIRMED gồm 6 bản ghi tầng RPC và 7 bản ghi tầng controller; không phải 13 STOP gửi.
- 7 CAMERA_TRANSACTION_ERROR đều là `read_status`, tách khỏi lỗi ACK của STOP.

Trong 163 PAN_TILT_DECISION, có **122 MOTOR_BUSY với owner zoom**, 20 TARGET_UNCERTAIN với owner zoom, 21 TARGET_UNCERTAIN khi chưa có owner. Bộ đếm toàn phiên ghi **3.164 MOTOR_BUSY** do vòng worker kiểm tra lặp, không phải 3.164 frame độc lập.

`ZOOM_STARTED` chỉ là event vòng đời tác vụ: phiên này có một event đó nhưng **không có START trên transport**. Không dùng nó để kết luận camera vừa zoom. Owner vẫn `zoom` tại shutdown là hệ quả giữ quyền khi STOP chưa xác nhận, không phải bằng chứng motor còn quay.

## 4. Shutdown

| Mốc | Thời điểm | Từ yêu cầu GUI |
|---|---|---:|
| SHUTDOWN_REQUESTED | 10:21:56.369 | 0 ms |
| RPC close, motor unconfirmed | 10:21:56.407 | 38 ms |
| Controls hoàn tất, cleanup_failures rỗng | 10:21:56.409 | 40 ms |
| GUI: controls_finished và stream_finished true | 10:21:56.475 | 106 ms |
| Application stopped | 10:21:56.665 | **296 ms** |

Ba event `SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR` thuộc RPC/controls/GUI, không phải ba lần đóng. Không có SHUTDOWN_STOP_TIMEOUT hoặc KeyboardInterrupt trong phiên này. Các cờ hoàn tất không thay thế phép quan sát motor vật lý; log đã cảnh báo STOP chưa được xác nhận.

## 5. Tracking và hiệu năng

Cửa sổ tính dwell: từ dòng log đầu đến yêu cầu shutdown, **89,601 giây**, bao gồm khởi tạo. Trước transition đầu tiên được tính SEARCHING.

| Chỉ số | Kết quả |
|---|---:|
| SEARCHING | 21,853 s |
| TARGET_LOCKED + PERSON_ZOOM | **61,721 s — 68,88%** |
| OCCLUDED_HOLD | 6,027 s |
| Số lần HOLD / mất khóa hoàn toàn | **2 / 1** |
| Recovery sau mất khóa | **431,88 ms** |
| Nhận diện khuôn mặt | **26 UNKNOWN, 0 CANDIDATE, 0 MATCH** |
| Face association rejection | 179, đều FACE_ASSOCIATION_AMBIGUOUS |
| Frame nhận / detection / GUI unique | 2.571 / 973 / 1.103 |
| Camera / detection / GUI FPS | **29,75 / 11,26 / 12,76** |
| YOLO total mean / p95 | 31,02 / 49,95 ms |
| ReID total mean / p95 | 116,04 / 270,75 ms |
| ReID selection mean / p95 | 94,53 / 134,12 ms |
| Receive → GUI submit mean / p95 | 56,93 / 73,10 ms |

Khóa đầu 10:20:48.189, HOLD 10:20:52.202, mất khóa hoàn toàn 10:20:55.318, khóa lại 10:20:55.750. HOLD lần hai bắt đầu 10:21:53.458 và phiên kết thúc khi vẫn HOLD; không tính đó thành lần mất khóa hoàn toàn thứ hai.

So với phiên 09:36: detection FPS 8,53 → 11,26; ReID mean 175,04 → 116,04 ms; tỷ lệ có target 66,92% → 68,88%. Nhưng phiên ngắn hơn, khác cảnh và workload, **không phải benchmark A/B hay bằng chứng bản sửa parser tăng FPS**. Phiên trước có một MATCH, phiên này không có MATCH. Tracking state cũng không chứng minh bám đúng người theo ground truth.

## 6. Kết luận hành động

1. Đã có mẫu cấu trúc thực tế cho **STOP `kCmdZoomWide`: empty result object**. Không còn cần đoán `result` là null, chuỗi hay object có field ẩn ở các mẫu đã lưu.
2. Bước còn thiếu là xác minh ý nghĩa response empty object của firmware, đặc biệt STOP trên session mới đối với hướng còn từ phiên trước, cùng quan sát motor đã dừng. Không coi cấu trúc response là bằng chứng đủ để clear fault.
3. Chưa có mẫu START/pan/tilt mới. Không suy rộng schema STOP zoom thành schema thành công cho mọi command.
4. Giữ checkpoint và không bypass fault để bật auto thử. Thực hiện theo [hướng dẫn xác minh có giám sát](hanet_ack_hardware_validation_20261113.md) trước khi thêm success rule.

Lượt này chỉ kiểm tra log/source và viết báo cáo; không sửa runtime code, không chạy ứng dụng hay gửi lệnh camera.

## 7. Nguồn và giới hạn

- [Application log](../logs/ptz_20261113_102026.log).
- [Camera log](../logs/camera/camera_control_20261113_102030_063664.log).
- [First failures, có schema STOP đầu](../logs/camera/first_failures_20261113_102030_063664.json).
- [Latency summary](../logs/latency/latency_summary_20261113_102030_063664.csv).
- [Metrics trích xuất và SHA-256 nguồn](ai_ptz_current_status_20261113_102026_metrics.json).
- [Báo cáo bản sửa ACK](hanet_ack_compatibility_fix_20261113.md), [phiên trước](ai_ptz_current_status_20261113_093627.md).

Latency JSONL còn 41.279 dòng từ **10:21:07.660 → 10:21:56.484054**, không bao phủ đầu phiên. Timeline motor lấy từ camera log đầy đủ; thống kê toàn phiên lấy LATENCY_REPORT cuối. Không cộng sự kiện trùng giữa các nguồn. Hash các file source trong manifest bản sửa ACK vẫn khớp tại thời điểm audit.
