# Phân tích phiên camera 19:13:52 — 19/11/2026

**V4 đã chạy live đúng cấu hình. Bản ghi hợp lệ, dùng được cho nghiên cứu tiếp; không cần quay lại phiên này.** Tuy nhiên, shadow vẫn HOLD nhiều khi người còn hiện trong khung. Chưa đủ bằng chứng để đưa policy identity này vào điều khiển PTZ.

## Dữ liệu và kiểm chứng

- App: `logs/ptz_20261119_191352.log`, đóng sạch lúc 19:17:01.
- Capture: `logs/identity_capture/capture_18e5ba0fa26d492cbb7c19839cccb4b2`.
- Shadow: `logs/identity_shadow/shadow_36b5020b7c7843828b19de10f27d711e.jsonl`.
- 1.120 PNG, frame ID 977–5100, khoảng 137,432 giây, 565.431.206 byte; finalized, recorder 0 dropped/0 errors.
- Đã kiểm tra checksum và decode/shape toàn bộ PNG, source/model hash. Tất cả khớp. Đây là ảnh frame AI đã lấy mẫu, không phải video main stream liên tục; 0 dropped của recorder không chứng minh RTSP không mất frame.
- `SHADOW_START.batched=true`, capture config cũng true; một target session, hai lần reset giữ lịch sử. Các cờ Phase 2, identity-memory control và gallery vẫn OFF. Shadow ghi 0 physical commands.
- Cả 276 comparison đều có ảnh đúng frame ID, timestamp và baseline proposal; không thiếu phần cuối như phiên trước.
- Tái tính OSNet trên 1.120 frame bằng CUDA, rồi replay đúng thời điểm mẫu live: **276/276 khớp selected candidate, reason, state và action**, không mismatch.
- `VERIFIED` trong [live_audit.json](live_audit.json) chỉ xác nhận log vận hành v4 hợp lệ; không chứng nhận accuracy hay hiệu năng đạt yêu cầu sản phẩm.

Chi tiết: [capture_audit.json](capture_audit.json), [sampled_replay_summary.json](sampled_replay_summary.json), [ảnh tổng quan](overview.jpg).

## Nhịp xử lý và độ trễ

| Chỉ số | V3 phiên 19:00 | V4 phiên 19:13 |
|---|---:|---:|
| Thời gian shadow, giây | 172,45 | 136,11 |
| Comparison hợp lệ | 236 | 276 |
| Comparison/giây | 1,37 | 2,03 |
| Khoảng cách mẫu P95, giây | 2,21 | 1,43 |
| Khoảng cách mẫu lớn nhất, giây | 6,53 | 3,47 |
| Khoảng cách mẫu >1 giây | 53/235 | 29/275 |
| Tuổi mẫu median / P95, ms | 391 / 743 | 296 / 702 |
| Kết quả bị loại do quá tuổi | 103 | 49 |
| Completed / discarded inference passes | 526 / 76 | 306 / 15 |

Các chỉ số quan sát được tốt hơn phiên trước, nhưng **hai phiên khác cảnh, thời lượng và tải; không phải A/B có kiểm soát**, không quy toàn bộ chênh lệch cho batching. Max gap v3 gồm ranh giới đổi session; trong cùng session v3 max 5,23 giây và 52 gap >1 giây. V4 chỉ có một session.

V4 vẫn có 655 lần bỏ submit vì worker bận và 6.576 lần thử lại admission. Các lần thử lại không tương đương số frame mất. 29 gap >1 giây vẫn vượt cửa sổ xác nhận liên tiếp của policy.

CSV toàn phiên có 2.144 mẫu `receive_to_gui_submit_ms`: median **65,42 ms**, P95 **88,30 ms**, max **141,98 ms**. Chỉ số này không cho thấy trễ hiển thị hàng giây, nhưng không đo glass-to-glass, độ mượt từng frame hoặc đồng bộ main/sub tuyệt đối.

Trace đã rotation, chỉ còn 38,54 giây cuối ON và 15,74 giây OFF sau, không còn OFF trước:

| Chỉ số trong trace còn giữ | Shadow ON | OFF sau |
|---|---:|---:|
| Detection result/giây | 7,68 | 11,05 |
| Foreground wait P95, ms | 117,12 | 54,64 |
| GUI-submit P95, ms | 92,45 | 84,93 |

Có dấu hiệu cạnh tranh tài nguyên cần theo dõi: foreground wait max ON khoảng 948 ms. Vì cảnh và tác vụ khác nhau, chưa thể kết luận overhead nhân quả hoặc v4 không ảnh hưởng pipeline chính.

## Quyết định identity và nguyên nhân HOLD

Trong 276 comparison:

- 32 lần baseline và shadow cùng chọn một candidate.
- 77 lần cả hai HOLD.
- 167 lần baseline có lựa chọn nhưng shadow HOLD: 117 similarity thấp, 47 chờ xác nhận, 3 overlap.
- Không có shadow-only hoặc hai bên cùng chọn nhưng khác candidate trong tập này. Đây không phải ground truth và không chứng minh cả phiên không đổi nhầm người.

Toàn bộ shadow có 150 `LOW_REFERENCE_SIMILARITY`, 48 `AWAITING_MULTI_OBSERVATION_CONFIRMATION`, 27 `PERSON_OVERLAP`, 18 `NO_OBSERVATION_UNKNOWN`, 1 `LOW_DETECTION_CONFIDENCE`, 32 confirmed. 147/150 lần similarity thấp dùng ngưỡng reacquire 0,7; ba lần còn lại ngưỡng acquire 0,65. Vì vậy không nên diễn giải đơn giản là OSNet hoàn toàn không nhận ra người: mất tính liên tục có thể đẩy policy sang ngưỡng xác nhận lại cao hơn mức keep 0,5.

Đã xem [đủ 32 ảnh được chọn](all_selected.jpg): bbox xanh đều nằm trên người áo đen có chữ trước ngực, quần xám/nâu trong các ảnh này, không nằm trên người áo đỏ. Đây là rà soát trực quan tập selected, chưa có nhãn độc lập đầy đủ cho toàn bộ 1.120 frame.

### Các đoạn đáng chú ý

Các thời lượng dưới đây là từ mẫu HOLD đầu đến mẫu HOLD cuối liên tiếp trong **shadow đã lấy mẫu**, không phải thời gian motor dừng hoặc thời gian target chắc chắn hiện diện liên tục. Mỗi đoạn được xem 12 ảnh trải đều; danh sách frame trong [review_segments.json](review_segments.json).

| Frame | Khoảng HOLD | Quan sát ảnh |
|---|---:|---|
| 2765–3381 | 20,53 giây | Target đi gần camera rồi quay lưng, đi về phía tủ; vẫn thấy trong các ảnh kiểm tra. 26 low similarity, 8 chờ xác nhận. [Ảnh](hold1.jpg) |
| 3488–4040 | 18,40 giây | Target đi từ phía tủ về gần camera, đổi hướng; nhiều ảnh có mặt trước rõ nhưng shadow chưa xác nhận liên tục. 23 low similarity, 9 pending, 1 overlap. [Ảnh](hold2.jpg) |
| 1420–1962 | 18,07 giây | Target gần mép ảnh rồi ra ngoài khung trong một đoạn, sau đó xuất hiện lại bên trái. Có 18 mẫu không có observation; không tính toàn bộ 18 giây là mất người còn hiện diện. [Ảnh](hold3.jpg) |
| 4589–5068 | 15,96 giây | Target quay lưng/đi vòng quanh bàn, có lúc bị đồ vật che một phần; similarity thấp chiếm 29/33 mẫu HOLD. [Ảnh](hold4.jpg) |
| 2089–2522 | 14,45 giây | Target quay lưng đi gần hướng người áo đỏ; hai bbox chồng lên nhau theo góc nhìn. 18 overlap, 19 low similarity, 6 pending. [Ảnh](hold5.jpg) |
| 4118–4520 | 13,40 giây | Target đổi hướng đi quanh bàn và ghế; 18 low similarity, 5 pending. [Ảnh](hold6.jpg) |

### Kiểm tra vai trò của lấy mẫu

Replay ở đúng thời điểm live ghi nhận 12 lần chuỗi pending bị ngắt: 9 vi phạm gap, 6 vi phạm IoU, 0 vi phạm appearance continuity trong các lần đó. Có trường hợp cùng vi phạm gap và IoU nên không cộng thành 15 chuỗi độc lập.

Mô phỏng dùng toàn bộ 1.120 frame với cùng policy, cố định reset tại frame hoàn thành đầu tiên sau mỗi reset (1706 và 2292):

- Tại 276 thời điểm live: 142 giữ nguyên lựa chọn, **134 live HOLD chuyển thành selected**.
- Không có live selected chuyển thành HOLD, không có hai bên cùng selected nhưng khác candidate.
- Toàn bộ dense replay có 786 selected/1.120 observation.

Đây là counterfactual để chẩn đoán, không mô phỏng đúng tài nguyên/scheduler live và không phải accuracy. Tuy nhiên, nó cho thấy cần xử lý nhịp lấy mẫu và giữ liên tục trước khi chỉ giảm ngưỡng similarity hoặc tăng gallery.

## Hướng tiếp theo

1. Giữ v4 làm mặc định cho shadow; đã có bằng chứng live hợp lệ. Baseline tiếp tục điều khiển PTZ.
2. Dùng chính capture mới này cho đánh giá offline Phase 2, đặc biệt hai đoạn target còn thấy nhưng HOLD dài và đoạn quay lưng. Không cần yêu cầu quay lại ngay.
3. Ưu tiên benchmark lịch event-ReID/tiếp tục xác nhận để giảm gap quanh chuỗi pending, đồng thời bảo vệ inference foreground. Lịch event thưa có thể làm gap tệ hơn; phải đo cùng dữ liệu và cùng điều kiện.
4. Đánh giá overlap theo mức che phủ và evidence của người gây nhiễu; không bỏ overlap gate chỉ vì thấy hai người tách biệt ngoài đời.
5. Review nhãn target/visibility tại các đoạn trên trước khi công bố ID switch, recovery latency hoặc accuracy; kết quả của chính OSNet chỉ là pseudo-label.
6. Khi đã có thay đổi đáng kiểm chứng mới cần phiên live tiếp theo. Hiện bạn chưa cần quay thêm để hoàn thành phân tích này.

Phân tích chỉ tạo artifact trong thư mục báo cáo; không sửa runtime, ngưỡng, dữ liệu gốc hay chạy camera/motor. Các script [analyze.py](analyze.py), [replay_sampled.py](replay_sampled.py), [review_segments.py](review_segments.py) lưu cùng dữ liệu kết quả để tái kiểm tra.
