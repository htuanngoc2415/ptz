# HANET trong dự án PTZ

Thư mục `ptz/` là gốc chạy độc lập; không import `../camera.py` hoặc `../frigate-dev`.
`camera.json` đã đổi IP RTSP và điều khiển sang `192.168.0.108`, backend `hanet`,
video `opencv`, tốc độ 4 trong dải 1–8. Giữ tài khoản hiện có.

## Điều khiển và giao diện

- `camera/hanet_rpc.py`: đăng nhập digest, heartbeat, WebSocket Control.DoPtz,
  đối chiếu ID phản hồi; timeout không được coi là ACK thành công.
- `camera/hanet_controller.py`: cùng hàng đợi lệnh của ứng dụng, hỗ trợ pan/tilt
  và zoom hướng. Dừng hướng trước khi đổi hướng. START mất ACK vẫn thử STOP;
  STOP không được xác nhận sẽ chặn chuyển động mới cho tới khi STOP thành công.
- `camera.create_controller` chọn backend theo cấu hình; pipeline và công cụ
  camera_diagnostics không còn mặc định gửi Dahua tới camera HANET.
- GUI chính thêm pan/tilt, giới hạn tốc độ 8 và tắt focus/zoom tuyệt đối/reset 1x.
  Không áp dụng backlight CGI Dahua lên HANET.
- `camera_viewer.py` và `main.py --camera-only` cho phép xem/điều khiển không
  tải YOLO/ReID/ArcFace. Chế độ AI vẫn cần môi trường và model của dự án.
- Không suy diễn tọa độ zoom, focus hoặc trạng thái motor từ ACK. Các hằng số
  giới hạn zoom/thời gian cũ không phải hiệu chuẩn của HANET. Auto Zoom dựa trên
  ảnh chưa được kiểm chứng end-to-end trên camera này; autofocus bị vô hiệu.

## Cải tiến tham khảo Frigate

Nguồn tham khảo trong workspace:
`frigate-dev/frigate/video/detect.py`, `video/ffmpeg.py`, `ptz/autotrack.py`.
Đây là áp dụng nguyên tắc, không chép toàn bộ hệ thống NVR:

- Chờ frame có timeout và giữ frame mới nhất (đã tích hợp ở đợt trước).
- Kiểm tra session/ý định zoom còn hợp lệ sau chờ hàng đợi và đăng nhập, ngay
  trước khi gửi chuyển động. Không phát lại START khi kết nối lại.
- Luồng video dùng timeout FFmpeg ngay lúc mở, đọc RGB liên tục, giải phóng
  capture trong finally và xóa mailbox khi mất luồng. Kết nối lại có backoff
  và Event.wait để dừng không phải chờ hết thời gian backoff.
- Heartbeat được tuần tự hóa với lệnh camera, backoff khi lỗi và dừng bằng Event.
- Không thêm motion-gating: camera PTZ đang di chuyển và người đứng yên cần
  kiểm chứng riêng. Không tuyên bố cải thiện FPS hay CPU khi chưa benchmark A/B.

## Xác minh

- `bash run_camera.sh --camera-only --check`: đọc được 704×480 từ gốc ptz.
- `tools/check_camera.py`: 139 frame/5 giây, không lỗi đọc, worker dừng sạch.
- Xung zoom-in 0,15 giây tốc độ 1 rồi STOP được camera ACK; đã kiểm tra lại
  với đối chiếu ID ACK. Không có telemetry để xác nhận quãng đường hoặc vị trí
  dừng vật lý. Không tự chạy thử pan/tilt dài hoặc hiệu chuẩn ống kính.
- Kết quả: `logs/hanet_integration_readonly.json`,
  `logs/hanet_integration_motion.json`, `logs/hanet_integration_verified_ack.json`.
- 14 test mới cho controller, ACK và video; cùng 9 test liên quan có sẵn đạt.
- Bộ test đầy đủ: 249 test, vẫn còn 4 failures và 20 errors thuộc nhóm lỗi cũ
  absolute zoom và môi trường AI/GStreamer. Log: `logs/hanet_unit_tests.log`.
- Môi trường hiện tại thiếu PyTorch/Ultralytics/ONNX Runtime; không khẳng định
  GUI AI hoặc nhận diện tự động đã chạy end-to-end. Không thay môi trường CUDA.

## Lệnh chạy

Từ thư mục này:

```bash
bash run_camera.sh --camera-only
python3 tools/check_camera.py --seconds 10
# Chỉ dùng khi muốn gửi xung zoom thật:
python3 tools/check_camera.py --seconds 5 --zoom-pulse
# Pipeline AI, sau khi có đủ môi trường:
bash run_camera.sh
```
