# Phiên 19/11/2026 08:30:19 — kiểm tra và Phase 1

Đã hoàn tất kiểm tra bản ghi mới và chạy Phase 1 offline bằng OSNet thật CUDA. Dữ liệu dùng được; không cần quay lại phiên này. Không mở camera, không gửi lệnh motor, không đổi code/config hoặc bật Phase 1 live.

## Chất lượng capture

`logs/identity_capture/capture_706c13123cb84c2294eecf5fa4c3850f`: 1667 frame 704×480, thời lượng giữa frame đầu/cuối **149,900 giây**, khoảng 811 MB. Metadata/reference và toàn bộ PNG đã kiểm tra checksum; 1667/1667 ảnh giải mã đúng shape, timestamp/frame ID tăng, auto_labels khớp metadata. Writer complete, 0 lỗi, 0 queue drop. Khoảng cách frame median 94 ms, p95 138 ms, max 234 ms. Đây là ảnh AI được lấy mẫu, không phải toàn bộ frame camera.

Nhãn live: 1512 frame target selected, 155 unconfirmed. Có SEARCHING, OCCLUDED_HOLD, PERSON_ZOOM, VERIFIED. Contact sheet 8 ảnh trải đều cho thấy nhiều vị trí người, góc nhìn/zoom; không thấy lỗi khối ảnh lớn trong các mẫu đã xem. Kiểm tra hash/PNG không khẳng định mọi pixel nguồn RTSP đều sạch. Không suy diễn nhãn tự động thành ground truth.

`preview.mp4` là video xem lại từ PNG theo timestamp (VFR, thời gian lượng tử hóa khi encode), không dùng làm cache benchmark. `capture_*_contact.jpg` hiển thị bbox và target do baseline chọn. Ảnh gốc không thay đổi.

## Record và trễ main/sub

Nguồn `logs/ptz_20261119_083019.log/.jsonl`, latency summary và 4 trace còn giữ của phiên `083022_300297`.

Toàn phiên, 2585 GUI submit:

- Tuổi frame main từ decoded receive đến Tk: median **62,16 ms**, p95 **80,62 ms**, max **180,44 ms**.
- GUI render median **27,54 ms**, p95 **37,38 ms**, max **63,85 ms**.
- Không có tuổi main tới 3 giây như phiên trước. Chặn overlay ở 0,8 giây không phải lý do các số tuổi frame giảm: metric vẫn đo receive→Tk độc lập với box.

Trace chi tiết đã rotate, chỉ còn **08:33:05.811–08:33:56.142**. Phần còn giữ bao gồm cuối đoạn ghi và sau ghi:

| Khoảng timestamp | GUI mẫu | Median tuổi main | p95 | Max |
|---|---:|---:|---:|---:|
| Trong capture | 218 | 63,08 ms | 78,36 ms | 106,88 ms |
| Ngoài capture (phần còn giữ là sau capture) | 416 | 57,93 ms | 74,25 ms | 117,97 ms |

Không có GUI sample trên 500 ms trong trace còn giữ. Chênh median khoảng 5 ms, không tái hiện stall dài. Không thể khẳng định recorder hoàn toàn không có overhead hoặc đã tìm ra nguyên nhân HEVC của phiên trước: khác cảnh/tải, không đo riêng recorder/decoder, và thiếu trace main trước record.

Log FRAME_METRICS vẫn bao phủ cả ba giai đoạn (timestamp JSON là UTC):

| Giai đoạn | Số mẫu | Median tuổi frame AI |
|---|---:|---:|
| Trước capture | 28 | 96,59 ms |
| Trong capture | 144 | 69,65 ms |
| Sau capture | 30 | 73,88 ms |

Đủ khoảng trước/sau theo quy trình thử. Các khoảng dựa trên timestamp frame đã lưu, không phải thời điểm bấm Start/Stop. Kết quả không cho thấy recorder gây chậm lớn trong phiên này; cũng không coi AI nhanh hơn khi ghi là do recorder. Metric không đo sensor→màn hình, không chứng minh main/sub đồng bộ nội dung tuyệt đối. Không có stderr terminal kèm theo lượt này để xác nhận có/không còn thông báo HEVC.

## Phase 1

Cache: `capture_706c13123cb84c2294eecf5fa4c3850f/cache/`; baseline và identity output: `.../comparison/`. Kiểm tra source/model/reference provenance thành công. Dùng tracklet/candidate/timestamp đã ghi, tính OSNet offline cho từng candidate, cùng evidence cho hai policy. Hoàn tất 1667/1667 mẫu, không dùng pseudo-label làm GT. Gallery update OFF, physical commands 0.

| Kết quả replay | Số frame |
|---|---:|
| Baseline chọn target | 1513 |
| Phase 1 chọn target | 1270 |
| Cả hai chọn cùng candidate | 1270 |
| Baseline chọn, Phase 1 HOLD | 243 |
| Cả hai không chọn | 154 |
| Cả hai chọn nhưng khác candidate | 0 |
| Chỉ Phase 1 chọn | 0 |

Baseline replay khớp lựa chọn live ở 1666/1667 frame. Một frame sai khác không bất thường với replay dùng score tính lại và lịch hoàn thành verification mô phỏng; replay không phải tái hiện nguyên lịch thread live. Cả hai policy chọn lần đầu tại +1,397 s từ frame đầu.

Trong 243 frame HOLD thêm của Phase 1:

- 193: LOW_REFERENCE_SIMILARITY.
- 23: PERSON_OVERLAP.
- 14: AWAITING_MULTI_OBSERVATION_CONFIRMATION.
- 13: LOW_DETECTION_CONFIDENCE.

Các đoạn baseline vẫn chọn mà Phase 1 HOLD dài nhất (tính từ frame đầu capture): **70,96–81,70 s** (124 frame), **27,17–32,53 s** (54 frame), **141,63–144,26 s** (32 frame). Đây là các đoạn cần tập trung phân tích trước khi thay đổi policy. Trạng thái ambiguous của Phase 1 chiếm khoảng 38,59 s theo metric replay; tên/state của hai policy khác nhau nên không so ngang trực tiếp với thời gian mất target thật.

Không có bằng chứng Phase 1 nhận đúng hơn baseline từ số liệu này. Kết quả hiện cho thấy Phase 1 bảo thủ hơn và có nguy cơ ngắt bám quá lâu. Không hạ ngưỡng chỉ để tăng số frame chọn và không bật điều khiển live. Correct/wrong target time, ID switch, fragmentation, recovery accuracy vẫn null vì chưa có GT độc lập. Số lần native track ID thay đổi không phải số lần đổi người thật.

## Bước tiếp theo

Không cần người dùng record lại ngay: đoạn 150 s đủ để phân tích các khoảng HOLD nêu trên. Bước kỹ thuật tiếp theo là xem evidence theo từng khoảng, phân biệt similarity giảm do góc nhìn/che khuất với đối tượng khác, rồi thiết kế/kiểm chứng thay đổi memory/continuity nếu có căn cứ. Dữ liệu hiện tại chưa cho phép chốt Phase 1 tốt hơn để đưa vào điều khiển camera.

Bằng chứng máy đọc: `audit.json`, `latency_and_replay.json`, `before_record_after.json`, `comparison_details.json`, `comparison/report.json`; log thực thi `phase1_run.log`. Các script phân tích được lưu cạnh báo cáo. Lượt này không sửa runtime, nên không chạy lại suite unit không liên quan; kiểm chứng thực hiện trực tiếp trên toàn bộ capture và replay.
