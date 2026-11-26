# Kiểm tra phiên camera 09:03:05 ngày 19/11/2026

Bản ghi ảnh dùng được cho replay. Shadow v2 không đủ mẫu để đánh giá chính sách: 168,81 giây chỉ xuất 4 comparison. Lỗi nằm ở admission của shadow; không phải người dùng record sai.

## Dữ liệu đã kiểm tra

- App: `logs/ptz_20261119_090305.log` và `.jsonl`.
- Capture: `logs/identity_capture/capture_91efcb317617412bb9d9a5a02faede59`.
- Shadow: `logs/identity_shadow/shadow_7e81325d4adf4f7b8b5fbf7734a18f10.jsonl`.
- Profiler: `logs/latency/latency_summary_20261119_090309_292867.csv`, trace cùng prefix.

`analyze.py` xác minh metadata/reference checksum, timestamp/frame ID tăng, từng checksum PNG, decode và shape. Đủ **1502 frame / 170,07 giây**, finalized, 0 dropped và 0 errors do recorder báo. Đây là ảnh frame AI được lấy mẫu, không phải video main liên tục. Nó không chứng minh RTSP không rơi frame trước recorder. Nhãn tự động là proposal baseline, ground_truth=false.

## Kết quả shadow và nguyên nhân lỗi

- Một session, cùng target/reference qua **7 reset**, các reset đều dùng ngưỡng **0,70**.
- Bốn mẫu đều HOLD vì similarity thấp; baseline đang chọn. Các frame 4995/5007/5029/5038 có ID/timestamp/baseline proposal khớp capture; không mismatch metadata.
- Xem `baseline_only.jpg`: cả bốn box baseline bao quanh người áo đen ở tiền cảnh. Đây là kiểm tra hình ảnh một nhóm nhỏ; không phải nhãn GT cho toàn clip.
- Không có lần shadow chấp nhận target trong phiên, vì vậy chưa kiểm chứng chất lượng memory khi đã có lịch sử chấp nhận qua reset. `history_retained=true` chỉ thể hiện cách quản lý session.
- 653 job bị bỏ do ACCELERATOR_BUSY, 835 submit bị rate limit. Scheduler ghi 810 resource-hold events nhưng chỉ 4 lần scorer hoàn tất. Resource-hold có cả nested acquisition: **không diễn giải 810 là 810 inference độc lập, cũng không cộng duration thành GPU time**.

Scorer v2 xin tài nguyên riêng cho ảnh raw và ảnh chuẩn hóa. Khi raw đã xong nhưng foreground lấy tài nguyên trước lượt chuẩn hóa, BackgroundBusy làm mất toàn bộ kết quả job. Việc bỏ dở lặp lại khiến shadow tiêu tốn ReID nhưng gần như không có comparison. Code path đã được tái hiện và sửa bằng bài kiểm tra tranh chấp giữa hai lượt; xem [v3](../identity_shadow_v3/REPORT.md).

## Độ trễ hiển thị

Summary toàn phiên có 3148 mẫu receive→GUI-submit: median **57,96 ms**, P95 **73,90 ms**, max **126,26 ms**. Không thấy đợt giữ frame main nhiều giây như phiên đầu trong số đo này. Đây là độ trễ từ lúc ứng dụng nhận frame đến submit GUI, không đo glass-to-glass hay độ trễ trước decode; không chứng minh main/sub đồng bộ tuyệt đối.

Trace chi tiết đã rotation, chỉ giữ 09:06:18–09:07:17. Thống kê ON/OFF trong `audit.json` dùng SHADOW_START/STOP monotonic (không dùng timestamp của 4 mẫu muộn). Không kết luận shadow không ảnh hưởng FPS: các đoạn có cảnh/trạng thái khác nhau, trace không bao trọn phiên, scorer vẫn làm công việc bỏ dở.

## Xử lý tiếp

Đã giữ nguyên raw capture/log và dùng ảnh này để kiểm chứng scorer v3 với OSNet thật, sau đó chuẩn bị evidence/replay Phase 1. Xem [báo cáo v3](../identity_shadow_v3/REPORT.md). Không cần gán lại nhãn thủ công cho các bước kiểm tra chính sách này; chưa thể gọi kết quả là accuracy/IDF1/HOTA.
