# Tracking và tốc độ PTZ — audit, benchmark và thử nghiệm offline

## Kết quả và trạng thái thực hiện

Đã hoàn tất backup, audit source/log/tài liệu, công cụ benchmark motor mặc định mock, thử KLT offline, so sánh policy trên input ghi nhận và unit tests. **Chưa hiệu chuẩn motor thật, chưa triển khai controller adaptive/predictive có quyền điều khiển camera, chưa nối local tracker vào target identity/PTZ.** Điều kiện “sau hiệu chuẩn phần cứng” trong yêu cầu chưa thỏa: chưa có speed→°/s, FOV theo zoom, startup/coast hoặc timestamp exposure được xác minh.

Không tăng tốc motor theo giả định 300°/s. Không sửa firmware, không phát lệnh motor, không thay tracker/OSNet, zoom/focus/face/GUI hoặc các guard hiện tại. Các cải tiến lần này là công cụ đo và khả năng đánh giá để quyết định thay đổi controller có bằng chứng.

## 1. Backup và khôi phục

- Backup đầy đủ: `/mnt/files/Project/ptz_before_latency_ptz_20261116_133521/project`.
- **8.827 mục** xác minh checksum/permissions/symlink, không loại trừ. Có Git metadata, mã overlap chưa commit, model/data/target, configs/logs/video. `manifest.json`, `git_status.txt`, `RESTORE.md` nằm ở thư mục cha.
- Branch mới: `dev/ptz-latency-calibration-20261116`; các thay đổi overlap trước đó vẫn giữ nguyên.
- Kiểm tra cuối: **211 file source/config hiện có không đổi** so backup. Bốn file mới là ba tool và một test module; tài liệu/kết quả nằm trong thư mục này. [integrity.json](integrity.json).

Chạy bản trước tác vụ, sau khi đóng ứng dụng đang chạy:

```bash
cd /mnt/files/Project/ptz_before_latency_ptz_20261116_133521/project
conda activate py
python ptz/main.py
```

Khôi phục thành bản làm việc độc lập, đích phải chưa tồn tại:

```bash
cp -a /mnt/files/Project/ptz_before_latency_ptz_20261116_133521/project /mnt/files/Project/PTZ_RECOVERED_BEFORE_LATENCY
```

## 2. Speed=4 thực sự là gì?

Chuỗi mapping đã kiểm tra:

1. `config.py::PTZ_SPEED` đọc `ptz_speed` từ cấu hình. GUI Motor Speed gọi `ptz.set_speed`.
2. `camera/hanet_controller.py::_clamp_speed` giới hạn số nguyên **1..8**.
3. `HanetController.command` truyền speed vào `HanetPTZClient.motion`.
4. `camera/hanet_rpc.py::_motion_payload` đưa giá trị vào **`params.data.step`**, method `Control.DoPtz`.

Vì vậy **4 là mức phần mềm gửi vào tham số step của giao thức**, không phải 4°/s và không có chứng cứ tuyến tính “4/8 = nửa tốc độ cực đại”. Driver đã dùng miền 1..8; firmware chưa cung cấp bảng mapping được xác minh.

Auto pan/tilt **không lấy trực tiếp slider 4**: `control/pan_policy.py::plan_pan` tính mức `min(max_speed, max(1, 1 + int(abs(error)*6)))`, với max_speed=3. `error` là độ lệch tâm bbox chia cho chiều ảnh. Chọn trục có độ lệch lớn hơn; không gửi pan và tilt độc lập đồng thời.

Hướng RPC: pan_left→kCmdLeft, pan_right→kCmdRight, tilt_up→kCmdUp, tilt_down→kCmdDown. START=`kOperateStart`, STOP=`kOperateStop`, qua WebSocket. Kênh RPC đang hardcode 0. Không đưa credentials vào báo cáo.

## 3. 300°/s và giới hạn driver/firmware

PDF cục bộ [HANET PTZ PANORA PF2 HN-PTZF258V](<../../../HANET PTZ PANORA PF2_HN-PTZF258V.pdf>), bảng PTZ, ghi pan 0,1–300°/s; tilt 0,1–100°/s. Bảng không nói rõ tốc độ cực đại áp dụng preset hay continuous movement, không có mapping step/gia tốc/điều kiện đạt tốc độ đó. Không suy ra tốc độ đo trên camera từ thông số quảng bá.

Chưa xác minh model/firmware trên thiết bị đang kết nối; không truy vấn hoặc sửa firmware. Tra cứu web không cung cấp tài liệu API chính hãng đủ để bổ sung bảng mapping chắc chắn cho firmware đang chạy; dùng source driver và PDF cục bộ làm bằng chứng chính.

| Khả năng | Điều driver hiện có chứng minh |
|---|---|
| START tới STOP | Có RPC và ACK; dạng lệnh duy trì chuyển động cho tới STOP |
| Đặt vận tốc °/s | Không; chỉ tham số step |
| Cập nhật speed khi đang chạy | Chưa được hỗ trợ/xác minh; `command` dừng code đang active trước lệnh mới |
| Pan/tilt speed độc lập đồng thời | Không có trong đường điều khiển đã kiểm tra |
| Absolute/relative pan/tilt | Không có API đã xác minh trong adapter |
| PTZ position feedback | `telemetry()` trả None, không có encoder pan/tilt đáng tin cậy |
| Zoom/FOV telemetry | `has_zoom_telemetry=False`; không có FOV calibrated theo zoom |
| Focus/AF | HANET adapter đánh dấu không hỗ trợ |

Không coi ONVIF trên datasheet là tính năng đã được adapter này triển khai. Cấu hình app dùng max zoom 37 trong khi tài liệu sản phẩm mô tả 32X; chưa xác minh thiết bị nên không sửa giá trị hoặc dùng nó để tính góc.

## 4. Vì sao có thể cảm thấy quay chậm?

`AutoPanTiltController` dùng xung **120 ms** rồi nghỉ **150 ms**. Duty cycle lý tưởng tối đa chỉ khoảng **44,4%**, trước RPC/scheduling/guard; chu kỳ lý tưởng 270 ms (~3,70 xung/s). Đây là tính từ cấu hình, không phải tốc độ motor đo được. Gia tốc khởi động có thể làm xung ngắn kém hiệu quả, nhưng hiện chưa đo để kết luận.

Sau STOP, worker còn yêu cầu observation mới hơn `_last_stop`. Với detection khoảng 9 FPS, có thể chờ frame mới. `MOTOR_BUSY`, cooldown, session invalidation và STOP latch là guard thật, không phải lỗi được phép xóa để tăng tốc. Bộ điều phối cho một owner, `CameraCommandWorker` tuần tự hóa I/O và ưu tiên lệnh dừng; STOP không xác nhận giữ fault thay vì trả quyền điều khiển.

Phiên mới nhất **12:28:05–12:33:12**, run `ptz_20261116_122805`, số liệu [historical_metrics.json](historical_metrics.json):

| Chỉ số | Giá trị |
|---|---:|
| Auto pan/tilt dispatch | 381 |
| Mức 1 / 2 / 3 | 188 / 164 / 29 |
| Pan/tilt RPC mean / p95 | 6,19 / 11,78 ms |
| Detection / camera receive | 8,95 / 29,15 FPS |
| ReID total mean / p95 | 126,21 / 244,63 ms |
| Receive→GUI submit mean / p95 | 117,14 / 90,85 ms |
| MOTOR_BUSY / COOLDOWN evaluations | 615 / 631 |
| Full loss / recovery counters | 6 / 6 |

Mean/count của LATENCY_REPORT là toàn profiler, percentile là bounded recent window; mean cao hơn p95 có thể do outlier/phạm vi khác, không phải phép đo exposure-to-screen. Các evaluation counter không phải số chuyển động độc lập.

Lớp RPC có 400 START_SENT, 399 START_ACK; 400 STOP_SENT/ACK (nhóm tên PAN_TILT còn bao gồm zoom). **12:31:53.634** WebSocket closed, START unconfirmed; STOP unconfirmed ban đầu, sau đó MOTOR_FAULT_RECOVERED **12:31:54.196**. Đây là gián đoạn thật, không kết luận mọi đoạn chậm đều do mạng. Không loại bỏ STOP/session guards để che lỗi này.

## 5. Công cụ benchmark motor

[tools/benchmark_ptz_motor.py](../../tools/benchmark_ptz_motor.py): mặc định MockMotor, một pulse, step 1..8, duration 20–500 ms, direction configurable. Ghi START request/return, STOP request/return, clock monotonic và wall time, RPC latency; xuất `.json`, `.jsonl`, `.csv`. File output đã tồn tại bị từ chối. Logging thất bại vẫn phải thử STOP.

Mock, không kết nối camera:

```bash
cd /mnt/files/Project/ptz/ptz
conda activate py
python tools/benchmark_ptz_motor.py --direction pan_right --speed 1 --duration 0.12 --output /tmp/ptz_mock_trial.json
```

**Chỉ operator thực hiện bước sau khi quan sát được camera và đã đóng GUI/các controller khác:**

```bash
python tools/benchmark_ptz_motor.py --physical --direction pan_right --speed 1 --duration 0.08 --output /tmp/ptz_physical_step1.json
```

Tool yêu cầu terminal tương tác và gõ chính xác câu ARM có hướng/mức/thời gian sau khi xác nhận vùng quay an toàn. Preflight STOP phải có ACK rồi mới START. Ctrl-C/SIGTERM yêu cầu hủy và STOP; finally đóng controller. Để lên mức cao hơn, bắt buộc có file step 1 physical cùng thiết bị/hướng, STOP ACK và operator xác nhận đã dừng an toàn:

```bash
python tools/benchmark_ptz_motor.py --physical --direction pan_right --speed 2 --duration 0.08 --previous-low-speed /tmp/ptz_physical_step1.json --output /tmp/ptz_physical_step2.json
```

Không tự quét speed hoặc tự tăng step. Giới hạn sector/cáp là kiểm tra trực tiếp của operator vì không có position feedback. Duration là giới hạn yêu cầu phần mềm; khi RPC bị kẹt, STOP có thể phải chờ timeout/lane, **không đảm bảo hard real-time hoặc hard angular boundary**. ACK không chứng minh motor đã dừng vật lý. Chưa chạy bất kỳ lệnh `--physical` nào.

### Đo góc, video, startup/acceleration/coast

Không gán độ dịch chuyển pixel thành góc khi FOV chưa biết. Tool nhận CSV từ encoder hoặc landmark đã hiệu chuẩn, cột `t_s,angle_deg,video_pts_s`; t_s của START/STOP và samples phải cùng clock. Phải có samples trước START và sau STOP. `video_pts_s` là PTS nguồn, không mặc nhiên là exposure timestamp.

```bash
python tools/benchmark_ptz_motor.py --measurements /path/to/measured_angles.csv --start-s 10.0 --stop-s 10.12 --output /tmp/ptz_angle_analysis.json
```

Các số 10.0/10.12 chỉ minh họa tham số, phải thay bằng thời điểm đồng bộ thực. Xuất displacement, mean/peak angular speed, onset delay theo ngưỡng 2°/s, đạo hàm gia tốc và coast sau STOP. Đạo hàm nhạy noise; không phải đặc tính firmware đã chứng minh. Nếu không có samples, metric góc/video/acceleration giữ **null**, không giả lập thành số đo thực.

## 6. KLT và predictive control

[evaluate_local_tracking.py](../../tools/evaluate_local_tracking.py) tái sử dụng forward/backward KLT sẵn có của `FaceTracker` cho ROI thân người ở tool offline. Không thay pipeline face. Có max age 200 ms, FB/inlier/scale checks, reset khi session đổi, timestamp đảo, camera_moving=True hoặc hết evidence. Output luôn `LOCAL_VISUAL_ONLY`, `identity_confirmed=False`, `ptz_authorized=False`.

Đã chạy **180 frame / 6,005 s** đầu video `16_11(1).mov`, resize 704×480, YOLO mỗi 3 frame. [Kết quả](local_video_probe.json):

| Chỉ số probe | Giá trị |
|---|---:|
| Detector ROI corrections | 56 (~9,33/s video) |
| Local geometric observations | 164 (~27,31/s video) |
| KLT compute mean / p95 | 2,51 / 4,14 ms |
| IoU agreement với detector | 0,881 trên 52 điểm đối chiếu |

ROI chọn/giữ bằng geometry phục vụ feasibility; không có OSNet/ground-truth identity cho probe. Đây không phải proof đúng người, không phải FPS end-to-end app. Camera motion chưa được bù trong clip; image-space velocity có cả target và camera movement. Không đưa velocity này thẳng tới motor.

```bash
python tools/evaluate_local_tracking.py --video '../16_11(1).mov' --frames 180 --output /tmp/ptz_local_probe.json
```

App có `runtime/camera_motion.py` với background flow/affine diagnostics, nhưng không có calibration FOV và motion-ground-truth để dùng như nguồn góc điều khiển. Các điều kiện cần trước triển khai predictive: FOV thực ở từng mức zoom, thời gian exposure/receive, motor response delay/coast, chuyển động camera đáng tin và horizon hữu hạn. Khi thiếu các đầu vào đó phải giữ controller cũ; không tạo góc/vận tốc giả từ zoom_estimate=nan.

## 7. So sánh A/B/C/D: phần đo được và phần chưa có

[compare_ptz_policies.py](../../tools/compare_ptz_policies.py) dùng **832 observations ghi nhận giống nhau** để so pure planning; shape suy ra từ target_center và normalized_error trong chính event, không gán FOV. `observations.jsonl` là mẫu telemetry, không phải video đầy đủ.

- A: controller hiện tại, 684 đề xuất MOVE, mean pulse 120 ms.
- B_pulse_proxy: bật cờ adaptive-pulse đã có sẵn chỉ trong bản sao config offline: vẫn 684 đề xuất MOVE, mean pulse **177,32 ms**. **Không phải adaptive velocity/controller đã hiệu chuẩn**.
- C/D: chưa có local observations được identity-admit đồng bộ với trace này; không đưa chúng vào control. Kết quả trùng A/B, không coi là so sánh tracking+KLT đầy đủ.
- Không mô phỏng scheduling/motor plant. Số đề xuất không phải số command thực (381); recenter time, overshoot, angular speed, ID switches/correct-target time đều null. Không tuyên bố cải thiện tracking/latency production từ proxy này.

```bash
python tools/compare_ptz_policies.py --observations docs/ptz_latency_20261116/observations.jsonl --output /tmp/ptz_policy_proxy.json
```

B/D theo đúng yêu cầu ban đầu vẫn chờ hardware calibration và ground truth. Không có lệnh bật “new calibrated controller” vì chưa triển khai controller đó. Cờ AUTO_PAN_TILT_ADAPTIVE của code cũ chỉ đổi pulse length và đang false trong app; không nên coi nó là controller mới hoặc tự bật ở camera để đạt 300°/s.

## 8. Tests, file thay đổi và bước tiếp theo

Thêm ba tool và `test/test_ptz_latency_tools.py` (15 test). Full suite **623 mục: 620 đạt, 3 lỗi thiếu gi/GStreamer có sẵn**, gồm test_gstreamer_cpu, test_sharpness và RTSP backoff test. Mọi socket connect/create_connection bị chặn trong suite. Test coverage gồm STOP khi START lỗi, STOP lỗi không báo thành công, hủy trước START, log writer lỗi vẫn STOP, input limits, angle samples, KLT translation/session/age/camera motion/unverified seed. [tests.log](tests.log).

Không thay source/config production so với backup tác vụ này. Chạy ứng dụng hiện tại vẫn là:

```bash
cd /mnt/files/Project/ptz
conda activate py
python ptz/main.py
```

Bước operator cần làm: đo low-step từng hướng với video/landmark hoặc encoder đồng bộ, tăng từng mức có kiểm soát, giữ zoom cố định và ghi model/firmware/FOV. Sau khi có curve step→velocity/startup/coast, mới đủ cơ sở viết controller có angular error, velocity prediction, braking, bounded horizon và feature flag. Tiếp đó cần video có ground-truth target để đo A/B/C/D đúng nghĩa, cùng zoom/AF/session regressions. Hiện chưa có đo phần cứng để xác nhận 300°/s đạt được qua mode này.
