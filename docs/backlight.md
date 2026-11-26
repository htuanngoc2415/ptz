# Cấu hình ngược sáng cố định

Đã hoàn tác tính năng tự động điều chỉnh sáng theo người/khuôn mặt.
Ứng dụng chỉ áp dụng `backlight.values` trong `camera.json` một lần khi khởi
động, trên worker riêng, rồi đọc lại để xác nhận. Không đo sáng để tự tăng,
giảm WDR/BLC/HLC trong luồng nhận diện; không chờ ổn định ảnh do tự chỉnh sáng.

Cấu hình trở về bộ thông số trước khi thêm tự động:

```json
"backlight": {
  "enabled": true,
  "values": {
    "ExposureMode": 1,
    "wdr_sensor": 1,
    "wdr_level_sensor": 80,
    "blc_level": 32,
    "hlc_enable": 1,
    "scene_mode": 1,
    "AE_strategy_mode": 1
  }
}
```

WDR/BLC nhận 0–255, các trường khác nhận 0 hoặc 1. Đây là cấu hình cố định
ban đầu của tính năng, không phải mặc định nhà sản xuất. Chỉ ghi trường
camera có trả về; giữ các trường cấu hình khác. Giá trị null hoặc bỏ khỏi
`values` sẽ không được sửa. `enabled: false` bỏ qua cấu hình khi khởi động,
không tự khôi phục giá trị đã lưu trên camera.

GUI giữ dòng **Ngược sáng** hiển thị số giá trị xác nhận được. Log
`[BACKLIGHT]` ghi trạng thái từng trường. `verified` là đọc lại đúng giá trị;
`unsupported` là thiếu trường; `unverified` là không khớp; `error` là lỗi API,
mạng hoặc phản hồi; `cancelled` là hủy khi đóng ứng dụng.

API dùng `GetFaceConfig`/`SetFaceConfig`,
`getImageAdjustment`/`setImageAdjustment`,
`getImageAdjustmentEx`/`setImageAdjustmentEx`. Tên và cấu trúc phản hồi tùy
firmware. Có thể ghi đè tên action qua `backlight.actions`.

Đóng hẳn ứng dụng cũ rồi mở lại để nạp bản đã hoàn tác.

```bash
python3 -m unittest discover -s test -p 'test_image_settings.py' -v
```
