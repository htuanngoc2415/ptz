# Đo Laplacian song song khi chỉnh camera

Giữ ứng dụng điều khiển camera ở terminal thứ nhất:

```bash
cd /home/edabk/tngoc/ptz
PTZ_RTSP_LATENCY_MS=100 python main.py
```

Mở terminal thứ hai, cùng môi trường Python:

```bash
cd /home/edabk/tngoc/ptz
python tools/laplacian_monitor.py --threshold 120 --timeout 1 --latency-ms 100
```

- Kéo chuột trên ảnh để chọn vùng người hoặc mặt. Tính trên pixel gốc, không tính trên ảnh preview đã thu nhỏ.
- Điểm Laplacian được cập nhật liên tục trong thread riêng, kể cả khi main.py đang zoom/focus. Chỉ giữ một frame chờ mới nhất.
- `F`: bắt đầu đánh giá tối đa 1 giây; dùng khi vừa gửi lệnh AF hoặc dừng zoom. Mốc này do người dùng nhấn phím, không tự đồng bộ với ACK của camera.
- `R`: xóa ROI. `Q`/Esc: thoát.
- `SHARP`: tối thiểu 3 frame mới liên tiếp có điểm >=120. `--confirm-frames 1` cho phép dùng một frame nếu muốn thử đúng điều kiện ngưỡng đơn.
- `QUALITY_OK`: lượt đã đạt tiêu chí; đây là kết quả lịch sử của lượt, xem trạng thái chất lượng hiện tại để biết ảnh sau đó có còn nét không.
- `TIMEOUT`: hết thời gian mà chưa chứng minh đạt nét, kể cả video đứng hình. Không có nghĩa motor đã dừng.
- `plateau=True`: 5 số đo gần nhất dao động không quá 5% giá trị lớn nhất; chỉ là chẩn đoán. Plateau dưới ngưỡng không được coi là nét.
- ROI cố định, không tự bám người: chọn lại khi người ra khỏi vùng đo hoặc bố cục zoom thay đổi nhiều. Không lấy toàn khung có nền sắc nét để kết luận khuôn mặt đã nét.

Công thức: chuyển crop BGR sang grayscale rồi `cv2.Laplacian(gray, cv2.CV_64F).var()`. Ngưỡng 120 là giá trị thử từ kịch bản, cần hiệu chỉnh theo ảnh thực tế. Dấu thời gian là lúc ứng dụng nhận frame, không phải timestamp phơi sáng cảm biến.

Công cụ mở thêm một kết nối RTSP từ camera.json; máy/camera cần đủ tài nguyên cho hai ứng dụng. Công cụ không gửi lệnh motor, không đổi cấu hình AF/nhận diện của main.py. Đóng công cụ không ảnh hưởng ứng dụng điều khiển camera.

## Không mở được cửa sổ / chạy qua SSH

Nếu Linux không có DISPLAY/WAYLAND_DISPLAY, công cụ tự chuyển sang terminal thay vì lỗi GTK. Có thể chọn rõ chế độ này:

```bash
python tools/laplacian_monitor.py --headless --threshold 120 --timeout 1
```

Terminal mode mặc định đo vùng giữa chiếm 50% chiều rộng/cao ảnh, không phải tự phát hiện mặt. Đặt vùng đo bằng `--roi x1,y1,x2,y2` theo pixel ảnh gốc. Ví dụ với ảnh 2448x2048: `--roi 612,512,1836,1536`.

Lượt 1 giây bắt đầu lúc nhận frame đầu tiên; sau TIMEOUT điểm độ nét hiện tại vẫn cập nhật. Đây không phải đồng bộ với lệnh focus; chạy lại công cụ khi muốn bắt đầu lượt mới. `--duration 10` tự thoát sau 10 giây. Ctrl+C để dừng. Không nhận frame đầu tiên trong 10 giây sẽ báo lỗi rõ ràng (`--stream-timeout` để đổi giới hạn).

Muốn cửa sổ kéo chọn ROI, chạy từ terminal trong phiên desktop có màn hình. Nếu máy có màn hình nhưng OpenCV vẫn không hỗ trợ GUI, dùng `--headless` để đo bằng terminal.
