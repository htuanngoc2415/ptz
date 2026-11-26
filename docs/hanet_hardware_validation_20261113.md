# HANET P0 — hướng dẫn hardware validation có giám sát

Ngày soạn: 13/11/2026 (UTC+7). **Chưa thực hiện bước nào trên camera thật.** Chỉ tiến hành sau khi người phụ trách thiết bị cho phép và có phương án dừng độc lập.

## Chuẩn bị

1. Bố trí người giám sát tại camera, vùng chuyển động thông thoáng, không có người/đồ vật có thể va chạm. Chuẩn bị cơ chế dừng độc lập đã được kiểm chứng theo hướng dẫn nhà sản xuất; không phụ thuộc ứng dụng/WebSocket đang thử.
2. Kiểm tra trực tiếp camera đang đứng yên trước lần triển khai đầu tiên. Checkpoint motor chỉ bảo vệ các lệnh phát **sau** hotfix; không tái tạo được trạng thái vật lý từ phiên log cũ hoặc từ thiết bị điều khiển khác.
3. Giữ nguyên cấu hình tracking, pan/tilt/zoom policy. Trong thao tác thủ công thử nghiệm, dùng tốc độ nhỏ nhất được thiết bị hỗ trợ (1 trong mapping hiện tại) và một xung ngắn đã được người phụ trách xác nhận an toàn. Không tăng xung/tốc độ để “thấy rõ hơn”.
4. Dùng một phiên ứng dụng điều khiển duy nhất cho mỗi camera. Không mở đồng thời công cụ chẩn đoán, giao diện web hoặc một ứng dụng PTZ khác.
5. Đồng bộ đồng hồ video quan sát độc lập và command log. Lưu `logs/camera/first_failures_*.json`, trace JSONL, camera log, checkpoint `logs/camera/motor_state/*.json` và phiên bản code. Không chia sẻ password/token hay raw authentication response.
6. Người giám sát và người thao tác thống nhất tín hiệu dừng ngay. Không khởi động automatic tracking trong bước xác minh giao thức đầu tiên.

## Ma trận thử có kiểm soát

| Bước | Thao tác được phép sau xác nhận | Quan sát và tiêu chí |
|---|---|---|
| 1 | Kết nối/heartbeat, chưa START | Một WebSocket mỗi client; không có lệnh chuyển động do login/reconnect |
| 2 | Một START thủ công ngắn rồi STOP | UUID request/ACK trùng, `result: true` theo schema được chấp nhận; video xác minh hướng, thời điểm bắt đầu/dừng thực tế |
| 3 | Đổi hướng sau STOP đã được ACK và quan sát đứng yên | STOP hướng cũ trước START mới; không có lệnh chồng lấn |
| 4 | Kiểm tra emergency STOP khi pan hoặc zoom đang giữ owner | STOP không chờ lease; START đang chờ bị hủy; kiểm tra motor thật đã dừng |
| 5 | Đóng ứng dụng trong xung ngắn bình thường | STOP trước đóng RPC; không START sau `SHUTDOWN_REQUESTED`; GUI đóng hữu hạn |
| 6 | Thử session hết hạn/reconnect trong điều kiện **đã xác minh motor đứng yên** | Login mới, generation tăng; không replay START; đối chiếu ACK schema của firmware |
| 7 | Chỉ khi có kế hoạch riêng được duyệt: mô phỏng mất ACK hoặc ngắt kết nối | Không tự ý thực hiện. Có người trực cơ chế dừng độc lập; fault chặn mọi START, giữ owner; retry có backoff và kết thúc |
| 8 | Phục hồi sau fault | Reconnect riêng không đủ để clear fault. Yêu cầu emergency STOP một lần, đợi ACK và người giám sát xác nhận đứng yên; sau đó mới cho phép thao tác tiếp |
| 9 | Shutdown khi STOP chưa được xác nhận | UI/log cảnh báo camera có thể còn chuyển động; GUI không chờ vô hạn; kiểm tra checkpoint còn hướng chưa xác nhận |
| 10 | Khởi động lại sau fault được tạo có kiểm soát | Checkpoint nạp `FAULT_LATCHED`, START bị chặn, không tự gửi START; STOP xác nhận mới clear trạng thái logic |

## Khi thử không đạt

- Dừng thử bằng cơ chế độc lập, quan sát trực tiếp và giữ vùng chuyển động an toàn.
- Không xóa checkpoint để “mở khóa”, không tăng retry hoặc bỏ ownership. Emergency STOP thủ công cho phép một lần thử mới; nó vẫn có thể trả unconfirmed nếu transport còn backoff/offline.
- Lưu first-failure summary ngay. Ghi operation, code, request ID, connection generation, exception type/errno, thời điểm video và trạng thái owner. Không log nội dung đăng nhập.
- Nếu firmware trả ACK khác boolean `result: true`, giữ fail-closed, thu mẫu đã loại credential để xác minh schema trước khi sửa parser. Không coi mọi dict/non-false response là thành công.
- STOP RPC ACK chỉ là xác nhận giao thức, **không phải phép đo motor đã đứng yên**. Không ghi “physical STOP PASS” nếu không có quan sát độc lập.

## Kết quả cần ghi

Cho mỗi bước: phiên bản firmware, hash code, command ID, generation, thời gian START/STOP gửi và ACK, thời gian bắt đầu/dừng nhìn thấy trên video, trạng thái fault/owner, số retry, thời gian shutdown, kết quả PASS/FAIL và người giám sát. Chỉ nghiệm thu phần cứng khi có đủ bằng chứng này.
