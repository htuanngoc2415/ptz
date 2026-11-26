# Phân tích timestamp phiên mới nhất — 16/11/2026, 16:36–16:39

## 1. Kết luận

Phiên này thực sự chạy **`asynchronous`**, xác nhận bởi `PAN_TILT_CONFIG`. Frame → worker dispatch trung bình **111,20 ms**; dispatch → RPC trả về **19,69 ms**; tổng frame → kết quả RPC **130,89 ms**. Chỉ tính 34 START thành công thì tổng trung bình **130,21 ms**.

Bàn giao observation sang worker nhanh: **2,10 ms** trên 35 intent đã dispatch. Phần chờ sau khi consume tới dispatch trung bình **33,03 ms**, có liên quan lịch pulse/settle, quyền motor và thời điểm worker thức dậy; không được coi toàn bộ là overhead vô ích. Capture đạt **29,88 FPS**, detection **3,90 FPS**, GUI **11,00 FPS** theo thống kê toàn phiên. Vấn đề đáng chú ý hơn ACK là tải xử lý AI/tìm lại người: 4 lần mất target, 3 lần tìm lại, mỗi lần khoảng **4,92–10,01 giây**.

Không có bằng chứng phiên này nhanh hơn baseline 13:49. Mean tổng tăng khoảng 13,49 ms; P95 tổng gần như bằng nhau. Hai phiên khác cảnh/người/thời lượng và số lệnh, nên không thể quy chênh lệch cho thay đổi asynchronous.

## 2. Nguồn log và giới hạn độ phủ

- `logs/ptz_20261116_163631.log` và `.jsonl`: phiên ứng dụng mới nhất, kết thúc 16:39:42.260.
- `logs/camera/camera_control_20261116_163635_205025.log`: 16:36:35.210–16:39:41.949; đủ cấu hình khởi động và shutdown, khoảng 186,74 giây.
- `logs/latency/latency_trace_20261116_163635_205025.jsonl` cùng `.1`, `.2`, `.3`: chỉ còn **16:37:56.788–16:39:42.091**. Phần đầu đã bị rotation ghi đè. Không được dùng riêng trace còn lại để kết luận toàn phiên không có event.
- `latency_summary_20261116_163635_205025.csv` và `LATENCY_REPORT` cuối: mean/count tích lũy từ đầu profiler; percentile theo cửa sổ giới hạn của profiler.
- `metrics.json`: số liệu tái tính, bộ đếm, ví dụ, metadata và SHA-256 camera/trace. `analyze.py` tái tạo số liệu, `write_report.py` tạo báo cáo này. Không sửa mã điều khiển hay gửi lệnh camera.

Đã ghép RPC bằng `rpc_command_id` + operation; intent bằng `command_id`. Tách `kCmdLeft/Right/Up/Down` khỏi zoom: event tên `PAN_TILT_START_*` được dùng cho cả zoom nên đếm tên event đơn thuần sẽ sai.

Log camera dùng `+07:00`; log tracking JSONL dùng UTC `+00:00`: **09:37 UTC = 16:37 Việt Nam**, không phải lệch đồng hồ 7 giờ. Interval dùng monotonic, không trừ chuỗi giờ hiển thị. Timestamp receive là frame đã decode đến ứng dụng, không phải lúc sensor chụp.

## 3. Độ trễ lệnh

| Khoảng thời gian | N | Mean (ms) | P50 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| START gửi → ACK, pan/tilt cả auto và manual | 36 | 6.71 | 6.58 | 11.32 | 14.57 | 16.22 |
| STOP gửi → ACK, pan/tilt cả auto và manual | 36 | 5.56 | 4.93 | 10.99 | 13.53 | 13.85 |
| Auto dispatch → wire dispatch | 34 | 11.10 | 9.35 | 20.49 | 33.70 | 38.54 |
| Auto dispatch → RPC result, gồm 1 bị từ chối | 35 | 19.69 | 17.49 | 28.61 | 39.26 | 43.92 |
| Frame → auto dispatch | 35 | 111.20 | 113.66 | 153.37 | 265.75 | 319.14 |
| Frame → auto RPC result, gồm 1 bị từ chối | 35 | 130.89 | 130.59 | 179.37 | 284.30 | 338.33 |
| Frame → auto RPC thành công | 34 | 130.21 | 130.56 | 179.37 | 285.89 | 338.33 |

Có **35 auto intent dispatch**, **34 START auto thành công**, **1 bị từ chối do stale**. Wire ghi **36 START pan/tilt** (34 auto + 2 manual), mỗi START đều có ACK; 36 STOP pan/tilt đều có ACK. Ngoài ra có 31 START/STOP zoom-out (`kCmdZoomWide`). Tổng wire START là 67, không phải 67 lệnh quay tự động.

ACK START nằm trong dispatch→RPC result, **không cộng thêm ACK một lần nữa**. Thời gian từ host gửi đến ACK gồm truyền mạng, xử lý protocol và host nhận; không tách được thời điểm camera nhận chính xác hay lúc motor bắt đầu quay.

## 4. Phân rã 35 intent auto trên cùng mẫu

| Khoảng thời gian | N | Mean (ms) | P50 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| Frame nhận → detection hoàn tất | 35 | 60.81 | 57.96 | 90.36 | 99.33 | 103.87 |
| Detection hoàn tất → quyết định target | 35 | 15.25 | 5.86 | 18.82 | 182.13 | 266.08 |
| Quyết định → publish | 35 | 0.01 | 0.01 | 0.02 | 0.03 | 0.03 |
| Publish → consume | 35 | 2.10 | 1.46 | 5.20 | 6.32 | 6.59 |
| Consume → dispatch | 35 | 33.03 | 30.88 | 91.89 | 95.19 | 96.61 |
| Dispatch → RPC result | 35 | 19.69 | 17.49 | 28.61 | 39.26 | 43.92 |

Tổng mean các bước xấp xỉ **130,89 ms** (sai số làm tròn/timestamp emit rất nhỏ). Trong đó frame→quyết định ~76,06 ms, publish/consume ~2,11 ms, consume→dispatch ~33,03 ms và RPC ~19,69 ms.

- Mailbox không phải nguồn chậm chính ở các intent này. Trace còn giữ có 361 event consume, mean publish→consume **1,12 ms**, P95 **3,25 ms**; đây là mẫu cuối phiên, khác mẫu 35 intent, không trộn hai số.
- Detection→decision P50 **5,86 ms** nhưng max **266,08 ms**: có outlier lớn, không phải mọi frame đều tốn 266 ms.
- Consume→dispatch P50 **30,88 ms**, P95 **91,89 ms**. Cấu hình pulse 120 ms, settle 150 ms, worker có chu kỳ chờ 30 ms. Log có 41 quyết định cooldown và 54 motor busy (counter toàn phiên). Cần tách các điều kiện này trước khi giảm khoảng chờ.
- Stop-return→dispatch kế tiếp P50 **168,35 ms**, mean **1.355,45 ms**, max **28,59 s**. Mean lớn gồm thời gian không đủ điều kiện/mất target/manual; không thể gọi đó là độ trễ mạng.

## 5. Ví dụ timestamp thực tế

Intent `pan_099df84a698f4fe2b5ae76b0ce4321d3`, frame 1245, tilt_up speed 1:

| Mốc | Giờ Việt Nam |
|---|---|
| Frame nhận, suy từ monotonic cùng event | ≈16:37:17.317 |
| Detection hoàn tất | ≈16:37:17.368 |
| Quyết định/publish target | ≈16:37:17.635 |
| Worker dispatch | 16:37:17.636 |
| START gửi | 16:37:17.650 |
| ACK START | 16:37:17.654 |
| RPC trả về | 16:37:17.655 |
| STOP trả về | 16:37:17.791 |

Đây là outlier: frame→dispatch **319,14 ms**, frame→RPC **338,33 ms**. Phần lớn chậm trước quyết định target, không nằm ở ACK START khoảng vài ms. Các giờ suy ra được làm tròn, không phải event sensor độc lập.

## 6. AI, GUI và tracking

Thống kê profiler toàn phiên: 5.584 frame nhận, 729 detection frame, 2.055 GUI submission; 4.718 frame được ghi là dropped và 87 superseded detection frame. Với cơ chế lấy frame mới nhất, các bộ đếm này phản ánh frame không được đưa qua inference/được thay thế; **không chứng minh RTSP mất 4.718 gói/frame**.

| Công đoạn | Mean | P95 | Ghi chú |
|---|---:|---:|---|
| YOLO total | 41,58 ms | 72,30 ms | 731 mẫu timing, khác counter 729 detection |
| ReID selection | 263,96 ms | 402,43 ms | 458 mẫu, phụ thuộc trạng thái |
| ReID reacquisition wall | 272,13 ms | 411,41 ms | 324 mẫu tìm lại |
| Periodic verify wall | 260,35 ms | 410,52 ms | 17 mẫu, worker riêng |
| Frame receive → GUI submit | 70,66 ms | 96,08 ms | Không phải sensor→màn hình |
| Camera command queue wait | 0,83 ms | 2,50 ms | 327 giao dịch, cả query/control |

Không cộng các mean trên vì khác mẫu và có công đoạn lồng nhau/song song. YOLO 41,58 ms không đồng nghĩa pipeline đạt 24 FPS: còn ReID/state, scheduling, gates và các công việc khác. Số liệu phù hợp với chi phí acquisition/recovery ReID đáng kể, nhưng chưa đủ xác định nguyên nhân duy nhất khiến throughput 3,9 FPS.

| Mất target (giờ VN) | Tìm lại | Khoảng trống từ event | Track ID |
|---|---|---:|---|
| 16:37:29.005 | 16:37:38.591 | 9,587 s | 51 → 70 |
| 16:37:42.965 | 16:37:52.976 | 10,011 s | 70 → 72 |
| 16:38:03.345 | 16:38:08.266 | 4,921 s | 72 → 82 |
| 16:38:12.321 | Chưa tìm lại trước khi đóng | ≈90 s đến cuối phiên | 82 |

Track ID đổi sau reacquisition không tự chứng minh đổi nhầm danh tính. Cần video/ground truth để kết luận identity switch. Cuối phiên top1 ReID thường khoảng 0,47–0,56, confirm 0/3; thấp hơn threshold 0,65 ghi trong score log. Không nên hạ ngưỡng chỉ dựa vào kết quả này.

Overlap counters: 36 weak-overlap continuity, 6 identity-threatening, 128 overlap-without-trusted-continuity, 81 skipped-all-candidates-overlap. Đây là số lần đánh giá, không phải 81 lần mất người. Chúng gợi ý cần xem video đúng các mốc mất target; chưa chứng minh overlap là nguyên nhân của cả 4 lần.

## 7. An toàn và shutdown

- 16:38:00.124 có một RPC result `Stale camera movement rejected`. Đây là intent bị chặn trước wire, không phải timeout mạng. Không có đủ log lý do chi tiết để phân biệt chắc chắn supersession/session/geometry trong lần này.
- Auto STOP: 31 `PULSE_COMPLETE`, 3 `AUTHORIZATION_OR_GEOMETRY_CHANGED`, 1 `COMMAND_FAILED` đi với intent bị từ chối. Tất cả 35 STOP return được xác nhận thành công.
- Không thấy MOTOR_FAULT/STOP_UNCONFIRMED trong camera log phiên này.
- 16:39:41.949 `SHUTDOWN_COMPLETED`: owner null, `unconfirmed_motor=false`, cleanup_failures rỗng. 16:39:42.260 ứng dụng báo dừng.
- File tên `first_failures` chứa schema `ACK_SUCCESS` trong phiên này; tên file không đủ để kết luận có lỗi.

ACK chỉ xác nhận protocol theo profile `camera_py`, không đo motor đã quay/dừng vật lý. Capture/AI/transport có thread riêng theo log; không có bằng chứng về backlog lệnh mạng lớn. Trace bị rotation khiến việc tái dựng overlap thời gian cho toàn bộ phiên không đầy đủ.

## 8. So với phiên 13:49

| Mean/P95 | Baseline 13:49, 390 intent | Phiên này, 35 intent |
|---|---:|---:|
| Frame→dispatch | 100,93 / 161,85 ms | 111,20 / 153,37 ms |
| Dispatch→RPC result | 16,47 / 25,55 ms | 19,69 / 28,61 ms |
| Frame→RPC result | 117,40 / 179,64 ms | 130,89 / 179,37 ms |
| START sent→ACK | 4,95 / 11,11 ms | 6,71 / 11,32 ms |

ACK ở phiên mới lấy 36 wire pan/tilt, gồm manual; tổng RPC gồm 1 intent không thực thi. Đây là đối chiếu mô tả, không phải A/B tương đương. Mẫu 35 lệnh nhỏ nên P99 rất nhạy với outlier. Không có bằng chứng cải thiện latency tổng; cũng chưa đủ để kết luận asynchronous gây regression.

## 9. Việc nên kiểm tra tiếp

1. Giữ nguyên ngưỡng/tốc độ hiện tại, xem đoạn video quanh bốn mốc mất target; xác định che khuất, detection miss, motion blur hay ReID không đạt.
2. Profiling riêng acquisition/recovery và trạng thái locked để giải thích detection FPS thấp; không tối ưu ACK trước vì ACK chỉ ~6,7 ms.
3. Đo tách consume→dispatch thành cooldown, motor busy và worker scheduling trước khi sửa vòng chờ.
4. Tăng khả năng lưu trace toàn phiên hoặc tách observation trace để không mất phần đầu. Không thay cấu hình trong tác vụ phân tích này.
5. Nếu cần độ trễ camera thật bắt đầu quay, dùng video đồng bộ/encoder/telemetry motor; log ACK hiện tại không cung cấp phép đo đó.
