# AI PTZ — điều tra nguyên nhân, sửa lỗi và kiểm thử độc lập, 12/11/2026

Đã sửa source trong project hiện tại và tạo checkpoint trước sửa: [before.zip](before.zip). **Không kết nối RTSP, không gửi lệnh camera thật, không chạy hiệu chuẩn phần cứng.** Tham chiếu tình trạng thực tế: [báo cáo phiên 08:36](../ai_ptz_current_status_20261112_083611.md). Các kết quả mới bên dưới là kiểm thử logic, mock và mô phỏng; chưa chứng minh tăng FPS hay tốc độ motor ngoài thực tế.

## A. Root Cause Analysis

| Vấn đề | Phân loại / nguyên nhân | Bằng chứng, file/hàm | Trạng thái |
|---|---|---|---|
| HANET recovery bị từ chối | **Confirmed:** thiếu lens telemetry → NaN bị kiểm tra như absolute position | 381 `RECOVERY_INVALID_ZOOM_ESTIMATE`; `camera/hanet_controller.py:telemetry`, GUI `get_zoom_level`, `control/auto_zoom.py:_recover_missing` | Thêm chiến lược pulse không cần vị trí lens; giữ telemetry strategy |
| Không tiếp tục recovery sau một xung | **Confirmed:** finalization khóa recovery khi không có telemetry | `control/auto_zoom.py`, nhánh sau STOP; test recovery trước đây chỉ cho một xung | Với pulse strategy, cho phép xung tiếp theo sau settle và observation mới, trong budget |
| Reset budget bởi update không có target | **Confirmed ở source:** reset nằm trước kiểm tra target hợp lệ | Diff `_update`; có thể làm mất ý nghĩa tổng budget nếu caller gửi update missing | Chuyển reset sau kiểm tra geometry/freshness/session; missing không reset |
| Detection cùng object xuất hiện hai lần | **Confirmed bằng dữ liệu dựng sẵn**, chưa xác nhận xảy ra trong log thật | ByteTrack adapter trước sửa sửa trực tiếp dict đầu vào; replay alias thất bại acquisition | Chỉ khử alias cùng Python object; copy output, không sửa detection gốc |
| IDs 58/62 IoU 0,965 | **Unverified:** duplicate YOLO, association/lifecycle, hai người overlap hoặc camera motion | Log có bbox sau tracker, không có frame đồng bộ/pre-tracker để phân biệt | Chưa tuyên bố sửa nguyên nhân thực tế; giữ hai detection độc lập và thêm diagnostics |
| ReID làm việc dù mọi candidate không qua overlap gate | **Confirmed bằng source và replay A/B** | `HybridReIDTargetSelector.select_person`; scoring trước gate không thể cấp quyền chọn target | Bỏ scoring nếu tất cả candidate đủ confidence đều bị overlap gate từ chối |
| Background ReID quá hạn vẫn chờ rồi dùng GPU | **Confirmed ở đường admission trước sửa** | `_start_verification`, `ReIDTargetSelector._scores`, `InferenceScheduler.use` | Kiểm tra epoch/tuổi job trước raw và normalized GPU admission |
| ReID 191 ms, detection 6,28 FPS | **Probable:** scoring đồng bộ acquisition/recovery, normalization thêm batch và chia sẻ accelerator ảnh hưởng throughput | Log lịch sử; OSNet preprocess, gallery CPU transfer, scheduler source | Thêm phân pha/timing; chưa có profile sau sửa để quy trách nhiệm từng phần hoặc kết luận GPU yếu |
| 408 stale | **Confirmed:** counter tăng ở nhiều nhánh; không phải số frame duy nhất | `inference_worker`, `face_worker`, zoom dispatch | Thêm counters theo lý do; không suy ngược phân bố 408 từ log cũ |
| Pan có ACK nhưng chậm | **Confirmed cấu hình:** speed theo error, pulse cố định 120 ms; **unverified tốc độ cơ học** | 46 lệnh còn trace, `auto_pan_tilt`, `hanet_controller` | Tách pure policy, thêm simulator và công cụ hiệu chuẩn; adaptive vẫn OFF mặc định |

### Audit detection → tracker → selector

`ai/detector.py` gọi `model.predict(conf=..., iou=..., imgsz=...)`, nhận `results.boxes`, lọc class/geometry rồi tạo dict riêng cho từng detection. Không tìm thấy bước cố ý nhân bản detection. Ultralytics cài tại máy là 8.4.25: detect predictor gọi `non_max_suppression`, có nhánh `end2end` tùy model. Chưa có tensor trước NMS hoặc bằng chứng model mode ở thời điểm overlap để khẳng định NMS lỗi; không đổi threshold/NMS theo suy đoán.

Adapter giữ detection index qua cả high/low partitions. ByteTrack dùng association high-score, low-score, unconfirmed và lifecycle tracked/lost/removed. Hàm `remove_duplicate_stracks` của thư viện so tracked với lost; đây không phải bảo đảm mọi cặp active track luôn khác biệt hình học. Output adapter dùng **bbox observed từ detection**, còn row ByteTrack là bbox posterior đã cập nhật Kalman. Vì vậy không được gọi bbox log cũ là bbox prediction thuần.

`TRACKER_OVERLAP_DIAGNOSTICS` mới có frame/receive timestamp, detection trước tracker, logical/native ID, source index, confidence, state, observed/posterior bbox, tuổi theo số update, số update từ observation gần nhất, lost/removed IDs, IoU và overlap/min-area. Track đang được trả ra đều vừa update nên `updates_since_observed=0` là bình thường. `reid_evidence=null` tại tracker vì scoring xảy ra sau đó; `TARGET_OVERLAP_EVIDENCE` bổ sung similarity/margin và timestamp positive evidence gần nhất, không ngụ ý đó là embedding của frame hiện tại.

Snapshots mặc định **OFF**, tối đa 20 file riêng `overlap_*.jpg`, resize cạnh dài ≤960, JPEG quality 75; giá trị giới hạn bị clamp 1..100. Lỗi ghi ảnh được log và không làm hỏng tracking. Không ghi ảnh/embedding mặc định. Hai dict riêng dù cùng bbox/confidence vẫn không bị gộp. Acquisition và continuity cùng giữ overlap gate.

## B. Files modified

| File | Thay đổi |
|---|---|
| `ai/hybrid_reid.py` | Bỏ scoring chắc chắn bị gate chặn; admission validity của periodic verification; timestamp evidence/stale reasons |
| `ai/reid_target.py` | Phân pha ReID, crop/batch/normalization counters và timing, kiểm tra job trước model admission |
| `runtime/byte_tracking.py` | Khử alias có bằng chứng; output copy; mapping/lifecycle/overlap diagnostics và snapshot tùy chọn |
| `runtime/inference_worker.py` | Truyền frame ID/frame cho diagnostics; phân lý do stale |
| `runtime/face_worker.py` | Phân stale/session/track và mailbox superseded |
| `runtime/scheduling.py` | Counter admission validity rejected; giữ detection priority |
| `telemetry/latency.py` | Helper ghi aggregate stale và reason |
| `control/auto_zoom.py` | Recovery strategies, budget hữu hạn, sửa reset budget và thiếu telemetry sau STOP |
| `app/application.py`, `config.py` | Truyền cấu hình recovery; bật pulse recovery không telemetry, budget 1,2 s; snapshots OFF |
| `control/pan_policy.py` | Pure MOVE/HOLD/STOP policy với geometry, identity, freshness, manual, busy, settle gates |
| `control/auto_pan_tilt.py` | Dùng pure policy cho direction/speed/duration; giữ dispatcher/wire/STOP safeguards |
| `tools/pan_dynamics_benchmark.py` | 64 mô phỏng baseline/adaptive trên cùng plant/workload |
| `tools/tracking_logic_benchmark.py` | A/B cùng 6 detection workloads và recovery NaN; source checkpoint/current |
| `tools/calibrate_pan_manual.py` | Dry-run mặc định; một pulse xác nhận tay; emergency STOP; đo displacement video offline |
| `test/test_root_cause_revision.py` | Replay ByteTrack thật + scripted ReID, policy, recovery, calibration và ownership integration |

Danh sách máy đọc: [files.json](files.json). Diff: [changes.patch](changes.patch). Không thay model, resolution, identity threshold hoặc max pan speed.

## C. Independent test results

Baseline trước sửa: **114 tests PASS**, [baseline.log](baseline.log). Bộ targeted sau sửa: **175 tests PASS**, [targeted.log](targeted.log). Regression: **440 mục, 438 PASS, 2 ERROR**, [regression.log](regression.log). Hai lỗi là import `test_gstreamer_cpu` và `ControlIntegrationTests.test_rtsp_unavailable_schedules_backoff_without_raising`, cùng thiếu `gi` dưới Python conda 3.10. [Log regression trước lượt sửa](../pan_tilt_fix_20261111/full-tests.log) đã có đúng hai lỗi này (416 mục). Không tính module không import được là test đã pass. Sau chỉnh nhãn diagnostics/giữ transition reason tương thích, chạy lại 45 tests liên quan: [final_changed_tests.log](final_changed_tests.log).

| Test | Input | Expected | Actual | Kết quả |
|---|---|---|---|---|
| A duplicate có bằng chứng | Cùng dict detection lặp hai lần; ByteTrack thật | Một logical observation, không false overlap hold | Một track; input không bị sửa; acquisition thành công | PASS |
| B overlap hai identity | Hai dict riêng gần trùng, target/other | Không gộp; không chọn target khi ambiguous | Hai active tracks; selection None; không gọi scoring dư thừa | PASS |
| C fragmentation đúng | Reset tracker, identity target giữ nguyên | ID mới cần xác minh; logical UID giữ | Rebind sau confirmation; UID giữ | PASS |
| C fragmentation sai | ID mới, scripted similarity thấp | Không tự theo người khác | Selection None | PASS |
| D full loss/reacquire | 100 update không detection rồi target trở lại | Mất lock; xác minh lại | Reacquire, không chọn nhầm | PASS |
| E camera translation | Bbox toàn cảnh dịch mạnh; có distractor | Không quyết identity chỉ bằng IoU | Hold/reverify rồi chọn đúng scripted identity | PASS |
| F target missing | Detections rỗng | Không trả bbox cũ | Selection None | PASS |
| Frame out of order | Timestamp nhỏ hơn update trước | Không đổi bbox/track state | Tracker rỗng, selector None, bbox giữ | PASS |
| Quay lưng / không face | Scripted body identity giữ, face None | Face không phải điều kiện body tracking | Duy trì body selection | PASS logic; không kiểm chứng OSNet với ảnh quay lưng |
| Xa → gần | Bbox tăng dần | Theo đúng identity | Selection đúng | PASS logic |
| Policy boundaries | Deadzone/hysteresis, NaN/degenerate bbox, stale/manual/uncertain/busy | HOLD/STOP phù hợp | Đúng các nhánh kiểm tra | PASS |
| Dynamics | 4 offset × 8 tình huống × 2 policy | Không START khi gate chặn; mọi START có STOP | 64 mô phỏng, zero violations theo oracle mô phỏng | PASS |
| Periodic job quá hạn | Validity=False trước admission | Không vào model | TimeoutError; model không được gọi | PASS |
| Recovery None/NaN/budget | Không telemetry; pulse 40 ms; budget 120 ms | Cho OUT hữu hạn, không giả zoom | 3 pulse rồi từ chối thêm | PASS |
| Recovery target xuất hiện | Bbox fresh/valid giữa pulse | STOP BBOX_FOUND | Đúng reason | PASS |
| Recovery mất stream/session | Observation cũ hoặc session invalid | Không START | Không command | PASS |
| Disconnect giữa pulse | Tín hiệu CAMERA_DISCONNECTED | STOP | Đúng reason | PASS mock event |
| Optical target loss | Đang zoom person, chuyển missing | STOP optical trước recovery | Không phát recovery chồng lệnh | PASS |
| STOP failure | Mock STOP trả False | Disable; không restart recovery | Disable và từ chối | PASS |
| Manual/busy | Manual owner hoặc pan đang sở hữu motor | Không recovery START | Không command | PASS |
| Pan+zoom/manual/shutdown | Hai controller chung orchestrator | Không đồng thời START; hủy khi manual; đóng dừng | Đúng ownership và STOP | PASS |
| Calibration disconnect/cancel | False status hoặc emergency trước START | Không motion | Không command | PASS |
| Calibration START exception/STOP retry | Mất ACK hoặc STOP lần đầu fail | Luôn thử STOP, retry có giới hạn | Đúng thứ tự | PASS |

Ma trận 15 integration scenarios yêu cầu được đối chiếu như sau: 1,2,3,4,5,6,7 chạy trong replay mới (crossing được biểu diễn bằng overlap ambiguous); 8 dùng `test_hybrid_tracking.test_delayed_old_session_result_is_ignored` và các latency tests; 9–14 dùng replay/recovery và `CombinedOwnershipTests`; 15 dùng `PulseRecoveryFaultTests`, `test_auto_zoom`, `test_hanet_controller`. Đây là tích hợp thành phần bằng mock/scripted evidence, **không phải 15 video end-to-end có ground truth**. Bài turning-back không đánh giá khả năng OSNet nhận ra người quay lưng.

## D. Performance benchmark

A/B chạy cùng tool, cùng config và 160 observations mỗi scenario; baseline import source từ checkpoint, after import source hiện tại. Detection là dữ liệu dựng sẵn, ByteTrack là implementation thật, ReID là scores scripted. Không coi thời gian logic dưới đây là latency OSNet. Chi tiết [tracking_before.json](tracking_before.json), [tracking_after.json](tracking_after.json).

| Workload | Selected/160 trước → sau | ReID calls trước → sau | Hold frames trước → sau | Incorrect following trước → sau |
|---|---:|---:|---:|---:|
| single | 152 → 152 | 11 → 11 | 6 → 6 | 0 → 0 |
| proven_alias | 0 → 152 | 160 → 11 | 0 → 6 | 0 → 0 |
| ambiguous_overlap | 0 → 0 | 160 → 0 | 0 → 0 | 0 → 0 |
| fragmentation | 152 → 152 | 12 → 12 | 6 → 6 | 0 → 0 |
| missing_reacquire | 55 → 55 | 6 → 6 | 30 → 30 | 0 → 0 |
| camera_translation | 151 → 151 | 12 → 12 | 7 → 7 | 0 → 0 |

Recovery cùng 20 fresh missing observations, zoom NaN, budget 120 ms, pulse 40 ms: trước **0 pulse**, sau **3 pulse**, tổng requested budget sau **0.12 s**, không có lệnh sai hướng. Target không xuất hiện lại trong workload này nên successful reacquisition = 0; không tính exhaustion thành thành công tracking.

Reacquisition sau missing trong cả hai bản: **0,3 s theo clock replay**. Số target tracker IDs: single=1, fragmentation=2, missing/reacquire=2, translation=2 ở cả hai; native ID fragmentation không đồng nghĩa identity switch. Trong ground truth scripted không có selection sang identity `other`; chưa có ID-switch metric thực tế. Single sau sửa selected 152/160 giống baseline: harness đặt periodic interval=100 s để deterministic, nên identity-age guard vẫn tạo 6 hold frames trong 16 s; không gọi các hold này là lỗi overlap. Workload alias trước sửa không acquire được; đây không phải bằng chứng alias gây cặp IDs 58/62 trong phiên thật.

Các file JSON có P50/P95/P99 của **CPU wall toàn logic replay**, không phải ReID inference. Scripted overlap sau sửa giảm 160 scoring calls/320 candidates xuống 0; chi phí diagnostics vẫn tồn tại, vì thế không khẳng định toàn logic nhanh hơn theo tỷ lệ đó.

| Metric thực tế cần đo | Trước (phiên 08:36, không phải controlled A/B) | Sau sửa |
|---|---:|---|
| Detection FPS | 6,28 | N/A: chưa chạy model/camera benchmark |
| ReID mean / P95 | 191,01 / 436,24 ms | N/A |
| ReID P50/P99 theo phase | Chưa có phân pha tương đương | Instrumentation đã thêm; chưa có mẫu live |
| CPU/GPU utilization, GPU execution, queue wait | Không đủ trace tương đương | N/A; không đo từ scripted scores |
| Stale unique frames / phân bố reason | Aggregate 408, chưa de-duplicate | N/A; counters mới sẽ có từ phiên tới |

Instrumentation: `reid_phase` phân SEARCHING, ACQUIRING, TARGET_LOCKED_PERIODIC_VERIFY, OCCLUDED_HOLD, REACQUISITION; timing `REID_PHASE`, crop, normalization, preprocess, inference, matching, batch/candidate/crop counts. Scheduler vẫn ưu tiên detection đang chờ; không preempt được kernel đang chạy. `reid_gpu_execution_ms` chỉ có khi GPU profiler được bật; CPU wall `reid_inference_ms`/`reid_total_ms` không được gọi là CUDA execution time. Preprocess hiện có cả host→device transfer, gallery `.cpu().tolist()` có thể đồng bộ; chưa thay các đoạn này khi chưa có timing chứng minh bottleneck.

Stale reasons gồm DETECTION_FRAME_TOO_OLD, SELECTION_DEADLINE_EXPIRED, SESSION_MISMATCH, TRACK_MISMATCH, FACE_GEOMETRY_OR_AGE, TRACKING_RESET, CONTROL_DEADLINE_EXPIRED, ZOOM_SESSION_MISMATCH, OUT_OF_ORDER_RESULT, WORKER_RESULT_SUPERSEDED, DEADLINE_EXPIRED, ADMISSION_VALIDITY_REJECTED và fallback face. Admission chỉ biết callback false nên không gán chắc là timeout. Các `stale_reason_*` bao gồm cả dropped mailbox/skipped job, trong khi aggregate `stale_inference_results` giữ các đường đếm cũ: **tổng reason không nhất thiết bằng aggregate**, cũng không phải unique-frame count. Khi cần unique count phải đối soát frame/session/job ID trong trace.

## E. PTZ control analysis

Speed hiện tại: `min(max_speed, max(1, 1+int(abs(axis_error)*6)))`, error chuẩn hóa theo chiều rộng/cao ảnh. Speed=1 khi error dưới khoảng 1/6; speed tăng theo error và clamp ở **3**, không đổi max mặc định. HANET clamp protocol speed vào 1..8 rồi truyền `rpc.motion(code, True, speed_value)`; chưa có quan hệ đo được giữa protocol speed và độ/giây. 46 speed=1 trong trace cuối không đại diện mọi lệnh cả phiên.

Baseline pulse 120 ms vì `AUTO_PAN_TILT_ADAPTIVE=False`. Adaptive tăng tuyến tính duration từ 120 lên tối đa 240 ms giữa deadzone và far-error threshold. Hysteresis vào/ra 0,08/0,04; chỉ chọn một trục mỗi pulse. Mọi reversal cần pulse/STOP, observation mới và settle; không thêm cơ chế tăng max speed. Các gate identity uncertain/missing/stale/manual vẫn chặn lệnh. Pure policy trả MOVE/HOLD/STOP để test; dispatcher hiện hữu vẫn tự giữ latest observation và revalidate ở thời điểm wire, vì output policy không thay thế authorization thực tế.

Settle 150 ms bắt đầu sau STOP ACK; observation trước STOP không được tái dùng. Cooldown không reset ở mỗi frame. Motor ownership gồm pan, zoom, focus/manual và settling. `MOTOR_BUSY` có thể chính là pulse pan đang chạy. Khoảng nghỉ mean 800,93 ms/max 15,48 s từ trace lịch sử còn chứa deadzone, thiếu identity và thiếu observation, không phải settle cố định. Tracking đã lock dùng bbox ByteTrack khi periodic ReID chạy nền; acquisition/hold vẫn cần ReID xác minh.

Simulator [pan_dynamics.json](pan_dynamics.json) có vị trí/velocity góc, latency 40 ms, acceleration 100°/s², deceleration 160°/s², speed map giả định {1:8, 2:19, 3:35}°/s, FOV 60°, dt 10 ms, observation 9 Hz, horizon 12 s. Đây là **plant giả lập, không phải calibration HANET**. Pure policy hỗ trợ hai trục; benchmark động học chạy trục pan với các tình huống stationary, moving 3°/s, fast 12°/s, stop bất ngờ, missing, delayed, busy, manual. Busy được đưa vào như ownership bên ngoài; disconnect và RPC/STOP failure được kiểm tra ở mock controller riêng.

| Offset stationary | Centering time baseline → adaptive | START/STOP baseline → adaptive | Final absolute error baseline → adaptive |
|---|---:|---:|---:|
| 5% | None → None s | 0/0 → 0/0 | 0.0500 → 0.0500 |
| 15% | 2.67 → 2.33 s | 8/8 → 7/7 | 0.0353 → 0.0360 |
| 30% | 5.5 → 3.2600000000000002 s | 16/16 → 9/9 | 0.0292 → 0.0346 |
| 50% | 9.11 → 4.62 s | 26/26 → 12/12 | 0.0305 → 0.0389 |

Centering định nghĩa lần đầu error ≤0,04, không phải settling bền vững. Offset 5% nằm trong deadzone vào nên HOLD, không đạt tiêu chí 4% là đúng policy. Stationary overshoot trong plant này bằng 0; các scenario khác và command rate/duty/start interval/ownership blocking có trong JSON. Không suy rộng zero violations của oracle thành bảo đảm vật lý; settling/response time thực tế vẫn phải đo. Simulator chỉ ghi requested duty, chưa tính được motor duty thật từ ACK.

## F. Recovery analysis

Strategy telemetry giữ wide limit, lens fault, fresh telemetry, STOP/Idle và measured progress trước segment tiếp theo. Backend tuyên bố có telemetry nhưng trả None vẫn fail closed; không tự hạ sang pulse để che lỗi sensor.

Strategy pulse áp dụng khi capability không có lens telemetry và `PULSE_RECOVERY_WITHOUT_TELEMETRY=True`. Không tạo zoom position giả; log `lens_position=null`, hướng duy nhất OUT. Budget ứng dụng **1,2 s tổng requested pulse time** cho một đợt mất target (min với recovery_max_duration); mỗi pulse clamp vào phần budget còn lại. Sau STOP chờ max(wait_after_zoom,recovery_interval), yêu cầu observation mới hơn STOP/settle, rồi kiểm tra reacquisition. ACK chỉ xác nhận giao thức, không xác nhận wide limit/idle cơ học. Hết budget giữ trạng thái chờ; không tự khởi động lại chỉ vì tiếp tục missing.

Guards còn giữ: finite/ordered receive/publish timestamps; tuổi observation và publish; session; auto enabled; không dùng lại observation; optical action hiện hữu phải STOP trước; motor lease/manual; settle/direction cooldown; bounded pulse; in-flight feedback watchdog; reacquired bbox hợp lệ thì BBOX_FOUND STOP; disconnect/close phát STOP; STOP failure disable và giữ cơ chế fault/ownership. Stream không sống được nhận qua thiếu fresh observations và tín hiệu disconnect hiện hữu, không qua lens ACK. Budget reset khi có target geometry fresh/valid và session hợp lệ, hoặc thao tác bật/tắt/reset chủ động theo lifecycle hiện hữu.

Budget là thời lượng **yêu cầu**, không đo thời gian quay vật lý; RPC/STOP latency có thể làm thực tế dài hơn. Test mới dùng 40 ms×3 để chạy nhanh; cấu hình ứng dụng vẫn pulse 300 ms, interval 350 ms, budget tối đa 1,2 s. Không suy từ mock pulse ra khoảng zoom đạt được.

## G. Remaining issues

- Chưa xác định hai bbox 58/62 là một hay hai người. Cần snapshot có thời điểm, pre-tracker detection, native lifecycle và identity evidence từ phiên mới. Không nâng confidence/NMS threshold để giấu triệu chứng.
- Chưa benchmark YOLO/OSNet thực, CPU/GPU, RTSP hoặc GUI sau sửa. Có video cũ trong các thư mục benchmark khác nhưng không phải frame đồng bộ sự kiện 08:41:53; không dùng chúng làm ground truth cho sự kiện này.
- ReID optimization chỉ bỏ việc chắc chắn không được chấp nhận; không hứa tăng FPS hoặc giảm stale trên mọi cảnh. Không hủy được kernel đã chạy.
- Chưa hiệu chuẩn motor HANET; default adaptive OFF. Chưa có command-to-visible-motion/settling/angular velocity/overshoot phần cứng.
- Snapshot debug là I/O đồng bộ có rate limit; cần theo dõi overhead nếu bật. Log lifecycle là số update, không nhầm với camera frame count.
- Quay lưng, hai người giao nhau và camera motion mới được kiểm thử logic với scripted identity, chưa đánh giá độ chính xác thị giác ngoài thực tế.
- Regression có lỗi môi trường GI/GStreamer được ghi riêng; không thay dependency hệ thống hoặc giả module để báo pass.

## H. Run instructions

Chạy từ thư mục project chứa `main.py`:

```bash
cd /mnt/files/Project/ptz/ptz
PY=/home/admin1/miniconda3/envs/py/bin/python
PYTHONPATH=.:test "$PY" -m unittest test_root_cause_revision test_hybrid_tracking test_pan_follow_revision test_zoom_ownership test_zoom_latency_safety test_auto_pan_tilt test_auto_zoom test_reid_target
PYTHONPATH=.:test "$PY" tools/run_unit_tests.py
PYTHONPATH=. "$PY" -m tools.pan_dynamics_benchmark --output /tmp/pan_dynamics.json
PYTHONPATH=.:test "$PY" tools/tracking_logic_benchmark.py --root . --output /tmp/tracking_after.json
```

Tái lập baseline source: giải nén `before.zip` vào thư mục tạm riêng, chạy cùng `tracking_logic_benchmark.py --root <thư-mục-checkpoint>`. Tool lấy config hiện tại trước khi import source baseline để hai phía dùng cùng settings; không mở camera. Bộ baseline targeted ban đầu gồm 6 modules: hybrid_tracking, pan_follow_revision, zoom_ownership, zoom_latency_safety, auto_pan_tilt, auto_zoom.

GUI chỉ chạy thủ công khi sẵn sàng quan sát camera:

```bash
"$PY" main.py
```

Trước phiên đối soát, giữ adaptive OFF và snapshot OFF nếu không cần ảnh. Để điều tra overlap, bật tạm `TRACK_SNAPSHOT_ENABLED=True` trong `config.py`, bảo đảm thư mục log còn chỗ, lưu trace ngay sau phiên để tránh rotation. So sánh cùng đường đi/người/ánh sáng/zoom ban đầu; giữ ground truth video nếu muốn đo ID switches/false hold thật. Xem events mới và ratio thời gian theo state, không coi một counter là frame độc lập.

Hiệu chuẩn (đóng GUI và các client khác đang có quyền motor trước bước execute):

```bash
# Đã chạy dry-run trong lượt này; không kết nối camera.
PYTHONPATH=. "$PY" -m tools.calibrate_pan_manual --log /tmp/pan_dry_run.jsonl
# Chỉ người dùng tự chạy khi sẵn sàng cho một pulse vật lý:
PYTHONPATH=. "$PY" -m tools.calibrate_pan_manual --execute --speed 1 --duration .12 --direction pan_right --log /tmp/pan_live.jsonl
# Phân tích video đã ghi, không phát lệnh:
PYTHONPATH=. "$PY" -m tools.calibrate_pan_manual --video /path/to/recorded.mp4 --log /tmp/pan_video.jsonl
```

Execute yêu cầu gõ chính xác `MOVE`; Enter/Ctrl-C/SIGTERM yêu cầu emergency STOP. Một lần chạy chỉ một pulse; speed 1..3, duration 20..240 ms. Kiểm tra heartbeat connected trước START; START exception/mất ACK vẫn thử STOP, tối đa 3 lần. STOP chưa ACK báo lỗi và cần kiểm tra camera; không thể bảo đảm dừng tức thời qua mạng mất kết nối. Worker/network timeout khiến emergency STOP có độ trễ, không thay thế nút dừng vật lý. Công cụ không giành lease giữa nhiều process nên phải đóng GUI trước.

Bắt đầu speed1/120 ms, ghi video/timestamp, sau đó chỉ đổi một biến mỗi lần trong giới hạn tool. Video helper phase-correlation xuất dx/dy pixels và confidence theo frame, không tự suy ra góc. Dùng cảnh tĩnh có texture, bỏ frame low confidence; đồng bộ clock video với command log để đo onset/settling, quan sát residual displacement sau STOP. Nếu người trong ảnh tự di chuyển, correlation không tách riêng camera motion đáng tin cậy. Chỉ tính angular velocity khi có FOV/zoom hoặc mốc góc đã hiệu chuẩn.

## I. Rollback

Tắt nhanh pulse recovery mới: `PULSE_RECOVERY_WITHOUT_TELEMETRY=False` trong `config.py`, khởi động lại ứng dụng. Adaptive vốn OFF; giữ `AUTO_PAN_TILT_ADAPTIVE=False` để trở về pulse baseline. Tắt snapshot bằng `TRACK_SNAPSHOT_ENABLED=False`. Không tăng budget khi chưa hiểu lý do target mất.

Checkpoint chứa source **trước lượt sửa này**, không phải reset toàn repository. [changes.patch](changes.patch) và [files.json](files.json) xác định chính xác nhóm thay đổi. Giữ lại thay đổi mới của người dùng sau thời điểm checkpoint trước khi phục hồi. Không giải nén đè toàn project mù quáng.

- Recovery: phục hồi `control/auto_zoom.py`, `app/application.py`, phần config recovery từ checkpoint cùng nhau.
- Tracking/performance: phục hồi `ai/hybrid_reid.py`, `ai/reid_target.py`, `runtime/byte_tracking.py`, `runtime/inference_worker.py`, `runtime/face_worker.py`, `runtime/scheduling.py`, `telemetry/latency.py` cùng nhau; giữ caller/adapter signature đồng bộ.
- Pan policy: phục hồi `control/auto_pan_tilt.py`; file policy mới có thể giữ không dùng. Max speed và adaptive default không thay đổi so checkpoint.
- Tools/tests mới không ảnh hưởng GUI nếu không chạy. Sau rollback test mới có thể không còn áp dụng; chạy baseline targeted để kiểm tra bản cũ.

Có thể kiểm tra reverse patch trước, không thực thi khôi phục trong lượt này:

```bash
patch --dry-run -R -p1 < docs/root_cause_fix_20261112/changes.patch
```
