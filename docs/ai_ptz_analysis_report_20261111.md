# Báo cáo hiện trạng AI PTZ và dữ liệu phân tích tối ưu

Ngày tổng hợp: **11/11/2026**. Múi giờ hiển thị: **Asia/Ho_Chi_Minh (UTC+7)**.

## 1. Mục tiêu và phạm vi

Tài liệu để gửi cho AI/kỹ sư phân tích nguyên nhân mất bám, gián đoạn zoom, độ trễ nhận diện và đề xuất tối ưu có kiểm chứng. Đây là tổng hợp đọc code, cấu hình và log; không chạy lại camera, không gửi lệnh motor, không thay đổi ngưỡng hay code vận hành.

Phiên log mới nhất tìm thấy khi tổng hợp: **10/11/2026, 13:27:51–13:44:11**; chưa thấy phiên mới ngày 11/11 trong thư mục log chính. Run ID: `d21d2da7e34b4f6c97666d83b3709e00`.

Phân biệt ba loại bằng chứng:
- **Log phiên chạy:** chứng minh sự kiện đã được ghi trong phiên 10/11.
- **Code/cấu hình đọc ngày 11/11:** thể hiện triển khai hiện có; không phải snapshot đầy đủ của mọi biến môi trường/toggle trong phiên cũ.
- **Giả thuyết:** hướng điều tra, chưa phải nguyên nhân đã xác minh.

## 2. Kết luận có bằng chứng

- AI đã chạy: person detector nạp thành công; OSNet dùng `cuda:0`; SCRFD và ArcFace dùng `CUDAExecutionProvider`.
- Theo dõi dùng **ByteTrack + OSNet kiểm tra định kỳ/khi có sự kiện**. Có 21 lần khóa từ trạng thái tìm kiếm, 21 lần mất mục tiêu và 20 lần bắt lại.
- Camera command worker xử lý 4.145 lượt tác vụ với đủ chuỗi vào hàng đợi → lấy ra → bắt đầu → kết thúc. Không có bằng chứng hàng đợi command bị nghẽn kéo dài trong phiên này.
- Có 27 xung zoom tự động, cả 27 có ACK và sự kiện kết thúc; 24 zoom out, 3 zoom in. ACK chưa chứng minh biên độ chuyển động vật lý.
- Trước lúc ngắt RTSP, tốc độ nhận frame khoảng 29,96 FPS nhưng xử lý detection khoảng 9,39 FPS; mục tiêu cấu hình AI là 15 FPS.
- Bộ đếm cuối ghi **122 stale inference results**. Chỉ có **9 dòng `PERCEPTION_RESULT_STALE` trong JSONL ứng dụng**: không được coi 9 là tổng số frame lỗi.
- Log ghi `RTSP_DISCONNECTED` lúc 13:35:38.188; đến 13:44:11 ứng dụng mới dừng do `KeyboardInterrupt`. Chưa xác định ngắt do thao tác người dùng, đóng capture hay lỗi mạng/camera.

## 3. Những thành phần đang sử dụng

### 3.1 Camera, video và điều khiển

| Thành phần | Cấu hình/triển khai hiện tại | Bằng chứng/giới hạn |
|---|---|---|
| Camera | HANET trong mạng LAN | Địa chỉ và thông tin đăng nhập không cần cho AI phân tích nên không đưa vào báo cáo |
| PTZ backend | `hanet` | `camera.json`; log có worker `hanet-heartbeat` |
| Video backend | `opencv` | `camera.json`; sự kiện ngắt từ `opencv-capture` |
| Luồng AI | RTSP channel 1, subtype 1 | Substream; kiểm tra độc lập trước đó đọc được 704×480 |
| Luồng hiển thị cấu hình | RTSP channel 1, subtype 0 | Không coi cấu hình là bằng chứng cả hai stream đều chạy suốt phiên |
| RTSP latency | 200 ms được in lúc startup | Không phải độ trễ đầu-cuối đã đo; hiệu lực phụ thuộc backend |
| Tốc độ PTZ chung | 4 | Log startup và cấu hình |
| Dải optical zoom khai báo | 1×–37×; full zoom time 4,80 s | Tham số phần mềm, chưa có đo/calibration lens thực tế trong phiên |
| Telemetry lens | Không có zoom/focus position | HANET `telemetry()` trả None |
| Focus | `UNSUPPORTED` | Backend chưa xác minh autofocus/focus position |
| Toggle khi startup | Auto Zoom=False, Auto Focus=False, Digital PTZ=False | Zoom có chạy về sau; không suy ra toggle giữ nguyên cả phiên |

`read_status()` của HANET hiện dùng heartbeat, trả zoom/focus=None. `stop()` chỉ gửi STOP cho các mã chuyển động đang active; nhiều lượt gọi stop có thể hoàn tất cục bộ, không tương đương từng ấy gói tin camera nhận được.

### 3.2 AI, tracker và thư viện

| Khối | Đang dùng | Tham số chính |
|---|---|---|
| Person detector | YOLO qua Ultralytics, `models/person/person.pt` | imgsz=640; confidence=0,35; NMS IoU=0,60; class person=0 |
| Motion tracker | ByteTrack, adapter riêng | low=0,10; high/new=0,35; match=0,8; fuse_score=False; frame_rate cấu hình=15 |
| Person ReID | OSNet-AIN x1.0, `osnet_ain_x1_0_msmt17.pth` | input H×W=256×128; vector 512 chiều; batch=4; CUDA |
| Face detector | SCRFD `det_2.5g.onnx` | input=640×640; conf=0,50; NMS=0,40; CUDA ORT |
| Face recognizer | ArcFace R50 `w600k_r50.fp16.onnx` | CUDA ORT; 12 identities, 36 embeddings lúc startup |
| Face pipeline | optimized, bất đồng bộ | cấu hình detection=3 Hz; face optical-flow tracking=False |
| GUI | Tkinter, worker tách nhánh | latest-frame mailbox; ưu tiên frame mới |
| GPU scheduling | Một accelerator lane có khóa tái nhập | detection được ưu tiên; recognition nhường detection/face |
| Camera I/O | CameraCommandWorker | tuần tự hóa tác vụ camera |

Thư viện khai báo trong `requirements.txt`: numpy>=1.24, opencv-python>=4.8, onnxruntime-gpu==1.23.2, Pillow>=10, python-mpv>=1, requests>=2.31, websockets>=12, ultralytics==8.4.25, lap>=0.5.12,<0.6, tkinterdnd2>=0.4.2. OSNet còn dùng torch/torchvision. Đây là **yêu cầu khai báo**, không phải `pip freeze` của tiến trình đã chạy. Chưa đủ bằng chứng trong báo cáo để xác nhận GPU model, driver, CUDA, phiên bản torch/torchvision và tất cả phiên bản thực tế. Không suy ra YOLO architecture cụ thể chỉ từ tên `person.pt`.

### 3.3 Luồng so khớp OSNet

1. Ảnh mục tiêu được detect người, chọn người có bbox lớn nhất (`TARGET_IMAGE_IS_CROP=False`).
2. Gallery gồm 5 bản: gốc, tối nhẹ, sáng nhẹ, gamma và biến thể sáng/tương phản nhẹ. Gallery không tự học thêm từ người đang bám.
3. Crop mỗi người → BGR sang RGB → resize 256×128 → normalize ImageNet → OSNet → chuẩn hóa L2 vector 512 chiều.
4. Cosine similarity = tích vô hướng vector đã chuẩn hóa; lấy max trên 5 vector mẫu.
5. Nếu luma<90 hoặc contrast<35, tạo thêm crop CLAHE=1,5 và gamma=0,75, trích đặc trưng thêm. Fusion hiện tại là `max(raw, normalized)`.
6. Xếp hạng người theo điểm. Score là độ giống đặc trưng, không phải xác suất đúng người.

| Điều kiện | Giá trị |
|---|---:|
| Acquire threshold | 0,65 |
| Reacquire threshold | 0,70 |
| Top1 − Top2 khi có người thứ hai | ≥0,08 |
| Xác nhận | 3 frame xử lý liên tiếp |
| IoU xác nhận | 0,40 |
| IoU continuity khi đã khóa | 0,25 |
| Kiểm tra OSNet định kỳ sau khóa | 1,0 s |
| Lost grace | 3,0 s |
| Crossing overlap cấu hình | 0,15 |
| Search interval | 0 s: mỗi frame inference mới khi tìm |
| `TARGET_CONTINUOUS_REID` | False |

Lưu ý triển khai hybrid: khi đến hạn kiểm tra, selector đặt `crossing_hold=True` và stage `TEMP_LOST` trước khi chấm điểm, kể cả kiểm tra định kỳ. Nếu đạt, có thể trở lại `TARGET_LOCKED` ngay trong lần xử lý. Nếu mất ID/chồng lấn thì dùng điều kiện recovery chặt hơn; còn chồng lấn thì không nhận ứng viên. Vì vậy số transition `TEMP_LOST` không bằng số lần thực sự mất người. `REID_KEEP_THRESHOLD=0,50` tồn tại trong cấu hình/base selector nhưng không phải ngưỡng xác minh sau khóa của hybrid: hybrid dùng acquire/reacquire threshold.

Cấu hình pipeline có `reid_verification_interval_frames=10`, nhưng điều kiện `due` trong override `HybridReIDTargetSelector` dùng interval theo thời gian và sự kiện; không nên mặc định mọi frame gate của base selector đều có hiệu lực.

### 3.4 Điều khiển pan/tilt/zoom

- Auto pan/tilt bật; deadzone=0,08, release=0,04 theo kích thước ảnh; max speed=3; pulse=0,12 s; settle=0,15 s.
- Tỷ lệ chiều cao người mục tiêu=0,40; mặt mục tiêu=0,25; vùng face hold=0,22–0,28.
- Zoom pulse cấu hình dài/ngắn/tinh=0,25/0,12/0,06 s; các 27 yêu cầu trong phiên đều đặt duration=0,25 s.
- Tuổi observation tối đa khi nhận lệnh=0,8 s; feedback timeout=0,8 s; direction hold/cooldown=0,40 s; settle=0,20 s.
- HANET dùng zoom theo hướng và thời gian; absolute zoom/focus không khả dụng. Pan/tilt và zoom tranh quyền điều khiển motor, thể hiện qua `OWNER_pan_tilt`.

## 4. Timeline và thay đổi ảnh mục tiêu

| Thời điểm 10/11, UTC+7 | Sự kiện |
|---|---|
| 13:27:51.788 | Bắt đầu ứng dụng |
| 13:27:55 | Person model, face CUDA providers và database sẵn sàng |
| 13:27:57.122 | OSNet target ban đầu `target.jpg` sẵn sàng |
| 13:28:02.589 | **Thay ảnh mục tiêu** bằng `aded02f5-dc45-41cc-a9e8-61dabd31b3a9.jpeg`; detect một người, bbox=(110,105,472,1186) |
| 13:28:14.281–14.287 | Xác nhận/khóa mục tiêu đầu tiên |
| 13:29:37.529 | Xung auto zoom đầu tiên |
| 13:31:09.017 | Xung auto zoom cuối cùng |
| 13:33:25–26 | Một command và hai stop từ manual worker |
| 13:35:38.172 | `MANUAL_REQUEST`, command=null; chưa đủ chứng cứ quy kết đây là nguyên nhân ngắt video |
| 13:35:38.188 | `RTSP_DISCONNECTED` |
| 13:44:11.750 | KeyboardInterrupt và ứng dụng dừng |

Không gộp điểm ReID trước/sau thay ảnh mà bỏ qua sự thay đổi reference. Ghi chú `live_camera_check_20261110.md` là phép kiểm tra ban đầu khi thiếu thư viện và chưa xác minh motor; không đại diện cho phiên AI chạy sau đó.

## 5. Đối chiếu các lần nhận/xử lý lệnh

### 5.1 Toàn bộ camera worker

Đọc cả camera log chính, `.1`, `.2`, bao phủ 13:27:55.100–13:44:11.618:

| Tác vụ | Số lượt transaction |
|---|---:|
| `command` | 423 |
| `stop` | 2.747 |
| `read_status` | 975 |
| Tổng | **4.145** |

Counter theo command_id của bốn event QUEUE_ENTER, QUEUE_EXIT, TRANSACTION_START, TRANSACTION_END khớp nhau, kể cả số lần lặp. Có 4.118 ID khác nhau: ID tương quan có thể dùng lại cho các operation trong một hành động, không dùng số ID làm số lượt transaction.

Chờ queue: median=0,543 ms, max=9,678 ms. Transaction duration: median=0,328 ms, max=27,748 ms. **TRANSACTION_END chỉ chứng minh hàm đã kết thúc**, không tự chứng minh return thành công hay camera ACK; trong báo cáo chỉ khẳng định ACK ở các event zoom có bằng chứng riêng.

### 5.2 Điều khiển tay

| Thời gian vào queue | Operation | Queue wait ms | Transaction ms |
|---|---|---:|---:|
| 13:33:25.661 | command | 0,101 | 4,634 |
| 13:33:25.804 | stop | 1,121 | 5,752 |
| 13:33:26.015 | stop | 0,113 | 0,043 |

Không đủ dữ liệu trace còn giữ để xác nhận hướng của command tay. Event manual command=null lúc 13:35:38 không phải bằng chứng một lệnh chuyển động mới.

### 5.3 Chi tiết 27 xung zoom tự động

Ghép theo command_id: ACTION_ACCEPTED → WORKER_DISPATCH → RPC_ACK → ACTION_ENDED. Thời điểm trong bảng là dispatch; ACK ms là khoảng cách dispatch đến event RPC_ACK phía ứng dụng, **không phải network RTT thuần**. Duration thực lấy từ ACTION_ENDED. ID đầy đủ giúp đối chiếu lại log.

| # | Dispatch UTC+7 | Hướng | ACK sau (ms) | Duration thực (ms) | Lý do kết thúc | command_id |
|---:|---|---|---:|---:|---|---|
| 1 | 13:29:37.529 | OUT | 10.82 | 263.01 | PULSE_COMPLETE | `cmd_31b67cb55fb1409785e8076cae21d884` |
| 2 | 13:29:38.087 | OUT | 6.10 | 257.78 | PULSE_COMPLETE | `cmd_525eeb7bb09b4872b1c048dcd51aaacf` |
| 3 | 13:29:38.707 | OUT | 6.68 | 258.59 | PULSE_COMPLETE | `cmd_353a54cfc09a48589bcf990987fcab11` |
| 4 | 13:29:39.453 | OUT | 4.00 | 266.00 | PULSE_COMPLETE | `cmd_6fc09dc8f12146788045f782124e5797` |
| 5 | 13:29:40.046 | OUT | 8.98 | 258.04 | PAN_TILT_CENTERING | `cmd_246dcd6f7fdc4162829f0fac8bb1d602` |
| 6 | 13:29:44.178 | OUT | 9.86 | 262.30 | PULSE_COMPLETE | `cmd_53fb381b26ff4dbcb3aa0d912a61c13c` |
| 7 | 13:29:44.711 | OUT | 10.61 | 269.46 | PULSE_COMPLETE | `cmd_f0ebb9b11366467f931cc9e87315e687` |
| 8 | 13:29:45.297 | OUT | 12.92 | 259.51 | PULSE_COMPLETE | `cmd_eb03897662fc4f3da4e5ecb42db878f0` |
| 9 | 13:29:45.843 | OUT | 9.29 | 264.64 | PULSE_COMPLETE | `cmd_8c624126ee914c2eadcceeb7e10079bb` |
| 10 | 13:29:46.586 | OUT | 4.38 | 259.48 | PULSE_COMPLETE | `cmd_4e4811ff282344bcbb810a85e1e3a4fa` |
| 11 | 13:29:47.138 | OUT | 4.37 | 260.99 | PULSE_COMPLETE | `cmd_db3dd1f1c51348be89866c06d9608ce4` |
| 12 | 13:29:47.730 | OUT | 6.14 | 256.23 | PULSE_COMPLETE | `cmd_5695c1011469431280f114a0af2e429c` |
| 13 | 13:30:02.885 | OUT | 8.51 | 256.35 | PULSE_COMPLETE | `cmd_1bae32f99c4a45e98dc3b2ce33a4e7fc` |
| 14 | 13:30:03.491 | OUT | 10.61 | 260.40 | PULSE_COMPLETE | `cmd_cdb80a13f2444dabbe3671a3e0e715ea` |
| 15 | 13:30:07.312 | OUT | 6.80 | 263.16 | PULSE_COMPLETE | `cmd_13485d9785dc44098702741d36c60285` |
| 16 | 13:30:07.937 | OUT | 8.28 | 267.85 | PULSE_COMPLETE | `cmd_99decd3840f14f8e81eaa4483510daae` |
| 17 | 13:30:08.569 | OUT | 6.69 | 267.65 | PULSE_COMPLETE | `cmd_37f462cf926042a3bec0c84c83eac323` |
| 18 | 13:30:09.257 | OUT | 15.89 | 263.42 | PULSE_COMPLETE | `cmd_0e707e932b9b44f8b8f22b1a4ba33eb3` |
| 19 | 13:30:24.076 | OUT | 8.29 | 214.20 | PAN_TILT_CENTERING | `cmd_5e890d167c024c51a73cc2d718dde7d8` |
| 20 | 13:30:25.686 | OUT | 5.28 | 265.87 | PULSE_COMPLETE | `cmd_7406cbc0f0224a6c88ebff64aebd4038` |
| 21 | 13:30:58.529 | IN | 10.58 | 257.24 | PULSE_COMPLETE | `cmd_0d5544f62d76440eaa8f4d0bd12fac72` |
| 22 | 13:30:59.079 | IN | 10.31 | 265.02 | PULSE_COMPLETE | `cmd_3d53bb8a5de1403bb85a56c91ba7d6fa` |
| 23 | 13:30:59.696 | IN | 11.00 | 257.44 | HOLD_ZONE | `cmd_c0358915305c409c898ea7b6e43aa705` |
| 24 | 13:31:04.807 | OUT | 12.60 | 268.89 | PULSE_COMPLETE | `cmd_48eb4c1f9d77436897401611ed71b35c` |
| 25 | 13:31:06.728 | OUT | 9.59 | 257.66 | PULSE_COMPLETE | `cmd_823df49fe7f54b9b9791ad0e5b396af9` |
| 26 | 13:31:07.273 | OUT | 5.41 | 265.76 | PULSE_COMPLETE | `cmd_a9a3531d249342588bcf657d925318b2` |
| 27 | 13:31:09.016 | OUT | 9.07 | 162.13 | HOLD_ZONE | `cmd_97779c0b349a4337a659a16cea15ab8a` |

Cả 27 có ACK và kết thúc. Lý do: PULSE_COMPLETE=23, PAN_TILT_CENTERING=2, HOLD_ZONE=2. Có 93 event hoãn zoom: OWNER_pan_tilt=86, LENS_SETTLING=7. Đây là số **quyết định hoãn**, không phải 93 lệnh mạng bị camera từ chối.

Có 7 event ZOOM_VISUALLY_STABLE dựa trên `estimated_bbox_ratio`; `motor_stopped`/lens telemetry không khả dụng. Không được coi 7/27 là tỷ lệ motor hoàn tất, hoặc kết luận 20 lần còn lại thất bại.

## 6. Hiệu năng và chất lượng theo dõi

### 6.1 Snapshot trước ngắt video và cuối phiên

| Metric | 13:35:37.484, trước ngắt | 13:44:10.967, cuối phiên |
|---|---:|---:|
| received_frames | 13.853 | 13.873 |
| detection_frames | 4.341 | 4.350 |
| gui_unique_frames | 5.912 | 5.921 |
| dropped_frames | 9.087 | 9.099 |
| superseded_detection_frames | 382 | 382 |
| stale_inference_results | 121 | **122** |
| camera_receive_fps | 29,96 | 14,22 |
| detection_fps | 9,39 | 4,46 |
| gui_unique_fps | 12,79 | 6,07 |

FPS là metric do profiler báo, không phải sensor FPS hay throughput tức thời từng giây. Cuối phiên mẫu số thời gian có cả khoảng không nhận hình nên FPS giảm; không dùng mức cuối để kết luận GPU chậm đi. Với latest-frame mailbox, dropped/superseded frame có thể là chủ ý bỏ frame cũ; không tương đương mất gói mạng. Không cộng các counter nếu chưa xác minh chúng có giao nhau hay không.

Bộ đếm cuối: target_lock_success=21, tracking_lost=21, tracking_recovery=20; SCRFD calls=2.380; ArcFace calls=47; recognition_attempts=47; recognition_accepted=47; recognition_rejected=169; identity_confirmation=5; identity_reverify=1. Có rejection reason `FACE_ASSOCIATION_AMBIGUOUS`=169 trong snapshot. Accepted/rejected thuộc các gate khác nhau; không suy ra accuracy 100% từ 47/47 và không tính tỷ lệ sai người khi thiếu ground truth.

JSONL ứng dụng: 65 FACE_DETECTED, 65 FACE_LOST, 30 TARGET_CANDIDATE, 1 TARGET_CONFIRMED, 20 TARGET_REACQUIRED, 21 TARGET_LOST, 9 PERCEPTION_RESULT_STALE. Đây là event counts, không phải số người duy nhất.

Log text: TEMP_LOST→OCCLUDED_HOLD=576 và chiều ngược lại=576; TEMP_LOST→TARGET_LOCKED=192. Có thể có state churn khi các tầng ghi stage khác nhau; cần xem lifecycle, không tự coi 576 lần là 576 lần che khuất độc lập. Trial log có START=21, TARGET_LOCKED=16, RESET=14, TIMEOUT=6, FACE_DETECTED=2, CONFIRMED=1. Trial counts không thay thế counters khóa/nhận diện toàn pipeline.

### 6.2 Độ trễ theo CSV summary

Đơn vị ms. CSV là snapshot ghi trước khi ứng dụng dừng (camera transaction count=4.071 thay vì 4.145 cuối log). Mean/max dùng bộ tích lũy, percentile có cửa sổ tối đa 4.096 mẫu; không coi mọi cột là cùng toàn bộ phân phối. Các span có thể lồng nhau và khác tập mẫu, **không cộng mean các hàng thành latency đầu-cuối**. Thời gian host wall có thể gồm chờ GPU/đồng bộ.

| Stage | Count | Mean | P50 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| yolo_total_ms | 4352 | 29.35 | 26.91 | 47.72 | 57.60 | 448.79 |
| reid_preprocess_ms | 4135 | 8.27 | 4.75 | 17.95 | 89.12 | 340.99 |
| reid_inference_ms | 4135 | 30.49 | 28.74 | 49.41 | 62.22 | 129.44 |
| frame_age_since_receive_ms | 4350 | 17.52 | 17.89 | 33.02 | 49.11 | 70.47 |
| reid_total_ms | 1994 | 93.07 | 77.98 | 184.05 | 370.62 | 1012.99 |
| reid_selection_ms | 1390 | 76.08 | 72.88 | 172.03 | 373.06 | 832.57 |
| face_queue_wait_ms | 2380 | 1.19 | 0.04 | 0.08 | 47.00 | 104.97 |
| arcface_inference_ms | 47 | 53.54 | 47.75 | 48.79 | 245.67 | 413.25 |
| face_recognition_total_ms | 47 | 59.18 | 51.58 | 58.18 | 258.26 | 428.41 |

Ví dụ cụ thể: 13:28:11.320 có frame_age≈913,17 ms và reid_ms≈832,68 ms; 13:28:15.438 có frame_age≈897,44 ms và reid_ms≈830,43 ms. Cần đối chiếu định nghĩa span vì yolo_ms/reid_ms trong event không nhất thiết là các đoạn nối tiếp không giao nhau.

Một RESOURCE_SAMPLE lúc 13:34:46.193 ghi CPU process≈488% (100%=một core), RSS≈2.477 MB, GPU utilization=None. Đây là một mẫu, không phải peak/mean toàn phiên; chưa đủ chứng minh GPU saturation hay thiếu VRAM.

## 7. Giả thuyết nguyên nhân cần kiểm chứng

| Ưu tiên | Giả thuyết | Bằng chứng gợi ý | Cách kiểm chứng và giới hạn |
|---|---|---|---|
| 1 | Chuyển state/authorization khi verify định kỳ làm tracking/face/zoom bị ngắt quãng | Hybrid đặt TEMP_LOST trước chấm điểm; transition dày; 65 face lost | Replay cùng video, ghép lock_session_id/track_id/timestamp/consumer gate; phân biệt mất thật với verify thành công trong cùng lượt |
| 1 | OSNet toàn pipeline và xử lý ảnh bổ sung làm tăng tuổi observation | ReID total mean≈93 ms, p95≈184 ms, max≈1.013 ms; 122 stale | Đo số người, batch count, normalized crop count, CPU preprocess và GPU time theo frame; thử A/B cùng video |
| 1 | Chất lượng crop/reference và góc nhìn gây score không bền | Reference đổi giữa phiên; score cuối có nhiều mẫu dưới 0,65; mất/bắt lại liên tục | Gắn nhãn đúng người, thống kê score đúng/sai theo ánh sáng/góc/kích thước; chưa giảm threshold hàng loạt |
| 1 | Pan/tilt chiếm quyền khiến zoom bị trì hoãn | 86 OWNER_pan_tilt; 2 zoom kết thúc do PAN_TILT_CENTERING | Đo thời gian ownership, tâm bbox, pan/tilt pulse/settle, starvation; A/B điều chỉnh lịch điều khiển |
| 2 | Motion camera gây đổi bbox/ID/crossing, dẫn tới verify/recovery nhiều | ByteTrack + kiểm tra IoU/overlap và motor di chuyển | Ghi trigger cụ thể của due, bbox trước/sau, camera motion và ID switch trên ground truth |
| 2 | Zoom theo xung chưa hiệu chuẩn gây nhiều OUT hoặc đổi chiều | 24 OUT, 3 IN; không có lens position | Xem ratio trước/sau từng xung, kích thước mục tiêu mong muốn, pulse duration và hysteresis; nhiều OUT tự nó không chứng minh lỗi |
| 2 | Frame freshness/RTSP lifecycle là nguồn gián đoạn | DISCONNECTED lúc 13:35:38; frame counter gần như ngừng sau đó | Đối chiếu thao tác GUI và shutdown/reconnect path, exception/reason capture; chưa kết luận mất mạng |
| 3 | Logging hoặc tranh tài nguyên CPU/GPU góp phần gây spike | Nhiều event, CPU một mẫu cao, scheduler một accelerator lane | A/B profiling mức thấp/cao trên cùng workload; lấy GPU utilization, CUDA events, CPU profiles; queue camera hiện không phải nút thắt rõ ràng |

Không có đủ ground truth để xác nhận mỗi lần lock đều đúng người, mức zoom thực tế hay pan/tilt có đưa mục tiêu về tâm đúng hướng.

## 8. Đề nghị AI phân tích và kế hoạch tối ưu

Có thể gửi nguyên tài liệu này kèm yêu cầu:

> Hãy phân tích pipeline AI PTZ trong báo cáo. Tách rõ sự kiện đã chứng minh, giả thuyết và dữ liệu thiếu. Ưu tiên nguyên nhân làm mất bám/đổi quyền motor trước tối ưu tốc độ thuần. Đề xuất thay đổi nhỏ có thể A/B, nêu file/hàm cần xem, tác động dự kiến, rủi ro sai người, metric đánh giá và cách rollback. Không kết luận từ ACK rằng motor đã di chuyển đúng, không coi dropped frame là packet loss, không hạ threshold ReID chỉ để tăng số lần lock. Chú ý ảnh mục tiêu đã đổi, 122 stale là counter còn 9 là số dòng log, và FPS cuối phiên chứa thời gian không có video.

Dữ liệu cần bổ sung để xác minh:
1. Video replay cùng cảnh có ground truth identity, occlusion và camera movement; ảnh reference thực dùng sau thay đổi.
2. Snapshot cấu hình hiệu lực/toggle theo timestamp; hash model, code revision, Python/package versions, CPU/GPU/driver/CUDA.
3. Event verify ghi rõ trigger: periodic, missing ID, overlap, low IoU, hold; raw/fused score, top2, margin và threshold thực dùng cho mỗi quyết định.
4. Correlation end-to-end theo frame_id, track_id, acquisition_id, lock_session_id, command_id; command arguments, returned ok/error và ACK correlation.
5. Capture disconnect reason, reconnect attempt/outcome, thao tác GUI lân cận; không chỉ timestamp ngắt.
6. Đo GPU timing và CPU preprocessing riêng; giữ clip/workload giống nhau giữa baseline và mỗi biến thể.

Metric nghiệm thu đề xuất: false lock/ID switch; thời gian acquisition/reacquire p50/p95; thời gian giữ đúng mục tiêu; stale rate trên mẫu số xác định; detection FPS khi stream hoạt động; observation age p95/p99; thời gian ownership pan/tilt/zoom; command ACK/failure; độ lệch tâm và ratio sau zoom; số lần đổi chiều; thời gian có mặt đủ chất lượng. Chốt baseline trước, thay từng nhóm tham số, giữ khả năng rollback.

## 9. Nguồn để đối chiếu

Đường dẫn tương đối từ thư mục `ptz/`:

- `logs/ptz_20261110_132751.log`: startup, thay target, state, trial, shutdown.
- `logs/ptz_20261110_132751.jsonl`: event AI, zoom start/stop, frame metrics.
- `logs/camera/camera_control_20261110_132755_098020.log` cùng `.1`, `.2`: toàn chuỗi camera transaction và zoom intent/ACK/end.
- `logs/latency/latency_trace_20261110_132755_098020.jsonl` cùng `.1`, `.2`, `.3`: trace còn lưu; **đã rotate nên không bảo đảm còn đầu phiên**. Việc không thấy một event ở tập trace còn lại không chứng minh event chưa từng xảy ra.
- `logs/latency/latency_summary_20261110_132755_098020.csv`: latency snapshot.
- `docs/live_camera_check_20261110.md`: phép kiểm tra ban đầu, phạm vi hạn chế.
- `camera.json`, `config.py`, `requirements.txt`: cấu hình và dependency khai báo.
- `person_search/reid.py`, `ai/reid/gallery.py`: preprocessing, embedding, similarity, normalization/fusion.
- `ai/reid_target.py`, `ai/hybrid_reid.py`: acquire/verify/recovery và chuyển trạng thái.
- `runtime/byte_tracking.py`, `runtime/inference_worker.py`, `runtime/scheduling.py`: tracker, orchestration và tài nguyên inference.
- `camera/hanet_controller.py`, `camera/hanet_rpc.py`, `runtime/camera_worker.py`: camera backend và queue/ACK.
- `control/auto_zoom.py`, `control/manual.py`: zoom policy/dispatch và điều khiển tay.

Báo cáo không đính kèm mật khẩu, ảnh nhận diện hoặc danh sách tên trong face database. Các link/đường dẫn nguồn dùng để tìm trong dự án; nội dung chính đã được đưa trực tiếp vào tài liệu để gửi độc lập.


## Cập nhật triển khai sau báo cáo

Đã sửa trực tiếp continuity/verification và bổ sung test ngày 11/11/2026. Xem [audit, thay đổi và giới hạn kiểm chứng](tracking_continuity_20261111/implementation.md) và [kết quả test](tracking_continuity_20261111/results.md). Nội dung hiện trạng phía trên được giữ để phân biệt bằng chứng trước/sau sửa; chưa có phép đo camera mới.
