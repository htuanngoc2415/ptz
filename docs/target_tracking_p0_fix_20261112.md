# AI PTZ — sửa P0 overlap và ReID, 12/11/2026

Đã sửa trực tiếp trong project hiện tại, tạo checkpoint trước sửa, chạy baseline/replay/tests và đối chiếu hành vi motor. **Chưa đủ điều kiện nghiệm thu P0 ngoài thực tế:** không có raw video/snapshot có nhãn tại năm failure cases để xác nhận một hay hai người, false hold hoặc ID switch thực. Không tuyên bố đã khắc phục hoàn toàn mất target trên camera.

Phạm vi đã hoàn thành: xác minh mode model hiện tại; snapshot bất đồng bộ theo trigger; overlap gate có giới hạn thời gian/evidence; periodic ReID theo candidate liên quan; giảm global scans vô ích trong geometry hold; phân loại stale counters; replay ba phiên bản; tests và rollback. Không phát lệnh camera, không mở RTSP hoặc chạy GUI.

## A. Root Cause Analysis

### Confirmed

1. **Model hiện tại chạy head end-to-end.** Load metadata từ local `person.pt` cho `DetectionModel/Detect`, `model.end2end=True`, `head.end2end=True`, class duy nhất `0: person`. Ultralytics **8.4.25** có nhánh `prediction.shape[-1] == 6 or end2end` trả boxes theo confidence/class/max_det, không chạy IoU NMS truyền thống. Vì vậy `PERSON_IOU_THRESHOLD=0.60` không bảo đảm lọc cặp IoU 0,84. [Model audit và SHA-256](target_tracking_p0_20261112/model_audit.json). Chỉ load local weights/metadata; không chạy inference/network.
2. Log có hai **source detection riêng trước ByteTrack**, cùng được observed trong frame, không chỉ track Kalman cũ. Điều này đã được báo cáo ở [phiên 09:41](ai_ptz_current_status_20261112_094147.md). Chưa suy ra hai observations cùng một người.
3. Gate cũ đánh đồng `intersection / min(area) >= 0.15` với unsafe overlap ở mọi trạng thái. Ví dụ IoU 0,04588/overlap-min 0,18570 vẫn hold ngay. Log còn giữ có 10/10 hold transitions do PERSON_OVERLAP; không suy rộng ra toàn bộ 17 hold.
4. Geometry hold cũ vẫn chấm toàn bộ nhóm nếu có ít nhất một distractor không overlap. Periodic verification cũng chấm mọi người, mặc dù track ổn định. Với 5 candidates/batch limit4 và normalized variants, công việc tăng đáng kể.
5. `stale_inference_results` trước P0 bao gồm control-only STOP khi session bị vô hiệu lúc hold. Phiên mới 387/389 stale aggregate thuộc nhánh zoom-session, không phải 387 inference frame trễ.

### Probable

End-to-end output có thể giữ hai proposals cho một người; bbox nhỏ nằm trong bbox lớn có thể khiến gate hold quá thường xuyên. Nhiều global scoring ở hold/reacquisition làm detection loop chậm hơn và kéo dài thời gian thiếu quyền điều khiển. Đây là cơ chế phù hợp source/log, chưa lượng hóa đầy đủ quan hệ nhân quả bằng video có ground truth.

### Unverified

Hai bbox 58/55 hoặc 74/76 có phải cùng người; số false holds/ID switches ngoài thực tế; P0 có tăng detection FPS hay continuity live; model file đã dùng trong phiên cũ có đúng cùng hash hiện tại không. Checkpoint chứa source, không chứa snapshot model runtime cũ. Không coi mode model hiện tại là chứng minh byte-for-byte lịch sử.

## B. Detector / Tracker Findings

- Adapter nhận `results.boxes`, confidence/class filtering rồi clip bbox. Model hiện tại một class; nhánh NMS truyền thống có class-aware/agnostic tùy predictor, nhưng nhánh end-to-end đã bypass bước đó. Giữ nguyên model, resolution, confidence/IoU thresholds; **không thêm NMS suy đoán** và không deduplicate theo IoU.
- Bổ sung `PERSON_POSTPROCESS_MODE`: end-to-end flag, mode, confidence, configured IoU, agnostic flag, class filter; mỗi detection giữ `source_detection_index` từ output model. ByteTrack output thêm `tracker_source_index` cho vị trí trong array input sau khử alias.
- ByteTrack tiếp tục high/low association và tracked/lost/removed lifecycle, không đổi threshold. Chỉ khử same-object alias đã có trước P0. Hai source indices khác nhau được giữ; output dùng observed bbox, metadata giữ posterior Kalman riêng.
- Unit test dùng đúng `ultralytics.utils.nms.non_max_suppression` với hai detections IoU>0,8 xác nhận nhánh end-to-end vẫn trả cả hai; đây là test postprocess, không chứng minh ảnh chứa một người.

| Bằng chứng log | Cặp | IoU / overlap-min | Ý nghĩa |
|---|---|---|---|
| 09:43:21.235 | ID58/source4 và ID55/source3 | 0,832 / 1,0 | Hai observations trước tracker |
| 09:44:18.045 | ID74/source5 và ID76/source4 | 0,84127 / 1,0 | Hai observations trước tracker |
| 09:44:28.973 | Target72 và ID71 | 0,04588 / 0,18570 | Contact nhẹ cũng vượt gate cũ |

### Snapshot diagnostics

Mặc định OFF. Chỉ bật bằng debug env `PTZ_TRACK_SNAPSHOTS=1`. Các trigger: IoU>0,8; vào hold; reacquire→hold dưới1s; đổi track ID. Trigger có thể gộp chung một snapshot khi cùng frame.

Một bundle gồm JPEG frame đầu vào AI không resize thêm, JPEG annotated và JSON chứa detection trước tracker, observed/posterior track boxes, IDs/class/confidence/source indices, selected và bound target, session, tracking/identity state, transition reason, frame ID, receive monotonic timestamp, wall time tại lúc trigger và model postprocess mode. “Raw” ở đây là frame trước annotation, **JPEG có nén**, không phải bitstream RTSP hoặc timestamp sensor gốc.

Writer thread riêng; một job đang ghi + tối đa một pending job. Inference chỉ copy ảnh/metadata khi trigger và queue có chỗ; resize/encode/write chạy ở writer. Cooldown1s; tối đa20 **bundles** và64MB tổng JPEG/JSON; giới hạn file config giờ là số bundles, không còn một JPEG đơn lẻ. Frame vượt8 megapixels bị bỏ để giới hạn RAM. Writer xử lý lỗi I/O, dọn bundle cũ/ảnh dở thuộc prefix riêng; shutdown drain best-effort tối đa2s. Không có worker ghi ảnh khi OFF. Không ghi embeddings, tên người hoặc recognition payload.

## C. Overlap Policy

Không tăng `REID_CROSSING_OVERLAP=0.15`. Đây vẫn là trigger cần đánh giá. P0 chỉ cho phép **weak geometric contact** tiếp tục khi đồng thời có:

- Đúng track ID/session hiện tại; đang locked và chưa crossing hold.
- Bbox là observation mới, gap so observation trước không vượt `MAX_CONTROL_OBSERVATION_AGE=0.8s`; không điều khiển bằng bbox dự đoán cũ.
- Positive identity evidence không quá1,5s, không từ tương lai, không có negative verification đang tích lũy; identity VERIFIED/VERIFYING.
- IoU với bbox trước ít nhất ngưỡng continuity hiện hữu0,25; area ratio0,5..2.
- Với **mọi** candidate giao nhau vượt gate0,15: intersection không quá10% diện tích target, IoU pair không quá0,10; candidate khác không giải thích bbox trước tốt gần bằng target (margin IoU0,10).

Nếu đủ, giữ observed target tối đa **350ms** để yêu cầu verification target + candidates gây giao nhau. Muốn tiếp tục lâu hơn, phải có kết quả verification chụp **sau khi weak contact bắt đầu**, đủ margin theo threshold cũ, bao gồm các IDs xung đột và còn trong cửa sổ1,5s. Tập conflicting IDs đổi → hold; strong geometry, evidence mâu thuẫn, missing, trajectory jump hoặc hết hạn → hold ngay theo guards tương ứng.

Không miễn overlap sau reacquire. Track/session đổi reset memory; clean observation reset episode. Kết quả background hợp lệ được consume **trước** quyết định timeout geometry, tránh bỏ nhầm kết quả vừa hoàn tất. Kết quả sai session/epoch/ID hoặc quá hạn vẫn bị loại. Acquisition/recovery khi identity chưa được xác minh vẫn dùng gate chặt; P0 không cấp quyền chỉ từ similarity lịch sử.

Các giá trị mới nằm trong config và có rollback flag `REID_TEMPORAL_OVERLAP_ENABLED`. 10% target-cover/IoU0,10 giới hạn ngoại lệ vào contact nhỏ; 350ms là thời gian chờ hữu hạn cho verification,1,5s ngắn hơn identity max-age cũ5s. Đây là **giới hạn thiết kế đã test**, không phải threshold học/calibrate trên ground truth camera. Cần validation live có nhãn trước khi nghiệm thu.

| Tình huống | Hành vi |
|---|---|
| Weak contact đơn lẻ, trajectory/evidence mới | Continue observed target và yêu cầu verification |
| Weak contact kéo dài, không có evidence mới | Hold sau grace hoặc khi evidence/gap hết hạn |
| Weak contact đã verify đủ candidates, còn mới | Continue trong cửa sổ evidence hữu hạn |
| Strong overlap / candidate gần bằng association / merge-split risk | Hold ngay |
| Appearance conflict, ID đổi, stale/future evidence | Không dùng weak grace để giữ quyền |
| Missing detection / hết loss grace | Giữ cơ chế hold/full loss hiện hữu |
| Reacquire vừa xong | Đánh giá cùng evidence guards, không có miễn trừ theo thời gian lock |

## D. ReID Optimization

**Periodic locked:** chọn target và mọi person có giao hình học với target (kể cả dưới trigger0,15), hoặc có IoU với previous target bbox đủ continuity. Người không liên quan không đi vào batch này. Không lọc bỏ conflicting low-confidence track đang được ByteTrack trả ra.

**Strong geometry hold:** global retry tối đa mỗi0,5s khi current target còn overlap. Giữa các lần retry vẫn xử lý geometry/freshness/missing và giữ hold; không tái dùng score để điều khiển. Mỗi global retry xét **tất cả eligible candidates**, kể cả người xa vị trí cũ. Khi geometry hết overlap, confirmation tiếp tục bình thường; target mất hẳn/full reacquisition giữ global search hiện hữu. Không hard-gate người xa và không thay threshold identity.

Cách này giảm scoring chắc chắn bị geometry chặn mà không giới hạn khả năng tìm người ở xa. Chưa thay toàn bộ global reacquisition thành spatial shortlist vì thiếu ground truth để chứng minh an toàn; đây vẫn là workload cần đo tiếp.

**Normalization giữ nguyên.** Điều kiện cũ: luma<90 hoặc contrast<35, với contrast=p90−p10; normalized embedding là một pass ảnh biến đổi có chủ đích, không phải cùng tensor bị lặp. Trong450 REID_SCORE samples của app log mới,411 có luma<90 và0 có contrast<35. Cửa sổ workload khác cho1678/1683 crops có normalization; không trộn hai tập mẫu để khẳng định tỷ lệ nguyên nhân chính xác. Thêm dark/low-contrast counts vào `REID_WORKLOAD`.

Không bỏ normalized pass theo raw score vì có thể thay margin/false acceptance của distractors; chưa có cùng crop ảnh có nhãn để A/B. Không thêm cache embedding giữa frames, không cập nhật gallery bằng observations. Fresh-frame guard đã loại same/out-of-order selection; normalized variants vẫn là công việc riêng. Test low-quality xác nhận raw0,6/normalized0,8 tiếp tục giữ score0,8 theo fusion cũ.

### Stale counters mới

Safety guard và thứ tự STOP không đổi. Chỉ tách categories:

- `stale_category_inference` và aggregate `stale_inference_results`: inference results thật.
- `stale_category_reid_result` cộng với `stale_reid_results`: background ReID result bị discard.
- `stale_category_ptz_observation`: spatial PTZ observation có session mismatch.
- `stale_category_control_stop_session_mismatch`: STOP/control-only gặp session đã vô hiệu.
- `stale_category_tracking_reset_cancellation`: hủy do reset.

`stale_reason_*` giữ reason để drill-down. Không cộng control STOP/PTZ/reset vào aggregate inference nữa. Schema aggregate **thay đổi so log cũ**; không dùng số mới giảm để tuyên bố inference nhanh hơn. Đây vẫn là event counts, chưa phải unique frame/job counts; không đặt nhãn unique cho counters này.

## E. Replay Tests

Dataset [failure_cases.json](target_tracking_p0_20261112/failure_cases.json) trích từ trace thực `latency_trace_20261112_094151_229452.jsonl` và `.1/.2/.3`. Có frame ID, receive timestamp, bbox/confidence/track/session, previous observation, prior positive evidence timestamp và logged transition. Evidence lấy từ TARGET_OVERLAP_EVIDENCE hoặc **relock event đã hoàn tất trước observation hiện tại**, không lấy score tương lai. Không có raw frame hoặc identity ground truth.

Replay khôi phục context locked đã được ghi trước từng failure và gọi geometry gate của selector. Đây là **gate/state-context replay tại5 mốc**, không chạy lại cả 92s video hoặc tự tạo scores cho các frame thiếu. Vì trace là dữ liệu đã rate-limit/rotate, không thể suy full-loss count, identity-switch, total selected time hay inference FPS ngoài thực tế từ năm quyết định này.

Ba source versions: A=`root_cause_fix_20261112/before.zip` (trước lượt sửa trước), B=`target_tracking_p0_20261112/before.zip` (ngay trước P0), C=source P0. Config chung được giữ khi import cả ba để tách code effect.

| Failure marker | A | B | C | Kết luận có thể rút ra |
|---|---|---|---|---|
| 09:43:12.642 | HOLD | HOLD | CONTINUE | Không hold tức thì ở geometry gate; vẫn cần verification |
| 09:43:25.566 | HOLD | HOLD | CONTINUE | Không hold tức thì ở geometry gate; vẫn cần verification |
| 09:43:28.867 | HOLD | HOLD | CONTINUE | Không hold tức thì ở geometry gate; vẫn cần verification |
| 09:44:08.234 | HOLD | HOLD | HOLD | Giữ conservative hold |
| 09:44:28.974 | HOLD | HOLD | CONTINUE | Không hold tức thì ở geometry gate; vẫn cần verification |

Bốn quyết định chuyển từ HOLD sang CONTINUE có giới hạn, một strong-overlap case vẫn HOLD. **Không gọi bốn quyết định này là bốn false holds đã chứng minh**. Các JSON replay để false_holds/identity_switches/full_losses/inference throughput=`null`.

### Kiểm thử thực sự đã chạy

| Nhóm | Expected behavior | Actual |
|---|---|---|
| Same-object duplicate / distinct source / high-IoU | Chỉ khử alias; giữ hai detections độc lập; observed khác posterior | PASS |
| End-to-end postprocess | Không giả định IoU0,60 tự loại duplicate | PASS bằng hàm thư viện cài tại máy |
| Single/persistent weak contact | Continue ngắn hạn; hết evidence thì hold | PASS |
| Strong/candidate-changing/appearance conflict | Không dùng grace để cố giữ target | PASS |
| ID change, distant reacquisition | Xác minh lại; không bỏ candidate xa | PASS trên scripted labelled data |
| Reacquire rồi weak/strong overlap | Weak đủ evidence được tiếp tục; strong hold | PASS |
| Missing và loss grace | Không trả bbox cũ; full loss rồi reacquire | PASS |
| Delayed/out-of-order/future evidence | Không refresh session/evidence không hợp lệ | PASS |
| Candidate-aware periodic | Target đơn lẻ1 crop; conflict vẫn đi cùng | PASS |
| Normalization/gallery | Giữ normalized path; không ghi gallery | PASS |
| Snapshot OFF/cooldown/queue/retention | OFF không tạo writer; ghi async và giới hạn bundles | PASS |
| Stale counters | STOP/reset không cộng inference aggregate | PASS |
| Recorded replay provenance / strong event | Evidence không từ tương lai; strong case vẫn hold | PASS |
| GPU scheduler expired admission / ownership | Job stale không vào model, release lane; CPU-only không chiếm accelerator | PASS trong tests hiện có |
| Manual/STOP/zoom/disconnect/shutdown | Giữ ownership, STOP, recovery bounded | PASS mock integration hiện có |

- [Baseline trước P0](target_tracking_p0_20261112/baseline.log): **175 tests PASS**.
- [Targeted](target_tracking_p0_20261112/targeted.log): **209 tests PASS** (gồm29 P0 tests).
- [Bộ P0 riêng](target_tracking_p0_20261112/p0_tests.log): **29 tests PASS**.
- [Kiểm tra cuối sau reset hold-episode state và low-confidence continuity](target_tracking_p0_20261112/final_changed_tests.log): **50 tests PASS** (29 P0 +21 hybrid).
- [Full regression](target_tracking_p0_20261112/regression.log): **469 mục,467 PASS,2 ERROR**. Hai lỗi môi trường đã có trước: `test_gstreamer_cpu` import và `test_rtsp_unavailable_schedules_backoff_without_raising`, do conda Python3.10 thiếu `gi`. Không sửa dependency hoặc fake GStreamer để báo xanh. Full regression cuối đã bao gồm các tests replay/continuity/rollback bổ sung.

Trong quá trình regression đã phát hiện instrumentation snapshot truy cập field chỉ có ở hybrid khi selector base được dùng; đã sửa OFF fast-path và optional fields, rồi chạy lại targeted/full. Không còn assertion failures trong các log kết quả cuối. Test quay lưng/xa dùng scripted body identity, không đánh giá neural recognition trên ảnh quay lưng.

## F. Benchmark cùng workload

[Tool benchmark](../tools/benchmark_p0_scenarios.py) dùng100 observations/scenario, dt100ms, IDs/bbox/nhãn cố định, scripted scores0,95/0,1; không chạy model hoặc camera. Các results [A](target_tracking_p0_20261112/benchmark_old.json), [B](target_tracking_p0_20261112/benchmark_pre_p0.json), [C](target_tracking_p0_20261112/benchmark_p0.json).

Periodic background được tắt trong state scenarios để không tạo evidence asynchronous giả; identity max-age vẫn giữ. Vì vậy scenario isolated cũng có một VERIFY_TIMEOUT hold ở cả ba bản sau5s; không coi hold này là false overlap. Periodic candidate workload được đo riêng cùng5 observed boxes.

| Scenario | Hold A/B/C | Selected seconds A/B/C | ReID calls A/B/C | Candidates A/B/C |
|---|---|---|---|---|
| isolated | 1 / 1 / 1 | 9.6 / 9.6 / 9.6 | 6 / 6 / 6 | 6 / 6 / 6 |
| single_weak | 2 / 2 / 1 | 9.3 / 9.3 / 9.6 | 10 / 9 / 6 | 11 / 9 / 6 |
| persistent_weak_unverified | 2 / 2 / 2 | 7.4 / 7.4 / 7.8 | 29 / 9 / 9 | 49 / 9 / 9 |
| strong_crossing | 2 / 2 / 2 | 7.4 / 7.4 / 7.4 | 29 / 9 / 9 | 49 / 9 / 9 |
| missing_reacquire | 1 / 1 / 1 | 5.6 / 5.6 / 5.6 | 6 / 6 / 6 | 6 / 6 / 6 |
| distant_reacquire | 1 / 1 / 1 | 5.6 / 5.6 / 5.6 | 6 / 6 / 6 | 6 / 6 / 6 |
| strong_with_distractors | 2 / 2 / 2 | 7.4 / 7.4 / 7.4 | 29 / 29 / 13 | 89 / 89 / 25 |

- Periodic isolated candidates: **5 / 5 / 1**. Đây là giảm số crops được yêu cầu; vẫn kiểm tra mọi candidate liên quan geometry/association.
- Wrong-target selections trên7 synthetic labelled scenarios: **0 ở cả ba bản**. Chỉ đánh giá tập dựng sẵn, không suy ra ID-switch thực tế bằng0.
- Single weak case: một hold do geometry được loại trong scenario có nhãn; strong crossing vẫn hold. Persistent weak chưa verify chỉ tiếp tục thêm phần grace hữu hạn rồi hold.
- Missing/distant reacquisition không bỏ candidate xa; full loss/reacquisition behavior giữ trong workload này. JSON lưu reacquisition latency và median lock→hold cho từng scenario.
- Projected batch counts trong JSON giả định mọi crop có normalized pass (`2*ceil(candidates/4)`); **không phải số GPU calls đo thật**. Không dùng chúng để tính FPS vật lý.
- ReID neural wall time, GPU execution, actual detection throughput, live false holds/ID switches: **N/A**. Không có cùng raw failure video/crops và ground truth để làm controlled neural A/B. Không nhận nghiệm thu tiêu chí throughput thực tế chỉ từ tests mock.

## G. Files Modified

| File | Mục đích / hành vi |
|---|---|
| `ai/overlap_policy.py` (mới) | Weak-contact memory, evidence/trajectory/geometry bounds, time-based expiry |
| `ai/hybrid_reid.py` | Dùng policy; consume valid verification trước gate; periodic candidate subset; bounded global retry trong geometry hold; typed ReID counter |
| `ai/detector.py` | Model postprocess metadata và source detection index; không đổi filtering |
| `runtime/byte_tracking.py` | Index/diagnostic metadata và snapshot triggers; tracking algorithm không đổi |
| `runtime/tracking_snapshots.py` (mới) | Writer queue/cooldown/retention/annotation, mặc định OFF |
| `runtime/inference_worker.py` | Gửi frame và selector context cho diagnostics sau selection; motor freshness check vẫn giữ |
| `ai/reid_target.py` | Thêm dark/low-contrast workload counts; không đổi normalization/fusion |
| `telemetry/latency.py` | Phân stale categories, chỉ inference mới cộng aggregate inference |
| `control/auto_zoom.py` | Chỉ đổi lời gọi counter để phân STOP với spatial observation; logic motor không đổi |
| `config.py` | P0 flags và bounds; debug snapshot env/cap; không đổi các config PTZ/recovery |
| `tools/replay_p0_overlap.py` (mới) | Extract dataset real logs, replay gate trên từng source revision |
| `tools/benchmark_p0_scenarios.py` (mới) | Labelled synthetic A/B/C và candidate workload |
| `test/test_target_tracking_p0.py` (mới) | Behavioral/safety/provenance tests |

[Danh sách file](target_tracking_p0_20261112/files.json), [diff](target_tracking_p0_20261112/changes.patch), [checkpoint](target_tracking_p0_20261112/before.zip). Không có git repo tại workspace này nên dùng checkpoint, không reset/ghi đè lịch sử người dùng.

[Kiểm tra motor unchanged](target_tracking_p0_20261112/motor_unchanged.json) đối chiếu bytes: HANET adapter/RPC, auto-pan, pan policy, zoom policy, orchestrator và application wiring giữ nguyên. `auto_zoom.py` chỉ khác lời gọi typed counter. Recovery without telemetry/budget, speed/pulse/cooldown/settle/deadzone, manual/STOP safety không thay đổi. Lỗi tên STOP `RECOVERY_WIDE_LIMIT` khi budget exhausted đã biết được để lại lượt sau theo phạm vi yêu cầu.

## H. Remaining Uncertainty và cách chạy

**Chưa nghiệm thu P0 live.** Cần snapshot/video đồng bộ các cặp detections và nhãn target/other để xác minh duplicate/false hold/ID-switch. Threshold weak-contact là giới hạn thử nghiệm có tests, không phải kết quả calibration. Điều kiện còn thiếu: no increase wrong-target switching trên video có nhãn; detection throughput cùng workload; continuity live sau P0. Không đổi model/NMS hoặc bỏ normalization để làm số liệu đẹp khi thiếu dữ liệu đó.

Full suite còn hai lỗi `gi` môi trường; RTSP disconnect được test bằng mock tín hiệu/watchdog, không kiểm chứng stream thật. Dataset failure trace thưa, một số frames mất do rotation; replay giữ unknown=null và không dựng ReID evidence cho chúng.

Chạy từ project:

```bash
cd /mnt/files/Project/ptz/ptz
PY=/home/admin1/miniconda3/envs/py/bin/python
PYTHONPATH=.:test "$PY" -m unittest test_target_tracking_p0 test_hybrid_tracking test_root_cause_revision test_pan_follow_revision test_zoom_ownership test_zoom_latency_safety test_auto_pan_tilt test_auto_zoom test_reid_target test_cuda_failure
PYTHONPATH=.:test "$PY" tools/run_unit_tests.py
PYTHONPATH=.:test "$PY" tools/replay_p0_overlap.py --source . --dataset docs/target_tracking_p0_20261112/failure_cases.json --output /tmp/replay_p0.json
PYTHONPATH=.:test "$PY" tools/benchmark_p0_scenarios.py --source . --output /tmp/benchmark_p0.json
```

Tái lập A/B: giải nén mỗi checkpoint vào một thư mục tạm riêng, rồi chạy cùng hai tools với `--source <thư-mục-source>`; giữ CWD project và `PYTHONPATH=.:test` để config dùng chung. `--extract` ở replay tool tái tạo dataset từ log gốc; không cần nó để chạy dataset đã lưu. Model weights không cần cho replay/unit tests.

Chỉ người dùng chủ động chạy phiên debug có camera khi sẵn sàng:

```bash
PTZ_TRACK_SNAPSHOTS=1 "$PY" main.py
```

Lệnh này chỉ hướng dẫn, **chưa chạy trong lượt sửa**. Nó mở GUI thật và có thể dùng auto control theo cấu hình hiện hữu. Chạy ngắn với cảnh có nhãn, giữ nguyên PTZ/zoom settings để tách biến; lấy `logs/overlap_snapshots` cùng toàn bộ trace ngay sau phiên. Tắt env hoặc đặt0 khi xong; mặc định production không lưu ảnh. Không gửi ảnh/credentials ra ngoài trong lượt này.

## I. Rollback

Tắt độc lập bằng config rồi restart:

- `REID_TEMPORAL_OVERLAP_ENABLED=False`: quay về gate geometry chặt; vẫn giữ identity guards.
- `REID_CANDIDATE_AWARE=False`: periodic score toàn bộ candidates và bỏ hold-scan throttle; không đổi threshold.
- `PTZ_TRACK_SNAPSHOTS=0`: tắt ảnh/debug writer; các metadata counters vẫn còn.

Để rollback source hoàn toàn, dùng checkpoint **ngay trước P0** trong `target_tracking_p0_20261112`, không dùng checkpoint cũ hơn làm mất recovery HANET đã hoạt động. Kiểm tra reverse patch trước:

```bash
patch --dry-run -R -p1 < docs/target_tracking_p0_20261112/changes.patch
```

Chỉ apply sau khi so sánh các thay đổi người dùng phát sinh sau checkpoint; không giải nén đè toàn project. Các nhóm phụ thuộc cần khôi phục đồng bộ:

1. Overlap/candidate: `ai/hybrid_reid.py` và các flags P0; module policy mới có thể giữ khi không được import.
2. Diagnostics: `runtime/byte_tracking.py` + `runtime/inference_worker.py` + `ai/detector.py`; khi rollback caller/adapter cần cùng signature. Writer mới có thể giữ không dùng.
3. Counters: `telemetry/latency.py` + phần instrumentation `control/auto_zoom.py` + ReID counter trong hybrid; không quay lùi logic recovery. Các dashboard phải biết schema aggregate stale đã đổi.
4. Tools/tests mới không ảnh hưởng GUI khi không chạy. Sau rollback chạy baseline175 tests; P0-specific expectations không áp dụng cho gate cũ.

Checkpoint, logs và replay artifacts giữ để review. Dừng công việc tại bản sửa P0 và báo cáo này; không tự chuyển sang tăng tốc motor hay đổi zoom policy.
