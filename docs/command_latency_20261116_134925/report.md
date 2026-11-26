# Độ trễ lệnh quay camera — phiên 16/11/2026 13:49–13:53

Phân tích 390 lệnh pan/tilt; ghép SENT/ACK bằng rpc_command_id và operation, các mốc dispatch/wire/result bằng command_id. Đã đọc camera log chính và rotations .1/.2. Loại 30 lệnh zoom khỏi thống kê quay. Dùng monotonic_ns để tính, không trừ timestamp hiển thị đã làm tròn.

| Khoảng thời gian | Số mẫu | Mean (ms) | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|
| Sau gửi START → nhận ACK | 390 | 4.95 | 4.34 | 11.11 | 16.47 |
| Sau gửi STOP → nhận ACK | 390 | 5.21 | 4.49 | 11.21 | 27.96 |
| Worker dispatch → vào RPC motion | 390 | 9.58 | 8.42 | 17.48 | 33.03 |
| Worker dispatch → RPC trả về | 390 | 16.47 | 15.68 | 25.55 | 37.89 |
| Nhận frame → worker dispatch | 390 | 100.93 | 98.81 | 161.85 | 223.63 |
| Detection xong → worker dispatch | 390 | 46.48 | 37.24 | 102.51 | 152.17 |
| Nhận frame → RPC trả về | 390 | 117.40 | 114.83 | 179.64 | 247.35 |
| START trả về → STOP trả về | 390 | 143.08 | 142.90 | 157.71 | 183.71 |
| STOP trước → dispatch kế tiếp | 389 | 392.25 | 165.98 | 603.85 | 25435.35 |

## Diễn giải timestamp

`PAN_TILT_WIRE_DISPATCH` đặt trước khi gọi rpc.motion, chưa phải thời điểm packet tới camera. `PAN_TILT_START_SENT` đặt sau _send_frame: chỉ xác nhận phía client đã gửi xong. ACK được ghi sau receive_response/parse/validation trên client. Vì vậy ~4,95 ms là vòng gửi→phản hồi theo instrumentation, không phải thời gian một chiều camera nhận lệnh; cũng không chứng minh motor bắt đầu quay sau 4,95 ms. Không chia đôi RTT để coi là latency một chiều đã đo.

Ví dụ kCmdRight: SENT 13:49:41.961, ACK 13:49:41.968; monotonic delta 6,890 ms. Không có timestamp firmware nhận lệnh hoặc encoder đo lúc motor bắt đầu chạy. ACK chỉ là chấp nhận giao thức theo profile camera_py.

Độ trễ phần mềm trước ACK đáng kể hơn mạng: mean từ frame được nhận tới RPC trả về khoảng 117,40 ms; trong đó frame→dispatch khoảng 100,93 ms. Không cộng detection→dispatch vào frame→dispatch vì hai đoạn chồng nhau. Không cộng p95 giữa các stage để suy ra p95 tổng. Receive timestamp là lúc app nhận frame đã decode, không phải exposure; còn thiếu phần capture/encode/RTSP/decode và motor startup.

Controller yêu cầu xung 120 ms tính sau khi START trả về, sau đó STOP. Mean START-return→STOP-return 143,08 ms bao gồm thời gian pulse/chờ/STOP RPC, không phải thời gian motor vật lý chạy chính xác. Median nghỉ tới dispatch tiếp theo 165,98 ms phù hợp settle 150 ms cộng scheduling. Mean nghỉ 392,25 ms và max 25,44 s còn gồm khoảng không cần quay/mất target; không quy tất cả thành lỗi scheduling.

Phiên này không ghi UNCONFIRMED; đủ 390 ACK START và 390 ACK STOP của pan/tilt. Điều này không xác nhận tốc độ góc, gia tốc, motor stop vật lý hoặc chuyển động đúng hướng.

## Kết luận

Giao tiếp gửi lệnh và nhận ACK nhanh: START mean 4,95 ms, p95 11,11 ms. Từ quyết định dispatch tới RPC return mean 16,47 ms. Chưa đo được thời điểm camera nhận lệnh một chiều và motor bắt đầu quay. Muốn đo chính xác cần video/encoder hoặc camera quan sát ngoài đồng bộ với timestamp START; tính onset/độ dịch chuyển có reference góc. Công cụ benchmark đã có có thể ghi lệnh và nhập angle samples; chưa chạy thử motor thật trong phân tích này.

Chỉ tạo báo cáo/script phân tích, không đổi code điều khiển hoặc cấu hình, không tạo thêm backup. Số liệu: [metrics.json](metrics.json); tái tính: [analyze.py](analyze.py).
