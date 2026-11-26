# Báo cáo kiểm tra hai phiên AI PTZ mới nhất — 16/11/2026

Ngày kiểm tra: **16/11/2026**. Thời gian dưới đây là **UTC+7**. “Hai log mới nhất” được hiểu là hai **phiên chạy** mới nhất theo timestamp tên file; `.log` và `.jsonl` cùng basename thuộc một phiên.

## 1. Kết luận chính

**Điều khiển HANET hoạt động ở mức gửi lệnh và nhận ACK trong cả hai phiên.** Không thấy lỗi transport, timeout, fault motor hay lỗi shutdown trong camera log đã đọc. Mỗi START/STOP đều có ACK tương ứng theo profile `camera_py`. Điều này chưa chứng minh chuyển động hoặc dừng motor vật lý vì `physical_motor_stopped=null`.

**Phiên 08:43 có tỷ lệ thời gian giữ target cao hơn: 71,33% so với 47,56%.** Tuy nhiên vẫn có 11 lần HOLD trong khoảng 113 giây; chưa thể kết luận tracking đã ổn định. Phiên 08:33 có 5 lần mất khóa hoàn toàn, lần cuối kéo dài đến khi đóng ứng dụng.

**Nhận diện có MATCH ở cả hai phiên**, nhưng còn nhiều lần không gắn được khuôn mặt với target và nhiều kết quả UNKNOWN. Đây là điểm cần ưu tiên kiểm tra cùng tính liên tục của tracking; chưa có ground truth để xác nhận các MATCH đúng người.

## 2. Phạm vi và phương pháp

| Nguồn/chỉ số | Phiên A — 08:33:42 | Phiên B — 08:43:14 |
|---|---:|---:|
| Application bắt đầu | 08:33:42.802 | 08:43:14.667 |
| GUI yêu cầu đóng | 08:39:23.039 | 08:45:07.660 |
| Application stopped | 08:39:23.341 | 08:45:07.901 |
| Cửa sổ tính trạng thái | 340,237 s | 112,993 s |
| Event application JSONL | 803 | 267 |
| Event camera, gồm rotation | 23.301 | 9.274 |
| Camera log bao phủ | 08:33:48.377–08:39:23.060 | 08:43:17.923–08:45:07.681 |
| LATENCY_REPORT cuối | 08:39:23.188698 | 08:45:07.792748 |

Đã đọc application log/JSONL, camera log và toàn bộ rotation camera tương ứng: A có `.log.1`, `.log.2`; B có `.log.1`. Hiệu năng lấy CSV summary và counters của LATENCY_REPORT cuối trong trace hiện có, không đếm trace còn giữ để suy ra tổng toàn phiên. Mean/count là aggregate từ đầu profiler; percentile dùng cửa sổ giới hạn theo profiler.

Thời gian trạng thái được cộng từ các transition `[STATE]`, từ dòng application đầu đến GUI shutdown request; đoạn trước transition đầu tính SEARCHING. “Giữ target” là tổng TARGET_LOCKED + PERSON_ZOOM + VERIFIED, không gồm OCCLUDED_HOLD; không đồng nghĩa luôn bám đúng người.

## 3. Tracking và recovery

| Trạng thái/chỉ số | Phiên A | Phiên B |
|---|---:|---:|
| SEARCHING | 156,216 s | 20,982 s |
| TARGET_LOCKED | 1,471 s | 1,505 s |
| PERSON_ZOOM | 121,486 s | 30,791 s |
| VERIFIED | 38,849 s | 48,297 s |
| OCCLUDED_HOLD | 22,215 s | 11,418 s |
| Giữ target | **161,806 s / 47,56%** | **80,593 s / 71,33%** |
| Số lần vào HOLD | 12 | 11 |
| Mất khóa hoàn toàn / recovery hoàn tất | 5 / 4 | 1 / 1 |
| Recovery mean / max | 10,299 / 21,533 s | 2,269 / 2,269 s |
| Khóa đầu | 08:34:20.257 | 08:43:33.380 |

**Phiên A:** các khoảng SEARCHING sau mất khóa là 08:35:45.533→08:35:48.957; 08:36:02.669→08:36:07.600; 08:36:29.002→08:36:40.315; 08:36:48.609→08:37:10.143. Lần cuối bắt đầu **08:38:05.480**, chưa phục hồi khi GUI đóng, dài **77,559 s**. Những mẫu ReID cuối phiên khoảng 0,50–0,53, thấp hơn threshold 0,70 trong JSONL; dữ liệu này phù hợp việc không xác nhận lại target, chưa đủ kết luận do sai threshold, đổi người hay thay đổi góc nhìn.

**Phiên B:** mất khóa hoàn toàn lúc **08:43:41.296**, phục hồi **08:43:43.565**. Các HOLD khác phục hồi về TARGET_LOCKED, nhưng xuất hiện dồn dập khoảng 08:43:46–08:43:57 và 08:44:58–08:45:01. Cuối phiên ở PERSON_ZOOM. Tần suất HOLD khoảng **5,84 lần/phút**, so với **2,12 lần/phút** ở A; do đó không nên chỉ nhìn tỷ lệ giữ target để tuyên bố mọi mặt đều cải thiện.

Hai phiên khác thời lượng/cảnh/thao tác, không phải A/B có kiểm soát; không quy chênh lệch cho bản sửa code.

## 4. Nhận diện khuôn mặt

| Chỉ số | Phiên A | Phiên B |
|---|---:|---:|
| UNKNOWN / CANDIDATE / MATCH trong application log | 35 / 6 / 5 | 12 / 4 / 3 |
| FACE_DETECTED / FACE_LOST trong application JSONL | 105 / 104 | 51 / 51 |
| Recognition rejected tổng, counter cuối | 380 | 178 |
| FACE_ASSOCIATION_AMBIGUOUS | 377 | 177 |
| STALE_RECOGNITION_QUEUE | 2 | 1 |
| SESSION_MISMATCH | 1 | 0 |

Các MATCH đều mang nhãn `HoangTuanNgoc`:

- A: **08:35:39.079 (0,571)**; **08:35:52.855 (0,521)**; **08:36:41.888 (0,520)**; **08:37:14.789 (0,506)**; **08:37:25.974 (0,615)**.
- B: **08:43:57.940 (0,549)**; **08:44:20.210 (0,503)**; **08:44:30.919 (0,463)**.

Ở B, nhiều UNKNOWN đầu/cuối phiên có crop chỉ khoảng 13–17 × 17–23 px. Tuy nhiên crop lớn hơn cũng có UNKNOWN, nên kích thước nhỏ chỉ là một yếu tố cần kiểm chứng. MATCH cuối B có crop 41×57 px; ba MATCH không nhất thiết là ba người hoặc ba lần khóa độc lập.

Đối chiếu `runtime/face_worker.py`: FACE_ASSOCIATION_AMBIGUOUS phát sinh khi phát hiện faces nhưng không chọn được face gắn với target. Vì vậy **không được diễn giải 377/177 rejection thành số lần ArcFace nhận sai**. FACE_DETECTED/LOST là event ứng dụng, không phải số người hay tỷ lệ chính xác của detector.

## 5. Pan/tilt, zoom và ACK

| Lệnh START trên transport | Phiên A | Phiên B |
|---|---:|---:|
| Trái | 116 | 71 |
| Phải | 108 | 77 |
| Lên | 86 | 31 |
| Xuống | 60 | 33 |
| Zoom-out | 29 | 11 |
| Zoom-in | 27 | 3 |
| Tổng START | **426** | **226** |
| Tổng STOP | **426** | **226** |
| Auto-pan dispatch | **370** | **212** |
| Zoom action | **56** | **14** |

Event transport tên `PAN_TILT_START_SENT/ACK` và `PAN_TILT_STOP_SENT/ACK` chứa cả mã zoom; bảng được phân loại theo `rpc_code` để tránh cộng trùng. Tất cả 852 ACK ở A và 452 ACK ở B là `ACK_SUCCESS`, request ID khớp response ID, `ack_profile=camera_py`, evidence `CAMERA_PY_EMPTY_RESULT`.

Auto-pan có đủ số DISPATCH, WIRE_DISPATCH, RPC_RESULT, STOP và COMMAND_ENDED tương ứng 370/212. Không thấy event CAMERA_TRANSACTION_ERROR, HANET_TRANSPORT_ERROR, STOP_UNCONFIRMED hoặc motor fault trong các camera log này. File tên `first_failures` có chứa schema ACK_SUCCESS; tên file tự nó không chứng minh lỗi.

Counters `ZOOM_SESSION_MISMATCH` là **1.360 / 781**, trong đó `control_stop_session_mismatch` **1.271 / 713**, `ptz_observation` **89 / 68**. Đối chiếu `control/auto_zoom.py`, nhánh này kiểm tra session không còn hợp lệ rồi gọi STOP với lý do STALE_SESSION. Đây là số lần xử lý kiểm tra, không phải số lệnh RPC thất bại hay số frame inference lỗi. Tần suất cao đáng kiểm tra vòng đời session/STOP bằng replay trước khi kết luận có vòng lặp bất thường; log vẫn cho thấy ACK và shutdown hoàn tất.

## 6. Hiệu năng

| Chỉ số | Phiên A | Phiên B |
|---|---:|---:|
| Camera receive FPS | 29,85 | 29,80 |
| Detection FPS | 9,91 | 11,25 |
| GUI unique FPS | 11,76 | 12,34 |
| Frame nhận / detection / GUI unique | 9.996 / 3.319 / 3.937 | 3.274 / 1.236 / 1.356 |
| YOLO total mean / p95 | 36,84 / 65,68 ms | 34,47 / 57,95 ms |
| ReID total mean / p95 | 70,69 / 182,07 ms | 110,66 / 256,19 ms |
| Receive → GUI submit mean / p95 | 64,48 / 90,70 ms | 58,81 / 73,47 ms |
| Pan/tilt RPC mean / p95 | 6,23 / 11,36 ms | 6,44 / 11,52 ms |

Đầu vào gần 30 FPS ở cả hai phiên; xử lý detection và GUI thấp hơn tốc độ nhận. B có GUI/YOLO tốt hơn theo số đo nhưng ReID chậm hơn; trạng thái xử lý/cảnh khác nhau nên chưa suy ra hồi quy hiệu năng. Receive→GUI submit không phải độ trễ end-to-end từ cảm biến đến màn hình, không bao gồm toàn bộ RTSP/hiển thị.

## 7. Shutdown

| Chỉ số | Phiên A | Phiên B |
|---|---:|---:|
| GUI request → controls complete | 21 ms | 21 ms |
| GUI request → application stopped | 302 ms | 241 ms |
| Backend khi đóng | READY | READY |
| Motor owner cuối | null | null |
| unconfirmed_motor | false | false |
| cleanup_failures | [] | [] |

Không thấy WARNING/ERROR trong hai application log. Shutdown sạch theo trạng thái phần mềm; không có telemetry để xác nhận motor vật lý đã dừng.

## 8. Việc nên ưu tiên tiếp theo

1. Đối chiếu video đoạn **08:38:05–08:39:23** của A để xác định target rời khung, bị che hay ReID không vượt ngưỡng. Chưa đủ cơ sở hạ threshold chỉ từ các điểm similarity cuối phiên.
2. Xem các cụm HOLD của B để kiểm tra đổi track ID, che khuất, bbox và chuyển động camera. B có ít full loss hơn nhưng HOLD dày hơn theo phút.
3. Kiểm tra face association và crop theo target ở các rejection; tách chất lượng phát hiện/gán mặt khỏi chất lượng embedding nhận diện.
4. Replay các đoạn đổi session để kiểm tra lượng STOP/session mismatch lớn có phải kiểm tra lặp dự kiến hay vấn đề cần tối ưu; giữ nguyên cơ chế chặn session cũ khi chưa xác định nguyên nhân.

Lượt kiểm tra này chỉ phân tích dữ liệu và viết báo cáo; không thay code/config hoặc gửi lệnh camera. Không chạy test ứng dụng vì không sửa logic.

## 9. Nguồn đối chiếu

- Phiên A: [application log](../logs/ptz_20261116_083342.log), [JSONL](../logs/ptz_20261116_083342.jsonl), [camera](../logs/camera/camera_control_20261116_083348_369309.log), [rotation 1](../logs/camera/camera_control_20261116_083348_369309.log.1), [rotation 2](../logs/camera/camera_control_20261116_083348_369309.log.2), [latency summary](../logs/latency/latency_summary_20261116_083348_369309.csv), [latency trace](../logs/latency/latency_trace_20261116_083348_369309.jsonl).
- Phiên B: [application log](../logs/ptz_20261116_084314.log), [JSONL](../logs/ptz_20261116_084314.jsonl), [camera](../logs/camera/camera_control_20261116_084317_917696.log), [rotation 1](../logs/camera/camera_control_20261116_084317_917696.log.1), [latency summary](../logs/latency/latency_summary_20261116_084317_917696.csv), [latency trace](../logs/latency/latency_trace_20261116_084317_917696.jsonl).
