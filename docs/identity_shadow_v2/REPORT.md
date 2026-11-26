# Shadow v2: reset identity và admission không chờ

Đã sửa và kiểm chứng bằng dữ liệu hiện có; không mở camera/motor. Baseline vẫn điều khiển, shadow mặc định OFF. Chưa có số đo FPS live của phiên bản mới.

## Sửa reset

`IdentityShadow.invalidate()` hủy token job đang chạy nhưng không xóa binding của target. Worker phân biệt:

- UID/reference thực sự thay đổi: manager/session mới, history mới.
- Baseline reset nhưng cùng target/reference: giữ manager/session/history; gọi `interrupt()` để bỏ pending confirmation, bbox continuity và quyền resume ngắn hạn; bắt lại cần ngưỡng 0,70.
- Shadow khởi động trong lúc baseline đang reacquire: lần xác nhận đầu cũng dùng 0,70, không dùng acquire 0,65.

`TargetIdentityManager.interrupt()` không xác nhận người từ history, không phát bbox, không sửa reference/gallery. Lượt observe chạy trên bản sao trạng thái nhỏ; chỉ commit nếu token/age còn hợp lệ sau quyết định. Không giữ lịch sử từ job bị loại sau xử lý. Start shadow lại là phiên mới; không lưu identity qua lần đóng ứng dụng.

Log thêm `SHADOW_RESET` với session, reset_serial, history_retained và threshold; comparison ghi baseline_reacquiring/reset_serial. Không cần dựa vào baseline_state SEARCHING để đoán ngưỡng.

## Giảm tranh chấp inference

Thêm context admission chỉ áp dụng cho worker shadow. Tại mỗi lượt xin accelerator:

- Nếu tài nguyên đang có owner hoặc có inference chính chờ: shadow ném `BackgroundBusy`, bỏ job và đếm `ACCELERATOR_BUSY`; không vào hàng đợi.
- Nếu rảnh: chạy lượt đó, vẫn serialize cùng model như trước. Nested model calls trong cùng thread không bị deadlock.
- Context không lan sang thread baseline; lịch baseline khi shadow OFF giữ nguyên. Mọi waiter foreground được tính (detection, face, ReID, recognition).

Không đổi trần lấy mẫu 5 Hz, không tăng tuổi cho phép 0,8 s, không bỏ gate score/overlap. Job GPU đang thực thi không thể preempt; foreground đến sau vẫn có thể chờ lượt ngắn đang chạy. CPU preprocessing/model forward vẫn có chi phí.

**Chưa tái sử dụng embedding baseline**: shadow vẫn tính appearance của toàn bộ candidate trên frame mẫu khi được admission. Đây là giảm xếp hàng/tranh chấp, không phải loại bỏ toàn bộ ReID trùng lặp. Vì vậy chưa tuyên bố FPS đã tăng hoặc overhead bằng 0. Số mẫu shadow có thể giảm; bỏ mẫu khi bận là chủ ý, cần đo tiếp trên camera. Ngưỡng/cửa sổ confirmation không được nới để bù việc sampling ít.

## Log đo A/B

- `SHADOW_START.monotonic_ts`: lúc Start được chấp nhận.
- `SHADOW_STOP.monotonic_ts`: lúc Stop được yêu cầu; `closed_monotonic_ts`: worker đóng file.
- Counters tách `WORKER_BUSY`, `RATE_LIMIT`, `INPUT_AGE`, `FRAME_SIZE`, `ACCELERATOR_BUSY`, `STOPPED`, `SUPERSEDED`, `RESULT_AGE`, `SCORER_TIMEOUT` khi phát sinh.
- Checksum source có thêm scheduler. Footer có counters cuối. Error có thể làm thiếu footer như trước và hiển thị ERROR trong UI.

## Kiểm chứng

**285 test pass** (`regression_tests.log`), gồm reset giữ history nhưng buộc 0,70; khởi động ở reacquire; đổi target tạo session khác; timestamp/counters; scheduler không chạy background khi foreground đang giữ tài nguyên hoặc đang đợi; context không rò sang baseline; nested scheduling; các regression stale/session/STOP/pan/zoom/recorder.

Tính lại OSNet thật CUDA trên **471 source frame** của phiên 08:48:52 (`replay_recorded_samples.py`). Dùng timestamp mẫu và ranh giới reset đã ghi; không mô phỏng scheduler v2 hay dự đoán FPS live.

| Chỉ số | Kết quả |
|---|---:|
| Logic cũ tính lại khớp lựa chọn shadow đã ghi | 471/471 |
| Shadow cũ chọn | 235 |
| Cách reset mới chọn | 227 |
| Quyết định thay đổi | 8 |
| Shadow chọn khi baseline không chọn | 11 → 3 |

8 thay đổi chính là nhóm điểm 0,663–0,699 sau reset ở frame 2297/2305/2312 và 5970/5974/5988/5996/6001. Không gọi chúng là 8 lỗi nhận nhầm đã sửa: đây là sửa tính nhất quán acquire/reacquire. Cả ba mẫu khác biệt còn lại vẫn cần xem như policy disagreement, không là bằng chứng accuracy cao hơn.

`counterfactual.jsonl` lưu quyết định cũ tính lại và mới từng frame; summary có checksum capture. Ground truth false, physical commands 0. Source/model được dùng từ workspace và manifest capture; PNG đã verify checksum. Raw log/capture không đổi.

Smoke worker OSNet thật dùng ảnh lưu sẵn, timestamp nhận giả lập: xem `smoke_result.json`, `smoke.log`. Không đo camera thật. Compile và diff check pass.

## Người dùng cần làm tiếp

1. Khởi động lại ứng dụng, chọn target; shadow OFF **30 giây**.
2. Bấm **Start record + nhãn target tự động**, rồi **Start Phase 1 shadow (chỉ log)**; thử **60–120 giây** gồm xoay người, crossing, che khuất, mất và bắt lại.
3. Bấm **Stop shadow**, **Stop record**, giữ baseline chạy thêm **30 giây**, chờ record hoàn tất rồi đóng.
4. Gửi tên log hoặc báo đã xong; ghi thời điểm nếu thấy lag. Không cần gán nhãn lại.

Nếu saved tăng chậm, xem skip/ERROR; ACCELERATOR_BUSY sẽ được phân tích trong log, không tự tăng rate. Nếu main lag, Stop shadow và giữ log. Lần thử nhằm xác nhận FPS/độ trễ, số session thật và reacquire sau mất track. Chưa bật Phase 1 motor.

## Snapshot

Trước sửa: `../validation/identity_shadow_v2_before/`. Sửa `runtime/identity_shadow.py`, `runtime/scheduling.py`, `ai/target_identity.py`, `test/test_identity_shadow.py`. `test/test_target_identity.py` được snapshot nhưng không sửa. `changes.json` ghi hash trước/sau. Không reset Git, không chỉnh baseline selection/motor/codec/config. Các rollback cũ sẽ phát hiện source sửa thêm; không cập nhật checksum cũ để che sửa mới.
