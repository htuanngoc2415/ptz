# Prototype V1 — Occlusion audit và thử nghiệm offline

Ngày: 2026-11-20. Kết luận: **GO cho nghiên cứu offline/live shadow; NO-GO thay policy production hoặc điều khiển motor bằng policy mới.**

Đã bổ sung policy thử nghiệm, diagnostic logs/overlay, replay A/B và đường chạy C bằng model segmentation local. Không thay giao diện, không tạo lại ứng dụng prototype riêng, không sửa production baseline trong công việc này. Snapshot SHA256 kiểm tra 85 file production/config: không thay đổi (`occlusion_integrity.json`). Những thay đổi git có sẵn trước audit được giữ nguyên. Không kết nối camera thật và không gửi lệnh motor.

## 1. Root cause và giới hạn bằng chứng

### Đã chứng minh bằng code và mock

`ai/hybrid_reid.py::_select_person` có gate `all(self._candidate_overlap(...))`: khi các ứng viên trong gate overlap, nó gọi `_hold('PERSON_OVERLAP', ...)`, trả `None` **trước bước chấm OSNet**. Mock dùng target có appearance mạnh và hai detections chứng minh `_scores` không được gọi, `crossing_hold=True`, không trả localization. Đây là lỗi policy có thể tái hiện độc lập với detector.

Overlap hiện tại dùng cả intersection/min(area), không chỉ IoU. `REID_CROSSING_OVERLAP=.15`; có ngoại lệ temporal minor-overlap nhưng không bao phủ overlap lớn. Không được diễn giải ngưỡng này thành bằng chứng target bị che thật.

`runtime/inference_worker.py` bỏ target session khi `crossing_hold` và không submit person cho auto pan/tilt. Controller yêu cầu session hợp lệ. Vì vậy một rejection bởi geometry có thể chặn localization/PTZ dù detector còn bbox. Trạng thái thực tế có hold/session invalidation; không cần có literal transition `LOCKED -> AMBIGUOUS` để gây triệu chứng này.

### Đã quan sát trên dữ liệu lưu

- Replay 1.120 observations tìm được 12 mẫu hold trong khi track ID hiện tại vẫn được quan sát. Không có mẫu nào trong 12 mẫu đồng thời đạt score >= .70 và confidence >= .35. Chưa có ground truth độc lập để gọi chúng là 12 false rejects. Xem `occlusion_root_cases.json`.
- Detector audit chạy lại 12 frame overlap: cả 12 có output end-to-end `[1,300,6]`, 3 decoded person rows đạt confidence .10, 2 đạt .35, và 3 detections sau postprocessing. Historical cache cũng có 3 detections/tracklets.
- Model hiện tại dùng end-to-end output; không có traditional NMS suppression trong 12 mẫu này. Tensor trên là decoded top-k trước postprocessing, **không phải toàn bộ proposals trong network**. Không thể loại trừ model/top-k đã bỏ một người trước tensor đó. Các rows confidence thấp không đồng nghĩa từng row là một người thật.
- Confidence .35 có thể loại một detection mà tracking ở .10 vẫn giữ. Chưa chứng minh detection đó chính là target.
- Adapter ByteTrack trả bbox của source detection kèm ID/source index. Không có nhãn identity để chứng minh track đã nhảy sang người khác. Pipeline hiện tại không có dense local body tracker; kết quả KLT Phase3 cũ không phải root cause được chứng minh cho pipeline này.

### Chưa được chứng minh

Target đứng trước và rõ trong clip thật có bị gate từ chối hay không; OSNet bị crop contamination đến mức nào; detector có merge hai người ở thời điểm lỗi người dùng thấy; ByteTrack identity switch; thứ tự trước/sau của hai người. Cần clip liên tục và nhãn độc lập. Không coi bbox overlap hoặc track ID là nhãn occlusion/identity.

## 2. Policy thử nghiệm B

`tools/occlusion_association.py` không được production import. Policy xếp hạng theo reference/anchor appearance và lưu tối đa 4 hypotheses. Track ID hoặc IoU không tự xác nhận identity. Acquisition/rebinding yêu cầu 3 quan sát rõ liên tiếp; không học gallery.

Khi overlap, policy chỉ giữ `LOCKED` với bbox đang quan sát nếu có full-body appearance phù hợp, ít nhất 2 vùng body tương ứng có appearance phù hợp, đủ support không lẫn người khác, continuity và thông tin camera motion hợp lệ. Trả `PARTIAL_OBSERVED`, uncertainty .35, `actual_occlusion=UNKNOWN`. Giá trị uncertainty là heuristic, chưa hiệu chuẩn xác suất. Geometry đơn thuần trả `PARTIAL_UNVERIFIED`, không tự kết luận identity ambiguous.

Bằng chứng appearance cạnh tranh thật trả `AMBIGUOUS` và giữ hypotheses. Khi detector mất, trả `OCCLUDED_UNOBSERVED`, rồi `LOST_LOCALIZATION` sau .8 giây; `identity_known` vẫn giữ. Không bịa bbox hoặc điều khiển bằng prediction khi không có observation. Crop overlap không cập nhật anchor/gallery. Session/frame cũ hoặc observation age > .25 giây bị loại.

`tools/occlusion_visibility.py` chia bbox thành 6 vùng; B loại pixels nằm trong bbox người khác, C dùng vùng instance mask không trùng mask khác. `support` là diện tích vùng cung cấp bằng chứng, **không phải tỷ lệ cơ thể thật đang thấy**. Không suy ra depth/front-back từ boxes. Khi target nằm hoàn toàn trong bbox người khác, B có thể không còn vùng dùng được dù target ở phía trước. Đây là hạn chế còn tồn tại, cần segmentation/landmarks hoặc dữ liệu khác để kiểm chứng.

OSNet part embeddings là thử nghiệm: model full-body chưa được hiệu chuẩn cho từng vùng. B dùng ngưỡng identity sau lock .70, baseline keep .50; freshness và crop gates cũng khác. Vì vậy A/B dưới đây là so sánh toàn policy, chưa phải ablation riêng overlap. Không hạ threshold để kéo dài bbox.

## 3. Diagnostic và code bổ sung

| File | Vai trò |
|---|---|
| `tools/occlusion_association.py` | Association, hypotheses, identity/localization states |
| `tools/occlusion_visibility.py` | Vùng appearance không lẫn competitor; mask support |
| `tools/occlusion_detector_audit.py` | Decoded output, confidence, postprocess, overlay |
| `tools/occlusion_benchmark.py` | A/B cache replay, real part OSNet, GMC, JSONL/video |
| `tools/occlusion_shadow.py` | Observer local video/live shadow; C tùy chọn |
| `tools/occlusion_synthetic.py` | 9 kịch bản với GT và appearance được lập trình |
| `tools/occlusion_metrics.py` | Recall IoU50 từ nhãn manual độc lập gắn SHA nguồn |
| `test/test_occlusion_association.py` | Policy contracts, root-cause regression, metrics |

Logs tách detections/source association, appearance hypotheses, geometry/support, state/reason, localization và shadow control eligibility. Xem `detector_audit/audit.json`, `occlusion_ab_final/decisions.jsonl`, `occlusion_ab_final/overlay.mp4` và `sample_*.jpg`. Các reason `OVERLAP:*` của A có thể là diagnostic của candidate xét cuối; phải đọc cùng transition và geometry, không coi reason đó là GT.

## 4. Kết quả A/B/C

A = current selector với cached full-body scores, async completion mô phỏng frame kế tiếp; gallery tắt trên config clone. Không tái hiện chính xác lịch xử lý live. Source hiện tại khác historical cache ở `ai/hybrid_reid.py`, được ghi trong summary.

| Replay | Mẫu | A chọn bbox | B chọn bbox | A interruption proxy (s) | B interruption proxy (s) |
|---|---:|---:|---:|---:|---:|
| Development, real OSNet parts | 500 | 317 | 173 | 22.05 | 38.79 |
| Retrospective holdout, không part inference | 1081 | 802 | 406 | 43.01 | 88.22 |

Các số chọn bbox không phải localization recall. Interruption proxy cộng khoảng thời gian giữa sampled observations khi policy không cho control; không phải thời gian target mất thật hoặc motor ngừng thật. Không có independent GT nên real localization recall, identity switch, lost duration và actual PTZ interruption đều `null`.

B giữ `LOCKED` với `TRUSTED_VISIBLE_SUPPORT` tại frames 2105, 2110, 2132 ở development. Điều này chứng minh nhánh overlap có bằng chứng được chạy với real embeddings; chưa chứng minh ba quyết định đó đúng identity. B chọn ít hơn và proxy interruption cao hơn A: **không có bằng chứng tổng thể cải thiện real tracking**.

Holdout được freeze policy trước khi chạy (`occlusion_holdout_freeze.json`) và không tuning sau đó. Dataset này đã từng được nghiên cứu trước đây nên chỉ là retrospective holdout, không phải dữ liệu hoàn toàn mới. Holdout không chạy parts: chỉ kiểm tra conservative fallback, chưa kiểm chứng đầy đủ khả năng visibility của B.

C đã có đường thực thi YOLO instance segmentation + separate ByteTrack + mask-parts association, nhưng **chưa chạy**: không có segmentation weights local. Không tự download. SAM2 không import được và không có weights, chưa benchmark, không tích hợp production. C phải được đo trên cùng video có nhãn; thêm inference segmentation sẽ làm thay đổi latency nên cần phân tích riêng.

Smoke test local MP4 chạy 13 frames, không phát lệnh vật lý; chỉ chứng minh observer chạy được, không chứng minh realtime. Xem `occlusion_video_smoke/summary.json`.

## 5. Tests và lỗi còn lại

125 tests pass: occlusion association/metrics, hybrid tracking, minor overlap, identity continuity, auto pan/tilt, camera motion. Xem `occlusion_regression_final.log`.

Synthetic benchmark dùng scripted embeddings/part evidence và discrete steps; không đo accuracy detector/OSNet/segmentation:

| Kịch bản | Recall A | Recall B |
|---|---:|---:|
| Target trước người khác, overlap lớn | 0 | 1 |
| Target đứng yên, người sau di chuyển | 0 | 1 |
| Target di chuyển phía trước | 0 | 1 |
| Che một phần với trusted support | 0 | 1 |
| Che hoàn toàn | N/A | N/A |
| Giao nhau rồi tách | .45 | .90 |
| Trang phục tương tự, không phân biệt được | 0 | 0 |
| Camera motion với transform được cung cấp | 0 | 1 |
| Mất detection, identity chưa bị bác bỏ | 0 | 0 |

Synthetic confirmed switch episodes = 0 cho cả A/B trong các kịch bản này. Với front/partial/motion, interruption mô phỏng giảm 2s -> 0s; crossing 1.1s -> .2s; ambiguous/missing/full-occlusion vẫn 2s. Không gọi các số này là physical PTZ performance. Test camera motion dùng affine synthetic, không phải motor pan thật. Chi tiết `occlusion_synthetic/summary.json`.

Còn lỗi/thiếu: containment không còn isolated support; similar clothes không đủ evidence; full occlusion không localization; detector missing không recovery bằng visual tracker; unknown/unreliable GMC khi camera di chuyển; latency CPU và RTSP backlog; chưa có continuous GT để đo switch/duration; chưa chạy C hoặc SAM2. Không đưa fallback không được kiểm chứng vào production để che các hạn chế này.

## 6. Lệnh tái hiện

Chạy từ `/mnt/files/Project/ptz/ptz`, môi trường `(py)`. Output phải là thư mục mới.

```bash
python -m tools.occlusion_benchmark \
  --cache docs/identity_shadow_audit_20261119_191352/capture_cache \
  --parts --limit 500 --out logs/occlusion_ab_review

PYTHONPATH=.:test python -m unittest \
  test_occlusion_association test_hybrid_tracking test_minor_overlap \
  test_identity_continuity test_auto_pan_tilt test_camera_motion -v

python -m tools.occlusion_shadow \
  --live-shadow --camera-config camera.json \
  --reference person_search/targets/target_20_11.jpg \
  --seconds 60 --out logs/occlusion_shadow_review
```

Live shadow đọc stream và credentials qua URI builder hiện tại; không instantiate motor controller, không đổi GUI/main flow. Chưa chạy lệnh live này trong audit. Kết quả ở `decisions.jsonl`, `overlay.mp4`, `summary.json`; `control_eligible` chỉ là quyết định mô phỏng. Nếu credentials cấu hình không hợp lệ, vẫn cần sửa cấu hình camera; observer không bỏ qua authentication.

Để chạy C khi có model segmentation local phù hợp:

```bash
python -m tools.occlusion_shadow \
  --video /path/to/overlap.mp4 \
  --reference person_search/targets/target_20_11.jpg \
  --seg-model /path/to/local-person-seg.pt \
  --seconds 60 --out logs/occlusion_abc_review
```

## 7. Quyết định tiếp theo

GO cho thu thập/label và shadow read-only. NO-GO cho thay production: chưa có bằng chứng real recall tăng, B hiện reject nhiều hơn, C chưa chạy. Cần dữ liệu liên tục có nhãn target/visibility/front-back và camera-motion, so sánh matched feature/threshold ablation, kiểm chứng part embeddings và segmentation, đo actual latency. Chỉ khi đạt tiêu chí identity switch và interruption trên dữ liệu độc lập mới cân nhắc ghép policy đã kiểm chứng vào pipeline PTZ hiện tại, giữ nguyên UI và arbitration.
