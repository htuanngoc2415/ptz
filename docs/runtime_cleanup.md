# Cải tiến runtime và dọn dự án (2026-11-09)

Phạm vi thực thi là thư mục `ptz/` bên trong workspace; `../camera.py` là công cụ riêng và không thay đổi.

## Kiểm tra Frigate

Không phát hiện dấu hiệu mã nguồn mã hóa qua tìm dấu hiệu PyArmor/loader giải mã,
kiểm tra phần mở rộng binary và parse toàn bộ file Python bằng AST. Các chỗ
base64 tìm thấy dùng cho ảnh/âm thanh, không phải chạy mã nguồn giải mã.
Đây là kiểm tra tĩnh, không phải bảo đảm về mọi dependency ngoài repository.

## Áp dụng

- Tham khảo `../frigate-dev/frigate/video/detect.py`: consumer chờ dữ liệu với
  timeout. FrameManager dùng Condition thông báo khi có frame, thay việc AI
  thức dậy mỗi 20 ms khi không có ảnh bằng chờ tối đa 100 ms. Vẫn chỉ giữ frame
  mới nhất; không tích lũy ảnh cũ. Giữ watchdog và kiểm tra trạng thái tắt sau chờ.
- Tham khảo cách dùng stop event trong `../frigate-dev/frigate/video/ffmpeg.py`
  và `../frigate-dev/frigate/ptz/autotrack.py`: camera worker dùng Event và get
  có timeout. close không còn chèn sentinel vào queue đang đầy dưới lock.
  Giao dịch đã nhận được xử lý hết; timeout của close giới hạn thời gian join,
  không thể hủy một giao dịch mạng đang chạy.
- Không sao chép thuật toán motion-gating vào PTZ: chuyển động camera và người
  đứng yên cần kiểm chứng riêng để tránh bỏ nhận diện. Chưa đo FPS/CPU/camera thật.

## Dọn dẹp

Đã xóa 19 cấu hình không được mã ứng dụng/test/tool tham chiếu (kể cả tham
chiếu bằng tên chuỗi), không xóa cấu hình còn làm đầu vào cho cấu hình khác:

```
FACE_CONF_THRESHOLD
FACE_IOU_THRESHOLD
FACE_IMGSZ
PERSON_TARGET_MODE
FACE_TARGET_MODE
FACE_USE_PERSON_ROI
FACE_LOST_FRAMES
FACE_FINAL_STOP_RATIO
UI_MAIN_FONT_SCALE
UI_MAIN_FONT_THICKNESS
UI_ID_FONT_SCALE
UI_ID_FONT_THICKNESS
AUTO_ZOOM_MEDIUM_ERROR
AUTO_ZOOM_MEDIUM_PULSE
ZOOM_STEP_LARGE
ZOOM_STEP_MEDIUM
ZOOM_STEP_SMALL
WAIT_AFTER_ZOOM
FACE_RECOGNIZER_MODEL_ID
```

Đã xóa 98.553 file, tổng 1.574.903.745 byte: cây tham khảo `phanmem_v1.0`,
ZIP lồng cùng tên, cache Python và `output.jpg` sinh ra khi chạy. Chi tiết tại
[cleanup_manifest.json](cleanup_manifest.json). Xóa thư mục tham khảo khỏi
inventory của công cụ benchmark; cập nhật README.

Bản gốc của cây tham khảo và ZIP lồng đã xác nhận có trong `../ptz.zip`.
Giữ các kho tham khảo ngoài dự án, bản sao lưu ZIP ngoài dự án, model, ảnh target,
cơ sở dữ liệu khuôn mặt, hiệu chuẩn, tài liệu và log được tài liệu trích dẫn.
Không tự suy đoán chúng là dữ liệu thừa.

## Kiểm thử

- 5 test mới đạt: đánh thức consumer, hết hạn chờ, bỏ frame cũ, đóng khi queue đầy,
  và đóng worker đang nhàn rỗi.
- Toàn bộ Python còn lại parse AST thành công.
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/run_unit_tests.py`: trước sửa 230 test,
  sau sửa 235 test; cả hai có cùng danh sách 4 failures và 20 errors.
- Lỗi sẵn có gồm thiếu onnxruntime/GStreamer bindings và 4 test absolute zoom.
  Không khẳng định toàn bộ suite đạt hoặc hệ thống đã được kiểm chứng trên camera.
- Log trước/sau: `logs/runtime_cleanup/tests_before.log`, `tests_after.log`.
