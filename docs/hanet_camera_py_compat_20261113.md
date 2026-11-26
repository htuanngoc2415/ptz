# HANET — tương thích với camera.py đang điều khiển được thiết bị

## Kết quả

Đã sửa **ba file backend HANET**, dựa trên việc người dùng xác nhận `/mnt/files/Project/ptz/camera.py` điều khiển được camera và phản hồi `{}` đã ghi trong log. Không sửa script tham chiếu, GUI, manual worker, Auto Zoom, Auto Pan, tracking, cấu hình hoặc thông số điều khiển. Không tự chạy camera thật.

Đây là thay đổi tương thích triển khai theo bằng chứng từ người dùng, **không phải chứng nhận firmware hoặc bằng chứng motor vật lý đã dừng**. Các báo cáo trước giữ `{}` UNKNOWN vì chưa có xác nhận sử dụng client tham chiếu thành công; lượt này bổ sung profile `camera_py` cho backend đang triển khai, vẫn giữ profile `strict`.

## Đối chiếu với script tham chiếu

- `_login()` và `_md5()` giống nhau theo AST: digest challenge/login không phải khác biệt được tìm thấy.
- Cùng HTTP `/mtrpc`, WebSocket `/mtrpcoverwebsocket`, method `Control.DoPtz`.
- Test đối chiếu payload thực tế của hàm `motion()` trong script với `_motion_payload()` trong ứng dụng: **cả 6 code, cả START/STOP giống nhau**, với cùng session, ID và speed. Không đổi channel, step, preset, patrol hoặc hướng.
- Script tham chiếu không bắt buộc `result is True`; nó chấp nhận response không có error truthy. Script còn bỏ qua timeout và không đối chiếu ID.
- Backend ứng dụng trước sửa bắt buộc boolean True; vì vậy loại response `{}` đang nhận, giữ persisted fault và khiến cả manual lẫn auto không thể START.

Log mới hơn cũng xác nhận điểm chặn vẫn còn:

| Camera log | STOP_SENT | Schema được lưu |
|---|---:|---|
| `camera_control_20261113_110909_872222.log` | 6 | 5 STOP dict / EMPTY_RESULT_UNVERIFIED |
| `camera_control_20261113_111158_808656.log` | 4 | 3 STOP dict / EMPTY_RESULT_UNVERIFIED |

Các phiên này không có START_ACK/STOP_ACK. Những failure ở tầng controller không tương ứng một-một với lệnh gửi.

## Thay đổi

| File | Thay đổi |
|---|---|
| `camera/hanet_ack.py` | Profile `camera_py` nhận **đúng empty object** cho DoPtz, operation START/STOP và 6 code PTZ đã biết; mặc định validator độc lập vẫn strict |
| `camera/hanet_rpc.py` | Truyền profile vào validator, ghi `CAMERA_PY_EMPTY_RESULT` thay vì gán nhầm LEGACY_BOOLEAN_TRUE; diagnostics ghi profile đang dùng |
| `camera/hanet_controller.py` | Client mặc định của backend HANET dùng profile camera_py; có thể chọn `ack_profile='strict'`. Client được inject tùy chỉnh giữ contract của nó |

Không sao chép việc bỏ qua timeout/ID của script. Vẫn cần response JSON-RPC hợp lệ, ID khớp đúng type/value, không error, đúng generation và trong deadline. Timeout, mismatched ID, malformed JSON và session cũ không thành success. Null, missing result, object có field lạ, string/number/list không được mở rộng thành success. Explicit error/false vẫn bị từ chối.

Profile mặc định áp dụng cho `HanetController` của ứng dụng, **không tự phát hiện model/firmware**. Việc hỗ trợ empty result cho sáu hướng dựa trên cùng đường DoPtz trong script người dùng xác nhận hoạt động; chưa có phép thử vật lý riêng từng hướng trong lượt này.

Checkpoint vẫn chỉ clear sau STOP được chấp nhận theo profile và commit/cleanup thành công. Không xóa checkpoint để vượt fault. Ownership, retry budget, shutdown deadline và chặn START khi motor unconfirmed giữ nguyên. ACK vẫn ghi `physical_motor_stopped=null`.

## Kiểm thử

- **8/8 test tương thích PASS**: payload parity với script; empty result giới hạn scope; các envelope/unknown khác vẫn bị chặn; default backend recovery checkpoint → manual sáu hướng → restart READY; strict mode giữ fault; timeout không ACK; generation cũ không ACK; auto-pan qua fake WebSocket START/STOP `{}` và release ownership.
- **Regression: 314 PASS, 1 SKIP / 315**. Skip do GI không có cho Python ABI hiện tại. Đây là bộ riêng, không bao gồm 8 test mới.
- Snapshot hash: chỉ ba file runtime trên đổi; **193 file đã chụp khác giữ nguyên**, gồm source/config và checkpoint motor có sẵn. Không chỉnh test cũ để nhận `{}` trong strict mode.
- Test dùng fake socket/login, chặn HTTP thật; không dùng kết quả mô phỏng để khẳng định camera thật đã quay.

Artifacts: [compat tests](hanet_camera_py_compat_20261113/compat_tests.log), [regression](hanet_camera_py_compat_20261113/regression_results.json), [diff](hanet_camera_py_compat_20261113/changes.patch), [manifest](hanet_camera_py_compat_20261113/change_manifest.json), [hash trước sửa](hanet_camera_py_compat_20261113/before.json). Bản sao ba file trước sửa nằm trong thư mục `hanet_camera_py_compat_20261113/before/camera/`.

```bash
PYTHONPATH=.:test /home/admin1/miniconda3/envs/py/bin/python -m unittest -v test_hanet_camera_py_compat
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py --regression
```

## Áp dụng cho lần chạy tiếp theo

1. Đóng ứng dụng cũ và script `camera.py` nếu đang chạy, rồi mở lại ứng dụng để nạp code mới. Chỉ dùng một chương trình điều khiển camera khi kiểm tra.
2. Giữ Auto Pan/Auto Zoom OFF trước khi kiểm tra manual. **Nhấn STOP một lần** để xử lý fault tồn đọng bằng phản hồi camera thật; không xóa `motor_state/*.json`.
3. Sau recovery thành công, thử pan/tilt thủ công ngắn rồi STOP. Sau manual hoạt động mới bật Auto Pan.

Dấu hiệu log cần có: `ack_profile=camera_py`, `evidence=CAMERA_PY_EMPTY_RESULT`, STOP_ACK → MOTOR_FAULT_RECOVERED; tiếp theo manual START_ACK/STOP_ACK hoặc auto PAN_TILT_DISPATCH. Nếu STOP vẫn lỗi hoặc fault chưa clear, chưa có căn cứ coi camera đã phục hồi; cần đọc log mới. Một ACK phần mềm không thay thế quan sát motor quay/dừng thực tế.
