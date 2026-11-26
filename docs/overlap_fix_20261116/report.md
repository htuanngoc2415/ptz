# Sửa minor overlap trên baseline ByteTrack + OSNet — 16/11/2026

## Kết quả

Đã sửa một cơ chế gây mất khóa có thể tái hiện: giao nhỏ ở cả hai bbox không còn tự động đi qua gate overlap nguy hiểm. Giữ ByteTrack + OSNet, ngưỡng similarity/margin, 3 lần xác nhận, bounded HOLD/recovery và kiểm tra session. Không đổi PTZ transport, tốc độ motor, zoom/focus, face thresholds, GUI, weights hoặc gallery.

**605/608 mục unit test đạt; 3 lỗi có sẵn do thiếu gi/GStreamer.** 19 test mới đạt, bao gồm tiny overlap, mô phỏng hand contact, severe overlap, ID switch, stale result và target disappearance. Synthetic success không chứng minh độ chính xác danh tính ngoài đời. Chưa chạy camera thật.

## A. Backup và nguồn

- Backup đầy đủ, không loại trừ: `/mnt/files/Project/ptz_baseline_0838_before_overlap_fix_20261116_122024/project`.
- **8.798 mục** đã xác minh trước sửa: SHA256 file, symlink và permission. Manifest, Git status, commit và RESTORE.md nằm ở thư mục cha backup. Backup có source, Git metadata, untracked files, configs, models, target images, embeddings, logs và video.
- Baseline commit `ede31989fe6a39b04d7cd4d31675ebd811a28397`; working tree tracked sạch trước sửa. Đây là ứng viên 08:38 được người dùng chọn, không có thêm bằng chứng chứng minh revision đúng tuyệt đối với phiên 08:38.
- Branch làm việc: `fix/minor-overlap-20261116`. Không reset/clean, không xóa baseline backup độc lập trước đó. Thay đổi chưa commit.
- Kiểm tra cuối: chỉ 3 file tracked thay đổi; 51 file model/data/target trùng byte backup. Xem [integrity.json](integrity.json).

Để quay lại baseline: đóng ứng dụng hiện tại rồi chạy:

```bash
cd /mnt/files/Project/ptz_baseline_0838_before_overlap_fix_20261116_122024/project
conda activate py
python ptz/main.py
```

Nếu muốn bản recovery riêng để sửa tiếp, đường dẫn đích phải chưa tồn tại:

```bash
cp -a /mnt/files/Project/ptz_baseline_0838_before_overlap_fix_20261116_122024/project /mnt/files/Project/PTZ_0838_RECOVERED_BEFORE_OVERLAP
cd /mnt/files/Project/PTZ_0838_RECOVERED_BEFORE_OVERLAP
conda activate py
python ptz/main.py
```

## B. Cơ chế và mức độ bằng chứng

### Cơ chế đã tái hiện

1. `ai/overlap_policy.py::overlap_geometry` dùng `intersection / min(target_area, competitor_area)`; `HybridReIDTargetSelector._candidate_overlap` cũng dùng tiêu chí này. Ngưỡng REID_CROSSING_OVERLAP=0,15.
2. Ví dụ target `(100,100,200,300)`, competitor `(196,100,216,300)`: giao chỉ **4% target**, nhưng **20% competitor**, IoU khoảng **3,45%**. Gate cũ coi đây là overlap cần xử lý đặc biệt.
3. Khi đang locked, `OverlapMemory.assess` chỉ cho weak overlap một khoảng grace **0,35 s** trừ khi đã có verification đúng tập competitor. Nếu chưa có bằng chứng phù hợp, lý do thành WEAK_OVERLAP_UNVERIFIED_TIMEOUT/OVERLAP_WITHOUT_TRUSTED_CONTINUITY; `_select_person` gọi `_hold('PERSON_OVERLAP')`.
4. Khi chưa locked hoặc đang recovery, gate hình học còn có thể chặn candidate trước khi xác nhận. Nhánh all-candidates-blocked bỏ qua scoring và reset confirm/pending_count; `ReIDTargetSelector.select_person` hoặc nhánh recovery Hybrid cũng reset nếu candidate bị gate chặn. Điểm OSNet cao không tự vượt qua gate.
5. `ReIDTargetSelector.finish_frame` không nhận person hợp lệ sẽ tăng missing; cũng dùng điều kiện số frame và REID_LOST_GRACE_SEC rồi reset về SEARCHING. Vì thế có chuỗi **giao nhỏ → geometry gate → không có person được chọn → HOLD → mất khóa sau grace** ngay cả với synthetic tracker ID không đổi.

Đây là tương tác **C/D/E/F** trong yêu cầu: overlap gate, bỏ qua/chặn ReID, reset confirmation và state transition. Không cần ByteTrack đổi ID hay YOLO đổi bbox để tái hiện. Không kết luận mọi lần mất target trong log đều có cùng nguyên nhân.

### Đối chiếu log 11:53

Latency rotations còn giữ chỉ bao phủ **11:55:25.638–11:56:33.779642**. Hai đoạn **11:54:36–11:54:42** và **11:54:57–11:55:09** chỉ còn application sampled scores/counters và camera telemetry liên quan, không có đủ dữ liệu association/ReID từng frame để xác định gate cụ thể hay chứng minh top1 là người đúng. Không suy ra chính xác nguyên nhân chỉ từ điểm cao hoặc tổng counter overlap.

Các transition HOLD còn trong trace:

| Thời điểm | Frame | Lý do ghi trong trace |
|---|---:|---|
| 11:55:26.473 | 2882 | PERSON_OVERLAP |
| 11:55:30.696 | 3008 | AMBIGUOUS_IDENTITY |
| 11:55:38.710 | 3250 | PERSON_OVERLAP |
| 11:56:02.880 | 3974 | PERSON_OVERLAP |
| 11:56:05.058 | 4040 | PERSON_OVERLAP |

Tại frame 4037, trace ghi inference/match xong, normalization bắt đầu trước khi frame 4040 vào HOLD. REID_PHASE_ERROR và REID_ERROR cùng lúc **11:56:05.075** là hai span của cùng công việc, không phải bằng chứng hai lỗi gốc. Không có exception type/message/stack cũ. Source có khả năng hủy GPU admission khi epoch hết hiệu lực, nhưng **chưa chứng minh đó là exception thực tế**. Không quy lỗi này là nguyên nhân HOLD đã xảy ra trước.

## C. Thay đổi

| File | Thay đổi |
|---|---|
| `ai/overlap_policy.py` | Thêm competitor_cover và `minor_contact`; lọc giao nhỏ trước logic ambiguity/weak-overlap cũ. |
| `ai/hybrid_reid.py` | Áp dụng cùng tiêu chí cho prefilter/acquisition/recovery; confirmation acquisition gắn cùng track ID và thời gian, recovery thêm giới hạn khoảng cách thời gian; diagnostics tùy chọn; log exception ở worker trước khi future có thể bị bỏ do stale. |
| `config.py` | Cờ và tham số bên dưới. |
| `test/test_minor_overlap.py` | 19 regression tests dùng synthetic/mock, không mở camera. |
| `docs/overlap_fix_20261116/` | Báo cáo, script so sánh, trích log, dataset geometry và kết quả. |

Tham số mới:

- `REID_MINOR_OVERLAP_ENABLED`: mặc định bật; `PTZ_MINOR_OVERLAP=0` tắt nhánh minor-contact, nhưng vẫn giữ diagnostics và siết confirmation. Muốn quay lại toàn bộ baseline dùng backup.
- `REID_MINOR_TARGET_COVER=0.05`, `REID_MINOR_COMPETITOR_COVER=0.25`, `REID_MINOR_IOU=0.05`: **phải đạt cả ba**. Đây là ranh giới bảo thủ cho trường hợp giao nhỏ, chưa được hiệu chỉnh bằng ground truth camera. Bbox nhỏ bị chứa hoàn toàn không được miễn gate chỉ vì target coverage nhỏ.
- `REID_CONFIRM_MAX_GAP=1.0` giây: bằng chứng xác nhận liên tiếp phải cùng candidate ID, bbox liên tục và khoảng cách thời gian hữu hạn; không hạ số lần 3/3 hoặc similarity/margin.
- `REID_OVERLAP_DIAGNOSTICS`: bật bằng `PTZ_OVERLAP_DIAGNOSTICS=1`, mặc định tắt.

Moderate/severe overlap vẫn dùng policy hiện có: bounded weak grace + xác minh competitor, hoặc HOLD ngay khi ambiguity mạnh. Minor contact được xử lý như quan sát hình học bình thường, **không tự cấp identity**: motion discontinuity vẫn HOLD; ID khác cần đủ 3 score hợp lệ; margin mơ hồ hoặc xác minh âm vẫn bị chặn; identity hết hạn vẫn HOLD; dữ liệu stale/out-of-order không gia hạn phiên. Không dùng bbox dự đoán không quan sát được để điều khiển.

Event `OVERLAP_DECISION` tối đa 2 Hz khi bật, có frame/time, target/candidate/competitor IDs và bbox, IoU, target/competitor coverage, xác nhận trước/sau, score/margin, lý do, state trước/sau ở selector, quan sát/identity/lost và quyền control ở selector. `continuity_confidence` là nhãn trusted/unconfirmed kèm timestamp evidence, không phải xác suất được hiệu chuẩn. `candidate_accepted` là đã trả về target, `confirmation_admitted` là tăng bộ đếm. State ở đây trước bước `finish_frame`; motor/session/freshness gate downstream vẫn có quyền chặn dù selector cho phép.

`REID_VERIFICATION_EXCEPTION` ghi type, message, traceback, frame, phase, session, track và superseded ngay ở producer. Vì vậy vẫn giữ được lỗi dù consumer bỏ future stale. Không thay exception thành negative identity observation. Test lỗi ValueError tổng hợp xác nhận worker có thể nhận việc tiếp theo và xác minh thành công; không chứng minh exception cũ là ValueError.

## D. Kiểm thử và replay

### Unit suite

Lệnh từ `/mnt/files/Project/ptz/ptz`: `PYTHONPATH=.:test python -m unittest discover -s test`. Lần kiểm tra thực tế dùng Python `/home/admin1/miniconda3/envs/py/bin/python`, chặn socket connect/create_connection và chạy discovery qua unittest.

**608 mục, 605 đạt, 3 lỗi môi trường**: test_gstreamer_cpu, test_sharpness và test_rtsp_unavailable_schedules_backoff_without_raising đều thiếu `gi`. Hai lỗi import module có thể che test bên trong. Không cài/đổi môi trường. Xem [tests.log](tests.log).

19 test mới bao phủ: không overlap; 4% target/20% competitor; hand-contact kéo dài vượt grace; moderate với verification mới; severe; competitor nhỏ bị chứa; ID switch phải 3 kết quả; confirmation không reset vì minor; confirmation không nối qua ID hoặc gap dài; margin ambiguous; contradictory verification; stale future; disappearance; diagnostics; tỷ lệ có hướng; identity hết hạn; motion jump; worker exception/recovery. PTZ/session/face tests sẵn có cũng chạy trong suite. Không phát lệnh motor thật.

### So sánh cùng input synthetic với source backup

[compare.py](compare.py) chạy riêng process trên source backup và source mới, cùng mocks/bbox/timestamp. Kết quả [baseline](baseline_synthetic.json), [modified](modified_synthetic.json):

| Case | Baseline | Bản sửa |
|---|---|---|
| Không overlap | 4/4 quan sát được chọn | 4/4 |
| Acquisition với giao nhỏ | Không khóa | Đủ 3/3 và giữ khóa |
| Target đã khóa, giao nhỏ kéo dài | 2/4 quan sát, sau đó HOLD | 4/4, không HOLD |
| Severe overlap | 0/4, HOLD | 0/4, HOLD |
| Đổi ID với score mạnh và giao nhỏ | Bị gate chặn | Hai lần đầu chưa chọn; lần thứ ba mới đổi ID |

Bộ dữ liệu synthetic gán nhãn cố định và score giả; không dùng để tuyên bố ID switch rate ngoài đời bằng 0.

### Recorded gate-only replay

Trích **43 mẫu** TARGET_OVERLAP_EVIDENCE từ đoạn trace còn giữ, dùng bbox/ID/time và evidence trước đó nếu có. Đánh giá cùng snapshot trên source backup và source mới bằng `tools/replay_p0_overlap.py`:

- Gate HOLD: **39 → 31**; 8 mẫu được bỏ chặn ở riêng gate hình học.
- Đây là **isolated gate replay**, có giả định selector đang locked/VERIFIED và không mô phỏng toàn bộ history, negative verification, timer, GPU, state/session. Không đồng nghĩa giảm 8 lần HOLD thực, hoặc 8 mẫu đó đều là người đúng.
- Không có ground truth để tính false HOLD, ID switch risk, reacquisition time hoặc occupancy sau sửa. Detection FPS/ReID latency mới cũng chưa đo trên camera; số cũ 8,89 FPS và ReID mean 133,98 ms không được coi là kết quả bản sửa.
- Không có snapshot raw frame của phiên mới. Các MOV hiện có metadata creation tương ứng 08:34, 09:49, 10:11 ngày 16/11 và ngày 13/11; chưa có liên kết đáng tin cậy tới phiên 11:53. Không gán chúng thành replay phiên 11:53 hoặc bịa score/ground truth.

Dataset/results: [recorded_cases.json](recorded_cases.json), [coverage](recorded_coverage.json), [baseline](baseline_recorded.json), [modified](modified_recorded.json), [extractor](extract_recorded.py). Hash nguồn và model consistency ghi trong integrity; không sửa logs.

## E. Chạy và thử camera có kiểm soát

Chạy bản sửa:

```bash
cd /mnt/files/Project/ptz
conda activate py
python ptz/main.py
```

Bật diagnostics; có thể bật snapshot để lần sau có ảnh đối chiếu:

```bash
cd /mnt/files/Project/ptz
conda activate py
PTZ_OVERLAP_DIAGNOSTICS=1 PTZ_TRACK_SNAPSHOTS=1 python ptz/main.py
```

Giữ cùng ảnh `aded02f5-dc45-41cc-a9e8-61dabd31b3a9.jpeg`, người, ánh sáng, camera start pose và toggles. Đóng ứng dụng trước khi chuyển bản. Quay các đoạn: đứng tách nhau; tiếp xúc cạnh bbox nhỏ; đi gần vừa phải; che nặng/cắt ngang; ra khỏi khung rồi trở lại. Ghi video đồng bộ và đánh dấu người đúng. So sánh với baseline backup theo số HOLD không cần thiết, thời gian recovery và ID switch được người xem xác nhận, cùng FPS/ReID latency. Không hạ ngưỡng để lấy số đẹp; dừng thử nếu bám nhầm người.

Kết luận hiện tại: cơ chế mất khóa do giao nhỏ đã có test tái hiện và sửa tối thiểu; hiệu quả và an toàn danh tính trên camera thực tế vẫn cần kiểm chứng có ground truth.
