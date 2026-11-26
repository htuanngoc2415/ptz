# Sửa continuity của AI PTZ — 11/11/2026

## Bằng chứng từ source đang có

- `HybridReIDTargetSelector` là nơi chọn person và quyết định identity; AI worker là chủ sở hữu state. Trước sửa, mọi nhánh `due`, kể cả periodic verification, đều đặt `crossing_hold=True`, `TEMP_LOST`. Worker cuối frame ánh xạ hold thành `OCCLUDED_HOLD`. Đây là nguyên nhân code đã xác nhận của dao động hai state; chưa chứng minh mọi lần mất trong log lịch sử đều cùng nguyên nhân.
- Worker publish `TargetSession(None)` khi crossing hold. Motor worker kiểm tra token và tuổi observation; vì vậy verification trước đây thực sự có thể thu hồi quyền PTZ. Face worker không được tự quyết định đổi person identity.
- `REID_KEEP_THRESHOLD=.50` có dùng trong base selector nhưng hybrid trước sửa không dùng nó. Hybrid trước đây dùng ngưỡng acquisition cho periodic verification và có thể chọn top1 khác với current track.
- Detector được dựng trong `app/application.py` với `BYTE_TRACK_LOW_THRESHOLD=.10`, không phải `.35`. `.35` là high/new-track và acquisition eligibility. Adapter đã truyền đúng original detection index qua high/low partitions. Lỗi còn lại là hybrid loại low-score track khỏi nhánh continuity.
- Đã đọc API cài đặt: Ultralytics **8.4.25**. `BYTETracker.update(results, img=None, feats=None)` có low-score association, không tạo track mới từ low-score partition. Adapter không truyền ảnh và không tạo GMC, nên baseline không có camera motion compensation.
- `track_buffer=90`, `frame_rate=15` cho `max_time_lost=45` update. Ở 9.39 update/s, khoảng này xấp xỉ 4.79 s, không phải 3 s. Selector có grace theo monotonic timestamp riêng; không tự thay FPS bằng số liệu phiên cũ.
- Gallery 5 ảnh chỉ thay ánh sáng. Chúng không thêm thông tin quay lưng/quay nghiêng. Không có dữ liệu gán nhãn đa góc nhìn để kết luận recognition đã cải thiện.
- HANET khai báo không có lens telemetry. Motor ownership độc quyền, manual override, STOP/settle và freshness ở wire boundary đã có. ACK không chứng minh motor đã di chuyển đúng.

## Thay đổi thực hiện

1. Stable ByteTrack ID, motion liên tục và không overlap: cập nhật bbox ngay. Periodic OSNet chạy trong một thread nền; tối đa một job đang tồn tại, không xếp hàng frame vô hạn. Ảnh và observations được sao chép; scorer dùng snapshot model/gallery. Chỉ AI thread áp dụng kết quả vào selector.
2. Job gắn epoch, lock session, tracker ID, frame ID và monotonic receive timestamp. Epoch đổi khi reset, hold hoặc rebind. Kết quả sai token/quá hạn bị bỏ, không tạo negative evidence. Capture timestamp được ghi `null` vì camera chưa cung cấp nó; không đánh đồng receive time với capture time.
3. Stable tracking dùng keep threshold `.50`. Một negative giữ bbox và identity `VERIFYING`; ba negative liên tiếp đưa vào `UNCERTAIN`. Positive reset counter. Cosine không được diễn giải là xác suất.
4. Overlap được kiểm tra quanh bbox hiện tại; missing/jump/ambiguous margin thu hồi quyền ngay. Recovery giữ ngưỡng `.70`, margin `.08`, ba observation liên tục với IoU xác nhận. Không rebind chỉ vì IoU hoặc top1 trong một frame. Stable periodic verification không chọn người khác.
5. `target_uid` độc lập ByteTrack ID, giữ qua recovery cùng reference. Khi rebind, lock session/generation vẫn xoay để vô hiệu hóa face/motor job cũ. Verification định kỳ không xoay phiên khóa.
6. Low-confidence detection được dùng để duy trì đúng current track; không acquire/reacquire từ nó. Không đổi ngưỡng YOLO, high/new-track, acquire `.65` hay reacquire `.70`.
7. Kiểm tra tuổi frame lần nữa ngay sau synchronous acquisition/recovery, trước publish authorization và submit pan/zoom đầu tiên. Frame quá hạn thu hồi token, cancel pan và request STOP zoom.
8. GUI giữ các state cũ; snapshot bổ sung target UID, tracking status, identity status. Face miss không xóa person lock. Giữ nguyên SCRFD/ArcFace và tỷ lệ person `.40`, face `.25`, face hold `.22–.28`.
9. STOP trong zoom dispatcher giữ binding hiện tại khi observation không có metadata; tránh xóa session làm mất phạm vi kiểm tra body-edge. Không đổi ưu tiên motor hay thời gian settle.
10. Telemetry bổ sung verification-only count, stale ReID count, tracker rebind count, kết quả verification và lý do hold. Transition có frame/session/UID, similarity, margin và tuổi last-seen/frame. Bộ đếm tracker rebind là thay đổi binding phần mềm, không phải ID-switch có ground truth. Lost counter chỉ đi qua lifecycle mất target thật, không đếm periodic verification.

## Config mới và giới hạn

| Config | Mặc định | Cơ sở |
|---|---:|---|
| `REID_NEGATIVE_CONFIRMATIONS` | 3 | Hysteresis qua ba mẫu verification; chịu được một crop/góc nhìn xấu. Cần hiệu chỉnh bằng replay. |
| `REID_VERIFY_RESULT_MAX_AGE` | 2.0 s | Không áp dụng appearance quá cũ; độc lập với ngưỡng freshness PTZ chặt hơn. |
| `REID_IDENTITY_MAX_AGE` | 5.0 s | Giới hạn continuation khi verifier lỗi/hung/không có positive mới. Không extrapolate bbox. |

Các giá trị là baseline an toàn cho kiểm thử, chưa phải tối ưu đo trên camera. ReID nền vẫn tranh GPU lane với detector: nó bỏ blocking trên AI thread nhưng không bảo đảm latency GPU thấp hơn. Acquisition và recovery vẫn đồng bộ, có freshness gate sau inference. Một native inference bị treo không thể bị Python cưỡng bức hủy; một job duy nhất giới hạn bộ nhớ và identity timeout chặn tiếp tục điều khiển vô hạn.

## P1/P2 chưa triển khai khi thiếu benchmark

- Giữ ByteTrack baseline. API cài đặt có hook GMC khi `self.gmc` tồn tại và truyền `img`; BoT-SORT có constructor `(args, frame_rate=30)`. Chưa thay tracker hoặc bật GMC chỉ dựa vào API. Thử nghiệm tiếp theo cần adapter giữ original indexes, ảnh cùng frame, reset GMC khi stream gap, rồi replay cùng detection timestamps và ground truth qua baseline/GMC. So sánh correct-target continuity, ID switches, wrong-target switches, latency P95, scale-change và crossing; không lấy số binding changes thay ground-truth ID switches.
- Chưa thêm online gallery: cần reference front/side/back thật, nhãn identity và chất lượng crop. Existing `REID_SCORE`/`TARGET_VERIFICATION` ghi raw/normalized score, quality và margin để phân tích; không đưa embedding chưa xác minh vào gallery.
- Chưa đổi motor arbitration để ép zoom chen vào pan. Deadzone/hysteresis và release/settle hiện có; ép preempt trong lúc target sát biên có thể gây cắt người hoặc xung đột STOP. Cần replay geometry/motor trace sau P0 để phân biệt pan cần thiết với starvation. Test simulator xác nhận ownership/manual/STOP và edge guard; chưa chứng minh hết starvation trên HANET.
- Không chạy camera, không đo lại detection FPS, ReID P95, false-positive rate thực tế hay correct-target continuity. Không tuyên bố đã loại hết mất bám khi quay lưng.

## Kiểm thử

`test/test_hybrid_tracking.py` bổ sung/đổi assertion cho periodic không mất quyền, single/repeated negative, keep threshold, job chưa xong, stale frame/session, ambiguity, weak tracking/acquisition, full loss/reacquisition và logical UID. Suite hiện có kiểm tra face miss, face association, initial zoom, manual override, ownership, STOP failure, watchdog, RTSP disconnect/backoff và GUI/model initialization qua mock.

Chạy bằng môi trường Conda `py`, không dùng entrypoint camera:

```sh
/home/admin1/miniconda3/envs/py/bin/python tools/run_unit_tests.py
```

Runner của dự án thử bổ sung system `gi` sau kiểm tra ABI; môi trường hiện tại vẫn không có `gi` tương thích Python 3.10, nên hai test GStreamer không chạy được. Đây là lỗi phụ thuộc môi trường, không phải lỗi network.

Lỗi không ổn định trong test body-edge đã được truy ra: STOP không metadata xóa `_correlation`. Sửa dispatcher giữ binding; test chờ dispatcher xong rồi kiểm tra session và edge hold. Bốn fixture Dahua dùng bbox `(0,0,100,200)` bị edge guard chặn trước nhánh telemetry; chuyển vào giữa frame, giữ kích thước 100×200 để kiểm tra đúng mục đích.

Kết quả cuối được lưu cạnh tài liệu này. GUI được kiểm tra headless/mock, chưa kiểm tra trực quan bằng desktop thật.

| Yêu cầu | Kiểm chứng tự động |
|---|---|
| 1 Periodic | `test_periodic_and_single_negative_keep_tracking`, `test_inflight_verification_does_not_block_motion` |
| 2 Một negative | Test single/repeated negative và positive reset counter |
| 3 Đổi ID | Test new ID/rotate session; stale-session test assert cùng target UID |
| 4 Crossing | Crossing same-ID và ambiguous background evidence thu hồi quyền |
| 5 Quay lưng/không face | Mock face miss giữ body; chưa kiểm chứng khả năng OSNet nhận diện lưng thật |
| 6 Late ReID | Token phiên cũ, result quá hạn, frame out-of-order |
| 7 Low confidence | ByteTrack giữ ID với .2; .2 không tạo track mới/không acquire identity |
| 8 Biến mất | Lost grace, bounded verification timeout, stale-body motor watchdog |
| 9 Motor | Pan/zoom ownership, manual preemption, STOP failure, settle/edge tests |
| 10 RTSP | OpenCV connection failure clears mailbox; dual-stream disconnect; bounded pulse khi không có frame mới. GStreamer: thiếu `gi`. |
| 11 Reacquire | Reject wrong score rồi reacquire .95, giữ UID; test worker yêu cầu xác nhận lại |
| 12 GUI compatibility | Headless init/target replacement/state snapshots; face, manual và zoom mock regressions |
