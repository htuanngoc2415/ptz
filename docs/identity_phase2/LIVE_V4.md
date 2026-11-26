# Kiểm chứng live v4

Trạng thái cập nhật sau phiên 19:13:52 ngày 19/11/2026: **VERIFIED về log vận hành**. Phiên `shadow_36b5020b7c7843828b19de10f27d711e` ghi `batched=true`, đóng hoàn tất và replay khớp 276/276 quyết định. [Audit live](../identity_shadow_audit_20261119_191352/REPORT.md), [kết quả Phase 2 tiếp nối](../identity_phase2_v4_followup/REPORT.md). Trạng thái này không chứng nhận identity accuracy hoặc không có overhead. Quy trình dưới đây dùng cho những phiên kiểm chứng tiếp theo, hiện chưa cần quay thêm chỉ để hoàn thành audit này.

Không bật Phase 2 để làm phép đo này. Các cờ Phase 2, `IDENTITY_MEMORY_ENABLED` và gallery đều OFF; baseline tiếp tục điều khiển PTZ. Không cần tự sửa ngưỡng.

Từ `/mnt/files/Project/ptz`, trong môi trường Python hiện dùng:

```bash
python3 ptz/main.py
```

V4 hiện là mặc định khi bật shadow trong GUI. Không cần đặt biến môi trường;
nếu shell đã đặt `PTZ_SHADOW_BATCHED=0` thì bỏ biến đó bằng `unset PTZ_SHADOW_BATCHED`.
Chỉ dùng `PTZ_SHADOW_BATCHED=0` khi cần đối chiếu hoặc quay lui v3.
Thay đổi mặc định không đồng nghĩa đã kiểm chứng v4 live và không bật quyền điều khiển PTZ cho shadow.

1. Chọn target, chạy 20 giây shadow OFF.
2. Start record trước, Start Phase 1 shadow sau. Chạy khoảng 60 giây, có đổi hướng và crossing nếu phù hợp.
3. Stop shadow trước, Stop record, tiếp tục 20 giây OFF. Chờ capture hoàn tất rồi đóng.
4. Giữ log/capture; ghi lại thời điểm nếu có lag hoặc saved ngừng tăng. Nếu lag rõ, Stop shadow.

Phân tích sau khi đóng app, từ Git root `/mnt/files/Project/ptz/ptz`; thay tên file bằng phiên vừa chạy và dùng output mới:

```bash
PYTHONPATH=. python3 tools/analyze_identity_live.py \
  --shadow logs/identity_shadow/shadow_ID.jsonl \
  --trace 'logs/latency/latency_trace_TIMESTAMP.jsonl*' \
  --output /tmp/identity_live_v4_audit.json
```

Hoặc liệt kê trạng thái mọi shadow log, không tính latency trace:

```bash
PYTHONPATH=. python3 tools/analyze_identity_live.py --output /tmp/identity_live_inventory.json
```

Output gồm runtime v3/v4, source hashes được ghi trong log, checksum các file đầu vào, comparison/s, gap P50/P95/max và số gap >1 giây, tuổi mẫu, counters stale/cancel từ footer, ON/OFF foreground wait, GUI latency, detection result Hz và thời gian trace còn giữ được.

`VERIFIED` trong script chỉ nghĩa có log v4 hoàn tất, có comparison và các kiểm tra cấu trúc/tuổi/quyền điều khiển đạt. Nó **không** chứng nhận chất lượng identity, cải thiện FPS hay overhead bằng 0. Các cửa sổ ON/OFF khác cảnh; trace rotation có thể làm thiếu phần đầu. Không so sánh những khoảng thiếu dữ liệu như benchmark có kiểm soát.

Sau phiên chạy cần kiểm tra thêm capture/source/model manifest, same-frame replay và các điểm bất đồng trước khi kết luận. Không có camera hay motor nào được khởi động bởi các công cụ phân tích offline.
