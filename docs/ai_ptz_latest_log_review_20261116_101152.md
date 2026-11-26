# Báo cáo log AI PTZ mới nhất — phiên 10:11 ngày 16/11/2026

Thời gian: UTC+7. Kiểm tra phiên **10:11:52**, đối chiếu phiên liền trước **09:49:07**. Báo cáo trước về 08:33/08:43 được giữ nguyên để lưu lịch sử.

## 1. Kết luận

**Phiên mới nhất thực sự chạy bản mới BoT-SORT + identity-aware asynchronous OSNet. Tuy nhiên tracking chưa ổn định:** 15 lần vào HOLD, 7 lần mất khóa hoàn toàn, chỉ phục hồi 6 lần trước khi đóng. Chưa có bằng chứng bản mới bám đúng người tốt hơn bản cũ.

Log khởi động xác nhận trực tiếp: dòng 48 của phiên 10:11 ghi `BoT-SORT + identity-aware asynchronous OSNet`; cùng dòng ở phiên 09:49 ghi `ByteTrack + periodic/event-driven OSNet verification`. Không suy đoán phiên bản từ cấu hình mặc định hiện tại.

**Camera phiên mới gửi/nhận ACK bình thường**, không thấy lỗi transport hoặc STOP_UNCONFIRMED trong camera log. Phiên 09:49 có lỗi WebSocket lúc 09:54:39 rồi phục hồi bằng STOP ACK. Đây là lỗi transport riêng, không phải bằng chứng tracker đổi nhầm người.

## 2. Tracking theo trạng thái ứng dụng

Cửa sổ đo: từ STARTUP đến SHUTDOWN_REQUESTED của GUI trong camera log. Trước transition đầu tính SEARCHING. “Giữ target” = TARGET_LOCKED + PERSON_ZOOM + VERIFIED, không bao gồm HOLD; đây là trạng thái phần mềm, không phải thời gian bám đúng người được xác nhận bằng video.

| Chỉ số | 09:49 — bản cũ | 10:11 — bản mới |
|---|---:|---:|
| Thời lượng (s) | 335.663 | 118.597 |
| SEARCHING (s) | 172.632 | 58.616 |
| TARGET_LOCKED (s) | 2.096 | 3.083 |
| PERSON_ZOOM (s) | 83.550 | 19.451 |
| VERIFIED (s) | 37.589 | 1.542 |
| OCCLUDED_HOLD (s) | 39.796 | 35.905 |
| Giữ target (s) | 123.235 | 24.076 |
| Giữ target (%) | 36.714 | 20.301 |
| Số lần HOLD | 15 | 15 |
| Mất khóa hoàn toàn / phục hồi | 9 / 8 | 7 / 6 |
| Khóa đầu | 09:49:37.837 | 10:12:15.802 |

Hai phiên khác thời lượng, cảnh và thao tác; đây không phải phép A/B có kiểm soát. Bản mới có tỷ lệ giữ target thấp hơn trong hai log này, nhưng chưa thể quy nguyên nhân hoàn toàn cho thay đổi thuật toán. Mẫu số có cả thời gian khởi động/chọn target.

Các lần mất khóa hoàn toàn của phiên mới:

| Bắt đầu SEARCHING | Khóa lại | Thời gian SEARCHING (s) |
|---|---|---:|
| 10:12:27.641 | 10:12:29.222 | 1.581 |
| 10:12:33.154 | 10:12:42.361 | 9.207 |
| 10:12:50.937 | 10:12:52.439 | 1.502 |
| 10:13:07.227 | 10:13:10.229 | 3.002 |
| 10:13:22.837 | 10:13:25.205 | 2.368 |
| 10:13:30.157 | 10:13:33.899 | 3.742 |
| 10:13:37.185 | Chưa phục hồi khi GUI đóng 10:13:51.042 | 13.857 |

Một số khóa rất ngắn: 10:13:33.899 vừa TARGET_LOCKED, đến 10:13:34.006 đã HOLD (**107 ms**). Phiên cũ có một lần SEARCHING dài **97.435 s**, từ 09:53:01.873 đến 09:54:39.308.

## 3. Identity, overlap và GMC

`IDENTITY_METRICS_FINAL` phiên mới xác nhận:

- 270 lần gọi ReID; 16 kết quả stale bị loại.
- 338 lần thấy bbox tương ứng track đang khóa; 142 lần được cho phép điều khiển; 196 lần thấy track nhưng identity không chắc chắn. Đây là số lần cập nhật selector, không phải toàn bộ 617 frame detection.
- 15 recovery attempts / 14 recoveries, còn recovery chưa hoàn tất; 7 short recovery failures; 1 tracker rebinding.
- Recovery nội bộ trung bình **3.884 s**, lớn nhất **12.299 s**. Bộ đếm này bao gồm phục hồi HOLD, khác với 7 mất khóa hoàn toàn / 6 phục hồi trong state ứng dụng; không cộng hai nhóm với nhau.
- `observed_target_seconds=20.976 s`: chỉ cộng khoảng liên tiếp được authorize, có chặn khoảng cách mẫu; không cùng định nghĩa với thời gian state ở bảng trên.
- Tracker rebinding không chứng minh identity switch. `ptz_commands_during_uncertain=null` là chưa có phép đo trực tiếp, không phải 0.

Trace còn giữ có 102 decision: PERSON_OVERLAP 22, AMBIGUOUS_IDENTITY 7, CONFIRMING_IDENTITY 24, BELOW_CONFIRMATION_THRESHOLD 6, TRACK_MISSING 1; các reason khác là RESET/REACQUIRED/VERIFIED. Đây là **số bản ghi chẩn đoán còn giữ**, không phải số lần lỗi độc lập hay tổng toàn phiên. Reason có thể lặp giữa nhiều bản ghi.

67 decision có camera_movement với `applied=true`, 35 không có thông tin camera_movement. Điều này chứng minh GMC có được sử dụng, không chứng minh chất lượng bù chuyển động đạt yêu cầu ở mọi frame.

Đối chiếu code `IdentityAwareTargetSelector.select_person`: `_candidate_overlap(...)` gọi HOLD với PERSON_OVERLAP; track mất hoặc temporal không đáng tin cũng chặn điều khiển. Vì vậy overlap/độ liên tục là hướng kiểm tra cụ thể. Chưa đủ dữ liệu video để kết luận HOLD là quá bảo thủ hay đang ngăn bám nhầm.

Mẫu gần cuối 10:13:50 có raw OSNet ≈0.537, final similarity ≈0.615, margin ≈0.078, trạng thái LOST/CONFIRMING_IDENTITY, control_allowed=false. Điểm sau gallery khác điểm ảnh tham chiếu gốc; không nên gọi cả hai là cùng một score. Mẫu này chưa chứng minh target thật có trong các candidate và không đủ cơ sở để hạ threshold.

## 4. Nhận diện khuôn mặt

| Chỉ số | 09:49 | 10:11 |
|---|---:|---:|
| UNKNOWN / CANDIDATE / MATCH trong application log | 32 / 5 / 3 | 8 / 3 / 1 |
| Recognition attempts, counter cuối | 40 | 15 |
| FACE_ASSOCIATION_AMBIGUOUS, counter cuối | 343 | 27 |
| Recognition rejected SESSION_MISMATCH | 0 | 4 |

MATCH phiên mới lúc **10:13:10.887**, nhãn `HoangTuanNgoc`, similarity **0.657**, crop **34×47 px**, confirm=2/2. UNKNOWN đầu phiên có crop khoảng 16–23×21–29 px; UNKNOWN cuối có crop 36×49 px, nên kích thước không giải thích được tất cả.

FACE_ASSOCIATION_AMBIGUOUS là không gán chắc chắn khuôn mặt cho target, không phải số lần ArcFace nhận sai. Số MATCH ít hơn không tự nó chứng minh độ chính xác kém hơn: thời lượng và thời gian khóa khác nhau. Không có ground truth để xác nhận MATCH hoặc đo bám nhầm người.

## 5. Hiệu năng

Mean/count lấy aggregate từ đầu profiler; p95 theo cửa sổ giới hạn của profiler. Không dùng số dòng trace còn giữ để tính FPS toàn phiên.

| Chỉ số | 09:49 | 10:11 |
|---|---:|---:|
| Camera receive FPS | 29.93 | 29.78 |
| Detection FPS | 6.46 | 5.35 |
| GUI unique FPS | 10.77 | 11.72 |
| yolo_total_ms mean / p95 | 44.69 / 79.07 | 46.41 / 72.93 |
| reid_total_ms mean / p95 | 144.83 / 249.68 | 324.43 / 455.68 |
| identity_track_association_ms mean / p95 | Không có stage tương ứng | 24.47 / 39.50 |
| receive_to_gui_submit_ms mean / p95 | 72.06 / 97.27 | 63.36 / 84.80 |
| pan_tilt_rpc_latency_ms mean / p95 | 6.84 / 12.89 | 6.64 / 13.09 |

Bản mới có detection khoảng **5.35 FPS**, thấp hơn đầu vào gần 30 FPS; association/GMC thêm khoảng **24.47 ms** trung bình. ReID total trung bình khoảng **324 ms**; chạy bất đồng bộ không đồng nghĩa miễn chi phí CPU/GPU. Đây là các nút thắt cần profile tiếp; stage có thể chồng lấn, không cộng thẳng các mean để suy ra FPS. Receive→GUI submit chưa phải độ trễ cảm biến→màn hình.

## 6. Điều khiển camera và shutdown

Phiên mới có **48 auto-pan dispatch**, **11 zoom dispatch**; tổng **59 START_SENT / 59 START_ACK**, **59 STOP_SENT / 59 STOP_ACK**. Event transport PAN_TILT chứa cả mã zoom, không cộng thêm zoom để tránh trùng. Không thấy lỗi transport/motor trong camera log phiên này. ACK là xác nhận giao thức, không chứng minh motor đã chuyển động/dừng vật lý.

Phiên cũ có **323 auto-pan dispatch**, **34 zoom dispatch**; 357 START_SENT nhưng 356 START_ACK. Lúc **09:54:39.332**, lệnh pan_right gặp `WebSocket connection closed`, START_UNCONFIRMED/HANET_TRANSPORT_ERROR; tiếp theo STOP_UNCONFIRMED và MOTOR_FAULT_LATCHED. Đến **09:54:39.875**, MOTOR_FAULT_RECOVERED với `STOP_RPC_ACK`, connection_generation=2. Nhiều event này thuộc cùng một sự cố, không phải nhiều sự cố độc lập.

Phiên mới có 355 ZOOM_SESSION_MISMATCH: 319 ở control_stop_session_mismatch, 36 ở ptz_observation. Đây là kiểm tra session cũ, không phải 355 RPC lỗi. Cần giữ phân biệt giữa từ chối lệnh/stale theo logic với lỗi kết nối thực tế ở phiên cũ.

GUI đóng phiên mới 10:13:51.042; controls complete 10:13:51.083 (**41 ms**); application stopped 10:13:51.391 (**349 ms**). Backend READY, motor_owner=null, unconfirmed_motor=false, cleanup_failures=[]; physical_motor_stopped=null nên chỉ kết luận shutdown sạch ở mức phần mềm.

## 7. Ưu tiên tiếp theo

1. Đối chiếu video các đoạn HOLD liên tiếp và khóa chỉ 107 ms; kiểm tra bbox chồng nhau, track trùng/tentative, association margin và identity của người thật trước khi sửa overlap gate.
2. Profile association/GMC và batch ReID trong cảnh nhiều người. So sánh cùng video, cùng reference, có nhãn target; đo cả thời gian bám đúng và bám nhầm.
3. Kiểm tra đoạn cuối 10:13:37–10:13:51 để phân biệt target rời khung, bị che, đổi track hay không vượt xác nhận ReID. Không hạ threshold chỉ vì mất khóa.
4. Kiểm tra riêng kết nối HANET ở 09:54:39; không gộp lỗi WebSocket vào đánh giá thuật toán tracking.

## 8. Phạm vi dữ liệu và nguồn

Đã đọc application log, toàn bộ latency trace còn giữ (file chính và .1/.2/.3), CSV summary, camera log cùng rotation hiện có. Latency trace cũ chỉ còn **09:53:05.946–09:54:43.660**; trace mới chỉ còn **10:12:24.067–10:13:51.239**. Vì rotation mất đầu phiên, các thống kê event chi tiết chỉ đại diện đoạn còn giữ; trạng thái dùng application log và hiệu năng dùng aggregate cuối.

- [Application mới](../logs/ptz_20261116_101152.log), [application trước](../logs/ptz_20261116_094907.log).
- [Camera mới](../logs/camera/camera_control_20261116_101155_841439.log), [camera trước](../logs/camera/camera_control_20261116_094911_446316.log).
- [Latency mới](../logs/latency/latency_trace_20261116_101155_841439.jsonl), [CSV mới](../logs/latency/latency_summary_20261116_101155_841439.csv).
- [Latency trước](../logs/latency/latency_trace_20261116_094911_446316.jsonl), [CSV trước](../logs/latency/latency_summary_20261116_094911_446316.csv).
- [Số liệu phân tích](latest_log_review_20261116/analysis.json).

Lượt này chỉ đọc log và viết báo cáo; không chạy main.py, thay logic/config hay gửi lệnh camera.
