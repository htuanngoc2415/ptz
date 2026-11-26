# Review độc lập Phase 2B

Mở `index.html` bằng trình duyệt trên máy này. Không cần camera, server hoặc Internet.

1. Nhập tên/mã người review và chọn development. Ảnh reference xác định target; không dùng số box/track làm identity.
2. Xem cả chuỗi trước/sau khi cần. Tắt “chỉ frame ưu tiên” để xem đủ các frame, tránh suy đoán một ảnh mơ hồ.
3. Với từng bbox, nhập `target`, `other_1`, `other_2`… theo người ổn định, hoặc `UNKNOWN` nếu chưa biết. Mặc định luôn UNKNOWN; không có nhãn tự điền từ policy.
4. Chọn visibility, tư thế và sự kiện. `OUT_OF_FRAME` khác `FULL_OCCLUSION`; không thấy target không đủ để kết luận bị che khuất. Ghi chú nếu detector bỏ sót target đang nhìn thấy.
5. Bấm “Xác nhận frame”, rồi “Xuất nhãn JSON”. Trang giữ nhãn trong bộ nhớ, vì vậy phải xuất trước khi đóng. Có thể nạp lại JSON để làm tiếp.
6. Review tập retrospective_holdout riêng. Không dùng nhãn holdout để chỉnh tham số; tập này từng được phân tích nên không thay thế một holdout mới hoàn toàn.

43 frame của review pack cũ được giữ trong danh sách ưu tiên development. Ngoài ra có đủ 205 disagreement, frame lấy mẫu đều không dựa trên kết quả policy và vùng quanh thay đổi tập track ID. Bộ ảnh đầy đủ: development 1.120 frame; retrospective_holdout 1.081 frame. Việc có frame gần bbox giao nhau không tự xác nhận đã có crossing; người review cần gán sự kiện.

Từ `/mnt/files/Project/ptz/ptz`, kiểm tra file vừa xuất:

```bash
PYTHONPATH=. python3 tools/phase2b_review.py \
  --pack docs/identity_phase2b/review/pack.json \
  --annotations /duong/dan/phase2b_annotations.json
```

Tính metrics sau review, xuất sang tên file chưa tồn tại:

```bash
PYTHONPATH=. python3 tools/phase2b_evaluate.py \
  --pack docs/identity_phase2b/review/pack.json \
  --annotations /duong/dan/phase2b_annotations.json \
  --benchmark docs/identity_phase2b/benchmark_final \
  --output docs/identity_phase2b/reviewed_metrics.json
```

UNKNOWN không được tính là distractor; khoảng chưa có nhãn làm đứt chuỗi tính duration/recovery. Metrics sparse chỉ mô tả phần đã review. Để đo thời gian bám đúng/sai, ID switch, fragmentation và recovery, cần review liên tiếp toàn bộ các đoạn được chọn, không chỉ một vài ảnh rời. Công cụ báo riêng identity confirmed và provisional localization.
