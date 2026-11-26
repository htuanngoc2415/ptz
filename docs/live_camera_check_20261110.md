# Kiểm tra camera thật 2026-11-10

Camera HANET 192.168.0.108 phản hồi TCP 80/554; địa chỉ cấu hình PTZ
192.168.110.108 hết hạn chờ sau 4 giây trên cả hai cổng.

Đăng nhập bằng HanetPTZClient của camera.py và heartbeat thành công, sau đó logout.
RTSP substream đọc được ảnh 704x480. Hai lần mở/đọc/đóng độc lập, mỗi lần
10 giây, đọc 312/313 frame; tốc độ đọc quan sát 31,05/31,10 frame/s.
Đây là tốc độ đọc trong thử ngắn, có thể gồm bộ đệm lúc mở, không phải phép đo
FPS cảm biến hay độ trễ đầu-cuối. Không lưu ảnh camera.

FrameManager thực tế nhận ảnh và đánh thức consumer; không timeout chờ 100 ms
trong hai khoảng đo. Consumer nhận 311/312 frame; mailbox chỉ giữ ảnh mới nhất.
Cả hai producer đều dừng. CameraCommandWorker thực thi tác vụ kiểm tra cục bộ
và đóng thành công; tác vụ này không gửi lệnh tới motor.

Chưa kiểm chứng AI hoặc chuyển động pan/tilt/zoom/focus:
- Python hiện tại thiếu torch, ultralytics, onnxruntime, gi và mpv.
- Môi trường Autofocus_Camera mà launcher ưu tiên không tồn tại.
- Python hệ thống có gi nhưng thiếu các thư viện AI và OpenCV.
- Dự án ptz dùng controller Dahua ZoomRPC, trong khi camera.py có client HANET
  riêng. Đăng nhập HANET thành công không chứng minh controller Dahua tương thích.

Không sửa camera.json hoặc cấu hình thiết bị trong đợt kiểm tra này.
Kết quả máy đọc: ../logs/runtime_cleanup/live_camera_check_20261110.json.
