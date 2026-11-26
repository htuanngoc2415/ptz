# Profiling pipeline PTZ: đo trước, tối ưu sau

Benchmark RTSP CPU/NVDEC thực tế và breakdown trước FrameManager: [audit 22/10/2026](rtsp_decoder_audit_20261022.md) · [HTML](rtsp_decoder_audit_20261022.html). Probe độc lập, không điều khiển motor hoặc thay pipeline production.

Điều tra ảnh hiển thị chậm 5–10 giây, phép đo drift RTSP thật và so sánh `phanmem_v1.0`: [camera_stream_review.md](camera_stream_review.md). Receive→GUI không bao gồm độ trễ trước khi frame ra khỏi libmpv; công cụ `tools/stream_diagnostics.py` đo riêng đoạn này bằng tiến triển PTS, không suy diễn sensor latency.

## Chạy một lệnh

Từ root repository:

```bash
python3 tools/profile_pipeline.py run --scenario P1
```

Launcher dùng `run_camera.sh` và Python camera hiện có (`PTZ_PYTHON` nếu được đặt). Chọn ảnh target, **chủ động bật Auto Zoom / Auto Focus trên GUI** rồi vận hành như bình thường. Đóng GUI để drain trace và tự xuất `logs/pipeline_profile/<thời_gian>_<id>/report.html`, `report.json`, `latency.csv`, `manifest.json`. HTML chạy offline, không CDN, có chọn session, timeline theo worker, zoom/pan/hover, phân bố latency, state/reject và biểu đồ FPS/CPU/GPU. `--scenario P2` hoặc `P3` đổi nhãn phiên; không đổi điều khiển.

Lệnh này chỉ bật sink profiling; không bật motor, không thay config/model/threshold. Auto Zoom và Auto Focus vẫn mặc định OFF như production. Không chạy launcher/camera trong quá trình triển khai này. Timestamp nguồn là monotonic của tiến trình; wall time chỉ để đọc.

Đọc log đã ghi, tự nạp cả `.jsonl.1`, `.2`, `.3`, sắp xếp theo monotonic trong từng run:

```bash
python3 tools/profile_pipeline.py report logs/latency/latency_trace_20261020_133204_171028.jsonl --output logs/pipeline_profile/historical_20261020_133204
```

Có thể truyền nhiều trace/thư mục; các run không bị ghép clock. Không trỏ vào thư mục chứa cả bản sao và bản gốc của cùng một trace. CLI chuẩn cũ `tools/latency_report.py` vẫn hoạt động.

Replay video dùng lại adapter offline của `tools/modular_benchmark.py`, không tạo một pipeline benchmark thứ hai:

```bash
python3 tools/profile_pipeline.py video --video /path/recorded.mp4 --target /path/target.jpg --scenario P3 --frames 300
```

Dùng `--python /path/camera/python` nếu Python hiện tại không có thư viện/model. Adapter chạy YOLO, OSNet, tracking, SCRFD, ArcFace hiện có và chặn network. Đây là đo module trên video, **không tái tạo scheduler song song hay state/motor của production**. Motor arrival, image settling do motor, focus movement và E2E production = N/A trong replay. Phải dùng trace live để đo những mục này. Các nhãn P1/P2/P3 không tự chứng minh scenario đã diễn ra hay đã thành công.

## Audit cơ chế hiện có

| Thành phần | Có sẵn | Bổ sung trong thay đổi này |
|---|---|---|
| `telemetry/latency.py` | Queue bounded 8192, writer riêng, rotating JSONL/readable/camera log, counter, p50/90/95/99 cửa sổ 4096, contextvars, span, CUDA event opt-in | Run/scenario, schema thống nhất, `start_ns`/`span_id`, observed state dwell, lấy mẫu overhead, host utilization ngay trên writer |
| `telemetry/zoom_trace.py` | Sink zoom riêng bất đồng bộ, command/session summaries, phân biệt ACK/motor/visual | Giữ nguyên, tiếp tục nhận qua `latency.event`; không tạo writer zoom mới |
| `event_logging.py`, `telemetry/events.py` | FileHandler đồng bộ cho log cũ và structured events | Bridge structured events vào queue sẵn có, prefix `STRUCTURED_`; giữ log cũ để không thay vận hành |
| `FrameManager` | Frame ID, receive age, mailbox wait, unknown capture/decode, receive count | Tận dụng; bổ sung thống kê frame bị thay thế khi refresh trước inference và RTSP reconnect ở GUI adapter |
| YOLO | Predictor preprocess/inference/postprocess + total | Giữ nguyên instrumentation |
| OSNet | Crop, preprocess, inference, match | Thêm postprocess (validation/output accumulation); normalize vẫn thuộc inference như cũ |
| SCRFD / ArcFace | Inference, face ROI/align/quality/match | Thêm preprocess/postprocess, call count từ event START thực tế |
| Scheduler | Queue wait, stale skip, ưu tiên detection → face → recognition | Owner/waiters snapshot, resource hold span, camera transaction span và queue overflow |
| Tracking / ReID | Candidate, lock, loss, reacquire, occlusion structured events | Association/selection/verification span, binding generation, state transitions; binding change không được gọi là ground-truth ID switch |
| Zoom | Plan/decision, latest mailbox, RPC sent/ACK/error, motor position polling, visual stability, AF wait | Guard dwell, replaced observations; tái sử dụng toàn bộ evidence cũ |
| Focus | Request, worker start/end, fresh-frame confirmation, quality result | CNN, motor polling loop/result, post-return quality wait và từng quality validation |
| GUI | Timer render, latency labels | Frame-consistent receive→Tk submission, unique/submission FPS, render span; scanout vật lý N/A |
| Reports / replay | CSV console, zoom summary, modular runner và GroundTruth | Offline report dùng lại `distribution`, `public_value` và adapter replay; không thay tests/acceptance của runner cũ |

Source inventory tại `logs/pipeline_profile/source_inventory.json` lưu đường dẫn, SHA256 và symbol của source Python; thư mục code cũ/ZIP/model binary không phải pipeline đang chạy. `.git` của workspace không cung cấp repository Git khả dụng; không có commit/hash Git được suy diễn.

Log cũ đồng bộ và sink zoom vẫn có chi phí; chưa thay chúng bằng một framework khác. Không bật CUDA synchronization mới. Các span PyTorch là host wall time: kernel launch có thể bất đồng bộ, kiểm tra tensor/copy ở postprocess có thể trả chi phí GPU trước đó. Không gọi host wall time là GPU kernel execution time. ORT run nhận output trên host có ranh giới khác PyTorch.

## Correlation và mốc E2E

Event mới có `timestamp_monotonic` (giây), `monotonic_ns`, `frame_id`, `session_id` (alias `lock_session_id`), `track_id`, `track_generation` khi có; thiếu dùng null. `run_id` tách từng lần cấu hình profiler. `track_generation` tăng mỗi lần lock; `face_generation` giữ token invalidate riêng của face worker. Context được truyền qua FaceJob, zoom/focus context và camera worker hiện có. Event camera thủ công hay resource toàn tiến trình không có target được để null.

Không ghép theo thời gian gần nhất. Log legacy chỉ được nối pre-lock perception bằng acquisition ID hoặc frame ID khi duy nhất. Lệnh recovery/manual không có session **không** bị gán cho lock tương lai chỉ vì chung frame. `track_generation` của legacy giữ null, không dựng lại từ một counter giả.

Mặc định origin là **receive timestamp của frame ứng viên ReID đầu tiên được chấp nhận** (`TARGET_FIRST_OBSERVED.observed_ns`). Target có thể đã xuất hiện trước đó: appearance thực N/A. E2E = `IDENTITY_CONFIRMED.monotonic_ns - origin_ns`. Các mốc YOLO, lock, zoom dispatch, motor arrival, visual settling, framing, face, focus, identity nằm trên cùng trục timestamp. Face có thể trước first zoom; identity có thể trước focus. Không cộng thời gian các nhánh, không cộng p90.

Nếu đã annotate đúng frame target xuất hiện bằng video/quan sát, có thể cung cấp:

```json
[
  {"run_id": "RUN_ID lấy từ report.json", "session_id": "lock_...", "appearance_frame_id": 123}
]
```

```bash
python3 tools/profile_pipeline.py report /path/trace.jsonl --annotations appearance.json --output logs/pipeline_profile/annotated
```

Annotation phải trỏ đúng một `FRAME_RECEIVED` của run. Khi đó origin là receive của frame annotate, vẫn **không phải sensor timestamp**. Nhãn identity thật/ID switch cần ground truth; `GroundTruth` của modular runner đã hỗ trợ, không suy diễn từ MATCH/similarity.

## Ý nghĩa các khoảng chờ

- Queue wait: timestamp enqueue → dequeue/grant, là đo thực. Resource hold: grant → release trên cùng scheduler, gồm host work trong lease, không đồng nghĩa GPU utilization. Owner snapshot ở enqueue chỉ là ảnh chụp tại thời điểm đó; dùng span overlap để điều tra contention.
- `GATE_START/END`: khoảng giữa hai lần quan sát state/reason. Các lane target state, stale/watchdog, occlusion/missing, zoom policy/dispatch guard, acquire interval, focus state độc lập. Chúng có thể overlap và có thể bao gồm công việc hữu ích của worker. Không được gọi toàn bộ dwell là thread bị block hay critical-path delay.
- `STALE_TRACK`, `TRACK_MISMATCH`, các reject/skip, timeout, target loss và recovery giữ nguyên lý do production. Reject đơn lẻ chỉ cho count; không tự gán thời lượng. Gate chưa đóng ở EOF là censored, không lấy EOF trừ start để giả thành thời lượng hoàn tất.
- RPC ACK chỉ chứng minh server trả lời. Zoom arrival dùng evidence position telemetry hiện có. `zoom_motor_duration_ms` có độ phân giải polling. Visual settling là tiêu chí hình ảnh hiện có, không chứng minh motor vật lý đứng tuyệt đối.
- `focus_motor_loop_ms` gồm control/poll/RPC của `chay_toi`, arrival là trong dung sai production 1024. `focus_quality_wait_ms` bắt đầu sau controller trả về đến quality confirmation/timeout/abort. Đây không phải ACK đầu tiên của RPC; từng ACK đã có `FOCUS_RPC_RESPONSE`.
- `dropped_frames` hiện có là gap ở mailbox consumer, không phải network drops. `superseded_detection_frames` là frame đã lấy nhưng bị thay ở refresh trước inference. `dropped_face_jobs`, `dropped_zoom_observations`, `dropped_camera_jobs` phân biệt với `dropped_log_events`.
- GUI FPS là số frame ID khác nhau được submit cho Tk; cùng frame được vẽ lại không làm tăng unique FPS. Receive→Tk không bao gồm timestamp sensor/RTSP trước appsink hay display scanout.
- CPU/RSS đọc `/proc`; GPU đọc các sensor Jetson đã biết đơn vị, không chạy `nvidia-smi`/`tegrastats` hay đồng bộ CUDA. Không đọc được thì null/N/A. Samples theo run, không attribution CPU/GPU cho từng target.

## Protocol P1/P2/P3

| Scenario | Thao tác người vận hành | Evidence cần xem |
|---|---|---|
| P1 | Một target đứng yên; chọn target, bật điều khiển chủ động, để đi qua tìm → lock → zoom → framing → nhận diện | Candidate→identity, lock→first movement, ACK/arrival/visual riêng, chất lượng/confirm; phiên chưa đạt identity giữ N/A |
| P2 | Target di chuyển liên tục sau lock, giữ điều kiện camera/model cố định | FPS/age/drop theo thời gian, scheduler starvation, association/verification, target binding và reacquisition; annotate ID thật nếu đánh giá switch |
| P3 | Một người khác che target trong lúc zoom, sau đó rời khỏi vùng che | Occlusion/target missing, guard/reject/stop, stale track/mismatch, generation, recovery→reacquire; annotate thời điểm và người đúng |

Chạy từng scenario vào thư mục riêng, giữ cả phiên không hoàn tất. Khuyến nghị lặp ≥3 lần cho mỗi điều kiện và ghi thời gian/ánh sáng/khoảng cách do người vận hành quan sát. Không tự giảm threshold hay đổi model để có kết quả đẹp.

## Overhead và giới hạn dữ liệu

```bash
python3 tools/profile_pipeline.py overhead --output logs/pipeline_profile/overhead_local
```

Đây là microbenchmark enqueue/drain **trên host chạy lệnh**, không phải benchmark camera/model. Kết quả triển khai: 10.000 calls, p50 disabled 0,00248 ms, enabled 0,02157 ms; p99 enabled 0,04807 ms. Burst làm rơi 1.807 log events; số này chứng minh giới hạn queue, không phải dropped frame của camera. CPU gồm drain: disabled ~0,034 s, enabled ~1,819 s. Không ngoại suy thành % overhead E2E. `pipeline_ab_overhead_percent` để null; cần các lượt ON/OFF cùng input/workload trên thiết bị camera để đo phần này.

Profiler live lấy mẫu chi phí producer khoảng 1/128 events trong `profiling_emit_sample_ms` (cửa sổ bounded); không gồm toàn bộ writer/legacy logging cost. CPU process gồm cả writer. JSON serialization và file I/O của phần bổ sung nằm trên writer; producer chỉ timestamp/counter/context/queue. Giữ rotation bounded hiện có (mặc định 8 MB × 4 file), nên phiên dài có thể mất đầu timeline. Report đọc retained trace, không lấy counter tổng để giả lại event đã mất. p50/p90/p99 trong HTML/CSV tính trên **retained raw event samples**; summary live dùng recent window, hai scope khác nhau.

Khi chỉ có reject không có begin/end, thiếu generation/motor/focus/utilization, mất đầu trace, hoặc stop giữa chừng: hiển thị N/A và xem provenance. Ranh giới sensor, RTSP decoder nội bộ và physical display chưa có clock đáng tin cậy nên luôn N/A.

## Kết quả và kiểm chứng trong repository

Báo cáo thực: `logs/pipeline_profile/historical_20261020_133204/report.html` và phiên đối chiếu `historical_20261020_134327/report.html`. Phân tích chi tiết, ranking và đề xuất chưa áp dụng: [pipeline_profiling_findings.md](pipeline_profiling_findings.md).

37 tests đã PASS; kiểm tra JavaScript và logic UI trên 8 mục chọn session bằng DOM/canvas stub cũng PASS. Tests chỉ tập trung measurement/correlation, queue/thread ownership, face result async và focus settling có thay instrumentation. Không chạy lại toàn bộ kiểm thử chức năng, không gọi camera/motor. Synthetic fixtures của unittest nằm trong thư mục tạm, không nhập báo cáo camera. AST của SCRFD, ArcFace, OSNet sau khi bỏ timing wrappers giống bản trước chỉnh sửa; model/threshold/config production không đổi.

## Khi startup báo CUDA out of memory

Nếu lỗi tại `runtime/device.py::resolve_device`, ứng dụng chưa tới bước tạo GUI/profiler và chưa chạy model pipeline. Report có `events=0` không phải kết quả P1. Launcher ghi `FAILED_NO_TRACE` cùng exit code; HTML hiển thị N/A.

Kiểm tra RAM bằng `free -h`, đóng các phiên IDE/ứng dụng không cần thiết trên thiết bị rồi chạy lại cùng lệnh. Không tự đổi model, threshold, allocator hoặc chuyển CPU để lấy benchmark GPU. Nếu còn OOM, cần kiểm tra tiếp môi trường CUDA và bộ nhớ của host; chưa thể quy nguyên nhân chỉ từ số RAM khả dụng.

`run_camera.sh` tìm `$HOME/miniconda3/envs/Autofocus_Camera/bin/python`, nếu không có thì dùng `python3` trong PATH. Dòng “Python đang dùng” trong lỗi cho biết interpreter thực tế. Chỉ đặt `PTZ_PYTHON=/đường/dẫn/python` khi đã xác minh môi trường đó tồn tại; thông báo gợi ý Conda cũ không chứng minh môi trường vẫn còn.

Nếu GUI/RTSP đã mở nhưng log có `Model initialization ERROR` (ví dụ ArcFace/cuBLAS), AI worker có thể đã dừng trong khi video vẫn tiếp tục. Đóng cửa sổ camera lỗi trước khi chạy lại; thay target hoặc bật Auto Zoom không khởi tạo lại worker. Report ghi `MODEL_INIT_FAILED`, launcher ghi `AI_PIPELINE_FAILED` và trả mã 3 khi tiến trình kết thúc bình thường nhưng trace có lỗi AI. FPS video/GUI không được dùng để kết luận AI hay P1 thành công. Report chỉ phản ánh các event còn được giữ trong trace.
