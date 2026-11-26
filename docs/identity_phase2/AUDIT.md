# Audit source trước Phase 2

Ngày 19/11/2026. Git root `/mnt/files/Project/ptz/ptz`, nhánh `work/identity-phase01-20261119`, HEAD `ede31989fe6a39b04d7cd4d31675ebd811a28397`. Working tree có thay đổi trước nhiệm vụ; snapshot lưu ở `../../../validation/identity_phase2_before/`, gồm Git status/diff, 188 source/config, checksum và bản đóng gói. Không reset/clean/commit.

## Trả lời 10 câu hỏi từ source

1. **OSNet được gọi ở đâu/khi nào?** `person_search/reid.py:OSNetReID.extract_embeddings` là backend. `ReIDTargetSelector._score_batch` gọi cho raw crops, có thêm lượt normalized khi luma/contrast thấp. `HybridReIDTargetSelector._select_person` gọi lúc acquire/reacquire và `_start_verification` gọi định kỳ khi LOCKED. Trong nhánh mọi candidate đều overlap không an toàn, selector có thể return HOLD trước OSNet. Shadow dùng `ShadowScorer.score`, chạy lại appearance cho frame shadow nhận được.
2. **Bbox nào vào OSNet?** Bbox từ person dict, qua `ReIDTargetSelector.crop` cắt theo biên ảnh. Baseline acquisition/recovery lấy người đủ detection confidence; locked verification lấy current target và tập ứng viên liên quan nếu `REID_CANDIDATE_AWARE` bật. Không dùng bbox dự đoán như một ảnh quan sát mới.
3. **Tất cả candidate hay chỉ target?** Không có một câu trả lời chung. Acquire/reacquire chấm mọi candidate đủ điều kiện của nhánh đó. Verification candidate-aware chỉ lấy target, các box giao target hoặc gần box trước theo IoU. Shadow v4 chấm toàn bộ person được submit trên frame mẫu; tập này là đầu ra tracker, không nhất thiết là mọi detection YOLO. Replay cache tính bằng chứng cho mọi candidate đã ghi.
4. **Similarity thấp được xử lý thế nào?** Acquisition không đủ threshold/margin thì không lock. Locked verification tăng negative count; đủ số lần mới HOLD `LOW_REID_SCORE`. Phase 1 trả HOLD `LOW_REFERENCE_SIMILARITY`. Trước Phase 2 chưa có confirmed-negative identity memory: `distractor_history` chỉ là `UNVERIFIED_ALTERNATIVE`.
5. **Reject có xóa người khỏi tracker không?** Không. ByteTrack cập nhật trước selector; reject chỉ tác động lựa chọn/quyền bám. MOT có thể tự retire track theo luật motion/lifetime của nó, không phải do selector gán score thấp.
6. **Overlap bị gate ở đâu?** `HybridReIDTargetSelector._candidate_overlap` gọi `overlap_geometry`, `minor_contact` và `OverlapMemory.assess` khi phù hợp. Có precheck chặn mọi candidate; có check current target; có check ứng viên recovery. Phase 1 `observe` có hard gate overlap-min-area với mọi box khác. Phase 2 chỉ có ngoại lệ hẹp cho minor contact đã có distractor đáng tin, không bỏ strong-overlap gate.
7. **Đổi hoặc tái sử dụng ID được bảo vệ ra sao?** Baseline đã có `target_uid`, còn `track_id` là binding localization/authorization. Verification có token epoch/session/track và age. Rebound xoay `track_generation` và `lock_session_id`, giữ target UID. Phase 1 kiểm tra source frame/session/time, appearance và nhiều xác nhận, không coi ID bằng nhau là đủ. Baseline vẫn có cửa sổ duy trì cùng ID dựa vào positive verification trước đó: không thể tuyên bố ID reuse luôn bị phát hiện ngay.
8. **LOCKED gặp người đi ngang khi nào HOLD?** Current track thiếu, unsafe overlap, IoU jump, verification quá tuổi, margin thấp với competitor hoặc đủ negative verifications. OverlapMemory còn kiểm tra merge/split, độ che target, đối thủ gần vị trí cũ, continuity và thời gian overlap chưa được xác minh.
9. **ReID tốt vẫn có thể bị chặn?** Có. Acquisition/recovery phải qua `acquisition_allowed`/overlap; Phase 1 rank bằng appearance nhưng top candidate vẫn phải qua reason gate và geometry/time confirmation. Capture 09:03 trước đây có 196 frame baseline-only do overlap, 92 frame thuộc điều kiện minor contact của baseline; đây là khác biệt policy, không phải OSNet không được nạp hoặc luôn nhận sai.
10. **Tái sử dụng gì từ v4?** `ShadowScorer` + `PreparedShadowBatch`, preprocessing CPU trước admission, shared OSNet eval/weights, raw-normalized fusion, minibatch bounded, giữ đúng phần đã hoàn tất của cùng job, `opportunistic_inference`, foreground priority và valid callback. Live `IdentityShadow` có một worker/job, rate cap 5 Hz, deadline từ receive gốc 0,8 giây, token reset/session, commit draft khi còn hợp lệ. Phase 2 offline dùng lại scorer/scheduler; không sửa hoặc tạo thêm thread live.

## Mapping file → function → trách nhiệm → rủi ro

| File | Function/class | Trách nhiệm | Rủi ro/giới hạn cần kiểm chứng |
|---|---|---|---|
| [person_search/reid.py](../../person_search/reid.py) | `OSNetReID.preprocess/extract_embeddings` | Backend 512-d normalized | Cosine không phải xác suất; crop pha trộn vẫn có thể tạo embedding hợp lệ |
| [ai/reid_target.py](../../ai/reid_target.py) | `crop/_score_batch/select_person` | Crop, quality, raw/normalized scoring, acquisition | Tập candidate phụ thuộc caller; full-body visibility chưa được chứng minh bằng bbox |
| [ai/hybrid_reid.py](../../ai/hybrid_reid.py) | `_select_person/_start_verification/_take_verification` | Continuity, async verification, reacquire | Geometry có thể chặn OSNet; evidence định kỳ không bảo đảm nhận ngay ID reuse |
| [ai/overlap_policy.py](../../ai/overlap_policy.py) | `minor_contact/OverlapMemory.assess` | Phân biệt tiếp xúc nhẹ và overlap nguy hiểm | Ngưỡng chưa là bộ phân loại identity; same track + IoU không chứng minh đúng người |
| [ai/target_identity.py](../../ai/target_identity.py) | `TargetIdentityManager.observe/interrupt` | Identity memory, xác nhận, provenance, rejection | Chuỗi mẫu thưa phá confirmation; quality/negative memory mới cần đánh giá độc lập |
| [runtime/byte_tracking.py](../../runtime/byte_tracking.py) | `ByteTrackPersonTracker.update` | Motion association, map detection/track | Track ID có thể đổi hoặc được tái sử dụng; không phải danh tính người thật |
| [runtime/identity_shadow.py](../../runtime/identity_shadow.py) | `ShadowScorer/IdentityShadow.submit/_run` | Shared scorer và worker diagnostic | Tranh chấp inference; max 5 Hz không phải rate đạt được |
| [runtime/identity_shadow_batch.py](../../runtime/identity_shadow_batch.py) | `PreparedShadowBatch.run` | Batch raw/normalized ngoài/vào accelerator đúng lúc | Một minibatch đang chạy không preempt; tail latency chưa đồng đều |
| [runtime/scheduling.py](../../runtime/scheduling.py) | `InferenceScheduler.use` | Một accelerator lane, foreground priority | Background không được admission vẫn mất cơ hội lấy mẫu |
| [runtime/identity_recording.py](../../runtime/identity_recording.py) | `IdentityCapture.record/automatic_labels` | Ghi ảnh AI, metadata, pseudo-label | Không phải full-rate main video hay GT; 0 recorder drop không xác nhận RTSP không mất frame |
| [tools/identity_replay.py](../../tools/identity_replay.py) | `replay_cache/offline_guard/dry_pan` | Shared-evidence policy replay, chặn network, pure planner | Verification baseline consume ở mẫu kế tiếp; không tái hiện scheduler live |
| [tools/identity_metrics.py](../../tools/identity_metrics.py) | `evaluate` | Metric theo nhãn identity độc lập | Thiếu GT thì để null; khoảng chưa gán nhãn không được ngoại suy |
| [runtime/inference_worker.py](../../runtime/inference_worker.py) | `ai_worker/consume_identity` | Pipeline owner, freshness và session | Đầu ra selector là proposal, còn gate trước publish/control; không bỏ gate |
| [control/target_state_machine.py](../../control/target_state_machine.py) | `TargetStateMachine.update` | Framing/face/zoom states | Đây không phải state identity; không sửa trong Phase 2 |
| [control/auto_pan_tilt.py](../../control/auto_pan_tilt.py) | `submit/validate_intent` | Session, age, manual override và motor admission | Không nhận đầu ra Phase 2; giữ nguyên STOP/fault/cooldown |
| [runtime/identity_events.py](../../runtime/identity_events.py) | `EventReID.offer/take/valid_result` | PoC lịch sự kiện offline, một pending request | Ước lượng nguy cơ từ geometry chỉ tạo trigger; không xác nhận identity |

## Root cause và phạm vi suy luận

- **HOLD do policy:** gate overlap có thể ưu tiên an toàn dù appearance đủ tốt. Thay gate bằng “score cao thì chọn” có nguy cơ bám người sai trong crop pha trộn.
- **HOLD do sampling:** gap >1 giây phá chuỗi confirmation dù từng sample chưa quá tuổi 0,8 giây. Event-driven cần đo cả coverage và latency, không chỉ giảm số call.
- **HOLD do appearance/quality:** góc quay, kích thước, blur, che khuất, bbox không toàn thân có thể làm score giảm. Một score thấp không đủ xác nhận distractor.
- **Không có GT độc lập đầy đủ:** chưa phân biệt mọi HOLD cần thiết với false rejection, cũng chưa chứng minh mọi lựa chọn baseline đúng. Pseudo-label chỉ ghi lại quyết định baseline.

## Ranh giới thay đổi

Giữ nguyên baseline selector, ByteTrack, OSNet weights/backend, overlap policy baseline, v4 scorer/scheduler/worker, recorder và toàn bộ controller. Mở rộng manager hiện có; thêm event planner và công cụ offline vì chưa có thành phần tương đương đáp ứng provenance/negative-memory và benchmark D. Không tạo target UID mới thay thế kiến trúc đang dùng.
