# GStreamer CPU — tích hợp và A/B desktop (22/10/2026)

**Kết luận: giữ `gstreamer_cpu` ở trạng thái opt-in; chưa đạt acceptance để đổi backend mặc định.** Capture/GUI cải thiện rõ, nhưng workload live giữa các lượt khác nhau, một số model không chạy ở nhiều lượt và latency host của YOLO có regression. Chưa có ground truth accuracy hoặc glass-to-glass. Không suy diễn PASS toàn bộ AI từ FPS capture.

## Thay đổi và cách sử dụng

`camera.json` vẫn giữ `video_backend="gstreamer"` theo yêu cầu chuyển về GStreamer ngay trước task này. Đây là **playbin cũ**, khác backend thử nghiệm `gstreamer_cpu`. Audit trước đó mô tả MPV; không sửa lại các số đo lịch sử. Factory default khi thiếu key vẫn là `gstreamer`. MPV và các tùy chọn decoder/scale/screenshot được giữ để rollback.

```bash
# Opt-in cho một tiến trình (desktop bình thường, có camera controls)
PTZ_VIDEO_BACKEND=gstreamer_cpu bash run_camera.sh

# Rollback MPV cho một tiến trình
PTZ_VIDEO_BACKEND=mpv bash run_camera.sh

# Full desktop benchmark: chặn camera writes/motor, kể cả startup/shutdown
python3 tools/desktop_capture_benchmark.py --backend gstreamer_cpu \
  --display :1 --warmup 20 --seconds 60 --output logs/desktop_capture_ab/new_cpu
python3 tools/desktop_capture_benchmark.py --backend mpv \
  --display :1 --warmup 20 --seconds 60 --output logs/desktop_capture_ab/new_mpv

# Tái tạo thống kê đúng cửa sổ đo, không gộp warmup
python3 tools/summarize_desktop_capture.py logs/desktop_capture_ab

# Lỗi receiver có kiểm soát, reconnect và create/close cycles (chỉ đọc RTSP)
python3 tools/check_cpu_stream_lifecycle.py --output logs/desktop_capture_ab/lifecycle_new.json
```

Các thư mục benchmark phải chưa tồn tại. `:1` là desktop thật 1920×1080 đã kiểm tra trên máy này; dùng DISPLAY của phiên đang chạy trên máy khác. Không dùng Xvfb thay thế desktop thật. Harness ép `PTZ_BENCHMARK_READ_ONLY=1`; mọi ZoomRPC instance đều áp dụng allowlist chỉ đọc, không thể bỏ chặn bằng `read_only=False` ở caller. Chỉ cho phép login, tạo handle PTZ, keepalive, đọc trạng thái/capabilities/config. Mọi start/stop/PositionABS/focus/config writes bị từ chối trước HTTP. Backlight initialization bị bỏ qua; lớp backlight hiện có thực ra cũng chỉ trả unsupported.

## Đường xử lý và ownership

```text
RTSP main H.264 3840×2160 @25, TCP, latency=200, drop-on-latency=true
 → rtph264depay → h264parse → H.264 alignment=au → avdec_h264
 → raw queue: leaky=downstream, max-buffers=2, bytes=0, time=0
 → videoscale 1280×720 PAR1 → videoconvert RGB
 → appsink: max-buffers=1, drop=true, sync=false, emit-signals=false
 → dedicated pull worker → stride/offset-aware owned NumPy RGB copy
 → CCTVApp.on_frame → existing FrameManager latest-only mailbox
      ├─ InferenceWorker → production scheduler → YOLO/OSNet/tracking
      │    └─ FaceWorker spatial + identity → SCRFD/ArcFace
      └─ Tk update → BGR overlay/digital PTZ → RGB/PIL/Tk configure
```

`CPUStreamPlayer` đặt trong `camera/rtsp_player.py`, dùng lại auth/status/backoff của receiver hiện có. Không có playbin/decodebin/NVDEC trong pipeline mới; runtime xác nhận factory `avdec_h264`. Consumer pull thread không phải GStreamer streaming thread; callback production chỉ publish/đổi tham chiếu, không chạy model hoặc render. Consumer chậm bị giới hạn bởi appsink một slot. Frame được copy đúng `GstVideoMeta` offset/stride (fallback `GstVideoInfo`), RGB giữ nguyên thứ tự kênh, độc lập buffer trước unmap; consumers không được sửa array trong mailbox.

PTS chỉ dùng cho tiến triển media và thống kê receiver. FrameManager tiếp tục gán receive timestamp tại decoded-app boundary sau copy; capture timestamp vẫn unknown. Không biến PTS hoặc appsink time thành sensor time. Mỗi reconnect có generation mới; không tính drift xuyên reset. Gst bus được polling ở worker, không phụ thuộc GLib main loop trên Tk. Stall 12 giây mặc định và bus ERROR/EOS dẫn tới teardown NULL + backoff reconnect; stop đánh thức backoff và join worker có kiểm tra.

Tài liệu API: [appsink](https://gstreamer.freedesktop.org/documentation/app/appsink.html), [GstVideoInfo](https://gstreamer.freedesktop.org/documentation/video/video-info.html). Dùng API có trên GStreamer 1.20.3 của máy; không giả định các counter appsink mới ở GStreamer 1.28 đã tồn tại.

## Protocol và giới hạn so sánh

`video_fps=15` vẫn là trần lấy screenshot của MPV; backend CPU mới không thêm videorate hay giới hạn giả còn 15 FPS. Output giữ đúng 1280×720; scheduler và AI_MAX_FPS giữ nguyên. Số frame thực tế được xử lý khác nhau là kết quả của backend/latest-only policy.

Chạy tuần tự MPV1 → CPU1 → CPU2 → MPV2 → MPV3 → CPU3. Mỗi process mở GUI thật, CUDA, model/target/gallery thật, InferenceWorker/FaceWorker/scheduler thật. Đợi target + detection ready, bật Auto Zoom qua cùng UI action để các nhánh chọn target được phép chạy; mọi tác động vật lý bị RPC từ chối. Warmup 20 giây sau ready, đo ≥60 giây. Auto Focus/model/threshold/Tracking/ZoomPolicy không chỉnh sửa. Không đổi allocator, clocks/power, stream URI, resolution hoặc model workload để cải thiện số.

User chọn tiếp tục với cảnh hiện tại. **Cùng cấu hình, target và nguồn camera, nhưng không phải cùng chuỗi ảnh cố định:** người/mặt vào-ra khác nhau, số inference OSNet/SCRFD/ArcFace không bằng nhau. Auto Zoom không thể thực sự đưa mặt vào ROI vì camera writes bị chặn; các fault/gates do deny actuation có thể thay đổi đường perception. Đây là production software pipeline trong chế độ không tác động camera, không phải acceptance vòng điều khiển vật lý. Model/target/config hashes nằm trong result của các lượt có manifest; MPV1 chạy trước khi bổ sung manifest nhưng không có đổi model/config giữa các lượt.

## Kết quả full production desktop

FPS capture đếm RGB frame được chuyển tới app; GUI unique FPS đếm frame ID mới được Tk submit, không phải tốc độ màn hình vật lý. Drift = Δhost − ΔPTS trong cùng connection. MPV dùng `time_pos` có sẵn trong capture loop, chưa phải timestamp của chính screenshot; GStreamer dùng PTS của buffer. Drift âm là tiến triển media nhanh hơn wall trong cửa sổ, **không phải độ trễ âm**.

| Run | s đo | Capture FPS | Drift s | GUI unique FPS | Detection/s | CPU p50 % | RSS p50 MiB | GPU p50 % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| [gstreamer_cpu_1](../logs/desktop_capture_ab/gstreamer_cpu_1/measured.json) | 60.108 | 25.005 | -0.047 | 14.308 | 10.814 | 268.873 | 1865.773 | 51.000 |
| [gstreamer_cpu_2](../logs/desktop_capture_ab/gstreamer_cpu_2/measured.json) | 60.086 | 24.998 | -0.013 | 14.213 | 7.722 | 273.297 | 2488.590 | 58.900 |
| [gstreamer_cpu_3](../logs/desktop_capture_ab/gstreamer_cpu_3/measured.json) | 60.077 | 24.885 | -0.088 | 13.832 | 9.438 | 264.179 | 1858.133 | 44.600 |
| [mpv_1](../logs/desktop_capture_ab/mpv_1/measured.json) | 60.104 | 8.103 | -0.895 | 8.086 | 8.103 | 212.897 | 1860.953 | 50.800 |
| [mpv_2](../logs/desktop_capture_ab/mpv_2/measured.json) | 60.104 | 8.202 | -1.903 | 8.186 | 5.507 | 242.949 | 2109.984 | 45.800 |
| [mpv_3](../logs/desktop_capture_ab/mpv_3/measured.json) | 60.076 | 7.491 | -1.093 | 7.507 | 7.341 | 204.988 | 993.781 | 45.400 |

CPU: 100%=một core, tối đa khoảng 600% trên sáu cores. GPU là sensor utilization toàn máy, không phải thời gian NVDEC hoặc CUDA kernel. RSS là process, khác RAM toàn máy. Không chạy benchmark capture khác đồng thời trong cửa sổ đo.

### Model latency và số lượt gọi

Host wall latency p50 / p90 / p99, ms; số mẫu theo đúng cửa sổ đo. N/A nghĩa là không có inference tương ứng, không có nghĩa là 0 ms. Không dùng số lần model đã nạp làm số lần inference.

| Run | YOLO n; p50/p90/p99 | OSNet n; p50/p90/p99 | SCRFD n; p50/p90/p99 | ArcFace n; p50/p90/p99 |
|---|---|---|---|---|
| [gstreamer_cpu_1](../logs/desktop_capture_ab/gstreamer_cpu_1/measured.json) | 650; 55.997 / 72.335 / 98.033 | N/A (0) | N/A (0) | N/A (0) |
| [gstreamer_cpu_2](../logs/desktop_capture_ab/gstreamer_cpu_2/measured.json) | 464; 56.863 / 71.830 / 106.789 | 115; 86.704 / 108.891 / 161.209 | 81; 44.456 / 52.904 / 57.158 | 7; 31.270 / 39.393 / 40.080 |
| [gstreamer_cpu_3](../logs/desktop_capture_ab/gstreamer_cpu_3/measured.json) | 567; 64.197 / 87.470 / 116.713 | 8; 84.635 / 115.845 / 133.767 | N/A (0) | N/A (0) |
| [mpv_1](../logs/desktop_capture_ab/mpv_1/measured.json) | 487; 46.784 / 60.485 / 73.208 | N/A (0) | N/A (0) | N/A (0) |
| [mpv_2](../logs/desktop_capture_ab/mpv_2/measured.json) | 332; 46.812 / 58.416 / 69.376 | 312; 71.638 / 82.814 / 95.079 | N/A (0) | N/A (0) |
| [mpv_3](../logs/desktop_capture_ab/mpv_3/measured.json) | 442; 52.760 / 87.289 / 167.757 | N/A (0) | N/A (0) | N/A (0) |

### Latency timeline và freshness

Các percentile là từng stage độc lập; **không cộng p50/p90 của các stage thành end-to-end**. Decoder residence bao gồm buffering/scheduling, không phải CPU execution của decoder. Receive→GUI kết thúc ở Tk image configure, chưa đo scanout.

| Run | Parser→RGB p50/p90/p99 ms | Decode residence p50 ms | RGB copy p50 ms | Frame age khi AI lấy p50/p90/p99 ms | Receive→GUI p50/p90/p99 ms | Detection scheduler wait p50/p90/p99 ms |
|---|---|---:|---:|---|---|---|
| [gstreamer_cpu_1](../logs/desktop_capture_ab/gstreamer_cpu_1/measured.json) | 84.856 / 106.692 / 289.155 | 58.199 | 1.867 | 16.512 / 34.426 / 67.249 | 43.305 / 148.306 / 241.910 | 0.064 / 0.134 / 0.258 |
| [gstreamer_cpu_2](../logs/desktop_capture_ab/gstreamer_cpu_2/measured.json) | 87.256 / 107.125 / 282.922 | 60.388 | 1.887 | 16.550 / 40.725 / 140.160 | 44.574 / 137.033 / 237.287 | 0.065 / 0.172 / 41.433 |
| [gstreamer_cpu_3](../logs/desktop_capture_ab/gstreamer_cpu_3/measured.json) | 88.105 / 115.315 / 280.629 | 60.006 | 1.964 | 17.015 / 37.175 / 78.749 | 45.679 / 140.540 / 226.081 | 0.066 / 0.144 / 0.301 |
| [mpv_1](../logs/desktop_capture_ab/mpv_1/measured.json) | N/A | N/A | N/A | 9.933 / 20.327 / 26.028 | 84.885 / 156.083 / 304.151 | 0.068 / 0.130 / 0.238 |
| [mpv_2](../logs/desktop_capture_ab/mpv_2/measured.json) | N/A | N/A | N/A | 25.702 / 89.447 / 138.258 | 82.671 / 142.028 / 257.722 | 0.063 / 0.119 / 0.259 |
| [mpv_3](../logs/desktop_capture_ab/mpv_3/measured.json) | N/A | N/A | N/A | 12.705 / 29.890 / 114.784 | 91.354 / 179.814 / 305.309 | 0.069 / 0.154 / 0.288 |

Mốc sensor/encoder/network trước parser = N/A; queue wait của face/recognition/reid có trong `measured.json` nếu nhánh chạy. Freshness ở FrameManager chỉ tính từ frame decode/copy ra app. MPV có thể có frame cũ trước boundary này; số age thấp không chứng minh hình camera mới.

### Dropped frames / latest-only

| Run | Mailbox skipped | Superseded sau scheduler grant | Stale AI results | Raw decoder−queue boundary | Queue−appsink boundary |
|---|---:|---:|---:|---:|---:|
| [gstreamer_cpu_1](../logs/desktop_capture_ab/gstreamer_cpu_1/measured.json) | 821 | 32 | 0 | 0 | 0 |
| [gstreamer_cpu_2](../logs/desktop_capture_ab/gstreamer_cpu_2/measured.json) | 983 | 57 | 2 | 0 | 0 |
| [gstreamer_cpu_3](../logs/desktop_capture_ab/gstreamer_cpu_3/measured.json) | 898 | 30 | 0 | -1 | -1 |
| [mpv_1](../logs/desktop_capture_ab/mpv_1/measured.json) | 0 | 0 | 0 | N/A | N/A |
| [mpv_2](../logs/desktop_capture_ab/mpv_2/measured.json) | 160 | 1 | 0 | N/A | N/A |
| [mpv_3](../logs/desktop_capture_ab/mpv_3/measured.json) | 9 | 0 | 0 | N/A | N/A |

`dropped_frames` là frame ID bị inference mailbox bỏ qua khi lấy frame mới nhất; không phải RTP loss. `superseded` có thể đồng thời góp vào mailbox skipped, không cộng hai counter như các tập rời. Hai cột boundary không phải exact drop counter: có buffer đang xử lý ở hai đầu cửa sổ. Appsink1.20 không cung cấp exact dropped count ở giao diện đang dùng. Không drop chủ động compressed H.264 trong queue; raw leaky queue nằm sau decoder. `drop-on-latency` vẫn có thể bỏ RTP ở jitterbuffer, không bảo đảm không bao giờ mất reference packet.

## Regression và bottleneck còn lại

1. CPU capture duy trì gần 25 FPS; GUI vẫn chỉ submit khoảng 17 FPS và unique thấp hơn capture. Tk timer + resize/overlay/PIL/ImageTk vẫn là bottleneck hiển thị.
2. Lượt đầu hai bên chủ yếu YOLO: GStreamer tăng throughput detection nhưng YOLO host inference p50 tăng. Scheduler detection wait rất nhỏ, nên không có bằng chứng scheduler wait là nguyên nhân chính của chênh lệch này. CPU process cũng cao hơn do capture/render/AI xử lý nhiều frame hơn. Không đồng nhất host timing với CUDA kernel; CPU/GIL, băng thông RAM chung và DVFS vẫn là các giả thuyết cần phép đo đối chứng.
3. Các lượt sau có số người/crop/face khác nhau: không thể gán mọi thay đổi throughput cho backend. Các stage preprocess/postprocess/resource_hold và queue waits giữ trong JSON để tách workload/CPU/GPU. Không giảm resolution hay tắt model để che regression.
4. MPV trong các cửa sổ full desktop sau warmup lần này không tái hiện +7,6 s/60 s của audit capture cũ. Các phép đo khác cửa sổ, tải và cảnh; giữ nguyên cả hai evidence, không kết luận audit sai hoặc MPV đã hết trễ tuyệt đối.
5. Lệnh motor bị deny có chủ ý làm một số controller ở trạng thái chờ/fault; shutdown GUI mất khoảng 20 giây do manual worker đợi ownership. Đây là đường điều khiển read-only, không phải Gst teardown. Các lượt đầu báo lỗi cleanup vì cố STOP ở session read-only; đã sửa close bỏ qua writes, không sửa chính sách zoom hoặc giả lập ACK motor.

**Chưa cô lập được nguyên nhân duy nhất của regression per-inference.** Cần lặp production trên cùng clip RTSP có đủ người/mặt + target, cùng frame windows, ghi clocks và timeline CPU/CUDA; so sánh per-model lẫn throughput toàn hệ. Không có CUDA kernel trace trong đợt này. Không tự khóa clocks, đổi allocator hoặc chuyển NVDEC.

## Phép đo bổ sung để định vị regression

![Full desktop comparison](../logs/desktop_capture_ab/comparison.png)

CUDA timing là **hai lượt diagnostic riêng**, mỗi lượt 60 giây sau warmup, bật `--cuda-timing`. Sử dụng CUDA events có sẵn trong telemetry, đồng bộ ở cuối stage; overhead này thay đổi execution nên không trộn vào sáu lượt A/B chính. Không đổi allocator hoặc model. CUDA-event elapsed bao gồm khoảng trên CUDA stream, có thể gồm launch gaps; không phải tổng thời gian từng kernel như Nsight.

| Diagnostic | YOLO host p50/p90/p99 ms | CUDA-event p50/p90/p99 ms | Detection wait p50 ms | Calls GPU |
|---|---|---|---:|---:|
| [gstreamer_cpu_cuda_timing](../logs/desktop_capture_ab/gstreamer_cpu_cuda_timing/measured.json) | 68.495 / 94.651 / 130.568 | 67.477 / 92.827 / 129.793 | 0.068 | 532 |
| [mpv_cuda_timing](../logs/desktop_capture_ab/mpv_cuda_timing/measured.json) | 52.072 / 68.129 / 92.321 | 50.669 / 66.150 / 90.331 | 0.070 | 471 |

Diagnostic ghi nhận MPV YOLO host/CUDA-event median khoảng 52,1/50,7 ms, CPU-GStreamer khoảng 68,5/67,5 ms. Phần chênh lệch nằm trong đoạn forward trên timeline CUDA, không chủ yếu ở scheduler wait (~0,07 ms) hoặc wrapper host bên ngoài. Đây là định vị regression, **chưa tách được GPU kernel slowdown với khoảng chờ launch/CPU/GIL và băng thông RAM chung**. GPU-event timing không thay thế kernel trace. GPU clocks không bị khóa; không có clock samples cùng cửa sổ diagnostic, nên không gán nguyên nhân duy nhất.

[Resource context](../logs/desktop_capture_ab/resources_context.json) giữ clocks/RAM/swap/thermal theo các cửa sổ có collector. Collector bắt đầu giữa CPU2 nên CPU2 chỉ có 10 GPU-clock samples; MPV1/CPU1 N/A. MPV2 clock median 408 MHz; MPV3 và CPU3 median 306 MHz. CPU3 clock mean cao hơn MPV3 nhưng YOLO vẫn chậm hơn, nên không thể giải thích toàn bộ chênh lệch chỉ bằng median clock. Có biến thiên DVFS và workload; không có bằng chứng throttle hay zero-throttle.

ArcFace chỉ có 7 lần gọi ở CPU2 trong bảng A/B chính. Các percentile được giữ như mẫu quan sát; không dùng p99 của 7 mẫu để kết luận ổn định hay accuracy.

## Reconnect, shutdown, memory và test

[Lifecycle evidence](../logs/desktop_capture_ab/lifecycle.json):

| Kiểm tra | Kết quả |
|---|---|
| connection_failure_detected | PASS |
| connection_recovered | PASS |
| eos_injected | PASS |
| eos_recovered | PASS |
| stall_recovered | PASS |
| worker_stopped | PASS |

| Cycle | Active RSS MiB | Stopped RSS MiB | Stop s | Capture thread alive |
|---|---:|---:|---:|---|
| 1 | 191.441 | 127.148 | 0.068 | False |
| 2 | 202.379 | 190.355 | 0.027 | False |
| 3 | 255.711 | 227.902 | 0.038 | False |
| 4 | 288.016 | 219.750 | 0.041 | False |

Ở lượt đầu, stopped RSS tăng từ 127 lên khoảng 220 MiB qua bốn cycles; không xem đây là bằng chứng không leak. Đã bổ sung teardown gỡ từng pad probe để giải phóng bound callbacks rõ ràng. Lỗi kết nối được tạo ở URI local không tồn tại, sau đó trả receiver về URI thật trong bộ nhớ; EOS và stall được tạo trên pipeline receiver. Không rút cáp, tắt camera hay sửa camera.json. Bốn create/close cycles và RSS trong từng benchmark chỉ là kiểm tra ngắn; **chưa chứng minh không có memory leak dài hạn**. Cần soak nhiều giờ với reconnect lặp.

Focused suite: **35 tests PASS** (số cuối cùng xem log). Tests: [focused suite](../logs/desktop_capture_ab/focused_tests.txt), [expanded suite](../logs/desktop_capture_ab/tests.txt). Các test mới kiểm tra RGB channel/padded stride/GstVideoMeta offset/lifetime, truncated buffer, factory decoder, bounded queues, slow consumer, failure/backoff/shutdown và RPC deny/cleanup. Bộ mở rộng có bốn lỗi ở `AbsoluteZoomIntegrationTests`: fixture bbox `(0,0,100,200)` chạm biên, bị guard `BBOX FRAME EDGE` hiện có chặn trước những trạng thái test muốn kiểm tra. Không thay AutoZoom/ZoomPolicy/threshold để làm test pass.

### Kiểm tra giải phóng mở rộng

[20-cycle evidence](../logs/desktop_capture_ab/lifecycle_extended.json). Chạy sau khi teardown gỡ từng pad probe. Không thay CUDA/host allocator và không gọi gc.collect trong lượt này.

| Cycle | Active RSS MiB | Stopped RSS MiB | Receiver objects còn sống |
|---|---:|---:|---:|
| 1 | 245.250 | 182.773 | 0 |
| 2 | 195.922 | 195.988 | 0 |
| 3 | 264.418 | 250.672 | 0 |
| 4 | 250.680 | 250.793 | 0 |
| 5 | 265.094 | 251.219 | 0 |
| 6 | 310.840 | 283.020 | 0 |
| 7 | 301.168 | 287.305 | 0 |
| 8 | 331.574 | 295.816 | 0 |
| 9 | 309.840 | 295.949 | 0 |
| 10 | 331.750 | 295.836 | 0 |
| 11 | 309.613 | 295.871 | 0 |
| 12 | 331.676 | 295.941 | 0 |
| 13 | 309.719 | 295.879 | 0 |
| 14 | 331.680 | 295.781 | 0 |
| 15 | 305.832 | 305.934 | 0 |
| 16 | 341.734 | 305.930 | 0 |
| 17 | 305.953 | 306.027 | 0 |
| 18 | 341.828 | 306.098 | 0 |
| 19 | 319.875 | 306.016 | 0 |
| 20 | 306.016 | 306.180 | 0 |

Sau teardown có quản lý, cả 20 cycles có **0 receiver objects còn sống** sau bỏ tham chiếu và không còn capture thread, không gọi gc.collect. RSS có bước tăng ban đầu rồi giữ khoảng 296 MiB ở cycles 8–14 và khoảng 306 MiB ở cycles 15–20 (cuối 306,2 MiB). Điều này phù hợp native/allocator retention hơn là giữ lại receiver Python; chưa cô lập từng native allocation. Không suy ra an toàn dài hạn từ 20 cycles ngắn.

## Acceptance theo yêu cầu

| Tiêu chí | Kết quả | Evidence / giới hạn |
|---|---|---|
| Tái sử dụng receiver/factory/FrameManager/runtime | PASS | CPUStreamPlayer trong rtsp_player; production workers giữ nguyên |
| Pipeline CPU tường minh, đúng avdec_h264 | PASS | Factory check và negotiated caps mỗi run |
| TCP200, raw bounded/leaky sau decoder, latest-only | PASS | Runtime properties + slow-consumer test |
| Main 4K25, output RGB1280×720 | PASS | CPU negotiated caps; URI/config không đổi |
| Model/target/threshold/tracking/focus không đổi | PASS về code/config | Không đồng nghĩa accuracy đã xác minh |
| Buffer lifetime/stride/color/thread safety | PASS kiểm tra hiện có | Owned RGB copy + pull worker + mailbox locks |
| Opt-in và rollback MPV | PASS | Environment override; default không đổi |
| Desktop thật, production scheduler/FaceWorker, không camera writes | PASS | Read-only guard tests + blocked counters; motor behavior N/A |
| ≥60 s sau warmup, ba lần mỗi backend | PASS — 6/6 | Chỉ tính các run có đủ window; không tính smoke |
| Cùng workload AI đầy đủ để so sánh | NOT_MET | Cảnh live thay đổi; số calls/gates không tương đương |
| CPU capture không tích lũy drift nhiều giây | PASS các cửa sổ đã đo | Không chứng minh glass-to-glass |
| AI accuracy và mọi model không regression | NOT_ACCEPTED | YOLO host latency tăng; workload/ground truth chưa đủ |
| Đầy đủ FPS/freshness/model/queue/resources/drops | PARTIAL | Exact RTP/appsink drops và vài model không có mẫu |
| Glass-to-glass | NOT_TESTED | Không có thiết bị/quan sát đo quang học; không suy diễn appsink time |
| Reconnect/failure/shutdown | PASS receiver lifecycle | Fault injection receiver; shutdown read-only có ownership wait |
| Giải phóng receiver / memory dài hạn | PASS object/thread; dài hạn NOT_TESTED | 20 cycles không còn receiver objects; RSS có warmup/retention, cần soak |
| Không tự NVDEC/allocator thay đổi | PASS | Chỉ avdec_h264; CUDA config giữ nguyên |

## Quyết định và bước tiếp theo

**Không chuyển `gstreamer_cpu` thành mặc định ở bước này.** Cho phép tiếp tục opt-in vì capture/GUI có lợi rõ ràng. Acceptance còn thiếu: workload production lặp lại có đủ bốn model và target, accuracy ground truth, xác minh per-model regression, soak memory/reconnect, và glass-to-glass nếu có thiết bị.

Bước triển khai tiếp theo: ghi một clip 4K25 có target/người/mặt đầy đủ, phát RTSP cùng clip theo thời gian thực cho hai backend, giữ nguyên các worker/gates và so khung thời gian tương ứng; bổ sung controlled live session để đánh giá ảnh→motor khi được người vận hành cho phép. Đo quang học bằng quay đồng thời đồng hồ/LED ở hiện trường và màn hình; báo phân bố qua nhiều mẫu, không lấy PTS thay cho capture time. Chỉ sau acceptance PASS mới đề xuất đổi `camera.json`, giữ lệnh rollback MPV và theo dõi một phiên canary trước rollout.

## File thay đổi

| File | Vai trò |
|---|---|
| camera/rtsp_player.py | CPUStreamPlayer opt-in, pipeline cố định, pull worker, stride/copy, bus/reconnect/stop, diagnostics |
| camera/stream.py, config.py | Factory gstreamer_cpu, process-local PTZ_VIDEO_BACKEND, benchmark flag |
| camera/mpv_player.py | Optional observer dùng time_pos đã đọc; không thêm screenshot/query |
| camera/dahua_rpc.py | Benchmark ép read-only và đếm write attempts bị chặn |
| camera/ptz_controller.py | Read-only close giải phóng RPC/worker mà không gửi STOP/restore |
| gui/cctv_app.py | Benchmark bỏ qua startup image settings |
| main.py, runtime/desktop_benchmark.py | Gắn harness vào CCTVApp thật; readiness/warmup/window/shutdown/results |
| tools/desktop_capture_benchmark.py | Launcher A/B với environment và camera guard, timeout, output |
| tools/summarize_desktop_capture.py | Rebuild model/window stats từ trace gốc |
| tools/check_cpu_stream_lifecycle.py | RTSP read-only lifecycle + cycles |
| test/test_gstreamer_cpu.py | Contract, stride/lifetime, slow consumer, failure, no-writes tests |
| README.md, docs/gstreamer_cpu_integration_20261022.* | Hướng dẫn và báo cáo |

Không sửa camera.json trong task tích hợp; không sửa YOLO/OSNet/SCRFD/ArcFace, FrameManager, scheduler, tracking, ZoomPolicy hoặc Auto Focus. Các trace/resource JSON là evidence thật; không sinh số liệu thiếu.
