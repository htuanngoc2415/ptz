# Sửa auto pan/tilt, overlap và telemetry — 11/11/2026

## 1. Kết quả và phạm vi

Đã sửa trực tiếp repository, kiểm thử bằng mock/simulator, không mở camera hoặc phát lệnh motor thật. Bản sửa ưu tiên gate identity và tính mới của observation; không tăng tốc độ motor mặc định. Adaptive pulse là tùy chọn A/B, mặc định **False**.

Báo cáo đầu vào: [hai phiên gần nhất](../ai_ptz_current_status_20261111_latest_two_runs.md). Bản sao trước sửa: [before.zip](before.zip); diff các file có sẵn: [changes.patch](changes.patch).

## 2. Root cause đã xác minh và giới hạn bằng chứng

### Auto pan/tilt bị gating

- `gui/cctv_app.py::_manual_override` tắt Auto Pan/Tilt, Auto Zoom/Focus và yêu cầu reset tracking. Không có tự động bật lại sau thao tác tay. Phiên A còn event manual `pan_right` lúc **20:17:43.441**. Nếu thao tác qua GUI, nó đi qua đường tắt automation này. Không có log toggle cũ để chứng minh pan được bật lại sau đó.
- `AUTO_PAN_TILT_ENABLED=True` chỉ là cấu hình source/default. Hai phiên cũ không ghi effective pan toggle, pan decision hoặc pan wire lifecycle; không thể khẳng định enabled suốt phiên, cũng không được lấy zero pan event làm bằng chứng zero lệnh.
- `OCCLUDED_HOLD` không tắt checkbox/controller vĩnh viễn. Worker thu hồi `TargetSession`, đưa person=None, nên pan không được cấp quyền và pulse đang chạy phải dừng. Khi phục hồi, token hợp lệ mới cho phép bám tiếp.
- `VERIFIED`/`VERIFYING` không phải điều kiện riêng chặn pan: khi person track và session còn hợp lệ, worker submit bbox mỗi frame xử lý. Periodic verification nền không cần hoàn tất trước submit. Acquisition/recovery vẫn có OSNet đồng bộ; freshness được kiểm tra lại sau inference.
- Không có target/session hợp lệ, manual override, bbox quá tuổi, deadzone, ownership hoặc cooldown đều có thể chặn lệnh. Zoom đang sở hữu motor thì pan không được dispatch; pan_needed làm worker request stop zoom trước khi pan có thể lấy ownership.

### Gate acquisition và tracking không nhất quán

Base acquisition trước sửa xác nhận theo similarity/margin/IoU liên tiếp nhưng không kiểm tra overlap. Hybrid tracking lại kiểm tra overlap ngay observation sau. Điều này giải thích khả năng “acquire score mạnh, sau 71/131 ms vào hold” ở phiên B, nhưng chưa xác định được bbox overlap thực tế là hai người, duplicate detection hay camera motion vì log không có đủ raw geometry tại các mốc đó.

Recovery còn có thể dùng cờ overlap quanh target/bbox cũ để chặn ứng viên khác. Bản sửa đánh giá geometry quanh **ứng viên sẽ được chọn**, và dùng cùng gate trước acquisition và trong recovery/continuity. Không hạ score/margin hoặc bỏ bảo vệ overlap.

Hai observation giống hệt bbox và cùng tracker ID được coi là bản sao cùng một observation trong phép đánh giá overlap. Hai tracker ID khác nhau, dù bbox trùng, vẫn bị coi là ambiguous: không tự suy đoán chúng là cùng một người.

### Duty cycle và nhịp frame

Cấu hình cũ vẫn có hiệu lực trong source: pulse **120 ms**, settle **150 ms**, max speed **3**, deadzone **0,08**, release **0,04**. Speed thực tế theo lỗi tâm: `min(3, max(1, 1+int(abs(error)*6)))`. Không phải mọi lệnh đều chạy speed 3.

Duty cycle lý tưởng của baseline là `120/(120+150) ≈ 44,4%`, chưa tính RPC/STOP và chờ frame. Code cũ yêu cầu timestamp ảnh **sau cả settle**, nên với detection 7,75–8,93 FPS có thể chờ thêm một nhịp 112–129 ms sau settle. Bản sửa yêu cầu ảnh **sau STOP**, nhưng chỉ dispatch khi wall-clock settle đã kết thúc. Ảnh mới nhận trong settle được tận dụng nếu vẫn còn hạn. Không dùng lại ảnh trước STOP.

Camera tăng tốc không kịp trong pulse ngắn là giả thuyết chưa đo. Queue nhanh không chứng minh motor nhanh. HANET gửi `Control.DoPtz`, trường `step` clamp 1..8; chưa có calibration step→độ/giây và không có pan/tilt position telemetry. Không tăng lên step 8.

### Recovery freshness

290 lần `STALE_RECOVERY_OBSERVATION` ở A là zoom recovery bị chặn, không phải 290 pan failure. Reason cũ gộp finite/order/observation-age/publish-age, không đủ dữ liệu để truy chính xác từng lần. Bản sửa tách reason và ghi timestamps/limits thực tế; không bỏ freshness guard và không tự sửa timestamp bằng thời gian mới.

### Counter zoom HANET

Đã xác nhận `zoom_trace` trước đây chỉ đếm `ZOOM_COMMAND_SENT` với `rpc_method=ptz.start`, bỏ sót đường HANET. Bản sửa phát một event trước mỗi RPC START zoom HANET sau wire validation; summary nhận `HANET.motion` và mã `kCmdZoomTele/kCmdZoomWide`. ACK/STOP không tăng counter nên không đếm trùng. Count là lệnh gửi/attempt, không phải chuyển động vật lý đã xác minh; RPC error được ghi riêng.

## 3. File và hàm thay đổi

| File | Thay đổi |
|---|---|
| `ai/reid_target.py` | Hook `acquisition_allowed` trước tăng confirmation, default giữ nguyên hành vi base selector |
| `ai/hybrid_reid.py` | Gate overlap quanh candidate; acquisition/recovery thống nhất; `TARGET_OVERLAP_EVIDENCE` ghi toàn bộ bbox, ID, IoU, overlap ratio, score hiện có, margin, timestamp, threshold |
| `control/auto_pan_tilt.py` | Dùng ảnh sau STOP trong settle; reject observation out-of-order; adaptive pulse opt-in; telemetry quyết định/dispatch/ACK/error/STOP/end; config/toggle events |
| `runtime/inference_worker.py` | Submit frame ID, UID, trạng thái tracking/identity, Auto Zoom toggle và timestamp detection-completed cho pan |
| `gui/cctv_app.py` | Log reason manual override qua controller; thông báo phải bật lại Auto Pan/Tilt để tiếp tục bám, không tự resume trái ý người dùng |
| `control/auto_zoom.py` | Tách lý do recovery freshness, log effective limits và Auto Zoom toggle |
| `camera/hanet_controller.py` | Event wire dispatch/result/error có backend; không đổi payload, speed mapping hoặc START/STOP order |
| `telemetry/zoom_trace.py` | Đếm lệnh HANET cùng summary, không đếm ACK/STOP |
| `config.py` | Ba cấu hình adaptive mới |
| `test/test_auto_pan_tilt.py` | Mock integration nhận metadata mới |
| `test/test_pan_follow_revision.py` | 9 test mới về gating/cooldown/adaptive/overlap/freshness/HANET summary |
| `tools/pan_tilt_report.py` | Đọc latency JSONL + rotations, tổng hợp rate/gating/latency/duration, không kết nối camera |

Score của candidate chưa chạy ReID được ghi null; không tạo similarity giả để hoàn thiện log. Overlap snapshot throttle tối đa 2 Hz khi có overlap. Pan decision đếm mọi evaluation, nhưng chỉ emit khi reason/direction đổi hoặc sau 0,5 s ở trạng thái ổn định. Command lifecycle và lỗi luôn ghi.

Telemetry mới phân biệt:

- **Không tạo lệnh:** `AUTO_PAN_DISABLED`, `MANUAL_OVERRIDE`, `TARGET_UNCERTAIN`, `NO_VALID_TARGET`, `DEADZONE`, `STALE_OBSERVATION`, `OUT_OF_ORDER_OBSERVATION`, `MOTOR_BUSY`, `COOLDOWN`.
- **Đã tạo/dispatch:** `PAN_TILT_DISPATCH`, `PAN_TILT_WIRE_DISPATCH` có command ID, hướng/speed/pulse và tuổi observation; timestamp detection-completed cho phép tính detection→supervisor dispatch riêng với receive→wire age.
- **Kết quả:** `PAN_TILT_RPC_RESULT`, `PAN_TILT_WIRE_RESPONSE/ERROR`, `PAN_TILT_STOP`, `PAN_TILT_COMMAND_ENDED`. STOP thất bại giữ lease và retry, không cấp motor cho zoom/manual khi chưa an toàn.

`command_active_duration_ms` là thời gian host từ dispatch đến kết thúc STOP, bao gồm I/O; không phải đo thời gian motor vật lý. Image displacement chỉ ghi khi có observation mới, cùng session/track và còn hạn trong khoảng lệnh; nó còn chứa chuyển động của người, không được dùng như lens/PTZ telemetry.

Recovery reasons mới: `RECOVERY_INVALID_TIMESTAMP`, `RECOVERY_TIMESTAMP_ORDER`, `RECOVERY_OBSERVATION_TOO_OLD`, `RECOVERY_PUBLISH_TOO_OLD`, `RECOVERY_SESSION_MISMATCH`, `RECOVERY_INVALID_ZOOM_ESTIMATE`. Dispatcher vẫn có kiểm tra session riêng trước thực thi.

## 4. Cấu hình cũ và mới

| Config | Trước | Sau |
|---|---:|---:|
| `AUTO_PAN_TILT_ENABLED` | True | True; manual vẫn tắt và cần bật lại |
| `AUTO_PAN_TILT_PULSE` | 0,12 s | 0,12 s |
| `AUTO_PAN_TILT_SETTLE` | 0,15 s | 0,15 s |
| `AUTO_PAN_TILT_MAX_SPEED` | 3 | 3 |
| `AUTO_PAN_TILT_DEADZONE` | 0,08 | 0,08 |
| `AUTO_PAN_TILT_RELEASE` | 0,04 | 0,04 |
| `AUTO_PAN_TILT_ADAPTIVE` | Chưa có | **False** |
| `AUTO_PAN_TILT_MAX_PULSE` | Chưa có | 0,24 s |
| `AUTO_PAN_TILT_FAR_ERROR` | Chưa có | 0,30 |
| `MAX_CONTROL_OBSERVATION_AGE` | 0,8 s trong source trước lần sửa này | Không đổi |
| ReID acquire/reacquire/margin | 0,65 / 0,70 / 0,08 | Không đổi |
| Person/face framing | 0,40 / 0,25; face hold 0,22–0,28 | Không đổi |

Khi adaptive=True, pulse nội suy từ 120 ms tại deadzone tới 240 ms khi lỗi tâm ≥0,30, không tăng speed. Hysteresis và kiểm tra session/freshness/manual/hướng vẫn có thể ngắt pulse sớm. Startup kiểm tra giới hạn adaptive, từ chối max pulse >0,5 s hoặc cấu hình đảo ngược; giới hạn mặc định thử nghiệm là 0,24 s.

## 5. Kiểm thử và simulator

- **Baseline trước sửa behavior: 93/93 đạt**, [baseline.log](baseline.log).
- **Bộ hồi quy trọng tâm: 208/208 đạt**, [tests.log](tests.log).
- **Runner toàn dự án: 416 mục, 414 đạt, 2 lỗi môi trường**, [full-tests.log](full-tests.log). Lỗi `test_gstreamer_cpu` và RTSP unavailable import thiếu `gi` phù hợp môi trường Python 3.10; không có assertion failure trong lượt cuối. Sau bổ sung timestamp detection cho telemetry, bộ 208 được chạy lại.
- Không bypass failure bằng skip. Test STOP failure, manual override, ownership, target loss, stale queue/wire và face/identity regression vẫn được giữ.

9 test mới kiểm tra: ảnh trong settle; out-of-order; VERIFYING vẫn follow; adaptive bounds; acquisition/tracking overlap nhất quán; candidate recovery không bị overlap ở vị trí cũ chặn; duplicate cùng ID; năm loại freshness rejection; bốn pulse HANET đếm đúng bốn và không đếm pan.

[Simulator](simulator.json) dùng target đứng yên, gain ảnh theo speed cố định, không có gia tốc/RPC trễ, lỗi ban đầu 0,40 chiều rộng, settle 150 ms, giới hạn overshoot 0,04. Kết quả:

| Gain giả lập | Pulse baseline → adaptive | Thời gian baseline → adaptive | Lỗi tâm cuối adaptive |
|---|---:|---:|---:|
| 0,06 | 31 → 24 | 8,370 → 7,485 s | 0,0339 |
| 0,12 | 15 → 12 | 4,050 → 3,759 s | 0,0300 |
| 0,25 | 7 → 5 | 1,890 → 1,573 s | 0,0253 |

Đây là kiểm tra bounded response trong mô hình, **không chứng minh không overshoot trên HANET thật**. Adaptive vẫn mặc định tắt.

Tái chạy:

```sh
PYTHONPATH=.:test /home/admin1/miniconda3/envs/py/bin/python -m unittest \
  test_pan_follow_revision test_auto_pan_tilt test_hybrid_tracking \
  test_reid_target test_crossing_retention test_zoom_ownership test_zoom_latency_safety \
  test_hanet_controller test_hanet_rpc test_control_regression test_runtime_waiting \
  test_hybrid_latency test_face_identity test_dahua_camera test_dual_stream test_opencv_player
```

```sh
/home/admin1/miniconda3/envs/py/bin/python tools/run_unit_tests.py
python tools/pan_tilt_report.py logs/latency/latency_trace_<run>.jsonl
```

Tool dùng counter toàn phiên khi còn `LATENCY_REPORT`; nếu không, tỷ lệ decision chỉ đại diện event lấy mẫu. Command rate trên đoạn trace còn lưu được ghi rõ retained scope. Log cũ thiếu PAN events không đủ để suy ra motor không hoạt động.

## 6. Hiệu chuẩn thật và A/B test — chưa thực hiện

1. Chọn khu vực trống, một target, góc rộng; chuẩn bị nút STOP/manual. Bật log, xác nhận backend HANET và hướng pan/tilt. Thử manual step 1 với xung ngắn trước; kiểm tra STOP vật lý, không chỉ ACK. Sau manual phải **bật lại Auto Pan/Tilt** và xác nhận `PAN_TILT_TOGGLE=true`.
2. **A:** adaptive=False, pulse 120 ms, settle 150 ms, max speed 3. Tạm tắt Auto Zoom để đo riêng pan/tilt. Cho target lệch tâm ở 0,10; 0,20; 0,30 chiều rộng và hai hướng; thử đứng yên rồi đi ngang. Ghi video, effective config, command IDs và toggle.
3. **B:** cùng camera, zoom, cảnh và đường đi; chỉ đổi adaptive=True, giữ speed/deadzone/settle. Bắt đầu với `MAX_PULSE=0.16`, sau quan sát đáp ứng/STOP mới tăng từng bước 0,04 tới tối đa 0,24. Dừng thử nếu rung, overshoot, mất identity hoặc STOP không đúng. Không tăng speed đồng thời.
4. So sánh receive/detection→dispatch, command rate, khoảng nghỉ, số START/STOP, tỷ lệ reason gating, thời gian vào deadzone và overshoot từ video. Phân loại controller không tạo lệnh, lệnh không ACK, và ACK nhưng chuyển động kém; không trộn ba loại.
5. Sau pan riêng, bật Auto Zoom và kiểm tra ownership/settle/manual. Target sát biên phải được ưu tiên giữ trong khung. Tiếp theo mới thử hai người giao nhau; đối chiếu `TARGET_OVERLAP_EVIDENCE` với video để tìm duplicate/overlap thật trước khi đề xuất thay threshold.
6. Khi target mất: xác nhận pulse dừng trong bound, không lặp START bằng ảnh cũ; đọc reason freshness recovery cụ thể. Không tăng max age để che pipeline chậm.

## 7. Rollback

Dừng ứng dụng trước khi rollback. Với thay đổi thử nghiệm adaptive, chỉ cần đặt `AUTO_PAN_TILT_ADAPTIVE=False` rồi khởi động lại; đây cũng là mặc định bản sửa.

Để phục hồi toàn bộ các file có sẵn về snapshot trước lần sửa này, từ thư mục dự án chứa `config.py`:

```sh
python - <<'PY'
import zipfile
with zipfile.ZipFile('docs/pan_tilt_fix_20261111/before.zip') as archive:
    archive.extractall('.')
PY
```

Lệnh ghi đè các file nằm trong snapshot: nếu đã sửa tiếp sau lần này, lưu bản hiện tại/so sánh `changes.patch` trước. Hai file mới `tools/pan_tilt_report.py` và `test/test_pan_follow_revision.py` không nằm trong snapshot; có thể xóa riêng khi rollback, đặc biệt test mới phụ thuộc API mới. Các báo cáo/log có thể giữ làm bằng chứng.

## 8. Những điều chưa kết luận

Chưa đo motor thật, chưa biết gain/acceleration/độ trễ firmware, chưa biết Auto Pan/Tilt bật/tắt ở mọi thời điểm của hai phiên cũ, chưa phân loại được overlap thật/duplicate tại các mốc 71/131 ms, chưa truy chính xác từng lần recovery freshness cũ. Bản sửa loại bỏ bất nhất gate và một khoảng chờ frame thừa; khả năng bám nhanh/mượt trên HANET cần xác minh bằng A/B ở trên.
