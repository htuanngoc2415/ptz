# AI PTZ — báo cáo phiên 13/11/2026, 11:20:25

Thời gian: **UTC+7**, từ **11:20:25.067 đến 11:24:50.381**, dài **265,314 giây**. Run ID `cee917b49d254a5a8cb0713a1b704b57`.

## Kết luận

**Đường điều khiển HANET đã hoạt động trở lại ở mức gửi lệnh/nhận ACK trong phiên mới.** Fault tồn đọng được phục hồi, Auto Pan/Tilt dispatch 375 xung; 414 START và 415 STOP đều có ACK theo profile `camera_py`. Không còn tình trạng toàn phiên bị zoom giữ owner như trước.

**Tracking vẫn còn mất khóa nhiều:** 23 lần HOLD, 7 lần mất khóa hoàn toàn, 6 lần phục hồi. Có 2 MATCH khuôn mặt. Ứng dụng đóng trong 319 ms, không còn unconfirmed motor ở trạng thái phần mềm.

Log chưa cung cấp telemetry motor vật lý; ACK theo profile tương thích và dịch chuyển target trên ảnh không tự chứng minh camera quay/dừng chính xác. Không kết luận chất lượng bám thực tế chỉ từ số lệnh ACK.

## 1. Fault và ACK sau bản sửa

| Mốc | Bằng chứng |
|---|---|
| 11:20:28.276 | Nạp FAULT_LATCHED / UNCONFIRMED_PREVIOUS_RUN |
| 11:20:43.331 | STOP kCmdZoomWide có ACK_SUCCESS / CAMERA_PY_EMPTY_RESULT |
| 11:20:43.345 | MOTOR_FAULT_RECOVERED, backend READY |
| 11:20:44.758–44.766 | START kCmdRight được gửi và ACK |
| 11:20:55.319 | Auto Pan/Tilt dispatch đầu tiên, tilt_up |
| 11:24:50.074 | Bắt đầu đóng camera, backend vẫn READY |

Toàn phiên có **829 request trên transport, 829 ACK có ID tương ứng**: 414 START + 415 STOP. Mọi ACK ghi `ack_profile=camera_py`, `evidence=CAMERA_PY_EMPTY_RESULT`. STOP nhiều hơn START một lệnh do xử lý hướng zoom-out tồn đọng từ phiên trước.

Có 257 bản ghi response schema, đều thuộc nhánh CAMERA_PY_EMPTY_RESULT; diagnostics giới hạn tần suất nên số schema ít hơn số ACK. Chỉ một connection generation được mở và đóng. Không có HANET_TRANSPORT_ERROR, reconnect, MOTOR_RECOVERY_FAILED, CAMERA_TRANSACTION_ERROR, STOP_UNCONFIRMED hoặc lỗi checkpoint trong camera log đã đọc.

Checkpoint hiện tại `logs/camera/motor_state/c761c95e9887b30d9adb.json`:

```json
{"version": 1, "active_codes": []}
```

Không có `.pending` cùng basename. Đây là trạng thái ứng dụng đã tự commit sau recovery; lượt audit này không thay đổi checkpoint.

## 2. Pan/Tilt, zoom và ownership

| Hướng | Tổng START trên transport | Auto-pan dispatch |
|---|---:|---:|
| Pan trái | 120 | 119 |
| Pan phải | 84 | 81 |
| Tilt lên | 86 | 85 |
| Tilt xuống | 90 | 90 |
| Zoom-out | 31 | — |
| Zoom-in | 3 | — |
| Tổng | **414** | **375** |

Có **5 START pan/tilt ngoài auto-pan** trong khoảng 11:20:44–49: phải 3, trái 1, lên 1, trước auto-pan dispatch đầu tiên. Chúng phù hợp thao tác manual, nhưng camera event không mang manual_request_id để quy nguồn tuyệt đối. Đã có ACK cho cả 5, khác với các phiên trước không gửi được START.

Auto-pan có đủ 375 DISPATCH, 375 WIRE_DISPATCH, 375 RPC_RESULT, 375 STOP và 375 COMMAND_ENDED. Mọi xung có duration lập kế hoạch 120 ms; đây không phải thời gian chuyển động đo bằng motor telemetry. Zoom gửi 34 START và nhận 34 ACK; có 35 tác vụ ZOOM_STARTED vì một tác vụ đầu bị chặn bởi fault cũ rồi thực hiện recovery STOP, không gửi zoom START mới.

Đối chiếu thứ tự START/STOP theo code không thấy START mới trong khi code trước chưa có STOP_SENT, và không còn code chưa STOP ở cuối log. Đây là kiểm tra tuần tự hóa lệnh phần mềm, không phải đo chuyển động đồng thời của motor.

Trong 388 bản ghi MOTOR_BUSY, **383 owner pan_tilt, 3 owner zoom, 2 owner null**. Owner null có thể do settle window; MOTOR_BUSY ở đây không còn đồng nghĩa zoom bị kẹt cả phiên. Bộ đếm worker cuối phiên ghi 701 MOTOR_BUSY và 670 COOLDOWN; các lần kiểm tra lặp không phải frame riêng biệt.

## 3. Tracking và nhận diện

Cửa sổ dwell là từ log đầu đến GUI shutdown request: **264,995 giây**, gồm khởi tạo; trước transition đầu được tính SEARCHING.

| Trạng thái/chỉ số | Kết quả |
|---|---:|
| SEARCHING | 80,147 s |
| TARGET_LOCKED | 2,340 s |
| PERSON_ZOOM | 92,317 s |
| VERIFIED | 46,057 s |
| OCCLUDED_HOLD | 44,134 s |
| Tổng thời gian có target được chọn | **140,714 s — 53,10%** |
| HOLD / full loss | **23 / 7** |
| Recovery hoàn tất | **6** |
| Recovery telemetry mean / p95 | **4.035,48 / 11.235,77 ms** |
| Nhận diện | **24 UNKNOWN, 4 CANDIDATE, 2 MATCH** |

Khóa đầu lúc 11:20:55.081. Lần phục hồi lâu nhất theo transition ứng dụng: 11:22:04.258 → 11:22:17.479, khoảng **13,221 s**. Lần mất khóa cuối lúc **11:24:24.145** chưa phục hồi trước khi đóng; không tính thành recovery thứ bảy.

Hai MATCH lúc **11:22:44.091** và **11:23:27.035**, cùng nhãn `HoangTuanNgoc`, similarity **0,468 / 0,521**, crop **33×44 / 35×48 px**. Đây là quyết định nhận diện của hệ thống, chưa có ground truth để xác nhận đúng người. Có 364 recognition rejection: 362 FACE_ASSOCIATION_AMBIGUOUS và 2 SESSION_MISMATCH; không phải 364 kết quả ArcFace nhận sai.

So với phiên 10:47, tỷ lệ có target giảm từ 65,54% xuống 53,10%, HOLD tăng 4 → 23 và full loss 1 → 7. Tuy nhiên phiên mới dài hơn khoảng ba lần, có điều khiển pan/tilt/zoom thực sự được dispatch, khác cảnh và thao tác; **không phải A/B và chưa thể quy kết do thay đổi thuật toán tracking**. Vấn đề còn đáng theo dõi là độ ổn định khóa target, không phải thiếu ACK như trước.

## 4. Hiệu năng

Thống kê aggregate lấy LATENCY_REPORT cuối, không lấy số event trong trace còn giữ để suy ra toàn phiên.

| Chỉ số | Phiên 10:47 | Phiên 11:20 |
|---|---:|---:|
| Camera FPS | 29,76 | 29,91 |
| Detection FPS | 7,24 | 10,52 |
| GUI unique FPS | 9,56 | 12,93 |
| YOLO total mean / p95 | 48,62 / 90,09 ms | 32,50 / 54,08 ms |
| ReID total mean / p95 | 200,25 / 477,00 ms | 115,61 / 232,52 ms |
| ReID selection mean / p95 | 134,44 / 196,18 ms | 99,03 / 145,19 ms |
| Receive → GUI submit mean / p95 | 81,62 / 113,18 ms | 56,45 / 73,63 ms |

Pan/tilt RPC latency mean **6,61 ms**, p95 **12,71 ms**. Toàn phiên nhận 7.834 frame, xử lý detection 2.756 frame và GUI unique 3.386 frame. Chênh lệch hiệu năng giữa hai phiên không đủ chứng minh bản sửa ACK làm tăng FPS.

## 5. Shutdown

- GUI yêu cầu đóng: **11:24:50.062**.
- Controls hoàn tất: **11:24:50.077**, sau **15 ms**; owner null, `unconfirmed_motor=false`, `cleanup_failures=[]`.
- Application stopped: **11:24:50.381**, sau **319 ms**.
- Không có SHUTDOWN_STOP_TIMEOUT hoặc SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR.

Trạng thái đóng phần mềm đã sạch hơn các phiên trước; `physical_motor_stopped` vẫn null vì không có phép đo motor vật lý.

## 6. Nguồn, phạm vi và bước tiếp theo

Đã đọc camera log hiện tại cùng **2 rotation files**: **19.241 events**, từ 11:20:28.276 đến 11:24:50.099, có cả startup và shutdown. Latency trace còn **43.112 dòng**, chỉ bao phủ **11:23:51.918 → 11:24:50.216903**; không dùng phần trace này làm timeline toàn phiên. Aggregate từ đầu profiler; percentile theo cửa sổ giới hạn của profiler.

- [Application log](../logs/ptz_20261113_112025.log).
- [Camera log](../logs/camera/camera_control_20261113_112028_272959.log), [rotation 1](../logs/camera/camera_control_20261113_112028_272959.log.1), [rotation 2](../logs/camera/camera_control_20261113_112028_272959.log.2).
- [Latency summary](../logs/latency/latency_summary_20261113_112028_272959.csv).
- [Metrics và SHA-256 nguồn](ai_ptz_current_status_20261113_112025_metrics.json).
- [Bản sửa tương thích](hanet_camera_py_compat_20261113.md).

Hash source trong manifest bản sửa vẫn khớp tại thời điểm audit. Lượt này chỉ đọc dữ liệu và viết báo cáo, không sửa code/config, không gửi lệnh camera.

Đường ACK/recovery/dispatch đã có bằng chứng hoạt động trong phiên thật. Bước đánh giá tiếp theo là đối chiếu video/quan sát với các đoạn HOLD và full loss, xem camera có bám đúng target, đổi hướng quá nhiều hay target ra khỏi khung hay không. Chưa có dữ liệu đủ để kết luận rung/đảo hướng hoặc tự thay speed, pulse hay tracking thresholds.
