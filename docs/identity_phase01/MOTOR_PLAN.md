# Phase 5 — audit và kế hoạch đo, không triển khai motor mới

Code đã kiểm tra: `camera/hanet_rpc.py:HanetPTZClient.motion` gửi START/STOP với speed step; `camera/hanet_controller.py:_poll/read_status/telemetry` không cung cấp measured pan/tilt encoder pose. `move_focus` báo không có focus position; zoom capability không đồng nghĩa có optical zoom feedback hiệu chuẩn. ACK xác nhận giao thức, không phải physical motion/stopped. Không query camera để xác nhận thêm trong nhiệm vụ này.

Đã có `tools/benchmark_ptz_motor.py`: mock mặc định; physical mode cần TTY, câu arming cụ thể, bounded requested pulse, preflight STOP và kiểm tra bước 1 trước tốc độ cao hơn. `measured_motion` đọc external time/angle CSV; ngưỡng chuyển động 2 degrees/s trong tool hiện tại là heuristic của analysis, cần kiểm sensitivity với noise floor. Không sửa hoặc chạy physical mode ở nhiệm vụ này. Không tạo công cụ motor thứ hai.

Khi người vận hành thực hiện một đợt đo riêng, dùng camera/calibrated target hoặc reference encoder bên ngoài, đồng bộ clock của hình ảnh và START/STOP request. Ghi video trước START tới sau khi dừng; ghi zoom setting thực đo nếu có, speed index API, hướng, clock uncertainty, transport latency, physical onset/end annotation. Không dùng ACK làm mốc first/last motion.

| Thử nghiệm | Dữ liệu cần | Kết quả phải tính |
|---|---|---|
| Mỗi speed index, hai chiều pan/tilt | angle(t), timestamp, zoom setting | velocity plateau mean/dispersion; khoảng không đạt plateau |
| START từ nghỉ | request/ACK và first physical motion | startup delay, acceleration, time-to-speed |
| STOP | request/ACK và last physical motion | stop delay, deceleration, overshoot/coast displacement |
| Pulse lengths trong giới hạn operator đã xác nhận | displacement và duration request thực | pulse→displacement curve, hysteresis, uncertainty |
| Đổi chiều | đi một chiều, STOP xác nhận quan sát rồi chiều ngược | backlash, delay, residual displacement |
| Các mức zoom | phép đo góc và pixel cùng thời điểm | image sensitivity, HFOV/VFOV calibration, speed dependence |

Lặp mỗi ô đủ để báo mean/median/p95 và uncertainty, giữ cả thất bại, không ngoại suy index chưa đo. Thử tốc độ thấp trước; operator phải có vùng chuyển động trống, giới hạn cáp và STOP độc lập, dừng khi response/motion không khớp. Requested duration không phải giới hạn physical bảo đảm nếu network bị treo. Không giả định 300 degrees/s từ quảng cáo/API.

Dry-run controller tương lai chỉ đủ cơ sở khi có HFOV/VFOV theo zoom, target velocity/residual uncertainty, command/physical-delay distributions và STOP characterization. Angular error có thể suy từ calibrated pinhole projection; latency prediction phải dùng observation timestamp thật tới thời điểm tác động đã đo. Các trường đầu vào còn thiếu thì HOLD, không tạo góc/tốc độ giả.

Thiết kế cần giữ nguyên: STOP priority, session token và track generation validation, command expiry, age tại dispatch, manual override, motor busy/settling, speed/acceleration limit và persisted fault latch. Deadband/hysteresis và pulse/continuous command chỉ chọn sau protocol/measurement validation. Recovery zoom-out cần budget và uncertainty; chưa có triển khai predictive control hoặc physical calibration result ở đây.
