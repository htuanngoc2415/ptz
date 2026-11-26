# Audit RTSP latency và CPU/NVDEC — 22/10/2026

Đo trên Jetson tại chỗ, giờ Việt Nam (UTC+07). Báo cáo này phân biệt **benchmark mới**, **trace production lịch sử**, và **giả thuyết**. Không sửa camera.json, pipeline production, model, threshold hoặc thuật toán điều khiển. Không tạo PTZ controller, gọi camera RPC hay điều khiển motor trong các phép đo mới.

**Ưu tiên đề xuất: GStreamer CPU low-latency 200 ms để thay mpv screenshot, sau acceptance full desktop.** CPU candidate cấp gần 25 FPS và giữ ~4,97 vòng AI/s trong hai lượt stress đủ bốn model; NVDEC no-DPB cấp gần 25 FPS, giảm CPU/lateness nhưng chỉ ~4,18–4,32 vòng AI/s (giảm 13–16%). Chưa triển khai phương án nào vào production.

![So sánh hai lượt matched workload cho mỗi candidate](../logs/decoder_audit/fixed_workload_comparison.png)

## Kết luận kỹ thuật

**Đã tái hiện tích lũy trễ trước AI trên đường capture đang được cấu hình: mpv CPU → scale → screenshot.** Cấu hình thực tế là `video_backend=mpv`, `video_hwdec=no`, không phải GStreamer. Camera phát **H.264 High 3840×2160, 25 FPS**. `video_max_width=1280`, `video_max_height=720`, `video_fps=15` chỉ giới hạn đầu ra lấy ảnh phía mpv; không làm camera phát 720p15 và không giảm khối lượng giải mã nguồn.

- [A_mpv](../logs/decoder_audit/A_mpv.json): yêu cầu 60.0 s, khoảng PTS hợp lệ **58.532 s**, video tiến **50.880 s**, tăng trễ tương đối **7.652 s**; screenshot p50/p90/p99 **76.868 / 89.595 / 180.150 ms**.

- [A_mpv_repeat](../logs/decoder_audit/A_mpv_repeat.json): yêu cầu 60.0 s, khoảng PTS hợp lệ **58.477 s**, video tiến **50.880 s**, tăng trễ tương đối **7.597 s**; screenshot p50/p90/p99 **81.233 / 107.449 / 155.221 ms**.


Các pipeline GStreamer thử nghiệm cho thấy đường nhận có thể giữ PTS gần thời gian thực với cùng main stream 4K. Vì vậy chưa có cơ sở kết luận “CPU decoding luôn gây trễ 5–10 s”, hoặc “NVDEC dùng CUDA cores nên làm AI chậm”. NVDEC đã giải mã thực, xuất `video/x-raw(memory:NVMM), format=NV12`; không có decoder CPU dự phòng trong pipeline thử nghiệm.

**Chưa đạt điều kiện triển khai NVDEC vào full AI.** Lượt `nvdec_dpb_100_ai` dừng sau 29,312 s khi ArcFace thực sự có mặt để xử lý: `CUBLAS_STATUS_ALLOC_FAILED`, kèm NvMap allocation error. Video vẫn ~25 FPS trước khi dừng; AI không còn khỏe. Đây là lỗi cấp phát có log chứng minh, chưa xác định do tổng memory budget, fragmentation, allocator/provider hay NVDEC surfaces. [Console đã redact](../logs/decoder_audit/nvdec_dpb_100_ai_console.txt). Không thay allocator/model hoặc tắt bớt model để biến lượt thất bại thành thành công.

**Camera→màn hình tuyệt đối vẫn N/A.** PTS drift đo mức trễ tăng thêm, không đo trễ ban đầu. Timestamp FrameManager nằm sau giải mã/copy. Những số receive→GUI thấp không phủ nhận video đã cũ từ upstream. Kết quả này xác định đường gây trễ hiện tại; không xác định được thời điểm hay commit gây regression trước/sau profiling vì không có benchmark đối chứng tương ứng.

## 1. Hardware, codec và phạm vi đo

| Hạng mục | Kết quả |
|---|---|
| Board | NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super |
| CPU / GPU | 6× Cortex-A78AE; CUDA báo GPU Orin; snapshot CPU max 1728 MHz |
| RAM / swap lúc preflight | 7619 MiB RAM, available 3058 MiB; swap used 1512 MiB; không coi swap đã dùng là bằng chứng đang thrash |
| JetPack / L4T / OS | 6.2.1+b38 / 36.4.7 / Ubuntu 22.04.5, kernel 5.15.148-tegra |
| Power mode | MAXN_SUPER (chỉ đọc, không đổi clocks/power) |
| GStreamer / PyTorch | 1.20.3 / 2.8.0, CUDA 12.6; thử cấp phát CUDA thành công |
| CPU decoders | avdec_h264, avdec_h265 có sẵn; chỉ H.264 được benchmark |
| NVIDIA plugins | nvv4l2decoder, nvvidconv, nvvideoconvert có sẵn; decode thực qua nvv4l2decoder; chỉ nvvidconv được benchmark chuyển đổi |
| Main stream | H.264 High, level 5.1 từ caps, 3840×2160 @ 25/1, 8-bit 4:2:0 |
| B-frames / GOP mẫu | ffprobe: has_b_frames=0; 131 frames có 6 I, 125 P, 0 B; keyframes cách 25 frames (~1 s). Không bảo đảm cấu hình camera sẽ luôn giữ như vậy |
| Ethernet / ping mới | 100 Mb/s Full Duplex; 5 ping, 0% loss, RTT min/avg/max 0.322/1.390/4.295 ms; mẫu ngắn không loại trừ mọi jitter/TCP stall |
| Đồng thời | Các probe camera chạy nối tiếp; preflight không thấy ứng dụng camera Python khác. Không quan sát được mọi client trên máy khác; desktop/IDE vẫn chạy |

Evidence: [host](../logs/decoder_audit/host.json), [plugins/CUDA](../logs/decoder_audit/decoder_cuda_preflight.json), [network](../logs/decoder_audit/network.json), [ffprobe](../logs/decoder_audit/stream_ffprobe.json). Plugin dumps nằm cạnh JSON. Sandbox ban đầu thiếu socket và `/dev/nvmap`; đã đo lại trên host có quyền truy cập. Các lỗi sandbox **không phải lỗi hardware production**.

NVDEC là khối giải mã chuyên dụng; `nvvidconv` mặc định dùng VIC trên Jetson (plugin hiện tại ghi `compute-hw=Default, VIC for Jetson`). CUDA preprocessing và inference là công việc khác. Chuyển NVMM sang buffer CPU vẫn có chuyển layout/copy/synchronization và tiêu thụ băng thông RAM chung; không đồng nghĩa PCIe transfer của GPU rời. [NVIDIA Accelerated GStreamer](https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/Multimedia/AcceleratedGstreamer.html).

## 2. Kiến trúc trích từ code

```text
camera.json → camera/stream.py:4 chọn mpv
RTSP TCP → libmpv/FFmpeg CPU (hwdec=no, vo=null)
  → lavfi scale ≤1280×720 → screenshot_raw → NumPy RGB
  → GUI.on_frame → FrameManager (1 slot, latest-only)
      ├─ detection-tracking thread: RGB→BGR → YOLO → OSNet/tracking
      │    └─ FaceWorker: spatial + recognition threads / scheduler
      └─ Tk timer: latest RGB → BGR → digital PTZ/overlay
                   → RGB → PIL/ImageTk → Tk image configure
```

`camera/mpv_player.py:67–120`: `profile=low-latency`, `untimed`, `cache=no`, `fflags=nobuffer`, `flags=low_delay`, `reorder_queue_size=0`, analyzeduration=0.1. Screenshot và query `time_pos` là lời gọi đồng bộ vào player, có thể chờ công việc nội bộ. Chi phí screenshot không chỉ là memcpy. Backend này **không có rtspsrc/appsink**.

Nhánh GStreamer có trong repository nhưng không được camera.json hiện tại chọn:

```text
playbin → tự chọn source/depay/parser/decoder bằng autoplug
  → queue leaky=downstream max-size-buffers=2
  → videoscale → video/x-raw width≤1280 height≤720 PAR=1/1
  → videoconvert → RGB
  → appsink emit-signals=true sync=false max-buffers=1 drop=true
  → map → np.frombuffer → reshape.copy → on_frame
```

`camera/rtsp_player.py:175,203,321,424`: source-setup đặt latency từ `latency_ms` (mặc định 200 ms), `drop-on-latency=true`, **TCP bắt buộc protocols=4**. `transport` trong config không thay lựa chọn TCP của nhánh này. Queue giữ default giới hạn bytes/time ngoài giới hạn 2 buffers; tại 4K giới hạn bytes có thể chạm trước. `video_fps=15` không được dùng bởi nhánh GStreamer. Decoder của playbin **không được code cố định**: không được suy ra CPU/NVDEC chỉ từ tên class hoặc plugin đã cài. Graph của production playbin chưa chạy trong audit; các benchmark dưới đây chọn decoder tường minh.

Appsink chỉ giới hạn hàng đợi của nó. `new-sample` chạy trên streaming thread; callback chậm vẫn chặn thread đó. Queue raw sau decoder tách luồng nhưng không làm decoder, jitterbuffer, TCP socket hay camera encoder tự hết backlog. Chỉ drop sau decode để không tùy tiện bỏ reference frames H.264/H.265; drop-on-latency ở jitterbuffer vẫn có thể bỏ RTP và ảnh hưởng tính liên tục của bitstream. [GStreamer appsink](https://gstreamer.freedesktop.org/documentation/app/appsink.html), [queue](https://gstreamer.freedesktop.org/documentation/coreelements/queue.html), [rtspsrc](https://gstreamer.freedesktop.org/documentation/rtsp/rtspsrc.html).

`runtime/frame_manager.py:20`: một slot, không có FIFO frame cũ; publish và latest dùng lock ngắn nhưng emit telemetry bên trong lock. Callback `gui/cctv_app.py:975` chỉ publish và cập nhật tham chiếu; không gọi inference. `runtime/inference_worker.py:295,369` lấy frame mới nhất, refresh lần nữa sau khi được scheduler cấp quyền, bỏ stale observation. Scheduler ưu tiên detection, rồi face, rồi recognition; đây là nơi có thể gây AI wait, không phải RTSP queue. GUI lấy frame mới nhất độc lập với AI result; overlay có thể phản ánh quan sát cũ hơn ảnh.

**Copy/memory boundary:** GStreamer production có ít nhất một `.copy()` RGB sau map; PyGI `map_info.data` có thể materialize bytes, chưa đo số copy nội bộ. mpv screenshot tạo ảnh host; `np.asarray` không bảo đảm toàn bộ chuỗi zero-copy. GUI và AI có RGB↔BGR conversions riêng. YOLO có upload tensor và trả detection về CPU; OSNet upload mỗi batch, giữ embedding GPU rồi match chuyển score về CPU; SCRFD/ArcFace nhận NumPy qua ORT và trả output host. Số transfers phụ thuộc số batch/crop/face, không phải hằng số mỗi frame. Chưa có CUDA/Nsight trace để đếm bytes/copy chính xác. NVDEC candidate thêm NVMM→host BGRx trước RGB; không phải zero-copy tới AI.

## 3. Protocol và bảng A–E

A là capture **đang cấu hình thật (mpv)**, AI OFF. Các lượt GStreamer có 5 s warmup sau frame đầu, rồi **60 s đo**, source/codec/resolution giữ nguyên, đầu ra RGB 1280×720; không giới hạn còn 15 FPS. A/mpv dùng công cụ cũ, 60 s gồm startup, khoảng PTS hợp lệ báo riêng. Không lấy callback FPS của mpv làm source FPS.

`--ai` / `--ai-load` chỉ dùng lại model wrappers và PersonTracker trong worker latest-frame độc lập. Có YOLO, OSNet trên người phát hiện được, SCRFD full-frame, ArcFace khi có face align hợp lệ. Không có target gallery matching, production FaceWorker scheduler/gates, GUI hoặc auto zoom. **Đây là phép đo tải perception bổ sung (B′/C′/E′), không được trình bày như benchmark full production B/C/E.** Tải phụ thuộc cảnh; phải đọc call counts. Profiler bật khi AI ON, tắt ở capture OFF, nên chênh lệch bao gồm overhead profiler. Không gọi GPU synchronization mới để tạo số kernel time giả.

Trong B′/C′ và NVDEC mặc định AI ON, live scene không có người/mặt được model phát hiện: **OSNet/ArcFace = 0 inference calls**, dù model đã được nạp. Lượt NVDEC no-DPB live có 24 OSNet calls rồi ArcFace gặp lỗi. Vì vậy các lượt live không phải matched full-model workload. Các run `*_fixed` là **stress test riêng**, AI dùng ảnh target có sẵn, letterbox 1280×720, yêu cầu phát hiện được người và mặt align hợp lệ trước khi chạy. Capture vẫn đọc cùng RTSP, nhưng nội dung AI cố định để workload so sánh được; không đánh giá tracking/recognition accuracy hay sensor latency. Các run `*_fixed` không thay thế B/C/E production.

| Test yêu cầu | Trạng thái / evidence | Giới hạn |
|---|---|---|
| A: CPU hiện tại, AI OFF | MEASURED — A_mpv | Không GUI; drift, screenshot và resource |
| B: CPU hiện tại, full AI ON | NOT_TESTED; B′: measured | Có tải perception riêng nếu khởi tạo được; không full production |
| C: CPU low latency, full AI ON | NOT_TESTED; C′: PASS | CPU 200 ms + perception riêng |
| D: NVDEC low latency, AI OFF | MEASURED — nvdec_50/100/200 và DPB candidate | Không GUI |
| E: NVDEC low latency, full AI ON | NOT_TESTED; E′: FAILED | Perception riêng; cần xác nhận production trước triển khai |


| MPV run | AI | s yêu cầu | s PTS hợp lệ | Callbacks | Callback FPS ≈ | PTS/wall | Drift tăng s | Screenshot ms p50/p90/p99 |
|---|---|---|---|---|---|---|---|---|
| [A_mpv](../logs/decoder_audit/A_mpv.json) | OFF | 60.0 | 58.532 | 496 | 8.267 | 0.869 | 7.652 | 76.868 / 89.595 / 180.150 |
| [A_mpv_repeat](../logs/decoder_audit/A_mpv_repeat.json) | OFF | 60.0 | 58.477 | 472 | 7.867 | 0.870 | 7.597 | 81.233 / 107.449 / 155.221 |
| [B_mpv_ai](../logs/decoder_audit/B_mpv_ai.json) | isolated_perception | 60.0 | 58.601 | 492 | 8.200 | 0.848 | 8.921 | 77.809 / 94.139 / 180.561 |


Callback FPS của mpv = count / thời gian yêu cầu, gồm startup; không phải decoder FPS hay steady-state FPS tương đương cửa sổ GStreamer. PTS được hỏi từ player; chưa có per-screenshot source timestamp để chứng minh thời điểm capture của từng RGB frame.

### GStreamer capture — số đo mới

FPS parser/decoder/appsink là số buffers qua từng boundary trong cửa sổ đo. Có thể chênh một buffer do ranh giới cửa sổ. Bitrate là payload compressed qua parser, không gồm RTP/TCP/IP overhead. Drift âm rất nhỏ là sai khác timestamp/nhịp lấy mẫu, không phải latency âm.

| Run | Status | AI | s đo | Parser / decoder / app FPS | Mbit/s | Drift tăng (s) | Parser→app p50/p90/p99 (ms) |
|---|---|---|---|---|---|---|---|
| [cpu_50](../logs/decoder_audit/cpu_50.json) | PASS | OFF | 60.000 | 22.917/22.933/22.933 | 11.891 | 0.093 | 92.408 / 107.276 / 258.809 |
| [cpu_100](../logs/decoder_audit/cpu_100.json) | PASS | OFF | 60.000 | 23.850/23.850/23.833 | 12.199 | -0.008 | 88.714 / 106.017 / 262.851 |
| [cpu_200](../logs/decoder_audit/cpu_200.json) | PASS | OFF | 60.000 | 24.967/24.967/24.983 | 12.568 | 0.076 | 51.852 / 57.523 / 169.873 |
| [nvdec_50](../logs/decoder_audit/nvdec_50.json) | PASS | OFF | 60.000 | 25.000/25.000/22.967 | 12.557 | 0.000 | 223.956 / 234.810 / 283.671 |
| [nvdec_100](../logs/decoder_audit/nvdec_100.json) | PASS | OFF | 60.000 | 25.000/25.000/23.000 | 12.563 | 0.000 | 224.174 / 234.716 / 284.784 |
| [nvdec_200](../logs/decoder_audit/nvdec_200.json) | PASS | OFF | 60.000 | 25.000/25.000/22.950 | 12.559 | -0.009 | 224.237 / 234.467 / 284.822 |
| [nvdec_dpb_100](../logs/decoder_audit/nvdec_dpb_100.json) | PASS | OFF | 60.000 | 25.000/25.000/25.000 | 12.561 | -0.004 | 50.614 / 84.252 / 106.622 |
| [cpu_200_ai](../logs/decoder_audit/cpu_200_ai.json) | PASS | isolated_perception | 60.000 | 25.000/25.000/25.000 | 12.551 | -0.003 | 86.193 / 106.093 / 255.902 |
| [nvdec_100_ai](../logs/decoder_audit/nvdec_100_ai.json) | PASS | isolated_perception | 60.000 | 25.000/25.000/22.983 | 12.560 | -0.013 | 224.330 / 234.819 / 282.155 |
| [nvdec_dpb_100_ai](../logs/decoder_audit/nvdec_dpb_100_ai.json) | FAILED | isolated_perception | 29.312 | 25.041/25.007/24.973 | 12.495 | -0.010 | 50.542 / 80.703 / 109.653 |
| [cpu_200_fixed](../logs/decoder_audit/cpu_200_fixed.json) | PASS | isolated_perception | 60.000 | 25.000/25.000/25.000 | 12.555 | -0.001 | 86.421 / 104.919 / 258.709 |
| [nvdec_dpb_100_fixed](../logs/decoder_audit/nvdec_dpb_100_fixed.json) | PASS | isolated_perception | 60.000 | 25.017/24.983/25.000 | 12.573 | -0.012 | 51.461 / 83.651 / 106.469 |
| [nvdec_dpb_100_fixed_repeat](../logs/decoder_audit/nvdec_dpb_100_fixed_repeat.json) | PASS | isolated_perception | 60.000 | 25.000/24.983/25.000 | 12.533 | -0.001 | 50.734 / 83.185 / 108.459 |
| [cpu_200_fixed_repeat](../logs/decoder_audit/cpu_200_fixed_repeat.json) | PASS | isolated_perception | 60.000 | 25.000/25.000/25.000 | 12.578 | 0.007 | 94.811 / 110.423 / 208.054 |
| [nvdec_dpb_100_jitter](../logs/decoder_audit/nvdec_dpb_100_jitter.json) | PASS | OFF | 60.000 | 25.000/25.000/25.017 | 12.550 | 0.003 | 51.271 / 90.572 / 122.202 |

### Model load — supplemental, không phải production

| Run | Calls / skipped | YOLO total ms | OSNet total ms | SCRFD total ms | ArcFace total ms | App age ms |
|---|---|---|---|---|---|---|
| [B_mpv_ai](../logs/decoder_audit/B_mpv_ai.json) (live_frames) | {"mailbox_skipped": 15, "yolo": 471, "tracking": 471, "scrfd": 471} | 49.910 / 65.335 / 77.479 | N/A | 44.205 / 52.936 / 61.650 | N/A | 5.024 / 23.517 / 84.131 |
| [cpu_200_ai](../logs/decoder_audit/cpu_200_ai.json) (live_frames) | {"mailbox_skipped": 917, "yolo": 583, "tracking": 583, "scrfd": 583} | 50.188 / 73.803 / 84.051 | N/A | 42.320 / 47.301 / 51.311 | N/A | 16.334 / 33.697 / 91.585 |
| [nvdec_100_ai](../logs/decoder_audit/nvdec_100_ai.json) (live_frames) | {"tracking": 548, "scrfd": 548, "mailbox_skipped": 832, "yolo": 547} | 62.815 / 79.005 / 84.916 | N/A | 44.341 / 48.576 / 57.183 | N/A | 20.083 / 53.733 / 97.386 |
| [nvdec_dpb_100_ai](../logs/decoder_audit/nvdec_dpb_100_ai.json) (live_frames) | {"tracking": 247, "scrfd": 247, "mailbox_skipped": 481, "yolo": 246, "osnet": 24} | 60.351 / 79.622 / 85.273 | 82.428 / 101.387 / 294.060 | 45.125 / 51.016 / 58.714 | N/A | 19.558 / 43.059 / 96.007 |
| [cpu_200_fixed](../logs/decoder_audit/cpu_200_fixed.json) (fixed_image_stress) | {"tracking": 299, "osnet": 299, "scrfd": 298, "arcface": 298, "mailbox_skipped": 1199, "yolo": 298} | 50.490 / 65.786 / 80.195 | 69.535 / 79.164 / 96.093 | 43.680 / 46.805 / 55.341 | 33.291 / 35.216 / 38.962 | 18.523 / 56.903 / 187.482 |
| [nvdec_dpb_100_fixed](../logs/decoder_audit/nvdec_dpb_100_fixed.json) (fixed_image_stress) | {"arcface": 259, "mailbox_skipped": 1245, "yolo": 259, "tracking": 259, "osnet": 259, "scrfd": 258} | 65.284 / 82.122 / 92.450 | 81.527 / 101.244 / 111.382 | 45.519 / 54.990 / 61.689 | 33.507 / 36.268 / 46.659 | 20.566 / 47.127 / 112.868 |
| [nvdec_dpb_100_fixed_repeat](../logs/decoder_audit/nvdec_dpb_100_fixed_repeat.json) (fixed_image_stress) | {"mailbox_skipped": 1253, "yolo": 251, "tracking": 251, "osnet": 251, "scrfd": 251, "arcface": 250} | 68.458 / 85.906 / 93.886 | 84.182 / 106.464 / 115.291 | 45.695 / 57.893 / 65.840 | 33.476 / 43.426 / 49.505 | 19.819 / 42.535 / 110.997 |
| [cpu_200_fixed_repeat](../logs/decoder_audit/cpu_200_fixed_repeat.json) (fixed_image_stress) | {"mailbox_skipped": 1206, "yolo": 298, "tracking": 297, "osnet": 297, "scrfd": 297, "arcface": 297} | 51.096 / 62.856 / 79.299 | 69.548 / 77.429 / 95.975 | 44.030 / 47.441 / 55.715 | 33.679 / 35.729 / 40.440 | 19.054 / 40.919 / 126.778 |


| Fixed input stress — thứ tự chạy | Status | s đo | YOLO loops/s | CPU % p50 | GPU MHz p50/p90/p99 | NVDEC MHz p50/p90/p99 | VIC MHz p50/p90/p99 |
|---|---|---|---|---|---|---|---|
| cpu_200_fixed | PASS | 60.000 | 4.967 | 234.129 | 408.000 / 816.000 / 816.000 | 115.200 / 115.200 / 115.200 | 115.200 / 115.200 / 115.200 |
| nvdec_dpb_100_fixed | PASS | 60.000 | 4.317 | 120.017 | 408.000 / 530.400 / 816.000 | 307.200 / 371.200 / 402.048 | 128.000 / 179.200 / 197.248 |
| nvdec_dpb_100_fixed_repeat | PASS | 60.000 | 4.183 | 118.359 | 408.000 / 816.000 / 816.000 | 320.000 / 384.000 / 422.400 | 140.800 / 179.200 / 207.744 |
| cpu_200_fixed_repeat | PASS | 60.000 | 4.967 | 237.470 | 408.000 / 510.000 / 816.000 | 115.200 / 115.200 / 115.200 | 115.200 / 115.200 / 115.200 |


Hai run fixed đầu tiên có cùng input và đủ bốn model: CPU 298 YOLO calls/60 s (~4,967/s), NVDEC 259/60 s (~4,317/s), tức throughput workload này thấp hơn ~13,1% với NVDEC. Đây là kết quả đo, không phải ước tính full production. GPU clock mean của hai run lần lượt 486,2 và 443,7 MHz, nên DVFS là confounder thực; CPU/RAM/VIC traffic và Python probes cũng chưa được tách. Không khóa clocks hoặc đổi power mode. Lượt lặp theo thứ tự ngược ở bảng trên dùng để kiểm tra tính ổn định, không gộp percentile của các run. Capture CPU/NVDEC còn khác conversion path và jitter setting; đây là so sánh candidate pipelines, không cô lập execution cost của decoder silicon.

Lượt lặp NVDEC→CPU ghi 251/60=4,183 và 298/60=4,967 vòng/s: NVDEC thấp hơn ~15,8%. Xu hướng không đảo khi đổi thứ tự chạy. Chỉ có hai lượt mỗi candidate trên một host/cảnh stress; không suy ra confidence interval hay chất lượng AI production.

Việc fixed-image chạy được ArcFace không xóa lỗi cấp phát ở lượt live. Scene/crop batches và thời điểm lazy allocation khác nhau. Cần stress live có người/mặt xuất hiện muộn, nhiều người, target đổi và đủ worker production trước acceptance.


**Breakdown model stress từ profiler sẵn có:** toàn bộ trace giữ lại, gồm initialization/warmup; scope khác cửa sổ 60 s ở bảng total phía trên. Mỗi ô p50/p90/p99 ms; không gộp percentile giữa run. Host wall spans không phải CUDA kernel time. [JSON và sample counts](../logs/decoder_audit/fixed_inference_breakdown.json).

| Metric | cpu_200_fixed | nvdec_dpb_100_fixed | nvdec_dpb_100_fixed_repeat | cpu_200_fixed_repeat |
|---|---|---|---|---|
| yolo_preprocess_ms | 3.954 / 5.524 / 7.211 | 5.029 / 6.589 / 8.128 | 5.171 / 6.704 / 9.005 | 4.033 / 5.370 / 7.387 |
| yolo_inference_ms | 39.169 / 52.283 / 65.448 | 52.479 / 66.789 / 79.839 | 54.914 / 69.374 / 78.453 | 39.544 / 50.443 / 66.028 |
| yolo_postprocess_ms | 1.893 / 3.142 / 5.092 | 2.461 / 4.503 / 6.988 | 2.556 / 4.271 / 7.806 | 1.983 / 3.514 / 5.018 |
| reid_preprocess_ms | 5.084 / 7.651 / 9.568 | 6.842 / 9.831 / 13.225 | 7.021 / 9.680 / 13.481 | 5.361 / 7.558 / 10.162 |
| reid_inference_ms | 60.453 / 69.917 / 86.283 | 70.498 / 88.092 / 100.524 | 73.845 / 92.446 / 101.397 | 60.236 / 66.889 / 83.152 |
| reid_postprocess_ms | 1.897 / 3.145 / 4.315 | 2.180 / 3.644 / 4.925 | 2.149 / 3.656 / 5.373 | 1.858 / 3.219 / 5.060 |
| scrfd_preprocess_ms | 3.197 / 3.888 / 5.690 | 3.469 / 4.211 / 6.364 | 3.553 / 4.202 / 6.330 | 3.147 / 4.090 / 6.393 |
| scrfd_inference_ms | 36.354 / 38.792 / 48.735 | 37.703 / 46.916 / 52.485 | 37.569 / 49.152 / 57.859 | 36.493 / 39.633 / 48.207 |
| scrfd_postprocess_ms | 2.618 / 3.718 / 6.922 | 3.206 / 4.857 / 7.715 | 3.206 / 4.940 / 6.768 | 2.673 / 3.721 / 5.992 |
| arcface_preprocess_ms | 0.388 / 0.628 / 1.054 | 0.491 / 0.786 / 1.301 | 0.498 / 0.779 / 2.026 | 0.390 / 0.601 / 1.454 |
| arcface_inference_ms | 31.965 / 33.562 / 38.939 | 32.047 / 34.420 / 45.070 | 32.045 / 41.798 / 47.692 | 32.178 / 34.242 / 40.692 |
| arcface_postprocess_ms | 0.251 / 0.564 / 0.972 | 0.279 / 0.364 / 0.880 | 0.288 / 0.380 / 1.032 | 0.250 / 0.611 / 0.929 |


YOLO inference host p50 CPU ~39 ms, NVDEC ~52–55 ms; OSNet ~60 so với ~70–74 ms. ArcFace ~32 ms ở cả hai. Có regression theo model được log chứng minh, nhưng chưa tách được DVFS, memory traffic, host scheduling và async CUDA; GPU load riêng lẻ không đủ quy kết nguyên nhân.


Các total host wall time gồm preprocessing/postprocessing; không so trực tiếp với inference-only của trace lịch sử. Trace `*_trace/` dùng profiler sẵn có để tách từng model/preprocess/postprocess/scheduler nếu đã chạy. Không gán giảm/tăng inference cho NVDEC khi workload/call counts, warmup, background load hoặc GUI chưa được giữ tương đương.

## 4. Latency breakdown theo boundary

Đơn vị ms, mỗi ô p50 / p90 / p99. Các span inclusive, overlapping và các percentile **không cộng được**. Thời gian decoder là residence theo matching PTS, gồm đợi/reorder/frame threading; không phải execution time riêng của NVDEC hoặc CPU.

| Boundary | cpu_200 | nvdec_100 | nvdec_dpb_100 |
|---|---|---|---|
| RTP cuối cùng cùng PTS → depay | 0.073 / 0.088 / 0.915 | 0.115 / 0.139 / 0.969 | 0.112 / 0.158 / 0.980 |
| Depay → parser AU | 0.077 / 0.087 / 0.273 | 0.147 / 0.176 / 0.984 | 0.144 / 0.209 / 0.937 |
| Decoder sink → src, cùng PTS | 28.817 / 33.232 / 143.928 | 200.750 / 210.525 / 260.267 | 26.116 / 58.765 / 76.094 |
| Raw queue wait | 0.178 / 0.382 / 2.500 | 0.155 / 0.348 / 46.864 | 0.188 / 11.090 / 24.954 |
| Resize; NVDEC gồm VIC + download/layout | 9.241 / 10.548 / 12.339 | 17.941 / 18.458 / 18.875 | 17.877 / 18.413 / 18.802 |
| Color conversion → RGB | 11.804 / 13.237 / 15.316 | 4.894 / 4.973 / 5.189 | 4.882 / 4.966 / 5.218 |
| Map call | 0.023 / 0.026 / 0.030 | 0.027 / 0.030 / 0.048 | 0.026 / 0.030 / 0.068 |
| Host image materialization/copy + unmap | 0.897 / 0.950 / 1.028 | 1.096 / 1.168 / 1.404 | 1.089 / 1.177 / 1.411 |
| FrameManager.publish | 0.115 / 0.138 / 0.167 | 0.152 / 0.177 / 0.261 | 0.150 / 0.174 / 0.243 |
| Appsink callback → publish xong | 1.151 / 1.233 / 1.356 | 1.414 / 1.513 / 1.855 | 1.414 / 1.534 / 1.834 |
| Parser → app (inclusive) | 51.852 / 57.523 / 169.873 | 224.174 / 234.716 / 284.784 | 50.614 / 84.252 / 106.622 |
| Pipeline running clock − buffer running PTS | 95.389 / 200.805 / 233.550 | 230.038 / 239.324 / 291.134 | 59.224 / 107.699 / 148.807 |


Run jitter riêng: **66325 RTP packets**, jitterbuffer residence p50/p90/p99 **0.497 / 4.786 / 13.566 ms**. Process CPU p50 trong run thêm probes là **58.193%**, so với khoảng 35% ở candidate no-DPB không có extra jitter probes. Đây là observer overhead đáng lưu ý; không trộn run này vào ranking CPU overhead của các pipeline chính.


**Socket/RTSP transport residence: N/A.** Các lượt chính bắt đầu tại depay sink, tức sau jitterbuffer; `latency=50/100/200` là cấu hình, không phải số đo residence. Run riêng `nvdec_dpb_100_jitter` thêm hai pad probes của rtpjitterbuffer, ghép theo SSRC + RTP sequence để đo jitterbuffer sink→src cho video. Nó bao gồm đợi/scheduling bên trong receiver, không gồm thời gian mạng trước jitter sink; extra per-packet probes có observer overhead. Jitter stats avg-jitter không được đổi thành camera latency. Depay span bắt đầu ở RTP packet cuối cùng cùng PTS, không đo thời gian chờ nhận trọn frame. Decoder matching PTS loại mẫu không có cặp và mẫu bắt đầu trước cửa sổ đo; caps/source không có capture timestamp đồng bộ. Pipeline clock−PTS là lateness phía receiver; không bao gồm camera capture/encode tuyệt đối. Pure NVMM map/download và VIC processing chưa tách riêng; ghi chung đúng boundary đo được.

### FrameManager, AI scheduler và GUI — trace production có sẵn

Trace 22/10 lúc 09:50:03 cùng các rotation, **20.130 events**, được dựng lại bằng profiler hiện có: [HTML tương tác](../logs/decoder_audit/historical_095003/report.html). Đây là phiên trước audit, **không ghép clock hoặc cộng số với các probe mới**.

| Metric | n | p50 / p90 / p99 ms |
|---|---|---|
| detection_mailbox_wait_ms | 399 | 13.593 / 75.133 / 142.647 |
| frame_age_since_receive_ms | 383 | 13.844 / 76.680 / 148.442 |
| detection_queue_wait_ms | 767 | 0.067 / 0.171 / 38.321 |
| face_queue_wait_ms | 52 | 0.061 / 0.084 / 447.940 |
| recognition_queue_wait_ms | 2 | 160.630 / 165.022 / 166.010 |
| yolo_preprocess_ms | 384 | 4.538 / 9.353 / 17.798 |
| yolo_inference_ms | 384 | 47.434 / 65.721 / 86.955 |
| yolo_postprocess_ms | 384 | 2.414 / 5.120 / 12.418 |
| reid_preprocess_ms | 111 | 9.548 / 18.024 / 38.720 |
| reid_inference_ms | 111 | 83.137 / 110.464 / 224.893 |
| reid_postprocess_ms | 111 | 2.835 / 5.507 / 13.709 |
| scrfd_preprocess_ms | 51 | 8.581 / 18.259 / 20.280 |
| scrfd_inference_ms | 51 | 38.067 / 42.545 / 254.941 |
| scrfd_postprocess_ms | 51 | 3.126 / 13.455 / 17.837 |
| arcface_preprocess_ms | 2 | 0.818 / 0.873 / 0.886 |
| arcface_inference_ms | 2 | 389.136 / 663.740 / 725.526 |
| arcface_postprocess_ms | 2 | 0.778 / 1.207 / 1.304 |
| gui_render_ms | 997 | 25.022 / 39.759 / 58.001 |
| receive_to_gui_submit_ms | 997 | 94.033 / 160.025 / 239.839 |


Snapshot cuối phiên: receive **7.548 FPS**, AI detection **5.724 FPS**, GUI unique **7.503 FPS**; 997 GUI submissions nhưng chỉ 502 frame IDs khác nhau. Mailbox skipped=76, refreshed/superseded=16. Đây không phải network drops. ArcFace chỉ có **2 mẫu**, gồm startup outlier: p99 không đại diện steady-state. Trace 20/10 có 27 mẫu ArcFace, inference p50/p90/p99=36.622 / 40.260 / 605.116 ms, recognition wait=137.052 / 170.060 / 188.184 ms; là phiên khác, không gộp mẫu.


`gui_render_ms` chỉ đo show_frame/PIL/Tk; RGB conversion, digital PTZ và overlay trước đó không nằm toàn bộ trong span này. receive→GUI submit bao gồm thời gian đợi timer và công việc host, **không đo physical scanout**. Không có benchmark GUI mới vì khởi tạo desktop hiện tại còn khởi động worker image settings/backlight (`gui/cctv_app.py:261`), không phù hợp yêu cầu audit không thay cấu hình camera.

## 5. CPU/GPU/RAM, drops và nhiệt

Process CPU dùng quy ước 100%=1 core, tối đa khoảng 600% trên 6 cores. GR3D là CUDA/graphics GPU load toàn hệ thống, không phải NVDEC utilization. Tegrastats lấy mỗi giây, gồm startup/warmup; process resource trong JSON GStreamer chỉ cửa sổ đo. RAM tegrastats toàn máy khác RSS process. CPU p50/p90/p99 và RSS p50 bên dưới.

| Run | Process CPU % | RSS MiB p50 | Host RAM MiB p50/p90/p99 | GR3D % p50/p90/p99 | CPU max °C | GPU max °C |
|---|---|---|---|---|---|---|
| [A_mpv](../logs/decoder_audit/A_mpv.json) | 117.885 / 120.221 / 121.350 | N/A | 4666.000 / 4673.000 / 4675.800 | 7.000 / 12.000 / 32.200 | 43.406 | 41.843 |
| [B_mpv_ai](../logs/decoder_audit/B_mpv_ai.json) | 180.933 / 187.834 / 218.993 | N/A | 5809.000 / 5816.000 / 5819.320 | 48.000 / 98.000 / 99.000 | 46.000 | 44.687 |
| [cpu_50](../logs/decoder_audit/cpu_50.json) | 141.298 / 144.555 / 157.211 | 184.219 | 4663.000 / 4668.000 / 4669.000 | 8.000 / 11.600 / 14.080 | 44.312 | 43.156 |
| [cpu_100](../logs/decoder_audit/cpu_100.json) | 145.269 / 147.980 / 162.967 | 186.699 | 4669.000 / 4706.000 / 4796.840 | 7.000 / 10.000 / 13.080 | 44.156 | 42.750 |
| [cpu_200](../logs/decoder_audit/cpu_200.json) | 149.553 / 152.689 / 158.358 | 179.547 | 4660.000 / 4662.000 / 4664.080 | 7.000 / 10.000 / 21.120 | 44.812 | 43.375 |
| [nvdec_50](../logs/decoder_audit/nvdec_50.json) | 31.898 / 35.612 / 35.986 | 332.416 | 4600.000 / 4602.000 / 4603.000 | 7.000 / 11.600 / 15.000 | 44.062 | 43.312 |
| [nvdec_100](../logs/decoder_audit/nvdec_100.json) | 32.834 / 34.578 / 35.350 | 333.000 | 4595.000 / 4601.000 / 4602.000 | 7.000 / 12.000 / 15.360 | 43.312 | 42.375 |
| [nvdec_200](../logs/decoder_audit/nvdec_200.json) | 33.182 / 35.189 / 36.023 | 336.641 | 4600.000 / 4605.000 / 4606.000 | 7.500 / 11.500 / 34.050 | 43.375 | 42.562 |
| [nvdec_dpb_100](../logs/decoder_audit/nvdec_dpb_100.json) | 35.011 / 36.743 / 36.911 | 338.656 | 4604.000 / 4607.000 / 4608.360 | 7.000 / 11.000 / 17.320 | 43.187 | 42.312 |
| [cpu_200_ai](../logs/decoder_audit/cpu_200_ai.json) | 228.616 / 233.934 / 235.215 | 2071.619 | 5800.000 / 5807.000 / 5810.450 | 43.500 / 98.000 / 99.000 | 47.781 | 46.468 |
| [nvdec_100_ai](../logs/decoder_audit/nvdec_100_ai.json) | 117.158 / 119.804 / 120.902 | 2055.266 | 5811.500 / 5815.000 / 5816.870 | 39.500 / 90.900 / 98.290 | 46.968 | 46.093 |
| [nvdec_dpb_100_ai](../logs/decoder_audit/nvdec_dpb_100_ai.json) | 118.476 / 123.087 / 125.807 | 2125.465 | 5864.000 / 5956.800 / 5980.950 | 34.500 / 89.400 / 98.590 | 46.687 | 45.531 |
| [cpu_200_fixed](../logs/decoder_audit/cpu_200_fixed.json) | 234.129 / 237.654 / 240.537 | 2569.986 | 6229.000 / 6233.700 / 6234.000 | 38.500 / 97.700 / 99.000 | 47.125 | 45.812 |
| [nvdec_dpb_100_fixed](../logs/decoder_audit/nvdec_dpb_100_fixed.json) | 120.017 / 124.186 / 125.578 | 2653.436 | 6153.000 / 6166.700 / 6204.870 | 36.500 / 98.000 / 99.000 | 46.500 | 45.406 |
| [nvdec_dpb_100_fixed_repeat](../logs/decoder_audit/nvdec_dpb_100_fixed_repeat.json) | 118.359 / 121.730 / 125.281 | 2650.025 | 6170.000 / 6176.000 / 6183.270 | 39.500 / 98.000 / 99.000 | 45.218 | 44.281 |
| [cpu_200_fixed_repeat](../logs/decoder_audit/cpu_200_fixed_repeat.json) | 237.470 / 244.116 / 248.224 | 2552.600 | 6172.500 / 6206.100 / 6244.140 | 47.500 / 98.000 / 98.270 | 47.593 | 46.250 |
| [nvdec_dpb_100_jitter](../logs/decoder_audit/nvdec_dpb_100_jitter.json) | 58.193 / 63.192 / 64.314 | 335.613 | 4617.000 / 4618.000 / 4618.360 | 7.000 / 12.600 / 44.480 | 45.843 | 44.500 |
| [A_mpv_repeat](../logs/decoder_audit/A_mpv_repeat.json) | 118.114 / 120.296 / 121.858 | N/A | 4613.000 / 4628.000 / 4635.000 | 7.000 / 12.000 / 19.600 | 46.593 | 45.125 |


Không có bộ đếm throttling đáng tin hoặc EMC/NVDEC/VIC utilization đầy đủ trong tegrastats được trả về: **N/A**. Nhiệt độ và clocks quan sát được không chứng minh nguyên nhân thermal throttling; không thay nvpmodel/jetson_clocks. GPU utilization tăng không tự chứng minh contention NVDEC↔CUDA. Swap occupancy không phải swap-in/out rate.

### Frame / packet drop

Chênh decoder_out−queue_out chỉ là **cân bằng boundary trong cửa sổ 60 s**, có thể lệch ở ranh giới; không phải exact drop counter. Không suy ra packet loss từ appsink FPS. Jitter stats ở đây tính cả startup/warmup, đơn vị packet; num-lost không đồng nghĩa ICMP loss hay mất frame. Appsink synchronous pull không có exact drop counter trên GStreamer 1.20.3 được probe đọc; drop appsink riêng = N/A. JSON giữ toàn bộ jitter stats.

| Run | Decode frames | Queue out | App frames | Boundary difference | RTP lost | RTP late | Queue buffers p50/p90/p99 |
|---|---|---|---|---|---|---|---|
| cpu_50 | 1376 | 1376 | 1376 | 0 | 26 | 0 | 0.000 / 0.000 / 0.000 |
| cpu_100 | 1431 | 1431 | 1430 | 0 | 26 | 0 | 0.000 / 0.000 / 0.000 |
| cpu_200 | 1498 | 1498 | 1499 | 0 | 25 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_50 | 1500 | 1378 | 1378 | 122 | 0 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_100 | 1500 | 1380 | 1380 | 120 | 28 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_200 | 1500 | 1377 | 1377 | 123 | 24 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_dpb_100 | 1500 | 1500 | 1500 | 0 | 25 | 0 | 0.000 / 0.000 / 0.410 |
| cpu_200_ai | 1500 | 1500 | 1500 | 0 | 27 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_100_ai | 1500 | 1379 | 1379 | 121 | 0 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_dpb_100_ai | 733 | 733 | 732 | 0 | 25 | 0 | 0.000 / 0.000 / 0.000 |
| cpu_200_fixed | 1500 | 1500 | 1500 | 0 | 26 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_dpb_100_fixed | 1499 | 1499 | 1500 | 0 | 30 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_dpb_100_fixed_repeat | 1499 | 1499 | 1500 | 0 | 25 | 0 | 0.000 / 0.000 / 0.410 |
| cpu_200_fixed_repeat | 1500 | 1500 | 1500 | 0 | 36 | 0 | 0.000 / 0.000 / 0.000 |
| nvdec_dpb_100_jitter | 1500 | 1500 | 1501 | 0 | 26 | 0 | 0.000 / 0.000 / 0.000 |


## 6. Top 5 điểm nghẽn / khoảng chờ có evidence

1. **Đường capture mpv scale+screenshot trước mailbox:** đo mới tái hiện tăng trễ nhiều giây với AI OFF; screenshot p50 đã vượt budget 66,7 ms của 15 FPS. Probe lịch sử 25 s: main capture drift +8,244 s, chỉ decode+scale +1,214 s, decode native ~0 s. Các lượt không đồng thời, chỉ định vị đường xử lý; không trừ ra CPU time của từng kernel. [Đối chứng trước đó](camera_stream_review.md).
2. **ArcFace allocation failure khi đủ workload:** ít nhất một lượt AI live bị dừng; mức ảnh hưởng là mất chức năng inference, không chỉ chậm hơn vài ms. RAM/swap/LFB có dấu hiệu memory pressure nhưng chưa chứng minh riêng nguyên nhân fragmentation hay contention với NVDEC. Các lượt không gọi ArcFace không thể xác nhận AI khỏe. Giữ nguyên evidence thất bại và đối chứng fixed input, không gán lỗi này là nguyên nhân video drift khi AI OFF.
3. **Buffering nội bộ NVDEC mặc định:** decoder residence ~200 ms trong candidate có DPB, dù throughput 25 FPS. No-DPB candidate giảm parser→app p50 từ ~224 xuống ~51 ms và app output từ ~23 lên 25 FPS ở AI OFF. Đây là fixed buffering phía nhận, không giải thích 5–10 s của mpv CPU; không gán residence này cho execution time của NVDEC.
4. **AI scheduler/model budget:** recognition wait production p90 ~165 ms (trace 20/10 ~170 ms), OSNet inference p90 ~110 ms, YOLO ~66 ms; có outlier face queue. Tác động lên nhận diện, không có evidence chặn capture callback. Detection/ReID dùng lane ưu tiên cao; giữ nguyên chính sách trong audit. Không cộng các span/percentile.
5. **GUI và chuyển đổi ảnh host:** receive→Tk submit p90 ~160 ms, render p90 ~40 ms; capture CPU còn resize/RGB conversion ở bảng breakdown. GUI unique ~7,5 FPS khác GUI submissions và source 25 FPS. Nguồn video trước receive đã có thể cũ; cần benchmark renderer riêng nếu muốn latency display thấp hơn.

Ranking này là thứ tự điều tra theo tác động quan sát được, không phải năm thành phần độc lập của một critical path. Chưa xác minh: trễ camera encoder tuyệt đối, TCP head-of-line trong đợt bất thường, regression do profiling toàn pipeline, EMC contention, physical display scanout. B-frames không xuất hiện trong mẫu ffprobe; GOP ~1 s không tự gây backlog tăng dần nhiều giây. Link 100 Mb/s không có evidence bão hòa từ bitrate đã đo.

## 7. Thứ tự tối ưu đề xuất — chưa triển khai

1. **Ưu tiên xác nhận low-latency CPU GStreamer 200 ms:** đây là lựa chọn cân bằng tốt nhất trong dữ liệu đã đo để xử lý drift nhiều giây mà giữ throughput AI stress tốt hơn candidate NVDEC hiện tại. Thay đường screenshot khi được chọn triển khai bằng pipeline tường minh avdec_h264 → raw queue bounded/leaky → scale → RGB → latest-only mailbox. Dữ liệu 50/100/200 ms cho thấy không nên chọn 50 ms chỉ vì con số nhỏ; 200 ms giữ nhiều frame hơn. GStreamer production vốn đã có drop-on-latency và appsink bounded, nên chỉ thêm những cờ đó không sửa được backend mpv đang chạy. Giữ source 4K và model settings; phải kiểm tra full desktop read-only trước khi triển khai.
2. **NVDEC + low latency là phương án tiếp theo nếu cần tiết kiệm CPU / giảm receiver lateness:** decode NVMM → raw queue → nvvidconv (VIC) resize 720p, download BGRx → videoconvert RGB → appsink. Hiện có trade-off AI throughput và một lỗi allocation live, nên chưa đạt yêu cầu “không giảm đáng kể AI performance”. So sánh inference latency, memory headroom và frame continuity, không chọn chỉ từ decode FPS. `disable-dpb=true` chỉ dùng khi camera không có B-frame; mẫu hiện tại đáp ứng, nhưng cần guard khi stream đổi codec/profile. NVIDIA ghi rõ điều kiện no B-frames. [Tài liệu NVIDIA](https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/Multimedia/AcceleratedGstreamer.html).
3. **NVDEC + memory/preprocessing:** chỉ sau matched full-workload benchmark. Giữ NVMM qua resize trước download; xem giảm RGB/BGR conversions, upload trùng giữa các model, và binding tensor khi layout/lifetime/synchronization được kiểm chứng. Không mặc định nvvidconv rẻ hơn mọi CPU conversion; số đo resize/download là tổng nhiều công việc. Không hứa zero-copy hay % tăng AI khi chưa có CUDA memory trace và kiểm tra ảnh/embedding.
4. **Phương án khác:** direct GPU preview như v1 có thể giảm screenshot overhead nhưng cần xử lý overlay timestamps; chưa benchmark GUI mới. Substream lịch sử hết drift nhưng giảm thông tin mặt, phải kiểm tra face/ReID quality. Không tự đổi stream, bitrate, GOP, source FPS, model hay sampling AI. UDP chỉ là thử nghiệm tiếp theo có kiểm soát loss; chưa có lý do ưu tiên hơn TCP từ dữ liệu hiện tại.

Lợi ích được chứng minh ở đây là giảm **drift phía nhận** trong standalone capture và khác biệt CPU/buffering của từng candidate. Chưa có số đo camera→display hoặc matched full production B/C/E để cam kết latency cuối cùng hay “không giảm AI performance”. Không triển khai lựa chọn cuối cùng trước khi bước xác nhận này hoàn tất.

## 8. Phép đo còn thiếu và lệnh tái lập

Chạy nối tiếp từ root repository, đóng camera client khác trước khi đo. Các tool đọc URI/credentials từ camera.json trong process; không đưa URL có mật khẩu vào shell/log. Không chạy bằng pipeline production và không bật motor.

```bash
# A: production capture, AI OFF; có thể lặp lại 3 lần vào output khác nhau
python3 tools/stream_diagnostics.py --seconds 60 --minimal-sampling --output logs/decoder_audit/recheck_A.json

# B′: cùng capture + tải model độc lập, không production scheduler
python3 tools/stream_diagnostics.py --seconds 60 --minimal-sampling --ai-load --output logs/decoder_audit/recheck_B_load.json

# CPU baseline; lần lượt thay 200 bằng 100 và 50
python3 tools/decoder_probe.py --decoder cpu --latency 200 --seconds 60 --output logs/decoder_audit/recheck_cpu.json
python3 tools/decoder_probe.py --decoder cpu --latency 200 --seconds 60 --ai --output logs/decoder_audit/recheck_C_load.json

# D / E′: NVDEC; chỉ thêm --disable-dpb sau khi xác minh no B-frames
python3 tools/decoder_probe.py --decoder nvdec --latency 100 --seconds 60 --output logs/decoder_audit/recheck_D.json
python3 tools/decoder_probe.py --decoder nvdec --latency 100 --seconds 60 --disable-dpb --ai --output logs/decoder_audit/recheck_E_load.json

# Matched all-model stress riêng: không dùng kết quả để đánh giá accuracy live
python3 tools/decoder_probe.py --decoder cpu --latency 200 --seconds 60 --ai --ai-fixed-image person_search/targets/target.jpg --output logs/decoder_audit/recheck_cpu_fixed.json
python3 tools/decoder_probe.py --decoder nvdec --latency 100 --seconds 60 --disable-dpb --ai --ai-fixed-image person_search/targets/target.jpg --output logs/decoder_audit/recheck_nvdec_fixed.json

# Jitter residence: extra per-packet probes, chạy riêng để quan sát overhead
python3 tools/decoder_probe.py --decoder nvdec --latency 100 --seconds 60 --disable-dpb --jitter-probe --output logs/decoder_audit/recheck_jitter.json

# Dựng báo cáo inference từ trace đã đo, không khởi động camera
python3 tools/profile_pipeline.py report logs/decoder_audit/recheck_E_load_trace --output logs/decoder_audit/recheck_E_profile
```

Để hoàn tất B/C/E full production: cần adapter benchmark vô hiệu hóa camera writes lúc khởi tạo, dùng đúng InferenceWorker/FaceWorker scheduler và target reference, giữ cùng cảnh/clip, model, độ phân giải, FPS và workload/person/face counts; mỗi lượt ≥60 s sau warmup, lặp ≥3 và đổi thứ tự CPU/NVDEC. Đây là bước cần làm riêng, không dùng `--ai` hiện tại để giả nhận đã đo production. Các CLI trên chạy phần còn thiếu khả thi với probe hiện có; chưa có CLI an toàn hoàn chỉnh cho full desktop B/C/E, nên các ô đó giữ NOT_TESTED.

**Glass-to-glass:** đặt đồng hồ mili-giây hoặc LED đổi trạng thái trong FoV, quay đồng thời vật thật và màn hình preview bằng camera đối chứng có FPS biết trước. Với clock, lấy chênh hiển thị cùng ảnh quay; với LED, đếm frame từ cạnh sáng ngoài cảnh đến cạnh sáng trên preview. Lặp nhiều lần (đầu/giữa/cuối mỗi run), báo p50/p90/p99 và sai số lượng tử của camera quay. Không suy thời gian sensor từ appsink PTS; không nhắm vào motor để tạo chuyển động. Khi chưa thực hiện: camera capture→display **N/A**.

## 9. Files khi triển khai phương án được chọn

| File | Thay đổi cần cân nhắc sau acceptance benchmark |
|---|---|
| camera/rtsp_player.py | Explicit codec/decoder, NVMM→VIC path, bounded raw queue; stride-safe copy; probes tùy chọn |
| camera/stream.py | Chọn backend/candidate tường minh, báo decoder thực, xử lý fallback có provenance |
| camera.json / config.py | Chỉ đổi backend/latency/decoder sau quyết định triển khai; không sửa source stream ngầm |
| camera/mpv_player.py | Nếu giữ backend: giải quyết scale/screenshot bằng benchmark, tránh đồng bộ screenshot làm chậm tiến độ |
| runtime/frame_manager.py / telemetry/latency.py | Bổ sung metadata PTS/stage timestamps tùy chọn, giữ capture_timestamp unknown và latest-only semantics |
| gui/cctv_app.py | Nếu đổi renderer: frame-consistent overlays và đo submission/physical display riêng |
| runtime/inference_worker.py / runtime/face_worker.py / runtime/scheduling.py | Chỉ adapter benchmark/readiness và memory boundary khi dữ liệu chứng minh cần; không sửa thuật toán/threshold trong audit |

Trong audit này chỉ thêm `tools/decoder_probe.py`, mở rộng `tools/stream_diagnostics.py` với model-load opt-in, và tạo report/evidence. Không cài dependency hay thay môi trường. Files production có SHA256 để đối chiếu tại `production_hashes.json`; `.git` workspace không phải repository Git khả dụng nên không suy diễn commit/diff Git.

Kiểm chứng: 10 tests hiện có cho stream diagnostics/mpv PASS, Python compile PASS; probes CPU/NVDEC đã chạy RTSP thật. Các test chỉ kiểm chứng timestamp/drift và capture lifecycle, không chứng minh AI accuracy hay control behavior. Giữ nguyên lượt AI thất bại trong báo cáo, không tính nó là một lượt benchmark đủ 60 s.
