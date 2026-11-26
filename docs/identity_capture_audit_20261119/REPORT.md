# Kiểm tra phiên camera 19/11/2026 08:17:10

Kết luận: hai capture hợp lệ để chạy Phase 1 offline; đã chạy xong bằng OSNet thật trên CUDA, không mở camera hoặc gửi lệnh motor. Không cần bỏ bản ghi này. Có xác nhận main đứng hình trong khi sub tiếp tục; chưa xác định recorder là nguyên nhân gốc. Đã sửa việc vẽ detection mới lên main quá cũ.

## Dữ liệu đã kiểm tra

Nguồn: `logs/ptz_20261119_081710.log`, `.jsonl`, latency summary và tất cả 4 phần trace còn giữ; hai thư mục `logs/identity_capture/capture_*`. Kiểm tra hash manifest/reference/frame metadata, hash và giải mã từng PNG, thứ tự timestamp/frame ID, nhãn tự động khớp metadata. 140/140 ảnh đọc được đúng kích thước 704×480, 0 lỗi ghi, 0 queue drop. Đã xem contact sheet 8 ảnh mỗi đoạn: box bám vị trí người trên ảnh sub ở các mẫu, không thấy lỗi khối ảnh lớn ở các mẫu đã xem. Hash và decode thành công không chứng minh mọi pixel của nguồn RTSP đều không lỗi.

| Capture | Bắt đầu tạo (giờ VN) | Thời lượng giữa frame đầu/cuối | Frame | Nhãn target tự động |
|---|---|---:|---:|---:|
| `eb68f45a3a914ba897f811ba3c0129b5` | 08:17:30.459 | 11,273 s | 114 | 80 |
| `9b6f72b28e284599a83a2ca918947993` | 08:20:59.260 | 2,834 s | 26 | 0 |

Đây là frame AI đã xử lý, không phải video liên tục gần 4 phút của phiên chạy. Đoạn cuối SEARCHING toàn bộ; similarity thấp không đủ xác nhận. Khoảng cách frame tối đa lần lượt 498 ms và 234 ms. Các frame AI không xử lý/không đủ điều kiện ghi không xuất hiện trong dataset; `drop=0` chỉ mô tả queue recorder.

File `*_preview.mp4` trong thư mục báo cáo là video xem lại được dựng từ PNG theo khoảng timestamp (VFR, lượng tử hóa thời gian của encoder), không phải RTSP gốc và không dùng làm cache benchmark. Contact sheet `*_contact.jpg` có box; màu xanh là target do baseline chọn, không phải nhãn GT. PNG gốc vẫn nguyên vẹn.

## Main/sub bị lệch

Trace xác nhận frame main **6548** được GUI submit lặp lại tới 08:21:02.401, tuổi ảnh lên **3025,58 ms**. Receive time suy ra khoảng 08:20:59.375. Trong thời gian này capture sub vẫn có frame 6729–6814 và GUI vẫn submit đều. Đó là main không cập nhật ảnh, không phải Tk main loop dừng toàn bộ. ID của hai stream độc lập, không dùng hiệu ID để suy ra độ trễ.

Các GUI submit trong khoảng timestamp capture cuối: median tuổi main **1155,47 ms**, p95 **2302,70 ms** (39 mẫu); ngoài các khoảng capture trong trace còn giữ: median **102,16 ms**, p95 **129,42 ms** (548 mẫu). Sự cố trùng phần lớn đoạn ghi cuối: có tương quan, chưa chứng minh quan hệ nhân quả. Khoảng này là frame đầu/cuối đã lưu, không phải log thời điểm bấm Start/Stop. Trace chi tiết đã rotate và chỉ còn từ 08:19:54.546, không đủ đối chiếu main của capture đầu.

Toàn phiên: GUI render median 47,66 ms, p95 66,20 ms; receive→Tk median 96,68 ms, p95 132,20 ms, max 3025,58 ms. Đây là thời gian từ ảnh decoded tới Tk submit, không đo sensor→màn hình; backlog trước callback không nằm trong số này.

`DualStreamPlayer` nhận main/sub độc lập. `display_result` trước sửa chỉ kiểm tra tuổi detection sub rồi scale bbox theo kích thước, không kiểm tra tuổi frame main. Vì vậy main đứng hình vẫn có thể nhận box mới từ sub. Hai receive timestamp mới cũng không bảo đảm cùng thời điểm phơi sáng; khác FOV/crop còn có thể gây sai tọa độ.

Recorder chỉ lấy frame sub sau selector, copy/serialize trong inference thread và nén PNG/ghi file trong worker queue 2. Nó không nằm trong callback nhận main. Tuy nhiên vẫn chia sẻ CPU/RAM/disk; không có timing recorder hoặc thử A/B cùng cảnh trong phiên này để loại trừ ảnh hưởng tài nguyên. Queue không drop không chứng minh recorder không gây tải.

## Các dòng cảnh báo

- `Could not find ref with POC`: decoder HEVC thiếu ảnh tham chiếu. [Mã FFmpeg chính thức](https://ffmpeg.org/doxygen/trunk/hevc_2refs_8c_source.html) phát cảnh báo tại bước tìm reference. RPS/cu_qp_delta là cảnh báo luồng giải mã, không phải lỗi nhãn hoặc lỗi ghi PNG. Chưa xác định nguyên nhân phía nguồn/mạng/decoder từ log này.
- Timeout 3000 ms khớp cấu hình read timeout của OpenCV. Nó phù hợp với đợt main không cập nhật ~3 s; stderr không ghi stream role và timestamp đầy đủ, nên không gán chắc từng thông báo HEVC cho main hay sub.
- OSNet 550/552: hai key khác shape là `classifier.weight` và `classifier.bias` (đã kiểm tra checkpoint, xem `checkpoint.json`); backbone có kiểm tra bắt buộc trong loader. Đây là head phân lớp không khớp số lớp khi dùng embedding ReID. Không liên quan tới lỗi HEVC.

## Sửa đã thực hiện

`runtime/display_mapping.py` nhận thêm receive timestamp của main; GUI truyền timestamp thật của packet. Khi main quá 0,8 giây (ngưỡng freshness hiện có), không vẽ persons/faces/target và ẩn recognition/face panel. Main có ảnh mới hợp lệ thì overlay trở lại. Không thay đổi quyết định tracking, reference, motor, stream codec hay ngưỡng ReID.

Đây là chặn overlay trên ảnh đứng hình, **chưa phải đồng bộ main/sub theo capture PTS và chưa sửa nguyên nhân decoder đứng hình**. Trong 0,8 giây đầu hoặc khi hai decoder trả ảnh lệch nội dung nhưng callback còn mới, lệch box vẫn có thể xảy ra. Muốn overlay khớp tuyệt đối cần ảnh và detection cùng frame hoặc clock/PTS giữa hai stream được kiểm chứng; không giả định receive time là sensor time.

262 test pass trong `tests.log`, gồm regression main đứng hình/sub mới, main timestamp tương lai/NaN, overlay phục hồi, không mutate kết quả AI và các regression tracking/STOP/pan/zoom/recorder. Compile và diff check pass. Chưa kiểm tra bản sửa trên camera thật.

Snapshot trước sửa: `../validation/identity_capture_audit_before/`. `changes.json` ghi hash trước/sau của 3 file. Rollback record cũ sẽ từ chối GUI đã sửa thêm; không cập nhật hash cũ để vô tình xóa sửa mới.

## Phase 1 đã chạy

Cache từng capture nằm ở `<capture>/cache/`, kết quả ở `<capture>/comparison/report.json`, baseline/identity JSONL cùng thư mục. OSNet thật CUDA, giữ frame/candidate ID/receive timestamp/tracklet gốc; reference/model/source checksum khớp. Baseline và identity dùng cùng evidence. Không nối hai capture vì khoảng trống gần 3 phút không có evidence.

| Capture | Baseline chọn target | Phase 1 chọn target | Quan sát |
|---|---:|---:|---|
| 114 frame | 80 | 59 | Phase 1: 53 LOW_REFERENCE_SIMILARITY, 2 chờ xác nhận, 59 CONFIRMED_MULTI_CUE |
| 26 frame | 0 | 0 | Cả đoạn similarity thấp, Phase 1 HOLD 26 frame |

Đoạn đầu: cả hai chọn lần đầu tại +4,262 s; Phase 1 giữ HOLD thêm ở các frame similarity thấp. Chưa có bằng chứng giảm nhận nhầm/ID switch; cũng không coi ít chọn hơn là cải thiện. Nhãn tự động không được đưa vào metric ground truth. Correct/wrong target time, ID switch, loss/recovery accuracy để null. ReID latency trong cache là tính lại offline, không phải runtime latency. `physical_commands=0`, gallery update OFF, Phase 1 vẫn OFF trong ứng dụng live.

## Có cần quay lại không?

Không cần quay lại vì file hỏng: cả hai đều dùng được và đã dùng. Cần **ghi bổ sung** để kiểm thử mục tiêu Phase 1: một đoạn liên tục khoảng 60–120 giây, Start trước khi target di chuyển, gồm đi qua nhau/che khuất/rời FOV/trở lại, Stop sau khi đã bắt lại. Quan sát bộ đếm saved tăng xuyên suốt; hai capture hiện tại chỉ bao phủ khoảng 14 giây.

Để phân biệt ảnh hưởng recorder, cần so sánh cùng cấu hình/cảnh trong khoảng tắt và bật record, có log per-stream receive/decode, tuổi main tại render và thời gian submit/encode ghi. Phiên hiện tại chưa đủ chứng cứ kết luận recorder gây hoặc không gây main stall. Không tự đổi codec/cấu hình camera trong lượt kiểm tra này.
