# HANET P0 — kiểm tra phục hồi Pan/Tilt, đóng băng tracking

Ngày **13/11/2026**, UTC+7. Không kết nối hoặc điều khiển camera thật trong lượt này.

**Đã sửa hai lỗi persisted checkpoint có test tái hiện, bổ sung validation theo phạm vi PTZ và kiểm thử đường pan end-to-end bằng fake transport. Tracking/config/model giữ nguyên. Chưa thể tuyên bố khôi phục Auto Pan/Tilt hoặc Auto Zoom trên camera thật: semantics của `Control.DoPtz → result: {}` vẫn chưa được xác minh.** Response này tiếp tục UNKNOWN; không bypass fault latch.

## A. Root Cause Analysis

### Chuỗi lỗi thực tế

Đối chiếu [phiên 10:20](ai_ptz_current_status_20261113_102026.md), [phiên 09:36](ai_ptz_current_status_20261113_093627.md), [bản sửa ACK](hanet_ack_compatibility_fix_20261113.md), [hotfix transport](hanet_transport_fault_fix_20261113.md) và source:

1. Phiên trước có START zoom-out chưa được STOP xác nhận; checkpoint còn `kCmdZoomWide`.
2. Khởi tạo phiên 10:20 nạp `UNCONFIRMED_PREVIOUS_RUN`, chặn START. Có 0 START_SENT và 6 STOP_SENT.
3. Năm schema STOP được lưu đều có ID khớp, `result={}`, không có field bị ẩn/cắt trong result. Generation 2 không có schema riêng vì rate limit; lỗi của lần đó cũng là ACK_UNKNOWN.
4. Validator chỉ nhận boolean True theo contract cũ, nên empty result không xác nhận STOP. Active code/fault không được clear.
5. Zoom supervisor đã acquire lease cho tác vụ rồi giữ lease sau STOP failure; pan gặp MOTOR_BUSY. Không có PAN_TILT_DISPATCH, không phải tracker không tạo được target.

Các sự kiện ZOOM_STARTED/START_FAILED là vòng đời tác vụ, không đồng nghĩa có START trên dây. Reconnect success chỉ nói transport đã mở lại, không phải motor safe.

### Parser trước/sau hotfix

Checkpoint trước hotfix: `/mnt/files/Project/ptz/.hotfix_checkpoints/hanet_20261113`, commit `8887d4dc92bcf63fea78e1e90abb212d20ce242b`. Parser đó nhận response có ID khớp nếu không có `error` truthy và `result` không phải False; vì vậy chấp nhận `{}`, null, thậm chí missing result. Hotfix thêm `result is True` để không coi phản hồi chưa rõ là ACK. [Diff đã lưu](hanet_ack_compatibility_fix_20261113/parser_pre_hotfix_vs_hotfix.diff).

Điều kiện boolean-only không nhận schema đã quan sát trên firmware. Nhưng parser permissive cũ và các log “acknowledged” không lưu response cũng không chứng minh empty object nghĩa là command accepted hoặc motor stopped. Không khôi phục logic permissive đó.

### Hai lỗi checkpoint phát hiện thêm

[Hai test trước khi sửa đều FAIL](hanet_pan_tilt_restoration_20261113/reproduced_before.log):

- **Không kiểm tra version:** `{"version":999,"active_codes":[]}` được nạp thành READY dù ứng dụng không biết định dạng đó.
- **Khoảng lỗi sau rename:** STOP ACK thành công, file checkpoint đã thay thành empty, nhưng directory fsync lỗi. RAM giữ fault, trong khi một controller khởi tạo lại đọc file empty và trở thành READY. Test chèn lỗi đúng sau empty replace tái hiện được tình huống này.

Hai lỗi này được sửa trực tiếp. Chúng **không phải nguyên nhân được chứng minh của phiên 10:20**; phiên đó vẫn bị chặn bởi ACK_UNKNOWN. Không dùng lỗi checkpoint để thay thế kết luận về ACK firmware.

## B. Source Code Changes

| File | Function/logic thay đổi | Mục đích |
|---|---|---|
| [camera/hanet_controller.py](../camera/hanet_controller.py) | Constructor, `_load_motor_checkpoint` | Kiểm tra version 1, kiểu dữ liệu/code hợp lệ, giới hạn 8.192 ký tự; nạp cả checkpoint và STOP guard |
| Cùng file | `_write_motor_checkpoint`, `_save_motor_state`, `_discard_stop_guard`, `stop` | Giữ bản pending bền vững trong lúc commit kết quả STOP; phân biệt lỗi ghi checkpoint với thiếu ACK |
| [camera/hanet_ack.py](../camera/hanet_ack.py) | `validate_response` | Giới hạn method Control.DoPtz, operation START/STOP và sáu code PTZ hiện có; empty object vẫn UNKNOWN với reason riêng |
| [camera/hanet_rpc.py](../camera/hanet_rpc.py) | `_receive_response` | Truyền operation/code vào validator tập trung |
| [test/test_hanet_persisted_recovery.py](../test/test_hanet_persisted_recovery.py) | 18 test mới | Fixture có nguồn, checkpoint failure/restart, full pan transport và zoom→pan recovery |
| [Fixture STOP](../test/fixtures/hanet_stop_empty_result_20261113.json) | Mẫu tái dựng từ schema đã lọc | Ghi rõ không phải raw capture và device semantics chưa xác minh |
| [tools/run_hanet_fault_tests.py](../tools/run_hanet_fault_tests.py) | Danh sách modules | Đưa test mới vào harness offline có network guard |

Không sửa auto-pan policy, auto-zoom policy, ownership implementation, camera worker, GUI hoặc interface tracker→PTZ. Không cần bỏ gate để test pan dispatch đạt.

**184 file hiện có ngoài bốn file source/runner sửa vẫn khớp hash**, bao gồm toàn bộ module tracking, face, scheduling và config đã chụp. Hash `config.py`, `camera.json` và **6 model files** khớp trước/sau. Snapshot effective config lưu 153 giá trị thuộc nhóm AI/tracking/control, không lưu credentials. [Scope audit](hanet_pan_tilt_restoration_20261113/scope_audit.json), [so sánh tracking](hanet_pan_tilt_restoration_20261113/tracking_comparison.json).

Rủi ro của sửa checkpoint: thêm ghi/fsync trên STOP đã ACK; storage chậm có thể tăng thời gian hoàn tất. Cleanup guard thất bại có thể gây thêm fault bảo thủ ở lần khởi động sau. Không tăng retry, speed, pulse hoặc bỏ deadline hiện có.

## C. ACK Validation và bằng chứng protocol

Đã tìm source/tài liệu trong workspace và tra cứu công khai các cụm HANET, Control.DoPtz, mtrpc, GetPtz. Trong phạm vi tìm kiếm, **chưa tìm được tài liệu HANET xác định semantics của empty result hoặc lệnh đọc motor state phù hợp**. Tài liệu PTZ hãng khác không được dùng để suy ra giao thức HANET.

Theo [JSON-RPC 2.0, mục Response object](https://www.jsonrpc.org/specification#response_object), response success ở tầng RPC có `result`, giá trị do method quy định; error dùng member riêng. Đặc tả không quy định hành vi motor của Control.DoPtz. Vì thế cần tách bốn mức:

| Mức bằng chứng | Tình trạng hiện tại |
|---|---|
| Envelope JSON-RPC hợp lệ/correlated | Đã quan sát cho STOP empty result |
| RPC-level result response | Có hình thức result response theo chuẩn; không có error member trong các mẫu đã lưu |
| Device đã chấp nhận/thực thi STOP theo contract HANET | Chưa xác minh |
| Motor vật lý đã đứng yên | Chưa có telemetry hoặc quan sát độc lập trong dữ liệu |

`read_status()` hiện chỉ heartbeat và trả `telemetry_available=False`; `telemetry()` trả None. Không invent endpoint đọc motor, không dùng video FPS hoặc socket connected để suy ra motor stopped.

Validator mới giữ nguyên tập success, đồng thời giới hạn ngữ cảnh:

| Response/ngữ cảnh | Kết quả |
|---|---|
| Control.DoPtz, code/operation hợp lệ, ID khớp, boolean True | ACK_SUCCESS theo **LEGACY_BOOLEAN_TRUE** hiện hữu; success fixture trong test là tổng hợp |
| Boolean False hoặc error object | ACK_REJECTED |
| Empty result object | **ACK_UNKNOWN / EMPTY_RESULT_UNVERIFIED** |
| Missing/null/object chưa biết/string/number | ACK_UNKNOWN |
| Method khác | ACK_UNKNOWN / METHOD_NOT_SUPPORTED; không mở rộng success sang RPC khác |
| Operation/code không hỗ trợ | PROTOCOL_ERROR |
| JSON hỏng, ID không khớp, generation cũ, frame sai | PROTOCOL_ERROR theo logic hiện có; ID cũ có thể bị bỏ để chờ response đúng trong deadline |
| Timeout/kết nối/send/receive lỗi | TRANSPORT_ERROR |

Không thêm flag cho phép `{}` thành success. Không tạo xác nhận thủ công giả làm ACK firmware. Boolean True được bảo toàn như contract phần mềm có sẵn, **không nâng thành bằng chứng firmware đã kiểm chứng**. START và STOP đều kiểm tra cùng ngữ cảnh nhưng chưa có dữ liệu để tuyên bố schema thực tế của chúng giống nhau.

## D. Motor Recovery và ownership

### Định dạng và startup

Checkpoint chính giữ format version 1: `version`, `active_codes`; không lưu password, session hoặc connection generation cũ. Hướng chưa xác nhận cần được STOP qua session mới; chưa chứng minh firmware cho phép STOP cross-session này có tác dụng vật lý.

File phụ mới cùng basename, đuôi **`.pending`**, cũng chứa version/active_codes. Nó lưu tập hướng chưa được commit là đã STOP. Startup lấy **hợp** code từ hai file; một file invalid làm controller giữ fault với tập code PTZ đã biết. Không gửi START hoặc tự replay sau nạp fault.

Reason phân biệt `UNCONFIRMED_PREVIOUS_RUN`, `INCOMPLETE_STOP_CHECKPOINT`, `INVALID_MOTOR_CHECKPOINT`. Không tự xóa checkpoint hỏng/không hiểu được.

### STOP recovery

Luồng điều khiển vẫn serialized trên camera worker; emergency STOP có ưu tiên và được thử có giới hạn như trước:

1. START bị chặn khi có fault. Người thao tác dùng STOP hiện có; supervisor retry theo ngân sách hiện có.
2. Kết nối/reconnect nếu cần, chỉ gửi STOP hướng ghi nhớ. Không replay START.
3. UNKNOWN/rejected/transport/protocol failure: giữ active codes, fault và owner.
4. Chỉ sau STOP success theo contract validator hiện hữu: ghi tập active codes cũ vào `.pending`, fsync file và thư mục.
5. Giảm tập active codes, ghi checkpoint chính, fsync file và thư mục. Nếu lỗi: khôi phục code trong RAM; guard còn trên đĩa để lần restart không đọc nhầm READY.
6. Chỉ sau commit chính thành công mới dọn guard. Crash trước cleanup gây một lần recovery thừa nhưng giữ bảo thủ. Cleanup lỗi được log riêng; main checkpoint đã commit nên không biến nó thành “không nhận ACK”.
7. Khi mọi code đã clear và checkpoint commit: backend READY/reset budget. Supervisor chủ lease hoàn tất STOP bookkeeping rồi release; manual không tự cướp/xóa lease của chủ khác.

Các event mới cần thiết: `MOTOR_RECOVERY_STARTED`, `MOTOR_RECOVERY_FAILED`, `MOTOR_CHECKPOINT_COMMIT_FAILED`, `MOTOR_CHECKPOINT_CLEANUP_PENDING`. Event commit failure giữ `rpc_stop_accepted=true`, `physical_motor_stopped=null`; phân biệt ACK đã nhận với lỗi lưu trạng thái. Event hiện có MOTOR_FAULT_RECOVERED/PAN_TILT_DISPATCH/START_ACK/STOP_ACK tiếp tục được dùng.

Ownership/gate được kiểm chứng bằng tests: STOP unknown giữ zoom/pan owner; STOP hợp lệ giả lập cho phép release và acquire chiều ngược lại; manual không vượt fault; shutdown đóng admission ngay cả khi owner đã release. Nếu auto đã disabled vì fault, cần bật lại sau recovery và có observation hợp lệ, không replay lệnh cũ.

**READY là trạng thái điều phối phần mềm theo contract ACK đang dùng, không phải MOTOR_STOP_VERIFIED.** Với `{}` của camera hiện tại, bước 4 chưa đạt nên fault vẫn giữ. Không có thao tác clear fault dựa vào lời xác nhận người vận hành được thêm vào production.

## E. Test Results

Python: `/home/admin1/miniconda3/envs/py/bin/python`. Test harness chặn live socket, HTTP và VideoCapture; fake transport dùng encoder/decoder WebSocket production. Test thread dùng Event và timeout hữu hạn; retry/deadline dùng fake clock trong các fixture sẵn có. Không có phép thử camera thật.

| Bộ kiểm tra | Kết quả thực tế |
|---|---|
| Baseline trước sửa | **288 PASS, 1 SKIP / 289 ca** |
| Hai test phát hiện lỗi trên source trước sửa | **2 FAIL**, đúng lỗi version và directory fsync/restart |
| Fixture/schema theo method-command mới | **2 PASS** |
| Persisted checkpoint/recovery mới | **13 PASS** |
| Full Auto Pan transport integration mới | **3 PASS** |
| Harness parser/fault/ownership/shutdown sau sửa | **75 PASS / 75** |
| Regression tổng sau sửa | **306 PASS, 1 SKIP / 307 ca** |
| Camera thật, START schema thật, physical STOP, auto-pan vật lý | **NOT RUN / UNVERIFIED** |

Ca skip duy nhất ở baseline và sau sửa: `test_runtime_architecture.ControlIntegrationTests.test_rtsp_unavailable_schedules_backoff_without_raising`, thiếu GI cho Python ABI hiện tại. OpenCV lifecycle vẫn chạy. Không cộng 75 ca fault vào 307 ca regression vì là tập con.

Artifacts: [baseline](hanet_pan_tilt_restoration_20261113/baseline_tests.json), [fault](hanet_pan_tilt_restoration_20261113/fault_results.json), [regression](hanet_pan_tilt_restoration_20261113/regression_results.json), [log regression](hanet_pan_tilt_restoration_20261113/regression_tests.log). Có một lần phát triển test lỗi do mock telemetry trả MagicMock thay timestamp; đã sửa fixture dùng wraps, sau đó chạy lại đạt. Không sửa production để che lỗi test.

### Coverage theo yêu cầu

- Parser: True contract tổng hợp; `{}` fixture có nguồn vẫn UNKNOWN; false/null/missing/error; malformed JSON; ID mismatch; generation cũ; context START/STOP/code/method.
- Persistence: startup sạch, loaded fault, version sai, JSON hỏng, STOP success/unknown, reconnect không đủ safe, commit thành công rồi restart, cửa sổ crash trước persistence, fsync lỗi sau replace, guard chưa dọn, guard hỏng, cleanup lỗi, ghi lỗi trước START, nhiều hướng chỉ một phần được STOP.
- Ownership: zoom fault giữ owner, pan fault giữ owner, STOP success giả lập release, emergency/manual, hai chiều acquire, concurrency arbitration.
- Pan path: target hợp lệ ngoài deadzone đi qua policy → owner → worker → real HANET framing với fake socket → START/STOP ACK; deadzone/uncertain/stale không dispatch; owner busy chặn; zoom recovery xong và observation mới thì pan dispatch.
- Shutdown: bình thường/fault/unconfirmed/disconnect, deadline, không START sau shutdown, worker join hữu hạn — các test hotfix trước vẫn đạt.

Test crash mô phỏng đúng cửa sổ checkpoint bằng fault injection và controller mới đọc lại file; **không phải thử cắt điện thật**. Successful ACK trong các ca motor là contract tổng hợp, không phải xác minh firmware.

### Tracking freeze và replay trước/sau

[Capture script](hanet_pan_tilt_restoration_20261113/capture_tracking.py) chạy trước sửa và sau sửa, cùng 127 observation tổng hợp qua **ByteTrackPersonTracker và HybridReIDTargetSelector thật**, mocked appearance scores. Chuỗi gồm acquire, overlap hai người, tách người, full loss, reacquire.

- **127/127 bản ghi giống nhau**, gồm selected bbox, track ID, stage/hold/reacquiring, transition reason và số lần gọi scorer ReID.
- **7/7 transition giống nhau**: SEARCHING → TARGET_LOCKED → HOLD → TARGET_LOCKED → HOLD → SEARCHING/reacquiring → TARGET_LOCKED.
- 153 config values, hash config và 6 model files giống nhau.
- Tracking P0, scheduling và face worker regression có sẵn được chạy cả baseline và sau sửa; không có failure mới.

[Replay trước](hanet_pan_tilt_restoration_20261113/tracking_before.json), [sau](hanet_pan_tilt_restoration_20261113/tracking_after.json), [comparison](hanet_pan_tilt_restoration_20261113/tracking_comparison.json).

Giới hạn: không inference YOLO/OSNet/SCRFD/ArcFace trên video thật, không ground-truth identity benchmark. Kết quả chứng minh implementation/config không đổi và replay xác định tương đương trong phạm vi trên; **không tuyên bố tracking accuracy hoặc FPS thực tế được chứng minh chỉ bằng unit tests**.

Lệnh tái lập offline:

```bash
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py \
  --json docs/hanet_pan_tilt_restoration_20261113/fault_results.json
/home/admin1/miniconda3/envs/py/bin/python tools/run_hanet_fault_tests.py --regression \
  --json docs/hanet_pan_tilt_restoration_20261113/regression_results.json
/home/admin1/miniconda3/envs/py/bin/python \
  docs/hanet_pan_tilt_restoration_20261113/capture_tracking.py /tmp/hanet_tracking_recheck.json
```

## F. Hardware Validation có giám sát

Chưa thực hiện phase nào. Xem thêm [hướng dẫn ACK có giám sát](hanet_ack_hardware_validation_20261113.md).

| Phase | Thao tác và điều kiện | Bằng chứng cần đạt |
|---|---|---|
| 1 — STOP | Vùng chuyển động an toàn, người giám sát, STOP độc lập; auto OFF; thu STOP được phép và có giới hạn | Firmware/version, request ID/generation/code, schema và ý nghĩa response theo tài liệu hoặc thực nghiệm được kiểm soát; quan sát motor đứng yên |
| 2 — Recovery | Chỉ sau khi semantics đủ tin cậy và validator được cập nhật/kiểm thử theo bằng chứng | Fault clear đúng, main checkpoint cập nhật, `.pending` đã dọn, supervisor trả owner; restart không tự phát START |
| 3 — Manual | Auto Pan OFF, Auto Zoom OFF; xung manual ngắn, tốc độ thấp đã được người phụ trách cho phép | START/STOP ACK theo schema xác minh, đúng hướng và dừng quan sát được |
| 4 — Auto Pan | Auto Pan ON, Auto Zoom OFF, target hợp lệ di chuyển ngang | PAN_TILT_DECISION đúng gate, ngoài deadzone mới dispatch, START/STOP ACK, video xác nhận bám chuyển động |
| 5 — Pan + Zoom | Chỉ khi các phase trước đạt | Lease chuyển đổi đúng, không xung đột motor, STOP trước chuyển chủ |

Nếu `{}` vẫn chưa được chứng minh ở phase 1, giữ UNKNOWN và dừng ở đó; không chuyển sang phase 2–5. Không tăng speed/pulse hoặc bật adaptive. Không xóa `.json`/`.pending` để mở khóa; không biến xác nhận của người vận hành thành ACK firmware. Nếu cần workflow xác nhận thủ công trong tương lai, phải là bằng chứng riêng với audit rõ ràng.

## G. Remaining Issues

1. **Blocker chính:** chưa có bằng chứng HANET về command acceptance/execution của `result:{}`. Đã có schema STOP nhưng chưa có START/pan/tilt schema và bằng chứng STOP cross-session.
2. **Motor physical state:** backend không có telemetry phù hợp đã xác minh. Không tuyên bố motor đã dừng từ ACK/envelope hoặc kết nối thành công.
3. **Storage:** guard giảm cửa sổ mất uncertainty khi replace/fsync lỗi; không bảo đảm trước filesystem/hardware mất dữ liệu ngoài cam kết fsync. File I/O bị treo không bị socket abort ngắt; chưa đo overhead/độ bền trên thiết bị vận hành.
4. **Guard cleanup:** crash/cleanup lỗi sau commit có thể tạo thêm fault bảo thủ ở restart. Recovery hợp lệ bằng code hiện tại sẽ xử lý; không dọn file thủ công.
5. **Nghiệm thu:** tests chứng minh Auto Pan có thể dispatch sau recovery theo contract tổng hợp. Chưa có bằng chứng Auto Pan/Zoom đã hoạt động lại trên camera thật; tiêu chí hoàn thành firmware compatibility chưa đạt.

## H. Rollback

Workspace chính không có Git repository; đã giữ nguyên mọi file hiện có và tạo checkpoint riêng:

- Path: `/mnt/files/Project/ptz/.hotfix_checkpoints/hanet_pan_restore_20261113`.
- Trước sửa: **`02bdc0e59017d72c5e2ac74b38f583a52b94d1ca`**.
- Sau sửa: **`28fb2b4cd213765a04454a7ee03aa67624808f35`**.
- [Metadata](hanet_pan_tilt_restoration_20261113/checkpoint.json), [manifest](hanet_pan_tilt_restoration_20261113/change_manifest.json), [diff](hanet_pan_tilt_restoration_20261113/restoration.patch), [scope audit](hanet_pan_tilt_restoration_20261113/scope_audit.json).

[Rollback helper](hanet_pan_tilt_restoration_20261113/rollback.py) mặc định dry-run, kiểm hash để không ghi đè thay đổi về sau. `--backend-only` chỉ khôi phục ba file `camera/`; tests/runner mới vẫn còn, nên các test cho lỗi vừa sửa sẽ có thể FAIL sau rollback có chủ đích.

**Backend trước lượt này không đọc `.pending`.** Helper từ chối rollback khi có guard trong `logs/camera/motor_state`. Nếu dùng custom safety_state_path, người vận hành cũng phải kiểm tra thư mục đó. Hoàn tất recovery đã xác minh bằng backend mới trước khi rollback; không xóa guard để qua kiểm tra. Luôn đóng ứng dụng và xác minh motor an toàn trước khi áp dụng rollback.

```bash
# Chỉ xem, chưa ghi file:
python docs/hanet_pan_tilt_restoration_20261113/rollback.py
python docs/hanet_pan_tilt_restoration_20261113/rollback.py --backend-only

# Chỉ khi chủ động hoàn tác sau điều kiện an toàn nêu trên:
python docs/hanet_pan_tilt_restoration_20261113/rollback.py --backend-only --apply
```

Đã kiểm tra dry-run cả hai chế độ; không thực hiện rollback. Helper không sửa tracking/config/models, credential, log hoặc motor state. Không dùng rollback để quay về parser permissive trước hotfix.
