# Phase 1 shadow — chỉ quan sát, không điều khiển

**Bản hiện hành là v3:** xem [báo cáo, kiểm chứng và bước tiếp theo](../identity_shadow_v3/REPORT.md). Phiên live 09:03:05 cho thấy v2 bỏ dở job giữa hai lượt ReID. V3 giữ kết quả của lượt đã xong, thử lại lượt còn thiếu khi tài nguyên rảnh và hủy frame khi quá hạn/reset/Stop. Mô tả và số kiểm thử bên dưới thuộc bản ban đầu; dùng báo cáo v3 cho admission/reset hiện tại.
**Thử nghiệm v4, mặc định OFF:** [batch shadow và kiểm chứng offline](../identity_shadow_v4/REPORT.md). Đường v3 vẫn là mặc định; v4 chỉ bật bằng `PTZ_SHADOW_BATCHED=1`, không bật identity control.


Đã tích hợp vào ứng dụng. Mặc định OFF mỗi lần mở; không cần sửa config hoặc bật IDENTITY_MEMORY_ENABLED. Nút Start/Stop shadow nằm dưới Target Person trong Record & gán nhãn.

## Phiên thử tiếp theo

1. Khởi động lại ứng dụng như cũ: `python3 ptz/main.py` từ `/mnt/files/Project/ptz`.
2. Chọn target, giữ cấu hình camera/baseline như phiên trước. Chạy khoảng 30 giây khi shadow OFF.
3. Bấm **Start record + nhãn target tự động**, rồi **Start Phase 1 shadow (chỉ log)**. Kiểm tra trạng thái `Shadow ON`, `saved` tăng. Nếu ERROR hoặc saved không tăng, giữ log để kiểm tra.
4. Chạy 60–120 giây: target đi lại, xoay người, đi ngang người khác, che khuất rồi xuất hiện lại. Record ảnh giúp đối chiếu những quyết định khác nhau; shadow riêng chỉ lưu metadata.
5. Bấm **Stop shadow**, **Stop record**, tiếp tục chạy baseline khoảng 30 giây. Đợi record hoàn tất rồi đóng ứng dụng.
6. Gửi tên log phiên chạy và thời điểm thấy lệch/đứng hình (nếu có). Không cần gán lại toàn bộ nhãn thủ công.

File shadow: `logs/identity_shadow/shadow_<uuid>.jsonl`. File camera và capture vẫn ở thư mục cũ. Có thể Stop shadow bất kỳ lúc nào mà baseline tiếp tục chạy. Start lại tạo file/session mới; nếu worker cũ chưa kết thúc, chờ rồi Start lại.

## Cách hoạt động và giới hạn

- Chỉ nhận snapshot frame/candidate/reference/model và baseline proposal; không nhận controller, motor, target_session hay callback phát lệnh. Không ghi vào baseline selector, AI result, gallery hoặc target reference.
- Submit sau baseline select, trước gate motor cuối. Baseline_selected trong log là proposal cùng source frame, không có nghĩa motor đã thực thi.
- Một worker, một job in-flight, tối đa 5 job/giây theo thời gian submit; bận/quá cũ thì bỏ mẫu, không dồn hàng đợi. Copy frame và candidate ở inference owner; tính ReID/ghi log ở worker.
- Dùng model OSNet hiện có, không tạo thêm model/GPU weights. Tính appearance lại cho toàn bộ candidate của mẫu, dùng cùng raw/normalized-max representation như replay. Các lượt extraction đi qua scheduler hiện có, ưu tiên detection đang chờ; không có cơ chế preempt GPU job đã chạy.
- **Có thêm tính toán ReID và tranh chấp CPU/GPU**: không tuyên bố FPS không đổi. Đây là lý do cần so sánh shadow OFF/ON/OFF trên camera. 5 Hz là trần lấy mẫu, không phải bảo đảm worker đạt 5 Hz.
- Bỏ kết quả quá tuổi observation hiện có 0,8 s, đổi target/reference, reset hoặc Stop; không dùng kết quả cũ để so với baseline frame hiện tại. Không tiếp tục history qua target/reset session.
- Manager riêng với gallery update luôn OFF, `ptz_authorized=false`, `physical_commands=0`. Phase 1 live control vẫn OFF. Motor STOP được yêu cầu trước khi dừng shadow khi đóng ứng dụng.
- Tối đa 10000 comparison/file rồi tự dừng. Không lưu ảnh/embedding từng frame trong shadow log. Thiếu frame vẫn có thể xảy ra do sampling/busy/stale; không suy ra số frame bị mất camera từ skip counter.
- So sánh khác biệt không phải ground truth: Phase 1 HOLD khi baseline chọn cũng tính là `different`. Không diễn giải thành lỗi nhận dạng hoặc số ID switch. Kết quả shadow lấy mẫu và thời gian thực sẽ khác replay mọi frame.
- Stop vô hiệu hóa job ngay; model inference đang chạy không bị kill giữa chừng. Close chỉ đợi hữu hạn; worker daemon có thể kết thúc sau nếu model chậm, kết quả hết hiệu lực không được xuất thành quyết định mới.

## Nội dung log

`SHADOW_START`: wall clock, interval, max age, phạm vi so sánh và additional_inference.
`SHADOW_POLICY`: policy (gallery OFF) và checksum manager/shadow source.
`SHADOW_SESSION`: session ID, target UID và hash reference embeddings.
`SHADOW_COMPARISON`: frame ID, receive monotonic timestamp, baseline proposal/state, decision đầy đủ của Phase 1, lý do/score/candidate audit, different, processing_ms, age_ms, skip/stale counters, ground_truth=false.
`SHADOW_STOP`: tổng saved/skipped/stale/different. File bị gián đoạn/lỗi có thể thiếu footer; xem ERROR trên UI. Đây là chẩn đoán, không phải capture có chứng nhận hoàn tất.

Processing_ms là wall time xử lý shadow sau khởi tạo scorer (có thể gồm chờ scheduler); không phải inference GPU time thuần hoặc latency motor. GUI latency dùng profiler của ứng dụng. Hai timestamp cùng hệ monotonic của tiến trình để đối chiếu log.

## Kiểm chứng

Regression suite ban đầu 278 test pass, bao gồm 10 test shadow. Thêm một test riêng kiểm tra interval khi worker rảnh và chạy lại 11 test shadow (`final_shadow_tests.log`). Có kiểm tra default OFF không thread/file, copy không mutate baseline, Stop/reset/đổi target trong job, stale/future, model/disk error, restart, budget và so sánh cùng frame. Các regression tracking/STOP/pan/zoom/recorder/GUI startup mock đã pass (`regression_tests.log`).

Smoke offline OSNet thật CUDA với 12 PNG đã ghi: 12 comparison, 0 stale, không lỗi (`smoke_result.json`). Timestamp receive trong smoke được tạo giả lập để kiểm tra worker; **không phải benchmark camera hoặc FPS live**. Network bị chặn trong suite và smoke; không mở camera/motor. Chưa kiểm thử giao diện Start/Stop và tải shadow trên camera thật.

## Thay đổi và snapshot

Mới: `runtime/identity_shadow.py`, `test/test_identity_shadow.py`.
Sửa: `config.py`, `gui/cctv_app.py`, `runtime/inference_worker.py`.
Snapshot trước sửa: `../validation/identity_shadow_before/`; `changes.json` ghi hash trước/sau. Tắt shadow bằng Stop hoặc khởi động lại; không cần rollback code để baseline hoạt động. Không thay checksum rollback cũ; các rollback cũ sẽ phát hiện source được sửa thêm và từ chối ghi đè.
