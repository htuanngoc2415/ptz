# Chẩn đoán và sửa hồi quy identity-aware tracking — 16/11/2026

## Kết luận và trạng thái nghiệm thu

Đã hoàn tất backup trước sửa, bổ sung diagnostics, sửa các lỗi có test tái hiện, giữ BoT-SORT/OSNet-AIN và chạy kiểm thử cùng replay đối chứng. **Chưa đạt điều kiện nghiệm thu “bám đúng người tốt hơn trong thực tế”**: video thật chưa có ground truth; đoạn monitor thậm chí có thời gian được chọn thấp hơn sau sửa. Không tự động đưa bản sửa thành mặc định.

`python ptz/main.py` vẫn mặc định ByteTrack. `PTZ_IDENTITY_TRACKING=1` riêng lẻ vẫn dùng hành vi identity trước sửa; muốn thử sửa phải bật thêm `PTZ_IDENTITY_FIX=1` và `PTZ_IDENTITY_REID_OPTIMIZED=1`. Không chạy camera thật trong lượt làm việc này.

## A. Backup đã xác minh

- Project: `/mnt/files/Project/ptz`.
- Backup: `/mnt/files/Project/ptz_backup_before_tracking_fix_20261116_103650`.
- Bắt đầu: **10:36:50**, kiểm tra hoàn tất **10:39:06**, 16/11/2026, giờ địa phương UTC+7.
- Root không phải Git repository: branch, commit, staged/modified/untracked/ignored theo Git đều **không áp dụng**. Không tạo Git mới hoặc giả định checkpoint là stable commit. Các `.git` lịch sử bên trong `.hotfix_checkpoints` được giữ nguyên trong snapshot.
- Trước backup: filesystem dữ liệu còn khoảng **73 GB**. Sao lưu nguyên project khoảng **5,1 GB** và nguyên Conda env `py` khoảng **9,5 GB**. Sau backup còn khoảng 59 GB.
- **Không loại trừ file nào**: gồm code, cấu hình, source GUI/face/PTZ, model YOLO/OSNet/face/focus, dữ liệu tham chiếu, recordings, cache, logs, hidden files, checkpoint và dependencies hiện tại.
- `cp -a` giữ cấu trúc, quyền, symlink; thư mục backup mode **0700**. Không upload backup hoặc nội dung credentials.
- Đã so SHA256 tất cả file thường, đích symlink và permission bits: **67.935 mục, 0 sai khác**. `manifest.json` và `pip-freeze.txt` nằm trong backup. Sau sửa đã chạy lại `verify_saved.py`: 67.935 mục, 0 lỗi; xem `verification_saved.log` trong backup. Snapshot này là baseline trước sửa, không khẳng định nó là bản tốt nhất.
- Có `RESTORE.md`, `verify.py` và `verify_saved.py`. `verify_saved.py` đối chiếu backup với manifest cũ mà không phụ thuộc source hiện đã sửa. `verify.py` là phép so với source lúc tạo backup; sau sửa source đương nhiên khác.
- Môi trường Python có thể khôi phục về đúng absolute path cũ. Driver GPU, GStreamer và thư viện hệ điều hành vẫn là dependencies của máy hiện tại; không tuyên bố snapshot là image hệ điều hành portable. Hai lỗi thiếu `gi` có trước sửa được giữ nguyên, không cài dependencies mới.

Khôi phục sau khi dừng ứng dụng:

```bash
python /mnt/files/Project/ptz_backup_before_tracking_fix_20261116_103650/verify_saved.py
cd /mnt/files/Project
mv ptz "ptz_before_restore_$(date +%Y%m%d_%H%M%S)"
cp -a /mnt/files/Project/ptz_backup_before_tracking_fix_20261116_103650/project /mnt/files/Project/ptz
```

Nếu cần phục hồi cả Conda env, dừng các Python process dùng env đó trước:

```bash
mv /home/admin1/miniconda3/envs/py "/home/admin1/miniconda3/envs/py_before_restore_$(date +%Y%m%d_%H%M%S)"
cp -a /mnt/files/Project/ptz_backup_before_tracking_fix_20261116_103650/python_environment /home/admin1/miniconda3/envs/py
```

## B. Nguyên nhân: đã xác nhận và chưa xác nhận

### 1. Vì sao có bbox nhưng bị chặn điều khiển?

`IdentityAwareTargetSelector._publish()` phân biệt “thấy bbox của track đã khóa” và “identity đủ tin cậy để điều khiển”. `crossing_hold`, track missing, temporal unreliable, appearance ambiguity và evidence expiry có thể chặn. 338 observed = 142 authorized + 196 uncertain trong phiên 10:11 là nhất quán với định nghĩa này, không phải 196 mất detection.

Không thể phân bổ chính xác 196 lần đó theo nguyên nhân từ log cũ: trace đã rotation, chỉ còn 102 diagnostic decisions lấy mẫu/thay trạng thái, trong đó 71 bản ghi không authorize. PERSON_OVERLAP=22, AMBIGUOUS_IDENTITY=7, CONFIRMING_IDENTITY=24, RESET=11, BELOW_CONFIRMATION_THRESHOLD=6, TRACK_MISSING=1 là số bản ghi còn giữ, gồm cả trạng thái không có track; không phải breakdown của 196 observations. Mọi kết luận khác sẽ là suy đoán.

Diagnostics mới có `observed_denial_reasons`, `denial_reasons`, `allow_reasons` tăng theo từng quyết định và xuất trong metrics cuối; `control_reason` tách khỏi transition reason cũ có thể còn lưu từ frame trước.

### 2. Hai lỗi overlap có thể tái hiện

**Thiếu cập nhật bằng chứng overlap:** baseline identity `_consume()` cập nhật `last_positive_verify`, nhưng không cập nhật `_overlap_memory.verified_at/verified_ids`. Trong khi `HybridReIDTargetSelector._take_verification()` của bản cũ có cập nhật cả hai. `OverlapMemory.assess()` cần các trường này để duy trì overlap nhẹ sau grace. Vì vậy bản mới có thể đã xác minh OSNet thành công mà overlap gate vẫn coi là chưa xác minh.

**ID tentative chuyển thành native ID gây HOLD:** policy `OVERLAP_CANDIDATES_CHANGED` có thể kích hoạt ngay khi người chạm nhẹ ở mép bbox đổi ID theo lifecycle tracker. Test tổng hợp với tracker thật tái hiện HOLD ở frame 21. Sửa chỉ cho phép xác minh lại trong cửa sổ ban đầu khi các gate geometry/association đã qua; không reset thời hạn mỗi lần ID đổi. Các candidate che nhiều, IoU cao, merge/split hoặc temporal yếu vẫn HOLD.

Ngoài ra grace cũ **0,35 s** nhỏ hơn ReID p95 phiên mới **0,456 s** cộng một chu kỳ detection khoảng **0,187 s**. Grace mới **0,70 s**, đồng thời yêu cầu evidence age giới hạn 1,5 s và temporal đáng tin theo policy sẵn có. Đây là thời gian đợi xác minh overlap nhẹ, không phải kéo dài track mất hay cho pan theo prediction. Bắt đầu overlap chủ động yêu cầu ReID; score mâu thuẫn mạnh/ambiguous vẫn thu hồi quyền ngay.

### 3. Đoạn khóa 107 ms không thể kết luận là HOLD sai

`IDENTITY_BOUND` tại **10:13:33.899**, frame 2921, gắn track 33, similarity **0,8125**, margin **0,3688**. Đến frame 2925 (**10:13:34.006**) có:

- Target bbox `[475,54,551,326]`, association **0,7596**, margin **0,2080**.
- Tentative track `-50200007`, bbox `[476,55,548,252]`, confidence **0,2346**, IoU với target **0,686**, overlap theo bbox nhỏ **1,0**.
- Track 22 cũng chạm một phần target. GMC confidence **0,990**, chuyển động ảnh được phân loại LOW_IMAGE_MOTION.

Đó là bằng chứng hình học cạnh tranh đáng kể, dù có thể là detection trùng. Không có nhãn/video đồng bộ đủ để chứng minh là cùng người. **Không xóa tentative detection chỉ vì confidence thấp hoặc bbox gần nhau.** Test giữ nguyên HOLD cho geometry này. “Khóa 107 ms” chứng minh state oscillation, chưa chứng minh không có lý do an toàn.

Một bất nhất code riêng được sửa: trước đây xác nhận initial/recovery có thể dựa trên IoU cũ dù `temporal_reliable=False`, rồi bị chặn ngay ở frame sau. Bản sửa yêu cầu temporal hiện tại và visibility đủ trước khi bind; vẫn cần nhiều kết quả OSNet độc lập và giữ nguyên threshold/margin. Chưa chứng minh bất nhất này gây đoạn 107 ms cụ thể ở trên.

### 4. ReID chậm: phần đã đo được

| Stage mean (ms) | 09:49 | 10:11 |
|---|---:|---:|
| Queue wait | 0,93 | 8,11 |
| Preprocess, gồm input transfer hiện tại | 13,94 | 20,12 |
| OSNet host-wall inference | 44,36 | 68,25 |
| Postprocess | 1,89 | 1,89 |
| Gallery match | 1,61 | 5,43 |
| Normalization | 2,41 | 5,55 |
| ReID total | 144,83 | 324,43 |

Trong cửa sổ trace còn giữ, mean crops/job tăng **4,06 → 4,94**, normalized crops **3,74 → 4,03**. Không cùng cảnh/cửa sổ, nên đây là workload evidence, không phải thí nghiệm nhân quả. Một lượt score có thể chạy cả crop gốc và normalized; tổng stage không cộng trực tiếp vì nested scheduling và số batch khác nhau.

Code bản identity baseline score toàn bộ eligible persons cả khi ổn định; bản hybrid có candidate-aware filtering. Sửa đưa filtering vào stable verification: giữ target và mọi ứng viên giao nhau/hình học liên quan; lúc uncertainty/loss vẫn tìm toàn cảnh. Không spatially loại candidate re-entry. Bản sửa cũng chuyển export embedding về CPU theo batch thay vì từng candidate.

Thêm span snapshot, worker scheduling, result age, crop quality và embedding/reference export. Microbenchmark bật CUDA event profiling; CPU-wall và GPU execution không bị gọi chung một loại độ trễ. Chưa cô lập hoàn toàn phần H2D transfer khỏi preprocess, chưa chạy contention YOLO/GUI/live RTSP; không quy toàn bộ chênh lệch 145→324 ms cho threading hay GMC.

### 5. GMC và tracking

Giữ BoT-SORT, Kalman XYWH, gate GMC finite/quality/scale/rotation và native prediction hiện có. Test cùng cached detections với GMC on/off cho thấy GMC thêm overhead đáng kể nhưng chưa có ground truth chứng minh gây đổi người. Không tự động tắt GMC. Trên monitor, native selection time như nhau ở baseline on/off; trên raw, selected time cũng như nhau nhưng invocation count khác. GMC applied không đồng nghĩa GMC đúng trong mọi frame.

## C. Thay đổi code và safety

| File | Thay đổi |
|---|---|
| `ptz/ai/identity_manager.py` | Diagnostics và aggregates; lưu evidence overlap; dùng native prediction chỉ khi temporal đáng tin; hysteresis hữu hạn cho overlap nhẹ/đổi ID competitor; kiểm tra motion trước bind; candidate-aware stable scheduling; clock-age/future-frame checks. |
| `ptz/ai/reid_target.py` | Bulk CPU embedding export cho nhánh optimized; thêm timing quality/export, không đổi model hoặc fusion/threshold. |
| `ptz/config.py` | `PTZ_IDENTITY_FIX`, `PTZ_IDENTITY_REID_OPTIMIZED` mặc định 0; grace overlap nhẹ 0,70 s khi fix bật. |
| `ptz/tools/identity_replay.py` | Năm nhánh ablation cùng frames/detections; cố định RNG GMC mỗi nhánh; candidate workload, tracker timing, native fragmentation/ID changes khi có labels; incorrect selection metric. |
| `ptz/tools/identity_regression_scenarios.py` | Bảy kịch bản có nhãn tổng hợp, dùng tracker thật và score giả lập. |
| `ptz/tools/identity_reid_benchmark.py` | Benchmark OSNet thật, cùng frame, warmup, thứ tự nhánh luân phiên, GPU profiling; không tạo controller. |
| `ptz/test/test_identity_regression.py` | 13 test mới cho overlap, candidate churn, scheduling, freshness, module config, aggregate và recovery. |

[Patch](changes.patch), [hash manifest code](changes_manifest.json). Các báo cáo, JSON, log kiểm thử, ảnh tham chiếu và output replay nằm trong thư mục này; cache raw mới ở `ptz/logs/identity_regression_raw`. Không sửa pipeline motor, pan/tilt, zoom ratios/boundaries, autofocus, face recognition, GUI, weights hoặc dependencies.

Diagnostics chi tiết khi bật `PTZ_IDENTITY_DIAGNOSTICS=1`: frame/decision time, target UID, active/candidate IDs, observed/predicted bbox, tuổi track/update gap, temporal consistency, IoU/margin, overlap geometry, raw reference/gallery/final score, result age, GMC scene/target, previous/next selector state, control reason, session generation. Trạng thái selector tại decision không thay thế event state-machine/PTZ sau đó. Mặc định chỉ log transition/interval; aggregate denial vẫn tính mọi decision.

Giữ một slot async, immutable snapshots, epoch/session/generation/order checks. Không nhận bbox cũ từ ReID. Prediction không authorize PTZ. Strong appearance contradiction vẫn HOLD và rollback gallery; genuine ambiguity vẫn HOLD; reacquisition vẫn cần nhiều confirmations và xoay session. Target UID/gallery/history giữ qua loss; không coi native ID là danh tính. Không bỏ STOP hoặc stale-session guard.

## D. Kiểm thử và đối chứng

- Trước sửa hành vi, diagnostics-only: **33 tests pass** (`diagnostics_tests.log`).
- Identity + regression: **46 tests pass**; regression mới **13 tests**.
- Integration phạm vi identity/ReID/face/zoom/GMC với flags fix+optimized bật: **126 tests pass** (`final_integration.log`).
- Toàn bộ runner: **634 tests, 632 pass, 2 errors** do thiếu `gi`: import `test_gstreamer_cpu` và RTSP-backoff integration trong `test_runtime_architecture`. Đây là hai lỗi môi trường đã có trước sửa, không ghi nhận là pass.
- Compile các file Python thay đổi thành công. Không phát lệnh camera vật lý.

### Video thật, chưa có nhãn identity

A = ByteTrack cũ; B = identity baseline + GMC; C = baseline không GMC; D = sửa identity, chưa tối ưu workload; E = sửa identity + tối ưu workload.

Monitor: `16_11(1).mov`, đoạn 113–190 s, 385 observations, khoảng 5 FPS. Cache có sẵn cùng YOLO/OSNet evidence. Đây là recording màn hình, không phải raw RTSP.

| Nhánh | Selected time (s) | Loss | ReID calls | Candidate evaluations | Tracker mean (ms) |
|---|---:|---:|---:|---:|---:|
| legacy | 16.815 | 1 | 187 | 280 | 0.93 |
| identity_baseline | 10.209 | 3 | 131 | 196 | 20.45 |
| identity_no_gmc | 10.209 | 3 | 137 | 205 | 2.65 |
| identity_corrected | 5.405 | 2 | 132 | 197 | 21.21 |
| identity | 5.405 | 2 | 132 | 196 | 20.66 |

Raw: `ptz/test/recordings/camera_20261001_103938.mp4`, 2448×2048, file FPS 25; đoạn 10–30 s lấy 5 FPS, 100 observations. Đã xem frame để phân biệt camera trực tiếp với quay màn hình. OSD có ngày khác tên file; không coi đây là video đồng bộ phiên 16/11. Ảnh tham chiếu là crop người áo sáng/tối ở giữa frame tại 10 s (`raw_reference.jpg`), dùng giống nhau cho mọi nhánh; không phải tự động gán ground truth toàn video.

| Nhánh | Selected time (s) | Loss | ReID calls | Candidate evaluations | Tracker mean (ms) |
|---|---:|---:|---:|---:|---:|
| legacy | 4.800 | 1 | 75 | 98 | 1.02 |
| identity_baseline | 4.200 | 1 | 44 | 68 | 28.19 |
| identity_no_gmc | 4.200 | 1 | 50 | 81 | 4.65 |
| identity_corrected | 4.200 | 1 | 44 | 68 | 32.96 |
| identity | 4.200 | 1 | 44 | 64 | 30.23 |

**Selected time không phải correct identity time.** Cả hai video chưa có annotation: incorrect duration, identity switches, false reacquisition và ground-truth native fragmentation đều chưa biết. Monitor sau sửa **5,405 s**, thấp hơn baseline **10,209 s**; raw giữ **4,2 s**. Do đó không tuyên bố cải thiện continuity thực tế. Thay đổi cadence/gating có thể ảnh hưởng chuỗi confirmations; cần gán nhãn trước khi quyết định nhánh nào đúng hơn. Các số này cũng không đại diện đầy đủ phiên live 10:11.

Cached replay chờ job trước frame tiếp, loại được sự khác nhau của detector nhưng không tái tạo GPU contention live. Các native ID changes chỉ được tính khi có nhãn và match bbox đủ IoU, không đồng nhất với đổi người thật. Unnecessary/legitimate HOLD để null khi không có đánh giá nguyên nhân; target vẫn nhìn thấy không tự động làm HOLD thành “unnecessary”.

### Kịch bản tổng hợp có nhãn

Bảy kịch bản: rotation/similarity dip, overlap nhẹ, crossing, ambiguous candidates, full occlusion/re-entry, image boundary, pan+zoom ảnh tổng hợp. So baseline identity với corrected+optimized:

- **Weak contact: correct selection 3,6 → 9,2 s**, mất quyền 1 → 0 lần. Điểm OSNet scripted, không phải độ chính xác model.
- Rotation dip **9,2 → 9,2 s**; crossing **6,4 → 6,4 s**; ambiguity **5,4 → 5,4 s**; occlusion/re-entry **7,0 → 7,0 s**; boundary **6,9 → 6,9 s**; pan/zoom **9,2 → 9,2 s**.
- Corrected có **0 incorrect selected seconds và 0 identity switches** trong cả bảy kịch bản. Không suy rộng kết quả này cho người thật/cảnh đông.
- Unit tests bổ sung partial overlap/occluder, confidence dips, stale future/session/reference reset, tracker new ID, bounded prediction, failed/NaN/rotation GMC, scale+translation, face/zoom integration và không dùng prediction để điều khiển.

### OSNet thật, workload microbenchmark

20 cùng frames có nhiều candidate từ raw cache, model warm, thứ tự all/local luân phiên; trung bình **2,3 → 1,0 crops**. Workload anchor là bbox lớn nhất để tái lập phép đo, **không phải ground-truth target**.

- `all_candidates`: mean **103.19 ms**, p95 **143.28 ms**.
- `local_candidates`: mean **52.19 ms**, p95 **62.05 ms**.

Đây là OSNet + crop/normalization/gallery + snapshot/async scheduling, không có YOLO/GUI chạy đồng thời. Không dùng kết quả này để hứa FPS live hoặc nói phiên 10:11 đã được giảm latency tương ứng. Không thay model/precision hoặc hạ threshold.

## E. Rủi ro còn lại và điều kiện nghiệm thu

1. **Thiếu ground truth real video** là hạn chế chính. Bản sửa cải thiện lỗi tổng hợp đã tái hiện nhưng chưa chứng minh real continuity. Không promote mặc định.
2. Tentative boxes trùng có thể gây HOLD nhiều; chưa đủ chứng cứ để xóa bằng heuristic IoU/confidence. Cần đánh dấu cụ thể các frame 2921/2925 trong video đúng phiên.
3. GMC overhead khoảng hàng chục ms vẫn còn; quality estimator không đảm bảo association đúng. Chưa có so sánh camera chuyển động thật với nhãn đủ để quyết định tắt/đổi thuật toán.
4. Global search khi mất mục tiêu vẫn tốn nhiều OSNet; normalize có thể nhân workload. Stable candidate filtering có thể bỏ qua người giống target ở xa, nhưng remote candidate không được phép trở thành target chỉ vì gần/giống; mọi rebind/loss vẫn quay lại global confirmation.
5. Weak-overlap grace được chọn từ số đo một phiên, cần kiểm tra dưới tải cao. Nếu evidence không đến kịp thì vẫn HOLD; không nới grace vô hạn.
6. Stale-session STOP giữ nguyên; chưa đo physical motor synchronization/stop bằng telemetry. Lỗi WebSocket 09:54:39 là vấn đề transport riêng; không sửa transport trong task này.
7. Driver/gi môi trường chưa sửa. Microbenchmark không chứng minh tránh starvation trong toàn pipeline; cần đo đồng thời detection FPS, GUI FPS và queue age ở phiên có giám sát.

Quy trình test camera có giám sát (chưa chạy): ghi raw stream và log cùng clock; chọn cùng ảnh tham chiếu; gán nhãn target/other người; kiểm tra cố định trước rồi mới bật pan/zoom qua UI trong khu vực thử; thực hiện đi bộ/quay người/crossing/occlusion/biên/re-entry; dừng thử nếu chuyển sai người. So đúng/người sai/HOLD hợp lệ/không cần thiết với baseline trên cùng nguồn; chỉ chấp nhận khi continuity tăng mà false switches không tăng ngoài tiêu chí đã thống nhất. Giữ emergency STOP và giới hạn zoom/focus hiện có.

## F. Lệnh chạy và rollback

Từ project root:

```bash
cd /mnt/files/Project/ptz
conda activate py
```

Bản sửa thử nghiệm:

```bash
PTZ_IDENTITY_TRACKING=1 PTZ_IDENTITY_FIX=1 PTZ_IDENTITY_REID_OPTIMIZED=1 python ptz/main.py
```

Identity baseline trước sửa:

```bash
PTZ_IDENTITY_TRACKING=1 PTZ_IDENTITY_FIX=0 PTZ_IDENTITY_REID_OPTIMIZED=0 python ptz/main.py
```

ByteTrack cũ/mặc định:

```bash
PTZ_IDENTITY_TRACKING=0 python ptz/main.py
```

Diagnostics từng decision:

```bash
PTZ_IDENTITY_TRACKING=1 PTZ_IDENTITY_FIX=1 PTZ_IDENTITY_REID_OPTIMIZED=1 PTZ_IDENTITY_DIAGNOSTICS=1 python ptz/main.py
```

Replay cùng dữ liệu, không điều khiển camera:

```bash
python ptz/tools/identity_replay.py --cache ptz/logs/identity_replay_nov16 --ablations --out /tmp/identity_monitor.json
python ptz/tools/identity_replay.py --cache ptz/logs/identity_regression_raw --ablations --out /tmp/identity_raw.json
python ptz/tools/identity_regression_scenarios.py --out /tmp/identity_scenarios.json
python ptz/tools/identity_reid_benchmark.py --cache ptz/logs/identity_regression_raw --min-candidates 2 --out /tmp/identity_reid_benchmark.json
```

Tạo lại raw cache với cùng reference:

```bash
python ptz/tools/identity_replay.py --video ptz/test/recordings/camera_20261001_103938.mp4 --cache /tmp/identity_raw_cache --reference ptz/docs/tracking_regression_20261116/raw_reference.jpg --device 0 --start 10 --end 30 --sample-fps 5 --source-kind raw_camera --ablations --out /tmp/identity_raw_fresh.json
```

Test và tổng hợp log:

```bash
PTZ_IDENTITY_FIX=1 PTZ_IDENTITY_REID_OPTIMIZED=1 PYTHONPATH=ptz:ptz/test python -m unittest test_identity_tracking test_identity_regression test_reid_target test_pipeline_identity_zoom test_face_identity test_camera_motion -q
python ptz/tools/run_unit_tests.py
python ptz/tools/identity_report.py ptz/logs/latency/latency_trace_RUN_TIMESTAMP.jsonl* --out /tmp/identity_metrics.json
```

Thay `RUN_TIMESTAMP` bằng timestamp phiên cần đọc; đọc cả rotation. Summary CSV nằm cạnh trace. Muốn đo accuracy trên video, dùng `annotate_identity_replay.py` rồi truyền `--annotations` cho replay. Full restore dùng đúng các lệnh ở phần A, không dùng reset/clean/force checkout.

### Nguồn

- [Báo cáo live hai phiên](../ai_ptz_latest_log_review_20261116_101152.md).
- [Bằng chứng log trích xuất](baseline_evidence.json): suspicious frames, workload, stage means và số denial samples.
- [Monitor A–E](replay_monitor.json), [raw A–E](replay_raw.json), [synthetic suite](scenarios.json).
- [OSNet benchmark](reid_benchmark_crowded.json), stage traces/CSV trong `reid_profile/latency/`.
- [Test integration cuối](final_integration.log), [toàn bộ tests](all_tests.log), [diagnostics-only tests](diagnostics_tests.log).
