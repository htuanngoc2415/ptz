ch# Tình trạng AI PTZ — phiên 20261112_125526

Kiểm tra ngày 12/11/2026, giờ UTC+7. Đối chiếu phiên 09:41:47 trước bản P0 và phiên 08:36:11 trước lượt sửa root-cause. Lượt này đọc log/source và viết báo cáo; không sửa code vận hành, không gửi lệnh camera.

## 1. Kết luận

**Tracking có cải thiện so với 09:41, nhưng hệ thống chưa duy trì target liên tục. Lỗi nổi bật mới trong phiên này là đường pan/tilt bị lỗi rồi STOP không được xác nhận, khiến cả pan và zoom bị chặn.**

- Thời gian có body target được chọn tăng **18,52% → 31,21%**; median đoạn lock/relock đến hold tăng **0,523 → 1,612 giây**. Tuy nhiên vẫn có **16 lần OCCLUDED_HOLD, 6 lần mất khóa hoàn toàn**.
- ReID total mean giảm **203,64 → 139,67 ms**, detection trong khoảng hoạt động đạt khoảng **9,14 FPS**, so với **4,81 FPS** trước P0. Đây là các phiên khác cảnh, không phải A/B cùng video.
- **12:57:30.241:** xuất hiện `PAN_TILT_WIRE_ERROR` ở lệnh cuối. Sau đó auto-pan bị vô hiệu hóa, STOP lặp lại không được xác nhận, quyền motor vẫn thuộc `pan_tilt`. **267 lần zoom bị `OWNER_pan_tilt` sau mốc này**.
- **12:58:49:** chuỗi tắt controller/đóng luồng xuất hiện; không còn hình mới. Worker STOP vẫn chạy đến **13:05:08**, khi ứng dụng kết thúc bằng `KeyboardInterrupt`. Không nên tính khoảng chờ đóng này thành thời gian AI tracking.
- Bản P0 đã thực sự được sử dụng: có **78 evaluation `WEAK_OVERLAP_CONTINUITY`**, kiểm soát tải ReID và stale theo nhóm. Tuy nhiên overlap và recovery chưa được giải quyết hoàn toàn.

**Ưu tiên tiếp theo là điều tra lỗi HANET START/STOP và quá trình shutdown; đồng thời tiếp tục kiểm chứng overlap bằng hình đồng bộ.** Tăng tốc pan hoặc nới gate không giải quyết được trạng thái STOP không được xác nhận.

## 2. Nguồn và cách tính

- [Log ứng dụng](../logs/ptz_20261112_125526.log), [JSONL ứng dụng](../logs/ptz_20261112_125526.jsonl).
- [Latency summary](../logs/latency/latency_summary_20261112_125530_057267.csv), [JSONL trace](../logs/latency/latency_trace_20261112_125530_057267.jsonl) cùng `.1/.2/.3`.
- [Readable trace](../logs/latency/latency_readable_20261112_125530_057267.log) cùng `.1`: còn lịch sử tên event từ đầu phiên, nhưng không chứa đầy đủ payload.
- [Camera control log](../logs/camera/camera_control_20261112_125530_057267.log) cùng `.1`; [zoom summary](../logs/zoom/zoom_summary_20261112_125530_056934.csv), [zoom trace](../logs/zoom/zoom_trace_20261112_125530_056934.jsonl) cùng `.1/.2`.
- [Số liệu trích xuất và SHA-256 nguồn](ai_ptz_current_status_20261112_125526_metrics.json).
- [Báo cáo 09:41](ai_ptz_current_status_20261112_094147.md), [báo cáo P0](target_tracking_p0_fix_20261112.md).

Run ID: `b993569acbdc4a95bd911c73cb63bbd9`.

| Phạm vi | Thời gian |
|---|---|
| Vòng đời ứng dụng | 12:55:26.447–13:05:08.724, **582,277 s** |
| Cửa sổ so sánh tracking | 12:55:26.447–12:58:49.035, **202,588 s** |
| Frame cuối | 12:58:49.021, frame 5952 |
| GUI submit cuối | 12:58:49.024 |
| JSONL latency còn giữ | 12:57:49.176–13:05:08.564 |
| Phần JSONL còn hình đang chạy | Chỉ khoảng **59,85 s cuối** |
| Zoom trace | 12:55:30.060–12:58:49.092 |

Các số đếm tổng lấy từ counter tích lũy, CSV và app log. Nguyên nhân chuyển trạng thái chi tiết chỉ kết luận cho đoạn JSONL còn giữ. Readable trace giúp xác định thời điểm lỗi đầu tiên dù payload JSONL đã xoay mất.

**FPS phải loại khoảng chờ đóng:** từ 12:59 trở đi, `received_frames=5952` và `detection_frames=1818` không tăng nhưng báo cáo tiếp tục phát. Đến 13:05, FPS tổng chỉ còn 10,43/3,18; đây không phải tốc độ camera/detection trong lúc chạy. Ước tính đúng phạm vi profiler đến frame cuối: camera **29,91**, detection **9,14**, GUI **11,29 FPS**. Kiểm tra chéo báo cáo 12:58:30: camera **29,90**, detection **9,25 FPS**.

## 3. So sánh tracking và hiệu năng

| Chỉ số | 08:36 trước sửa root-cause | 09:41 trước P0 | 12:55 sau P0 |
|---|---:|---:|---:|
| Cửa sổ so sánh, giây | 349,63 | 167,97 | 202,59 |
| Thời gian body target được chọn | 205,43 s | 31,11 s | **63,23 s** |
| Tỷ lệ có target được chọn | 58,76% | 18,52% | **31,21%** |
| Vào OCCLUDED_HOLD | 17 | 17 | **16** |
| Hold/phút | 2,92 | 6,07 | **4,74** |
| Mất khóa hoàn toàn | 8 | 8 | **6** |
| Mất khóa/phút | 1,37 | 2,86 | **1,78** |
| Recovery hoàn tất sau full loss | 7 | 7 | **5** |
| Median lock/relock → hold | 1,957 s | 0,523 s | **1,612 s** |
| Recovery mean / p95 | 7,61 / 15,08 s | 10,22 / 27,33 s | **14,34 / 33,37 s** |
| Detection FPS | 6,28 | 4,81 | **~9,14** |
| YOLO total mean | 51,21 ms | 37,99 ms | **38,29 ms** |
| ReID total mean / p95 | 191,01 / 436,25 ms | 203,64 / 295,39 ms | **139,67 / 313,89 ms** |
| ReID selection mean | 136,74 ms | 197,53 ms | **81,33 ms** |
| Pan dispatch | 439 | 52 | **61** |
| Zoom START | 35 | 52 | **12** |

So với 09:41, mất khóa/phút giảm khoảng **37,8%**, hold/phút giảm **22,0%**. Tuy nhiên tỷ lệ giữ target vẫn thấp hơn 08:36, recovery mean tăng, ReID p95 không giảm dù mean giảm.

Dwell trong cửa sổ tracking mới: `SEARCHING` **105,055 s**, `OCCLUDED_HOLD` **34,303 s**, `TARGET_LOCKED` **2,187 s**, `PERSON_ZOOM` **61,043 s**. Không có dwell `VERIFIED` trong app state. “Target được chọn” là trạng thái body tracking, không phải tỷ lệ bám đúng người theo ground truth.

Ngoài 6 full loss có một lần `PERSON_ZOOM → SEARCHING` lúc **12:55:58.036**, sát toggle auto-zoom và `TRIAL RESET`; không cộng lần reset này thành full loss. Có 7 lock từ SEARCHING và 10 lần trở lại lock từ HOLD.

Hai khoảng recovery dài cần lưu ý:

- **12:56:55.445 → 12:57:30.239: 34,794 s**, xảy ra trước lỗi pan cuối; không quy khoảng này cho lỗi motor xuất hiện sau đó.
- **12:57:37.816 → 12:58:05.484: 27,668 s**, xảy ra khi motor đã bị chặn.

Đoạn giữ target dài nhất kết thúc bằng hold là **12:58:14.327–12:58:36.700: 22,373 s**. Điều này chứng minh selector có thể giữ target dài hơn trước ngay cả khi pan đã vô hiệu hóa; không chứng minh camera đang tự quay theo.

## 4. Lỗi điều khiển chính: pan lỗi, STOP retry không thoát

### Chuỗi bằng chứng

1. Readable trace ghi **61 PAN_TILT_DISPATCH** toàn phiên. Dispatch cuối ở **12:57:30.241**, frame **3583**, command `pan_99fa1b9cae224762a182f7fa68c90d30`.
2. Cùng thời điểm có `PAN_TILT_WIRE_ERROR`. Payload lỗi đầu tiên đã mất do rotation; chỉ còn tên event. CSV còn **61 sample pan RPC thành công** nhưng metric này có thể gồm lệnh thủ công; không được suy thành 61/61 auto-pan thành công.
3. Có `PAN_TILT_STOP` lúc **12:57:30.245**, sau đó STOP liên tiếp. Toàn readable trace có **1.804 STOP event từ mốc này**; do thiếu payload đầu đoạn, không coi toàn bộ 1.804 là ACK=False đã được kiểm chứng.
4. Riêng JSONL còn giữ xác nhận **1.731/1.731 PAN_TILT_STOP** là `STOP_RETRY`, `acknowledged=false`, `rpc_result="HANET STOP unconfirmed"`, từ **12:57:49.264 đến 13:05:08.564**.
5. Toàn bộ **106 PAN_TILT_DECISION** còn trong JSONL đều có `AUTO_PAN_DISABLED`, `auto_pan_enabled=false`, `motor_owner=pan_tilt`.
6. Counter toàn phiên: `AUTO_PAN_DISABLED=804/1818` evaluations (**44,22%**), `TARGET_UNCERTAIN=781/1818` (**42,96%**). Việc camera không theo target ở cuối phiên có cả nguyên nhân điều khiển bị vô hiệu hóa, không chỉ target không chắc chắn.

### Source giải thích cơ chế

- [HANET controller](../camera/hanet_controller.py): `command()` lưu `_active_codes` trước START; khi RPC lỗi, gọi `stop()`. Nếu STOP tiếp tục lỗi, active code còn giữ và `_motor_fault=True`.
- [Auto pan/tilt](../control/auto_pan_tilt.py): `_stop()` thất bại đặt `_fault=True`, `enabled=False`, giữ quyền motor. `_run()` thử STOP mỗi khoảng 250 ms cho đến khi thành công.
- [Orchestrator](../control/orchestrator.py): khi owner còn `pan_tilt`, zoom không acquire được.
- [HANET RPC](../camera/hanet_rpc.py): `_connect_websocket()` chỉ mở socket nếu `_ws is None`; nhánh lỗi gửi/nhận không tự xóa socket. Đây là **khả năng khiến retry trên kết nối hỏng không hồi phục**, cần kiểm thử có lỗi giả lập. Log hiện tại chưa xác định được lỗi đầu tiên là đóng socket, session, ACK hay camera từ chối.
- `HanetController.stop()` bắt exception nhưng chỉ trả thông báo chung, không ghi loại lỗi/code STOP. Vì vậy không thể kết luận chắc nguyên nhân ở tầng mạng/phần cứng từ log này.

Giữ quyền motor khi STOP chưa được xác nhận là hành vi bảo vệ có chủ đích. Điểm cần khắc phục là khả năng phục hồi STOP, quan sát lỗi và kết thúc worker có kiểm soát; không nên đơn giản bỏ khóa rồi cho gửi START mới.

## 5. Zoom: nhiều kế hoạch nhưng ít lệnh được gửi

- Có **12 ZOOM_START**: **6 IN + 6 OUT**, gồm **4 pulse recovery OUT** và **8 lệnh zoom khác**. Zoom summary báo **0 RPC error**, nhưng số này không bao phủ lỗi pan/STOP nêu trên.
- **270 ZOOM_COMMAND_DEFERRED**: **268 `OWNER_pan_tilt` + 2 `LENS_SETTLING`**. Trong đó **267 `OWNER_pan_tilt` xảy ra sau lỗi lúc 12:57:30**.
- Zoom START cuối ở **12:56:38.502**. Các dòng app sau đó dạng `command=ZOOM_OUT pulse=250ms` là quyết định/kế hoạch; chúng không chứng minh lệnh được gửi ra camera.
- Tổng 4 recovery pulse yêu cầu 1,2 s, phân bổ qua các đợt recovery; không phải thời gian motor thực chuyển động. `lens_position=null` nên không xác nhận được vị trí zoom vật lý.
- STOP zoom: 8 `PULSE_COMPLETE`, 3 `HOLD_ZONE`, 1 `RECOVERY_WIDE_LIMIT`. Reason cuối vẫn có thể do hết budget chứ không chứng minh lens đã về wide-limit.

## 6. Hiệu quả và giới hạn của bản P0

### Overlap

Counter toàn phiên xác nhận policy mới đang hoạt động:

| Counter | Số evaluation |
|---|---:|
| `WEAK_OVERLAP_CONTINUITY` | **78** |
| `IDENTITY_THREATENING_OVERLAP` | 17 |
| `OVERLAP_WITHOUT_TRUSTED_CONTINUITY` | 321 |
| `WEAK_OVERLAP_UNVERIFIED_TIMEOUT` | 1 |
| `reid_hold_geometry_throttled` | 8 |
| `reid_skipped_all_candidates_overlap` | **723** |

Các counter này không phải số lần chuyển hold. Đặc biệt 78 weak-continuity không đồng nghĩa tránh được 78 lần mất target. `TARGET_OVERLAP_EVIDENCE` ghi hình học trước policy, không được đếm như một quyết định hold thực tế.

Trong JSONL còn giữ, **4/4 transition vào HOLD đều `PERSON_OVERLAP`**:

| Thời điểm | Track | ReID similarity tại transition |
|---|---:|---:|
| 12:58:05.807 | 22 | 0,709 |
| 12:58:08.761 | 22 | 0,795 |
| 12:58:36.700 | 26 | 0,734 |
| 12:58:39.981 | 26 | 0,699 |

Không suy rộng nguyên nhân này cho cả 16 hold. Evidence gần các mốc còn có overlap IoU nhỏ; chưa đủ payload policy theo từng frame để phân biệt chính xác trusted-continuity, timeout hay điều kiện khác cho mỗi transition.

Vẫn có box gần trùng: **12:58:17.478**, track **27/28**, observed bbox `[273,78,340,169]` và `[273,79,340,166]`, confidence **0,505/0,438**, IoU **0,956**, overlap/min-area **1,0**, cả hai `updates_since_observed=0`. Đây là hai detection hiện tại, không chỉ box Kalman cũ. Khi đó target là track 26; không quy cặp 27/28 trực tiếp thành mất target 26.

Không thấy snapshot trong thư mục mặc định `logs/overlap_snapshots`; config snapshot mặc định OFF. Không có hình tại sự kiện để xác nhận một người bị phát hiện hai lần hay hai người che nhau. Không kết luận ID switch hoặc false hold chỉ từ bbox.

### ReID

Toàn phiên có **455 ReID total hoàn tất**; periodic verification **61 lượt khởi tạo / 59 hoàn tất**. Mean theo phase:

| Phase | Số hoàn tất | Mean |
|---|---:|---:|
| SEARCHING | 56 | 123,29 ms |
| ACQUIRING | 4 | 161,59 ms |
| PERIODIC_VERIFY | 59 | 301,84 ms |
| OCCLUDED_HOLD | 62 | 122,89 ms |
| REACQUISITION | 274 | 111,33 ms |

Trong đoạn trace còn giữ có **170 workload**, tổng **324 crops**:

- Periodic: **24 workload / 28 candidates**, trung bình **1,17 candidate/lượt** — phù hợp candidate-aware mới.
- Hold: 19 workload / 37 candidates; reacquisition: 127 / 259. Global recovery vẫn hoạt động.
- **324/324 crops được normalization**, `dark_crop_count=324`, `low_contrast_crop_count=0`. Chi phí nhánh raw + normalized vẫn tồn tại; không có bằng chứng normalization đã được tối ưu bỏ đi.
- Có **2 REID_ERROR / REID_PHASE_ERROR** ở periodic lúc 12:58:08.809 và 12:58:36.700. Chúng trùng/gần chuyển HOLD và GPU admission bị kết thúc rất nhanh; source cho phép hủy verification hết hiệu lực. Không có exception payload để gọi đây là lỗi model/CUDA.

Candidate và cảnh mới khác phiên 09:41. Kết quả phù hợp mục tiêu giảm công việc ReID, nhưng không thể quy toàn bộ mức giảm thời gian cho code P0.

### Stale và nhận diện mặt

- Stale inference mới **4**, tracking-reset cancellation **4**, control STOP session mismatch **542**, PTZ observation stale **57**, stale ReID **7**. `ZOOM_SESSION_MISMATCH=599` gồm 542 + 57.
- Không so trực tiếp `stale_inference_results=4` mới với 389 cũ để tuyên bố giảm 99%: P0 đã đổi cách phân nhóm counter.
- Có **31 recognition observations được pipeline chấp nhận**, nhưng **31/31 `confirmed=false`**; 30 unknown và 1 observation có tên chưa xác nhận. **Chưa có danh tính khuôn mặt được xác nhận cuối cùng.**
- Có **180 FACE_ASSOCIATION_AMBIGUOUS** trong 187 rejection. `TARGET_CONFIRMED` của body ReID không đồng nghĩa face identity confirmed.

Theo [checkpoint P0](target_tracking_p0_20261112/motor_unchanged.json), các file HANET, auto-pan, pan/zoom policy và orchestrator không đổi trong lượt P0; `auto_zoom` chỉ thay phân nhóm counter. Không đủ cơ sở gọi lỗi motor mới quan sát là hồi quy do sửa overlap.

## 7. Đóng ứng dụng bị kéo dài

- **12:58:49.035:** `PAN_TILT_TOGGLE=false` từ MainThread và manual stop request.
- **12:58:49.036:** `ZOOM_TOGGLE=false`.
- **12:58:49.065:** `RTSP_DISCONNECTED`; không có frame sau đó.
- Các bước trên phù hợp đường `on_close()` trong [GUI](../gui/cctv_app.py). [OpenCV receiver](../camera/opencv_player.py) gọi disconnect callback cả khi dừng chủ động; vì vậy event này **không tự chứng minh mạng RTSP hỏng**.
- Worker auto-pan tiếp tục STOP retry đến 13:05:08.564; app ghi `KeyboardInterrupt` lúc **13:05:08.723** rồi `AI PTZ Camera stopped`.

Source có đường chờ không giới hạn: `on_close()` gọi `auto_pan_tilt.close(timeout=None)`; trong `_run()`, kiểm tra `_closed` nằm trong nhánh `not _fault`. Khi `_fault=True` và STOP không bao giờ được xác nhận, thread tiếp tục retry dù đã yêu cầu close. Đây là giải thích từ source phù hợp với khoảng chờ **~379,7 s** trong log; không có thread dump để xác nhận mọi worker đang chờ ở đâu.

Dòng “Camera stopped” sau KeyboardInterrupt không phải bằng chứng ACK STOP của camera đã thành công.

## 8. Thứ tự xử lý đề xuất

1. **HANET STOP/recovery:** ghi exception type, command code và trạng thái transport cho START/STOP; kiểm thử socket đóng, ACK mất, session hết hạn và STOP retry. Thiết kế phục hồi transport để xác nhận STOP mà không tự phát lại START.
2. **Shutdown:** kiểm thử đóng ứng dụng khi `_fault=True`; có thời hạn chờ, báo rõ STOP chưa xác nhận, tránh vòng join vô hạn.
3. **Tracking:** thu snapshot hoặc replay video đồng bộ cho weak overlap kéo dài và cặp detection IoU cao. Ghi policy reason theo frame để nối được geometry → weak grace → verify → HOLD.
4. **Đo lại sau khi motor ổn định:** so sánh trên cùng video/kịch bản, tách tracking availability, đúng danh tính và camera movement. Giữ riêng FPS hoạt động với khoảng disconnected/shutdown.

Bản P0 cho thấy cải thiện ở selector và tải tính toán. Phiên này vẫn chưa đạt tracking liên tục, và có lỗi điều khiển/shutdown cần ưu tiên trước khi đánh giá tốc độ quay thực tế.
