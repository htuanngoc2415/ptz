# Phân tích vỡ hình và zoom vào/ra — 22/10/2026

## Kết luận

Đã xác nhận một chuỗi tự động **zoom vào → mất quan sát/mục tiêu → recovery zoom ra** trong phiên 12:46. Chưa chứng minh nguyên nhân duy nhất khiến mất mục tiêu. Có lỗi SCRFD cấp phát bộ nhớ, quan sát điều khiển cũ hàng trăm ms và thay đổi khung nhìn khi zoom.

Ảnh người dùng gửi có mảng vỡ dạng khối ở cạnh cửa/tường. Chưa đủ bằng chứng xác định chúng phát sinh từ camera encoder, RTP/decode hay AnyDesk. Không thể quy toàn bộ hiện tượng này cho mất nét, GStreamer hoặc lỗi AI.

Phạm vi lần kiểm tra này: đọc code, log, trạng thái tiến trình và chụp desktop trực tiếp. Không đổi backend, model, threshold, allocator hay cấu hình camera; không gửi lệnh motor và không khởi động lại ứng dụng.

## Backend đang chạy

- `camera.json` chọn `gstreamer`; tiến trình ứng dụng PID 327381 không có `PTZ_VIDEO_BACKEND` override.
- Đây là `RTSPPlayer` dùng `playbin` trong [camera/rtsp_player.py](../camera/rtsp_player.py), **chưa phải** backend opt-in `gstreamer_cpu` có đường `avdec_h264` tường minh.
- Tiến trình có map `libgstlibav.so`; kiểm tra device handle không thấy NVDEC. Điều này phù hợp với decode phần mềm, nhưng chưa thay thế được kiểm tra graph/factory thực tế. Dòng lỗi NvMap của chương trình AI không chứng minh video đang dùng NVDEC.
- Nguồn main stream đã đo trong audit trước là H.264 3840×2160, 25 FPS; nhánh hiển thị scale xuống tối đa 1280×720. Không thực hiện lại phép đo caps trong lần phân tích này.

## 1. Vì sao zoom vào rồi lại zoom ra?

Nguồn: [log phiên được giữ lại](../logs/zoom_image_review_20261022/ptz_20261022_124641.log).

| Giờ trong log ứng dụng | Sự kiện |
|---|---|
| 12:47:06.498 | `SEARCH_PERSON → ZOOM_PERSON` |
| 12:47:06.834 | SCRFD lỗi `BFCArena::AllocateRawInternal`, yêu cầu buffer 9.830.400 byte |
| 12:47:12.573 | Face ratio 0,053; vùng mục tiêu 0,220–0,280; yêu cầu zoom vào; observation age 577 ms |
| 12:47:13.628 | Dừng vì `STALE_TRACK` |
| 12:47:14.978 | Lại zoom vào; face ratio 0,053; observation age 664 ms; lần này đích khoảng 6,63× |
| 12:47:16.981 | Dừng vì `TARGET_LOST` |
| 12:47:18.587 | Recovery bắt đầu từ zoom estimate 6,70×, zoom ra từng nhịp 300 ms |
| 12:47:49.565 | Chạm giới hạn zoom rộng, estimate khoảng 1,20× |

Đây là lệnh zoom ra có chủ đích từ [AutoZoom recovery](../control/auto_zoom.py), không phải bằng chứng motor tự đảo chiều. Timeout 5/8 giây ghi trong kế hoạch zoom vào là giới hạn; log thực tế có STOP sớm, không được hiểu thành motor chạy liên tục đủ 5/8 giây.

GUI dùng ảnh mới nhất, nhưng điều khiển còn phụ thuộc quan sát AI và trạng thái target. Hai quan sát cũ hơn nửa giây là dấu hiệu cần điều tra khi khung nhìn đang đổi nhanh. Chúng đo tuổi quan sát trong ứng dụng, **không phải** camera-to-display latency. Chưa chứng minh chúng là nguyên nhân duy nhất gây mất target; người di chuyển, bị che hoặc ra khỏi khung cũng có thể góp phần.

Trong [FaceDetector](../ai/face/face_detector.py), exception ở `session.run()` được bắt rồi trả `[]`. Ở giao diện trả kết quả này, lỗi inference không được phân biệt với không có mặt. Đây là điểm cần xử lý để lỗi tài nguyên không bị hiểu như một quan sát thị giác hợp lệ. Tuy nhiên lỗi SCRFD không trực tiếp chứng minh YOLO/OSNet đã mất người; sau lỗi, log vẫn có nhận diện thành công.

**Riêng ảnh lúc 12:48:** Auto Zoom đã bỏ chọn, status là “Đang đưa zoom về 1x…”. Code `reset_zoom()` gửi một lệnh manual về zoom nhỏ nhất dù Auto Zoom tắt. Trace lưu lại có zoom-out lúc 12:48:39 và STOP khoảng 12:48:43, phù hợp thao tác reset. Không gán chuỗi recovery 12:47 cho mọi chuyển động ở ảnh 12:48. Các STOP có ACK, nhưng ACK không đo thời điểm cơ khí dừng hoặc thời điểm người dùng nhìn thấy ảnh mới.

## 2. Vỡ hình và nhòe khi zoom

Các mảng khối vuông không được giải thích đầy đủ bằng mất nét quang học. Những giả thuyết cần phân biệt:

1. **Nén ở camera hoặc AnyDesk:** khi zoom, nhiều vùng ảnh thay đổi cùng lúc; có thể bộc lộ thiếu ngân sách bitrate/chất lượng nén. Chưa đo bitrate/QP trong đợt lỗi, chưa chứng minh bão hòa đường truyền.
2. **RTP bị bỏ trước decoder:** cấu hình dùng latency 200 ms và `drop-on-latency=true`. Jitterbuffer có thể coi gói đến quá muộn là mất và bỏ gói khi đầy. TCP không loại trừ việc bỏ dữ liệu tại tầng này. Bitstream H.264 thiếu dữ liệu có thể dẫn đến ảnh che lỗi/vỡ khối. Hiện chưa có bộ đếm packet late/lost và decoder warning tương ứng để xác nhận. Xem [GStreamer rtpjitterbuffer](https://gstreamer.freedesktop.org/documentation/rtpmanager/rtpjitterbuffer.html).
3. **Focus:** Auto Focus trên GUI đang tắt. Nếu trước đó ứng dụng đã bật focus tay, `focus_manual(True)` đặt `Mode=4`, `AutoFocusTrace=0`; thao tác zoom không tự khôi phục autofocus camera. Có thể gây nhòe trong/sau zoom. Checkbox này một mình không chứng minh camera đang ở chế độ focus nào; chưa đọc lại cấu hình focus thực tế.

Queue leaky trong video sink nằm **sau decode**. Bỏ một frame raw tại đây thường gây bỏ nhịp hiển thị, không làm mất reference H.264 của decoder. Cần phân biệt với bỏ RTP ở phía trước decoder. [Tài liệu queue](https://gstreamer.freedesktop.org/documentation/coreelements/queue.html).

[Ảnh desktop chụp trực tiếp trên Jetson lúc 12:52](../logs/zoom_image_review_20261022/host_desktop.png) có cảnh đứng yên khá rõ. Đây không phải ảnh đồng thời với zoom trong screenshot người dùng; không đủ để loại trừ hoặc quy lỗi cho AnyDesk. Không lấy chênh lệch giờ OSD camera và desktop làm phép đo delay.

## 3. Áp lực bộ nhớ

[Snapshot sau sự cố](../logs/zoom_image_review_20261022/memory_snapshot.json): MemAvailable khoảng 688 MiB; swap còn khoảng 134 MiB trên tổng 3,72 GiB, tức đã dùng khoảng 96,5%. Đây là số đo sau sự cố, không phải trace tại đúng 12:47:06 và không chứng minh đang swap liên tục.

Log xác nhận SCRFD không cấp phát được khoảng 9,38 MiB. Code ONNX hiện yêu cầu arena limit 1024 MiB cho CUDA provider. Cần phân biệt thiếu RAM dùng chung, giới hạn arena và phân mảnh; chưa đủ dữ liệu để chọn một nguyên nhân hoặc tự tăng giới hạn. Lỗi này ảnh hưởng nhánh AI, chưa chứng minh gây các khối vỡ của video.

## Thứ tự xử lý đề xuất

1. Ghi đồng thời frame gốc của ứng dụng, ảnh desktop và đoạn người dùng nhìn qua AnyDesk tại đúng lúc zoom; gắn frame ID/thời gian để xác định tầng đầu tiên xuất hiện vỡ hình. Thu RTP late/lost/drop và decoder warnings cùng thời điểm. Giữ nguyên stream/resolution/model.
2. Đo bộ nhớ và lỗi inference theo thời gian; tách trạng thái “inference thất bại” khỏi “không có mặt”. Không tự đổi CUDA allocator hoặc giảm workload để che lỗi.
3. Với điều khiển, đối chiếu frame dùng lập kế hoạch với zoom telemetry và frame sau khi motor dừng; kiểm tra việc tái sử dụng quan sát cũ và lý do mất target trước khi chỉnh recovery/threshold.
4. Xác minh chế độ focus thực của camera và quan hệ với checkbox Auto Focus. Không tự bật/tắt focus trong lần điều tra này.

Chưa có phép đo glass-to-glass hoặc tái hiện chủ động bằng motor. Chuyển sang `gstreamer_cpu` chỉ để đối chứng có kiểm soát; không coi việc đổi backend là phương án đã chứng minh chữa cả vỡ hình lẫn dao động zoom.
