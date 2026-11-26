> Cập nhật runtime: nhận diện giờ chờ optics ổn định; không còn chạy ArcFace trong lúc zoom/AF. AF có tối đa `FOCUS_MAX_ATTEMPTS` mỗi chu kỳ và `FOCUS_QUALITY_MODE` chọn đa chỉ số hoặc legacy. Auto Backlight đã loại bỏ; chỉ còn cấu hình hình ảnh tĩnh. Tham khảo `docs/refactor.md` trước khi dùng các ghi chú lịch sử bên dưới.

# Chạy thử cửa 408 → cửa thoát hiểm (~15 m)

Chạy bằng môi trường Python hiện có của dự án:

```bash
PTZ_RTSP_LATENCY_MS=100 python main.py
```

Biến môi trường chỉ thay latency cho lần chạy này. Bỏ biến để dùng cấu hình camera.json (hiện là 200 ms). So sánh 200/100/50/0 ms với cùng điều kiện; chọn mức ít trễ và không gây mất hình. Không thay decoder hoặc cấu hình cảm biến chưa xác minh model/firmware.

1. Chuẩn bị ảnh người mục tiêu ReID và danh tính trong face database như luồng hiện tại. Đây vẫn là luồng chọn người bằng ReID, chưa chuyển sang bám người bất kỳ.
2. Đưa camera về góc rộng ban đầu, nhìn rõ cửa thoát hiểm; mở cửa 408. Kiểm tra camera nhìn thấy mặt người đi ra.
3. Bật Auto Focus, rồi Auto Zoom. Hai chức năng vẫn mặc định OFF khi khởi động.
4. Người mục tiêu đi từ cửa thoát hiểm ra theo đường đã định. Không đổi target trong lượt.
5. Đọc `[TRIAL]` và `[AF]` trong `logs/ptz_*.log`. Lặp lại ít nhất 20 lượt, đưa camera về cùng góc/zoom trước mỗi lượt và tắt/bật Auto Zoom để reset lượt.

## Cách đọc kết quả

- START: timestamp của frame đầu tiên ứng dụng nhận được mà detector tìm thấy người, khi Auto Zoom bật. Mốc này bao gồm thời gian xử lý frame đó nhưng chưa bao gồm thời gian camera/mạng/decoder trước callback. Nếu có người khác trong cảnh, họ cũng có thể khởi động lượt; bố trí cảnh một người cho bài thử này.
- TARGET_LOCKED, ZOOMING, FACE_DETECTED, FOCUSING: mốc đầu tiên ứng dụng quan sát được từng giai đoạn, không phải telemetry vị trí lens.
- CONFIRMED: bộ nhận diện xác nhận danh tính trước deadline. Người kiểm thử cần đối chiếu danh tính đúng thực tế; log không tự chứng minh đúng người.
- TIMEOUT: chưa xác nhận trong 10 s, kể cả khi xác nhận đến muộn. Mặc định `ACQUISITION_TIMEOUT_DISABLE_AUTO_ZOOM=False`: chỉ ghi timeout và tiếp tục zoom. Chỉ khi đặt True, AI loop mới tắt Auto Zoom và yêu cầu STOP tại lần kiểm tra kế tiếp. Đây không phải bộ điều khiển thời gian thực; HTTP, inference hoặc hệ điều hành có thể làm STOP trễ. Watchdog zoom hiện có vẫn hoạt động.
- RESET: lượt kết thúc trước xác nhận do reset/mất mục tiêu; tính là lượt chưa thành công, không bỏ khỏi thống kê.
- AF QUALITY_OK: ba frame mới liên tiếp, cùng loại ROI, đạt ngưỡng 350; chỉ xét frame sau ACK ít nhất 200 ms và tuổi frame tối đa 300 ms. Ngưỡng cần hiệu chỉnh bằng ảnh thực tế.
- AF TIMEOUT: hết 1,5 s sau ACK mà chưa chứng minh đạt nét. Không coi timeout là đã nét. Tổng thời gian AF còn bao gồm HTTP request (timeout hiện tại 2 s).

AF kết thúc chờ sớm theo chất lượng và cooldown 0,75 s. Không dùng plateau đơn độc vì ảnh mờ cũng có thể đứng yên. Bỏ qua số đo cũ/trùng; chuyển ROI người → mặt phải đếm lại. HTTP đang chạy vẫn giữ quyền điều khiển motor khi reset.

Nhận diện vẫn có thể chạy trong lúc zoom/AF nếu mặt đủ kích thước, landmarks hợp lệ và Laplacian ít nhất `FACE_RECOGNITION_MIN_SHARPNESS=120` (giá trị thử nghiệm). Ngưỡng nhận diện khác ngưỡng dừng chờ AF; không có bảo đảm 120 phù hợp mọi ánh sáng/camera.

Ghi mỗi lượt: ánh sáng, latency, thời gian CONFIRMED/TIMEOUT/RESET, danh tính dự đoán và đúng/sai thực tế. Báo số lượt đúng ≤10 s trên tổng lượt, trung vị, P95 và lớn nhất. Muốn đo từ lúc người thực sự xuất hiện, dùng video/đồng hồ đối chiếu bên ngoài ứng dụng.


## Setup người và hiện trường

1. Bố trí một người thử và một người vận hành máy. Đo camera đến điểm bước ra khỏi cửa thoát hiểm khoảng 15 m. Mở cửa 408, hướng camera để điểm xuất hiện gần tâm ảnh. Hiện chưa tự pan/tilt motor; digital crop không sửa được hướng zoom quang.
2. Chuẩn bị ảnh toàn thân rõ, chỉ có người thử, mặc cùng quần áo lúc test. Bấm **Select Target**, chọn ảnh và đợi target khởi tạo thành công. Ảnh này dùng cho ReID, không đăng ký tên khuôn mặt.
3. Người thử phải có tên trong face database. Nếu chưa có, đặt 3–5 ảnh mặt rõ (chính diện, hơi quay trái/phải; mỗi ảnh một người) vào `data/face_database/TenNguoi/`. Dùng ảnh kiểm thử khác ảnh đăng ký.
4. Nếu thêm ảnh: đóng app, sao lưu `data/face_database/embeddings.npz`, chạy `python3 tools/build_face_database.py`, kiểm tra valid embeddings rồi mở lại app để nạp database mới.
5. Tắt Auto Zoom, đưa zoom về góc rộng và chờ ổn định. Cho người ra ngoài cảnh. Bật Auto Focus rồi Auto Zoom; để Digital PTZ OFF cho nhóm baseline.

## Trình tự test

- Nhóm A: người bước ra điểm đánh dấu, nhìn về camera, đứng khoảng 10 s. Kiểm tra TARGET_LOCKED → zoom → thấy mặt → AF/chất lượng ảnh → đúng danh tính.
- Nhóm B: đi chậm từ cùng điểm xuất phát về phía camera.
- Nhóm C: đi bình thường theo cùng đường. Chỉ thêm người thứ hai, quay đầu hoặc ngược sáng sau khi nhóm một người ổn; ghi kết quả thành nhóm riêng.
- Reset góc rộng và tắt/bật Auto Zoom trước mỗi lượt. Không đổi cấu hình giữa nhóm. Mỗi nhóm nên có 20 lượt; TIMEOUT/RESET vẫn tính vào tổng.
- Ghi: số lượt, khoảng cách, ánh sáng, kiểu đi, thời gian target lock/face/confirmed, AF outcome, tên dự đoán, đúng/sai thực tế. MATCH không tự chứng minh đúng người.

Zoom hiện dừng theo FACE_FINAL_LOCK khi mặt cao ít nhất 18% khung trong 3 frame, dù đích face thông thường là 25%. Nhận diện không bắt buộc chờ AF QUALITY_OK.

## Khi gặp lỗi

- Có người nhưng không lock: kiểm tra ảnh target, trang phục và ReID score trước khi chỉnh zoom.
- Mất người khi zoom: kiểm tra tâm quang và đường đi.
- face_too_small: kiểm tra kích thước mặt và mức zoom.
- face_blurry hoặc AF TIMEOUT: đối chiếu ảnh thật, ROI, tuổi frame và ánh sáng; không hạ ngưỡng chỉ để đạt test.
- UNKNOWN/sai tên: kiểm tra ảnh đăng ký và similarity.
- CUDA out of memory: đóng app test, giải phóng RAM từ ứng dụng không cần thiết rồi khởi động lại. Không tiếp tục đánh giá detect/zoom trên phiên đã lỗi CUDA.
- Có thể chạy `PTZ_DEVICE=cpu PTZ_RTSP_LATENCY_MS=100 python3 main.py` để chẩn đoán trên CPU (cả YOLO và ReID). Không dùng kết quả tốc độ này thay bài test GPU; frame chậm có thể bị chặn bởi stale-observation guard. Bỏ PTZ_DEVICE để trở về GPU mặc định.
