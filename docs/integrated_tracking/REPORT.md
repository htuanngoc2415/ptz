# Tracking/PTZ tích hợp vào ứng dụng hiện có

Theo yêu cầu sửa phạm vi: không xây lại giao diện, không chạy app prototype riêng. Chạy như trước:

```bash
cd /mnt/files/Project/ptz/ptz
python main.py
```

Chọn camera/ảnh target, dùng các nút Auto Pan/Tilt, zoom, focus, nhận diện và ghi hình như giao diện cũ. Không có launcher, cửa sổ OpenCV hoặc cách nhập RTSP mới. Không đổi trạng thái bật/tắt motor, cấu hình tài khoản hay các ngưỡng điều khiển đã có.

## Đã gỡ

Đã xóa khỏi source tree: `prototype.py`, package `prototype_v1/`, `tools/prototype_v1_*.py`, `tests/prototype_v1/`, `docs/prototype_v1/` và bytecode launcher liên quan. Bản archive kỹ thuật của phần bị gỡ nằm ngoài Git root, đường dẫn ở `retirement.json`. Không dùng archive đó khi chạy ứng dụng. Video/log người dùng đã ghi và ảnh target giữ nguyên.

## Các thay đổi nằm ngay trong luồng hiện tại

1. `ai/approach_risk.py` + `ai/hybrid_reid.py`: theo dõi khoảng cách tương đối giữa target và các người khác qua detections. Khi đang tiến gần trước overlap, yêu cầu OSNet verification sớm qua background worker có sẵn, tối đa mỗi 200 ms và chỉ một job in-flight. Lần kiểm risk này xét cả competitors thay vì lọc bỏ người chưa overlap. Không nâng identity theo geometry; overlap/negative/margin/epoch gates và reacquisition của selector cũ giữ nguyên.
2. `runtime/target_prediction.py` + `runtime/inference_worker.py`: giữ tối đa một frame trước để đo background motion bằng estimator có sẵn, mask detections. Chỉ tạo velocity khi cùng session/track/generation, frame fresh, GMC valid/confidence ≥.7 và background gần tĩnh. Trừ affine camera displacement đúng một lần khỏi velocity. Khi camera đang quay, zoom hoặc GMC không chắc chắn thì bỏ prediction, trở về điều khiển bằng bbox quan sát như trước.
3. `control/auto_pan_tilt.py`: dùng velocity để bù phần tuổi observation đã đo, horizon ≤150 ms, dịch tối đa 5% kích thước frame; prediction không được đảo chiều qua tâm. Ghi time-to-edge và planned bbox vào telemetry. Bbox target gốc không bị sửa, nên face/zoom/display tiếp tục nhận dữ liệu cũ. Giữ nguyên single motor owner, manual override, zoom lease, freshness, tốc độ/duration/deadband, watchdog và STOP hiện có.

Không chuyển sang KLT/CSRT chưa có bằng chứng tốt hơn. ByteTrack + OSNet hiện tại vẫn là đường tracking chính. Không tuyên bố đây là dense local tracking độc lập detector hoặc calibrated motor model hoàn chỉnh. Không giả định ACK là chuyển động thật, không tự đặt motor latency hay tăng tốc độ motor từ time-to-edge. Bản tích hợp này sửa đúng thuật toán trong ứng dụng cũ; các thử nghiệm nâng cao phải tiếp tục trên luồng đó.

## Bảo vệ tính năng hiện có

Checksum `main.py`, `gui/cctv_app.py`, `camera.json`, `config.py` giống đầu yêu cầu: xem `integrity.json`. Các dirty changes có từ trước không reset/clean/ghi đè. Chỉ chỉnh thêm ba file đang có: `ai/hybrid_reid.py`, `runtime/inference_worker.py`, `control/auto_pan_tilt.py`; thêm hai module thuật toán và tests. `changes.patch` chỉ chứa phần diff của yêu cầu này, không trộn các thay đổi cũ trong Git diff. Snapshot source trước sửa nằm ở `source_before.tar.gz`, không chứa camera credentials.

Tests mock/offline kiểm approach trigger, stale/session reset, motion compensation, prediction bounds/no reversal, existing motor ownership, selector, pan/tilt, zoom, focus, face identity và runtime. Lệnh tái chạy:

```bash
PYTHONPATH=.:test python docs/integrated_tracking/run_tests.py
```

Kết quả cuối: 184 tests chạy, **183 pass, 1 skip** vì môi trường `py` thiếu optional `gi/GStreamer`. Log `tests.log`. Test bị skip thuộc backend RTSP cũ, không sửa backend để che lỗi môi trường. Không mở ứng dụng hoặc gửi motor commands thật trong lần tích hợp này. GUI chưa được kiểm tương tác desktop; source giao diện giữ nguyên. Chưa đo tracking accuracy hoặc tác động độ trễ GMC trên camera thật, không coi synthetic tests là benchmark accuracy.
