# Baseline 08:38 — đang dùng để phát triển

Theo lựa chọn của người dùng, workspace `/mnt/files/Project/ptz` hiện sử dụng nguyên mã checkpoint `ede31989fe6a39b04d7cd4d31675ebd811a28397` làm baseline. Đây là ứng viên phiên 08:38 đã được lựa chọn; không có thêm bằng chứng chứng minh chính xác trạng thái đã chạy trong phiên đó.

- Backup baseline độc lập, đã kiểm tra SHA256 và quyền truy cập: `/mnt/files/Project/AI_PTZ_BASELINE_0838_BACKUP_20261116_114845` (671 file).
- Toàn bộ workspace trước khi thay, gồm cả dữ liệu và mã mới nhất: `/mnt/files/Project/PTZ_BEFORE_BASELINE_0838_20261116_114845`. Được chuyển nguyên thư mục, không xóa file.
- Bản ứng viên ban đầu vẫn còn nguyên: `/mnt/files/Project/AI_PTZ_RESTORED_20261116_0838_CANDIDATE`.
- 195 file tracked của baseline khớp hash lịch sử, 193 file Python kiểm tra cú pháp thành công, Git diff rỗng sau chuyển. Không sửa thuật toán hoặc config.
- Tài liệu và log cũ được giữ trong `ptz/docs`, `ptz/logs`; các tài nguyên ngoài thư mục ứng dụng, gồm video, vẫn giữ ở workspace mới. Model/data của baseline lấy từ bản ứng viên đã xác minh. Dữ liệu đầy đủ của workspace trước khi chuyển nằm trong thư mục lưu phía trên.
- Kiểm thử trước đó: 586 đạt, 3 lỗi thiếu gi/GStreamer. Smoke test sau chuyển được lưu tại `validation/smoke.log` và `validation/smoke_result.json`. Không chạy camera thật hoặc phát lệnh PTZ.

## Chạy baseline và phát triển tiếp

```bash
cd /mnt/files/Project/ptz
conda activate py
python ptz/main.py
```

Sửa mã trong `/mnt/files/Project/ptz/ptz`. Repository Git của checkpoint nằm ngay tại thư mục này. Không sửa bản backup để giữ mốc baseline.

## Chạy lại bản mới đã lưu

Đóng ứng dụng đang chạy trước khi mở bản khác:

```bash
cd /mnt/files/Project/PTZ_BEFORE_BASELINE_0838_20261116_114845
conda activate py
PTZ_IDENTITY_TRACKING=1 PTZ_IDENTITY_FIX=1 PTZ_IDENTITY_REID_OPTIMIZED=1 python ptz/main.py
```

## Lấy lại baseline sạch trong thư mục mới

Chọn một đường dẫn đích chưa tồn tại:

```bash
cp -a /mnt/files/Project/AI_PTZ_BASELINE_0838_BACKUP_20261116_114845 /mnt/files/Project/PTZ_BASELINE_0838_FRESH
cd /mnt/files/Project/PTZ_BASELINE_0838_FRESH
conda activate py
python ptz/main.py
```

Báo cáo điều tra lịch sử gốc nằm tại `/mnt/files/Project/AI_PTZ_BASELINE_0838_BACKUP_20261116_114845/RESTORATION_REPORT.md`. Lệnh quay về workspace cũ trong báo cáo đó được thay thế bởi hướng dẫn ở đây vì đường dẫn gốc nay đã là baseline.
