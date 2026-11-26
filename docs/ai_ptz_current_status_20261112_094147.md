# Tình trạng AI PTZ sau sửa code — phiên 12/11/2026 lúc 09:41:47

Đối chiếu với phiên **08:36:11 cùng ngày, trước lượt sửa root-cause**. Múi giờ Asia/Ho_Chi_Minh (UTC+7). Lượt kiểm tra này chỉ đọc log/source và viết tài liệu, không sửa code vận hành hoặc điều khiển camera.

## 1. Kết luận

**Tracking thực tế ở phiên mới kém ổn định hơn phiên trước.** Cảm nhận “mất target nhiều hơn” phù hợp số liệu khi chuẩn hóa thời gian: vẫn 8 lần mất khóa hoàn toàn và 17 lần vào OCCLUDED_HOLD, nhưng phiên mới chỉ dài 167,97 s so với 349,63 s trước đó — tần suất tăng khoảng **2,08 lần**.

Các điểm chính:

- Thời gian nằm trong các trạng thái có body target được chọn (`TARGET_LOCKED + PERSON_ZOOM + VERIFIED`) giảm từ **58,76% xuống 18,52%** toàn phiên. Median đoạn liên tục từ lock/relock đến hold giảm **1,957 → 0,523 s**.
- Trong **92,25 s trace chi tiết mới còn giữ**, cả **10/10 lần chuyển vào hold có reason `PERSON_OVERLAP`**, không có reason LOW_REID_SCORE hoặc TRACK_MISSING tại thời điểm chuyển đó. Không suy rộng thành nguyên nhân của cả 17 hold toàn phiên.
- Detection **6,28 → 4,81 FPS**, dù YOLO mean **51,21 → 37,99 ms**. ReID đồng bộ ở hold/reacquire và số candidate lớn hơn đang chiếm nhiều công việc; không có bằng chứng YOLO chậm hơn hay camera mất FPS.
- Recovery HANET đã hoạt động về mặt gửi lệnh: **24 recovery OUT pulses**, không còn counter reject do NaN. Tuy nhiên, recovery sau full loss mean **7,61 → 10,22 s**; sửa được đường điều khiển chưa đồng nghĩa giữ target tốt hơn.
- Pan dispatch **439 → 52**. Tỷ lệ evaluation bị `TARGET_UNCERTAIN` tăng **29,53% → 63,78%**. Camera ít được cấp quyền theo target hơn; log không chứng minh motor bị chậm cơ học.

Hai phiên có độ dài, số candidate và diễn biến cảnh khác nhau. Đây là **so sánh tình trạng trước/sau**, chưa phải A/B cùng video; chưa đủ bằng chứng quy toàn bộ suy giảm cho một thay đổi code.

## 2. Nguồn và giới hạn dữ liệu

| Nguồn | Trước sửa | Sau sửa |
|---|---|---|
| Log ứng dụng | [08:36:11](../logs/ptz_20261112_083611.log) | [09:41:47](../logs/ptz_20261112_094147.log) |
| JSONL ứng dụng | [trước](../logs/ptz_20261112_083611.jsonl) | [sau](../logs/ptz_20261112_094147.jsonl) |
| Latency summary | [trước](../logs/latency/latency_summary_20261112_083616_809086.csv) | [sau](../logs/latency/latency_summary_20261112_094151_229452.csv) |
| Latency trace và `.1/.2/.3` | [trước](../logs/latency/latency_trace_20261112_083616_809086.jsonl) | [sau](../logs/latency/latency_trace_20261112_094151_229452.jsonl) |
| Zoom summary | [trước](../logs/zoom/zoom_summary_20261112_083616_808443.csv) | [sau](../logs/zoom/zoom_summary_20261112_094151_228730.csv) |
| Zoom trace, kèm file xoay | [trước](../logs/zoom/zoom_trace_20261112_083616_808443.jsonl) | [sau](../logs/zoom/zoom_trace_20261112_094151_228730.jsonl) |

- Trước: **08:36:11.105–08:42:00.737**; run `d7f4fce31f7e43709cdd9b97aafe83d9`.
- Sau: **09:41:47.902–09:44:35.870**; run `29a2ef8522f04f5a900d9fd0a9086c50`.
- Latency trace trước chỉ còn **08:40:48.460–08:42:00.576**, 72,12 s cuối; sau chỉ còn **09:43:03.465–09:44:35.716**, 92,25 s cuối. Không dùng số event trong phần trace còn giữ làm tổng toàn phiên.
- Zoom trace sau gồm file gốc và `.1`, còn từ **09:41:51.234**, đủ đối chiếu 24 recovery pulses. Các tổng toàn phiên lấy từ `LATENCY_REPORT` cuối, CSV và application JSONL/log.
- Đối chiếu code với [checkpoint trước sửa](root_cause_fix_20261112/before.zip), [diff](root_cause_fix_20261112/changes.patch), [báo cáo triển khai](root_cause_fix_20261112/report.md). Source hiện tại không phải bằng chứng tuyệt đối về từng byte đã được nạp ở thời điểm chạy; các event mới xác nhận các nhánh recovery/diagnostics/phân pha đã hoạt động trong phiên.
- [Số liệu trích xuất](ai_ptz_current_status_20261112_094147_metrics.json) lưu counters, metrics và transitions phục vụ kiểm tra lại.

## 3. Tracking: so sánh có chuẩn hóa thời gian

| Chỉ số | Trước sửa | Sau sửa |
|---|---:|---:|
| Độ dài app (s) | 349.632 | 167.968 |
| Lock / full loss / full-loss recovery | 8 / 8 / 7 | 8 / 8 / 7 |
| OCCLUDED_HOLD / hold→lock | 17 / 9 | 17 / 9 |
| Full loss / phút | 1.373 | 2.858 |
| Hold / phút | 2.917 | 6.073 |
| Thời gian có target được chọn (s) | 205.431 | 31.105 |
| Tỷ trọng thời gian có target (%) | 58.756 | 18.518 |
| SEARCHING (s) | 104.578 | 92.793 |
| OCCLUDED_HOLD (s) | 39.623 | 44.070 |
| Median lock/relock → hold (s) | 1.957 | 0.523 |
| Face identity confirmation counter | 5 | 0 |
| Recognition observations accepted | 65 | 11 |

Dwell được tích phân từ các dòng `[STATE]`, gồm startup/shutdown; trước transition đầu tiên quy ước SEARCHING. Đây là thời gian trạng thái phần mềm, không phải tỷ lệ người thật còn nhìn thấy hoặc độ chính xác identity. Không đếm riêng `TARGET_LOCKED` vì phần lớn thời gian tracking nằm ở PERSON_ZOOM/VERIFIED. `TRACKED` ID và recognition observation không thay thế ground-truth ID-switch hoặc face confirmation.

Một số lần lock trở lại rất ngắn:

| Lock/relock | Vào hold | Khoảng liên tục | Sau đó |
|---|---|---:|---|
| 09:43:12.544 | 09:43:12.642 | 0,098 s | Full loss 09:43:15.902 |
| 09:43:25.295 | 09:43:25.566 | 0,271 s | Reverify rồi lại hold |
| 09:43:28.636 | 09:43:28.867 | 0,231 s | Full loss sau đợt hold tiếp theo |
| 09:44:07.967 | 09:44:08.234 | 0,267 s | Full loss 09:44:11.499 |
| 09:44:12.028 | 09:44:12.540 | 0,512 s | Full loss 09:44:15.811 |

Ngưỡng overlap giống nhau ở acquisition và continuity, nhưng gate không có hysteresis theo thời gian cho việc vừa reacquire rồi overlap lại ở frame sau. Ba frame confirmation không bảo đảm frame tiếp theo vẫn không overlap. Đây là hành vi chưa giải quyết, đã tồn tại trong baseline, không phải bằng chứng threshold mới bị siết.

## 4. Nguyên nhân mất target: bằng chứng mới

### 4.1. Overlap gate là nguyên nhân trực tiếp của các hold còn trace

10 transition mới có `PERSON_OVERLAP`; similarity được ghi ở các transition khoảng **0,725–0,848**, nhưng giá trị này có thể là evidence gần nhất, không nhất thiết scoring ngay frame chuyển hold. Không nên diễn giải thành “OSNet đã xác minh an toàn mọi bbox hiện tại”.

Gate dùng **intersection / diện tích bbox nhỏ hơn**, threshold **0,15**, không phải IoU=0,15. Ví dụ rõ nhất:

- **09:44:28.973**, frame diagnostics: ID71 `[282,79,335,231]`, confidence 0,910; target ID72 `[324,95,412,391]`, confidence 0,926.
- IoU chỉ **0,04588**, nhưng overlap/min-area **0,18570 > 0,15**.
- **09:44:28.974**: PERSON_ZOOM → OCCLUDED_HOLD, reason PERSON_OVERLAP; **09:44:32.281** full loss.

Như vậy không cần hai bbox gần trùng mới gây hold: một vùng giao nhau nhỏ tương đối cũng đủ. `REID_CROSSING_OVERLAP=0.15`, grace mất target **3 s**, confirmation **3 frames**, ReID reacquire threshold **0,70** đều không đổi so checkpoint. Không tự kết luận overlap này là hai người thật hay false detection khi thiếu ảnh.

### 4.2. Một số cặp chồng mạnh có ngay trước tracker

Diagnostics mới phân biệt được observed detection và bbox Kalman:

| Thời điểm | IDs / source detection index | Observed bbox | IoU / overlap-min |
|---|---|---|---|
| 09:43:21.235 | 58/index4; 55/index3 | `[194,120,251,224]`; `[194,120,251,245]` | 0,832 / 1,0 |
| 09:44:18.045 | 74/index5; 76/index4 | `[159,91,219,197]`; `[159,90,219,216]` | 0,84127 / 1,0 |

Cả hai source indices tồn tại riêng trong detection input và `updates_since_observed=0`. **Ở các mẫu này, không phải chỉ một bbox dự đoán cũ bị giữ lại bên cạnh detection mới**; có hai observation hiện tại từ upstream. Chưa chứng minh chúng cùng một người. Các cặp này cũng không nhất thiết là target đang được chọn ở đúng thời điểm đó.

`PERSON_IOU_THRESHOLD=0.60`, detector source không đổi. Nếu model đang đi qua NMS thông thường cùng class thì cặp IoU 0,84 sau predict là điểm cần điều tra. Tuy nhiên Ultralytics có nhánh end-to-end khác NMS truyền thống; chưa có runtime model mode/raw output để kết luận NMS hỏng. Cần log class/source proposal và model postprocess mode, kèm snapshot đúng frame. Không gộp hai ID chỉ theo IoU.

Sửa alias lần trước chỉ loại **cùng Python dict bị đưa vào hai lần**, không loại hai detection độc lập. Không có counter `repeated_detection_aliases` trong report cuối phiên mới. Vì vậy chưa có bằng chứng bản sửa alias tác động vào triệu chứng thực tế đang gặp.

## 5. Hiệu năng và stale

| Metric | Trước | Sau |
|---|---:|---:|
| Camera receive FPS | 29.93 | 29.87 |
| Detection FPS | 6.28 | 4.81 |
| GUI unique FPS | 9.70 | 11.99 |
| Detection frames | 2158.00 | 791.00 |
| Stale aggregate | 408.00 | 389.00 |
| Periodic verification count | 186.00 | 24.00 |
| YOLO mean/P95 ms | 51.21 / 85.75 | 37.99 / 67.00 |
| ReID mean/P95 ms | 191.01 / 436.24 | 203.64 / 295.39 |
| ReID selection mean/P95 ms | 136.74 / 186.21 | 197.53 / 272.42 |
| Recovery mean/P95 ms | 7610.01 / 15082.05 | 10218.71 / 27330.87 |

YOLO nhanh hơn theo wall-time đo được, nhưng detection loop giảm FPS. Bằng chứng tải ReID:

- Candidate mỗi scoring trong trace còn giữ: **mean 1,79 → 4,85**, hai cửa sổ khác nhau. Phiên mới 274/348 `REID_CANDIDATES` có 5 candidates.
- 347 workload records mới có **1.683 crops**, **1.678 normalization variants** (~99,7% crops chạy thêm normalized pass). Với batch limit 4, 5 candidates thường cần hai raw batches và thêm normalized batches. Normalization không mới được bật trong lượt sửa.
- Toàn phiên, số batch ReID inference đo được **1.703 → 2.018** dù phiên sau ngắn hơn nhiều. Mean từng batch **56,39 → 36,65 ms**; mean một lần ReID tổng vẫn **191,01 → 203,64 ms**. Không cộng các resource spans lồng nhau để suy utilization GPU.
- ReID phase mới: OCCLUDED_HOLD **180 lần, mean 198,42 ms**; REACQUISITION **301 lần, mean 191,90 ms**; periodic locked **21 completed, mean 358,50 ms/P95 684,66 ms**; searching 41 và acquiring 2. Các phase completed cộng 545; crop attempts 548, nên không coi ba attempt không hoàn tất là batch hoàn tất.
- Optimization “mọi candidate đều overlap thì bỏ ReID” chỉ kích hoạt **3 lần**. Khi target overlap nhưng người khác trong frame không overlap, điều kiện “mọi candidate” không đúng: vẫn score cả nhóm rồi target tốt nhất bị gate từ chối. Vì thế kết quả synthetic 160→0 calls trước đây không đại diện cảnh nhiều người lần này.

Đây là bằng chứng cho vòng lặp bất lợi: overlap → hold/search → nhiều ReID đồng bộ trên nhiều crop → ít update tracking hơn → khó phục hồi liên tục. Ảnh hưởng nhân quả của từng bước cần controlled replay; chưa kết luận GPU yếu, RTSP hỏng hoặc diagnostic overhead là nguyên nhân chính. ReID P95 giảm nhưng mean và tracking continuity xấu đi, nên chỉ nhìn P95 sẽ đánh giá sai tình trạng tổng thể.

### Stale không đồng nghĩa frame inference quá cũ

Phiên mới aggregate **389** gồm **387 `stale_reason_ZOOM_SESSION_MISMATCH` + 2 `stale_reason_TRACKING_RESET`**. Các counters riêng khác: superseded 15, admission validity rejected 3, stale ReID 4; không cộng tất cả rồi gọi là unique stale frames. Ratio aggregate/detection tăng 18,91%→49,18% chỉ là tỷ số counters.

Source giải thích vì sao ZOOM_SESSION_MISMATCH có thể tăng nhiều: khi crossing hold, `target_session.publish(None)`; mỗi update hold gọi `request_stop(IDENTITY_UNCERTAIN)`. Dispatcher giữ correlation cũ cho control-only STOP, rồi kiểm tra session trước khi xử lý STOP. Một STOP lặp trong lúc hold có thể đi qua nhánh “stale session” và tăng counter dù không có inference frame trễ. Counter không tách STOP/control observation, nên chưa phân bổ được chính xác 387 lượt thuộc từng loại. Việc thêm nhãn lần này phơi bày nhánh đếm đã tồn tại, không chứng minh phát sinh 387 stale inference mới.

## 6. Recovery và pan/tilt sau sửa

### Recovery đã được phát lệnh nhưng chưa giúp duy trì target

- Tổng zoom **35 → 52**, hướng IN/OUT **8/27 → 4/48**; summary RPC errors đều **0**.
- Trong 52 lệnh mới: **24 pulse PULSE_VISUAL recovery**, còn **28 lệnh zoom khác**. Recovery budget logs có `lens_position=null`, mỗi pulse yêu cầu 300 ms, tích lũy tối đa **1,2 s mỗi đợt**.
- 7 nhóm budget trong zoom trace: 1 pulse trước lock đầu; 5 nhóm đủ 4 pulses; nhóm cuối 3 pulses trước shutdown. Tổng requested duration **7,2 s**, không phải thời gian lens thực quay.
- Không có reject `RECOVERY_INVALID_ZOOM_ESTIMATE` ở counter cuối mới, so với **381** trước. Đây là phần sửa đã được xác nhận có tác dụng thực tế ở tầng lệnh.
- Ba STOP `RECOVERY_WIDE_LIMIT` tại khoảng **09:43:19.869, 09:43:38.275, 09:44:19.799**. Source dùng chung tên STOP này khi budget đạt giới hạn; **không có telemetry để kết luận đã đến góc rộng vật lý**. Budget được cộng khi START, nên observation tiếp theo có thể dừng pulse cuối sớm khi spent đã đủ; tên STOP đang gây hiểu nhầm.
- Sau full loss **09:43:34.724**, phải đến **09:44:07.967** mới lock lại (~33,24 s). Recovery có phát OUT trong khoảng đó nhưng không bảo đảm reacquire nhanh.

Recovery OUT có thể thay đổi kích thước/cảnh và số người nhìn thấy; đây là thay đổi hành vi vật lý đáng kiểm tra A/B, chưa chứng minh là nguyên nhân làm mất target nhiều hơn. Bật recovery mới giải quyết lỗi chặn NaN; không giải quyết false/ambiguous detections hoặc overlap gate.

### Pan ít cơ hội được điều khiển

- Toàn phiên: **439 → 52 dispatch**, tương ứng **75,34 → 18,58 dispatch/phút** theo thời gian app.
- `TARGET_UNCERTAIN` chiếm **29,53% → 63,78% evaluations**; không phải tỷ lệ thời gian. Chưa đủ lock liên tục để tạo các chuỗi pulse hữu ích.
- Trace sau còn **17 dispatch, 17 ACK=True**, **13 speed1 + 4 speed2**, đều **120 ms**. Trace trước còn 46 dispatch đều speed1/120 ms. Như vậy speed không bị cố định ở 1 trong phiên mới.
- Inter-pulse wait từ các dispatch còn trace: mean **800,93 → 5.329,74 ms**, max **15,48 → 36,52 s**. Bao gồm khoảng mất target/searching; không gọi đây là cooldown hoặc network latency.
- Adaptive vẫn OFF trong config; max speed3, pulse120, settle150, deadzone0,08/release0,04 không đổi so checkpoint. Việc tách pure policy không tự bật tăng tốc thực tế.

ACK không chứng minh motor di chuyển đúng hướng/tốc độ. Chưa có video đồng bộ hoặc calibration để kết luận phần cứng pan chậm hơn.

## 7. Đối chiếu chính xác những gì đã sửa so bản cũ

| Nhóm thay đổi | Bản trước checkpoint | Bản sau / bằng chứng phiên mới | Đánh giá hiện tại |
|---|---|---|---|
| Recovery không telemetry | NaN bị từ chối, no-feedback khóa lặp recovery | Cho bounded pulse, 24 recovery OUT thực sự ghi nhận | Sửa được đường lệnh; chưa cải thiện tracking |
| Reset recovery budget | Reset sớm trước kiểm tra target hợp lệ | Chuyển sau fresh valid target; có budget event | Không thấy budget vượt 1,2 s mỗi nhóm ghi nhận; chưa biết displacement |
| ByteTrack adapter | Mutate input dict; không chặn alias | Copy output, loại same-object alias | Chưa có bằng chứng alias xuất hiện trong phiên này; không xử lý hai detections riêng |
| Overlap diagnostics | Chủ yếu evidence sau tracker | 119 tracker diagnostic events còn trace; pre/post/index/lifecycle; 97 target overlap events | Hữu ích: xác nhận nhiều bbox chồng đã có từ upstream |
| Bỏ ReID khi mọi candidate overlap | Vẫn scoring trước gate | Kích hoạt 3 lần | Phạm vi tối ưu quá hẹp cho cảnh target overlap + nhiều người khác |
| Periodic ReID admission | Job chờ có thể vào GPU dù đã vô hiệu | Check epoch/age trước raw/normalized admission; có discard/skipped events | Safety/work avoidance; không xử lý tracking hold liên tục |
| ReID instrumentation | Tổng thời gian khó phân loại | Có phase/crop/batch/normalization | Là đo đạc, không phải giảm batch hay giảm model cost tự động |
| Stale reason | Chủ yếu aggregate | Thấy 387 zoom-session, 2 reset | Counter cũ không còn được diễn giải là 408 unique frames |
| Pan policy | Logic direction/pulse trong controller | Tách pure function + simulator; runtime vẫn baseline | Không có thay đổi max speed/pulse mặc định |
| Calibration/tests | Chưa có các bộ mới | Thêm tool dry-run/execute và replay | Không có event cho thấy tự chạy calibration trong phiên này |

**Không thay:** detector/NMS source, YOLO/OSNet/SCRFD/ArcFace, input resolution, ReID thresholds, overlap threshold0,15, confirmation3, grace3s, normalization ON, batch limit4, pan max speed3/adaptive OFF. Diff `config.py` so checkpoint chỉ thêm pulse recovery và tracker diagnostics/snapshot options. Snapshot vẫn OFF, không có ảnh mới đủ để xác nhận một hay hai người.

Vì vậy không nên ghi “đã sửa duplicate tracking ngoài thực tế” hoặc “đã tăng tốc pan”. Báo cáo triển khai trước đó chỉ chứng minh các tests/simulator đã chạy; phiên live này cho thấy mục tiêu giữ target liên tục **chưa đạt**.

## 8. Việc nên ưu tiên tiếp theo

1. **Điều tra overlap với ảnh đúng thời điểm.** Bật snapshot có giới hạn cho một phiên ngắn, ghi model postprocess/end-to-end mode, class/source index và pre-NMS nếu có. Ưu tiên hai loại: bbox gần trùng IoU>0,8 và overlap nhỏ vẫn gây hold như 09:44:28.973. Không xóa track hàng loạt hay nâng threshold chỉ để giảm số hold.
2. **Xem lại policy hold/reacquire theo thời gian và identity evidence.** Trường hợp target vừa reverify rồi hold sau 0,1–0,5 s cần bài replay riêng với bbox/candidate sequence thực, giữ nguyên bảo vệ tránh theo nhầm. Threshold0,15 hiện là gate rất nhạy; phải đo false hold và identity switch trước khi thay.
3. **Giảm công việc scoring không hữu ích trong hold.** Hiện score 4–6 người và normalized pass gần mọi crop, rồi target vẫn không thể được chọn do overlap. Cần kế hoạch verification theo candidate liên quan và freshness, có test distractor; không hạ ReID threshold để “khóa cho nhanh”.
4. **Đối soát ảnh hưởng recovery mới bằng một biến thay đổi.** Cùng cảnh/đường đi/zoom ban đầu, so `PULSE_RECOVERY_WITHOUT_TELEMETRY=True/False`, còn lại giữ nguyên; ghi video. OFF là đối chứng diagnostic, không phải khẳng định rollback là cách sửa cuối cùng. Tách STOP budget exhausted khỏi tên wide limit và kiểm tra pulse cuối bị cắt sớm.
5. **Tách stale control STOP khỏi stale inference frame** và thống kê unique frame/job/session để tránh tối ưu sai nguyên nhân. Đồng thời giữ đầy đủ trace ngay sau phiên, vì rotation đã mất đoạn đầu cả hai phiên.

Không tự áp dụng các đề xuất trên trong lượt kiểm tra này. Kết luận hiện tại: **recovery lỗi NaN đã được khắc phục ở đường lệnh, nhưng overlap gây mất quyền tracking và tải ReID trong hold/reacquisition vẫn là vấn đề chính thể hiện trong log mới; độ ổn định thực tế đang giảm.**
