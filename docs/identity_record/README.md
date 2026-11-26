# Ghi dữ liệu camera và gán nhãn identity

Đã tích hợp vào ứng dụng hiện có, mặc định **Record OFF**. Camera tiếp tục dùng baseline; recorder không bật Phase 1, không sửa motor controller và không tự tạo ground truth.

## Thao tác trong ứng dụng

1. Khởi động ứng dụng theo cách đang dùng, chọn đúng ảnh **Target Person**, chờ target reference sẵn sàng.
2. Trong sidebar, dưới **Target Person**, mở mục **Record & gán nhãn** và bấm **Start record + nhãn target tự động**.
3. Thực hiện các tình huống cần đánh giá: một người, crossing, che khuất, target mất/rời FOV rồi trở lại. Quan sát bộ đếm `saved`, `drop`, `error`.
4. Bấm **Stop record**. Chờ dòng **Đã hoàn tất — đã lưu nhãn tự động**. Đường dẫn bản ghi được hiển thị ngay bên dưới; mặc định `logs/identity_capture/capture_<uuid>/`.

Đến đây đã có ảnh và **nhãn target tự động**, không cần gán lại thủ công. Recorder lấy đúng target mà baseline đang chọn; không chạy thêm OSNet hoặc thay đổi cách tracking. Mỗi box có bbox, track ID, target UID, similarity nếu có và nguồn xác nhận. Các frame giữ track giữa hai lần ReID được ghi rõ là dựa trên tính liên tục của track, không giả định OSNet đã xác nhận lại ở mọi frame. Khi che khuất, observation quá cũ hoặc chưa chọn được target, nhãn là `unknown`; những người không được chọn cũng không bị tự coi là người khác chắc chắn.

Nhãn này là **pseudo-label** (kết quả hệ thống), phù hợp để lưu target và xem lại quá trình chạy. Nếu hệ thống nhận nhầm, nhãn tự động cũng có thể sai; không dùng nó làm đáp án chuẩn để kết luận độ chính xác/ID switch của chính hệ thống đó.

### Review thủ công — chỉ khi cần ground truth

Bấm **Review nhãn thủ công (tùy chọn)…**, chọn capture đã dừng. Chọn box, nhập `target` hoặc tên ổn định như `person_2`, bấm **Gán identity cho box đã chọn**. Gán mọi candidate của frame, chọn target có/không xuất hiện rồi **Xác nhận nhãn & tiếp**. Chỉ frame đã xác nhận mới được lưu vào `annotations.json`; bỏ qua frame không chắc chắn. Nhãn tự động vẫn được giữ riêng.

## Nội dung bản ghi

- Ảnh PNG **không overlay**, nguyên frame BGR tại thời điểm inference. Đây là các frame AI xử lý, **không phải video đủ 30 FPS** hay raw sensor/encoded RTSP stream.
- `frames.jsonl`: frame_id, receive monotonic timestamp, detections gốc, candidates/MOT track IDs, bbox, similarity nếu baseline đã tính, target_uid/session, state, selected proposal, age và detector latency.
- `auto_labels.jsonl`: nhãn tự động cho từng frame đã ghi thành công (`target`/`unknown`), bbox, track ID, target UID và evidence; `source=pseudo_label`, `ground_truth=false`. Nhãn cũng nằm trong `frames.jsonl` ở `auto_annotation`.
- `target_reference.png`: crop tham chiếu đang dùng; `manifest.json` có reference embeddings, config liên quan, checksum model/source/reference. Không có thông tin đăng nhập camera.
- `summary.json`: kết quả đóng writer, số frame/drop/error và checksum metadata. Chỉ bản ghi đã hoàn tất mới mở được trong label tool.
- `annotations.json`: **nhãn người review**, tách biệt prediction, gắn checksum capture. File nhãn thay đổi từ bên ngoài trong lúc review sẽ không bị tự ghi đè.

Recorder dùng lại queue/writer của `MotionRecorder`, queue tối đa 2 frame; không ghi PNG trên inference thread. Khởi tạo writer chỉ khi người dùng Start và có reference hợp lệ. Đổi target UID/reference sẽ dừng session để tránh trộn người; bấm Start lại để tạo capture mới. Stop và khi đóng ứng dụng sẽ drain queue; STOP motor được yêu cầu trước bước dừng recorder khi shutdown.

Giới hạn mặc định: 3.000 frame hoặc khoảng 1 GB ảnh/metadata frame; reference/manifest là phần dung lượng bổ sung nhỏ. Có thể sửa `IDENTITY_RECORD_MAX_FRAMES`, `IDENTITY_RECORD_MAX_BYTES`, `IDENTITY_RECORD_DIRECTORY` trong config trước khi mở ứng dụng. Record luôn OFF mỗi lần khởi động; không tự ghi từ environment variable.

`selected_candidate` ghi proposal ngay sau selector, **trước final freshness/session/motor gates**. Không diễn giải trường này là lệnh motor đã gửi. Timestamp là lúc decoded frame đến ứng dụng, không phải sensor exposure time. Queue drop, frame detector bỏ qua và camera gap có thể tạo khoảng trống; metric thời gian dựa trên interval sampling không thể phục hồi các frame chưa ghi.

## Chuyển sang benchmark Phase 1 offline

Nếu cần benchmark độ chính xác, sau khi Stop và review nhãn, từ Git root:

```bash
cd /mnt/files/Project/ptz/ptz
PYTHON=/home/admin1/miniconda3/envs/py/bin/python

# Chọn capture thực tế; dùng output mới mỗi lần để bảo toàn evidence
"$PYTHON" tools/prepare_identity_capture.py \
  --capture logs/identity_capture/capture_THAY_BANG_ID_THUC \
  --output /tmp/ptz_labeled_cache --device cuda:0

"$PYTHON" tools/identity_replay.py replay --identity-memory \
  --cache /tmp/ptz_labeled_cache \
  --annotations /tmp/ptz_labeled_cache/annotations.json \
  --output /tmp/ptz_labeled_comparison
```

Export giữ nguyên frame/candidate ID và timestamp để nhãn vẫn đúng. Nó sử dụng detection/tracklet đã ghi và tính OSNet thật offline cho toàn bộ candidate bằng reference embeddings đã lưu; không chạy lại YOLO/ByteTrack để đổi candidate dưới nhãn. Model/source checksum khác lúc capture sẽ bị từ chối. Capture thiếu nhãn vẫn export được cache nhưng không có annotations.json: bỏ `--annotations` để replay, các metric GT để null.

Detector latency/observation age là số đo lúc live capture; ReID latency của cache là **offline recomputation**, không phải ReID latency live. Benchmark vẫn là policy replay (completion verification ở frame mẫu tiếp theo), không mô phỏng motor/scheduler thật. Annotation không được dùng làm quality approval cho gallery hoặc làm tín hiệu giúp candidate chọn đúng người.

Có thể mở label tool không cần ứng dụng camera:

```bash
"$PYTHON" tools/label_identity_capture.py --capture logs/identity_capture/capture_THAY_BANG_ID_THUC
```

## Kiểm chứng

- Sau bổ sung nhãn tự động: **257 test pass**, gồm nhãn theo ReID/track continuity, unknown khi stale/hold và từ chối dùng pseudo-label làm GT. Xem `autolabel_tests.log`.

- 254 test pass trong `final_tests.log`: 15 test recorder/annotation/export mới, 23 test Phase 1 và regression identity, camera-motion recorder, GUI startup mock, stale/session/STOP/pan/zoom. Kết nối mạng bị chặn trong test suite.
- Đã chạy label window trên nested X display riêng: render, gán identity, xác nhận, reload. Dữ liệu test tổng hợp, không phải ground truth người thật. `ui_smoke.json`, `label_ui_smoke.png`.
- Offline PNG benchmark 40 mẫu mỗi kích thước, cùng ảnh có sẵn, queue được drain ngoài đoạn đo submit: 704×480 submit mean 0,493 ms/p95 1,080 ms, encode+write mean 19,009 ms; 1920×1080 submit mean 1,534 ms/p95 2,058 ms, encode+write mean 91,374 ms. Không phải live FPS hay phép đo overload; xem `recorder_benchmark.json`.
- Chưa chạy camera thật hoặc đo ảnh hưởng FPS/motor latency trên camera. Nhãn tự động không phải GT live. Không có tuyên bố giảm ID switch/target loss.

## Thay đổi và rollback

Tệp cũ sửa: `config.py`, `gui/cctv_app.py`, `runtime/inference_worker.py`, `runtime/motion_recording.py`. Tệp mới: `runtime/identity_recording.py`, `tools/identity_dataset.py`, `tools/label_identity_capture.py`, `tools/prepare_identity_capture.py`, `test/test_identity_recording.py`.

Không sửa YOLO/OSNet/ByteTrack, identity selection policy, PTZ controller hoặc STOP/session/motor-busy logic. `MotionRecorder` giữ hành vi mặc định cũ; hash ảnh và stop-at-limit chỉ bật cho identity writer mới.

Snapshot trước lượt thay đổi này: `../validation/identity_record_before/`. Đây là trạng thái **đã có Phase 1** và các thay đổi chưa commit của bạn. Không dùng `git reset/clean/checkout` để rollback.

```bash
# Mặc định chỉ kiểm tra checksum và in kế hoạch
"$PYTHON" docs/identity_record/rollback.py

# Khi muốn gỡ phần record/label: restore 4 tệp cũ, chuyển 5 source mới vào quarantine
"$PYTHON" docs/identity_record/rollback.py --apply
```

Script từ chối nếu source được sửa thêm sau lượt này; không ghi đè công việc mới. Capture/annotations/docs được giữ nguyên. Không chạy rollback Phase 1 cũ trước rollback record: checksum config của Phase 1 cũ sẽ cố ý không còn khớp. Rollback record đã được thử trên bản sao tạm; workspace thật chưa rollback.
