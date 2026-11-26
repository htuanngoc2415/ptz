# Pan/tilt tự động theo người

Đã nối với pipeline ByteTrack + OSNet trên camera HANET. Khởi động lại ứng dụng trong Conda `py`, chọn ảnh mục tiêu và bật **Auto Pan/Tilt (follow person)**. Mặc định tùy chọn này bật trên backend hỗ trợ pan/tilt; camera chỉ quay sau khi xác nhận được mục tiêu. Auto Zoom là tùy chọn riêng.

## Cách hoạt động

- Tâm bounding box người là điểm cần đưa về tâm ảnh. Không chọn người gần nhất hoặc khuôn mặt của người bên cạnh.
- Dùng tọa độ substream gốc; không lấy tọa độ từ ảnh đã digital crop. Mainstream vẫn dùng để hiển thị.
- Bắt đầu điều chỉnh khi lệch hơn 8% chiều rộng/cao ảnh; dừng trong vùng 4% để tránh bật/tắt liên tục.
- Ưu tiên trục lệch nhiều hơn. Quay từng nhịp tối đa 0,12 giây sau khi lệnh được xác nhận, sau đó STOP và chờ ổn định 0,15 giây. Chỉ dùng quan sát nhận sau khoảng chờ này cho nhịp tiếp theo.
- Tốc độ từ 1 đến 3 trên thang HANET 1–8. Hai trục chạy luân phiên; không giả định camera hỗ trợ lệnh chéo.
- Ưu tiên đưa người về giữa trước khi zoom tiếp. Pan/tilt, zoom, focus và thao tác tay dùng chung cơ chế sở hữu motor, tránh phát lệnh đồng thời.
- Mất người, ReID nghi ngờ, đổi phiên khóa, dữ liệu quá cũ, mất stream hoặc tắt ứng dụng sẽ hủy lệnh. Kiểm tra lại phiên, hướng và tuổi dữ liệu ngay trước RPC để không chạy lệnh cũ trong hàng đợi.
- Điều khiển tay tắt Auto Pan/Tilt; bật lại checkbox để tiếp tục tự bám. Không tự tìm kiếm bằng cách quay khi chưa xác nhận được người.
- Nếu STOP không được xác nhận, giữ quyền điều khiển và thử STOP lại; tự bám bị tắt. Chỉ bật lại sau khi lỗi kết nối/motor đã hết.

## Hiệu chỉnh

Các giá trị nằm cuối `config.py`: `AUTO_PAN_TILT_DEADZONE`, `AUTO_PAN_TILT_RELEASE`, `AUTO_PAN_TILT_MAX_SPEED`, `AUTO_PAN_TILT_PULSE`, `AUTO_PAN_TILT_SETTLE`. Nếu hướng quay vật lý bị ngược do camera/mount, đổi `AUTO_PAN_SIGN` hoặc `AUTO_TILT_SIGN` thành `-1`.

Chưa hiệu chuẩn motor thật cho tính năng này. Nhịp 0,12 giây không bao gồm thời gian truyền và xác nhận RPC; độ dịch chuyển thực tế phụ thuộc tốc độ motor, zoom và độ trễ mạng. Không bảo đảm tâm người luôn chính xác từng pixel. Khi mất xác nhận, ưu tiên dừng thay vì quay theo dự đoán.

## Kiểm thử

13 kiểm thử pan/tilt đạt: hướng quay, hysteresis, stale/session gate, mất mục tiêu, manual override, quyền sở hữu zoom, tắt/đóng, lỗi STOP, stale RPC và tích hợp worker. 11 kiểm thử HANET, 42 ReID/worker và 11 hybrid tracking cũng đạt. Dùng camera/RPC giả lập, không gửi lệnh quay camera thật trong lượt triển khai này.

Code chính: `control/auto_pan_tilt.py`, `runtime/inference_worker.py`, `camera/hanet_controller.py`.
