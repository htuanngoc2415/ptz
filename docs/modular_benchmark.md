# Modular Testing & Benchmark Framework

Runner: `python3 tools/modular_benchmark.py`. Chạy từ root repository bằng môi trường AI đã cài. Không cài model, không sửa threshold/config production, không khởi động ứng dụng chính. Các adapter gọi lại code production; tests dùng mock firmware/camera đã có.

## Bắt đầu và trạng thái

```bash
python3 tools/modular_benchmark.py list
python3 tools/modular_benchmark.py run --stage 0 --case unit
python3 tools/modular_benchmark.py run --stage 5 --case unit --test-filter track
python3 tools/modular_benchmark.py run --stage 6 --case mock --repeat 3
python3 tools/modular_benchmark.py run --stage all --case unit
```

Mỗi lần chạy tạo thư mục riêng trong `logs/modular_benchmark/`. Có `report.html`, `report.csv`, `latency.csv`, `report.json`, `manifest.json`; mỗi case có kết quả, timeline HTML/CSV, JSONL hoặc từng test ID/traceback. Report được cập nhật sau mỗi case. CLI không tự chuyển sang stage khác: chỉ chạy stage đã chọn. Dùng `--stage 2,3,4` để chủ động chạy nhiều stage tuần tự. `--repeat N` lặp trong tiến trình riêng; không dùng lại state/model giữa các lần.

- `PASS`: các assertion của case unit/mock, hoặc **toàn bộ metric gate do người dùng cung cấp**, có đủ bằng chứng và đạt. Không có nghĩa toàn stage đạt ngoài thực địa.
- `FAIL`: assertion, inference, decode, timeout, hoặc metric gate thất bại. Không tự sửa hoặc hạ threshold.
- `NOT_RUN`: thiếu dependency/input/nhãn/ngưỡng acceptance, test bị skip, hoặc cần người thao tác. Benchmark có thể đã đo thành công: xem `execution_status: PASS`, `metrics` và `reason`; acceptance vẫn `NOT_RUN`.
- `NOT_IMPLEMENTED`: adapter/chức năng chưa có. Không có kết quả giả lập thành công thay cho implementation.
- Exit code: `0` tất cả case PASS; `1` có FAIL; `2` có NOT_RUN/NOT_IMPLEMENTED hoặc CLI input không hợp lệ.

`--test-filter` lọc substring tên test được liệt kê trong `tests.json`; không khớp test nào là NOT_RUN. Unit được chạy theo allowlist, không discover các script camera tương tác. Offline Python chặn socket/DNS bằng audit hook. OpenCV offline chỉ nhận file local đã tồn tại. Audit hook không phải sandbox native library; các adapter không truyền URL tới decoder offline.

## Stage và test case

Mọi stage có `unit`, `trace`, `manual`. `list` in module/class test tương ứng. Tên test cụ thể, skip và lỗi được lưu riêng. Cùng test có thể được dùng ở nhiều stage; không cộng số lượt chạy thành số test duy nhất.

| Stage | Case bổ sung | Phạm vi / điều kiện |
|---|---|---|
| 0 Camera/RTSP | `replay`, online `stream`, `rpc` | Decode throughput, receive rate/mailbox drops, telemetry/RPC round-trip; reconnect được unit test bằng mock và theo dõi qua log stream thực |
| 1 Optical zoom | `mock`, online `motor` | Tái sử dụng absolute zoom benchmark, prediction, STOP/ACK/settle guard và toàn bộ suite zoom calibration |
| 2 YOLO | `replay` | `PersonDetector`, model/threshold hiện có, latency, bbox, optional bbox GT |
| 3 OSNet | `replay` | `--target` và `--detections`, hoặc modules `2,3`; cosine từng crop. Acquisition/false-match logic nằm trong unit; accuracy identity chưa có adapter GT |
| 4 Person tracking | `replay` | `PersonTracker` trên bbox input hoặc YOLO; optional GT identity để đếm ID switch/fragmentation |
| 5 Target rebinding | unit/trace/manual | Session change, TRACK_MISMATCH, STALE_TRACK, queued command/job. Chưa có API atomic rebind với `track_generation` chung trong production; không tuyên bố đã kiểm chứng tính năng đó |
| 6 Auto zoom | `mock` | Lock→zoom không cần face, recovery, oscillation, stale/ownership; ratio thực và physical settling cần trace/video có nhãn |
| 7 Face detect/track | `replay` | SCRFD full-frame hoặc ROI; tracker, association, drift/quality gate qua unit đã có |
| 8 Auto focus | `replay`, online `motor` | Replay chỉ chấm độ nét ROI bằng `FocusQualityAnalyzer`; motor chỉ đo hành trình focus thủ công. CNN target focus/occlusion cần thực nghiệm có người giám sát |
| 9 Face recognition | `replay --modules 7,9` | SCRFD landmarks→alignment→ArcFace→FaceMatcher hiện có. Best Shot/cache/event-driven qua unit; replay này không chạy scheduler FaceWorker |
| 10 GUI/Digital PTZ | `replay` | `--roi`, crop render/snapshot; overlay dùng bbox của module bật. Tk event loop/UI responsiveness cần manual |
| 11 End-to-end | `mock`, `e2e` | Mock hybrid acceptance sẵn có; `e2e` hiện NOT_IMPLEMENTED. Trace import cho production đã chạy; không gọi replay tuần tự là production E2E |

## Replay độc lập hoặc ghép module

```bash
# CPU được chọn tường minh chỉ cho tiến trình này; không đổi config/model production.
PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 0 --case replay --video test/recordings/camera_20261001_103938.mp4 \
  --frames 100 --record --repeat 2

PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 2 --case replay --video clip.mp4 --frames 100 --record

# Cùng video, không cần YOLO cho bài đo tracker độc lập:
PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 4 --case replay --video clip.mp4 --detections detections.json \
  --ground-truth people_gt.json --frames 100

PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 3 --case replay --modules 2,3,4 --target person_search/targets/target.jpg \
  --video clip.mp4 --frames 100 --record

PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 7 --case replay --video clip.mp4 --roi 100 100 400 800 --frames 100

PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 9 --case replay --modules 7,9 --video clip.mp4 --frames 100

PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 8 --case replay --video clip.mp4 --roi 100 100 400 800 --frames 100

PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run \
  --stage 10 --case replay --video clip.mp4 --roi 100 100 400 800 --record
```

Chỉ module trong `--modules` được bật; mặc định chính stage đã chọn. Stage chính phải có trong danh sách. Các dependency không được bật ngầm: ReID/tracker độc lập cần bbox input, ArcFace cần stage 7, quality/digital cần ROI. ROI là tọa độ pixel khung nguồn; ROI cố định không có nghĩa xác minh đúng target. Stage 7 ROI gọi `detect_face_inside_person` với top ratio production. Model không có file là NOT_RUN, không tải checkpoint tự động. Modules 5/6/motor không nằm trong replay; không bỏ stale/watchdog guard để làm replay chạy motor.

`--record`: `original.mp4` (decoded frames) và `overlay.mp4`, cùng số thứ tự frame, kích thước và FPS. Không phải sao chép bitstream RTSP nguyên bản. `FRAME` trong `events.jsonl` lưu `video_index` (0-based), `frame_id` (1-based), PTS nguồn, timestamp nhận và monotonic timestamp. Mapping này là chuẩn đồng bộ khi receive FPS không đều. Snapshot Digital PTZ lưu `digital_snapshot.jpg`. Recording đồng bộ có overhead, phải giữ cùng setting khi so sánh. `--pace` phát theo FPS nguồn; mặc định đo throughput nhanh nhất. Decode file không phải latency RTSP/cảm biến.

Latency model gồm call đầu/cold initialization nội bộ, không warm-up bị giấu. Model construction không nằm trong throughput loop; thời gian toàn case nằm ở `elapsed_seconds`. GPU YOLO/OSNet được synchronize ở biên adapter để call time có ý nghĩa; span production CPU-wall vẫn giữ nguyên nhãn của profiler. CPU process percent đo `process_time/wall_time`, có thể >100% khi dùng nhiều core. GPU utilization hiện null; profiler CUDA spans chỉ xuất khi production có hỗ trợ/bật đo. Không suy ra GPU contention từ CPU replay. Thời gian record/render/decode ảnh hưởng throughput. Số mẫu nhỏ không đủ kết luận p99 ổn định.

## Ground truth và acceptance

BBox GT cho primary stage 2/4/7:

```json
{"frames":[
  {"frame_id":1,"objects":[{"bbox":[100,100,200,400],"identity":"person_A"}]},
  {"frame_id":2,"objects":[]}
]}
```

Chỉ annotate class đang đánh giá (person cho 2/4, face cho 7). Danh sách rỗng là **đã kiểm tra không có object**; không có frame trong file là **chưa gán nhãn**, không bị tính FP/FN. Bbox phải cùng pixel space video gốc. Duplicate frame/identity, NaN, bbox ngược bị từ chối. Matcher one-to-one greedy descending IoU, mặc định `--iou 0.5`; báo cáo nêu matching policy, TP/FP/FN, precision/recall; không gọi đây là COCO mAP/MOTChallenge. Identity GT + prediction track_id mới được tính ID switches. Fragmentation là matched→missed trong frame được annotate→matched; chưa tính thời gian hồi phục khi không có timestamp/event đáng tin cậy. Không có identity GT thì các metric identity null.

`--detections` dùng cùng schema nhưng là **input prediction**, tuyệt đối không tự dùng làm GT. Có thể xuất bbox từ `PERSON` events của lần YOLO trước để replay tracker/OSNet; giữ đúng frame ID và hash video. Chưa có adapter GT cho ArcFace/ReID false-reacquisition; kết quả match/cosine không tự biến thành accuracy.

File `limits.json` ví dụ (người kiểm thử phải chọn ngưỡng phù hợp **trước** khi chạy):

```json
{"frames":{"min":100},"latency_ms.yolo_ms.p90":{"max":100},"ground_truth.recall":{"min":0.9}}
```

```bash
PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run --stage 2 --case replay \
  --video clip.mp4 --ground-truth people_gt.json --limits limits.json --frames 100
```

Ví dụ trên không phải ngưỡng production đã được phê duyệt. Metric không có dữ liệu → NOT_RUN; gate thất bại → FAIL; không tự hạ ngưỡng. Unit/mock PASS chỉ chứng minh assertions trong test đó.

## Trace và so sánh

```bash
python3 tools/modular_benchmark.py run --stage 11 --case trace \
  --trace logs/latency/latency_trace_SESSION.jsonl
python3 tools/modular_benchmark.py compare RUN_A RUN_B --output logs/modular_benchmark/comparison
```

Importer đọc event/metric/duration của `telemetry.latency` và zoom trace hiện có. Lưu timeline, p50/p90/p99, reject reasons, top p90 durations. Span có thể lồng nhau, vì vậy bảng này chỉ là ứng viên cần điều tra, không tự xác nhận bottleneck. JSONL hỏng bị FAIL, không silently bỏ dòng. Unknown sensor capture latency vẫn null.

Pair lifecycle chỉ tính khi cùng `(session_id/lock_session_id, track_id/target_track_id, track_generation)` và monotonic clock:

| Bắt đầu | Kết thúc | Metric ms |
|---|---|---|
| TARGET_SEARCH_STARTED | TARGET_LOCKED | target_acquisition_ms |
| TARGET_LOCKED | ZOOM_COMMAND_SENT (ptz.start, PositionABS/ZoomTele/ZoomWide) đầu tiên | lock_to_first_zoom_ms |
| TARGET_LOCKED | TARGET_RATIO_REACHED | time_to_target_ratio_ms |
| TARGET_LOST | TARGET_RECOVERED | tracking_recovery_ms |
| ZOOM_STARTED | ZOOM_SETTLED | zoom_settling_ms |
| FOCUS_STARTED | FOCUS_SETTLED | focus_settling_ms |

Chỉ phát SETTLED/RATIO_REACHED sau khi thực sự xác nhận bằng telemetry/annotation, không dùng ACK thay completion. Production chưa có trường `track_generation` chung; `FaceJob.generation` và `ZoomIntent.generation` là generation của worker/command, không được đổi tên giả làm track generation. Với trace cũ, importer vẫn thống kê metric duration có sẵn (ví dụ `target_lock_latency_ms`), nhưng không tự ghép cặp lifecycle thiếu token. Không nhận wall clock từ các máy khác như cùng monotonic clock; chỉ import một phiên/process clock có nguồn gốc xác định.

So sánh xuất HTML/CSV/JSON, median p90 giữa các repetitions và delta; kiểm tra hash input, model, config, package/platform, các tùy chọn đo. Không cùng điều kiện thì `comparable:false`, không tự kết luận regression. Thay source commit/hash được phép để so sánh code; source tree và từng file đều lưu trong manifest. Commit không đọc được ghi null/UNAVAILABLE, không giả commit. Model version dùng SHA256 nội dung. Config snapshot redacts credentials/URI; file camera gốc không được copy vào report.

## Online có người giám sát

**Chưa chạy online trong phiên triển khai.** Đóng mọi ứng dụng/vendor client khác điều khiển camera, cả máy khác. `--exclusive` là xác nhận của người vận hành; runner có lock theo camera và kiểm tra process Python local best effort. Process visibility có thể bị sandbox giới hạn; app legacy/remote không dùng lock này, nên không thể tự chứng minh độc quyền toàn mạng. Không chạy tool cũ song song. `--confirm-motor` là chấp thuận cụ thể cho movement đã chỉ định, không có default motion.

```bash
# Đọc stream production backend, không gọi motor.
python3 tools/modular_benchmark.py run --stage 0 --case stream --mode online \
  --exclusive --seconds 30 --frames 450 --record

# RPC2 read_only, close không gửi STOP/đổi config.
python3 tools/modular_benchmark.py run --stage 0 --case rpc --mode online --exclusive --frames 20

# Chỉ khi sẵn sàng giám sát: tọa độ PositionABS nội vùng đã xác minh (.15, 1).
python3 tools/modular_benchmark.py run --stage 1 --case motor --mode online \
  --exclusive --confirm-motor --position 0.5

# Đo hành trình focus thủ công bằng controller hiện có, không tuyên bố AF target thành công.
python3 tools/modular_benchmark.py run --stage 8 --case motor --mode online \
  --exclusive --confirm-motor --focus 12000
```

Motor gọi tool/controller hiện có, giữ STOP/cleanup/watchdog và các guard của đường manual diagnostic. Case motor không bị subprocess timeout cưỡng bức vì cần controller thực hiện STOP trong finally. Các case tự động target-bound chưa có online adapter: NOT_IMPLEMENTED. Không nối bypass trực tiếp RPC để giả chạy Auto Zoom/AF. Calibration target/ratio/settling đầy đủ dùng [tool calibration hiện có](../zoom_calibration/README.md) với thao tác người vận hành và nhập trace vào report. Runner không sửa file config, threshold hoặc model để đạt gate.

## Protocol thực địa và nhãn thủ công

Ghi `--scenario`, `--distance 3|7|14|15`, `--notes`; mặc định unknown, không tự đo khoảng cách/ánh sáng từ ảnh. Thực hiện riêng từng condition, ít nhất 3 repetitions khi có thể:

1. Target đứng yên rồi đi ngang/tiến/lùi, ở 3 m, 7 m, 14–15 m; một người, nhiều người, trang phục tương tự; thuận sáng/ngược sáng/ánh sáng yếu. Giữ camera, model, exposure và protocol giống nhau giữa hai phiên so sánh.
2. Gán identity thật bằng quan sát thủ công; annotate bbox và thời điểm target vào frame, lock đúng/sai. Đánh dấu frame chưa biết là không annotate. Không coi score cao là nhận đúng.
3. Che khuất ngắn/dài, hai người giao cắt, target rời rồi quay lại, đổi local track_id. Ghi session/track, event recovery, đúng/sai target. Test generation/atomic rebind hiện thiếu được giữ NOT_IMPLEMENTED.
4. Zoom khi đứng yên/di chuyển: annotate lock, first motor start, bbox-height/frame-height vào 0.46–0.54, giữ ổn định theo tiêu chí định trước; đo settling độc lập bằng telemetry/ảnh. Không lấy khoảng trễ ACK thay thời gian motor.
5. Face không detect: kiểm tra auto zoom vẫn theo person và recovery an toàn. Face stages: annotate landmarks/association, ROI drift, false match; đa góc/xa/ngược sáng. Best Shot cần người xem độ rõ và đúng identity.
6. Focus: nhiều mức zoom, ROI đúng target và vật cản; đo sharpness trước/sau, thời gian đạt ổn định và xác nhận vật thể thực được lấy nét. CNN hiện dùng ROI giữa D1; điểm nét ở person ROI không chứng minh CNN focus đúng target.
7. GUI: dùng desktop thật, annotate thời điểm input/overlay/snapshot, kiểm tra transform bbox và responsiveness. Replay headless không chứng minh Tk responsive.
8. RTSP reconnect: chủ động mất/khôi phục mạng trong môi trường kiểm thử, ghi thời điểm lỗi/first fresh frame, kiểm tra telemetry và STOP; frame/job cũ/session đổi/pending command dùng unit trước rồi mới thí nghiệm có giám sát. Không diễn giải thiếu sensor timestamp thành latency camera.
9. Toàn pipeline: chạy ứng dụng chính trên thiết bị thực, thu profiler/recording bằng tool hiện có, nhập trace. Cần measurement GPU utilization/thermal/load đồng thời để kết luận contention. `e2e` tự khởi chạy/record GUI chưa triển khai.

Những mục cần con người không tự PASS: `--case manual` luôn NOT_RUN và dẫn về protocol. Không coi video mẫu không nhãn là đã bao phủ các condition trên.

### Embedding model độc lập

```bash
# OSNet không cần YOLO/video/tracker. --frames là số lần gọi lặp ảnh.
PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run --stage 3 --case embedding \
  --target person_search/targets/target.jpg --frames 5

# ArcFace không cần SCRFD khi đã có ảnh align đúng chuẩn 112×112.
PTZ_DEVICE=cpu python3 tools/modular_benchmark.py run --stage 9 --case embedding \
  --target aligned112.png --frames 5
```

Có thể thêm `--candidate other.png` để đo cosine với reference. Ảnh ArcFace phải được align trước bằng `ai.face.face_alignment.align_face` với landmarks đã kiểm tra; runner từ chối ảnh không phải 112×112, không âm thầm resize ảnh người thành mặt. Case này đo repeated-image embedding latency, không kiểm chứng acquisition, identity cache hoặc accuracy. Không có candidate thì similarity null, không báo score 1.0 tự so ảnh với chính nó. Case replay stage 9 nếu không thấy mặt sẽ báo NOT_RUN và `unexercised_models: ["arcface"]`, dù SCRFD đã chạy.

## Kết quả triển khai ngày 21/10/2026

[Báo cáo tổng hợp HTML](../logs/modular_benchmark/summary/report.html),
[JSON](../logs/modular_benchmark/summary/report.json),
[CSV](../logs/modular_benchmark/summary/report.csv),
[so sánh hai phiên](../logs/modular_benchmark/comparison/comparison.html).
Các report con giữ nguyên manifest lúc chạy; timeline tổng hợp được xuất lại từ bằng chứng gốc. Những lần debug runner ban đầu được giữ trong thư mục log nhưng không dùng làm kết luận lỗi production.

File code/tài liệu thay đổi: `tools/modular_benchmark.py` (mới), `test/test_modular_benchmark.py` (mới), `docs/modular_benchmark.md` (mới), mở rộng `telemetry/benchmark.py`, thêm liên kết trong `README.md`. Artifact/video/ảnh align/manifest nằm riêng trong `logs/modular_benchmark/`. Không sửa model, `camera.json`, thuật toán hoặc threshold production. Đối chiếu 66 file production giữa manifest đầu và cuối: không thay đổi hash.

| Stage | Unit có sẵn qua runner | Bằng chứng bổ sung |
|---|---|---|
| 0 | 40 PASS | Replay 20 frame × 4 lần đo; online NOT_RUN |
| 1 | 29 PASS, 4 FAIL | Calibration riêng 31 PASS; mock absolute 2/2 PASS; motor NOT_RUN |
| 2 | 5 PASS | YOLO replay 20 frame × 4 lần đo, có raw/overlay |
| 3 | 42 PASS | OSNet replay 8 frame; embedding độc lập 3 lần |
| 4 | 6 PASS | Tracker độc lập 8 frame, bbox từ YOLO, không GT |
| 5 | 39 PASS | Atomic rebind case NOT_IMPLEMENTED |
| 6 | 60 PASS | Hybrid mock FAIL ở cả 2 lần lặp |
| 7 | 20 PASS | SCRFD 8 frame, không phát hiện face trong ROI mẫu |
| 8 | 22 PASS | Chấm nét ROI 8 frame; physical AF/settling NOT_RUN |
| 9 | 16 PASS | ArcFace độc lập 3 lần trên ảnh align 112×112 có provenance; không accuracy |
| 10 | 9 PASS | Digital crop/snapshot 8 frame; UI responsiveness NOT_RUN |
| 11 | 44 PASS | 21 framework tests PASS riêng; hybrid mock FAIL 2 lần; import 7.055 events; E2E NOT_IMPLEMENTED |

Các stage có test dùng lại; 336 lượt unit trong bảng không phải 336 test duy nhất. Suite gốc chạy độc lập: **310 test, 306 PASS / 4 FAIL**. Suite framework cuối: **21 PASS**. Suite calibration: **31 PASS**. Một lần regression tổng qua runner cũ cũng chạy 330 test (gồm 20 test framework tại thời điểm đó), vẫn đúng 4 FAIL cũ. Không có skip bị đổi thành PASS.

Benchmark replay/embedding đều giữ acceptance **NOT_RUN** vì chưa có ngưỡng được chọn trước/ground truth, dù `execution_status=PASS`. Không tự tạo nhãn để làm report xanh.

Các phép đo CPU mới, không có camera thật:

| Phép đo | Mẫu | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| YOLO, phiên A lần 1 | 20 | 390,40 ms | 478,86 ms | 515,86 ms |
| YOLO, phiên A lần 2 | 20 | 384,88 ms | 397,09 ms | 486,54 ms |
| OSNet embedding, ảnh lặp | 3 | 3775,24 ms | 3817,20 ms | 3826,64 ms |
| ArcFace embedding, ảnh align lặp | 3 | 309,66 ms | 311,61 ms | 312,04 ms |

YOLO throughput toàn replay có recording khoảng **1,85–1,91 FPS**. Stage 0 decode+record khoảng **7,33–7,61 FPS** dù FPS file là 25; đây là throughput pipeline ghi hình đồng bộ trên khung 2448×2048, không phải FPS camera. Mean/percentile của decode read riêng xem report; không suy ra decode là nguyên nhân từ throughput tổng. Median p90 YOLO giữa hai phiên A/B: **437,98 → 439,77 ms (+0,41%)**; cùng các điều kiện được kiểm tra, chưa có kết luận regression hay significance. Các benchmark ảnh embedding và video khác workload, không cộng latency thành E2E.

Lỗi và đề xuất (chưa sửa production/test gốc):

1. **Bốn FAIL absolute zoom là fixture chạm mép.** Test dùng bbox `(0,0,100,200)` nên bị `BBOX FRAME EDGE` chặn trước telemetry/zoom. Chỉ thay bbox vào giữa khung **trong bộ nhớ** để chẩn đoán: cả bốn test PASS. Nên sửa fixture cho các bài telemetry, giữ nguyên test/guard chống clipping riêng. Bằng chứng: [diagnostic](../logs/modular_benchmark/diagnostics/centered_bbox_in_memory.log).
2. **Hybrid benchmark cũ giả định recognition đồng bộ.** `_process()` hiện thu candidate/chạy recognition async nhưng helper đọc `take_identity()` ngay và assert. Cả stage 6/11 FAIL lặp lại. Chạy chẩn đoán chế độ baseline đồng bộ trong bộ nhớ thì PASS; không coi đó là PASS của optimized production. Nên cập nhật helper theo candidate policy/condition wait, kiểm tra kết quả stale đúng ngữ cảnh. Bằng chứng: [diagnostic](../logs/modular_benchmark/diagnostics/hybrid_sync_in_memory.log).
3. **Chưa có contract atomic rebind/track generation chung.** Có validation session/track và generation riêng từng worker/lệnh; không đủ bằng chứng để tuyên bố atomic rebind với local ID mới. Cần thiết kế/đặc tả API và fixture trước, không bỏ TRACK_MISMATCH/STALE_TRACK.
4. **CPU OSNet là thành phần tốn thời gian trong phép đo ảnh hiện tại.** Chỉ 3 mẫu trên CPU, không đại diện production GPU. YOLO CPU cũng vượt ngân sách 25 FPS file. Bước tiếp theo là đo cùng video/scene trên thiết bị triển khai với resource/thermal profiling, chưa tối ưu.
5. **Trace production cũ gợi ý kiểm tra scheduler.** Một segment ngày 20/10 có recognition queue 155,23–187,33 ms (n=2), ArcFace inference 28,21–38,25 ms (n=2); ReID total p90 182,25 ms (n=93), detection mailbox p90 107,85 ms (n=181). Các span lồng nhau và số mẫu recognition nhỏ: chưa chứng minh GPU contention hoặc nguyên nhân nhân quả. Cần trace đầy đủ cùng CPU/GPU utilization và ảnh/video có nhãn. Trace này cũng có một metric `tracking_recovery_ms=15643,93` do production phát; không tự diễn giải thành recovery đúng identity.
6. **GPU/online/accuracy chưa xác minh.** `nvidia-smi` không giao tiếp được driver trong workspace; không có GPU device node nhìn thấy. Không có lệnh camera/motor nào trong phiên triển khai. Các bài physical settling, khoảng cách/ánh sáng, recognition accuracy, UI và E2E vẫn cần protocol thực địa và GT; không tự đánh dấu PASS.
