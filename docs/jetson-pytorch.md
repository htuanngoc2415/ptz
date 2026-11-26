# Môi trường GPU đã kiểm chứng — 2026-10-07

Máy: NVIDIA Jetson Orin Nano Super, aarch64, Ubuntu 22.04, L4T R36.4.7,
gói JetPack 6.2.1, CUDA toolkit 12.6, cuDNN hệ thống 9.3.

Python: `~/miniconda3/envs/Autofocus_Camera/bin/python` (3.10.22).

Đã thay torch 2.13.0/cu130 và torchvision 0.28.0 bằng:

- torch 2.8.0 (CUDA 12.6, wheel Jetson).
- torchvision 0.23.0 (wheel Jetson).
- NumPy 1.26.4 (ABI phù hợp wheel Jetson).
- OpenCV 4.11.0.86 (không yêu cầu NumPy 2 như OpenCV 5).

Nguồn torch/torchvision: <https://pypi.jetson-ai-lab.io/jp6/cu126>.
Lựa chọn theo [tài liệu PyTorch cho JetPack 6.2](https://docs.pytorch.org/TensorRT/v2.9.0/getting_started/jetpack.html).
Không thay driver hay JetPack.

Chạy `bash run_camera.sh`. Launcher đặt `CUDA_MODULE_LOADING=LAZY` để giảm
bộ nhớ nạp CUDA. Jetson chia sẻ RAM giữa CPU/GPU; khi RAM không đủ, cần đóng
ứng dụng không cần thiết. Không coi số GPU bằng 1 là bằng chứng inference chạy được.

Đã kiểm tra trên GPU thật, không điều khiển camera:

- `torch.cuda.is_available() == True`, tên GPU `Orin`.
- Phép nhân ma trận CUDA, torchvision NMS CUDA.
- YOLO với `models/person/person.pt`, tham số model ở CUDA, inference ảnh giả thành công.
- OSNet với checkpoint của dự án, embedding 512 chiều ở CUDA, giá trị hữu hạn.
- 153 unit tests đạt sau khi thay thư viện.

Khi bổ sung gói, dùng `-c constraints-jetson.txt` để tránh nâng nhầm phiên bản.
Không cài torch/torchvision từ PyPI mặc định; constraints chỉ ghim phiên bản,
không thay thế yêu cầu dùng nguồn wheel Jetson ở trên.

Bản sao lưu tạm của các gói đã thay: `/tmp/ptz-gpu-rollback/`.
Danh sách gói trước thay: `/tmp/ptz-before-gpu-install.txt`.
Kết quả kiểm thử GPU: `/tmp/ptz-gpu-verify.log`.
Các file `/tmp` có thể bị hệ thống dọn khi khởi động lại.
