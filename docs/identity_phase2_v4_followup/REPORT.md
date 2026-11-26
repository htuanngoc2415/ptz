# Phase 2 sau phiên live v4 — kết quả thử nghiệm 19/11/2026

**Đã hoàn thành benchmark, thử lịch lấy mẫu, kiểm chứng OSNet thật và regression. Chưa tích hợp thay đổi Phase 2 vào shadow live vì các phương án thử chưa đủ bằng chứng tốt hơn. V4 hiện tại tiếp tục là mặc định.**

V4 live đã VERIFIED về vận hành ở phiên 19:13:52: 276/276 quyết định tái hiện khớp, toàn bộ có ảnh đối chiếu. Xem [audit live](../identity_shadow_audit_20261119_191352/REPORT.md). VERIFIED không phải chứng nhận identity accuracy.

## 1. Benchmark A/B/C/D trên capture mới

Capture `capture_18e5ba0fa26d492cbb7c19839cccb4b2`: 1.120 frame, 1.985 candidate, khoảng 137,43 giây. A là replay baseline hiện tại; B là Phase 1 identity mọi frame; C thêm filtering/memory/multi-cue; D là C với lịch sự kiện hiện có và OSNet thật. A/B/C dùng cache bằng chứng đã kiểm tra checksum; D chấm lại ảnh bằng scorer v4.

| Variant | Frame có lựa chọn | Frame không có lựa chọn |
|---|---:|---:|
| A | 937 | 183 |
| B | 786 | 334 |
| C | 581 | 539 |
| D | 71 | 1049 |

Các count này không phải accuracy. Đặc biệt D có 766 frame không chạy ReID, được ghi unknown/HOLD; không so sánh tỷ lệ chọn trên toàn bộ frame như cùng coverage với B/C. Không giữ nhãn selected cũ trên frame chưa được đo.

B→C mất 205 lựa chọn, không có lựa chọn mới: 59 quality chưa rõ, 113 similarity thấp, 33 chờ xác nhận. Quality gate có thể phá continuity, kéo theo reacquire khó hơn; vì thế không quy cả 205 trường hợp trực tiếp cho quality gate. Overlap vẫn bị HOLD 98 frame ở cả B/C; ngoại lệ minor-contact chưa mang lại thêm lựa chọn trên tập này.

D có 71 lựa chọn trên 354 frame chạy ReID; C có lựa chọn mà D không có tại 512 frame, D có thêm 2 frame (1325, 4118). Hai frame thêm đã xem trực quan, bbox trên người áo đen; chưa phải chứng minh D chính xác hơn. [Ảnh](d_extra_c.jpg).

Đã xem 16 frame trải đều trong 205 trường hợp B chọn/C HOLD: người áo đen còn hiện diện trong các ảnh, có góc trước, bên và sau; một số bbox gần biên/cắt người. [Ảnh](c_lost_b.jpg), [chi tiết](failure_review.json). Đây là review chẩn đoán có thiên lệch lấy mẫu, không thay cho ground truth độc lập.

Ablation thêm trên cùng bằng chứng: flags OFF cho 786 selected; chỉ filtering cho 581; filtering + memory vẫn 581; thêm multi-cue vẫn 581, cùng phân bố lý do. Như vậy sự suy giảm đã xuất hiện ở filtering và ảnh hưởng nối tiếp lên trạng thái, không phải do memory mới tạo ra. Memory có tối đa 2 entry nhưng chưa thay đổi kết quả lựa chọn ở tập này. [Kết quả](filter_ablation.json), [script](filter_ablation.py).

## 2. Chi phí inference thật của lịch sự kiện

| Đo tuần tự offline | Chấm mọi frame | Event D |
|---|---:|---:|
| Job được gọi | 1120 | 354 |
| Minibatch | 1264 | 428 |
| Tổng outer accelerator hold, giây | 19.49 | 6.33 |
| Job wall time P95, ms | 41.04 | 41.48 |

D giảm số job nhưng có 7 gap >1 giây; max 7,668 giây. Không timeout trong replay tuần tự này. Bộ nhớ distractor tối đa 2 entry. Outer hold là thời gian giữ lane scheduler, không phải GPU utilization đo bằng profiler; không suy ra detection FPS hay overhead live. Có cold-start outlier ở reference; không lấy tỷ số wall time làm speedup production.

## 3. Thử lịch lấy mẫu, policy identity giữ nguyên

Đã chạy 30 kịch bản trên ba capture 17:32, 19:00, 19:13, tổng 3.706 frame nguồn. Các capture cũ là dữ liệu development dùng lại, không gọi là holdout độc lập. Không đổi acquire/keep/reacquire, max-age/gap, overlap, gallery hay model.

- `fixed200`: nhận job tối đa mỗi 0,2 giây, cùng rate cap v4 hiện tại. Đây là kịch bản đạt được cadence khi không bị tranh tài nguyên, không phải cấu hình mới giải quyết được live contention.
- `fixed333`: tối đa mỗi 1/3 giây.
- `adaptive`: 0,2 giây khi chưa xác nhận; 0,5 giây khi đã confirmed. Quyết định lịch chỉ dùng metadata và trạng thái đã quan sát, không nhìn embedding tương lai.
- `dense`: mốc xử lý dày; vẫn bỏ job busy/quá tuổi/reset. Không phải giả định mọi frame đều chạy được trong live.
- Thêm service delay giả định 0 / 150 / 350 ms cộng thời gian ReID cache; mô phỏng một job in flight và loại quá tuổi/reset. Không tái dựng chính xác foreground scheduler.

Kết quả kịch bản không cộng delay giả định; mỗi ô là **completed / selected**:

| Capture | Dense | Fixed 200 ms | Fixed 333 ms | Adaptive 200/500 ms |
|---|---:|---:|---:|---:|
| 173204 | 1478 / 1047 | 599 / 367 | 391 / 195 | 441 / 131 |
| 190016 | 1066 / 758 | 486 / 323 | 329 / 172 | 362 / 109 |
| 191352 | 1108 / 786 | 515 / 360 | 340 / 203 | 391 / 110 |

Adaptive giảm số job nhưng tỷ lệ xác nhận trên job đã xử lý cũng thấp hơn fixed200 ở cả ba capture. Điều này phù hợp với ảnh target di chuyển/quay hướng và ràng buộc geometry/continuity; không nên giãn lịch ngay khi confirmed chỉ để tiết kiệm ReID. Gap dưới 1 giây vẫn không bảo đảm IoU và chuỗi xác nhận đạt.

Ở capture mới, thêm 350 ms service delay làm fixed200 còn 301 completed / 133 selected; adaptive 276 / 93. Đây là cảnh báo về độ nhạy với contention, không phải dự báo định lượng live. [Toàn bộ kịch bản](schedule_sweep.json), [script](schedule_sweep.py).

Các lịch không lấy cùng tập frame nên chỉ so lựa chọn trực tiếp tại giao của frame ID. JSON ghi riêng `not_sampled`; tuyệt đối không coi frame không được đo là một kết quả sai người. Không công bố correct-target time, ID switch hay recovery latency từ count này.

## 4. Kiểm chứng bằng OSNet thật

Đã chạy scorer v4 trên hợp 599 frame được chọn bởi fixed200 và adaptive ở capture mới. Max chênh score so cache 2.98e-07. Giữ timestamp/age của kịch bản để kiểm tra parity; wall time inference được đo riêng, không giả làm thời điểm hoàn thành live.

| Lịch | Quyết định tái tính | Mismatch | Outer hold, giây |
|---|---:|---:|---:|
| fixed200 | 515 | 0 | 9.77 |
| adaptive | 391 | 0 | 7.38 |

Frame chung dùng chung inference/cost đo trong phép kiểm chứng, không phải hai phiên benchmark độc lập. [Kết quả](real_schedule_validation.json), [script](validate_schedule.py).

## 5. Regression, dữ liệu và giới hạn

- **330 test regression pass**, có offline guard chặn camera/motor. [Log](regression.log).
- Model/source/cache được kiểm checksum trong các công cụ chạy. Không tạo nhãn GT từ baseline hoặc OSNet.
- Benchmark tạo review pack 43 frame gồm representative/easy/hard/disagreement, còn trạng thái UNREVIEWED_TEMPLATE: [template](abcd/capture_1/review/annotations_template.json).
- False acceptance/rejection theo người thật, ID switches, correct/wrong tracking time và recovery theo visibility chưa được xác nhận bằng GT độc lập.
- Không đo thêm detection FPS/PTZ latency live vì không khởi động camera. Dry pan plan là tính toán offline, không phải lệnh motor.
- Không sửa runtime, controller, thresholds hoặc các flag. Các script và artifact mới nằm trong thư mục báo cáo. Snapshot status/diff/hash: [source_before.json](source_before.json), [git_status_before.txt](git_status_before.txt), [git_diff_before.patch](git_diff_before.patch).

## 6. Quyết định và bước tiếp theo

**Không tích hợp C/D hoặc lịch adaptive vào shadow live ở lần này.** Điều kiện “chỉ tích hợp khi có bằng chứng tốt hơn” chưa đạt: C tăng HOLD, D để khoảng trống dài, adaptive làm mất xác nhận, và chưa có GT độc lập chứng minh giảm bám nhầm. Giữ nguyên v4 mặc định và baseline là controller duy nhất.

Các hướng có cơ sở để nghiên cứu tiếp: tách quality gate cho học memory khỏi quality gate cho tiếp tục association (vẫn bảo vệ crop che khuất/biên); đo cơ chế ưu tiên ngắn hạn cho chuỗi pending trong giới hạn foreground; đánh giá camera-motion-aware geometry trước khi giảm IoU hay nới max-gap. Đây là hướng thử tiếp, chưa phải thay đổi đã triển khai hoặc bảo đảm tốt hơn.

Bạn chưa cần quay thêm. Dữ liệu hiện có đủ cho vòng offline tiếp theo. Cần review identity/visibility độc lập trước khi nghiệm thu chất lượng; một phiên camera mới chỉ hữu ích sau khi có phương án offline vượt các tiêu chí này.

Rollback: runtime không đổi nên không cần rollback code sản phẩm. Các artifact mới độc lập; có thể lưu trữ hoặc bỏ riêng thư mục báo cáo này, không xóa log/capture gốc. Không reset Git.
