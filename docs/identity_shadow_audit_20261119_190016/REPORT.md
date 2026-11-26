# Phân tích bản ghi 19:00:16 ngày 19/11/2026

**Bản ghi dùng được; chưa cần quay lại để cứu dữ liệu. Phiên này chạy đường shadow v3 (`batched=false`), chưa phải phép thử batch v4.** Các ảnh có tình huống giao cắt thật, overlap do phối cảnh, quay lưng và ra/vào khung, phù hợp cho nghiên cứu identity tiếp theo.

Đã kiểm tra toàn bộ 1.081 PNG, metadata, checksum source/model; chạy lại OSNet CUDA, replay toàn capture và replay đúng lịch shadow. Đã xem ảnh tổng quan, các ảnh shadow chọn có trong capture và ba chuỗi tình huống khó. Lượt phân tích này không sửa runtime/config, không mở camera hoặc chạy motor.

## 1. Nguồn dữ liệu và cấu hình thực chạy

- App log: `logs/ptz_20261119_190016.log` và `.jsonl`; ứng dụng kết thúc bình thường lúc 19:03:58.
- Capture: `logs/identity_capture/capture_b4602881389f406b8db527c0bc96750d`.
- Shadow: `logs/identity_shadow/shadow_21a42713603243019cfefd8e47793591.jsonl`.
- Trace/CSV: prefix `logs/latency/latency_*_20261119_190019_792486`.

Capture **1.081 frame / 134,29 giây**, frame ID **793–4822**, finalized, **0 dropped / 0 errors**, khoảng 543 MB. Tất cả ảnh decode đúng shape 704×480 và đúng checksum; source baseline/shadow và weights khớp phiên ghi. Đây là frame AI lấy mẫu, không phải video main liên tục; 0 recorder drop không chứng minh nguồn RTSP không mất frame.

Header shadow ghi **`batched=false`**, manifest capture cũng ghi **`IDENTITY_SHADOW_BATCHED=false`**. Các cờ Phase 2, identity-memory control và gallery đều OFF. Không có bằng chứng v4 đã được bật trong phiên này; trạng thái kiểm chứng v4 vẫn **`AWAITING_LIVE_EVIDENCE`**. Không suy đoán người dùng đã nhập lệnh nào từ cờ ghi trong log.

Nguồn máy đọc: [capture_audit.json](capture_audit.json), [live_audit.json](live_audit.json).

## 2. Tại sao 49 comparison cuối không có ảnh?

Shadow có **236 comparison trong 172,45 giây**, dài hơn capture. Có **187 comparison khớp ảnh, frame ID, timestamp và baseline proposal**, 0 mismatch. **49 comparison cuối** ở frame 4825–5969 nằm ngoài khoảng record, không phải PNG bị hỏng hoặc recorder làm rơi 49 ảnh.

Log có hai identity session với **target UID khác nhau nhưng cùng hash reference**. Session thứ nhất có 187 comparison, 5 reset giữ history; session thứ hai có 49 comparison. UID/session đổi sát thời điểm capture kết thúc. Recorder hiện có cơ chế dừng khi target binding đổi; diễn biến này phù hợp với cơ chế đó, dù log không đủ để xác định thao tác UI cụ thể đã gây đổi binding.

Đây là thay đổi phiên target trong ứng dụng, **không tự chứng minh ID switch giữa hai người thật**. Chỉ 187 comparison đầu được kiểm chứng bằng ảnh trong bản ghi này. Một trong 7 lựa chọn shadow nằm ở frame 5348 ngoài capture nên chưa review được bằng ảnh.

Chi tiết: [session_analysis.json](session_analysis.json).

## 3. Những gì thấy trong ảnh

[Tổng quan](overview.jpg) · [6 lựa chọn shadow có ảnh](selected.jpg) · [Mẫu baseline chọn nhưng shadow HOLD](baseline_only.jpg).

Trong 6 ảnh shadow chọn có thể đối chiếu, box xanh bao người áo đen có chữ ở ngực, quần nâu/xám tương ứng mục tiêu trong cảnh. Không thấy box xanh chuyển sang người áo đỏ hoặc người mang balô trong 6 ảnh này. Đây là review giới hạn, không phải kết luận độ chính xác toàn clip hoặc không có ID switch.

### Giao cắt hai người mặc đồ tối — frame 1206–1515

[Chuỗi ảnh giao cắt](crossing.jpg), khoảng 19:00:59–19:01:09 trên OSD.

Người mang balô/đeo kính đi sát qua mục tiêu; hai bbox giao nhau, có lúc che nhau. Sau khi tách, target quay người và score chưa đủ bắt lại ngay. Replay Phase 1 có đoạn HOLD **10,30 giây**: 33 mẫu `PERSON_OVERLAP`, 32 mẫu `LOW_REFERENCE_SIMILARITY`, 3 mẫu chờ xác nhận.

Đây là dữ liệu crossing thực hữu ích cho Phase 2. HOLD ban đầu có cơ sở về ambiguity; phần kéo dài sau tách cần đánh giá riêng, không thể sửa bằng cách bỏ hoàn toàn geometry gate.

### Overlap do phối cảnh — frame 2043–2294

[Chuỗi ảnh overlap](overlap.jpg), khoảng 19:01:26–19:01:35.

Mục tiêu đứng/đi ở tiền cảnh, quay lưng; người áo đỏ ở phía sau bàn. Dù không phải cùng một vị trí vật lý, bbox trên ảnh vẫn giao nhau. Replay Phase 1 HOLD **8,36 giây**: 61 mẫu overlap, 9 mẫu similarity thấp, 2 mẫu chờ xác nhận.

Đây là ví dụ cần phân biệt giao cắt nguy hiểm với giao nhau của bbox do phối cảnh. Chưa có đủ GT hoặc chứng cứ để mặc định mọi overlap ở đây đều an toàn.

### HOLD dài có ra khỏi khung — frame 3941–4633

[Chuỗi ảnh ra/vào khung](long_hold.jpg), khoảng 19:02:30–19:02:53.

Target đi sát mép phải rồi biến khỏi khung trong nhiều ảnh; camera đổi góc/zoom và target xuất hiện trở lại ở phía bàn. Replay Phase 1 HOLD **23,07 giây**, gồm similarity thấp, detection confidence thấp, không có candidate và chờ xác nhận.

**Không được gọi toàn bộ 23 giây này là tracker mất target khi target còn nhìn thấy.** Một phần rõ ràng là target ra ngoài ảnh. Cần gán visibility theo chuỗi liên tiếp trước khi đo recovery latency hoặc false rejection.

Chi tiết frame và reason: [replay_analysis.json](replay_analysis.json).

## 4. Hiển thị và tải inference

CSV cuối phiên có 2.551 mẫu receive → GUI-submit, percentile window cũng 2.551:

| Chỉ số | Kết quả |
|---|---:|
| Median | 63,91 ms |
| P95 | 83,72 ms |
| Max | 171,98 ms |

Không thấy spike cỡ giây ở metric GUI này. Tuy nhiên đây là từ receive đến Tk submit, không đo từ cảm biến đến màn hình, không xác nhận đồng bộ main/sub và không loại trừ chậm inference.

Trace đã rotation, chỉ còn khoảng **33,37 giây cuối shadow ON** và **18,64 giây OFF sau đó**; không còn OFF trước:

| Chỉ số trong trace còn giữ | ON | OFF sau |
|---|---:|---:|
| Detection result/s | 8,93 | 9,71 |
| P95 receive → GUI | 83,85 ms | 92,62 ms |
| P95 foreground queue wait, các event được ghi | 96,10 ms | 83,87 ms |

Cảnh và workload giữa hai đoạn khác nhau. Không quy toàn bộ chênh lệch detector cho shadow, cũng không suy rằng shadow làm GUI nhanh hơn từ P95 trên. Đây không phải benchmark v4 và chưa phải A/B có kiểm soát.

## 5. Sampling vẫn là nút thắt chính của shadow

| Chỉ số | Kết quả |
|---|---:|
| Comparison / thời gian shadow ON | 236 / 172,45 s ≈ **1,37 mẫu/s** |
| Job hoàn tất / bị loại | 236 / 110 |
| Quá tuổi / superseded / stop | 103 / 6 / 1 |
| Submit bị bỏ do worker bận / rate limit | 1.038 / 26 |
| Tuổi mẫu median / P95 / max | 391 / 743 / 798 ms |
| Gap toàn log median / P95 / max | 0,411 / 2,212 / 6,533 s |
| Gap >1 giây toàn log | 53 |
| Gap >1 giây chỉ trong cùng session | **52** |
| Gap lớn nhất trong cùng session | **5,234 s** |

Gap lớn nhất toàn log đi qua ranh giới đổi session; không tính nó thành một chuỗi confirmation đang được giữ. Dù loại ranh giới này, vẫn có nhiều gap vượt max-gap 1 giây. Có 9.929 lần retry admission; 76/526 pass đã hoàn tất thuộc job cuối cùng không xuất comparison. Các counters này không phải số frame camera bị mất.

Tất cả comparison được publish còn dưới deadline 800 ms, nhưng nhiều job đã hết tuổi trước khi publish. **Đúng deadline từng mẫu không đồng nghĩa lấy được chuỗi xác nhận liên tục.**

## 6. Quyết định live và replay

Toàn bộ 236 comparison:

| Quan hệ baseline/shadow | Số mẫu |
|---|---:|
| Cùng chọn một candidate | 7 |
| Cùng HOLD | 142 |
| Baseline chọn, shadow HOLD | 87 |
| Shadow chọn, baseline HOLD | 0 |
| Cả hai chọn khác candidate | 0 |

87 baseline-only gồm **48 chờ xác nhận, 37 similarity thấp, 1 overlap, 1 detection confidence thấp**. Các số trên không phải đúng/sai target.

### Tái hiện đúng lịch mẫu

Đã tạo [cache bằng chứng OSNet](capture_cache/manifest.json) từ ảnh thật, giữ ID/timestamp, xác minh source/weights. **187/187 comparison có ảnh khớp selected candidate, reason, state và action**, 0 mismatch.

Trong phần có ảnh, 37 lần chờ confirmation gồm 26 lần mới đạt count 1 và 11 lần đạt count 2. Có **11 lần pending chain bị đứt**: 9 lần có gap >1 giây, 7 lần có IoU continuity không đạt, 0 lần appearance continuity không đạt; các nguyên nhân có thể trùng nhau.

Replay dày hơn trên cùng ảnh cho **64 thời điểm live HOLD chuyển thành chọn được candidate**; 123 thời điểm giữ nguyên, không có live chọn rồi dense HOLD hoặc hai bên chọn khác candidate. Đây là diagnostic với reset đặt tại frame hoàn tất đầu tiên của mỗi reset serial, không phải tái hiện chính xác scheduler/reset live và không phải 64 lần phục hồi target đúng đã được xác nhận.

Nguồn: [sampled_replay_summary.json](sampled_replay_summary.json), [pending_diagnostics.json](pending_diagnostics.json).

### Replay chuẩn toàn bộ capture

Trên cùng 1.081 frame, baseline replay chọn **810**, Phase 1 chọn **758**: cùng chọn 758, cùng HOLD 271, baseline-only 52, không có identity-only hoặc cả hai chọn khác candidate.

Replay baseline consume verification ở mẫu kế tiếp; không tái hiện live scheduler. Số liệu này cho thấy live shadow chọn ít còn chịu ảnh hưởng lớn của lịch quan sát, không thể đọc “7/236” thành độ chính xác 2,97%.

Chưa có GT độc lập; correct/wrong tracking time, ID switch, fragmentation, target loss và recovery latency theo người thật vẫn chưa kết luận. [Báo cáo replay](full_replay/report.json).

## 7. Kết luận và bước tiếp theo

1. **Giữ và dùng tiếp bản ghi này.** Dữ liệu hợp lệ, có crossing và các failure case cụ thể; không cần quay lại cùng cảnh chỉ để thay thế bản ghi.
2. **Chưa có phép đo v4 live.** Để kiểm tra đúng v4, khởi động lại từ `/mnt/files/Project/ptz` với:

   ```bash
   PTZ_SHADOW_BATCHED=1 python3 ptz/main.py
   ```

   Chạy 20 giây shadow OFF, bật record rồi shadow khoảng 60 giây, Stop shadow trước rồi Stop record, thêm 20 giây OFF. Nếu đổi/chọn lại target trong lúc record, kiểm tra trạng thái recorder và Start lại nếu nó đã dừng theo session. Giữ từng capture riêng.
3. **Không bật Phase 2 điều khiển từ kết quả này.** Ưu tiên tiếp tục phân tích crossing 1206–1515, overlap phối cảnh 2043–2294 và visibility khi ra/vào khung. Không hạ similarity hoặc kéo dài tuổi evidence để che sampling thưa.
4. Các ảnh đã xem không cho thấy shadow chọn người áo đỏ trong phần có ảnh, nhưng chưa đủ nhãn để khẳng định không bám nhầm toàn phiên. Cần review độc lập các đoạn liên tiếp trước khi dùng clip này để đánh giá accuracy Phase 2.

Không thay đổi source/config hoặc raw log/capture trong lượt phân tích. `physical_commands=0` áp dụng cho replay/shadow diagnostic, không có nghĩa baseline của phiên live trước đó không điều khiển PTZ.
