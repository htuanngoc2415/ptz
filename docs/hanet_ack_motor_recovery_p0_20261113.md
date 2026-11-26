# HANET P0 — ACK semantics, persisted motor fault và Pan/Tilt blocking

Ngày 13/11/2026, UTC+7. **Đã sửa và kiểm thử phần mềm trong phạm vi có bằng chứng. Chưa nghiệm thu khôi phục camera thật; không báo DONE toàn bộ P0.** Semantics firmware của `Control.DoPtz → result: {}` vẫn chưa xác minh, nên production tiếp tục giữ `ACK_UNKNOWN`. Không có lệnh PTZ thật được chạy trong lượt này.

## A. Root Cause

### CONFIRMED

1. **Parser từ chối `{}` vì không có success rule có bằng chứng cho object rỗng.** Contract boolean `true` là quy ước phần mềm hiện có, không phải schema firmware đã xác minh. Phản hồi thực tế STOP zoom là empty dict, không thỏa contract đó.
2. **Fault tồn tại qua restart:** phiên 10:47 nạp `UNCONFIRMED_PREVIOUS_RUN` và `active_codes=["kCmdZoomWide"]`. Không có STOP_ACK nên không đi tới clear checkpoint; đây không phải lỗi ghi đĩa được chứng minh trong phiên đó.
3. **Zoom giữ ownership do STOP chưa xác nhận.** Auto-pan vẫn enabled nhưng không acquire được: 112 bản ghi MOTOR_BUSY/owner zoom; counter worker toàn phiên là 2.424, không phải số frame riêng biệt.
4. **Không có pan dispatch:** 0 START_SENT, 6 STOP_SENT, 0 STOP_ACK, 0 MOTOR_FAULT_RECOVERED, 0 PAN_TILT_DISPATCH trong camera log 10:47. Reconnect thành công không xác nhận motor an toàn.
5. **Lỗi cleanup riêng, được tái hiện bằng fault injection:** trước sửa, `_discard_stop_guard()` nuốt OSError. Controller có thể trả READY/STOP thành công và zoom nhả owner trong khi `.pending` còn lưu fault; restart lại fault. Phiên thật chưa chạm nhánh này vì không có ACK.
6. **Lỗ hổng envelope riêng:** trước sửa, thiếu `jsonrpc` hoặc `result: true` kèm `error: null` vẫn có thể ACK_SUCCESS. Test mới tái hiện trước sửa.
7. **Cổng auto-pan chưa đầy đủ:** khi backend fault và không có owner, worker trước sửa có thể emit PAN_TILT_DISPATCH rồi backend mới từ chối. Test tái hiện được event này; không chứng minh START được gửi trái phép. Nay chặn trước dispatch và kiểm tra lại intent ở ranh giới I/O.

### PROBABLE

Firmware có thể dùng `{}` cho kết quả phương thức không trả dữ liệu; việc siết boolean-only có thể gây bất tương thích với firmware. Code cũ chấp nhận `{}` và các integration artifact từng báo acknowledged, nhưng chúng không lưu response, nên chưa chứng minh giả thuyết này.

### UNVERIFIED

Camera đã chấp nhận/thực thi STOP hay chưa; motor có đứng yên; boolean `true` có từng được firmware trả về; START/STOP và các hướng có cùng schema; firmware/model cụ thể nào cho phép coi `{}` là thành công. Không sửa tracking để bù cho các phần chưa biết này.

## B. Protocol Evidence

| Nguồn | Kết quả đọc/đối chiếu | Kết luận được phép |
|---|---|---|
| [Camera log 10:47](../logs/camera/camera_control_20261113_104723_870476.log), [first failures](../logs/camera/first_failures_20261113_104723_870476.json) | 5 schema STOP kCmdZoomWide: ID khớp, result dict rỗng, không truncate; EMPTY_RESULT_UNVERIFIED | Biết cấu trúc response; chưa biết mức thực thi |
| [App log 10:47](../logs/ptz_20261113_104720.log) và [báo cáo](ai_ptz_current_status_20261113_104720.md) | Tracking tiếp tục, shutdown 316 ms, STOP chưa xác nhận | Phần mềm đóng hữu hạn; không chứng minh motor dừng |
| [Báo cáo 10:20](ai_ptz_current_status_20261113_102026.md), [09:36](ai_ptz_current_status_20261113_093627.md), [ACK fix](hanet_ack_compatibility_fix_20261113.md), [restoration](hanet_pan_tilt_restoration_20261113.md) | Cùng chuỗi fault/ownership; 09:36 có START nhưng chưa có schema diagnostics | Không dựng response START từ exception |
| Toàn bộ camera logs còn lưu, quét riêng HANET_RESPONSE_SCHEMA | 10 mẫu: đều dict/ACK_UNKNOWN, thuộc hai phiên 10:20 và 10:47 | Không tìm thấy captured boolean success trong các schema còn lưu |
| [Fixture STOP](../test/fixtures/hanet_stop_empty_result_20261113.json) | Tái dựng cấu trúc từ diagnostics, provenance ghi không phải raw capture | Dùng test UNKNOWN; không dùng làm firmware success fixture |
| [Integration motion](../logs/hanet_integration_motion.json), [verified_ack](../logs/hanet_integration_verified_ack.json), [tài liệu integration](hanet_integration.md) | Chỉ lưu kết quả cấp ứng dụng, không raw response hoặc video motor | Tên “verified_ack” không chứng minh boolean True hay physical STOP |
| Git checkpoint trước hotfix `hanet_20261113`, commit `8887d4dc92bcf63fea78e1e90abb212d20ce242b` | Parser bỏ ID không khớp sau ép chuỗi; reject error truthy/result False; còn lại return | `{}`, null, missing result đều từng được chấp nhận; đây là code permissive, không phải SDK chính thức |
| Tìm tài liệu/SDK trong repo; tìm công khai `"HANET" "Control.DoPtz" API SDK` | Không tìm được tài liệu firmware/SDK chính thức định nghĩa ACK cho phương thức này; truy vấn công khai không trả kết quả | Không khẳng định tài liệu không tồn tại; chưa có nguồn đủ để mở success rule |
| [JSON-RPC 2.0 Specification, §5](https://www.jsonrpc.org/specification#response_object) | Response cần version 2.0, ID và một trong result/error; kiểu result do method quyết định | Đủ căn cứ kiểm tra envelope; không định nghĩa motor ACK của HANET |

Dữ liệu trích xuất và SHA-256 nằm trong [protocol_evidence.json](hanet_ack_motor_recovery_p0_20261113/protocol_evidence.json). Không cộng trùng events camera và latency.

### Năm mức xác nhận

| Mức | Bằng chứng hiện có | Trạng thái |
|---|---|---|
| JSON-RPC response hợp lệ về cấu trúc | Các mẫu `{}` có ID khớp, version/result được ghi nhận qua diagnostics | Có bằng chứng cấu trúc; không phải raw capture đầy đủ |
| RPC method báo hoàn tất thành công | Theo JSON-RPC chuẩn, result thuộc nhánh response thành công; firmware có tuân thủ semantics đó hay không chưa có tài liệu | Chưa xác minh cho firmware này |
| Camera chấp nhận lệnh PTZ | Không có định nghĩa ACK vendor hoặc mẫu success đã xác nhận | UNVERIFIED |
| Camera thực thi lệnh PTZ | Không có telemetry thực thi/quan sát đồng bộ | UNVERIFIED |
| Motor physically stopped | Log `physical_motor_stopped=null`, không quan sát trực tiếp | UNVERIFIED |

Trả lời A1: trước hotfix `{}` được chấp nhận vì không bị nhánh reject; sau hotfix boolean True được yêu cầu để tránh false ACK, không xuất phát từ firmware specification. Chưa có bằng chứng firmware trả True. Chưa biết DoPtz là void/empty-object method, hoặc START/STOP có cùng schema; chúng chỉ đang dùng chung parser trong phần mềm.

## C. Source Changes

[Diff đầy đủ](hanet_ack_motor_recovery_p0_20261113/changes.patch), [manifest](hanet_ack_motor_recovery_p0_20261113/change_manifest.json).

| File / function | Thay đổi và mục đích |
|---|---|
| `camera/hanet_ack.py::validate_response` | Envelope cần version 2.0; cấm result+error kể cả error null; kiểm tra error code integer/message string; chuẩn hóa reason; giữ `{}` UNKNOWN |
| `camera/hanet_rpc.py::_receive_response`, `motion` | Dùng reason mới, invalid JSON → MALFORMED_RESPONSE, lỗi transport → CONNECTION_FAILURE trong event; giữ deadline/generation/invalidation |
| `camera/hanet_controller.py::_discard_stop_guard`, `stop` | Cleanup lỗi phải truyền thất bại lên stop; khôi phục active code trong RAM, giữ fault và owner tới recovery hợp lệ |
| `control/auto_pan_tilt.py::_run`, `validate_intent` | Backend fault không phát PAN_TILT_DISPATCH, hủy intent đang chờ; kiểm tra fault lại lúc validate intent |
| `test/test_hanet_motor_p0.py` | 8 test mới cho envelope, cleanup→ownership→restart, motor fault không owner và manual override |
| Bốn test HANET có sẵn | Synthetic response thêm envelope JSON-RPC đúng; cập nhật kỳ vọng cleanup phải fail closed. Fixture thực tế không được đổi thành success |
| `tools/run_hanet_fault_tests.py` | Thêm module test mới vào harness chặn socket/HTTP/live capture |

Không sửa orchestrator locking, manual, Auto Zoom, GUI, pan policy, tracking hoặc inference. Quy trình ownership hiện có đã hỗ trợ release sau STOP thành công; lỗi mới được sửa tại điều kiện backend trả thành công. Không thay speed, pulse, deadzone, cooldown, settle hoặc zoom ratio.

Rủi ro tương thích: response thiếu version hoặc error object sai chuẩn trước đây có thể được chấp nhận/reject theo cách khác, nay PROTOCOL_ERROR. Không có bằng chứng cần hỗ trợ envelope lỗi như success. Cleanup I/O lỗi nay tiếp tục chặn motor; không che lỗi storage bằng READY.

## D. ACK Validation

| Response / điều kiện | Classification / reason |
|---|---|
| Envelope hợp lệ, ID đúng cả type/value, method DoPtz, code/operation trong phạm vi, result boolean true | ACK_SUCCESS / VALID_METHOD_SUCCESS — chỉ contract boolean phần mềm hiện có |
| result false trong DoPtz | ACK_REJECTED / EXPLICIT_REJECTION |
| Error object chuẩn, không có result | ACK_REJECTED / EXPLICIT_REJECTION |
| result `{}` | ACK_UNKNOWN / EMPTY_RESULT_UNVERIFIED |
| result null/object khác/string/number/list | ACK_UNKNOWN / UNVERIFIED_RESULT_SCHEMA |
| Thiếu result và error | ACK_UNKNOWN / MISSING_RESULT; không phải envelope success |
| ID khác | PROTOCOL_ERROR / MISMATCHED_REQUEST_ID; tiếp tục chờ đúng ID trong deadline |
| JSON lỗi, không phải object, thiếu/sai version, result+error, error sai cấu trúc | PROTOCOL_ERROR / MALFORMED_RESPONSE |
| Method khác | ACK_UNKNOWN / METHOD_NOT_SUPPORTED nếu envelope không có error |
| Operation/code không hỗ trợ | PROTOCOL_ERROR / INVALID_PTZ_OPERATION hoặc UNSUPPORTED_PTZ_CODE |
| Response từ generation cũ/client đã đóng | PROTOCOL_ERROR / STALE_CONNECTION_GENERATION |
| Connect/send/receive/timeout thất bại | TRANSPORT_ERROR; event UNCONFIRMED ghi CONNECTION_FAILURE |

START/STOP đang dùng chung validator hẹp cho `Control.DoPtz`, các code ZoomTele/ZoomWide/Left/Right/Up/Down. Boolean success vẫn ghi evidence `LEGACY_BOOLEAN_TRUE` trong ACK event và `physical_motor_stopped=null`; tên reason VALID_METHOD_SUCCESS không có nghĩa đã kiểm chứng firmware.

Không có UNKNOWN → SUCCESS mới. **Chưa thêm firmware success profile nào.** Nhánh boolean True được giữ theo contract có sẵn để kiểm thử hồi quy, chưa đủ để chứng nhận thiết bị tương thích. Mẫu `{}` thật tiếp tục UNKNOWN ở cả START/STOP và mọi code. Late ACK ngoài deadline không được chấp nhận; reconnect không replay START.

## E. Fault, Checkpoint và Ownership

Thứ tự hiện tại trên camera command lane:

```text
LOAD main + pending (hợp các active codes)
 → MOTOR_UNCONFIRMED / chặn START
 → RECOVERY_STOP (retry hữu hạn)
 → VALIDATE_STOP_RESPONSE
   ├─ UNKNOWN / REJECTED / PROTOCOL / TRANSPORT → giữ fault + owner
   └─ ACK theo contract
       → ghi pending + fsync file/thư mục
       → cập nhật main checkpoint + fsync file/thư mục
       → xóa pending + fsync thư mục
       → tất cả code đã xử lý: clear fault, READY, reset retry
       → supervisor hoàn tất STOP bookkeeping và release owner
       → acquire mới theo manual/settle/shutdown gate + observation mới
```

**Commit trước clear fault/release** để không cấp quyền chuyển động trước khi trạng thái recovery được lưu bền vững. Nếu ghi main hoặc cleanup thất bại: active code được trả lại RAM, STOP trả false, fault giữ nguyên. ACK đã nhận được vẫn được log `rpc_stop_accepted=true`; không gán nhầm là không có wire ACK.

Crash trước commit chính: checkpoint cũ hoặc guard giữ uncertainty. Crash sau main commit nhưng trước cleanup: startup có thể nạp guard và STOP lại bảo thủ. Nếu unlink đã xảy ra nhưng fsync thư mục cleanup lỗi: process hiện tại vẫn giữ fault; khi restart, guard có thể còn hoặc không. Main đã được commit STOP hợp lệ trước unlink, nên nếu guard không còn thì READY không bỏ qua một STOP chưa ACK. Nếu guard còn thì STOP lại bảo thủ. Đây là fault injection mô phỏng, không phải thử cắt điện thật.

Owner đang giữ chuyển động chưa xác nhận không bị nhả. Ở worker pan khi backend fault nhưng chưa có owner, lease vừa acquire chưa gửi lệnh được hủy cùng intent; đây không phải giải phóng owner của lệnh chưa STOP. Cổng backend trên command lane vẫn chặn START nếu fault thay đổi sau kiểm tra worker.

Retry tự động vẫn tối đa 5 lần theo budget/backoff hiện có; emergency STOP là một yêu cầu recovery rõ ràng, không replay START. Sau emergency STOP hợp lệ, watchdog zoom/pan quan sát READY rồi hoàn tất bookkeeping/release. Manual không tự xóa lease của chủ khác. Khi shutdown, admission vẫn đóng kể cả STOP thành công.

Checkpoint thật được đọc và hash trước/sau, giữ nguyên `active_codes=["kCmdZoomWide"]`. Không xóa main/guard hoặc sửa active_codes để mở khóa. Nếu chỉ có người quan sát motor đứng yên nhưng chưa có ACK semantics, chưa triển khai nút override: quy trình thủ công cần định danh thiết bị/firmware, người giám sát, thời gian, hướng lệnh, bằng chứng dừng độc lập và nhật ký được duyệt; không tự clear production state dựa trên lời xác nhận chung.

## F. Test Results

Môi trường: `/home/admin1/miniconda3/envs/py/bin/python`. Harness chặn live socket, HTTP và VideoCapture; fake WebSocket chạy encoder/decoder thật. Mọi success response trong tests đều **synthetic**, không được coi là captured firmware ACK.

| Test | Expected | Actual | Kết quả |
|---|---|---|---|
| Baseline trước sửa | Bộ hiện có ổn định | 306 PASS, 1 SKIP / 307 | PASS, có SKIP môi trường |
| 8 ca mới chạy trên code trước sửa | Tái hiện lỗ hổng | 6 test method lỗi, 13 failure records gồm subtests; 2 method pass | Reproduction đạt |
| ACK parser và diagnostics | True/false/empty/null/missing/error/ID/late/invalid JSON/generation/method phân biệt đúng | Các test trong fault harness pass; `{}` không thành success | PASS |
| Guard unlink/fsync failure và zoom owner | Không READY/release; recovery hợp lệ clear guard, restart READY | 2 ca mới pass | PASS |
| Persisted state/crash consistency/retry exhaustion | Không START khi uncertain; restart không bỏ fault chưa xử lý | Bộ persisted/fault/shutdown pass | PASS |
| Pan READY → policy → ownership → fake HANET START/STOP | Có DISPATCH/ACK lifecycle, release sau STOP | Integration test full transport pass | PASS |
| Deadzone/identity uncertain/stale | Không dispatch | Integration controls pass | PASS |
| Backend fault không owner | Không PAN_TILT_DISPATCH/START | Ca mới pass | PASS |
| Zoom owner/manual override | Auto không giành quyền | Ca hiện có + ca mới pass | PASS |
| Concurrent STOP/zoom/pan, emergency STOP | Serialize, không vượt fault/owner | Fault tests pass | PASS |
| Normal/fault/unconfirmed/unavailable shutdown | Thoát hữu hạn, không late START | Shutdown regression pass | PASS |
| Fault harness sau sửa | 0 lỗi | **83 PASS / 83** | PASS |
| Regression sau sửa, bao gồm fault harness | 0 lỗi trong phạm vi khả dụng | **314 PASS, 1 SKIP / 315** | PASS, có SKIP |
| GStreamer/GI unavailable test | Chạy khi ABI có GI | `test_rtsp_unavailable_schedules_backoff_without_raising` không chạy | SKIPPED |
| Tracking replay trước/sau | Bằng nhau | 127 observations, 7 transitions, mọi output bằng nhau | PASS |
| Camera thật / physical STOP | Cần giám sát | Không thực hiện | NOT RUN |

Không cộng 83 và 314: fault harness nằm trong regression. Không chạy full discovery toàn repo ngoài phạm vi đã nêu.

Artifacts: [baseline](hanet_ack_motor_recovery_p0_20261113/baseline_tests.json), [reproduction trước sửa](hanet_ack_motor_recovery_p0_20261113/reproduction_before.log), [fault results](hanet_ack_motor_recovery_p0_20261113/fault_results.json), [fault log](hanet_ack_motor_recovery_p0_20261113/fault_tests.log), [regression results](hanet_ack_motor_recovery_p0_20261113/regression_results.json), [regression log](hanet_ack_motor_recovery_p0_20261113/regression_tests.log).

Lệnh tái lập offline từ gốc ứng dụng:

```bash
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py --regression
/home/admin1/miniconda3/envs/py/bin/python docs/hanet_pan_tilt_restoration_20261113/capture_tracking.py /tmp/hanet_p0_tracking_recheck.json
```

## G. Hardware Validation Guide — chưa thực hiện

Giữ thêm [checklist ACK có giám sát](hanet_ack_hardware_validation_20261113.md). Không có công cụ live PTZ nào được tự chạy trong lượt sửa này.

**Chuẩn bị:** một tiến trình điều khiển duy nhất; camera trong vùng thông thoáng; người quan sát tại chỗ; phương án STOP độc lập đã kiểm tra; Auto Zoom/Auto Pan OFF. Ghi model, firmware, source hash, cấu hình, người giám sát, thời gian; lưu response đã lọc secret, camera log và video đồng bộ. Giữ nguyên checkpoint.

**F1 — STOP trước:** gửi một yêu cầu STOP có giám sát cho hướng tồn đọng. Đối chiếu request ID, method/code/operation, session generation, response schema với tài liệu firmware; quan sát motor. UNKNOWN/REJECTED/error thì dừng bài thử, không chuyển sang START. Camera vốn đang đứng yên và trả `{}` không đủ chứng minh STOP đã dừng một chuyển động. Khi cần kiểm tra đang chuyển động phải có phương tiện điều khiển độc lập đã được xác minh, có giới hạn và người giám sát; không tạo chuyển động bằng cách bypass fault của ứng dụng.

**Cổng semantics:** cần tài liệu vendor hoặc bằng chứng thử có giám sát được duyệt cho đúng model/firmware/method/code/operation. Sau đó mới thêm success profile hẹp, fixture provenance và tests. Nếu firmware không cung cấp ACK đủ xác minh, cần thiết kế riêng recovery thủ công được kiểm toán; chưa bật đường đó trong production.

**F2 — recovery checkpoint:** chỉ sau cổng semantics, dùng STOP hợp lệ; kiểm tra main commit, không còn pending, MOTOR_FAULT_RECOVERED, supervisor release, không có START replay. Restart ứng dụng, xác nhận READY không có fault tồn đọng sai và không tự phát chuyển động.

**F3 — manual pan/tilt:** Auto Zoom OFF, Auto Pan OFF. Người giám sát chọn pulse ngắn/tốc độ thấp trong giới hạn đã phê duyệt bằng điều khiển thủ công hiện có, không đổi config auto. Kiểm tra START_SENT/ACK, STOP_SENT/ACK, quan sát quay và dừng. Có bất thường thì dùng phương án dừng độc lập, dừng bài thử.

**F4 — auto pan/tilt:** Auto Zoom OFF, Auto Pan ON; target đủ điều kiện identity, còn hạn, chuyển trái/phải/lên/xuống. Đối chiếu policy → authorization → PAN_TILT_DISPATCH → START/STOP ACK → chuyển động/dừng thật. Trong deadzone, target uncertain/stale hoặc manual override phải không dispatch. Chỉ sau F4 đạt mới thử Auto Pan + Auto Zoom và kiểm tra không có START đồng thời trái ownership.

Biên bản mỗi bước ghi PASS/FAIL/UNVERIFIED riêng cho envelope, RPC completion, command acceptance, execution, physical stop; không dùng một cột “ACK OK” thay cho cả năm mức.

## H. Tracking Freeze Verification

Workspace chính không có Git repository; `git status` trả “not a git repository”. Đã tạo Git checkpoint riêng **trước khi sửa**, giữ nguyên toàn bộ source/config hiện có, không reset hoặc ghi đè thay đổi trước đó.

- Checkpoint: `/mnt/files/Project/ptz/.hotfix_checkpoints/hanet_motor_p0_20261113`.
- Before commit: `f10c87e979926a1c2e6453c43a718779405e8a35`.
- Snapshot 189 file source/config/test; **180 file hiện có không đổi**. Chỉ 9 file hiện có đổi và 1 test mới, theo manifest.
- Toàn bộ module YOLO, ByteTrack, OSNet, hybrid selector, overlap, ReID/gallery/thresholds, face và inference scheduling nằm ngoài diff. `control/pan_policy.py`, orchestrator, Auto Zoom, manual và GUI không đổi.
- `config.py`, `camera.json`, **153 effective config values**, hash **6 model files** bằng nhau trước/sau.
- Replay real selector/ByteTrack với score giả lập: **127 observations, 7 transitions**, output và state bằng nhau. Không phải inference benchmark hay bằng chứng tracking accuracy trên video thật.

[Scope audit](hanet_ack_motor_recovery_p0_20261113/scope_audit.json), [before](hanet_ack_motor_recovery_p0_20261113/tracking_before.json), [after](hanet_ack_motor_recovery_p0_20261113/tracking_after.json), [comparison](hanet_ack_motor_recovery_p0_20261113/tracking_comparison.json), [checkpoint metadata](hanet_ack_motor_recovery_p0_20261113/checkpoint.json).

## I. Remaining Blockers

**P0 chưa hoàn tất nghiệm thu thiết bị:** thiếu xác minh `result: {}` theo firmware và chuyển động vật lý. Với response đã ghi hiện tại, bản sửa này vẫn sẽ giữ fault và chặn pan/tilt; đây là hành vi chủ đích, không tuyên bố đã khôi phục camera quay.

Chưa có captured firmware success schema, mẫu START có semantics đã xác nhận, hoặc bằng chứng STOP trên connection mới dừng hướng tồn đọng từ phiên trước. Điều kiện ACK dựa trên schema có bằng chứng firmware và hardware F1–F4 chưa đạt. Các invariant phần mềm, checkpoint, ownership, mock dispatch và tracking freeze đã kiểm thử như mục F/H.

## J. Rollback

Dừng ứng dụng theo quy trình hiện có và xác minh motor bằng phương tiện độc lập trước khi thay source. Không rollback trong lúc worker đang chạy. Không xóa motor state để vượt điều kiện rollback.

[Rollback helper](hanet_ack_motor_recovery_p0_20261113/rollback.py) mặc định dry-run, kiểm hash mọi file dự định thay; từ chối nếu có thay đổi mới hoặc pending guard ở thư mục mặc định. Nếu dùng safety_state_path tùy chỉnh phải kiểm tra thêm đường dẫn đó. Bản trước có lỗi cleanup đã nêu nên helper không cho rollback khi pending còn tồn tại.

```bash
# Chỉ xem kế hoạch:
python docs/hanet_ack_motor_recovery_p0_20261113/rollback.py
# Khôi phục cả runtime/tests/runner của riêng lượt này khi đã kiểm tra:
python docs/hanet_ack_motor_recovery_p0_20261113/rollback.py --apply
# Chỉ runtime camera/control; test mới còn và có thể FAIL có chủ đích:
python docs/hanet_ack_motor_recovery_p0_20261113/rollback.py --runtime-only --apply
```

Helper chỉ khôi phục các file trong manifest và xóa test mới nếu rollback đầy đủ; không chạm tracking, config, model, logs hoặc checkpoint motor. Đã kiểm tra dry-run; không thực hiện rollback thật.
