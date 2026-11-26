# Shadow v4 thử nghiệm: giảm công việc giữ accelerator

Đã triển khai đường batch chỉ dành cho shadow, **mặc định OFF**. Kiểm chứng offline trên 5 capture cho thấy giữ nguyên quyết định identity, giảm thời gian giữ accelerator trung vị và tăng số mẫu hoàn tất trong fixture tranh chấp. Chưa đo v4 cùng camera thật; vẫn còn khoảng mẫu vượt 1 giây, nên chưa coi vấn đề continuity đã được giải quyết hoàn toàn.

## Thay đổi

- Bật bằng `PTZ_SHADOW_BATCHED=1`; khi không bật, scorer tiếp tục dùng đường v3. Shadow vẫn phải được Start trong GUI, mặc định không chạy; Phase 1 motor vẫn OFF.
- Chuẩn bị tensor ảnh bằng preprocessing OSNet hiện có **trước khi xin accelerator**. Khi foreground đang dùng/chờ, giữ CPU tensor của minibatch hiện tại và thử lại admission; không lặp preprocessing.
- Gộp danh sách raw và normalized crops, chia theo batch_size hiện có của model. Chỉ giữ tài nguyên trong mỗi minibatch; nhả giữa các minibatch để foreground có quyền ưu tiên.
- Giữ kết quả các minibatch đã xong trong đúng job/frame, không suy diễn embedding frame cũ thành frame mới. Chỉ một job shadow đang xử lý, một minibatch tensor CPU đang chờ. Hết hạn/reset/đổi target/Stop hủy kết quả theo gate hiện có.
- Dùng nguyên preprocessing, eval network, weights và L2 normalization của OSNet. Backend kiểm tra đúng loại `OSNetReID`, model đang eval, shape 512 chiều, finite và normalized. Không chỉnh phương thức baseline `extract_embeddings`, detector, ByteTrack, selector, scheduler hoặc motor.
- Giữ ngưỡng acquire/keep/reacquire, margin, 3 xác nhận, max-gap 1 giây và max-age 0,8 giây. Không tăng rate cap 5 Hz.

`runtime/identity_shadow_batch.py` chứa backend; `runtime/identity_shadow.py` chọn đường xử lý theo flag. `SHADOW_START.batched` ghi cấu hình thực dùng; source hashes bổ sung backend và OSNet. `completed_passes` ở đường mới đếm minibatch accelerator đã hoàn tất, trong khi v3 đếm extraction raw/normalized (mỗi extraction có thể chứa nhiều minibatch). Không dùng riêng pass count để suy ra tốc độ GPU; benchmark đo outer scheduler hold, loại nested acquisition.

Chưa dùng lại embedding của baseline: baseline không luôn tính mới cho tất cả candidate của frame. Thay đổi này giảm công việc shadow, chưa tạo cơ chế chia sẻ evidence hoặc bảo đảm lấy mẫu liên tục trên camera.

## Kiểm chứng correctness

- **298 regression tests pass**, gồm 30 test shadow/scheduler/batch. [Log](regression.log), [script](run_regression.py). Sau khi bổ sung hash OSNet vào header, chạy lại 30 test shadow/batch đều pass: [log cuối](final_shadow_tests.log).
- Test mới kiểm tra batching/resume không tính lại, candidate ordering, raw/normalized và max/weighted fusion, foreground priority, preprocessing ngoài lane, expiry trước admission, backend/eval sai, output lỗi, empty input và cleanup.
- **4814 frame / 10180 candidate từ 5 capture**, OSNet CUDA thật. Không đổi selected_candidate, reason, state hoặc action của manager trên cùng chuỗi evidence: **0 decision mismatch**. Sai khác số học lớn nhất: embedding 7,83e-7, score 3,28e-7. Đây là kiểm tra tương đương tính toán, không phải accuracy hay GT.
- Baseline source/model/cache hash và từng PNG được xác minh trước khi dùng. Trọng số và các source baseline nằm trong manifest không đổi. [Kết quả](validation_result.json), [script](validate.py).
- Compile và diff whitespace check pass. Tất cả replay/inference dùng offline_guard; không mở camera/motor. Physical commands=0.

## Benchmark trên cùng ảnh

60 frame lấy đều từ hai capture 09:03 và 17:32, chạy cả v3 và v4 trên cùng model; đổi thứ tự chạy giữa các ảnh. Một số shape đã được warm bằng lượt kiểm chứng. Thời gian là wall time và outer scheduler hold, không phải CUDA kernel time. [Từng mẫu](benchmark_rows.json).

| Chỉ số | V3 | V4 batch |
|---|---:|---:|
| Wall time median / job | 34,27 ms | 22,58 ms |
| Wall time P95 / job | 49,85 ms | 53,45 ms |
| Tổng hold median / job | 32,08 ms | 15,90 ms |
| Tổng hold mean / job | 32,77 ms | 22,07 ms |
| Một lần hold dài nhất, P95 | 28,38 ms | 21,24 ms |
| Một lần hold dài nhất, max | 37,16 ms | 70,91 ms |

Median giảm rõ nhưng đuôi wall time và max không cải thiện đồng đều. Không tuyên bố v4 luôn nhanh hơn, không ảnh hưởng baseline, hoặc FPS camera tăng tương ứng.

## Fixture tranh chấp từ log thật

Lấy cùng cửa sổ 12 giây của phiên 17:32: **124 frame, 262 khoảng giữ tài nguyên foreground, tổng hold gốc 9,84 giây**. Replay ảnh và tuổi observation theo timestamp; OSNet shadow chạy thật. Foreground được mô phỏng bằng thread giữ scheduler theo thời lượng log, không chạy lại YOLO/face hoặc RTSP. Do đó đây là bài thử admission có kiểm soát, không phải benchmark live. Chạy 3 cặp, đảo thứ tự cặp giữa các lần. [Kết quả](contention_result.json), [script](contention.py).

| Tổng của 3 lượt, mỗi đường 36 giây | V3 | V4 batch |
|---|---:|---:|
| Comparison hoàn tất | 58 (18/20/20) | 86 (30/27/29) |
| Job stale/canceled | 23 | 12 |
| Khoảng giữa mẫu >1 giây | 13 | 7 |
| Pass hoàn tất nhưng job bị hủy | 15 | 1 |

Số mẫu hoàn tất tăng **48,3% trong fixture này**. Các lần v4 vẫn có gap tối đa 1,59–1,79 giây; v3 khoảng 2,00 giây. P95 chờ foreground không giảm trong mọi lượt (cặp thứ hai v3 11,54 ms, v4 18,03 ms). Không suy rộng fixture thành live FPS, độ chính xác tracking hay guarantee không lag.

## Trạng thái và bước cần camera thật

Đường mới đủ điều kiện **thử shadow có kiểm soát**, chưa đủ chứng cứ bật mặc định hoặc bật Phase 1 điều khiển. Phần offline đã xong; cần người dùng vận hành camera để xác nhận phân bố khoảng mẫu, stale rate và tác động foreground trong thực tế.

Từ `/mnt/files/Project/ptz`, môi trường Python đang dùng:

```bash
PTZ_SHADOW_BATCHED=1 python3 ptz/main.py
```

1. Chọn target, chạy 20 giây khi shadow OFF.
2. Bật record trước, rồi Start Phase 1 shadow. Chạy khoảng 60 giây như phiên trước; quan sát `saved` và độ mượt.
3. Stop shadow trước, rồi Stop record, để baseline chạy thêm 20 giây; chờ lưu xong rồi đóng.

Không cần gán nhãn lại. Nếu saved ngừng tăng hoặc GUI lag rõ, Stop shadow và giữ log. Lần kiểm tra này đánh giá throughput/độ trễ của v4; không thay đổi quyền điều khiển PTZ.

## Rollback và snapshot

Feature rollback: bỏ prefix `PTZ_SHADOW_BATCHED=1` (hoặc dùng `PTZ_SHADOW_BATCHED=0`), khởi động lại để quay về đường v3. Không cần sửa config khác.

Snapshot dirty tree trước sửa ở `../validation/identity_shadow_v4_before/`; `git_state.json` lưu branch, HEAD và trạng thái ban đầu. [changes.json](changes.json) lưu hash trước/sau. Không reset/clean/commit, không sửa raw log/capture hoặc manifest rollback cũ.

Full code rollback có kiểm tra checksum, mặc định dry-run:

```bash
# Chạy tại /mnt/files/Project/ptz/ptz, khi ứng dụng đã đóng:
python3 docs/identity_shadow_v4/rollback.py
# Chỉ thực hiện khi cần gỡ v4:
python3 docs/identity_shadow_v4/rollback.py --apply
```

Tool khôi phục đúng các file có snapshot, chuyển file source/test mới vào quarantine và giữ docs/log/cache. Nếu có sửa thêm sau v4, tool từ chối ghi đè. Dry-run đã pass; không thực thi rollback.
