# Comprehensive AI PTZ audit — 16/11/2026

## Quyết định cuối cùng: ROLLBACK TO VERIFIED BASELINE

**Đã rollback thực tế**, không chỉ đặt lại biến môi trường. `/mnt/files/Project/ptz/ptz` hiện chứa nguyên mã/config/model của snapshot **baseline B trước asynchronous**, giữ cải tiến minor-overlap đã có. Toàn bộ 198 file Python của cây runtime khớp baseline. Không đưa tối ưu thử nghiệm vào bản mặc định.

Lý do: thử gộp crop gốc và crop chuẩn hóa giảm thời gian score trong microbenchmark, nhưng không chứng minh cải thiện ≥10% ở mục tiêu chính (tracking đúng danh tính/reacquisition hoặc phản hồi PTZ/recentering). Không có ground truth danh tính và không đo motor vật lý. Kết quả ứng viên: **NOT PROVEN**, không phải đã chứng minh unsafe.

## A. Backup, baseline và phiên bản

### Backup trước mọi sửa mã

`/mnt/files/Project/ptz_async_before_comprehensive_audit_20261116_170604/project`

- Copy toàn bộ outer workspace `/mnt/files/Project/ptz`: source, `.git`, cấu hình, uncommitted/untracked/ignored files, models, ảnh target, logs, video và dependencies trong dự án.
- 8.891 manifest entries, không loại trừ; SHA-256/mode/symlink được kiểm chứng trước sửa. Thư mục bao ngoài quyền 0700, hạn chế truy cập secrets cục bộ. Không đưa nội dung credentials vào báo cáo.
- `manifest.json`, `git_status.txt`, `git_metadata.txt`, `RESTORE.md` ở thư mục cha. Environment hiện hành ghi thêm trong `dependencies.txt`, `hardware.json` của audit; không phải snapshot toàn bộ Conda installation.
- Disk trước copy: ~33 GB trống. Không dùng destructive Git reset/cleanup.

### Phân biệt hai baseline lịch sử

| Tham chiếu | Snapshot | Bằng chứng và giới hạn |
|---|---|---|
| Baseline A, ứng viên 08:38 | `/mnt/files/Project/AI_PTZ_BASELINE_0838_BACKUP_20261116_114845` | HEAD `ede31989fe6a39b04d7cd4d31675ebd811a28397`; báo cáo phục hồi cũ xác nhận đây là ứng viên người dùng chọn, chưa có source fingerprint chứng minh tuyệt đối đúng code lúc 08:38. Không lấy làm rollback lần này. |
| **Baseline B, pre-async** | `/mnt/files/Project/ptz_before_async_control_20261116_162242/project/ptz` | Snapshot đầy đủ đã tạo ngay trước tác vụ async, gồm overlap fixes. Kiểm lại toàn manifest 8.864 entries trước audit; kiểm lại hash sau benchmark không có file đổi. Có log 13:49 trong snapshot và lịch sử thao tác nối snapshot với thay đổi async. Không suy ra commit timestamp chứng minh chính xác mọi byte lúc 13:49. |
| Async trước audit | `/mnt/files/Project/ptz_performance_experiment_20261116/installed_async_before_rollback` | Di chuyển nguyên thư mục đang active trước rollback, giữ mã, Git state, logs và assets; thêm full backup 17:06 ở trên. |
| Thử nghiệm C | `/mnt/files/Project/ptz_performance_experiment_20261116/ptz` | Branch `experiment/ptz-performance-recovery`, commit `07f6593`; không phải bản chạy mặc định. |

Baseline B có source `ai/hybrid_reid.py`, `ai/overlap_policy.py`, `config.py` khác original HEAD do overlap fixes có sẵn. Vì vậy **HEAD cũ một mình không định danh đầy đủ baseline**: dùng snapshot + hash. Không trộn baseline A vào B.

Config SHA-256 B/active:
`b87663f165c9cf3a891f557ad10baac8b2be90bc798154d17dbf7291555e4bde`

Camera JSON SHA-256:
`227a5813f0fe6411823dd44869da7d939645b4dfafd7b8c22e0bf49d3d34ce1f`

`baseline_fingerprints.json` có source/config/model hashes từng bản. Dependency versions lịch sử không được fingerprint tại thời điểm các phiên cũ; không gán environment hiện tại cho quá khứ. Các benchmark mới dùng cùng environment hiện hành: GTX 1650 4 GB, driver 595.84; chi tiết CPU/GPU trong `hardware.json`.

## B. Root-cause investigation

### Pipeline và blocking thực tế

`RTSP/capture → FrameManager latest slot → detection/tracking worker → target selection → pan worker → serialized HANET worker`.

Baseline B đã có worker pan/tilt và transport độc lập. Async bổ sung observation mailbox/guard, không chuyển RPC từ inference ra thread lần đầu. Camera queue wait phiên mới mean 0,83 ms/P95 2,50 ms; publish→consume trên 35 intent mean 2,10 ms. Hai chỗ này không giải thích phần lớn độ chậm.

| Vấn đề | Bằng chứng/source | Kết luận |
|---|---|---|
| YOLO ~41,58 ms nhưng detection 3,90 FPS | `runtime/inference_worker.py` gọi trực tiếp `reid_target.select_person`; `ai/hybrid_reid.py::_select_person` gọi `_scores` khi chưa locked hoặc đang hold/recovery; chỉ periodic verification dùng thread riêng | **Xác nhận synchronous selection/recovery nằm trong detection loop.** YOLO duration không đại diện chu kỳ toàn pipeline. Không phải YOLO chỉ cần nhanh thêm. |
| ReID ~260–270 ms | `ai/reid_target.py::_score_batch`: crop → OSNet raw → quality/normalization → OSNet variants → fusion. `person_search/reid.py` chia batch 4; tensor checks và `.cpu().tolist()` tạo điểm đồng bộ GPU/CPU | **Xác nhận nhiều forward và đồng bộ**, không phải mỗi mẫu score = một forward. |
| Chậm chủ yếu lúc tìm lại | Trace retained: 268 reacquisition calls, 2.188 crop gốc + 2.014 variants, mean 276,59 ms/call | Trung bình **8,16 crop gốc + 7,51 variants/lượt**. Với batch 4 thường phải nhiều forward. Chưa có GPU execution profiler đủ để chia chính xác kernel/copy/scheduling. |
| Queue AI | ReID queue wait mean 0,46 ms; detection queue wait 2,43 ms; face queue p95 ~318,62 ms | Không thấy FIFO backlog lớn cho ReID; vẫn có contention cho face. Scheduler accelerator dùng condition, ưu tiên detection nhưng không preempt GPU work đã được cấp. Nested scheduling làm số sample resource/queue không bằng số frame. |
| Mất người rồi chờ 5–10 s | Phải đạt threshold + margin + geometry + 3 lần xác nhận liên tục; hold có grace 3 s trước full loss | Recovery gap gồm thời gian chờ bằng chứng hợp lệ, không chỉ một lần inference 270 ms. Không được bỏ guard hoặc hạ threshold để tạo số đẹp. |
| PTZ ít lệnh | 572 TARGET_UNCERTAIN, 42 DEADZONE, 41 COOLDOWN, 54 MOTOR_BUSY; 4 full losses; manual override/toggle | Số lệnh thấp phù hợp target thường không được cấp quyền. Các counter là evaluations, không phải thời lượng hay số loss. |
| Consume→dispatch 33,03 ms | Worker chờ 30 ms, settle 150 ms, active pulse 120 ms, arbiter; notification có thể đến trước worker bước vào wait | Có khả năng scheduling đóng góp; **không thể tách chính xác 33 ms thành từng nguyên nhân từ log hiện có**. Không tự quy hết cho polling. |
| Rotation chậm | Auto speed `min(3,max(1,1+int(abs(error)*6)))`, pulse 120 ms rồi settle 150 ms | Speed 1 là lựa chọn chủ ý cho sai lệch nhỏ, không mặc nhiên là mapping bug. Khả năng pulse ngắn chưa đạt tốc độ ổn định chỉ là giả thuyết khi chưa đo gia tốc. |
| Async không giảm tổng latency | Before đã async RPC; mailbox handoff vốn nhỏ; cùng observation replay A/B/C cho latency gần nhau | Chưa thấy lợi ích end-to-end từ mailbox mới. Các phiên live khác cảnh không chứng minh async là nguyên nhân regression. |

Các model được load khi khởi tạo/đổi target, không thấy đường load lại OSNet mỗi frame. Có cấp phát tensor/crop theo lượt; chưa có memory allocation profile đủ để định lượng riêng. Latest FrameManager bỏ frame cũ có chủ đích, không phải RTSP mất số frame tương ứng. Logging emit sampled mean ~0,071 ms, P95 ~0,249 ms; chưa đo tổng overhead bật/tắt logging nên không kết luận bằng không.

### Theo trạng thái, chỉ phần trace còn giữ

Cửa sổ **16:37:56.788–16:39:42.091**. Ghép state transitions trong app log với YOLO_END trong trace; rate theo tổng thời gian trạng thái trong cửa sổ:

| State | Thời lượng | Detection frames | FPS |
|---|---:|---:|---:|
| SEARCHING | 94,691 s | 290 | 3,06 |
| PERSON_ZOOM | 4,492 s | 35 | 7,79 |
| OCCLUDED_HOLD | 6,006 s | 38 | 6,33 |
| TARGET_LOCKED | 0,113 s | 1 | 8,85 — mẫu quá ngắn |

Đây không phải FPS toàn phiên từng trạng thái. VERIFIED là trạng thái identity khác với stage; không có đủ retained trace để thống kê đầy đủ tổ hợp stage × identity. Trace đầu phiên đã bị rotation ghi đè; không nội suy phần mất. `latest_run_audit.json` chứa cách trích xuất, workload và evidence quanh losses.

## C. Điều tra bốn lần mất target

| Full loss | Trước đó | Reacquire | Điều xác nhận / chưa xác nhận |
|---|---|---|---|
| 16:37:29.005, track 51 | PERSON_ZOOM→OCCLUDED_HOLD 16:37:25.535 | 16:37:38.591, track 70; 9,587 s | App log cho thấy hold rồi timeout. Trace chi tiết đã rotate: không thể xác định chắc detector miss/overlap/verification hay chuyển động camera. |
| 16:37:42.965, track 70 | Hold 16:37:39.912 | 16:37:52.976, track 72; 10,011 s | Tương tự: xác nhận hold→loss, thiếu per-frame reason. |
| 16:38:03.345, track 72 | Frame **2533**, hold **PERSON_OVERLAP** lúc 16:38:00.308; stored similarity 0,7863, margin 0,3334 | 16:38:08.266, track 82; 4,921 s | Frame **2624** SEARCHING, last_seen_age 3,198 s. Xác nhận overlap gate rồi timeout; score trong transition là bằng chứng đã lưu, không chắc là score mới đúng frame 2533. |
| 16:38:12.321, track 82 | Frame **2804**, hold **PERSON_OVERLAP** lúc 16:38:09.351; stored similarity 0,7192, margin 0,2717 | Không tìm lại trước shutdown | Frame **2894** SEARCHING, last_seen_age 3,106 s. Không có ground truth/video đồng bộ để khẳng định người rời FOV hoặc ID switch. |

Ví dụ gần episode 3, frame **2528**, target 72 bbox `[283,137,363,469]`, competitor 74 `[233,155,308,256]`: intersection 2.525 px², target cover **9,51%**, competitor cover **33,33%**, IoU **7,99%**. Vượt minor-contact thresholds 5% / 25% / 5%; không phải trường hợp “chạm rất nhỏ nhưng bị loại” theo policy hiện hành. Đây là frame trước hold, không thay thế bằng chứng đúng frame 2533.

`_candidate_overlap` được gọi cho nhiều ứng viên; overlap fields trong một `TARGET_OVERLAP_EVIDENCE` có thể tương đối với candidate đang được xét, không nhất thiết target trong correlation context. Không quy mọi overlap của người khác thành overlap target. Candidate similarity/frame/confirmation không được log đầy đủ ở mọi frame; dữ liệu thiếu giữ unknown.

Minor overlap bypass đã tồn tại qua `minor_contact`, sau đó vẫn kiểm tra appearance, margin, continuity. Không tìm được reproducible case đủ bằng chứng để đổi overlap/recovery policy trong tác vụ này. Counter 128/81/36/6 không phải 128/81/36/6 loss episodes. Không sửa ngưỡng, identity guard hoặc confirmation count. Cuối phiên top1 thường <0,65; giảm threshold sẽ làm tăng rủi ro chọn người sai.

## D. Tối ưu thử nghiệm được thực hiện

**Chỉ một tối ưu**, trong experiment C:

- `ai/reid_target.py`: `_score_batch_combined` gộp crop gốc và normalized variants vào một lời gọi OSNet; engine vẫn chia batch tối đa 4. Dùng đúng crop transforms, gallery, fusion weights và thresholds cũ. Kiểm tra số score trả về; admission callback vẫn kiểm tra trước GPU.
- `config.py`: cờ `PTZ_REID_BATCH_NORMALIZED=1`, mặc định **0**. Không đổi tracking thresholds/algorithm. Code cũ tiếp tục dùng khi tắt cờ.
- `test/test_reid_combined_batch.py`: 4 tests cho score/fusion, normalization off, stale admission và số call. Hai fixture cũ được cập nhật để mock trả đúng số embedding khi nhiều crop nằm cùng call; không nới assertion về score.
- `tools/audit_pipeline_benchmark.py`, `audit_control_benchmark.py`, `audit_paired_scores.py`, `audit_latest_run.py`: benchmark và evidence extractor.
- Những thay đổi async/overlap có sẵn cũng được commit để experiment tự chứa đầy đủ, **không phải thay đổi thuật toán mới của audit này**. Xem diff với snapshot 17:06 để tách phạm vi.

Không chuyển toàn acquisition/recovery sang background trong lần này: điều đó đòi hỏi quản lý lifecycle/generation và kiểm chứng identity trên ground truth. Không đổi speed, pulse, cooldown, arbiter, continuous control hay wakeup semantics khi chưa tách được delay hợp lệ. Không loại normalized crop/candidate để tăng FPS bằng cách làm mất bằng chứng appearance.

## E. Benchmark có kiểm soát

Tiêu chí được ghi **trước benchmark** trong `acceptance.md`: ≥10% ở primary metric, không >5% regression throughput/P95, không tăng identity errors/safety failures. Synthetic hoặc video không annotation không đủ chứng minh tracking đúng danh tính. Không coi 10% là ý nghĩa thống kê.

Tên A/B/C trong benchmark **khác tên baseline lịch sử**:

- **Benchmark A = baseline B pre-async đã xác minh**.
- Benchmark B = async đang dùng trước audit.
- Benchmark C = async + combined normalization batching.

### E1. Real model/component replay

Cùng 120 frame BGR 704×480: 40 frame đầu cách nhau 10 frame từ mỗi clip `16_11(1).mov`, `16_11(2).mov`, `16_11(3).mov`; cùng target `aded02f5-dc45-41cc-a9e8-61dabd31b3a9.jpeg`; cùng YOLO26n/OSNet weights, ByteTrack và config. Hash decoded frame stream, target và model khớp ở cả **9 runs / 1.080 frame evaluations**. Chạy tuần tự, order ABC/BCA/CAB; warmup trước đo; nvidia-smi samples và process CPU seconds được lưu mỗi run.

**Đây là component replay chạy không giới hạn nhịp**, không phải toàn app với RTSP/decode/GUI/face/AF và không phát lại realtime video PTS. Detector, tracker, selector và mock pan chạy thật; dùng host time tại lúc xử lý frame. Không so FPS này trực tiếp với 3,90 FPS live. Footage/target không có annotation; 113/120 frame chủ yếu SEARCHING, chỉ 7 frame selected mỗi run. Không có nhiều crop như cảnh live cuối phiên.

| Variant | FPS ba lần | Mean FPS | Selected frames/run | OSNet API calls/run |
|---|---|---:|---:|---:|
| A pre-async | 37,38 / 35,10 / 39,29 | **37,26** | 7 | 87 |
| B async | 34,59 / 37,00 / 34,76 | **35,45** | 7 | 87 |
| C combined | 37,06 / 39,53 / 39,85 | **38,81** | 7 | 83 |

C tăng **4,18% so với A**, khoảng **9,48% so với B** trong harness này, chưa đạt 10% và không phải primary tracking/response proof. Selected count bằng nhau không chứng minh chọn đúng người. Mỗi run chỉ một cặp mock START/STOP: quá ít để đánh giá command tails. Loss/fragmentation trong clip ngắn không phản ánh các episode live. Correct-target time, false-target time và ID switches **chưa đo** do thiếu ground truth.

### E2. Paired score test

48 cặp evaluation thực OSNet (16 trường hợp có person × 3 lần), thứ tự reference/combined luân phiên, CUDA synchronize quanh phép đo. `paired_scores.json`:

- Max absolute score difference **1,19e-7**.
- Ranking và ngưỡng acquire decisions giống nhau trong tất cả cặp.
- Reference **33,14 ms**, combined **28,98 ms**: ~**12,55%** cải thiện microbenchmark.

Không chứng minh identity accuracy hoặc toàn pipeline tốt hơn 10%; số float khác có thể vẫn quan trọng với ứng viên sát ngưỡng ngoài mẫu đã đo. Đây là tối ưu đáng giữ để nghiên cứu, chưa đủ activate.

### E3. Matched control replay

100 bbox observations đã ghi, 20 Hz, mock START 20 ms và STOP 5 ms; cùng speed/pulse/deadband/settle. Mỗi variant 3 runs:

| Variant | Frame→RPC mean từng run | START/STOP mỗi run | Shutdown |
|---|---|---|---|
| A | 33,02 / 32,96 / 33,11 ms | 17 / 17 | Thành công cả 3 |
| B | 33,34 / 33,12 / 33,11 ms | 17 / 17 | Thành công cả 3 |
| C | 33,30 / 33,19 / 33,11 ms | 17 / 17 | Thành công cả 3 |

Không có cải thiện ≥10%. Đây là software/mock timing; frame được publish ngay trước submit, không gồm real inference. Không so các số 33 ms với live 117/131 ms như speedup. Raw event/counter/latency từng command trong `control_A/B/C.json`. CPU/GPU utilization mới được đo ở component replay; không có utilization cùng thời điểm phiên live để kết luận tài nguyên là nguyên nhân duy nhất.

### E4. So với live trước đó

13:49 pre-async: frame→RPC mean 117,40 ms; 16:36 async: 130,89 ms. Đó là quan sát khác cảnh, không A/B. ReID selection/reacquisition/total là các stage khác nhau, không đem mean 133,98 ms cũ so trực tiếp 263,96 ms selection rồi gọi tăng gấp đôi cùng metric.

## F. PTZ hardware và safety

PDF cục bộ `HANET PTZ PANORA PF2_HN-PTZF258V.pdf` ghi model **HN-PTZF258V**, pan **0,1–300°/s**, tilt **0,1–100°/s**. Tài liệu không phân biệt rõ cực đại ở preset/continuous mode và không có curve step→°/s. Chưa xác nhận camera đang nối thực sự đúng model/firmware này; không gọi mạng hoặc tự chạy motor để xác minh.

Driver HANET giới hạn step integer **1..8**. GUI manual speed đi qua clamp; auto pan dùng error mapping riêng, cap 3. Speed 1 khi |error|<1/6 (sau deadband) là chủ ý. START/STOP là lifecycle driver đang dùng; thiết bị hỗ trợ điều chỉnh continuous velocity/speed khi đang quay hay không chưa được chứng minh. Không đổi vận tốc lên 300°/s theo thông số quảng bá.

Công cụ độc lập đã có và được tái dùng, không tạo controller song song: `tools/benchmark_ptz_motor.py`. Đã chạy **mock** step 1/pulse 120 ms, kết quả `motor_mock.json`. Có direction, step, pulse, Ctrl-C/STOP finally, START/STOP timestamp, external calibrated-angle samples (displacement/speed/acceleration/deceleration), interactive physical opt-in và yêu cầu evidence step 1 trước step cao hơn.

**Giới hạn:** không có encoder/position feedback, nên travel/cable boundary phải do operator kiểm tra; công cụ không cung cấp hard angular boundary. Duration phần mềm không đảm bảo motor dừng đúng hạn khi network/RPC bị treo. Physical mode không được chạy trong audit. Recapture/recentering, overshoot, startup angular delay và vận tốc motor vẫn **unverified/null**.

### Kết quả kiểm thử

- Candidate C bật cờ: **660 tests, 658 pass, 2 errors thiếu `gi`**. Không có safety regression test failure sau sửa fixture đáp ứng interface batching.
- Four new batch tests pass; real paired score test pass tolerance.
- Baseline sau restore: **622 tests, 620 pass, 2 errors thiếu `gi`** (`test_gstreamer_cpu`, `test_rtsp_unavailable_schedules_backoff_without_raising`). Không tuyên bố toàn suite xanh.
- Existing mocked suites kiểm tra START/STOP timeout, late/lost ACK, WebSocket disconnect/reconnect, fault persistence, STOP priority, stale sessions, ownership, manual/emergency stop, bounded shutdown. No real motor commands.
- `restored_smoke.json`: 198 Python files parse OK và khớp baseline; `main.py` import OK; person/ReID/face/target paths tồn tại. Không mở GUI/camera, do đó desktop RTSP smoke còn chưa được xác minh trong environment này.

## G. Rollback thực tế và safety trước đổi code

Lúc 17:16:40 đã:

1. Kiểm tra không có app Python main/CCTV đang chạy, các `motor_state/*.json` active_codes rỗng.
2. Kiểm chứng lần shutdown gần nhất 16:39:41.949: `unconfirmed_motor=false`, owner null, cleanup failures rỗng. Không phát thêm STOP mạng; không có command active được thấy. Đây là bằng chứng protocol/software, **không phải physical motor sensor**.
3. Copy snapshot B vào staging và kiểm tra 2.954 file regular trước switch; giữ logs/docs mới.
4. Di chuyển nguyên bản async active sang `installed_async_before_rollback`, rồi đưa staging vào `/mnt/files/Project/ptz/ptz`.
5. Tạo branch `baseline/verified-pre-async-20261116`, giữ pre-existing overlap changes đúng snapshot; chạy regression và smoke.

`rollback_receipt.json` ghi thời điểm, paths, config hashes, HEAD và safety evidence. Bản B gốc vẫn nguyên hash sau toàn bộ benchmark. Không xóa code thử nghiệm, logs, models, bản 08:38 hay các backup cũ.

## H. Bản active và lệnh chạy

**Mặc định hiện tại:** baseline B pre-async ByteTrack + OSNet + minor-overlap improvements, controller gốc vốn có worker pan/tilt riêng. Không có mailbox async mới và không có combined batching. Git HEAD `ede31989fe6a39b04d7cd4d31675ebd811a28397` + đúng các dirty overlap/config changes của snapshot B; dùng hash ở mục A để xác định đầy đủ.

```bash
cd /mnt/files/Project/ptz
conda activate py
python ptz/main.py
```

Không tự khởi chạy GUI trong audit. Model/config được resolve từ thư mục active. Muốn xem đúng bản async đã chạy 16:36, **đóng app trước** rồi chạy bản đã giữ:

```bash
cd /mnt/files/Project/ptz_performance_experiment_20261116
PTZ_EXECUTION_MODE=asynchronous /home/admin1/miniconda3/envs/py/bin/python installed_async_before_rollback/main.py
```

Muốn nghiên cứu C (không phải mặc định, có thể điều khiển camera nếu tự mở GUI):

```bash
cd /mnt/files/Project/ptz_performance_experiment_20261116
PTZ_EXECUTION_MODE=asynchronous PTZ_REID_BATCH_NORMALIZED=1 /home/admin1/miniconda3/envs/py/bin/python ptz/main.py
```

Benchmark offline, không camera:

```bash
cd /mnt/files/Project/ptz_performance_experiment_20261116/ptz
PYTHONPATH=.:test PTZ_REID_BATCH_NORMALIZED=1 PTZ_EXECUTION_MODE=asynchronous /home/admin1/miniconda3/envs/py/bin/python tools/run_unit_tests.py
/home/admin1/miniconda3/envs/py/bin/python tools/benchmark_ptz_motor.py --speed 1 --duration 0.12 --output /tmp/ptz_mock_new_trial.json
```

Để phục hồi toàn bộ trạng thái trước audit (sau khi đóng app và xác minh không còn lệnh/motor uncertainty):

```bash
cd /mnt/files/Project
mv ptz "ptz_retained_before_restore_$(date +%Y%m%d_%H%M%S)"
cp -a /mnt/files/Project/ptz_async_before_comprehensive_audit_20261116_170604/project ptz
```

Đây là phục hồi toàn workspace async trước audit, không phải kích hoạt bằng biến môi trường trên code baseline. Có thể chạy bản giữ ở đường dẫn riêng thay vì đổi workspace. Không chạy đồng thời hai GUI/controller.

## Những phần chưa thể kết luận

- Đúng/sai danh tính trên video và các bốn loss episode: cần annotation/reference + video đồng bộ phiên live; hai episode đầu mất raw trace.
- Exact per-reason PTZ waiting duration: hiện chỉ có counters và timestamps từng intent, không đủ timeline ownership/eligibility toàn thời gian.
- Model/firmware thiết bị thực, tốc độ preset/continuous, gia tốc, recenter/overshoot: cần operator kiểm tra và đo motor có giới hạn an toàn.
- Full desktop/GStreamer startup trong Conda thiếu `gi` chưa pass.

Các thiếu hụt này là lý do không giữ experiment làm mặc định, không được lấp bằng số synthetic hoặc giả định tốc độ motor.
