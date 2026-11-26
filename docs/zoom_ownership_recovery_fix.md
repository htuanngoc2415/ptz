# Sửa ownership và cancellation của zoom — 09/10/2026

## Kết luận từ phiên camera cũ

Nguồn: `logs/camera/camera_control_20261009_094312_656247.log`, hai phần `logs/latency/latency_trace_20261009_094312_656247.jsonl*`, và `logs/ptz_20261009_094306.log`. Các log này là bằng chứng **trước bản sửa**, không phải kết quả chạy lại camera.

### Không có PERSON zoom-in không tự nó chứng minh lỗi

- Lock đầu tiên: 09:43:38.881 (trace), 09:43:38.882 (app).
- PERSON observation thực sự được supervisor nhận ở 09:43:38.885, 39.034, 39.141, 39.277; queue wait lần lượt 0.492, 0.534, 1.985, 2.969 ms. Nhánh PERSON chạy trước khi submit/take SCRFD, không chờ nhận diện mặt.
- Context được giữ sang `FRAME_MANAGER_POP` kế tiếp chứa kết quả quyết định frame trước: 09:43:38.938 có person_ratio=0.722222; 39.047=0.720486; 39.172=0.718750; 39.291=0.715278. Cả bốn đều `ZOOM_OUT_PERSON_APPROACH`. Không được gán các ratio này cho frame mới vừa pop.
- Những ratio này lớn hơn 0.54; lệnh đúng theo yêu cầu là OUT hoặc HOLD khi chạm wide-limit, **không phải IN**. Không thấy person đạt dải HOLD 0.46–0.54 ở đoạn này.
- Mặt đầu tiên: 09:43:39.275; FACE_CONTROL xuất hiện 09:43:39.895, sau các observation PERSON đầu tiên. Không có bằng chứng FACE latch chặn PERSON từ lúc lock.
- 09:43:40.574, telemetry trong FACE plan là zoom=1.1. Controller chặn OUT ở min_zoom+epsilon=1.15. Đây là lời giải phù hợp cho việc không OUT trước đó; log cũ không lưu telemetry và suppression reason ở từng return nên không thể xác nhận trực tiếp zoom tại 38.885.
- Phiên hai: PERSON ratio=0.762; request OUT 09:44:14.529, gửi 14.580; SCRFD phát hiện mặt 14.725. Điều này xác nhận pipeline có thể điều khiển PERSON trước khi thấy mặt.

Không ép PERSON IN cho scene có person_ratio≈0.72. Bổ sung log tất cả return để lần chạy tới phân biệt rõ HOLD, wide-limit, stale frame, direction hold, settling, owner và telemetry unavailable.

### Vì sao recovery bị STOP liên tục

`AutoZoomController.motion_feedback()` trước đây yêu cầu body hợp lệ và cùng session/track với action đang chạy. RECOVERY diễn ra khi không có body, nên mỗi frame mới gọi `request_stop('TARGET_LOST')`, đặt cancel Event và khiến watchdog/supervisor gửi STOP. Log có 33 recovery kết thúc TARGET_LOST và một lần BBOX_FOUND. Khoảng START→STOP toàn nhóm là 74.389–294.017 ms.

Đây là xung đột ownership: điều kiện kích hoạt tìm lại target đồng thời bị dùng làm điều kiện hủy chính thao tác tìm lại đó. Không phải timeout pulse quá ngắn.

Ngoài ra, inference có nhánh BBOX_FOUND áp dụng cho mọi recovery khi thấy person. Điều này cũng sai với HEAD_RECOVERY: person vẫn còn hiện diện là điều kiện bình thường của face recovery. HOLD do mất mặt ở frame kế tiếp cũng có thể ngắt HEAD_RECOVERY. Bản sửa phân biệt hai scope.

## Thay đổi triển khai

| File / hàm | Thay đổi |
|---|---|
| `control/auto_zoom.py::motion_feedback` | TARGET recovery không bị body absence/khác association hủy; chỉ target có session/track và geometry hợp lệ mới STOP BBOX_FOUND. HEAD recovery vẫn yêu cầu association và body an toàn. TARGET recovery không đòi track còn hiện diện; session và generation validation vẫn giữ nguyên. |
| `request_stop`, `_stop` | TARGET_LOST không hủy TARGET recovery đang sở hữu motor. Không bỏ qua manual, stale session, stale observation, watchdog, fault hoặc giới hạn. Cancel Event không bị xóa để nuốt một STOP an toàn khác. |
| `recover_missing`, `_recover_missing` | Segment vẫn có duration và tổng budget cũ; sau STOP phải có snapshot mới hơn STOP ACK, trạng thái Idle và giảm zoom >0.01 mới được chạy segment tiếp. Không có tiến triển hoặc không có lens telemetry thì chặn lặp lại mù. |
| `head_recovery`, `continue_face_recovery` | Tách scope FACE/TARGET; giữ HEAD recovery qua các frame vẫn mất mặt nếu body/session/track còn hợp lệ; segment vẫn bounded. Đoạn tiếp theo phải có feedback Idle và tiến triển. |
| `_zoom_worker` | Giữ session/generation/track và observation guards; thêm manual-owner và real motor fault checks. Fault dừng và ngăn recovery tự khởi động lại. |
| `update`, `_update` | Giữ logic 0.46–0.54 của PERSON; wrapper ghi ratio, bbox, lens status và lý do cho cả các đường return không gửi motor command. |
| `_stop`, `_observe_motor_idle` | Ghi before/after/delta/duration, initiator và reason. Motor Idle chỉ ghi khi có telemetry mới xác nhận Idle. |
| `runtime/inference_worker.py::start_person_zoom_on_lock` | Giữ submission trước SCRFD; ghi các suppression như DISABLED, TARGET_NOT_VALIDATED, FACE_OWNER/FACE_LATCHED. |
| `InferenceWorker.run` | Ghi policy decision, chỉ BBOX_FOUND-cancel TARGET recovery; HOLD mất mặt cho phép safe ABS hoặc safe FACE recovery tiếp tục. |
| `control/zoom_policy.py::decide` | Chỉ latch FACE với geometry hiện diện, finite, kích thước dương, giao với frame và đủ mới. Không latch chỉ vì state có target_type=FACE nhưng không có face geometry. |

Không đổi model weights, ZoomValue/PositionABS calibration, verified PositionABS lower bound 6.4, các dải framing hoặc tăng timeout để che lỗi ownership.

## Face-loss policy sau sửa

- Mất mặt ngắn, person còn hợp lệ: giữ FACE authority, không trở lại framing person 50%; ABS có thể tiếp tục trong grace hiện có, không phát lại command.
- Mất mặt kéo dài: HOLD; chỉ HEAD recovery khi policy có bằng chứng mặt liên kết trước đó ở mép trên và body tiếp tục bị cắt đầu qua nhiều frame, tối thiểu một giây. Giữ giới hạn tối đa hai HEAD attempts của policy. Association/session/body freshness là điều kiện safety; không suy ra head pose từ SCRFD miss đơn thuần.
- Person thực sự mất: target recovery độc lập với trạng thái SCRFD; target phải qua ReID lại trước khi được zoom-in.
- BBOX_FOUND của TARGET recovery yêu cầu target hợp lệ, không phải người bất kỳ hoặc face chưa liên kết.

## Log mới

`ZOOM_CONTROL_DECISION` ghi cả khi không gửi motor: timestamp (profiler tự thêm), frame_id, lock_session_id, person_track_valid, person_ratio, face_detected, face_latched, face_ratio, zoom_owner, zoom_state, zoom_direction_requested, decision, decision_suppression_reason, observation_age_ms. Framing decisions có thêm target_bbox, current_zoom_value, zoom_motion_state và telemetry_status. `ZOOM_POLICY_DECISION` ghi riêng quyết định trước dispatch.

Các lifecycle events:

1. `ZOOM_REQUEST_CREATED` — intent được tạo.
2. `ZOOM_COMMAND_SENT` — camera adapter gửi RPC thực tế.
3. `ZOOM_RPC_RESPONSE` / `ZOOM_RPC_ACK` — ACK; không chứng minh motor chạy hoặc dừng.
4. `ZOOM_MOVING` — nếu telemetry quan sát được chuyển động; không phát sinh giả nếu thiếu evidence.
5. `ZOOM_STOP_REQUESTED` hoặc `ZOOM_TARGET_REACHED` — có cancellation reason/initiator.
6. `ZOOM_STOP_ACK` — ACK của STOP ở PTZ adapter.
7. `ZOOM_ACTION_ENDED` — tổng kết controller action: zoom_before, zoom_after, zoom_status_before/after, actual_zoom_delta, command_duration_ms, stop_initiator, stop_reason, telemetry_status, motor_stopped=null.
8. `ZOOM_MOTOR_IDLE_OBSERVED` — snapshot mới hơn thời điểm STOP ACK thực sự báo Idle; giữ command_id cũ để ghép log.

`command_duration_ms` là thời gian controller action từ worker dispatch đến kết thúc xử lý STOP, có cả RPC/setup; không phải thời gian motor vật lý. Zoom-after tại ACTION_ENDED có thể còn Moving. Giá trị kết thúc đáng tin hơn nằm trong MOTOR_IDLE_OBSERVED. Dữ liệu thiếu ghi null và telemetry_status=unavailable, không điền từ ước lượng motor hoặc ACK. Không có Idle telemetry thì không phát event Idle; recovery không được chạy tiếp chỉ nhờ ACK.

[71 events mẫu offline/mock](zoom_ownership_mock_events.jsonl) được tạo bằng mock lens trong 22 acceptance tests. Mỗi dòng có evidence_source=offline_mock; không phải log camera mới và không có RPC tới thiết bị thật. Fixture chỉ chứa các decision, STOP và summary events mới; không giả lập event wire SENT/ACK bằng dữ liệu bịa.

## Kiểm thử

- **233/233 tests PASS**: 22 ownership acceptance + 211 regression hiện có, 13 suites; lần chạy cuối 5.339 s.
- 22 acceptance tests gồm IN/HOLD/OUT theo person ratio; OUT tại wide-limit; FACE latch không trở lại PERSON; geometry mặt thiếu/invalid/stale không giành ownership; bounded head recovery; face recovery giữ ownership và dừng khi person thực sự mất; target recovery bắt đầu và không bị TARGET_LOST lặp hủy; reacquire hợp lệ/invalid; ABS qua brief face miss; manual override; real fault; wide limit; Idle và tiến triển bắt buộc trước segment mới; không lặp khi thiếu telemetry; STOP ACK không giả thành Idle; mất track không chặn TARGET recovery nhưng vô hiệu hóa session vẫn dừng motor.
- Regression suites: zoom latency safety, absolute audit, auto zoom, hybrid latency, pipeline identity zoom, oscillation, Dahua adapter, runtime architecture, ReID target, control regression, crossing retention, final framing.
- Không mở camera, không tải model và không gửi motor command thật. Tất cả camera calls dùng Mock hoặc transport in-process của bộ test.

Chạy riêng acceptance:

```bash
PYTHONPATH="$PWD:$PWD/test" /home/edabk/miniconda3/envs/Autofocus_Camera/bin/python -m unittest test_zoom_ownership
```

Bộ regression đầy đủ đã chạy bằng `/tmp/run_zoom_owner_all.py`; output kiểm tra tại `/tmp/zoom_owner_all.log`. Kiểm tra thêm import/compile bằng `python3 -m py_compile control/auto_zoom.py runtime/inference_worker.py control/zoom_policy.py test/test_zoom_ownership.py`.

## Cần xác nhận trên camera thật

Chưa chạy camera sau bản sửa. Cần kiểm tra ZoomStatus thực tế có trả Idle sau STOP, độ trễ polling, mức delta sau segment 300 ms và thời gian motor coast; nếu firmware không trả Idle thì controller sẽ giữ recovery chờ feedback, không tự suy ra Idle. Cần scene có person_ratio<0.46 trước khi thấy mặt để đo PERSON IN; scene cũ không thỏa điều kiện này. Cần scene face loss/head clipping để đánh giá proxy mép ROI và chất lượng track; mock không chứng minh chất lượng head localization.

Không dùng kết quả mock để tuyên bố latency hoặc FOV change thực tế đã cải thiện. Timeline 09:43–09:45 vẫn được giữ nguyên là lịch sử trước bản sửa.
