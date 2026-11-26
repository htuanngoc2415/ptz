# Hybrid Person Search → Absolute Zoom → Face Identification: fix 2026-10-08

> Cập nhật: logic fallback về person framing đã được thay bằng sticky face phase/HOLD; xem [zoom oscillation fix](zoom_oscillation_fix.md).
## Root causes và bằng chứng baseline

Phiên camera thật 10:11:13–10:13:31 được phân tích trong
[session_20261008_101113.json](latency_audit/session_20261008_101113.json).
Có 14 lần recognition chạy xong, tuổi frame 971–1191 ms, nhưng không có identity
observation: `FaceWorker.valid()` dùng chung giới hạn motor 800 ms ở cả admission,
sau ArcFace và lúc đọc mailbox. Kết quả bị bỏ trước temporal confirmation.

Trong `InferenceWorker.ai_worker()`, `face_stop` trước đây đúng ngay khi face có mặt
nhưng state chưa chuyển sang FACE. Vì cần ba xác nhận face, person zoom bị ngắt/chặn
trong giai đoạn SEARCH_FACE. Khi mất face, state giữ FACE với `control_target=None`,
controller nhận FACE_LOST và STOP mặc dù person còn được theo dõi. Việc gửi zoom
cũng nằm sau face-result handling và image-quality work. Timestamp face cũ còn có
thể được dùng cho một quyết định zoom đang dựa trên bbox person mới.

Baseline lock → first command: 986–1803 ms. Camera queue trung bình 2.1 ms, RPC
48 ms; phần lớn khoảng chờ xảy ra trước RPC. SCRFD trung bình 233 ms, recognition
388 ms. Không cộng các nhánh song song thành latency toàn tuyến.

## State và scheduling sau sửa

```text
YOLO + tracking + OSNet temporal confirmation
    → TARGET_LOCKED
        ├→ gửi ngay person observation vào zoom supervisor
        │   → estimator → PositionABS nếu capability/mapping/range cho phép
        │   → endpoint/visual settle → HOLD hoặc bounded correction
        └→ SCRFD → association → quality
                         ├→ publish bbox mới ngay, không chờ ArcFace
                         └→ ArcFace → FaceDatabase → identity observation queue
                                            → xác nhận session/track → GUI identity
```

- `start_person_zoom_on_lock()` chạy trước face work, sharpness và GUI handling.
  Chỉ submit mailbox; không chờ RPC trong detection thread. Orchestrator vẫn chặn
  khi manual/AF/settle hoặc motor đang có tác vụ khác.
- Person ratio tính trên frame gốc; target 0.50, hold 0.46–0.54. Không dùng Digital PTZ.
  Person control dùng timestamp của person frame, không mượn timestamp face.
- Khi chưa đủ ba face observations đáng tin, person zoom vẫn hoạt động.
  Face ổn định chuyển sang FACE, target 0.25, hold 0.22–0.28.
- Giữ **chốt final-face framing có sẵn** (ngưỡng 0.18, ba observations) và brake mép ảnh;
  đây là safety override có thể HOLD sớm hơn đích face 0.25, không phải một ngưỡng
  ArcFace mới. Không thay đổi model weights, similarity hoặc quality thresholds.
- Khi face mất ngắn hạn, original person/session vẫn đáng tin: watchdog nhận fresh
  person heartbeat, không hủy lệnh absolute face đã lập chỉ vì detector miss.
  Lệnh vẫn chịu timeout, telemetry, session invalidation và manual safety.
- Qua grace `face_lost_timeout`, state fallback PERSON; không unlock/reassign target.
  Cần ba face observations mới để quay lại FACE. Pulse/person move có thể dừng vì
  **person framing đã đạt**, không vì một face miss. Không có person/track đáng tin
  thì không gia hạn watchdog; recovery/STOP giữ nguyên.
- Khi person đã ở hold band và chưa có face: `HOLD_AND_RETRY_HEAD_ROI`, additional
  zoom budget = 0. Tiếp tục tìm trong vùng đầu của track hợp lệ; không tự zoom tối đa.
- Một camera executor, mailbox latest-only, shared GPU scheduler và ReID periodic
  verification hiện có được giữ nguyên. Không thêm thread model cạnh tranh GPU.

Đích absolute vẫn được tính qua estimator hiện có. Không có LUT camera-bound thì
log rõ `UNCALIBRATED_OPTICAL_SCALE`; dùng xấp xỉ ZoomValue và mapping PositionABS đo
lịch sử, không coi raw encoder là optical magnification. Đích dưới sàn mapping 6.4
hoặc capability không có vẫn là **fallback có lý do**, không ép zoom vượt framing
chỉ để gửi absolute. Thiếu telemetry thì defer; RPC absolute reject không thử pulse.
Không gửi pulse chỉ để chờ SCRFD. Một principal move/plan; tối đa hai correction 15%.

## Face-person association và Face Database

`FaceWorker` chỉ xét face trong ROI của person đã lock (hoặc ROI face đã liên kết
trước đó nếu body tạm mất). Kiểm tra containment đầy đủ, tâm face ở vùng đầu/thân
trên, không đồng thời thuộc bbox người khác, temporal face IoU và margin ambiguity.
Nhiều face/candidate chồng lấn hoặc face ở vùng chân bị từ chối với
`FACE_ASSOCIATION_AMBIGUOUS`. Không chọn face lớn nhất toàn ảnh.

`TargetSession` xuất bản nguyên tử cặp session/track. Worker kiểm tra pair hiện tại
cả trước lẫn sau model; crossing hold/reset làm mất hiệu lực observation đang chạy.
Không tự chuyển sang bbox lân cận để giữ recognition count.

OSNet vẫn chỉ so ảnh target appearance. ArcFace nhận aligned face từ video và gọi
`FaceMatcher.match()` với `FaceDatabase` tại `FACE_DATABASE_PATH`. Không đưa ảnh
OSNet target hoặc OSNet score vào FaceMatcher. Giữ similarity threshold 0.45 và
hai lần temporal confirmation hiện có. Không match → UNKNOWN, không bịa identity.
UNKNOWN là **observation hợp lệ không match**, không bị thống kê như timeout.

## Hai loại validity và hai kênh kết quả

1. **Spatial/motor:** image mới lúc admission; bbox khi consume vẫn ≤800 ms,
   cùng session/track và compatible với vị trí hiện tại. Bbox cũ bị loại với
   `STALE_FOR_MOTOR`. Không nới giới hạn motor.
2. **Identity:** image hợp lệ lúc admission, association/quality đáng tin ở source
   frame, session/track/generation vẫn khớp khi model xong và khi consume; frame ID
   chưa được xử lý. Không áp giới hạn 800 ms của motor lần nữa sau ArcFace.

Worker publish một bản spatial trước ArcFace; completion không publish lại cùng
bbox, tránh đếm một face hai lần để xác nhận. Identity có queue riêng tối đa 32;
result mới không ghi đè identity chưa consume. Duplicate/out-of-order frame không
được tăng identity confirmation. Temporal confirmation dùng thời gian source frame.

`consume_identity()` chỉ cập nhật identity/GUI, không sửa `person_box`, `face_box`
hay tạo lệnh motor. Session/track đổi trong ArcFace → reject. Kết quả không được
chuyển sang target mới. Overflow và inference error được ghi rõ.

Metadata: `frame_id`, `target_track_id`, `lock_session_id`, `face_observation_id`,
`frame_received_timestamp`, `recognition_started_timestamp`,
`recognition_finished_timestamp`. Các timestamp này dùng monotonic clock;
`monotonic_ns` của trace dùng perf_counter, không tự trộn epoch.

## Logging và metrics

Tái sử dụng `logs/latency/` và `logs/zoom/`; zoom log tiếp tục chạy khi profiling tắt.
Thêm/reuse các event:

- TARGET_CANDIDATE_FOUND, REID_CONFIRMED, TARGET_LOCKED.
- ZOOM_REQUEST_CREATED, ZOOM_POSITION_ESTIMATED, ZOOM_COMMAND_SENT, ZOOM_RPC_ACK,
  ZOOM_MOVEMENT_COMPLETE, ZOOM_VISUALLY_STABLE, PERSON_ZOOM_STARTED, FACE_ZOOM_STARTED.
- FACE_LOST chỉ từ detector miss; empty mailbox không được in thành face lost.
  FALLBACK_TO_PERSON_TRACKING và PERSON_FRAMED_FACE_SEARCH mô tả chuyển mode.
- FACE_DETECTED, FACE_ASSOCIATED, FACE_QUALITY_PASSED, ARCFACE_STARTED/COMPLETED,
  IDENTITY_OBSERVATION_ACCEPTED/REJECTED/APPLIED và IDENTITY_CONFIRMED.

Completion lấy telemetry hoặc plateau có nhãn `estimated`; ACK không phải motor
complete. Không tạo measured motor duration nếu thiếu quan sát onset/completion.
Rejection reasons gồm SESSION_MISMATCH, TRACK_MISMATCH, FACE_ASSOCIATION_AMBIGUOUS,
LOW_FACE_QUALITY, DUPLICATE_OBSERVATION, INFERENCE_ERROR và overflow.
LOW_SIMILARITY nằm trên observation **accepted UNKNOWN**. STALE_FOR_MOTOR chỉ loại
spatial result; identity tương ứng vẫn có thể accepted.

CSV mỗi session thêm lock → zoom request, first face, first usable face, first
identity observation; giữ lock → command và identity confirmed. First usable lấy
mốc quality passed, không đợi ArcFace trả kết quả. Có accepted/rejected counts và
reason breakdown. Rejected count bao gồm các candidate bị chặn bởi association/
quality trước ArcFace; không so count đó trực tiếp với riêng số ArcFace attempts.
Unknown và unmatched vẫn nằm trong accepted count. APPLIED phân biệt observation
worker đã chấp nhận với kết quả đã được detection thread đưa lên shared state.

Nếu không đạt mục tiêu latency, đối chiếu `ZOOM_COMMAND_DEFERRED`:
MANUAL_OVERRIDE, OWNER_focus/owner khác, LENS_SETTLING, LENS_TELEMETRY_UNAVAILABLE;
và log freshness/session/capability. Chỉ yêu cầu <100 ms request/<200 ms command
khi controller sẵn sàng, telemetry và target hợp lệ.

## Files/functions

| File | Thay đổi |
|---|---|
| `runtime/inference_worker.py` | `start_person_zoom_on_lock`, `consume_identity`, thứ tự dispatch, fresh person timestamps, face-gap heartbeat |
| `runtime/face_worker.py` | `valid`, `rejection_reason`, `_process`, `_run`, `take`, `take_identity`, association, hai kênh kết quả, dedup |
| `runtime/target_session.py` | Atomic session + track validation |
| `control/target_state_machine.py` | Grace → person fallback → face reconfirmation |
| `control/auto_zoom.py` | `absolute_face_active`, lifecycle/defer/latency events, person framing settle |
| `ai/reid_target.py` | Candidate/ReID-confirmed events; không đổi ngưỡng matching |
| `telemetry/zoom_trace.py` | Session metrics, identity counters/reasons, face events |
| `tools/benchmark_hybrid_acceptance.py` | Hardware-free ready-controller và delayed-identity evidence |
| `test/test_pipeline_identity_zoom.py` | Acceptance tests mới |
| `test/test_hybrid_latency.py`, `test/test_reid_target.py`, `test/test_face_hold_digital.py` | Early spatial feedback, miss-with-person, fallback expectations |

RTSP/GUI/manual controls/database formats/config và weights được giữ nguyên.

## Validation và giới hạn

Chạy trong environment `Autofocus_Camera`:

```bash
python tools/run_unit_tests.py
python tools/benchmark_hybrid_acceptance.py --output-dir /tmp/hybrid_mock
```

216 tests pass; bao phủ no-face lock→absolute, face/recognition trong khi zoom,
face miss còn person, occluder/head geometry, delayed recognition 1100 ms, đổi
session/track, duplicate, independent database UNKNOWN, one principal command,
manual/AF/timeout/stale và các regression pipeline hiện có.

[Mock evidence lưu trong repository](latency_audit/hybrid_fix_mock/result.json):
lock→request ~2.3 ms, lock→command ~3.8 ms; 1 absolute, 0 pulse, cuối cùng HOLD.
Recognition 388 ms và frame age 1100 ms là **clock giả lập** để test validity:
identity observation accepted, bbox invalid cho motor. Một observation chỉ là
candidate, chưa confirmed; vẫn cần hai observation đúng ngưỡng như trước.

Đây không phải benchmark throughput GPU/camera thật. Tool dùng mock models/transport,
không chạy inference weights hoặc motor. Chưa đo lại A/B thật sau sửa; không tuyên bố
đã đạt <200 ms trên phần cứng. Cần chạy app, khóa target xa/không thấy face, face quay
đầu, người đi ngang, manual override; đối chiếu JSONL/CSV và tỷ lệ confirmed/UNKNOWN.
Đặc biệt kiểm tra strict head/neighbor geometry không loại quá nhiều face hợp lệ,
framing safety override 0.18 có phù hợp cảnh thực và RPC/RTSP latency lúc tải nặng.
