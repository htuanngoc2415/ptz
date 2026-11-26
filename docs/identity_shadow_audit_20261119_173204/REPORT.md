# Phân tích phiên 17:32:04 ngày 19/11/2026

**Bản ghi hợp lệ, hiển thị ổn định trong số đo; shadow v3 đã khắc phục việc gần như không thu được mẫu của v2. Tuy nhiên, mẫu shadow còn thưa và không đều, làm gián đoạn xác nhận identity. Chưa nên dùng kết quả live này để kết luận độ chính xác của Phase 1 hoặc bật Phase 1 điều khiển camera.** Dữ liệu hiện có đủ cho bước xử lý tiếp; chưa cần quay thêm.

## Nguồn và tính toàn vẹn

- App log: `logs/ptz_20261119_173204.log` và `.jsonl`; ứng dụng kết thúc bình thường lúc 17:35:34.
- Capture: `logs/identity_capture/capture_ca0c81d0b16441a8b0c7b48b7b0a698f`.
- Shadow: `logs/identity_shadow/shadow_d3b836c5ae5c492ba7ddb95282250359.jsonl`.
- Latency: `logs/latency/latency_summary_20261119_173207_536257.csv` và trace cùng prefix.

Đã xác minh checksum metadata/reference, từng PNG, decode/shape, thứ tự frame/timestamp: **1505 frame, 155,94 giây**, finalized; recorder báo **0 dropped, 0 errors**. Đây là frame AI lấy mẫu, không phải video main liên tục; không suy ra RTSP không rơi frame trước recorder.

259 comparison shadow; **255 có ảnh đối chiếu và khớp frame ID/timestamp/baseline proposal**, không mismatch. Bốn frame 959/967/977/5662 nằm ngoài khoảng record 981–5659, đều có quyết định LOW_REFERENCE_SIMILARITY. Sự lệch biên Start/Stop này không phải lỗi lưu ảnh. Hash manager/shadow/scheduler trong SHADOW_POLICY khớp source v3 hiện tại. Source baseline và model weights được kiểm tra khi tạo cache OSNet.

Một session qua **5 reset**, ngưỡng bắt lại **0,70**. Khác phiên v2 trước, lần này có accepted observations trước và sau reset; session không bị tạo mới. Mọi comparison giữ `ptz_authorized=false`, `physical_commands=0`.

## Hiển thị và tải xử lý

Toàn phiên, 2617 mẫu receive→GUI-submit: median **59,11 ms**, P95 **74,47 ms**, max **133,76 ms**. Phù hợp với quan sát của người dùng rằng không giật lag. Đây là từ lúc nhận frame đến Tk image configure, không đo glass-to-glass, độ trễ trước decode hoặc bảo đảm đồng bộ main/sub.

Trace đã rotation, chỉ giữ 17:34:35–17:35:34: khoảng 42,17 giây cuối ON và 16,95 giây OFF sau đó. Trong phần này:

| Chỉ số quan sát | Shadow ON | OFF sau đó |
|---|---:|---:|
| Tần suất cập nhật GUI | 12,76/s | 12,74/s |
| P95 receive→GUI | 74,25 ms | 72,98 ms |
| Kết quả detector xuất ra | 9,44/s | 11,27/s |

GUI ổn định nhưng vẫn có tranh chấp inference. Cảnh, state và workload thay đổi giữa các cửa sổ; không quy toàn bộ chênh lệch detector cho shadow và không gọi đây là benchmark FPS có kiểm soát. Hai cửa sổ sát nhau 15 giây cũng cho detector 9,27/s ON và 11,20/s OFF, GUI 12,60/s và 12,87/s. Chi tiết: [detail.json](detail.json).

## Shadow v3: lỗi bỏ dở đã giảm, sampling vẫn là nút thắt

- **259 comparison / 157,47 giây = 1,64 mẫu/s**, so với v2 4 / 168,81 giây. Hai phiên khác cảnh nên chỉ dùng để xác nhận khả năng hoàn tất job, không đo tốc độ cải thiện tuyệt đối.
- **343 job được nhận**, 259 hoàn tất, 84 bị loại: 79 quá tuổi 0,8 giây, 4 bị superseded, 1 do Stop. Tỷ lệ hoàn tất 75,51%.
- 1177 submit bị bỏ: 1124 WORKER_BUSY, 53 RATE_LIMIT. Đây không phải số frame camera/recorder mất.
- 9236 lần thử admission; 565 lượt extraction hoàn tất, 60 thuộc job không xuất comparison (10,62%). Phần còn lại 505 pass khớp tổng completed_passes của 259 comparison. Resource telemetry chứa cả nested acquisition; không cộng nó thành GPU time độc lập.
- Tuổi mẫu hợp lệ median **382 ms**, P95 **704 ms**, max **787 ms**; không có comparison vượt deadline 800 ms.
- Khoảng giữa hai mẫu median **0,366 giây**, P95 **1,967 giây**, max **4,637 giây**. Có **44 khoảng vượt 1 giây**, trong khi confirmation max-gap là 1 giây.

## Quyết định live cùng frame

| Kết quả | Số mẫu |
|---|---:|
| Cùng chọn một candidate | 10 |
| Cùng HOLD | 162 |
| Baseline chọn, shadow HOLD | 87 |
| Shadow chọn, baseline HOLD | 0 |
| Cả hai chọn nhưng khác candidate | 0 |

87 baseline-only gồm **46 similarity thấp, 37 chờ đủ xác nhận, 3 overlap, 1 detection confidence thấp**. Trên toàn bộ 259 mẫu, số HOLD do similarity thấp là 154; chờ xác nhận 54; overlap 39; confidence thấp 2.

Đã xem cả 10 mẫu shadow chọn trong [all_selected.jpg](all_selected.jpg): các box xanh đều bao quanh người mặc áo đen, quần nâu ở cảnh quay, không bao người mặc áo đỏ. Đã xem 12 mẫu phân bố dọc nhóm baseline-only trong [baseline_only.jpg](baseline_only.jpg). Đây là kiểm tra hình ảnh giới hạn, không phải gán GT toàn clip hay chứng nhận không có ID switch.

## Tách ảnh hưởng sampling bằng replay

Tạo [cache evidence](capture_cache/manifest.json) bằng OSNet thật cho mọi candidate của 1505 frame, giữ ID/timestamp. Không dùng pseudo-label baseline làm ground truth.

**Replay đúng thời điểm lấy mẫu:** dùng age và reset_serial đã ghi; **255/255** mẫu có ảnh tái hiện khớp selected_candidate, reason, state, action; 0 mismatch. Ba frame thiếu ở đầu đều HOLD similarity thấp trước khi có accepted/pending history; frame thiếu cuối không ảnh hưởng phần so sánh. Xem [sampled_replay_summary.json](sampled_replay_summary.json).

Trong 54 lần AWAITING_MULTI_OBSERVATION_CONFIRMATION:

- 41 lần mới đạt count=1, 13 lần đạt count=2.
- 22 lần bắt đầu khi không có pending chain trước đó.
- **19 lần đang có pending chain nhưng phải bắt đầu lại vì khoảng mẫu >1 giây**; 13/19 đồng thời không đạt IoU continuity, không lần nào trong nhóm này trượt appearance continuity. Không được quy cả 19 hoàn toàn cho một nguyên nhân duy nhất.

**Replay dày hơn với cùng evidence:** cho manager quan sát toàn bộ ảnh, giữ reset tại frame đầu tiên hoàn tất của mỗi reset_serial. Đây là thí nghiệm chẩn đoán; mốc này là proxy, không tái hiện thời điểm invalidate thật hoặc scheduler live. Tại chính 255 thời điểm được đối chiếu:

- 176 quyết định chọn/HOLD không đổi.
- **79 thời điểm live HOLD nhưng replay dày hơn chọn được candidate**.
- Không có trường hợp live chọn mà replay dày hơn HOLD, hoặc cả hai chọn khác candidate.

Kết quả cho thấy chuỗi quan sát thưa làm mất cơ hội xác nhận/duy trì và đẩy manager về ngưỡng acquire/reacquire cao. Không thể đọc “10/259 được chọn” thành accuracy 3,86%, hoặc kết luận OSNet hỏng. Đồng thời, similarity thấp do tư thế/kích thước/ánh sáng và overlap vẫn là hạn chế thực tế cần xem riêng.

[Biểu đồ theo thời gian](timeline.png) · [PDF](timeline.pdf). Dòng dense là chẩn đoán offline, không phải kết quả camera v3 đạt được.

## Replay chính sách toàn bộ 1505 frame

Replay chuẩn có baseline verification được tiêu thụ ở mẫu kế tiếp, không tái hiện reset/scheduler live. [Báo cáo](full_replay/report.json), [phân tích](replay_analysis.json).

| Kết quả | Số frame |
|---|---:|
| Baseline replay chọn | 1116 |
| Phase 1 replay chọn | 1055 |
| Cùng chọn một candidate | 1044 |
| Cùng HOLD | 378 |
| Baseline-only | 72 |
| Phase 1-only | 11 |
| Cả hai chọn khác candidate | 0 |

72 baseline-only gồm 33 overlap, 19 similarity thấp, 8 chờ xác nhận, 7 detection confidence thấp, 5 margin mơ hồ. Đoạn HOLD dài nhất **13,56 giây**, frame 3475–3882, chủ yếu similarity thấp. Số chọn này không đo đúng/sai target; chưa có GT độc lập, nên các metric ID switch, thời gian bám đúng/sai, recovery latency theo GT vẫn để null.

## Bước tiếp theo

Đủ dữ liệu để tiếp tục offline; **không cần người dùng quay lại ngay**. Ưu tiên công việc kỹ thuật:

1. Xử lý cách cung cấp evidence cho shadow để lấy được chuỗi mẫu liên tục mà không làm chậm luồng chính. Đánh giá tái sử dụng embedding baseline chỉ khi đúng frame/candidate và còn tuổi; baseline không luôn tính đủ mọi candidate nên không được coi embedding cũ là evidence mới.
2. Dùng capture hiện có kiểm chứng cơ chế mới, đo distribution khoảng mẫu và tỷ lệ đứt confirmation. Không dùng việc hạ ngưỡng similarity hoặc kéo dài freshness để che vấn đề sampling.
3. Sau khi tách được sampling, tiếp tục kiểm tra các đoạn overlap/similarity thấp của Phase 1 trên cả capture cũ và mới. Giữ Phase 1 chỉ quan sát trong giai đoạn này.

Lượt phân tích này tạo audit/cache/replay/contact sheets/biểu đồ; không sửa runtime, policy, config hay raw capture/log. Các bước offline chạy không mở camera/motor. Đã kiểm tra tổng số mẫu, đối chiếu metadata, checksum source và hạch toán completed/discarded pass.
