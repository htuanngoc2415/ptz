# CUDA OOM và vòng lặp inference lỗi — 09/10/2026

## Bằng chứng

- `logs/ptz_20261009_101424.log:26`: 10:14:27.634, GPU probe đã báo CUDA out of memory trước khi pipeline model chạy. Áp lực bộ nhớ/CUDA đã có từ bước startup của phiên này.
- Phiên kế tiếp `logs/ptz_20261009_101454.log`: YOLO load lúc 10:15:01.172; lỗi PERSON đầu tiên 10:15:03.518, trong lúc chuẩn bị target. ReID bị DISABLED cùng thời điểm.
- `logs/ptz_20261009_101454.jsonl`: 223 CUDA_MEMORY events từ 10:15:03.518 đến 10:15:19.713. Allocated luôn 40,113,664 bytes (38.255 MiB); reserved luôn 77,594,624 bytes (74 MiB). Không có bằng chứng allocator PyTorch tăng dần trong đoạn lỗi này.
- Dòng CUDA asynchronous chỉ là thông báo rằng vị trí báo lỗi có thể khác vị trí phát sinh. Log cũ thiếu traceback đầu tiên và driver memory nên không xác định được kernel/allocation chính xác.
- Kiểm tra chỉ đọc ngoài sandbox: GPU là Orin (nvgpu), NVIDIA-SMI không cung cấp số memory total/used/free trên máy này.
- Mẫu `tegrastats` **10:19:18**, sau thời điểm lỗi: RAM 6440/7619 MB; swap 1798/3810 MB; lfb 3x4 MB. Đây là bằng chứng áp lực bộ nhớ ở thời điểm đo, không chứng minh chính xác tình trạng lúc 10:15:03. Không suy ra leak hoặc fragmentation chỉ từ lfb/allocator counters.

Không có bằng chứng lỗi do zoom ownership hoặc model weights mới. File sửa zoom không chứa GPU allocation. Chưa thể chốt nguyên nhân OOM ban đầu là tiến trình nào hay allocation nào; số allocated/reserved của PyTorch không phải toàn bộ bộ nhớ máy/GPU.

## Lỗi mã nguồn đã sửa

`ai/detector.py::YOLODetector.detect` trước đây bắt exception, return `[]`, rồi xóa last_error và gọi lại cùng CUDA predictor ở frame tiếp theo. Hậu quả: một lần OOM thành hàng trăm lần thử lại và log lỗi lặp. Việc parse `.item()` / `.cpu()` nằm ngoài catch predict; CUDA asynchronous error ở đó có thể bị bỏ qua thành một detection lỗi hoặc trả kết quả một phần.

Thay đổi:

- `_inference_failure`: nhận diện CUDA/CUBLAS/CUDNN error, latch fatal_error cho instance, ghi traceback/stage/model/device/imgsz/frame shape và diagnostics một lần.
- `detect`: khi fatal_error đã latch, giữ nguyên last_error, return `[]` và **không gọi predict/CUDA lại**. Không tự reload model, tự chuyển CPU hoặc bật retry mỗi frame.
- CUDA error ở chuyển kết quả GPU→CPU cũng latch, loại bỏ toàn bộ detections của frame, không trả kết quả một phần.
- Exception không phải CUDA vẫn có thể retry ở frame kế tiếp; success xóa last_error. Không coi lỗi input thông thường là CUDA context hỏng.
- Memory diagnostics dùng device được cấu hình; ghi allocated/reserved và driver free/total nếu đọc được. Nếu diagnostics cũng lỗi, ghi trạng thái unavailable mà không che exception gốc.
- `runtime/inference_worker.py::handle_detector_failure`: yêu cầu STOP, disable Auto Zoom, invalidate target/session và face jobs qua reset, xóa kết quả tracking cũ; frame lỗi không được dùng làm target-loss evidence để chạy recovery.
- Nhánh lỗi bỏ qua phần ReID/face/framing của frame. Báo UI một lần; nếu người dùng bật lại Auto Zoom trong khi detector vẫn lỗi, controller tiếp tục chặn. Không tự bật lại điều khiển sau error.

Không thay model weights, input size, precision, CUDA allocator settings, zoom calibration hoặc các timeout. Không gọi empty_cache mỗi frame. PyTorch mô tả empty_cache chỉ giải phóng cache không dùng, không giải phóng tensor còn sống; vì vậy đây không phải cách sửa vòng lặp lỗi hay bảo đảm OOM hết. [PyTorch CUDA memory management](https://docs.pytorch.org/docs/2.14/notes/cuda.html#memory-management).

## Kiểm thử

**238/238 PASS**, 14 suites, 5.346 giây:

- 5 tests mới trong `test/test_cuda_failure.py`: một predict cho 20 frame OOM; diagnostics lỗi không làm exception thoát; non-CUDA error có thể phục hồi; CUDA failure khi parse loại bỏ detections một phần; worker disable/invalidate một lần và không tự bật lại zoom.
- 233 regression về zoom, identity, scheduling, ownership, camera adapter, runtime, crossing/final framing vẫn PASS.
- Compile PASS: `ai/detector.py`, `runtime/inference_worker.py`, `test/test_cuda_failure.py`.
- Predictor và CUDA memory APIs được mock. Không chạy model inference trên GPU thật, không kết nối hoặc gửi motor command tới camera. Kiểm tra NVIDIA-SMI/tegrastats chỉ đọc.
- Runner đầy đủ: `/tmp/run_cuda_regressions.py`; output: `/tmp/cuda_all_regressions.log`. Runner append đường dẫn GI hệ thống sau site-packages của môi trường Conda, tránh import nhầm SymPy hệ thống.

## Chạy lại

Process đang chạy cần khởi động lại để nhận code mới và tạo lại CUDA state. Giảm các ứng dụng không cần thiết đang dùng RAM trước khi thử GPU lại. Không có tiến trình nào bị dừng tự động trong lần sửa này.

Nếu chủ động chọn CPU:

```bash
PTZ_DEVICE=cpu bash run_camera.sh
```

CPU mode không được kích hoạt tự động vì có thể thay đổi latency. Chưa chạy camera hoặc model thật sau sửa nên chưa chứng minh OOM ban đầu đã hết. Bản sửa chứng minh việc không lặp CUDA vô hạn và không điều khiển bằng kết quả inference lỗi; lần chạy GPU tiếp theo sẽ ghi rõ traceback và memory diagnostics đầu tiên để tiếp tục xác định nguyên nhân nếu vẫn OOM.
