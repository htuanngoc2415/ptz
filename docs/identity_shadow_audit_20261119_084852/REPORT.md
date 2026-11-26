# Kiểm tra shadow phiên 19/11/2026 08:48:52

Đã hoàn tất kiểm tra log camera/shadow, toàn bộ capture và ảnh ở các mẫu khác biệt. Shadow chạy và đóng bình thường, dữ liệu dùng được. Chưa đủ căn cứ chuyển Phase 1 sang điều khiển motor: còn tải inference và khác biệt khởi tạo/reset cần xử lý. Lượt này chỉ phân tích; không sửa runtime/config hoặc mở camera.

## Dữ liệu và tính toàn vẹn

- Log camera `logs/ptz_20261119_084852.log/.jsonl`; latency phiên `084855_454390`.
- Shadow `logs/identity_shadow/shadow_5660ef2737454217807c15be0d38a41b.jsonl`: start lúc **08:49:42.608**; 471 comparison, khoảng **164,97 s** giữa mẫu đầu/cuối; có footer SHADOW_STOP và counts khớp.
- Capture `logs/identity_capture/capture_1b1ecfbd76204aefa35e048b8e6ed40a`: **1559 frame / 166,75 s**, 785,8 MB, writer complete, 0 lỗi và 0 queue drop. Kiểm tra hash metadata/reference và từng PNG, decode 1559/1559 đúng shape; monotonic frame/timestamp hợp lệ. 0 queue drop không có nghĩa lưu tất cả frame camera.
- 471/471 mẫu shadow tìm được frame tương ứng trong capture. Timestamp và baseline selected candidate khớp **toàn bộ**; không ghép nhầm frame trong log so sánh.
- Source checksum shadow/identity khớp code đang có. Mọi decision đều `ptz_authorized=false`, mọi comparison/stop `physical_commands=0`; không có đường phát motor trong shadow. Baseline vẫn điều khiển bình thường, không diễn giải là cả ứng dụng không có lệnh motor.

## Độ trễ và tải thêm

Toàn phiên: main receive→Tk median **60,03 ms**, p95 **76,65 ms**, max **126,72 ms**; không tái hiện main đứng 3 s của phiên cũ. GUI render median 27,29 ms/p95 36,02 ms. Các số này đo từ decoded receive tới Tk submit, không đo sensor→màn hình và không chứng minh main/sub đồng bộ tuyệt đối.

Shadow thực tế khoảng **2,85 mẫu/s**, thấp hơn trần admission 5 mẫu/s. Interval median 300 ms/p95 652 ms/max 1,53 s. Processing wall time median **223 ms**, p95 **566 ms**; age tại log median **337 ms**, p95 **622 ms**, max **790 ms**, vẫn dưới gate 800 ms ở mọi comparison. Processing bao gồm chờ tài nguyên và tính ReID, không phải GPU inference time thuần.

Footer: **1063 skipped**, **10 stale**, **70 different**. Skip bao gồm interval/busy/admission, không phải mất 1063 frame camera. Stale bao gồm hết tuổi hoặc token không còn hiệu lực do reset/Stop; log hiện không tách từng nguyên nhân nên không quy tất cả cho decoder/GPU quá chậm.

Trace chi tiết chỉ còn **08:51:48.876–08:52:46.665**, do rotation. Counter delta trong những cửa sổ còn giữ:

| Chỉ số | Trong khoảng mẫu shadow (38,23 s) | Sau khoảng mẫu shadow (17,08 s) |
|---|---:|---:|
| Camera receive | 30,00 FPS | 30,03 FPS |
| Detection | **9,89 FPS** | **11,65 FPS** |
| GUI unique frames | 12,63 FPS | 13,23 FPS |
| Process CPU median | 519% (~5,19 core) | 466% (~4,66 core) |

Detection thấp hơn khoảng 15% ở cửa sổ shadow so với cửa sổ sau. Đây là dấu hiệu tải thêm phù hợp với việc chạy thêm ReID, không phải phép đo nhân quả thuần: cảnh, số người, zoom và face workload thay đổi; không có trace đầu phiên để làm A/B đầy đủ. Thời điểm chia khoảng là mẫu comparison đầu/cuối, không phải timestamp Stop chính xác (footer chưa ghi timestamp).

FRAME_METRICS toàn phiên: YOLO wall median trước/trong/sau lần lượt **47,57 / 75,98 / 34,82 ms**; frame AI age median **83,97 / 79,92 / 60,69 ms**. Không được nói shadow không ảnh hưởng tốc độ chỉ vì main hiển thị mượt. Gate tuổi frame và detection priority vẫn hoạt động nhưng việc tính ReID bổ sung có chi phí.

## Kết quả so sánh

| Kết quả cùng source frame | Mẫu |
|---|---:|
| Cả hai chọn cùng candidate | 224 |
| Cả hai không chọn | 177 |
| Baseline chọn, shadow HOLD | 59 |
| Shadow chọn, baseline không chọn | 11 |
| Cả hai chọn nhưng khác candidate | 0 |

401/471 mẫu đồng thuận quyết định; **không phải accuracy 85,1%**. 70 khác biệt không đồng nghĩa 70 lỗi. Nhãn baseline và shadow đều là prediction.

59 mẫu baseline-only: **32 LOW_REFERENCE_SIMILARITY**, **19 chờ nhiều quan sát xác nhận**, **8 PERSON_OVERLAP**. Đã xem 12 ảnh trải đều nhóm này (`baseline_only.jpg`); thấy các trường hợp target xoay người, tiến gần người khác, đi xa và đổi góc nhìn. Không gán GT đầy đủ cho 59 mẫu từ contact sheet.

Đã xem **đủ 11 ảnh shadow-only** (`shadow_only.jpg`). Trong ảnh này box shadow nằm ở người áo đen được theo dõi trong phiên; không thấy chọn sang người áo xanh/áo đỏ ở các mẫu đã xem. Đây là nhận xét ảnh giới hạn, không chứng nhận độ chính xác toàn bộ chuỗi hoặc thời điểm ẩn sau occlusion. Hai bên chưa phát sinh mẫu cùng chọn khác candidate trong log, nhưng kết quả giữa các mẫu shadow chưa được quan sát.

## Điểm quan trọng: reset bộ nhớ shadow

Có **5 SHADOW_SESSION**, cùng target UID và cùng hash reference. InferenceWorker gọi `shadow.invalidate()` khi reset tracking; các đường mất target/recovery baseline cũng dùng reset này. Vì vậy shadow khởi tạo lại manager, mất last_trusted, không chỉ reset khi đổi người dùng chọn.

Mẫu đầu của các session ở offset 0; 9,23; 29,53; 113,60; 151,84 s từ sample shadow đầu. Khi manager mới chưa có history, threshold là acquire **0,65**; baseline recovery dùng reacquire **0,70**. Có hai nhóm shadow-only cần tách riêng:

- Frame 2297/2305/2312, offset ~29,96–30,46 s: score ~0,686–0,699; shadow vừa acquire rồi continuing trong session mới, baseline SEARCHING.
- Frame 5970/5974/5988/5996/6001, offset ~152,41–153,44 s: score ~0,663–0,680; cùng tình huống khởi tạo mới.

Do đó **8/11 mẫu shadow-only** nằm trong nhóm có ảnh hưởng khác biệt acquire/reacquire khi reset. Không được dùng chúng để tuyên bố Phase 1 recovery tốt hơn. Ba mẫu còn lại có baseline OCCLUDED_HOLD và shadow chọn: frame 1517, 4179, 4184, score ~0,79–0,85. Gate/thời điểm xác nhận và sampling khác nhau, cần xem như policy disagreement.

Sampling 2,85 Hz, gap có lúc 1,53 s cũng làm khó đạt ba xác nhận trong cửa sổ 1 s; kết quả live shadow không tương đương replay toàn bộ 9–11 frame AI/s.

## Kết luận và việc tiếp theo

Không cần record lại vì file lỗi: dữ liệu đủ để tiếp tục chẩn đoán. Giữ baseline điều khiển, chưa bật Phase 1 motor.

Hai việc kỹ thuật ưu tiên trước phiên thử tiếp theo:

1. Phân biệt reset do thay target/session thực với baseline mất track, và bảo toàn hoặc khai báo rõ chế độ reacquire khi shadow cùng target khởi tạo lại. Không hạ ngưỡng để làm đẹp tỷ lệ chọn.
2. Giảm chi phí ReID trùng lặp hoặc cải thiện admission shadow để không tranh tài nguyên quá nhiều; thêm timestamp Start/Stop monotonic và lý do skip/stale cụ thể để đo A/B chính xác. Không tự tăng sample rate vì đã có tải đáng kể.

Sau khi sửa, dùng capture hiện tại để kiểm tra trước; lúc đó mới cần một phiên live xác nhận. Lượt kiểm tra này không thay source để tránh trộn kết quả quan sát với một phiên bản mới chưa kiểm chứng.

Artifacts: `audit.json`, `performance_and_differences.json`, `analyze.py`, hai contact sheet. Toàn bộ ảnh đã xác minh và metadata cùng-frame được assert; không chạy lại unit suite vì không thay runtime. Raw log/capture giữ nguyên.
