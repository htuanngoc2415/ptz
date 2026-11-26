# Phase 2 — Candidate Filtering, Distractor Memory và Event-Driven ReID

**Ngày:** 19/11/2026. **Phạm vi triển khai:** PoC offline, mọi feature flag mặc định OFF.  
**Live v4:** `AWAITING_LIVE_EVIDENCE`. **Phase 2 production:** chưa đủ bằng chứng để bật.

## 1. Kết quả và quyết định

Đã thực hiện audit, đóng băng scorer/scheduler v4, tái lập regression/parity, mở rộng `TargetIdentityManager` hiện có, xây dựng lịch ReID theo sự kiện offline, chạy OSNet CUDA thật và benchmark A/B/C/D trên **3 capture, 4.674 frame, 9.900 candidate**. Không mở camera hoặc gửi lệnh motor.

Kết quả chính:

- **298 test trước sửa và 330 test sau sửa đều pass.**
- Tái lập v4 trên **4.814 frame / 10.180 candidate**: 0 khác biệt quyết định; sau mở rộng manager, flags OFF còn được đối chiếu với đúng source snapshot cũ trên 4.814 frame, **toàn bộ output khớp**.
- Candidate filtering và memory hoạt động trong test ID reuse/crossing/occlusion; dữ liệu thật có các candidate được phân loại distractor theo chuỗi bằng chứng, có TTL và giới hạn bộ nhớ.
- Lịch sự kiện D chạy **1.402 job ReID thật**, so với 4.674 job của đường chấm mọi frame được đo riêng. Tổng outer accelerator hold quan sát được giảm từ **85,59 xuống 26,98 giây** trong replay tuần tự này.
- **Chưa đạt tiêu chí chất lượng tracking.** C HOLD nhiều hơn B trên cả ba capture; D còn bỏ nhiều frame và đứt chuỗi xác nhận. Chưa có GT độc lập để chứng minh giảm nhận nhầm. Không đưa Phase 2 vào điều khiển hoặc gọi kết quả này là cải thiện accuracy.

Đây là implementation và kết quả thực nghiệm có cả hạn chế được phát hiện, không phải bản thay thế baseline đã được nghiệm thu.

## 2. Baseline, audit và bảo toàn source

Git root `/mnt/files/Project/ptz/ptz`; branch `work/identity-phase01-20261119`; HEAD `ede31989fe6a39b04d7cd4d31675ebd811a28397`. Baseline là SHA **cộng dirty working tree**, không chỉ SHA.

Snapshot trước thay đổi: [identity_phase2_before](../../../validation/identity_phase2_before/), gồm Git status/diff, checksum 188 source/config và `source_before.tar.gz`. Không reset/clean/commit. Những sửa đổi có trước ở hybrid/overlap/GUI/scheduler được giữ nguyên.

[AUDIT.md](AUDIT.md) trả lời đủ 10 câu hỏi về OSNet, candidate, overlap, ID reuse, HOLD và v4; có bảng file → function → trách nhiệm → rủi ro từ source thực tế.

Root cause được xác nhận trong code:

1. Baseline có precheck “mọi candidate đều unsafe overlap” có thể return HOLD trước OSNet.
2. Phase 1 có overlap hard gate; ReID tốt vẫn có thể bị chặn bởi overlap hoặc geometry/time confirmation.
3. Sampling thưa làm chuỗi xác nhận bị đứt; nâng score hoặc dùng track ID cũ không giải quyết nguồn gốc vấn đề.
4. Baseline đã có `target_uid`; Phase 2 tiếp tục dùng UID và session của manager, không tạo hệ thống định danh thay thế.

Các file runtime/source được sửa hoặc thêm:

| File | Thay đổi |
|---|---|
| [ai/target_identity.py](../../ai/target_identity.py) | Thêm `Phase2Policy`, `DistractorEvidence`, phân loại candidate và ngoại lệ minor contact có ràng buộc vào manager hiện có |
| [config.py](../../config.py) | Thêm đúng bốn flag OFF cho filtering, memory, event ReID và multi-cue |
| [runtime/identity_events.py](../../runtime/identity_events.py) | Quality proxy dùng analyzer cũ, event planner/coalescing offline; không thread/model/controller mới |
| [tools/analyze_identity_live.py](../../tools/analyze_identity_live.py) | Audit v3/v4, coverage và latency ON/OFF, không mở camera |
| [tools/benchmark_identity_phase2.py](../../tools/benchmark_identity_phase2.py) | A/B/C shared evidence; D inference thật theo lịch sự kiện; tạo review pack |
| [test/test_identity_phase2.py](../../test/test_identity_phase2.py) | 28 test manager/event/quality |
| [test/test_identity_live_analysis.py](../../test/test_identity_live_analysis.py) | 4 test audit log, timeout, legacy header và trace |

[source_integrity.json](source_integrity.json) xác minh mọi source cũ khác manager/config không đổi. [changes.json](changes.json) lưu checksum trước/sau. V4 scorer, batch backend, scheduler, baseline selector, ByteTrack, OSNet và controller giữ nguyên.

## 3. Candidate filtering và association

Ba lớp diagnostic:

- `TARGET_CANDIDATE`: crop đạt quality proxy, reference similarity và margin đạt yêu cầu. Chưa phải target được xác nhận; vẫn phải qua geometry/time/history và nhiều observation của manager.
- `CONFIRMED_DISTRACTOR`: nhiều observation âm rõ, có appearance/geometry/time continuity và một target mạnh đồng thời ở vị trí tách biệt. Chỉ là kết luận của policy có thời hạn, không phải nhãn GT.
- `IDENTITY_UNCERTAIN`: thiếu/không rõ bằng chứng, crop kém, score thấp đơn lẻ, conflict hoặc chưa đủ chuỗi xác nhận.

Quality dùng lại `FocusQualityAnalyzer` với ngưỡng hiện có, kiểm tra bbox nằm trong ảnh và aspect full-body-like từ `REID_FULL_BODY_ASPECT`. Blur, bbox chạm biên, aspect không phù hợp hoặc occlusion được đánh dấu làm quality chưa biết. **Heuristic này không chứng minh người được thấy toàn thân**; vật thể che người bên trong bbox có thể chưa được phát hiện.

Không xóa distractor khỏi YOLO/ByteTrack hay tập bbox dùng kiểm tra overlap/margin. Candidate bị xác nhận distractor bị veto trong association. Nếu appearance khớp memory distractor nhưng reference score tăng, trả `DISTRACTOR_TARGET_CONFLICT`, không tự gán lại target UID. Không có tổng trọng số cố định để score bù cho gate thất bại.

Các kiểm tra giữ nguyên: acquire 0,65; keep 0,50; reacquire 0,70; margin 0,08; 3 xác nhận; max-gap 1 giây; max-age 0,8 giây. Gallery mặc định OFF và không được cập nhật từ crop ambiguous.

Multi-cue bổ sung:

- Reference và target-history appearance phải tương thích khi anchor còn tươi; motion/IoU tốt không ghi đè conflict appearance.
- IoU vẫn làm feasibility/continuity, đi cùng appearance, time và provenance. Không chọn người gần nhất để giải quyết mơ hồ.
- Minor contact chỉ có thể được giải thích khi đối thủ đã là distractor có bằng chứng, target có reference/anchor mạnh, geometry/time liên tục, hai crop đạt proxy quality và geometry thuộc ngưỡng minor-contact hiện có. Strong overlap vẫn HOLD.
- Giữ riêng `identity_confidence`, `localization_confidence`, `visibility_confidence`; không phát bbox dự đoán thành một target đã được xác nhận.

Ngoại lệ minor contact được kiểm thử có/không có memory; trong ba capture này C **không có frame chọn thêm so với B**. Chưa có bằng chứng ngoại lệ đó giải quyết được các ca crossing thực tế.

## 4. Distractor Memory

Memory nằm trong manager, không tạo UID người mới. Mỗi entry lưu Candidate bất biến gồm candidate ID, track ID, bbox, embedding, frame/time/session và quality; thêm score với reference, số observation liên tục, tối đa 8 điểm quỹ đạo và lý do phân loại. Target provenance thuộc manager có reference/UID bất biến.

Giới hạn PoC: **32 entry**, **TTL 3 giây** tính từ observation sạch hỗ trợ gần nhất. Đây là tham số thử nghiệm chưa được calibration; không phải ngưỡng nghiệm thu.

Liên kết entry bằng appearance + geometry + thời gian, không dùng track ID như khóa identity. Đổi ID nhưng vẫn cùng appearance/quỹ đạo có thể giữ evidence; dùng lại ID với appearance khác không kế thừa nhãn âm. Mỗi entry chỉ được nối với tối đa một candidate trong một frame; nếu các memory tương tự nhau mà không đủ margin thì chưa nối.

Để học negative mới cần đồng thời:

1. Frame/session/embedding/geometry của toàn bộ tập candidate hợp lệ.
2. Crop rõ theo proxy, đủ detection confidence, không occluded hoặc policy-rejected, tách khỏi bbox khác.
3. Score với target thấp hơn keep; không giống anchor target tươi.
4. Có candidate target khác đạt reacquire, margin và quality, tách biệt với mọi đối thủ.
5. Chuỗi đủ 3 quan sát; HOLD không gia hạn tuổi evidence.

Khi overlap mạnh, không dùng crop pha trộn để học thêm negative hoặc gallery target. Giữ evidence cũ trong TTL và HOLD/AMBIGUOUS; sau tách ra kiểm tra lại. Không đặt non-target vĩnh viễn. Số entry tối đa quan sát trong ba replay lần lượt **4, 3, 2**.

Một reference sai hoặc một người khác được lặp lại nhiều lần với score cao vẫn có thể làm memory học sai. Vì vậy kiểm thử logic chưa chứng minh chất lượng phân loại distractor trên người thật.

## 5. Event-Driven ReID offline

Các trigger đã có: `INITIAL_ACQUISITION`, `APPROACHING_OVERLAP`, `ACTIVE_OVERLAP`, `POST_OVERLAP_SEPARATION`, `TRACK_ID_CHANGE`, `TRACKING_UNCERTAIN`, `TARGET_REACQUISITION`, `PERIODIC_IDENTITY_CHECK`.

- Nguy cơ trước overlap dùng bbox mở rộng và dự đoán dịch chuyển từ hai quan sát metadata; không xem embedding để quyết định frame nào được inference. Chuyển động camera có thể tạo false trigger; chưa bù camera motion.
- Ưu tiên post-separation, approach/ID-change trước periodic; priority có tập giá trị hữu hạn 1–6.
- Chỉ một request chờ, chứa bản sao ảnh/bbox của đúng frame, session, generation và timestamp gốc. Frame mới không đủ quality không đẩy bỏ ảnh sạch đang chờ; mọi request vẫn hết hạn sau 0,8 giây.
- Không tạo thread. D chạy đồng bộ offline qua `ShadowScorer` v4 và `PreparedShadowBatch`, dùng shared scheduler và valid callback. V4 worker live không được sửa.
- Rate cap vẫn 0,2 giây; periodic của PoC là `min(REID_VERIFY_INTERVAL, max_gap/2)` = 0,5 giây. Đây là lịch thử, không bảo đảm luôn có ảnh sạch hoặc đạt tốc độ đó.
- Strong overlap/không có person làm mất quyền continuity trong D; việc này tạo uncertainty từ geometry, không tạo positive identity.
- Inference xử lý mọi candidate trên **frame được chọn**, để giữ competitor evidence đầy đủ. PoC chưa chọn một subset người dựa trên identity cũ; điều đó cần kiểm chứng riêng để không bỏ đối thủ chưa biết.
- Không lấy embedding của frame chưa xử lý. Kết quả sau reset cùng session, đổi session, quá tuổi hoặc không đúng thứ tự bị từ chối. Source frame ID/time được giữ khi một request cũ còn tươi được xử lý.

D hiện là bộ quan sát identity theo sự kiện. Frame không được ReID được ghi `NO_FRESH_REID_EVENT`, chưa có cơ chế phát localization-only giữa các lần kiểm tra. Không ngoại suy nhãn target/authorization để làm đẹp số frame được chọn. Đây là giới hạn quan trọng cần xử lý trước tích hợp live.

## 6. Kiểm chứng correctness và flags OFF

| Kiểm chứng | Kết quả | Bằng chứng |
|---|---|---|
| Regression v4 trước sửa | 298 pass | [before_regression.log](before_regression.log) |
| Tái lập OSNet CUDA v4 trên 5 capture | 4.814 frame / 10.180 candidate, 0 decision mismatch | [validation_result.json](validation_result.json), [script](v4_revalidate.py) |
| Manager mới flags OFF so với snapshot cũ | 4.814 frame, full output khớp | [off_parity.json](off_parity.json), [script](verify_off.py) |
| Regression sau sửa | 330 pass = 298 cũ + 32 mới | [regression.log](regression.log), [script](run_regression.py) |
| Rollback checksum dry-run | Pass, không apply | [rollback_dry_run.json](rollback_dry_run.json) |

Test mới bao phủ score thấp một lần, repeated negative, blur/occlusion/không có positive witness, crossing đổi ID, ID reuse appearance khác, mạnh overlap, minor contact có/không memory, reference/history conflict, hai người tương tự, TTL/capacity, bad session/stale/out-of-order, incomplete competitors, gallery guard, crop biên/thiếu body-like, flag dependencies, pre-overlap trigger, periodic, priority/coalescing, deadline và reset generation.

Các test embedding tổng hợp kiểm tra contract và tình huống đối kháng có đáp án biết trước; không tính chúng thành accuracy video. Mọi replay/inference/regression chạy với offline network guard; `physical_commands=0`.

## 7. Benchmark A/B/C/D

### Phương pháp

- **A:** baseline Hybrid ByteTrack + OSNet selector hiện tại; verification cache consume ở frame mẫu kế tiếp như replay cũ.
- **B:** Phase 1 manager hiện tại, không Phase 2; scorer v4 đã được chứng minh parity với cache.
- **C:** B + filtering + distractor memory + multi-cue mới.
- **D:** C + event-driven inference thật; thêm geometry-triggered interrupt khi active overlap/absence. Do đó C/D khác cả lịch evidence lẫn cách invalidation khi không chạy ReID, không phải ablation sampling thuần túy.

A/B/C dùng cùng cache có hash, cùng target/reference/model/config. Quality C lấy từ PNG được xác minh checksum. D chỉ dùng embedding do chính các request đã được chọn chạy OSNet tạo ra; không đọc cached embedding để chọn request hoặc chấm frame bỏ qua.

Đo thêm một đường all-frame v4 chạy inference thật cho mọi frame để có chi phí tham chiếu. Đây **không phải** số call hoặc occupancy của baseline A: A vốn đã có throttling và skip overlap. A/B/C trong policy replay dùng cache nên không có lượt GPU mới riêng của từng policy.

Replay dùng thời gian media; age D ghép tuổi đầu vào cache, thời gian chờ theo lịch media và thời gian scorer đo được. Không có foreground YOLO/face/RTSP thực đang cạnh tranh. Đường all-frame chạy trước D ở mỗi frame, có thể làm D hưởng warm-up; không diễn giải tail latency như A/B live có kiểm soát.

### Lựa chọn và bất đồng chính sách

| Capture | Frame | A chọn | B chọn | C chọn | D chọn | D có ReID |
|---|---:|---:|---:|---:|---:|---:|
| 17:32 — `ca0c81…` | 1.505 | 1.116 | 1.055 | 884 | 144 | 431 |
| 09:03 — `91efcb…` | 1.502 | 1.117 | 894 | 563 | 113 | 561 |
| 08:30 — `706c13…` | 1.667 | 1.513 | 1.443 | 798 | 117 | 410 |

Số chọn không phải accuracy. D chỉ xử lý lần lượt 28,64%, 37,35%, 24,60% frame; phần còn lại không được gán nhãn target mới.

**C mất lựa chọn mà B có:**

| Nguyên nhân | 17:32 | 09:03 | 08:30 |
|---|---:|---:|---:|
| Quality chưa rõ | 100 | 154 | 216 |
| Chờ xác nhận lại | 50 | 20 | 44 |
| Similarity không đủ ở trạng thái mới | 20 | 157 | 385 |
| Conflict với appearance anchor | 1 | 0 | 0 |
| Tổng | **171** | **331** | **645** |

C không có lựa chọn thêm hoặc đổi candidate so với B trên ba capture này. Quality gate gây HOLD, rồi đứt chuỗi giữ target và tăng yêu cầu bắt lại; đây là tác động dây chuyền chứ không phải mọi trường hợp đều do score mới xấu hơn.

Để không đánh đồng D skip với sai identity, đối chiếu C/D chỉ tại frame D thực sự xử lý:

| Capture | C/D giống lựa chọn hoặc cùng HOLD | Chỉ C chọn | Chỉ D chọn | Cả hai chọn khác candidate |
|---|---:|---:|---:|---:|
| 17:32 | 311 | 107 | 13 | 0 |
| 09:03 | 458 | 100 | 3 | 0 |
| 08:30 | 321 | 80 | 9 | 0 |

Không có different-candidate không chứng minh không có ID switch; cả hai có thể cùng chọn sai hoặc cùng HOLD.

### Chi phí inference và sampling

| Capture | All-frame jobs / minibatches | D jobs / minibatches | All-frame hold | D hold | D gap P95 / max | D gap >1s |
|---|---:|---:|---:|---:|---:|---:|
| 17:32 | 1.505 / 1.834 | 431 / 543 | 25,16 s | 7,39 s | 0,664 / 2,731 s | 10 |
| 09:03 | 1.502 / 2.643 | 561 / 1.001 | 36,21 s | 13,71 s | 0,570 / 3,899 s | 9 |
| 08:30 | 1.667 / 1.749 | 410 / 427 | 24,22 s | 5,89 s | 0,667 / 7,946 s | 5 |

Đây là **outer scheduler hold đo bằng CPU wall**, không phải GPU kernel time hoặc GPU utilization %. Một job có thể chứa nhiều minibatch raw/normalized. D không tiết kiệm chi phí trên mỗi job một cách nhất quán; phần giảm chủ yếu do bỏ nhiều job/frame.

D comparison/media-second lần lượt **2,76; 3,30; 2,74**. Age P95 **241; 263; 211 ms**, max **389; 454; 349 ms**; 0 timeout trong replay tuần tự. Không suy ra các rate/age này sẽ đạt được trên camera có tranh chấp inference.

Event planning P95 khoảng **0,29–0,44 ms/frame**. Chi phí pure PTZ `plan_pan` được đo riêng cho A/B/C/D, P95 khoảng **0,015–0,024 ms**. Có một outlier cold import khoảng 108,6 ms ở A đầu tiên; giữ trong JSON, không gọi là độ trễ motor. Các số trên không bao gồm truyền lệnh, phản hồi camera hoặc live controller queue.

### Metric có thể và chưa thể kết luận

Đã có: số invocation/minibatch thật, scheduler hold, wall time, policy disagreement, state duration, gap/age, chi phí association/planner offline, classification counts và memory bound. `ambiguous_state_duration_s` là tích phân state phần mềm; các state A khác B/C, D còn chứa no-event, nên không dùng trực tiếp như thời gian mất target thật.

**Chưa có GT:** correct/wrong-target time, ID switch, fragmentation, true-target loss, recovery latency, false rejection true target và false acceptance distractor. Các giá trị đó để `null`, không dùng pseudo-label baseline thay thế. **Chưa có live Phase 2:** detection FPS và PTZ end-to-end decision latency để `null`.

Nguồn: [comparison_summary.json](comparison_summary.json), [benchmark đầy đủ](benchmark_final/summary.json), [capture 17:32](benchmark_final/capture_1/report.json), [09:03](benchmark_final/capture_2/report.json), [08:30](benchmark_final/capture_3/report.json).

## 8. Failure cases cụ thể và review

[Ảnh chẩn đoán](failure_cases.jpg), [bằng chứng score/quality/anchor](failure_evidence.json), [gap và state durations](failure_details.json).

| Trường hợp | Quan sát và hệ quả |
|---|---|
| 17:32, frame 1344 | Score reference 0,657; similarity với anchor 0,935; bbox chạm biên nên C trả quality uncertain. Kiểm tra này có thể quá chặt cho duy trì target đã biết. |
| 17:32, frame 1354 | Reference 0,722, quality tốt, nhưng anchor similarity 0,69933 thấp hơn gate 0,70; C HOLD `APPEARANCE_HISTORY_CONFLICT`. Một khác biệt gần ngưỡng đủ phá continuity. Chưa sửa ngưỡng từ một ví dụ. |
| 09:03, frame 2639 | Reference 0,716 và anchor 0,938, nhưng bbox biên bị chặn; các người còn lại vẫn được giữ và phân loại trong audit. |
| 09:03, frame 2674 | Sau gián đoạn, reference 0,635 không đủ reacquire dù anchor 0,762; minh họa tác động dây chuyền của quality HOLD. |
| 08:30, frame 1439 | Reference 0,801 và anchor 0,964 nhưng bbox biên vẫn HOLD; cần tách điều kiện học identity mới khỏi điều kiện duy trì localization từ bằng chứng có hạn. |
| 09:03, frame 6058 | Top score chỉ khoảng 0,468 và thuộc distractor memory; C veto. B cũng có thể HOLD vì score thấp, nên không tính đây là một lần “đã cứu nhận nhầm”. |
| D, 08:30 frame 1846→2084 | Khoảng không có comparison 7,946 giây. Event/quality gating chưa bảo đảm continuity; periodic còn tồn tại nhưng không tạo ra crop sạch khi dữ liệu không đạt điều kiện. |

Đã xem ảnh reference, overview và các ảnh trên. Các hình cho thấy rõ ca người áo đen/quần nâu chạm biên hoặc xoay người bị HOLD và cảnh có nhiều người cạnh tranh. Đây là review chẩn đoán giới hạn, **không phải GT độc lập của toàn clip** hay chứng nhận mọi distractor label đúng. Chưa có false association được xác nhận/định lượng bằng GT; không ghi giá trị bằng 0.

Đã chuẩn bị **136 frame review**: 46 + 45 + 45, gồm lấy mẫu đại diện, overlap, similarity thấp, disagreement và easy selection. Ảnh chỉ có candidate ID/bbox, template để trống identity/visibility và ghi `UNREVIEWED_TEMPLATE`:

- [Review 17:32](benchmark_final/capture_1/review/)
- [Review 09:03](benchmark_final/capture_2/review/)
- [Review 08:30](benchmark_final/capture_3/review/)

Tập này lấy mẫu phân tầng, không tự đại diện cho tỷ lệ lỗi toàn dân số; cần reviewer độc lập xác nhận, thêm đoạn liên tiếp để đo recovery và giữ riêng capture holdout chưa dùng phát triển. Không đổi `source=manual` trước khi review thực sự.

## 9. Live v4 và hướng dẫn chạy

[live_status.json](live_status.json) kiểm tra ba phiên shadow có sẵn, chưa có log v4 batch thật. Công cụ đã được thử trên log v3 mới nhất và tái hiện **259 comparison, 44 gap >1 giây, gap P95 1,967 s/max 4,637 s**: [live_v3_reference.json](live_v3_reference.json).

Trace v3 chỉ còn 42,17 giây ON và 16,94 giây OFF sau; không còn OFF trước. Tool báo thiếu coverage thay vì bịa baseline window. Detection output quan sát 9,44/s ON và 11,28/s OFF trong các khoảng đó; cảnh khác nhau nên không suy ra quan hệ nhân quả hoặc hiệu năng v4.

[LIVE_V4.md](LIVE_V4.md) chứa lệnh chạy, quy trình 20s OFF → 60s ON → 20s OFF và lệnh phân tích. Người dùng vận hành camera bằng `PTZ_SHADOW_BATCHED=1 python3 ptz/main.py`; Phase 2 vẫn OFF. Không cần quay lại để cứu các capture đã có, không cần gán nhãn lại cho phép đo throughput này.

## 10. Tái lập và rollback

Từ Git root, dùng môi trường `py`, output benchmark mới để tránh ghi đè bằng chứng:

```bash
PYTHONPATH=.:test python3 docs/identity_phase2/run_regression.py

PYTHONPATH=. python3 tools/benchmark_identity_phase2.py \
  --cache docs/identity_shadow_audit_20261119_173204/capture_cache \
  --cache docs/identity_shadow_v3/capture_cache \
  --cache docs/identity_capture_audit_20261119_083019/capture_706c13123cb84c2294eecf5fa4c3850f/cache \
  --output /tmp/identity_phase2_new_run
```

`v4_revalidate.py` là bản sao validator v4, lưu kết quả riêng trong thư mục Phase 2 để không sửa bằng chứng gốc. `benchmark_final/` là lần chạy hoàn tất dùng cho báo cáo. `benchmark/` là lượt pilot đã dừng trước khi chốt quality body-like và result-generation guard; giữ lại để minh bạch, không gộp vào kết quả cuối.

Feature rollback: giữ bốn flag Phase 2 OFF và không truyền `Phase2Policy` bật vào manager. Các flag này hiện chỉ được harness offline sử dụng; không có đường bật Phase 2 motor/GUI. `IDENTITY_MEMORY_ENABLED` và gallery vẫn OFF.

Full code rollback, khi ứng dụng đã đóng, có checksum và mặc định dry-run:

```bash
python3 docs/identity_phase2/rollback.py
# Chỉ dùng khi muốn gỡ code Phase 2:
python3 docs/identity_phase2/rollback.py --apply
```

Rollback phục hồi manager/config đúng snapshot trước nhiệm vụ và chuyển 5 source/test mới vào quarantine, giữ docs/log/cache và mọi dirty change trước đó. Nếu file bị sửa thêm, tool từ chối ghi đè. Đã chạy dry-run, không apply. Sau rollback, script phân tích Phase 2 trong docs có thể cần các module đã gỡ; artifacts kết quả vẫn được giữ.

## 11. Hướng tiếp theo dựa trên kết quả

1. **Không bật Phase 2 live hoặc điều khiển.** V4 live có thể được đo riêng theo hướng dẫn, trong khi Phase 2 tiếp tục offline.
2. **Ưu tiên phân biệt quality cho học identity với quality cho duy trì vị trí.** Các ca 1344/2639/1439 cho thấy gate bbox biên gây HOLD dù appearance với anchor mạnh. Cần cơ chế localization-only có tuổi và uncertainty rõ ràng; không nới score hoặc tự gia hạn evidence cũ.
3. **Review độc lập các ca crossing và veto trước khi sửa association.** Memory đã có contract nhưng chưa có chứng cứ giảm nhận nhầm thật; không tối ưu theo số target được chọn.
4. **Sửa event scheduling theo coverage của confirmation.** So sánh các chuỗi approach → overlap → separation có ảnh sạch; cân nhắc burst xác nhận có giới hạn và tách uncertainty từ geometry khỏi mất identity. Giữ deadline, periodic check và foreground priority.
5. **Thêm holdout có crossing/ID reuse rõ**, gồm đối thủ giống target và đoạn pan/zoom. Sau khi có GT mới báo cáo false acceptance/rejection, ID switch và recovery theo người thật.

Tiêu chí quan trọng nhất vẫn chưa được chứng minh: giảm bám nhầm trong crossing mà không tăng đáng kể thời gian mất target hoặc độ trễ. Kết quả hiện tại cung cấp implementation có thể kiểm thử và những nguyên nhân thất bại cụ thể để quyết định bước tiếp theo, thay vì mặc định Phase 2 tốt hơn baseline.
