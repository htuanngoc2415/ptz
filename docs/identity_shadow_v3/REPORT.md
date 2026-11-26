# Shadow v3 và kiểm tra bản ghi 09:03:05

Bản ghi mới dùng được: **1502 frame / 170,07 giây**, checksum/decode hợp lệ, recorder báo 0 drop/error. Đã sử dụng toàn bộ để replay Phase 1. Shadow v2 chỉ xuất **4 mẫu / 168,81 giây** do lỗi bỏ dở giữa hai lượt ReID; v3 sửa lỗi này và đã kiểm chứng offline. Chưa có phép đo v3 chạy cùng camera thật.

Báo cáo log/capture: [audit phiên mới](../identity_shadow_audit_20261119_090305/REPORT.md).

## Sửa trong v3

`runtime/identity_shadow.py` giữ kết quả raw của đúng job/frame hiện tại. Nếu lượt chuẩn hóa gặp foreground đang chạy/chờ, worker đợi 10 ms ngoài scheduler rồi thử riêng lượt còn thiếu. Không tính lại raw; không giữ accelerator trong thời gian chờ. Kết quả được lưu CPU, chỉ một job đang xử lý. Khi xong hoặc hủy, giải phóng crops/embeddings; không tích lũy cache qua frame.

- Deadline vẫn tính từ receive timestamp gốc, tối đa 0,8 giây; không gia hạn khi retry.
- Stop/reset/đổi target hủy job đang chờ; inference đã bắt đầu có thể kết thúc rồi bị loại. Không preempt GPU.
- Foreground giữ ưu tiên admission. Mỗi lượt inference shadow đã bắt đầu vẫn chiếm tài nguyên tới khi xong; không đảm bảo FPS live không đổi hoặc đạt trần 5 Hz.
- Giữ cách crop/normalize, reference gallery, fusion và chọn embedding như scorer cũ. Baseline/motor, policy manager và các ngưỡng không sửa trong v3.
- Giữ reset v2: cùng UID/reference giữ session, interrupt continuity và cần ngưỡng 0,70 khi bắt lại.

Log thêm `admission_retries`, `completed_passes`, `discarded_passes`; retries không phải số frame rơi. Pass là batch extraction raw hoặc normalized, không phải mỗi crop hay mỗi minibatch nội bộ. Footer phân biệt lượng công việc đã làm nhưng không xuất comparison. `processing_ms` gồm chờ thử lại; không dùng nó làm GPU time thuần. Scorer v3 không đi qua wrapper `_scores`, nên metric `reid_identity_shadow_wall_ms` của v2 không còn là cơ sở so sánh; dùng processing/age trong comparison và branch resource telemetry.

## Kiểm chứng đã hoàn tất

- **290 tests pass**, bao gồm 22 test shadow/scheduler. [Log regression](regression.log), [script](run_regression.py).
- Test mới tái hiện bận trước raw, bận giữa raw/normalized, tiếp tục đúng lượt, expiry khi tài nguyên luôn bận, Stop/reset lúc retry. Xác nhận không publish frame hết hiệu lực, không tính lại raw, có đếm pass bị hủy.
- **OSNet thật CUDA trên 12 frame lấy dọc capture mới:** 37 candidate; raw/normalized/final score, quality và embedding khớp chính xác scorer baseline. [Script](validate_real.py), [kết quả](validation_result.json).
- Tái hiện tranh chấp bằng thread foreground giữ scheduler sau raw: worker thực sự gặp BackgroundBusy, chờ release rồi hoàn tất. **1 comparison, 2 pass, 1 retry, 0 stale, 0 discarded pass**. Timestamp nhận giả lập; đây không phải benchmark live.
- Regression và inference offline chạy trong `offline_guard`, không mở camera/motor; physical_commands=0.
- Compile và Git diff whitespace check pass. Snapshot trước sửa ở `../validation/identity_shadow_v3_before`; [hash thay đổi](changes.json). Không sửa raw log/capture hay checksum rollback cũ.

## Replay toàn bộ capture mới

Đã kiểm tra source/model hash theo capture, tính lại OSNet cho mọi candidate bằng pipeline baseline và tạo [evidence cache](capture_cache/manifest.json). [Báo cáo replay](full_replay/report.json), [phân tích khác biệt](replay_analysis.json).

| Kết quả trên cùng 1502 frame | Số frame |
|---|---:|
| Baseline replay chọn candidate | 1117 |
| Phase 1 replay chọn candidate | 894 |
| Cùng chọn một candidate | 877 |
| Cùng HOLD | 368 |
| Baseline chọn, Phase 1 HOLD | 240 |
| Phase 1 chọn, baseline HOLD | 17 |
| Cả hai chọn nhưng khác candidate | 0 |

Đây là replay mọi frame với verification baseline hoàn tất xác định ở mẫu kế tiếp, không tái hiện scheduler/reset live. Số chọn nhiều hay ít không phải accuracy. Nhãn tự động vẫn là pseudo-label; ID switches, correct/wrong tracking time, recovery latency theo GT chưa đo được.

**Hạn chế chính của Phase 1 là HOLD tại overlap.** Trong 240 frame baseline-only, 196 do PERSON_OVERLAP, 26 chờ xác nhận nhiều frame, 10 similarity thấp, 7 margin mơ hồ và 1 confidence thấp. Đoạn HOLD dài nhất **17,53 giây** (frame 5355–5881), gồm overlap rồi similarity thấp trước khi đủ xác nhận; không được gọi đây là thời gian mất target thật nếu thiếu GT.

Đã xem contact sheets [baseline-only](baseline_only.jpg) và [identity-only](identity_only.jpg), mỗi loại 12 mẫu phân bố theo chuỗi. Nhóm identity-only được xem đều khoanh người áo đen ở tiền cảnh; không suy rộng thành 17/17 đúng. Trong các ảnh overlap, box người tiền cảnh giao box người ngồi phía sau. [Audit geometry](overlap_audit.json) thấy 92/196 frame chỉ có xung đột nằm trong điều kiện minor-contact đang dùng ở baseline, còn manager Phase 1 dùng hard gate overlap 0,15. Đây là khác biệt chính sách cần đánh giá riêng trên nhiều capture/crossing trước khi bật điều khiển Phase 1; không phải lỗi admission v3. Chưa đổi policy từ kết quả một clip.

## Bạn cần làm tiếp

Chỉ phần đo v3 dưới tải camera thật cần bạn thao tác; không cần quay lại để cứu dữ liệu hay gán nhãn lại.

1. Khởi động lại: `python3 ptz/main.py` từ `/mnt/files/Project/ptz`. Chọn target, chạy shadow OFF khoảng **20 giây**.
2. Bật **Start record + nhãn target tự động**, rồi **Start Phase 1 shadow (chỉ log)**, chạy **60 giây** với đi lại/xoay người/che khuất như trước. Quan sát `saved` tăng. Nếu sau 10 giây vẫn đứng yên hoặc main giật rõ, Stop shadow và giữ log để kiểm tra.
3. **Stop shadow**, **Stop record**; chạy tiếp **20 giây**, chờ record hoàn tất rồi đóng. Báo đã xong.

Mục tiêu phiên này là xác nhận shadow v3 thu mẫu được trong tải thật và đo ON/OFF. Phase 1 vẫn chỉ quan sát; `IDENTITY_MEMORY_ENABLED=False`. Không cần tự sửa ngưỡng hay bật Phase 1 điều khiển camera.
