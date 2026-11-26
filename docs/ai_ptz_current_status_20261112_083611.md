# Kiểm tra phiên AI PTZ mới nhất — 12/11/2026

> Cập nhật sau sửa code: xem [báo cáo phiên 09:41:47 và so sánh trước/sau](ai_ptz_current_status_20261112_094147.md). Nội dung bên dưới giữ nguyên làm mốc trước sửa.

Thời gian hiển thị: Asia/Ho_Chi_Minh, UTC+7. Chỉ đọc log/source; không chạy camera hoặc sửa code vận hành.

## 1. Kết luận

**Auto pan/tilt đã gửi lệnh và có ACK; vấn đề hiện tại không còn là hoàn toàn không tạo lệnh.** Tracking vẫn mất ổn định, hiệu năng inference thấp hơn hai phiên trước và recovery zoom bị chặn bởi điều kiện không phù hợp với backend không có lens telemetry.

Ba điểm cần ưu tiên:

1. **Recovery bị chặn 381 lần vì `RECOVERY_INVALID_ZOOM_ESTIMATE`.** Đối chiếu source xác định được đường truyền: HANET không có telemetry → `get_zoom_level()` trả NaN → `_recover_missing()` từ chối. Đây không phải lỗi timestamp của những lần này.
2. **Hai tracker ID có bbox gần như trùng nhau**, IoU tới 0,965 và overlap/min-area gần 1,0, gây hold. Có dấu hiệu duplicate detection/track, nhưng cần frame/video và detection trước tracker để phân biệt với hai người thực sự chồng lên nhau.
3. **Detection chỉ 6,28 FPS, ReID mean 191 ms/P95 436 ms, stale counter 408.** Camera nhận gần 30 FPS nên inference/selection là phần cần điều tra, không có bằng chứng camera command queue bị nghẽn dài.

## 2. Nguồn và độ đầy đủ

- [Log ứng dụng](../logs/ptz_20261112_083611.log), [JSONL ứng dụng](../logs/ptz_20261112_083611.jsonl).
- Phiên **08:36:11.105–08:42:00.737**, khoảng **5 phút 49,63 giây**.
- Run ID: `d7f4fce31f7e43709cdd9b97aafe83d9`.
- [Latency summary](../logs/latency/latency_summary_20261112_083616_809086.csv), [latency trace](../logs/latency/latency_trace_20261112_083616_809086.jsonl) và `.1/.2/.3`.
- [Zoom summary](../logs/zoom/zoom_summary_20261112_083616_808443.csv), zoom trace tương ứng và các file xoay.

Latency trace sau khi gộp chỉ còn **08:40:48.460–08:42:00.576**, khoảng **72,12 s cuối phiên**. Counter/aggregate lấy từ `LATENCY_REPORT` cuối phiên; event chi tiết chỉ đại diện đoạn được giữ. Config event đầu phiên đã không còn trong latency trace này. Không dùng 46 dispatch còn trong trace thay cho counter 439 toàn phiên.

## 3. Số liệu toàn phiên

| Chỉ số | Giá trị |
|---|---:|
| Camera receive FPS | 29,93 |
| Detection FPS | 6,28 |
| GUI unique FPS | 9,70 |
| Received / detection frames | 10.289 / 2.158 |
| Dropped / superseded detection frames | 7.345 / 668 |
| Stale inference results | 408 |
| Stale inference jobs skipped | 5 |
| Stale ReID results | 5 |
| Lock success / full loss / recovery sau full loss | 8 / 8 / 7 |
| Lần vào OCCLUDED_HOLD — log ứng dụng | 17 |
| Hold → locked, chưa full loss | 9 |
| Verification-only count | 186 |
| Tracker rebind counter | 1 |
| Pan/tilt dispatch counter | 439 |
| Zoom commands / pulses / RPC errors — summary | 35 / 35 / 0 |
| Zoom in / out — JSONL ZOOM_START | 8 / 27 |
| SCRFD / ArcFace calls | 1.410 / 65 |
| Recognition accepted observations | 65 |
| Rejected face association ambiguity | 478 |
| Identity confirmation count / reverify | 5 / 1 |

Counter tracker rebind không phải ground-truth ID-switch count. 65 recognition observations cũng không phải 65 phiên xác nhận danh tính.

Stale/detection counter ratio khoảng **18,91%**, không phải tỷ lệ frame duy nhất đã đối soát. JSONL chỉ có một dòng `PERCEPTION_RESULT_STALE`; không dùng số dòng đó thay tổng 408, vì có nhiều đường tăng counter và cơ chế giảm tần suất log. Dropped frames cũng không đồng nghĩa RTSP packet loss: latest-frame mailbox chủ động bỏ frame.

| Latency | Mean | P95 |
|---|---:|---:|
| YOLO total | 51,21 ms | 85,75 ms |
| ReID total, 849 mẫu | 191,01 ms | 436,24 ms |
| Camera command queue | 0,89 ms | 3,15 ms |
| Pan/tilt RPC latency, 439 mẫu | 6,79 ms | 13,30 ms |
| Recovery sau full loss, 7 mẫu | 7.610,01 ms | 15.082,05 ms |

So với phiên 11/11 lúc 20:18: detection 8,93 → 6,28 FPS; ReID mean/P95 115/242 → 191/436 ms. Cảnh và độ dài phiên khác nhau, nên đây là so sánh tình trạng, chưa chứng minh thay đổi code gây chậm. Cần tách latency acquisition/recovery/background và tải thiết bị. Log startup vẫn ghi CUDA cho SCRFD/ArcFace và `cuda:0` cho ReID, không có bằng chứng fallback CPU ở các dòng đó.

## 4. Auto pan/tilt: đã chạy, nhưng chưa chứng minh motor quay nhanh

Trong khoảng trace còn lưu:

- **46 dispatch, 46 RPC result ACK=True**.
- Hướng: 24 tilt_up, 18 pan_left, 4 pan_right.
- Cả 46 kế hoạch có **speed 1, pulse 120 ms**. Không suy ra mọi lệnh trong toàn phiên đều như vậy; đoạn này thể hiện baseline pulse, không thể hiện adaptive đang hoạt động.
- **47 STOP event**, đều ACK=True: 44 `PULSE_COMPLETE`, 3 `AUTHORIZATION_OR_GEOMETRY_CHANGED`. Chênh lệch 47 STOP/46 START có thể do đoạn trace bắt đầu giữa một lệnh; không kết luận STOP thừa chỉ từ chênh lệch này.
- Detection hoàn tất → supervisor dispatch: mean **33,55 ms**, max **145,14 ms**.
- Frame receive → wire dispatch: mean **106,56 ms**, max **211,74 ms**.
- Host active duration: mean **135,59 ms**, max **161,20 ms**, gồm I/O/STOP chứ không phải motor telemetry.
- Khoảng nghỉ giữa pulse mean **800,93 ms**, max **15,48 s**; bao gồm cả deadzone/mất identity, không được diễn giải là settle 801 ms.
- 22 mẫu có image displacement; chuyển động ảnh còn chứa chuyển động của người, không phải độ quay cơ học.

Counter lý do quyết định toàn phiên:

| Reason | Số evaluation | Tỷ trọng |
|---|---:|---:|
| TARGET_UNCERTAIN | 647 | 29,53% |
| COOLDOWN | 482 | 22,00% |
| DEADZONE | 493 | 22,50% |
| MOTOR_BUSY | 369 | 16,84% |
| TARGET_LOCKED | 200 | 9,13% |

Đây là tỷ lệ evaluation, không phải tỷ lệ thời gian và không phải tỷ lệ command failure. Trong event lấy mẫu còn giữ, MOTOR_BUSY xuất hiện 33 lần với owner=pan_tilt và một lần với owner=zoom: phần lớn là đang có pulse của chính pan, không phải bằng chứng zoom chiếm motor kéo dài.

Event decision đoạn cuối ghi Auto Pan=True và Auto Zoom=True. Toggle pan=False lúc **08:42:00.437**, sát lúc đóng ứng dụng; event mang reason `USER_TOGGLE` nhưng shutdown cũng gọi cùng API, nên không xác định đây là người dùng bỏ chọn checkbox.

**Đánh giá:** đường tạo lệnh, queue và ACK đã hoạt động. Speed 1 và pulse 120 ms trong đoạn cuối có thể khiến đáp ứng vật lý nhỏ, nhưng cần video/hiệu chuẩn step→chuyển động để khẳng định. Không nên tăng speed trước khi xử lý mất identity và xác minh lỗi tâm của từng lệnh.

## 5. Overlap: có bằng chứng geometry cụ thể

| Thời điểm | Track IDs | IoU giữa hai bbox | Overlap/min-area |
|---|---|---:|---:|
| 08:40:50.232 | 49 và 57 | 0,688 | 1,000 |
| 08:41:53.066 | 58 và 62 | 0,812 | 1,000 |
| 08:41:53.995 | 58 và 62 | 0,965 | 0,995 |
| 08:41:55.238 | 62 và 58 | 0,955 | 0,983 |

Ví dụ lúc **08:41:53.995**:

- ID 58: bbox `[282,171,346,357]`, confidence **0,843**.
- ID 62: bbox `[281,172,347,357]`, confidence **0,357**.

Hai bbox gần như cùng vị trí/kích thước nhưng khác tracker ID. Đây là dấu hiệu mạnh để kiểm tra duplicate detection/track hoặc adapter association; chưa đủ để tự xóa track thấp confidence, vì hai người thật cũng có thể che nhau.

Các event mới ghi `similarity=None` ở từng candidate vì frame đó chưa có ReID score riêng cho mọi bbox. Similarity cấp target là evidence trước đó, không được coi là điểm xác minh mới cho từng người ở frame overlap.

Timeline cuối phiên:

- 08:40:50.232: overlap → hold; 08:40:53.416: về SEARCHING.
- 08:40:55.857, 08:41:37.895, 08:41:41.655, 08:41:50.487: vào hold với reason TRACK_MISSING.
- 08:41:53.066 và 08:41:55.238: overlap → hold.
- 08:41:58.370: full loss; cuối phiên vẫn SEARCHING.

Không thấy TEMP_LOST ping-pong cũ trong log state, nhưng continuity thực tế vẫn chưa đạt.

## 6. Recovery zoom: xác định được điều kiện chặn

Counter mới ghi **381 `RECOVERY_INVALID_ZOOM_ESTIMATE`**, zoom trace cũng có 381 quyết định HOLD cùng reason.

Đối chiếu code hiện tại:

1. `camera/hanet_controller.py::telemetry()` trả None vì không có lens telemetry.
2. `gui/cctv_app.py::get_zoom_level()` trả `float('nan')` khi telemetry=None.
3. Worker truyền giá trị này vào `recover_missing`.
4. `_recover_missing` yêu cầu zoom_level hữu hạn và trả `RECOVERY_INVALID_ZOOM_ESTIMATE` trước khi phát recovery pulse.

Một event lúc **08:40:53.997** có observation age khoảng **203 ms**, publish age khoảng **1,12 ms**, trong khi limits ghi **800/800 ms**. Ở event này timestamp hợp lệ và còn hạn; zoom estimate mới là điều kiện chặn.

Đây là hạn chế chức năng được xác định của recovery trên backend không telemetry, không phải camera RPC thất bại. Không nên sửa bằng cách thay NaN thành “1×” hoặc giá trị giả. Hướng sửa cần tách recovery dựa trên ảnh/xung có ngân sách hữu hạn cho HANET khỏi logic dùng lens position, giữ STOP, freshness, ownership và budget.

27 xung zoom out toàn phiên không đồng nghĩa 27 recovery thành công: recovery bị chặn riêng, zoom out có thể là framing thông thường.

## 7. Telemetry zoom và lỗi hệ thống

Summary mới ghi **35 pulse commands**, khớp **35 ZOOM_START và 35 ZOOM_STOP** trong JSONL ứng dụng. Lỗi đếm HANET bằng 0 ở phiên trước đã có bằng chứng được khắc phục. Summary không ghi RPC error và dropped zoom trace events.

Log ứng dụng không có traceback hoặc RTSP_DISCONNECTED được tìm thấy; ứng dụng kết thúc với thông báo stopped. ACK và thông báo đóng ứng dụng không thay thế xác minh motor dừng vật lý.

## 8. Ưu tiên tiếp theo

1. Sửa recovery cho backend không lens telemetry theo budget xung và feedback ảnh; không giả lập lens position.
2. Lưu frame cùng detection trước/sau ByteTrack tại các mốc duplicate để tìm nguồn hai ID gần trùng. Chưa hạ overlap protection hoặc ReID threshold.
3. Tách ReID latency theo trigger/state, đo queue/GPU/candidate count; điều tra 408 stale results và 6,28 FPS trước khi thay pulse/speed.
4. Khi identity đã ổn định, A/B baseline/adaptive với cùng cảnh, xác nhận Auto Pan bật; đo lỗi tâm, overshoot và wire-to-motion từ video. Các command ACK nhanh hiện tại chưa trả lời tốc độ quay vật lý.
5. Tăng khả năng giữ trace hoặc ghi một bản tóm tắt state/command riêng: hiện chỉ còn khoảng 72 s chi tiết của phiên gần 350 s.
