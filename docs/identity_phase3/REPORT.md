# Phase 3 — Local Visual Tracking và PTZ Motion Compensation

**Quyết định: NO-GO tích hợp production.** Đã triển khai PoC KLT offline, replay A/B, công cụ review bbox, metrics có kiểm tra nguồn nhãn, recorder độc lập và diagnostic GMC. Đã chạy trên video thực hiện có. Chưa chứng minh định vị tốt hơn baseline hoặc phù hợp PTZ; không nới ngưỡng để kéo dài bbox. Baseline, v4, YOLO, ByteTrack, OSNet, scheduler, gallery và controller giữ nguyên.

## 1. Audit source và dữ liệu

Đối chiếu Phase 2B, Phase 2 follow-up, v4, `ai/target_identity.py`, `ai/hybrid_reid.py`, `ai/overlap_policy.py` và các đường capture/tracking/control hiện có:

| Thành phần | Kết quả audit |
|---|---|
| `camera/opencv_player.py` | Đọc FFmpeg/TCP, callback ảnh đã decode; không cung cấp exposure timestamp hoặc RTP sequence. |
| `camera/mpv_player.py` | Decode liên tục nhưng lấy latest frame theo cadence cấu hình; không suy FPS file thành FPS ảnh mới. |
| `runtime/frame_manager.py` | Mailbox một slot, frame sequence của ứng dụng, receive monotonic. Capture timestamp mặc định `None`. |
| `runtime/inference_worker.py` | Có drop/refresh frame trước inference; ByteTrack nhận frame sequence và receive time. AI capture không phải toàn bộ stream. |
| `runtime/byte_tracking.py` | ID dùng association motion, không tự coi ID là bằng chứng identity. |
| `runtime/face_tracking.py` | Đã có FB-LK/affine cho face ROI, không phải body tracker đã được kiểm chứng. |
| `tools/evaluate_local_tracking.py` | Probe cũ chọn bbox bằng geometry/IoU. Không đủ làm trusted correction hay GT identity. |
| `runtime/camera_motion.py` | Có bộ FB-flow + affine RANSAC, masks người/OSD, residual, inlier, spatial coverage và sanity checks. Tái sử dụng riêng, không sửa. |
| `camera/ptz_controller.py` | Có zoom status/encoder fields từ `ptz.getStatus`, nhưng chưa có timeline pose đồng bộ với video được chọn. ACK không dùng làm movement truth. |

Danh mục: [source](source_inventory.txt), [timing](timing_audit.txt), [video inventory](video_inventory.json), [tìm cả file bị Git ignore](all_video_paths.txt).

- **5 file `.mov` ngoài repo là video quay màn hình**, đã xem ảnh mẫu. Có GUI, viền màn hình, ánh phản chiếu, rung và góc quay của thiết bị ghi. Ví dụ `16_11(2).mov`: 1920×1080, 6.263 frame, khoảng 29,974 FPS. Không phải RTSP raw; một nội dung GUI có thể được quay lặp nhiều frame. Không thể tách chuyển động camera PTZ khỏi chuyển động thiết bị quay bằng FPS file.
- Các PNG Phase 2/2B là frame AI lấy mẫu. Hai capture chính có 1.120 và 1.081 ảnh; không dùng chúng như chuỗi 30 FPS.
- Các `preview.mp4` từ PNG vẫn là preview lấy mẫu, không phải nguồn liên tục.
- Có **21 `original.mp4` từ benchmark cũ**, dài 8–100 frame. Đây là bản encode lại các frame replay camera, không phải dữ liệu giả. `tools/modular_benchmark.py` đọc tuần tự nguồn offline rồi ghi lại. Nguồn gốc ghi trong manifest là `test/recordings/camera_20261001_103938.mp4`, SHA `dee5c85b…`, hiện không còn ở đường dẫn được ghi hoặc trong repo.
- Sử dụng ngay đoạn còn lại dài nhất: `logs/modular_benchmark/20261021T120841Z_4dacd817/stage_0_replay_1/original.mp4`, 100 frame, 25 FPS, 2448×2048. Người ở bên phải bị vật thể che, chỉ thấy một phần. Video này không có identity reference/GT liên tục cho target của phiên mới.
- Không có mapping được xác minh giữa các video này và frame IDs/detections của capture 19:00/19:13. Không ghép theo OSD hoặc thời gian gần nhau. Replay đọc PTS bằng ffprobe; đây là media PTS của file, không phải sensor capture time.

## 2. PoC đã triển khai

- [Local tracker](../../tools/phase3_local.py): `initialize`, `update`, `correct`, `invalidate`, `get_tracking_state`, `reset`. Mặc định `Options.enabled=False`.
- [Replay](../../tools/phase3_replay.py): chỉ nhận file local; A constant velocity, B KLT, C chỉ khi có file trusted detector corrections đồng bộ đúng source/frame/time. Không tự gọi camera hoặc motor.
- [Review UI](../../tools/phase3_review.html): vẽ bbox trực tiếp, visibility, identity, loại chuyển động camera và event; export/import JSON. Không tự điền hoặc nội suy bbox. Có 300 frame monitor và 100 frame excerpt để review.
- [Metrics](../../tools/phase3_metrics.py): kiểm SHA/source, frame/PTS, duplicate, reviewer, bbox/visibility. UNKNOWN loại khỏi mẫu; không coi là distractor. Center error, IoU, recall với error ≤10% đường chéo GT, missing prediction là failure; tách static/moving/unknown. Drift định nghĩa IoU <0,2; chỉ gộp episode trên frame đã duyệt liên tiếp. Takeover cần bbox distractor bổ sung trong JSON; UI hiện vẽ target bbox, chưa có workflow bbox nhiều người. Recovery/direction duration chưa tính từ nhãn sparse.
- [Recorder](../../tools/phase3_record.py): OFF nếu thiếu `--record`, FFV1, một writer thread/queue tối đa 16, giới hạn thời lượng, receive timestamps và source sequence, frame index trong file, resolution, gaps, queue drops, SHA và decode-back count. Không import production config, YOLO, OSNet hay PTZ.
- [GMC diagnostic](../../tools/phase3_gmc_diagnostic.py): OFF nếu thiếu `--enable`, tái sử dụng estimator hiện có với source-bound conservative foreground masks. Chỉ đo nền, không biến transform thành target identity hoặc lệnh điều khiển.

KLT dùng feature trong phần trong bbox, kiểm forward/backward, RANSAC affine partial, survival so với seed, phân bố feature, scale/rotation/jump, biên ảnh, resolution, session, timestamp và gap. Không replenish tự động vào background. Ngưỡng thử nghiệm: ít nhất 10 points, survival 0,5, FB 1,2 px, residual RANSAC 2 px, scale mỗi frame 0,92–1,08, rotation ≤8°, gap ≤150 ms, tuổi anchor ≤800 ms. Đây là guard ban đầu, **chưa phải ngưỡng tối ưu từ GT**.

Correction yêu cầu provenance riêng và đúng frame/time/session/source; disagreement hoặc risk làm invalidation, không snap bbox gần nhất. Caller chịu trách nhiệm xác minh trusted detection; chuỗi provenance không phải bằng chứng identity tự thân. KLT không xác nhận lại anchor bằng output của chính nó. Khi mất tracking, cần initialize bằng anchor mới có nguồn hợp lệ.

Feature trong bbox có thể thuộc nền, vật che hoặc người khác. FB error thấp không chứng minh ownership. `risk=True` hỗ trợ dừng khi bên ngoài biết crossing/occlusion; **PoC chưa có bộ phát hiện semantic crossing độc lập**. Tests với risk flag không phải bằng chứng tự phát hiện takeover ngoài đời.

## 3. Kết quả kỹ thuật trên video thực

Hai phương án mỗi lần chạy dùng cùng video, resize width 960, cùng anchor thủ công và cùng budget 800 ms. Anchor do coding agent chọn ROI nhìn thấy, chỉ phục vụ thí nghiệm vị trí, không phải GT identity. Không re-anchor định kỳ từ output. Vì mỗi clip chỉ có một anchor, A khởi đầu velocity = 0; không được diễn giải đây là benchmark đầy đủ Kalman/ByteTrack production.

| Phép đo | Monitor, frame 450–749 | Camera excerpt, frame 0–99 |
|---|---:|---:|
| Frame / thời gian PTS đầu-cuối | 300 / 9,975 s | 100 / 3,960 s |
| A: frame có bbox, gồm anchor | 24 | 21 |
| B: frame có bbox, gồm anchor | 5 | 12 |
| B: cập nhật KLT thành công sau seed | 4 | 11 |
| B: frame invalid đầu tiên | 455 | 12 |
| Nguyên nhân | FEATURE_SURVIVAL | FEATURE_SURVIVAL |
| Feature còn lại lúc dừng | 19/45 = 42,2% | 13/53 = 24,5% |
| B: P95 bước có thực hiện tracking/seed, gồm bước thất bại | 15,27 ms | 12,43 ms |
| Throughput toàn replay, có decode/resize/JPEG/overlay | 97,30 FPS | 51,63 FPS |
| CPU process, 100% = một core | 363,3% | 219,2% |
| Peak RSS | 222,4 MiB | 300,9 MiB |

Nguồn: [monitor summary](monitor_probe_final/summary.json), [camera excerpt summary](raw_excerpt_final/summary.json). Các run không mang hậu tố `final` là lần thử kỹ thuật trước; bảng này dùng các run `final`.

**Không dùng throughput trên để khẳng định KLT chạy liên tục 97 FPS**: phần lớn frame tracker đã invalid, gọi update rất rẻ. P50/P95 toàn frame cũng bị chi phối bởi no-op; phải xem active-attempt latency. OpenCV LK đặt một thread; FFmpeg codec vẫn có thể dùng nhiều thread, giải thích process CPU >100%. GPU không dùng trong PoC, không đo tải GPU của tiến trình khác. Chi phí ffprobe/hash chuẩn bị nguồn nằm ngoài throughput replay.

Ảnh phân tích: [monitor](monitor_probe_final_contact.jpg), [excerpt](raw_excerpt_final_contact.jpg). Video: [monitor overlay](monitor_probe_final/overlay.mp4), [excerpt overlay](raw_excerpt_final/overlay.mp4).

- Monitor: feature giảm mạnh ngay khi dáng/vị trí người đổi giữa các nội dung GUI; một số frame trước đó có flow gần zero. Đây là nguồn bị quay lại màn hình, có overlays/ánh phản chiếu. Chỉ xác nhận nguyên nhân guard là survival; không quy tất cả lỗi cho motion blur hoặc codec.
- Excerpt: ROI chứa phần người bị che cạnh bàn/tủ, biến dạng và mất feature khi di chuyển. Ngay trước invalidation bbox đã đổi scale; không có GT để phân biệt chính xác scale thật và drift. Thêm feature từ nền có thể làm bbox sống lâu hơn nhưng không chứng minh bám đúng người.
- Không có nhãn độc lập nên center error, IoU GT, drift thật, takeover, recovery và sai hướng được để **chưa đo**, không ghi 0.
- C chưa chạy: không có trusted YOLO corrections đồng bộ nguồn. Không gọi bbox từ KLT/IoU là nhãn YOLO tin cậy.

## 4. GMC: diagnostic riêng sau benchmark KLT

Đã chạy estimator hiện có trên 99 cặp frame excerpt, che bảo thủ toàn vùng foreground bên phải và OSD: [masks](raw_foreground_masks.json), [kết quả](gmc_diagnostic/summary.json).

- 99/99 cặp estimate valid, đều `LOW_IMAGE_MOTION`.
- Median residual nền khoảng 0,0031 px; median inlier ratio 1,0; latency P95 15,71 ms.
- Đây là cảnh nền gần tĩnh, chưa có pose GT. Không phải kiểm chứng pan/tilt/optical zoom.
- **Chưa chạy so sánh target A/B/C-GMC/D-KLT+GMC**: không có trajectory GT, foreground annotation phù hợp theo chuỗi hoặc dữ liệu PTZ nguồn đầy đủ. Không double-apply affine camera vào bbox KLT vốn đã chứa image motion. Không thêm homography khi chưa có bằng chứng cần và chưa đánh giá parallax.

## 5. Dry-run và giới hạn metric

Mỗi output có frame ID, `capture_timestamp=null` khi không biết, media/observation timestamp, bbox/source, localization confidence, identity/tracking status, observation age và control-estimation flag. `identity_status=UNVERIFIED_LOCALIZATION_ONLY`, `control_authorized=false`, `is_valid_for_control_estimation=false` ở mọi output hiện tại. Không gallery, không RPC.

B cập nhật ảnh mới nên compute-age proxy trên frame còn valid có P95 khoảng 15,92 ms / 13,07 ms; A có age của anchor P95 728 / 760 ms. **Không so sánh hai số này như latency camera thật**: B là chi phí xử lý offline, A là tuổi đo hình ảnh cuối; không có decode/queue/exposure age trực tiếp. `observation_age_basis` ghi rõ proxy. Chưa chứng minh lợi ích freshness end-to-end.

B chỉ có bbox ở 5/300 và 12/100 frame; invalid 295/300 và 88/100. Tần suất gồm anchor trên toàn window: 0,50 / 3,03 output/s, không đạt mục tiêu duy trì localization liên tục. Không có GT nên không gọi đây là tần suất cập nhật “đúng target”, không tính wrong-direction time hoặc nguy cơ takeover thành xác suất. Chi phí chạy cùng production chưa đo; không thể lấy chi phí offline để cam kết main không lag.

## 6. Tests, integrity và rollback

- Regression bản cuối có guard offline: **392 tests pass**, gồm 359 tests trước đó và 33 tests Phase 3: [log](regression_final.log). Lần chạy trước: 384 tests pass [log](regression.log).
- Sau đó bổ sung validator metrics và chạy bản cuối: **33 tests mới pass** (25 tracker + 8 metrics): [log](unit_tests_final.log).
- Bao phủ seed/no seed, mặc định OFF, timestamp gap/stale/out-of-order, jump, feature loss/full và partial occlusion trên fixture, crossing risk, border, translation pan/tilt, zoom, session/reset, correction aligned/disagreement, không identity promotion/gallery/motor import.
- Synthetic fixtures chỉ kiểm contract, không dùng làm accuracy thực tế. Chưa đủ các kịch bản thực về speed change, turnaround, giống trang phục, crossing, full occlusion rồi recovery và PTZ.
- [OFF replay](disabled_smoke/summary.json): A/B 0 bbox khi không bật flag. [Timestamp-sidecar smoke](timestamp_smoke/summary.json): replay dùng receive timestamps và source sequence từ recorder thay vì nominal AVI FPS.
- Recorder stress input file chạy không pace: 300 received, 29 written, 271 queue drops; báo đúng số drop, decode-back 29/29. Đây là test overload chủ động, không phải camera bị mất 271 frame.
- Recorder pace file 30 FPS: **91 received = 91 written = 91 decoded, queue drop 0**, receive FPS khoảng 30,39, P95 gap 33,38 ms. [Manifest](recorder_paced_smoke/manifest.json). Đây vẫn chỉ là smoke file, chưa đo RTSP live.
- Review UI JS đã smoke với mock DOM: draw bbox, từ chối thiếu reviewer, mark reviewed. Chưa có browser thực để kiểm render: [log](ui_smoke.txt).
- [Integrity](source_integrity.json): 289 file Python có trước giữ nguyên hash; chỉ thêm tools/test/docs của Phase 3. Mọi thay đổi dirty tree có trước vẫn được giữ. Không mở camera thật, không gửi motor commands.

Rollback: không có integration/flag production cần hoàn tác. Dừng các lệnh `tools.phase3_*`; main vẫn chạy như trước. Nếu muốn bỏ PoC, so SHA trong `source_integrity.json`, chỉ chuyển các file trong `new_files` sang thư mục lưu trữ và giữ docs/data. Không `git reset`, không `git clean`, không chép đè source bằng bản backup cũ.

## 7. Việc tiếp theo và vận hành

**Cần bạn ghi dữ liệu RTSP nguồn**, vì video quay màn hình và excerpt 4 giây chưa đủ quyết định PTZ. Không cần bật thêm tính năng trong main. Recorder riêng không gọi AI/PTZ; bạn thao tác camera bằng cách đang dùng nếu cần, chú ý việc ghi thêm stream có thể dùng thêm băng thông/CPU nên cần kiểm tra thực tế.

Từ thư mục repo `/mnt/files/Project/ptz/ptz`, trong môi trường `py`:

```bash
read -rsp 'RTSP URL: ' PHASE3_RTSP_URI; echo
export PHASE3_RTSP_URI
PYTHONPATH=. python -m tools.phase3_record --record --seconds 120 --out logs/phase3_static_01
unset PHASE3_RTSP_URI
```

URL nằm trong biến môi trường, không ghi vào manifest hay đối số command. Dùng tên output mới mỗi lần; công cụ không ghi đè thư mục cũ. FFmpeg có thể ghi lỗi decoder riêng; timestamp hiện tại là host-after-decode, upstream/network/decoder drops chưa đo được và giữ `null`, không mặc định là 0.

Ghi riêng các clip (ví dụ `phase3_static_01`, `phase3_crossing_01`, `phase3_ptz_01`):

1. Camera tĩnh: cùng target đi ngang, đổi tốc độ, quay lưng/đổi hướng, sát biên.
2. Camera tĩnh: hai người tiến gần/giao nhau, che một phần rồi che hoàn toàn, tái xuất hiện, thêm người mặc gần giống.
3. Camera pan, tilt và optical zoom từng đoạn riêng; giữ đủ background, thêm cảnh target di chuyển. Ghi chú đoạn thao tác camera; không xem ghi chú/ACK là pose encoder truth.

Kiểm tra `manifest.json`: `integrity_ok=true`, `queue_dropped_frames=0`, `read_failures_or_eof=0` cho clip đủ thời lượng, measured FPS gần mức stream mong muốn; xem receive gaps. Nếu queue rơi frame, điều tra encode/disk/độ phân giải trước khi dùng benchmark, không coi nominal FPS là đủ. FFV1 có thể tốn dung lượng đáng kể.

Tôi sẽ kiểm tra video/sidecar mới, tạo pack gán nhãn theo đúng frame, giữ tập holdout trước tuning và chạy A/B/C khi có correction đáng tin. Bạn duyệt bbox/visibility/identity trên các đoạn chọn; không phải tự gán lại mọi frame ngay bây giờ. Hai pack hiện tại mở trực tiếp:

- [Review monitor](monitor_probe_final/review.html)
- [Review excerpt](raw_excerpt_final/review.html)

Đánh giá file export khi có nhãn độc lập:

```bash
PYTHONPATH=. python -m tools.phase3_metrics --run docs/identity_phase3/monitor_probe_final --labels /duong/dan/manual_bbox.json --out docs/identity_phase3/manual_metrics.json
```

Sau khi có benchmark KLT có GT, mới thử GMC-corrected prediction và KLT với camera prior như các biến thể riêng trên cùng frame/nhãn. Chỉ cân nhắc NvDCF nếu KLT tiếp tục có giới hạn rõ trên dữ liệu nguồn; chưa cài DeepStream hay thay ByteTrack. Điều kiện GO vẫn là giảm lỗi định vị và không tăng takeover, giữ uncertainty, đo freshness/overhead thật, có kết quả riêng static/PTZ. Hiện tại chưa đạt các điều kiện đó.
