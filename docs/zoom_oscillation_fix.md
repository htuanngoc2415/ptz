# Fix zoom-out oscillation — 2026-10-08

## Root cause

Log phiên 11:05:54 cho thấy face zoom làm bbox person lớn lên, sau đó
`TargetStateMachine.update()` xóa `face_latched` khi quá face-loss timeout mặc dù
person vẫn đúng. Nó trả PERSON target 50%; AutoZoom gửi ZoomWide để thu bbox 75–100%
về 46–54%. Đồng thời inference có nhánh tự STOP `PERSON_FRAMING_HOLD` khi đang face
pulse. Latch final-face 18% cũng veto điều khiển face 22–28%, kể cả chiều OUT.

Đây là các quyết định framing xung đột, không phải hai motor executor. Camera worker
vẫn chỉ có một. Latest-only mailbox không loại được một lệnh person đã dequeued hoặc
đang chờ camera queue, nên cần thêm kiểm tra policy ở thời điểm gửi RPC.

## State sau sửa

```text
TARGET_LOCKED → PERSON_APPROACH → đủ face association → FACE_TRACK
                    ↓                                   ↓
                ZOOM_HOLD                        ZOOM_HOLD (22–28%)
                                                        ↓ face mất, person còn đúng
                                                FACE_TEMPORARILY_LOST
                                                        ↓ face trở lại
                                                   FACE_TRACK
Mất target thực sự → TARGET_RECOVERY → xác nhận lại target
```

`ZoomPolicy` giữ phase face theo `lock_session_id`. State spatial cũ ZOOM_FACE,
TRACK_FACE, FACE_LOST_HOLD vẫn dùng cho tracking/UI; không xóa face phase chỉ vì miss.
Re-lock signal cùng session không gọi person approach nữa; session mới thật sự được
phép approach lại. Không khẳng định hai session khác nhau là cùng người chỉ vì bbox gần.

Person approach vẫn gửi ngay sau OSNet lock, target 46–54%, không chờ SCRFD. Sau khi
vào face phase, bbox person chỉ duy trì tracking/FOV, không ra lệnh thu về 50%.
Vùng face 22–28% quyết định HOLD; GUI framing latch được đồng bộ ngưỡng dưới 22%,
không còn chốt 18% chặn policy. Face visible quá lớn phải có 3 quan sát và ≥300 ms
trước khi cho OUT. Estimator, endpoint mapping, bounded correction và safety giữ nguyên.

Khi face miss thực sự và person còn đáng tin: HOLD, hủy chuyển động đang chạy nếu có,
không đưa PERSON observation vào supervisor. Empty mailbox không phải miss: cho phép
heartbeat ngắn <800 ms từ mốc source face, không tái phát command. Face trở lại không
reset về person framing. Source timestamp không được đóng dấu lại để kéo dài freshness.

## Zoom-out hợp lệ

- `ZOOM_OUT_FACE_TOO_LARGE`: face visible vượt 28%, qua xác nhận thời gian/frame.
- `ZOOM_OUT_HEAD_CLIPPED`: face liên kết trước đó ở sát mép trên (≤3% chiều cao),
  hiện mất face và person top ở ≤1%, tồn tại ≥3 frame và ≥1 s. Bbox person lớn một
  mình không đủ. Tối đa 2 bước OUT, mỗi bước ≤300 ms, cách yêu cầu ≥800 ms, có settle
  và đánh giá lại bằng frame mới. Budget không reset vì face chập chờn trong session.
  Đây là bằng chứng hình học ước lượng, không phải khẳng định biết đầu thật ngoài ảnh.
- `ZOOM_OUT_TARGET_RECOVERY`: target mất thực sự; giữ recovery pulse/watchdog/budget
  hiện có. Không zoom dài vô điều kiện.
- `ZOOM_OUT_MANUAL`: đường manual/diagnostic trực tiếp, không đổi điều khiển tay.

`ZOOM_HOLD_FACE_TEMPORARILY_LOST` không được dùng person_ratio>0.54 làm lý do OUT.
Trong PERSON_APPROACH của session mới, OUT vì người quá lớn vẫn hợp lệ.

## Decision authority và queue

`ZoomPolicy.decide()` là nơi chọn mode/cho phép chuyển động trong inference; bỏ
nhánh person framing brake và veto face-framing riêng trước đây. AutoZoom vẫn làm
estimation, deadband, timeout và safety; camera executor vẫn serialize motor I/O.

`AutoZoom.enter_face_mode()` đặt latch session để chặn PERSON observation cũ ngay
trong `update()` và motor worker. RPC2 kiểm tra lại `zoom_policy_valid` trước HTTP
POST, vì một lệnh đã vào camera queue có thể trở nên lỗi thời trong lúc chờ.
Recovery và manual không bị chặn bởi person-only guard. Actual session invalidation
vẫn được kiểm tra riêng; không replay command bị reject bằng pulse.

## Logs

Mỗi trace có các field yêu cầu: zoom_state, previous_zoom_state, face_visible,
face_lost_duration_ms, person_track_valid, person_ratio, face_ratio,
current_zoom_position, requested_zoom_position, command_type, decision_reason,
lock_session_id, target_track_id, command_id, timestamp. Unknown dùng null.
Correlated fields đi từ quyết định qua supervisor/camera queue đến RPC gửi thật.
Manual không có perception/session context thì các field đó null.

`ZOOM_STOP_REQUESTED` và dòng `STOP_REQUEST` chỉ ghi yêu cầu STOP; ACK không được
coi là motor đo được đã dừng. Completion telemetry/estimated stabilization giữ
cách phân biệt measured/estimated hiện có.

## File/function

- `control/zoom_policy.py`: policy theo session, HOLD, size hysteresis, head recovery budget.
- `control/target_state_machine.py:update`: giữ face latch khi person còn đúng.
- `runtime/inference_worker.py`: gọi policy, bỏ các framing veto xung đột,
  ngăn repeated lock khởi động person approach trong cùng face session.
- `control/auto_zoom.py`: `enter_face_mode`, `head_recovery`, queue/session guards,
  metadata command và STOP-request logging.
- `camera/ptz_controller.py:_connect`, `camera/dahua_rpc.py:_call`: guard lúc gửi RPC,
  giữ nguyên manual zoom/focus.
- `telemetry/zoom_trace.py`: decision fields mặc định null.
- `test/test_zoom_oscillation.py`: regression mới; cập nhật test kỳ vọng fallback cũ
  trong test_face_hold_digital, test_pipeline_identity_zoom, test_reid_target.

## Validation

227 automated tests PASS, không kết nối camera; compileall/pyflakes file sửa PASS.
Bao gồm: mất face còn person 99.7% vẫn HOLD; flicker không về person approach;
face quá lớn cần confirmation; head clipping có/không có bằng chứng và budget;
true target loss; session cũ/mới; queued PERSON bị reject trước RPC; pulse head
recovery tự dừng dù inference không gửi thêm frame. Các test pipeline/identity,
manual/AF ownership và absolute feedback trước đó vẫn pass.

Chưa chạy motor thật sau bản sửa. Cần xác minh trên cảnh cũ rằng sau FACE_TRACK
không còn ZoomWide vì person framing; đánh giá head clipping threshold và face OUT
trong FOV thật. Không thay đổi OSNet, ArcFace, Face Database, model thresholds hay weights.
