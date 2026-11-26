# Mainstream hiển thị, substream AI

`camera.json` dùng hai địa chỉ:

- `uri`: subtype=1, dành riêng cho AI và các phép đo điều khiển.
- `display_uri`: subtype=0, dành cho GUI và chế độ camera-only.

GUI nhận hai luồng độc lập qua DualStreamPlayer. AI chỉ đọc mailbox substream;
mainstream không được đưa vào inference. Mailbox hiển thị chỉ giữ ảnh mới nhất,
không ghi các sự kiện hàng đợi inference vào profiler. Khi đóng, cả hai receiver
đều được dừng kể cả khi một receiver báo lỗi khi đóng. Mất mainstream không
xóa mailbox AI; mất substream vẫn kích hoạt cơ chế dừng điều khiển hiện có.

Bounding box và landmarks được đổi từ kích thước ảnh inference sang kích thước
ảnh hiển thị theo từng trục. Tâm digital PTZ được đổi trên bản sao trạng thái,
không sửa tọa độ AI. Overlay quá tuổi MAX_CONTROL_OBSERVATION_AGE bị ẩn.
Cách quy đổi giả định hai stream cùng vùng nhìn; không đồng bộ theo timestamp
cảm biến, nên chuyển động nhanh vẫn có thể có độ lệch thời gian giữa hình và box.

Kiểm tra camera thật 15 giây:

- Substream: 704×480, 438 frame nhận được.
- Mainstream: 3840×2160, 414 frame nhận được.
- Cả hai không có lỗi đọc hoặc reconnect, worker đã dừng sạch.
- Đây là kiểm tra thu hình, không phải benchmark tốc độ inference hay GUI.

Số liệu: `logs/dual_stream_check.json`. Test bao gồm phân luồng callback,
disconnect độc lập, dừng khi một receiver lỗi, đổi bbox/landmark, hết hạn overlay
và tâm crop digital PTZ.

Khởi động lại ứng dụng từ gốc `ptz/` để dùng cấu hình mới:

```bash
conda activate py
bash run_camera.sh
# Hoặc chỉ xem camera:
bash run_camera.sh --camera-only
```
