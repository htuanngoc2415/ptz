# Runtime refactor và migration

Entry point vẫn là `python3 main.py`; tên module cũ được giữ. Các hàm điều phối trong `CCTVApp` chuyển sang `runtime.inference_worker.InferenceWorker`, GUI giữ wrapper cho tương thích. Test nhánh face patch các hàm detect/alignment tại module `ai.face`, và chờ mailbox trong test harness. Context adapter vẫn chia sẻ các service/state cũ với GUI; đây là migration từng phần, chưa phải event-bus hoàn toàn độc lập.

`app.application` tạo các controller và nạp model trong AI thread. Model/session không tạo lại mỗi frame. Batch ReID mặc định giảm còn 4; khi checkpoint thực sự thay đổi, bỏ reference model cũ trước khi tạo mới. Không gọi empty_cache theo frame, không tự chuyển CPU che lỗi. CUDA failure có event và số allocated/reserved khi lấy được. Chưa xác định nguyên nhân gốc của lỗi CUDA trên thiết bị chỉ bằng static review.

Auto Backlight bị loại bỏ theo xác nhận của chủ dự án. Các mục tiêu tự tuning image/đợi IMAGE_STABILIZING trước ReID không được bật trong runtime hiện tại. Cấu hình BLC/HLC/WDR tĩnh được read/merge/write/readback một lần; capability chỉ báo các trường đã được camera xác nhận, không tự bịa tên setter gamma/exposure/gain.

Nhánh face hiện chạy bất đồng bộ sau khi lock target và có thể nhận diện trong lúc zoom/focus nếu ảnh đạt các ngưỡng chất lượng hiện có. Xem [hybrid pipeline](hybrid_pipeline.md) để biết worker, correlation và latency. AF chất lượng đa chỉ số là heuristic, cần đo false-positive trên ảnh nhiễu/texture và false-negative trên mặt nhỏ. Timeout không đồng nghĩa lens chưa nét; có thể thiếu đủ frame mới sau ACK. Ngân sách AF reset khi target reset/bật AF, không reset theo mọi xung zoom.

Manual control có mailbox hữu hạn và chờ HTTP đang chạy kết thúc trước khi đổi quyền motor. Emergency STOP không phải hard-real-time: độ trễ vẫn chịu timeout RPC2 và firmware. Sau STOP lỗi giữ khóa để không phát START mới. Cần thử mất mạng giữa START/STOP trên camera thật.

## Xác minh camera thật

Chạy normal_3m, normal_6m, long_range, backlight, low_light, walking, back_view, short_occlusion, multiple_people, lost_and_reacquire. Mỗi nhóm có ảnh đăng ký và người không phải mục tiêu; ghi đúng/sai thực tế, thời gian acquire/reacquire, sống sót track, mặt/nét/danh tính và tuổi frame. So sánh raw-only với gallery/normalized trên cùng dữ liệu; không coi score cao hơn là tự động tốt hơn.

Bổ sung thử rút/cắm mạng, camera không sẵn sàng lúc khởi động, STOP khi inference chậm, chuyển manual giữa AF/zoom, tắt ứng dụng khi RPC2 đang timeout. Unit test mock không chứng minh firmware hoặc quang học hoạt động đúng.

## Giao cắt và focus vật cản

Khi nhiều bbox giao với vùng target, selector chuyển `OCCLUDED_HOLD`, không cập nhật bbox và không nối mặt qua vùng giao cắt. Sau đó cần 3 frame mới đạt ngưỡng ReID và margin trước khi tiếp tục cùng track. Mất bằng chứng vẫn tuân theo grace/lost và reacquire. Đây là guard thận trọng: nếu detector gộp hai người thành một bbox mà không quan sát được giao cắt, vẫn có nguy cơ đổi người. Cần kiểm thử clip nhiều người có nhãn trước khi kết luận khắc phục hoàn toàn.

ReID diagnostics bổ sung kích thước ROI, tương phản và Laplacian để phân tích acquisition ở xa. Không hạ threshold hoặc zoom vào người chưa xác nhận chỉ dựa vào khoảng cách ước lượng.

BUILTIN_AF chỉ gửi lệnh autofocus, không truyền target ROI hay khoảng cách lens. ROI trong phần mềm chỉ đánh giá kết quả, không điều khiển điểm lấy nét của firmware. Khi vật cản trước mặt target, cần API focus-area/range được xác minh hoặc thuật toán manual-focus có hiệu chỉnh; không thể bảo đảm target nét bằng thay ngưỡng Laplacian.
