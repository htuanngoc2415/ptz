# HANET ACK — quy trình xác minh có người giám sát

**Chưa chạy trên camera thật.** Tài liệu này dành cho lần thử được người phụ trách thiết bị cho phép, sau kiểm thử offline. Hiện chưa biết schema ACK thành công của firmware; không bật auto control để dò schema.

## 1. Chuẩn bị và trạng thái an toàn

- Có người quan sát trực tiếp, vùng chuyển động thông thoáng và cơ chế STOP độc lập đã kiểm chứng. Xác nhận camera đang đứng yên bằng quan sát, không dựa vào dòng “application stopped”.
- Chỉ một tiến trình điều khiển camera. Auto Pan/Tilt, Auto Zoom và autofocus OFF. Không khởi chạy pipeline AI với auto-control bật sẵn.
- Giữ nguyên checkpoint `logs/camera/motor_state/*.json`. Checkpoint `kCmdZoomWide` chưa xác nhận từ phiên trước phải được xử lý bằng STOP hợp lệ; không xóa để mở khóa START.
- Ghi phiên bản firmware, endpoint, hash source, thời gian và người giám sát. Không đưa tài khoản/mật khẩu vào biên bản chia sẻ.
- Chuẩn bị cơ chế giới hạn thời gian thao tác và STOP đã được người phụ trách xác nhận. Không thử tốc độ cao hoặc pulse dài; không chỉnh speed/pulse/deadzone trong cấu hình auto.

## 2. Bật diagnostics

Biến môi trường đọc khi tạo HANET client:

```bash
export PTZ_HANET_RESPONSE_DIAGNOSTICS=1
```

Đây chỉ là cấu hình, không phải lệnh khởi chạy camera. Mặc định giá trị là `1`; `0` tắt diagnostics, không thay đổi quy tắc ACK. Constructor cũng hỗ trợ `response_diagnostics=False` cho công cụ nhúng.

Xác nhận `PROFILING.enabled=True` để ghi log và `log_camera_control=True` để có camera log. Dùng quy trình khởi chạy thủ công đã được phê duyệt, với auto control OFF; không tự dùng script thử motor cũ để thu mẫu.

Các file cần giữ:

- `logs/camera/camera_control_<stamp>.log`.
- `logs/camera/first_failures_<stamp>.json`: giữ schema đầu START/STOP theo classification, kể cả khi queue đầy hoặc trace bị rotation.
- Latency trace, app log, checkpoint motor và video quan sát độc lập có đồng hồ đồng bộ.

Event cần tìm: `HANET_RESPONSE_SCHEMA`. Trường chính: `request_id`, `response_id`, `method`, `rpc_code`, `operation`, `connection_generation`, `response_keys`, `response_shape`, `result_type`, `result_keys`, `ack_classification`, `parser_reason`.

String values, tên key lạ và cả status code chưa được xác minh là không nhạy cảm đều bị bỏ. ID không khớp được biểu diễn `<unmatched>`. Đây là chủ ý bảo mật; không chuyển sang dump raw response nếu thiếu dữ liệu. Khi đã biết key/type, người phụ trách giao thức cần xác minh ngữ nghĩa từ tài liệu firmware hoặc mẫu đã được kiểm duyệt; nếu cần giá trị code, bổ sung allowlist hẹp sau khi đánh giá trường đó không chứa secret.

## 3. Thu schema trước, kiểm chứng chuyển động sau

1. Trong trạng thái đứng yên và dưới giám sát, xử lý STOP của hướng còn trong checkpoint bằng thao tác emergency STOP được phép. Một yêu cầu STOP vẫn có thể bị backoff chặn; ghi rõ có `STOP_SENT` hay chưa. Không gửi lặp vô hạn.
2. Nếu STOP trả UNKNOWN/REJECTED/protocol/transport error: giữ fault latch, lưu schema, kết thúc lần thử và xác minh trạng thái vật lý bằng phương tiện độc lập. Không tiếp tục START.
3. Đối chiếu schema STOP với tài liệu firmware hoặc bằng chứng đã được kiểm duyệt. Phân biệt “đã nhận yêu cầu” với “đã thực thi STOP”. Chưa đủ bằng chứng thì chưa thêm success rule và chưa mở auto-control.
4. Chỉ khi có điều kiện STOP đủ tin cậy, người phụ trách mới cho phép một thao tác START ngắn, tốc độ thấp nhất phù hợp thiết bị, có STOP giới hạn và quan sát trực tiếp. Thu cả schema START và STOP, không suy từ zoom sang pan/tilt.
5. Lưu fixture đã lọc secret và nguồn xác minh. Nếu cần hỗ trợ schema object, bổ sung validator/negative tests rõ ràng cho từng command/operation; chạy lại bộ offline trước lần thử tiếp.

## 4. Ma trận nghiệm thu sau khi schema đã xác minh

| Thứ tự | Thử nghiệm | Bằng chứng phải có |
|---|---|---|
| 1 | START/STOP thủ công có giới hạn | ID khớp, generation đúng, ACK success theo schema đã kiểm chứng; video xác nhận bắt đầu/dừng |
| 2 | Phục hồi fault bằng STOP | Backend READY, checkpoint không còn hướng active, supervisor release owner sau STOP; UNKNOWN vẫn giữ fault |
| 3 | Manual pan/tilt | Dừng hướng cũ trước hướng mới, không chồng lệnh |
| 4 | Auto Pan/Tilt, Auto Zoom OFF | Observation mới/hợp lệ, acquire pan, PAN_TILT_DISPATCH, START/STOP ACK và quan sát motor |
| 5 | Auto Pan/Tilt cùng Auto Zoom | Zoom trả owner sau STOP; pan acquire lại; không đồng thời hai chủ motor |
| 6 | Reconnect/session mới khi đã đứng yên | Không replay START; STOP đúng hướng; xác minh firmware cho phép STOP lệnh từ session trước |
| 7 | Restart sau trạng thái unconfirmed đã tạo có kiểm soát | Nạp checkpoint thành fault; START bị chặn; recovery có bằng chứng mới được clear |
| 8 | Shutdown bình thường / fault | Đóng hữu hạn; không START sau shutdown; STOP chưa xác nhận vẫn được báo unconfirmed |

Pan/zoom đã bị disable bởi fault cần được người thao tác bật lại sau xác minh an toàn. Release owner không tự phát lại lệnh cũ. Trong shutdown, owner có được release cũng không mở lại admission.

Không tự ngắt mạng khi motor đang chạy để tạo fault. Bài thử mất ACK cần kế hoạch riêng, cơ chế dừng độc lập và người giám sát tại thiết bị.

## 5. Biên bản

Mỗi ca ghi: firmware, code hash, command/operation, request ID, generation, classification/reason, schema đã lọc, timestamp send/ACK, timestamp chuyển động/dừng quan sát được, fault trước/sau, owner trước/sau, checkpoint, retry count, kết quả và người giám sát. Đánh dấu **NOT RUN** cho ca chưa chạy, **UNVERIFIED** cho trạng thái vật lý không quan sát được.

Một boolean ACK giả lập trong unit test không phải bằng chứng firmware. Một ACK thật nhưng chỉ xác nhận nhận lệnh cũng chưa đủ để nghiệm thu dừng motor vật lý.
