# Audit và sửa absolute zoom — 2026-10-08

## 1. Trước khi sửa: bằng chứng là pulse liên tiếp

Đường thực thi: `ai/reid_target.py` xác nhận lock →
`runtime/inference_worker.py` chọn bbox trên ảnh gốc →
`AutoZoomController.submit_observation()` → `_dispatch()` → `update()` →
`_start_zoom()` → `_zoom_worker()` → `PTZController.start_zoom_target()` khi có đích,
hoặc `PTZController.command()` khi đích `None` → `ZoomRPC.goto()` hoặc `start()` → RPC2.
Camera command worker là nơi thực thi I/O motor; inference chỉ gửi mailbox.

[Thống kê log trước sửa](zoom_audit/before_summary.json) được trích từ
`logs/camera/camera_control_20261008_091528_174507.log` và
`logs/ptz_20261008_091522.log`:

- 52 `ZOOM_REQUEST_CREATED`, **cả 52 có `zoom_position_requested: null`**.
- 52 `ptz.start` ACK và 56 `ptz.stop` ACK của trục zoom.
- 35 `PULSE_COMPLETE`, 8 `FACE_LOST`, 3 `ZOOM_LIMIT`, 2 `STALE_SESSION`,
  và mỗi loại `FACE_EDGE`, `SLOW_APPROACH`, `BBOX_FOUND`, `STALE_OBSERVATION`: 1.
- 09:15:56.549: frame 404, hướng IN, timeout 5000 ms, đích null; 09:15:56.920 dừng FACE_LOST.
- 09:16:13.331: frame 660, hướng IN, pulse 120 ms, đích null.

Log cũ không ghi `params.code`. Kết luận pulse dựa trên **log null target + nhánh code**,
không giả vờ đã có packet capture: `_zoom_target is None` gọi `command()`, ánh xạ
sang `ZoomTele`/`ZoomWide`. Đây là trường hợp **B** trong task. Không có bằng chứng
ở phiên này cho A (một absolute chậm) hoặc C (nhiều absolute). D (firmware AF ngắt
zoom) chưa xác minh; log cũ không đủ để quy nguyên nhân. E (sai ước lượng) có rủi ro
rõ trong code nhưng chưa có calibration/ground truth để định lượng.

Công thức cũ trong `AutoZoomController.update()`:

```python
predicted = max(min_zoom, min(37., current * max(2/3, min(1.5, target_ratio/ratio))))
if predicted >= 6.4:
    self._zoom_target = predicted
```

Từ mức 3, ratio 0.1 → desired 0.5, đích cũ chỉ 4.5, tiếp tục pulse; đích hình học
xấp xỉ là 15. Lệnh absolute cũ cũng chịu timeout chọn từ pulse 60/120/250 ms hoặc
`SLOW_APPROACH`, có thể bị ngắt trước khi đến đích. Empty mailbox face bị diễn giải
như mất face, mặc dù nhánh person vẫn có quan sát hợp lệ. Những lỗi này đã được sửa.

Cooldown/độ trễ vẫn tồn tại có chủ đích: settle 0.2 s, watchdog publication 0.8 s,
đổi chiều 0.4 s, AF và zoom giữ ownership luân phiên. AF có thời gian xử lý và chờ
quality/cooldown riêng; không cộng các nhánh AI song song thành latency toàn tuyến.
Face framing latch dừng zoom và giữ khung khi nhận diện. Identity success không tự
tạo lệnh zoom; không bỏ latch chỉ để tăng tốc. Không sửa model YOLO/OSNet/SCRFD/ArcFace.

## 2. Khả năng API và các hệ tọa độ

[Probe camera thật, chỉ đọc](zoom_audit/read_only_probe.json), 09:29:40 +07:
firmware `1.000.0000.0.R`, build `20250908`; `ptz.getCurrentProtocolCaps` trả
`result:true`, `Move` có `Absolutely`, `MoveAbsolutely.PTZ` có `Zoom`.
`ptz.listMethod` có `moveAbsolutely`, `start`, `stop`, `isMoving`.
`ptz.isMoving` trả `params.moving:false`; status báo ZoomStatus/FocusStatus Idle.
Các lời gọi PTZ cần object handle lấy từ `ptz.factory.instance`; probe đã dùng đúng handle.

**Firmware quảng bá hỗ trợ absolute zoom.** Probe không di chuyển nên không chứng minh
PositionABS đã đến đúng đích trên firmware hiện tại. Tên method không chứng minh đơn vị.

| Giá trị | Ý nghĩa/nguồn | Xử lý |
|---|---|---|
| `PTZZoomHD=1043` | Encoder/raw position quan sát được | Log nguyên giá trị; chưa xác minh toàn range |
| `ZoomValue=11` | Thang zoom vendor; ứng dụng dùng `/10 = 1.1` | Không gọi đây là optical magnification đã hiệu chuẩn |
| `Postion[2]=0.06365966796875` | Normalized encoder do firmware trả | Không đưa trực tiếp vào PositionABS |
| PositionABS normalized | Hệ tọa độ **lệnh**, ánh xạ lịch sử trong repository | Vùng danh nghĩa 0.15–1; khác normalized encoder |
| GUI 1–128 | `round(position*127)+1`, truyền `arg3` | Log thêm GUI value và normalized sau lượng tử hóa |

Nguồn mapping là bảng **đo lịch sử** trong
`phanmem_v1.0/phanmem/loi/zoom_kem_net.py`, ghi ngày 27/08:

| PositionABS | .15 | .30 | .45 | .60 | .75 | .90 | 1.00 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ZoomValue/10 | 6.4 | 11.7 | 17.1 | 22.5 | 27.9 | 33.3 | 37.0 |

Không tráo bảng này với `ZOOM_CURVE` của encoder. Dưới khoảng .14, tài liệu lịch sử
báo lệnh 0 không đạt wide như mong muốn, nên giữ fallback có tên rõ. Bảng lịch sử
chưa có bằng chứng camera ID/firmware đầy đủ; cần tái kiểm tra nội vùng có giám sát.
HFov/VFov trong probe đều bằng 0: không dùng chúng làm FOV quang học.

`move_to_zoom_position()` gửi **một** `ptz.start(code=PositionABS)`, kế thừa giao thức
zoom-only của implementation đã đo trong repository. `arg1/arg2=0` là các trường
padding của mã lệnh này, không phải tọa độ Pan/Tilt của `ptz.moveAbsolutely`.
Không gọi API ba trục với Pan/Tilt mặc định. Không bổ sung một mapping
`ptz.moveAbsolutely`/ONVIF/CGI chưa kiểm chứng. Repository không có thư viện ONVIF
phù hợp đang dùng cho đường zoom này.

Đã tham khảo [OpenIPC python-dhip client](https://github.com/OpenIPC/python-dhip/blob/master/dahua/client.py)
cho status/caps/isMoving và sự khác nhau giữa đơn vị encoder, tọa độ lệnh. Đó không
phải bằng chứng đơn vị của firmware cụ thể này; bằng chứng local được giữ riêng ở trên.

`ZoomRPC._call()` kiểm tra `result/error`, giữ lỗi RPC đã lọc; không coi HTTP 200 là
thành công. Rejection hoặc mất ACK absolute dẫn đến STOP/error, **không tự thử pulse**.
Capability false/unknown chọn pulse có event `ABSOLUTE_ZOOM_UNSUPPORTED`.
Session `read_only=True` chỉ cho whitelist API đọc, không keepalive có nhánh STOP,
không STOP hay restore focus/config lúc đóng.

## 3–4. Estimator và trình tự sau sửa

`camera/zoom_prediction.py` tách `ZoomCalibration`, `ZoomEstimator` và `PositionABSMapping`:

```text
M_required = zoom_to_magnification(current_zoom_value) * desired_ratio / current_ratio
target_zoom_value = magnification_to_zoom(M_required)
requested_position = historical_command_mapping(target_zoom_value)
```

LUT là piecewise linear đơn điệu, đảo đơn điệu, clamp đích vào miền đo;
current nằm ngoài miền calibration bị từ chối. Loader đòi schema, camera ID,
reference cố định, fixed distance, mẫu stable, số hữu hạn, thứ tự tăng nghiêm ngặt
và **holdout độc lập đạt ngưỡng**. Camera ID phải khớp serial đọc từ camera đang kết nối.
Không tự nạp LUT cũ thiếu provenance trong `phanmem_v1.0`.

Chưa có LUT hợp lệ cho camera hiện tại: chế độ mặc định là
`GEOMETRIC_ZOOMVALUE_APPROXIMATION`, `calibration_valid:false`,
`UNCALIBRATED_OPTICAL_SCALE`: dùng `current_zoom_value*desired/current`, clamp 1–37.
Đây là xấp xỉ, không giả định đã có optical calibration. Mẫu person và face dùng
ratio/desired riêng, tính trên ảnh gốc trước Digital PTZ. Chỉ nhận frame/session mới.

```text
IDLE → TARGET_LOCKED → ZOOM_PLANNING → ZOOM_MOVING
     → ZOOM_SETTLING → ZOOM_HOLD
                    → ZOOM_CORRECTING → ZOOM_MOVING (tối đa 2 lần)
Lỗi RPC/telemetry/absolute timeout/settle timeout → ERROR
```

- Một đích chính không còn bị giới hạn hệ số 1.5. Absolute timeout mặc định 8 s,
  không dùng thời lượng pulse để cắt lệnh chính.
- Không lập đích mới trong lúc busy. Vẫn cho STOP vì mất target/session, frame cũ,
  đổi loại target, đạt framing, mép ảnh, giới hạn, đổi chiều an toàn hoặc manual override.
- ACK chỉ là ACK. Hoàn tất cần status **sau ACK**, ZoomValue gần đích (±0.3) và
  ZoomStatus Idle. Nếu không có trạng thái motor, cần ba mẫu vị trí gần đích,
  đánh dấu `estimated_position_plateau` và gửi STOP phòng ngừa.
- Sau đó cần ba frame khác nhau, cùng loại bbox, dao động ratio ≤2%, sau settle.
  Đây là ổn định kích thước ước lượng, không phải chứng nhận độ nét.
- Hiệu chỉnh giới hạn 15% mức hiện tại, tối đa hai lần; residual trong deadband thì HOLD.
  Target thay đổi đáng kể so với bbox đã HOLD cho phép lập kế hoạch chính mới.
- Empty face mailbox chỉ gia hạn watchdog khi person/session hiện tại vẫn hợp lệ.
  Face miss thực sự, mất body, stale/ambiguous target vẫn dừng.
- Một camera command executor thực hiện motor; orchestrator loại trừ AF/zoom/manual.
  Nhánh keepalive lỗi vẫn có fail-safe STOP có sẵn, không tạo START thứ hai.

`AF_STARTED/AF_COMPLETED` chỉ nói về worker AF của ứng dụng. `FOCUS_STABLE` yêu cầu
quality thực tế từ ảnh. `af_state` trong ZOOM_STATUS là telemetry firmware nếu có.
Chưa quan sát motor thật trong task này nên không kết luận firmware tự AF sau zoom
hay AF của firmware đã gây gián đoạn.

## 5. Các file/function thay đổi

| File | Thay đổi chính |
|---|---|
| `camera/dahua_rpc.py` | `_call`, `RPCError`, `read_only`, `close`, `goto`, `protocol_caps`, `motion_status`; mã lệnh/GUI/RPC ACK/error |
| `camera/ptz_controller.py` | `probe_zoom_capability`, `read_status`, `move_to_zoom_position`, `start_zoom_target`; raw telemetry, camera binding |
| `camera/zoom_capability.py` | Parse capability true/false/unknown |
| `camera/zoom_prediction.py` | Calibration, inverse, validation, prediction, command mapping |
| `control/auto_zoom.py` | State machine, main move, bounded corrections, completion/settle/watchdog, heartbeat |
| `runtime/inference_worker.py` | Async mailbox heartbeat, domain của stability, AF-wait measurement |
| `control/auto_focus.py` | Event worker AF bắt đầu/kết thúc với outcome |
| `telemetry/zoom_trace.py`, `telemetry/latency.py` | Trace/CSV riêng, chạy cả khi latency profiling tắt |
| `config.py`, `app/application.py`, `gui/cctv_app.py` | Cấu hình estimator/log, init và flush lúc shutdown |
| `tools/probe_zoom.py` | Probe không chuyển động |
| `tools/zoom_calibration.py` | Thu mẫu passive và build/holdout LUT |
| `tools/zoom_benchmark.py`, `tools/zoom_report.py` | Mock, supervised interior motor test, so sánh CSV và trích baseline |
| `tools/camera_diagnostics.py` | Diagnostic mặc định dùng RPC read-only, không restore config/STOP |
| `test/test_absolute_zoom_audit.py`, `test/test_dahua_camera.py`, `test/test_auto_zoom.py` | Regression và mock không phần cứng |

## 6. Logging, calibration và benchmark

Mặc định log riêng:

```text
logs/zoom/zoom_trace_YYYYMMDD_HHMMSS_microseconds.jsonl
logs/zoom/zoom_summary_YYYYMMDD_HHMMSS_microseconds.csv
```

Writer bất đồng bộ, queue 8192, file xoay vòng 8 MB × 4 mỗi lần chạy;
`dropped_trace_events` trong CSV báo mất event nếu queue đầy. Các session lưu trong CSV
của lần chạy, key `lock_session_id`; lệnh manual không có session vào `UNSCOPED`.
Trace được ghi ngay, summary cập nhật định kỳ theo event/idle và flush khi đóng app.

Mỗi event có schema thống nhất chứa các field yêu cầu (frame/track/session,
position/source, person/face ratio, estimated/requested position, estimator validity,
actual method, command ID/latency, motor/AF/focus state, rejection, corrections).
Chưa đo được là JSON `null`, CSV `NULL`. `zoom_error`/`zoom_position_error` là NULL
vì raw encoder và tọa độ lệnh khác nhau; không lấy chúng trừ nhau.
`zoom_value_error` mới là sai lệch trên cùng thang ZoomValue/10.
`zoom_prediction_error` là ratio quan sát trừ desired ratio.

Đếm lệnh tại **`ZOOM_COMMAND_SENT`**, lọc `rpc_method=ptz.start`, chia theo
`rpc_code=PositionABS` hoặc `ZoomTele/ZoomWide`. Không đếm plan, ACK hay STOP là lệnh zoom mới.
RPC errors có count riêng. Số correction cũng dựa trên lệnh đã dispatch.
Mean RPC latency gồm RPC motor zoom, kể cả STOP; motor duration chỉ có khi quan sát
được onset và completion, là ước lượng theo polling. AF waiting duration chỉ ghi khi
có nhu cầu zoom nhưng application AF đang busy. Không suy diễn unknown thành 0.

Các latency end-to-end trong CSV lấy hiệu timestamp monotonic:
lock→first command, first command→visual stable, first command→usable face,
lock→identity confirmed. Không cộng thời gian các nhánh song song. Session bị hủy
hoặc chưa nhận diện có metric NULL, không loại bỏ im lặng khi so sánh A/B.

Các lệnh bên dưới chạy trong environment `Autofocus_Camera`:

```bash
conda activate Autofocus_Camera
# Chỉ API đọc, không chạy motor:
python tools/probe_zoom.py --output /tmp/zoom_probe.json
# Không kết nối camera, đi qua controller/worker/RPC transport mock:
python tools/zoom_benchmark.py --mock
# Mặc định không có cờ cũng chỉ probe đọc:
python tools/zoom_benchmark.py
# CHỈ chạy khi chủ động muốn test motor, có người giám sát:
python tools/zoom_benchmark.py --execute-motor --supervised --position 0.45
```

Motor test chỉ cho nội vùng `.15 < position < 1`, kiểm tra cả GUI sau lượng tử hóa,
không cho lệnh làm tròn tới endpoint. Test này chỉ đo lens; không đo time-to-identity.
**Lệnh motor trên chưa được chạy trong task này.**

Thu calibration: đặt cùng vật chuẩn/cùng khoảng cách; tự di chuyển camera đến các
mức nội vùng. Giữ camera và vật đứng yên, lưu ảnh **gốc không crop**, đo bbox ratio.
Mỗi lần `sample` kiểm tra năm status, cả zoom/focus Idle và vị trí ổn định;
người đo phải xác nhận ổn định hình ảnh/khoảng cách. Ảnh phải vừa lưu trong 30 s;
đảm bảo không thay đổi zoom giữa ảnh và sample. Tool lưu hash/timestamp ảnh,
source annotation thủ công, camera serial, raw position, ratios, motion/focus state.
Nó không tự chứng minh ảnh thật sự cùng reference; xác nhận của người đo là một phần provenance.

```bash
python tools/zoom_calibration.py sample --output /tmp/zoom_samples.jsonl \
  --reference fixed-board-1 --image /tmp/original_frame.png \
  --person-ratio 0.12 --fixed-distance --visual-stable
# Lặp lại ở >=4 mức, giữ ít nhất 1 điểm nội suy làm holdout, không dùng để fit:
python tools/zoom_calibration.py build --samples /tmp/zoom_samples.jsonl \
  --holdout 2 --ratio-kind person --output /tmp/measured_zoom_lut.json
```

Các số trên minh họa cú pháp, không phải mẫu calibration thật. Cần nhiều mức và
nhiều lần đo lặp hơn để đánh giá sai số đáng tin. Holdout phải nằm trong miền fit,
khác vị trí training; threshold mặc định 10%, reject nếu vượt, không extrapolate.

Tùy chọn thêm vào `camera.json` (giữ nguyên các key khác):

```json
"zoom_control": {
  "mode": "auto",
  "calibration_path": null,
  "camera_id": null,
  "absolute_timeout": 8.0,
  "absolute_settle_timeout": 3.0,
  "max_corrections": 2,
  "correction_fraction": 0.15
},
"zoom_logging": {"enabled": true}
```

Sau khi có LUT đạt validation, đặt path và serial đúng vào hai trường null. Không
có LUT thì dùng xấp xỉ có nhãn rõ. Cho A/B thực tế: chạy A với `mode:"pulse"`,
B với `mode:"auto"` và LUT thực; giữ cùng cảnh, target, khoảng cách, zoom ban đầu,
ánh sáng, tốc độ motor, model/scheduler. Mỗi cấu hình cần nhiều phiên thành công
và thất bại, ghi rõ thời điểm/config. Mode pulse là baseline hiện tại đã sửa an toàn,
không tái tạo các bug mailbox của phiên cũ.

```bash
python tools/zoom_report.py --summary path/to/A.csv path/to/B.csv --output /tmp/ab.json
python tools/zoom_report.py \
  --legacy-camera logs/camera/camera_control_20261008_091528_174507.log \
  --legacy-app logs/ptz_20261008_091522.log
```

So sánh command count, correction, AF wait và các latency trực tiếp; không dùng
mock timings làm kết quả phần cứng. Chưa có A/B thật nên **chưa kết luận B nhanh hơn**.

## 7. Kiểm thử và bằng chứng một lệnh

```bash
python tools/run_unit_tests.py
```

Kết quả: **201 tests pass** trong môi trường `Autofocus_Camera`; có hỗ trợ/không hỗ trợ,
RPC reject, out-of-range, LUT invalid/nonlinear/holdout/saturation, stale session/frame,
missing target, watchdog, AF ownership, manual override, serialize worker, bounded
correction, shutdown và pipeline regressions. Không test nào chạy motor thật.
Compileall và pyflakes trên các file sửa pass.

[Mock result](zoom_audit/mock_result.json) trỏ đến JSONL/CSV đã lưu. Chạy đầy đủ
`AutoZoomController → CameraCommandWorker → PTZController → ZoomRPC → MockFirmware`:

```json
{
  "evidence_source": "mock",
  "absolute_commands": 1,
  "pulse_commands": 0,
  "ack_preceded_completion": true,
  "final_state": "ZOOM_HOLD",
  "corrections": 0
}
```

Đích ví dụ từ ZoomValue-scale 3 lên khoảng 15; log gửi
`actual_command_method:"ptz.start:PositionABS"`, `rpc_code:"PositionABS"`.
Nhiều frame trong lúc MOVING không phát lệnh thứ hai. Test khác cố ý tạo residual,
chỉ cho hiệu chỉnh sau stability và chặn khi hết ngân sách.
Đây là bằng chứng **đường phần mềm gửi đúng một absolute command tới transport mock**,
không phải tuyên bố đã bắt gói lệnh motor của camera thật sau sửa.

## 8. Chưa xác minh bằng phần cứng hiện tại

- Arrival/độ chính xác và thời gian PositionABS trên firmware build 20250908;
  range/raw encoder đầy đủ; tái xác nhận mapping lịch sử, lượng tử hóa/hysteresis.
- Optical LUT camera-bound đo mới và sai số holdout thực. Chưa tạo LUT bằng dữ liệu ví dụ.
- Firmware có tự AF sau zoom không, AF có ngắt chuyển động không trong cấu hình hiện tại.
- A/B time-to-ratio, time-to-usable-face, time-to-identity, số corrections trên cảnh thật.
- Visual bbox stability là ước lượng; cần kiểm tra độ trễ RTSP, blur và scene motion thật.

Các phần này được để lại dưới dạng test chủ động có giám sát đúng yêu cầu; không
bật motor hay chạy endpoint tự động để hoàn thành báo cáo.
