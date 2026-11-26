# Kiểm tra zoom thủ công và ArcFace GPU — 20/10/2026

## Kết luận từ phiên 12:48

Nguồn: `logs/ptz_20261020_124809.log`,
`logs/latency/latency_trace_20261020_124814_118399.jsonl` và CSV cùng phiên.

- Không có sự kiện ARCFACE_STARTED, ARCFACE_INFERENCE hoặc SCRFD inference.
  Target chưa được xác nhận (`confirm=0/3`), nên nhánh face chưa suy luận.
  Chưa có bằng chứng ArcFace CPU gây chậm zoom trong phiên này.
- Camera command queue: trung bình 1,03 ms, lớn nhất 4,90 ms.
- Zoom RPC: trung bình 48,0 ms, p95 59,6 ms, lớn nhất 104,4 ms.
  Đây là thời gian phản hồi RPC, không phải thời gian motor thực sự bắt đầu/dừng
  hoặc thời gian thay đổi xuất hiện trên màn hình.
- YOLO total trung vị 55,2 ms; ReID total trung vị 163,3 ms. Không có ArcFace
  để đo mức tranh chấp CPU/GPU trong phiên này.

Đối với thao tác thủ công:

1. GUI chỉ gửi yêu cầu vào `ManualController`; không trực tiếp đợi ArcFace.
2. Worker tắt Auto Zoom/Focus, chờ quyền điều khiển camera, rồi gửi lệnh.
3. Khi nhả nút, `finally` gửi STOP; sau đó quyền điều khiển có khoảng chờ
   `AUTO_ZOOM_SETTLE_TIME=0.20` giây.
4. Yêu cầu nhả nút (`command=None`) còn có thể được lấy ra ở vòng tiếp theo và
   gửi STOP lần nữa. Log có ví dụ STOP lúc 12:48:43.162 và 12:48:43.464.
   Các bước này có thể làm thao tác nhấn lại/đổi chiều nhanh bị chờ thêm.
   Chưa đo được thời gian từ callback nút đến camera trong log cũ để định lượng
   chính xác ảnh hưởng cho từng thao tác.
5. Video hiện dùng mpv, giải mã software (`video_hwdec=no`), lấy hình 15 FPS;
   GUI hẹn cập nhật mỗi 33 ms sau khi render. Đây là một đường trễ khác, không
   được đo bởi `zoom_command_latency_ms`. Chưa có số đo camera-to-display.

Đã bổ sung MANUAL_REQUEST, MANUAL_WORKER_BEGIN, MANUAL_DISPATCH,
MANUAL_COMMAND_RETURN, MANUAL_STOP_BEGIN/RETURN. Chúng đo từ lúc callback gửi
yêu cầu, không đo thời điểm click vật lý trước khi Tk xử lý sự kiện. Lần chạy
tiếp theo có `manual_queue_wait_ms`, `manual_request_to_dispatch_ms`,
`manual_command_call_ms`, `manual_stop_call_ms`. Giữ nguyên quy tắc STOP,
quyền điều khiển và thời gian settle trong đợt kiểm tra này.

## Thử nghiệm GPU riêng, không kết nối camera

ORT 1.23.0, Torch 2.8.0, CUDA provider; CUDA_MODULE_LOADING=LAZY;
arena 1024 MiB/session, kSameAsRequested, HEURISTIC, max workspace=0.

| Phép thử | Kết quả |
| --- | --- |
| ArcFace FP32 + SCRFD session | Nạp được; ArcFace suy luận trung vị 25,45 ms trên input zero |
| Thêm YOLO và OSNet rồi chạy SCRFD 640×640 | SCRFD lỗi NvMap/AllocateRawInternal ở buffer 4.915.200 byte |
| Thay ArcFace bằng FP16, cùng trình tự | Cả YOLO, OSNet, SCRFD và ArcFace suy luận thành công |
| Sau phép thử FP16 bốn model | Snapshot CUDA free khoảng 1.475 MiB |

Các phép thử tuần tự diễn ra ở trạng thái RAM hệ thống khác nhau, nên không
thể lấy chênh lệch free RAM làm mức tiết kiệm RAM chính xác của FP16. Chúng chỉ
chứng minh một cấu hình vượt qua phép thử và cấu hình kia thất bại trong lần
đo đó. Snapshot free RAM không bảo đảm lần cấp phát NvMap tiếp theo thành công.
Phép thử chưa gồm RTSP, Tk, model focus CNN, chạy dài hoặc nhiều người.

Đã chuyển bản sao `w600k_r50.onnx` thành `w600k_r50.fp16.onnx`,
giữ input/output float32 và kiểm tra ONNX graph:

- File gốc: 174.383.860 byte; FP16: 87.206.711 byte.
- 36 ảnh database được detect/align bằng SCRFD CPU rồi dùng cùng crop cho hai model.
- ArcFace FP32 CPU: median 314,38 ms, p95 323,09 ms.
- ArcFace FP16 GPU: median 17,43 ms, p95 29,24 ms.
- Cosine embedding nhỏ nhất: 0,99999750; trung bình 0,99999836.
- Kết quả matcher giữ nguyên 36/36; sai lệch score lớn nhất 0,00000263.
- Đây là ảnh từ database, không phải tập đánh giá độc lập. Không suy ra độ chính
  xác trên người lạ, góc mặt mới hoặc camera trực tiếp.

Log và script thí nghiệm lưu tại `logs/diagnostics/arcface_20261020/`.
Các script đó là bản lưu thí nghiệm, có đường dẫn `/tmp` của phiên kiểm tra.
Tool tái tạo model: `python3 tools/convert_arcface_fp16.py` (không ghi đè file đã có).

## Cách thử trong ứng dụng

Thoát phiên PTZ cũ rồi chạy từ thư mục dự án:

```bash
PTZ_ARCFACE_DEVICE=cuda \
PTZ_ARCFACE_MODEL="$PWD/models/face/w600k_r50.fp16.onnx" \
PTZ_ORT_GPU_MEM_MB=1024 \
bash run_camera.sh
```

Kiểm tra log model path là file FP16 và FaceRecognizer dùng CUDAExecutionProvider.
Đã thêm log `[ONNX] CUDA requested arena_limit_mib=...` trước mỗi CUDA session.
Giá trị này là trần arena yêu cầu, không phải tổng bộ nhớ CUDA.
Cập nhật sau kiểm tra: mặc định ứng dụng đã dùng ArcFace FP16 trên CUDA,
arena 1024 MiB/session. Chỉ cần `python3 main.py`; entry point cũng bật
CUDA_MODULE_LOADING=LAZY khi chưa được đặt trong môi trường.
Muốn dùng ArcFace CPU, chạy `PTZ_ARCFACE_DEVICE=cpu python3 main.py`:
cấu hình sẽ tự chọn lại model FP32 gốc nếu không có override PTZ_ARCFACE_MODEL.

Ưu tiên thử FP16 trước. Nếu vẫn thiếu bộ nhớ khi chạy toàn ứng dụng, cần đo thêm
peak memory của RTSP/focus và warm-up cả bốn model; không tăng arena liên tục.
TensorRT FP16 là hướng tiếp theo nhưng cần kiểm tra riêng engine build, workspace
và độ chính xác; chưa được triển khai/đánh giá ở đây.

Nguồn kỹ thuật:
- [ONNX Runtime FP16](https://onnxruntime.ai/docs/performance/model-optimizations/float16.html):
  chuyển FP16, giữ kiểu I/O và đánh giá sai số.
- [CUDA provider options](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html):
  arena, thuật toán cuDNN và workspace.

Kiểm tra code: cú pháp các file đổi; 1 test manual worker, 4 test control regression
và 7 test mpv đều qua. Không gửi lệnh motor trong các phép kiểm tra này.
