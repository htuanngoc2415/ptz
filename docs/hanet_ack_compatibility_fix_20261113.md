# HANET P0 — ACK parser, diagnostics và kiểm thử phục hồi motor

Ngày: **13/11/2026**, UTC+7. Phạm vi: sửa source và kiểm thử offline; **không chạy camera thật**.

**Trạng thái: hoàn tất instrumentation, phân loại ACK và kiểm thử phần mềm theo contract hiện có. Chưa hoàn tất sửa tương thích firmware hoặc khôi phục Auto Pan/Tilt trên camera thật vì chưa có response schema được xác minh.** Không thêm success rule suy đoán để làm test PASS.

## A. Root Cause

### Bằng chứng phiên 09:36 ngày 13/11

Đã đọc lại [camera log](../logs/camera/camera_control_20261113_093630_907965.log), first-failure diagnostics, application log và [báo cáo mới nhất](ai_ptz_current_status_20261113_093627.md). [Kết quả đếm độc lập](hanet_ack_compatibility_fix_20261113/log_audit.json):

- START `kCmdZoomWide` gửi **09:36:49.614**, lỗi lúc **49.625**.
- **1 START_SENT, 5 STOP_SENT, 0 START_ACK, 0 STOP_ACK**. Cả sáu thao tác đã gửi đều gặp `HANET response has no verified success result`.
- 5 lần reconnect mở được generation mới nhưng không xác nhận STOP; không replay START.
- Không có PAN_TILT_DISPATCH; owner zoom giữ motor khi STOP chưa rõ. Retry và shutdown đã hữu hạn như báo cáo trước.
- **0 event schema response** trong log này. Không thể dựng lại nội dung response từ exception.

Nguyên nhân trực tiếp đã xác minh là điều kiện ACK quá hẹp so với **phản hồi thực tế đang nhận**, nhưng chưa biết phản hồi đó có nghĩa thành công hay không. Không có bằng chứng cho việc chỉ cần trả bất kỳ object nào là ACK.

### Đối chiếu bắt buộc trước/sau hotfix

Workspace ứng dụng không có Git repository. Checkpoint riêng trước hotfix còn nguyên tại `/mnt/files/Project/ptz/.hotfix_checkpoints/hanet_20261113`, commit **`8887d4dc92bcf63fea78e1e90abb212d20ce242b`**. Đây là snapshot **trước hotfix**, không phải bản sau hotfix. Bản sau hotfix được đối chiếu bằng manifest/hash và snapshot đầu lượt này.

[Diff trước hotfix → sau hotfix](hanet_ack_compatibility_fix_20261113/parser_pre_hotfix_vs_hotfix.diff) xác nhận:

| Hành vi | Trước hotfix | Hotfix 13/11 trước lượt này |
|---|---|---|
| ID | So sánh dạng chuỗi, bỏ response không khớp | Tương tự |
| Rejection | `error` truthy hoặc `result is False` | Tương tự, exception riêng |
| Success | Mọi response còn lại với ID khớp; hàm trả None | Bắt buộc `result is True`, trả True |
| Missing/null/object/string/number result | Được chấp nhận nếu không có error truthy | Bị từ chối |
| START/STOP | Cùng parser; dựa vào không có exception | Cùng parser, yêu cầu explicit True |
| Kết nối | Không có invalidation/backoff mới | Lock transaction, deadline, đóng socket/session lỗi, backoff; không replay START |

Thay đổi gây nhánh lỗi hiện tại chính là thêm `if result is not True: raise RuntimeError(...)`. Code cũ quá dễ chấp nhận; không được dùng nó làm tài liệu giao thức.

Các file `logs/hanet_integration_motion.json` và `logs/hanet_integration_verified_ack.json` chỉ lưu kết quả ứng dụng “acknowledged”, không giữ response. Chúng không chứng minh boolean True hay một object cụ thể là schema firmware. HTTP Auth.Login trả object cũng không phải bằng chứng cho Control.DoPtz trên WebSocket. File `../camera.py` còn permissive hơn và không được import vào ứng dụng; không dùng làm nguồn success rule.

**Phân biệt:** regression kiểm tra ACK đã xác định ở mức code; giả định boolean-only chưa được chứng minh với firmware; success/rejection thực tế của phản hồi ngày 13/11 vẫn chưa biết. Không gán lỗi này cho timeout/network khi log đang cho thấy nhánh kiểm tra response.

## B. HANET Response Schema

Không có fixture response thành công từ firmware trong nguồn hiện có. Thay đổi lần này **không mở rộng tập response success**. Boolean True được giữ để tương thích contract phần mềm cũ, được đánh dấu `LEGACY_BOOLEAN_TRUE`; chưa được gắn nhãn “firmware verified”.

Validator tập trung trong [camera/hanet_ack.py](../camera/hanet_ack.py):

| Input/điều kiện | Phân loại | Ghi chú |
|---|---|---|
| Object, ID đúng giá trị và type, result boolean True, không error | ACK_SUCCESS | Contract boolean hiện có, chỉ fixture tổng hợp |
| result boolean False hoặc error object không rỗng | ACK_REJECTED | Không đoán error code là auth expiry |
| Missing/null/object/string/number/list result | ACK_UNKNOWN | Không đổi UNKNOWN thành SUCCESS |
| JSON hỏng, envelope sai, JSON-RPC version sai nếu có, error field sai dạng | PROTOCOL_ERROR | Không lộ raw response trong exception |
| ID không khớp | PROTOCOL_ERROR cho response đó | Bỏ qua để chờ response khớp trong deadline; nếu chỉ có ID sai đến hết hạn, kết thúc protocol error |
| Generation thay đổi / client đóng trước xử lý ACK | PROTOCOL_ERROR | Không cho response cũ xác nhận lệnh mới |
| Timeout, send/receive/connection failure | TRANSPORT_ERROR | Kết nối bị invalidated, áp dụng backoff hiện có |

Ping/pong không phải ACK. Binary/unsupported opcode không được coi là JSON response. ACK đến sau deadline toàn transaction bị từ chối, kể cả có ID và result True.

Chưa biết START và STOP có schema khác nhau hay zoom khác pan/tilt. Không tạo command-specific success validator khi chưa có dữ liệu. Parser hiện nhận ID chính xác type; request thật dùng UUID string nên không cần chấp nhận ID số chuyển thành chuỗi.

## C. Source Changes

| File | Thay đổi |
|---|---|
| [camera/hanet_ack.py](../camera/hanet_ack.py) — mới | `validate_response`, classification/exception tập trung, `response_schema` chỉ lấy key/type an toàn |
| [camera/hanet_rpc.py](../camera/hanet_rpc.py) | Constructor flag diagnostics; `_response_diagnostic`; `_receive_response` dùng validator, deadline/generation; `motion` truyền context và ghi classification; lỗi frame được gắn protocol error |
| [telemetry/latency.py](../telemetry/latency.py) | `event` giữ schema đầu theo operation/classification trong first-failure artifact, độc lập queue và rotation |
| [test/test_hanet_ack_compatibility.py](../test/test_hanet_ack_compatibility.py) — mới | 20 test mới cho parser, bảo mật diagnostics, retention, fault và ownership |
| [tools/run_hanet_fault_tests.py](../tools/run_hanet_fault_tests.py) | Đưa test mới vào harness; thêm regression telemetry/profiling do writer được sửa |

Không sửa controller/supervisor ownership vì audit và test cho thấy đã có đường phục hồi theo STOP contract. Không sửa GUI, tracking, ReID, overlap gate, face recognition, speed, pulse duration, deadzone, zoom ratio hoặc recovery policy. [Scope audit](hanet_ack_compatibility_fix_20261113/scope_audit.json) xác nhận **181 file Python hiện có khác không đổi**, bao gồm config và các module điều khiển/AI được bảo vệ.

### Diagnostics

- Flag `PTZ_HANET_RESPONSE_DIAGNOSTICS=1` mặc định bật; `0` tắt. Có constructor override `response_diagnostics`. Flag không thay đổi validation.
- Event `HANET_RESPONSE_SCHEMA` chứa timestamp/run ID từ profiler, request/response ID, method `Control.DoPtz`, code, START/STOP, generation, keys/types và reason/classification.
- Chỉ tên key nằm trong allowlist được ghi. Key không biết có thể chứa secret nên chỉ đếm, không dump. ID không khớp được thay `<unmatched>`.
- Không ghi password/token/cookie/session, raw response hoặc string status/message. Chưa có numeric code được kiểm chứng không nhạy cảm nên chỉ ghi type, chưa ghi giá trị.
- Tối đa 16 key mỗi object, depth 2, ngân sách node cố định; phần schema tối đa 4.096 ký tự JSON, có truncation. Không duyệt toàn bộ object lớn để log.
- Rate limit **1 event/giây cho mỗi operation/classification** trên client; success không che mất first UNKNOWN. Cache không tăng theo request ID hoặc generation.
- First schema của START/STOP theo bốn classification ACK_SUCCESS/REJECTED/UNKNOWN/PROTOCOL_ERROR được giữ: tối đa 8 mục bổ sung trong `first_failures`. Cả success đầu tiên cũng được giữ để so sánh, dù tên file là first_failures.
- Writer ghi bất đồng bộ; queue đầy vẫn giữ schema đầu. Cần profiler enabled để có artifact; nếu tắt profiler, flag debug riêng không tự tạo logger khác.

[Benchmark cục bộ](hanet_ack_compatibility_fix_20261113/diagnostic_benchmark.json): 3.000 lần chiếu schema tổng hợp, mean khoảng **11,26 µs**, p95 **12,01 µs**. Đây chỉ là chi phí chiếu schema, không đo RPC end-to-end, disk I/O hoặc camera thật.

Compatibility impact: không thêm success schema; bool contract vẫn chạy. Error field sai kiểu, ID sai type, frame sai opcode và ACK quá hạn được phân loại chặt hơn. Event `HANET_TRANSPORT_ERROR` giữ tên để tương thích log hiện có nhưng `failure_kind` nay ghi đúng ACK_UNKNOWN/ACK_REJECTED/PROTOCOL_ERROR/TRANSPORT_ERROR; cần đọc classification, không suy mọi event đó là lỗi mạng.

## D. Motor Fault Recovery

Giữ lifecycle và safety guards hiện có:

1. Trước START, persist hướng vào checkpoint. START chưa rõ vẫn có thể đã được camera nhận nên giữ active code/fault.
2. Reconnect chỉ sửa transport; không clear motor fault, không replay START.
3. STOP UNKNOWN/rejected/protocol/transport error không xóa active code, không clear checkpoint, không trả lease.
4. Theo **contract phần mềm hiện có**, STOP success trên generation hiện tại mới xóa active code và persist checkpoint. Nếu persist thất bại thì giữ uncertainty. Chỉ khi mọi active code đã clear mới về READY/reset budget.
5. Supervisor sở hữu lease thực hiện bookkeeping sau STOP thành công rồi release. Manual emergency STOP không tự xóa lease của chủ khác; pan/zoom nhận biết READY và hoàn tất STOP bookkeeping kể cả budget supervisor đã hết.
6. Orchestrator vẫn áp dụng settling/manual/shutdown gate. Owner được release không đồng nghĩa tự resume target cũ; auto đã disabled do fault cần được bật lại sau xác minh an toàn.

Điều kiện thứ 4 là hợp đồng ứng dụng đã tồn tại, **chưa phải bằng chứng semantics STOP của firmware**. Chưa thêm schema object hoặc cho rằng ACK nhận lệnh đủ chứng minh physical STOP. Trong triển khai thực tế đang lỗi, response chưa rõ tiếp tục giữ latch. Việc cho phép schema mới mở lại control cần có xác minh protocol và quan sát STOP theo hướng dẫn phần cứng.

Đã kiểm thử zoom fault → STOP unknown giữ owner → STOP success giả lập trả owner để pan acquire; chiều pan → zoom tương tự. Có ca emergency STOP sau hết budget, generation cũ/ACK trễ, checkpoint qua restart, manual override, shutdown khi fault và shutdown không mở lại admission.

## E. Test Results

Python chạy test: `/home/admin1/miniconda3/envs/py/bin/python`. Harness chặn live socket, HTTP và `cv2.VideoCapture`; transport dùng fake, các ca backoff/deadline dùng fake clock. Các ca thread lifecycle dùng Event/join có giới hạn.

| Bộ test | Kết quả |
|---|---|
| Parser/diagnostics mới | **15 PASS** |
| Fault/ownership mới qua fake transport | **5 PASS** |
| Toàn bộ harness fault/shutdown | **57 PASS, 0 FAIL**; gồm 20 ca mới + 37 ca trước |
| Regression mở rộng | **288 PASS, 0 FAIL, 1 SKIP / 289 ca**; gồm cả 57 ca fault, control, tracking P0, RTSP OpenCV và telemetry |
| Camera thật / schema success firmware / physical STOP | **NOT RUN / UNVERIFIED** |
| Full discovery toàn repository | **Không chạy trong lượt này**; đã chạy regression theo phạm vi thay đổi |

Artifacts: [fault JSON](hanet_ack_compatibility_fix_20261113/fault_results.json), [fault log](hanet_ack_compatibility_fix_20261113/fault_tests.log), [regression JSON](hanet_ack_compatibility_fix_20261113/regression_results.json), [regression log](hanet_ack_compatibility_fix_20261113/regression_tests.log).

Ca skip: `test_runtime_architecture.ControlIntegrationTests.test_rtsp_unavailable_schedules_backoff_without_raising`, vì GI không khả dụng cho Python ABI hiện tại. Không cộng 57 ca fault thêm vào 289 ca regression vì chúng nằm trong cùng tập mở rộng.

Lệnh tái lập offline từ thư mục `ptz/`:

```bash
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py \
  --json docs/hanet_ack_compatibility_fix_20261113/fault_results.json
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py --regression \
  --json docs/hanet_ack_compatibility_fix_20261113/regression_results.json
```

Coverage gồm: boolean True tổng hợp, rejection/error object, missing/null/unknown result, malformed JSON/envelope, ID mismatch, ACK trễ/generation cũ, config OFF vẫn fail-closed, không rò giá trị secret, schema giới hạn, rate limit, rotation và queue đầy; START không replay, STOP unknown giữ fault, hai chiều release owner, manual STOP, shutdown hữu hạn và restart checkpoint.

**Không có test “HANET real schema success PASS”**. Không tạo một object success giả rồi gắn nhãn firmware fixture. START/STOP schema khác nhau cũng chưa thể kiểm chứng trước khi có dữ liệu thực.

## F. Hardware Validation

Hướng dẫn riêng: [hanet_ack_hardware_validation_20261113.md](hanet_ack_hardware_validation_20261113.md).

Thứ tự: xác minh motor đứng yên → auto OFF → bật diagnostics → thao tác được phép và giới hạn để thu schema → xác minh semantics → START/STOP có giám sát và quan sát vật lý → fault clear/owner release → manual pan/tilt → Auto Pan/Tilt với Auto Zoom OFF → sau đó mới phối hợp auto zoom.

Nếu response vẫn UNKNOWN, dừng ở bước thu bằng chứng. Không xóa checkpoint, không tăng retry/speed/pulse, không cho auto chạy chỉ để thử. Tắt diagnostics không sửa được compatibility.

## G. Remaining Risks / ranh giới chưa hoàn tất

- **Firmware ACK vẫn chưa biết:** phần sửa tương thích thực tế chưa thể hoàn thành. Dữ liệu cần tiếp theo là schema đã lọc của START/STOP và bằng chứng về ý nghĩa success/rejection.
- **Physical state:** không có telemetry motor. ACK_SUCCESS chỉ là kết quả parser theo contract; không phải đo motor đã dừng hay đi đúng hướng.
- **Session/reconnect:** chưa xác minh camera cho phép STOP của session mới dừng chuyển động do session trước tạo ra. Không replay START dù reconnect thành công.
- **Auto-pan thực tế:** test chứng minh khả năng acquire sau STOP giả lập; chưa chứng minh PAN_TILT_DISPATCH trên camera sau bản sửa này. Source hiện tại vẫn chặn response unknown của phiên cũ.
- **Diagnostics:** giới hạn depth/key và không ghi status values có thể cần một bước instrumentation hẹp tiếp theo sau khi biết schema. Không tự nới thành raw logging.
- **Môi trường:** GI/GStreamer không khả dụng cho Python test hiện tại; ca đó được skip rõ ràng, không ghi PASS. OpenCV lifecycle được kiểm thử offline.

Vì thiếu bằng chứng schema firmware, chưa đánh dấu toàn bộ tiêu chí P0 đạt. Phần được bàn giao là thay đổi có thể review, diagnostics đủ để nhận diện cấu trúc và kiểm thử không làm yếu safety guards.

## H. Rollback

Checkpoint riêng đầu lượt này: `/mnt/files/Project/ptz/.hotfix_checkpoints/hanet_ack_20261113`, commit **`01ac2d9086e076e07275c4df273081b689d78855`**. [Metadata](hanet_ack_compatibility_fix_20261113/checkpoint.json), [manifest](hanet_ack_compatibility_fix_20261113/change_manifest.json), [patch](hanet_ack_compatibility_fix_20261113/ack_fix.patch), [scope audit](hanet_ack_compatibility_fix_20261113/scope_audit.json).

Snapshot source sau sửa trong cùng checkpoint riêng: **`d8797a4cf67b391f91cd1b8bfbefc81806658fae`**. Không tạo Git repository hoặc ghi đè thay đổi trong thư mục ứng dụng của người dùng.

- Tắt schema diagnostics: đặt `PTZ_HANET_RESPONSE_DIAGNOSTICS=0` khi tạo client lần sau. Validation, retry, fault latch và checkpoint không đổi.
- Rollback code: chỉ sau khi ứng dụng đã đóng và người phụ trách xác minh motor an toàn. Script mặc định dry-run, kiểm hash để từ chối ghi đè thay đổi mới; phục hồi ba file sửa và bỏ hai file source/test mới.
- Không đụng credentials, camera config, logs hoặc persisted motor state. Rollback lượt này trở lại **hotfix bounded retry/shutdown**, không trở về parser permissive trước hotfix.

```bash
python docs/hanet_ack_compatibility_fix_20261113/rollback.py
# Chỉ khi chủ động quyết định hoàn tác, sau điều kiện an toàn ở trên:
python docs/hanet_ack_compatibility_fix_20261113/rollback.py --apply
```

Đã kiểm tra rollback ở chế độ dry-run; không thực hiện rollback trong lượt triển khai.
