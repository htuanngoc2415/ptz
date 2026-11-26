# Điều tra camera hiển thị trễ và so sánh phanmem_v1.0

Audit tiếp theo với các lượt 60 giây, CPU/NVDEC, jitterbuffer 50/100/200 ms và tải AI: [RTSP decoder audit 22/10/2026](rtsp_decoder_audit_20261022.md) · [HTML](rtsp_decoder_audit_20261022.html). Có đối chứng đủ bốn model và lưu cả lượt ArcFace allocation failure; chưa triển khai thay đổi production.

Ngày đo: 22/10/2026, UTC+07:00. Đọc RTSP thật trên thiết bị hiện tại; không chạy AI, GUI hoặc lệnh motor trong các probe. Không thay camera.json, model, threshold hoặc code pipeline production.

## Kết luận có số đo

Đã tái hiện được video tiến chậm hơn thời gian thực ngay trong đường capture hiện tại. Main stream đang là H.264 3840×2160, libmpv 0.34.1, hwdec-current=no. Đường decode/scale/screenshot khiến ảnh chậm dần trước khi FrameManager nhận được frame. Đây là bằng chứng phù hợp với hiện tượng chậm 5–10 giây, không phải phép đo tuyệt đối sensor-to-display.

Các probe nối tiếp, mỗi probe yêu cầu 25 giây, chỉ hỏi PTS mỗi khoảng một giây trong nhóm đối chứng chính. Khoảng đo dưới đây nằm giữa hai mẫu PTS hợp lệ đầu/cuối, không bao gồm toàn bộ startup. Không cộng hoặc trừ các kết quả giữa hai lượt để coi đó là phần trăm critical path.

| Cấu hình thử nghiệm | Thời gian monotonic | PTS tiến | PTS/thời gian thực | Trễ tăng thêm tương đối |
|---|---:|---:|---:|---:|
| Main 4K, capture production: scale + screenshot | 23,604 s | 15,360 s | 0,651× | **8,244 s** |
| Main 4K, giữ scale, không screenshot | 23,534 s | 22,320 s | 0,948× | **1,214 s** |
| Main 4K, không scale, không screenshot | 23,114 s | 23,120 s | 1,000× | −0,006 s, xấp xỉ 0 |
| Substream, cùng capture production | 23,057 s | 23,000 s | 0,998× | **0,057 s** |

Chênh lệch âm rất nhỏ trong probe native là sai khác lấy mẫu/nhịp PTS, không phải latency âm. Decode-only không xuất ảnh GUI nên không phải benchmark hiển thị hoàn chỉnh.

Evidence JSON, lần lượt:

- [main capture](../logs/stream_review/current_main_minimal_sampling.json)
- [main decode + scale](../logs/stream_review/main_scaled_decode_minimal_sampling.json)
- [main native decode](../logs/stream_review/main_native_decode_only.json)
- [sub capture](../logs/stream_review/sub_capture_minimal_sampling.json)

Main capture có screenshot call p50/p90/p99 = **77,059 / 104,776 / 179,657 ms**; sub capture = **36,970 / 44,202 / 50,921 ms**. Đây là thời gian host chờ lời gọi screenshot, gồm xử lý/chờ bên trong libmpv, không chỉ thời gian memcpy. Số frame callback trong toàn probe main/sub là 205/319; không dùng số callback để suy ra source FPS.

Hai probe lấy nhiều property hơn cũng tái hiện: main capture trễ tăng thêm 7,639 s/23,079 s; decode+scale không screenshot 1,337 s/23,897 s. Xem [capture detailed](../logs/stream_review/current_main_capture.json), [decode detailed](../logs/stream_review/current_main_decode_only.json). Không dùng các lượt detailed để trừ trực tiếp chi phí so với minimal sampling.

## Luồng hiện tại và luồng cũ

| Thành phần | Pipeline hiện tại | phanmem_v1.0 |
|---|---|---|
| Stream mặc định | camera.json subtype=0; đo được main 3840×2160 | loi/dieu_khien.py SUBTYPE=1; GUI cho đổi main/sub |
| Đường hiển thị | MPV vo=null → scale CPU → screenshot_raw → NumPy → OpenCV/overlay → PIL/ImageTk → Tk label | MPV vo=gpu nhúng trực tiếp vào wid của khung video |
| Decode yêu cầu | video_hwdec=no; probe xác nhận hwdec-current=no | hwdec=auto-safe: yêu cầu thử hardware, chưa đo backend thực tế trên máy này |
| Scale | Bộ lọc lavfi scale xuống tối đa 1280×720 trên đường video, trước screenshot | Không có bộ lọc lavfi scale tương ứng trong _start_player; renderer GPU trình bày video |
| Phụ thuộc Tk | Mỗi ảnh hiển thị qua update_video/show_frame | MPV tự vẽ video; Tk tick chủ yếu cập nhật trạng thái/OSD |
| TCP/cache/profile | TCP, cache=no, low-latency, untimed, nobuffer/low_delay | Các tùy chọn này cũng có trong GUI cũ |
| Quan sát player | Log hiện tại chủ yếu đếm frame đã ra tới Python | GUI cũ còn hiện demuxer_cache_duration, hwdec_current, estimated_vf_fps |

Source: [MPVPlayer](../camera/mpv_player.py), [GUI hiện tại](../gui/cctv_app.py), [GUI cũ](../phanmem_v1.0/phanmem/giao_dien/ung_dung.py), [cấu hình cũ](../phanmem_v1.0/phanmem/loi/dieu_khien.py).

V1 còn có đường lấy ảnh riêng `loi/luong_anh.py::LuongMPV` (vo=null/hwdec=no, screenshot khi consumer cần). Không nhầm đường lấy ảnh này với đường hiển thị GPU của GUI cũ. Các số 271–284 ms trong chú thích code cũ là dữ liệu lịch sử của tác giả, không được dùng làm benchmark hiện tại; chính chú thích cũng nói chưa đo tuyệt đối đường vo=null.

## Vì sao profiler trước không thấy 5–10 giây

1. `MPVPlayer._capture` đọc time_pos, gọi screenshot_raw đồng bộ rồi mới gọi frame_callback.
2. `CCTVApp.on_frame` gọi FrameManager.publish; received_ns được tạo ở đây, **sau** bước screenshot và mọi trễ upstream.
3. FrameManager chỉ giữ một frame mới nhất đã được publish. GUI đọc latest_frame_rgb; không có FIFO ảnh dài ở đoạn này. GUI dùng kết quả AI mới nhất để overlay, không đợi inference hoàn tất trên từng frame.
4. Receive→Tk submission p50 95 ms, p99 243 ms của P1 chỉ đo đoạn sau publish. Frame mới tới Python vẫn có thể là nội dung camera cũ nhiều giây.
5. Watchdog video_stall_timeout chỉ phát hiện không có frame tiến triển. Một luồng vẫn tiến nhưng chậm dần không bị điều kiện này bắt lại.
6. `latency_ms=200` được truyền vào config nhưng MPVPlayer không đọc trường đó; chỉ backend GStreamer dùng nó. Dòng startup “RTSP latency 200 ms” không phải mức trễ thực hoặc giới hạn trễ của backend mpv.

Ở probe detailed, decoder_frame_drop_count và frame_drop_count bằng 0 ở các mẫu có dữ liệu, trong khi PTS không theo kịp thời gian thực. Demuxer cache thường khoảng 1 giây, nhưng đây không bao gồm mọi buffer upstream và không giải thích toàn bộ 8 giây. Theo [tài liệu chính thức mpv](https://mpv.io/manual/stable/#low-latency-playback), dữ liệu livestream có thể tích tụ khi playback chậm hơn nguồn; cache=no/low-latency không chứng minh toàn bộ pipeline không có buffer.

## Mức chắc chắn và giới hạn

- Đã đo: main 4K native decode theo kịp trong probe; thêm scale có drift nhỏ hơn; capture đầy đủ có drift lớn. Việc lấy screenshot và scale trong đường capture là vị trí cần ưu tiên xử lý.
- Chưa tách được chi phí nội bộ decode/filter/RGB conversion/command synchronization của libmpv. Không gọi toàn bộ 77 ms screenshot là copy cost hay khẳng định CPU decoder đơn thuần là nguyên nhân duy nhất.
- Không có AI/GUI production chạy đồng thời trong các probe. Không suy ra mức trễ khi đủ workload từ probe standalone.
- Không chạy GUI cũ để benchmark trực tiếp; so sánh kiến trúc dựa trên code. Probe sub vẫn dùng capture hiện tại, không giả làm phanmem_v1.0.
- Polling cũng có overhead: ở main capture minimal, property_poll_ms p50/p90 = 18,61/46,56 ms mỗi lần hỏi khoảng 1 Hz. Ở sub là 0,28/4,65 ms. Không phải phép đo không nhiễu; hai chế độ sampling đều tái hiện drift main lớn.
- PTS là clock media, không phải clock sensor đồng bộ. Trễ ban đầu, sensor-to-display tuyệt đối và scanout vật lý: **N/A**. Chưa đo được con số tuyệt đối 5–10 giây mà người dùng quan sát; đã đo được mức tăng trễ tương ứng.

## Hướng sửa đề xuất, chưa áp dụng

1. Tách đường hiển thị trực tiếp qua mpv renderer như v1 khỏi đường lấy ảnh AI. Xác minh renderer/hardware backend thực tế trên thiết bị; không mặc định auto-safe đồng nghĩa đã dùng hardware decoder. Khi ghép overlay, cần xử lý khác biệt timestamp/toạ độ giữa nhánh hiển thị và AI.
2. Với nhánh AI, đo phương án decode/scale/readback phù hợp phần cứng và lấy mẫu để không kéo chậm đường hiển thị. Chưa đổi model/threshold hoặc tần suất inference.
3. Substream là đối chứng tốt và có thể là lựa chọn giảm tải, nhưng đổi sang sub sẽ thay độ phân giải đầu vào AI/face recognition. Cần đánh giá chất lượng trước khi chọn làm cấu hình production; không tự đổi camera.json.
4. Bổ sung quan sát PTS tiến so với monotonic và backend decode thực tế vào telemetry hiện có ở tần suất thấp. Giữ nhãn timestamp sau decode rõ ràng, không gọi receive age là tuổi ảnh từ camera. Không tự thêm reconnect/drop-buffers hoặc nới watchdog dựa trên một probe.

## Tái đo chỉ đọc

```bash
python3 tools/stream_diagnostics.py --seconds 25 --minimal-sampling --output logs/stream_review/recheck_main.json
```

Thêm `--subtype 1` chỉ đổi URI của kết nối đo, không ghi camera.json hay cấu hình thiết bị. Thêm `--decode-only` để bỏ screenshot; `--decode-only --no-scale` bỏ thêm filter scale trên player thử nghiệm. Chạy nối tiếp, tránh mở thêm camera app khi đối chứng. Tool không khởi tạo camera controller hay gọi RPC, không chụp ảnh ra đĩa, không bật motor.

Kiểm chứng: 10 focused tests PASS (`test_stream_diagnostics`, `test_mpv_player`), gồm drift tương đối, timestamp reset, dữ liệu thiếu và capture lifecycle có sẵn. Không chạy lại toàn bộ functional tests.

## Đối chiếu việc trễ xuất hiện sau khi thêm profiling

Người vận hành xác nhận mốc so sánh là cùng AI PTZ trước khi thêm profiling, không phải phanmem_v1.0. Vì vậy so sánh với v1 chỉ giải thích khác biệt kiến trúc, không chứng minh nguyên nhân của thay đổi trước/sau profiling.

Đối chiếu bản sao `/tmp/ptz_profile_original`: `camera/mpv_player.py`, `camera/stream.py` và `runtime/frame_manager.py` hiện giống từng byte với bản trước triển khai profiling. [Evidence hash](../logs/stream_review/profiling_source_comparison.json). Tài liệu [ngày 20/10](arcface_gpu_zoom_20261020.md) đã ghi video_hwdec=no và lấy ảnh 15 FPS. Không có bằng chứng rằng đợt profiling mới chuyển decoder từ GPU sang CPU. Tuy nhiên telemetry sink và GUI/AI instrumentation đã thay đổi, có thể thêm chi phí CPU, lock, GIL và I/O; chưa được loại trừ ảnh hưởng trên toàn pipeline.

Đo thêm ba lượt nối tiếp OFF → ON → OFF với cùng main 4K, capture loop và `FrameManager.publish` production, không AI/GUI. OFF tắt latency sink một cách tường minh; ON bật writer/event/resource sampling hiện có. Mỗi lượt 25 giây, PTS polling tối thiểu:

| Lượt | Khoảng đo PTS | Video tiến | Trễ tăng thêm |
|---|---:|---:|---:|
| Profiler OFF lần 1 | 23,783 s | 15,520 s | 8,263 s |
| Profiler ON | 23,797 s | 15,280 s | 8,517 s |
| Profiler OFF lần 2 | 23,783 s | 15,680 s | 8,103 s |

Evidence: [OFF 1](../logs/stream_review/profiling_ab_off_1.json), [ON](../logs/stream_review/profiling_ab_on.json), [OFF 2](../logs/stream_review/profiling_ab_off_2.json). FrameManager.publish p50 OFF lần 1 là 0,129 ms, ON là 0,506 ms; writer không báo lỗi hoặc dropped log events. Đây là chi phí callback nhận frame; không đại diện cho hàng trăm events/giây của AI/GUI trong ứng dụng đầy đủ.

Kết luận giới hạn: trong điều kiện hiện tại, capture vẫn tích lũy trễ lớn khi tắt profiling sink. Việc bật sink ở riêng FrameManager chưa giải thích được toàn bộ 8 giây; cũng không đủ để kết luận profiling toàn pipeline vô can. Chưa có PTS drift/clock sensor và cấu hình môi trường tương ứng của lần chạy trước khi thêm profiling, nên chưa xác định được tác nhân khiến người vận hành bắt đầu thấy trễ. Cần A/B toàn pipeline trước/sau cùng input, cấu hình, tải và trạng thái hệ thống để định lượng regression; không quy kết CPU decoding, profiling hay thay đổi camera chỉ từ thời điểm xuất hiện.

Tái đo một nhánh bằng công cụ chẩn đoán, không bật camera controls:

```bash
python3 tools/stream_diagnostics.py --seconds 25 --minimal-sampling --frame-profiler off --output logs/stream_review/recheck_off.json
python3 tools/stream_diagnostics.py --seconds 25 --minimal-sampling --frame-profiler on --output logs/stream_review/recheck_on.json
```

Hai lệnh này chỉ đo capture/FrameManager, không giả lập workload AI/GUI hoặc tuyên bố overhead toàn pipeline.
