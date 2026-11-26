# Báo cáo tình trạng AI PTZ qua hai phiên log mới nhất

Ngày phân tích: **11/11/2026**. Tất cả thời gian dưới đây dùng **Asia/Ho_Chi_Minh, UTC+7**.

## 1. Kết luận chính

**Bản sửa verification nền đã có dấu hiệu hoạt động đúng, nhưng hệ thống chưa duy trì target liên tục trong hai phiên mới.** Không còn chuỗi `TEMP_LOST ↔ OCCLUDED_HOLD` trong log ứng dụng. Một điểm ReID thấp đơn lẻ đã được chịu đựng mà không lập tức mất khóa. Tuy nhiên, ambiguity và overlap vẫn đưa target vào hold, rồi có thể mất khóa sau grace.

Các vấn đề nổi bật hiện tại:

1. Phiên đầu khóa một lần, mất một lần và không bắt lại trước khi đóng ứng dụng. Phiên sau khóa ba lần, mất ba lần, bắt lại sau mất hai lần; ngoài ra có bốn lần phục hồi từ hold mà chưa mất khóa hoàn toàn.
2. Phiên sau có tình trạng **vừa bắt lại đã hold do overlap**, dù similarity acquisition đủ cao. Đây là điểm ưu tiên điều tra tiếp theo.
3. Phiên đầu có **290 quyết định recovery bị chặn với `STALE_RECOVERY_OBSERVATION`**. Chưa thấy zoom out recovery được thực hiện.
4. Phiên sau có **247 quyết định zoom trả `DISABLED`**. Không được kết luận camera không zoom vì lỗi arbitration trong phiên này.
5. Detection đạt khoảng **7,75–8,93 FPS**; ReID mean khoảng **110–115 ms**, P95 **230–242 ms**. Pipeline vẫn có chi phí ReID lớn, nhất là khi tìm kiếm/recovery.
6. CSV tổng kết zoom có lỗi bao phủ backend: ghi **0 lệnh**, trong khi phiên đầu có **4 xung zoom in, 4 ACK và 4 lần kết thúc** trong trace/JSONL.

Báo cáo này chỉ đọc log và đối chiếu source. Không chạy camera, không phát lệnh motor và không sửa code vận hành.

## 2. Phạm vi và nguồn bằng chứng

“Hai log mới nhất” được hiểu là **hai phiên chạy ứng dụng mới nhất**, không phải hai file phụ cùng một phiên.

| Thuộc tính | Phiên A | Phiên B — mới nhất |
|---|---|---|
| Log chính | [ptz_20261111_201610.log](../logs/ptz_20261111_201610.log) | [ptz_20261111_201808.log](../logs/ptz_20261111_201808.log) |
| JSONL ứng dụng | [201610.jsonl](../logs/ptz_20261111_201610.jsonl) | [201808.jsonl](../logs/ptz_20261111_201808.jsonl) |
| Bắt đầu | 20:16:10.379 | 20:18:08.250 |
| Dừng ứng dụng | 20:18:04.875 | 20:19:51.435 |
| Thời lượng ứng dụng | 114,50 s | 103,19 s |
| Run ID | `88d6fba9aa544843a8f2f29bc9d240ad` | `cdcae4f2981243d7a0607f6df9be9e05` |
| Hậu tố profiler/camera | `201613_844887` | `201811_670655` |
| Hậu tố zoom trace | `201613_844525` | `201811_670203` |

Đã đọc log chính, JSONL ứng dụng, latency trace gồm file chính và `.1/.2/.3`, CSV latency, camera log, zoom trace gồm file chính và `.1`, cùng CSV zoom.

**Giới hạn lưu trữ:** dù đã gộp các file xoay còn tồn tại, latency trace A chỉ còn từ **20:16:38.083**, B chỉ còn từ **20:18:37.646**. Phần đầu đã không còn trong tập trace được lưu. Vì vậy:

- Số liệu toàn phiên lấy từ `LATENCY_REPORT` cuối phiên và CSV aggregate; không lấy số dòng trace còn lại làm tổng phiên.
- Timeline state đầy đủ hơn lấy từ log ứng dụng; lý do chi tiết chỉ kết luận khi còn structured event.
- Ví dụ A còn 26 dòng `TARGET_VERIFICATION`, trong khi counter tổng là 34 job verification-only. Không coi chênh lệch này là lỗi worker.
- Percentile có phạm vi `bounded_recent_window`; count/mean của profiler có phạm vi `since_profiler_start`.

## 3. So sánh chỉ số

| Chỉ số | Phiên A | Phiên B |
|---|---:|---:|
| Frame nhận | 3.304 | 2.967 |
| Detection frames | 859 | 890 |
| Camera receive FPS | 29,82 | 29,78 |
| Detection FPS | 7,75 | 8,93 |
| GUI unique FPS | 12,00 | 12,91 |
| Dropped frames — counter | 2.251 | 1.870 |
| Superseded detection frames | 156 | 171 |
| Stale inference results — counter | 51 | 5 |
| Dòng `PERCEPTION_RESULT_STALE` trong JSONL | 12 | 5 |
| Target lock success | 1 | 3 |
| Tracking lost | 1 | 3 |
| Recovery sau mất khóa | 0 ghi nhận | 2 |
| Lần vào `OCCLUDED_HOLD` — log ứng dụng | 1 | 7 |
| Hold → locked — chưa mất khóa hoàn toàn | 0 | 4 |
| Verification-only count | 34 | 43 |
| Tracker rebind counter | Không có counter trong report | 1 |
| SCRFD calls | 342 | 513 |
| ArcFace/recognition attempts | 17 | 21 |
| Recognition accepted observations | 17 | 21 |
| Rejected `FACE_ASSOCIATION_AMBIGUOUS` | 111 | 169 |
| Identity confirmation count | 1, thêm 1 reverify | 2 |

`Dropped frames` không đồng nghĩa RTSP mất gói: latest-frame mailbox chủ động bỏ qua frame khi inference chậm hơn luồng nhận. Các counter dropped/superseded có phạm vi riêng, không cộng tùy tiện. Tỷ số stale/detection lần lượt khoảng **5,94% và 0,56%**, chỉ là tỷ số hai counter, không phải tỷ lệ frame duy nhất đã đối soát.

17/21 accepted recognition observations không đồng nghĩa 17/21 lần xác nhận danh tính độc lập. Các rejection ambiguity cũng không đồng nghĩa từng ấy lượt ArcFace chạy thất bại.

### Độ trễ từ profiler

Đơn vị ms; dạng **mean / P95 / max**.

| Stage | Phiên A | Phiên B |
|---|---:|---:|
| YOLO total | 34,54 / 59,72 / 445,71 | 32,53 / 56,18 / 434,75 |
| ReID total | 109,85 / 229,53 / 463,28 | 115,33 / 241,74 / 580,90 |
| Detection queue wait | 3,68 / 18,06 / 366,90 | 3,84 / 23,70 / 368,13 |
| Camera command queue wait | 0,61 / 2,15 / 6,14 | 0,58 / 1,93 / 5,90 |
| Camera transaction | 3,82 / 11,65 / 17,42 | 4,13 / 10,88 / 18,94 |

ReID total có **521 mẫu ở A, 390 mẫu ở B**; đây không chỉ là periodic verification, còn gồm tìm kiếm và recovery. Không so trực tiếp với 34/43 verification-only để kết luận chạy thừa. Camera command queue không cho thấy nghẽn kéo dài trong hai phiên. Detection queue vẫn có đỉnh chờ khoảng 367–368 ms.

B có FPS tốt hơn và ít stale hơn A, nhưng ReID P95 cao hơn; hai cảnh thử không có replay/ground truth chung nên chưa thể kết luận mức cải thiện do code.

## 4. Diễn biến tracking và identity

### Phiên A

- **20:16:22.874:** thay target bằng `target_11_11.jpg`, detect được một person trong ảnh tham chiếu.
- **20:16:28.643:** khóa target. Đến lúc vào hold ở 20:17:04.484 có khoảng **35,84 s không xuất hiện hold trong state log**; đây là continuity của state, không phải xác nhận đúng người bằng ground truth.
- **20:16:51.532:** face recognition ghi MATCH; sau đó có reverify MATCH lúc 20:17:01.511.
- **20:16:59.156:** verification score **0,467**, thấp hơn keep threshold 0,50, nhưng không có chuyển sang hold ngay. Mẫu kế tiếp 20:17:00.197 đạt **0,546**, margin **0,180**. Đây là bằng chứng cụ thể cơ chế chịu đựng một negative đang có tác dụng.
- **20:17:04.484:** `VERIFIED → OCCLUDED_HOLD`, reason **`AMBIGUOUS_IDENTITY`**, score **0,546**, margin **0,0323**. Score trên keep threshold nhưng khoảng cách với ứng viên khác không đạt 0,08.
- **20:17:07.689:** mất khóa, sang `SEARCHING`; last-seen age khoảng **3,29 s**. Structured transition lúc này mang reason `MOTION_DISCONTINUITY`; không nên dùng nó thay cho nguyên nhân khởi đầu hold là ambiguity.
- Không có reacquisition đến khi ứng dụng dừng 20:18:04.875.

### Phiên B

- **20:18:19.414:** thay cùng ảnh target.
- **20:18:31.216:** khóa lần đầu.
- **20:18:31.996 → 20:18:33.510:** hold rồi phục hồi, khoảng **1,51 s**. Phần latency trace này đã xoay mất nên chưa xác định reason chi tiết.
- **20:18:34.216 → 20:18:36.383:** hold rồi phục hồi, khoảng **2,17 s**; cũng thiếu structured reason đầu phiên.
- **20:18:40.514 → 20:18:42.281:** hold **`PERSON_OVERLAP`**, rồi phục hồi sau khoảng **1,77 s**.
- **20:18:53.730 → 20:18:55.654:** hold **`AMBIGUOUS_IDENTITY`**, score **0,454**, margin **0,0296**; phục hồi sau khoảng **1,92 s**.
- **20:18:55.654 → 20:19:26.292:** khoảng **30,64 s không có hold trong state log**, dù có nhiều face-miss và verification score khoảng 0,52–0,55. Không thấy face miss tự xóa person lock trong đoạn này.
- **20:19:26.292:** hold do overlap. **20:19:29.407:** mất target.
- **20:19:31.413:** bắt lại; chỉ khoảng **71 ms** sau đã hold do overlap. Score tại transition **0,708**, margin **0,375**. **20:19:34.642:** mất tiếp.
- **20:19:35.036:** bắt lại; khoảng **131 ms** sau lại hold do overlap. Score **0,732**, margin **0,421**. **20:19:38.323:** mất tiếp.
- Cuối phiên vẫn `SEARCHING`; các score được in cuối phiên chủ yếu khoảng 0,31–0,42, chưa đủ reacquire.

Hai lần recovery sau full loss có latency profiler **2.002,76 ms** và **389,43 ms**, mean **1.196,09 ms**. Bốn lần hold recovery phía trước là loại chuyển trạng thái khác, không cộng vào recovery counter này.

**Nhận định:** acquisition có thể chấp nhận appearance đủ mạnh trong cảnh đang overlap, nhưng ngay observation sau nhánh tracking lại thu hồi quyền. Log chứng minh chuỗi này xảy ra; chưa đủ bbox/video để kết luận overlap là thật, detection trùng hay gate quá nhạy. Không nên chỉ hạ threshold để tránh hold.

## 5. PTZ và auto zoom

### Phiên A: có zoom in; recovery bị chặn

Từ 20:16:35.390 đến 20:16:37.278, trace có:

- **4 `ZOOM_STARTED`**, đều có `ZOOM_COMMAND_ACK` với `command_kind=pulse`.
- **4 `ZOOM_ACTION_ENDED`**: ba `PULSE_COMPLETE`, một `HOLD_ZONE`.
- Log ratio trước các xung: **0,265 → 0,267 → 0,327 → 0,348**; dải target ghi trong log **0,360–0,440**.
- Không có chứng cứ lens-position để xác nhận hành trình motor. Ratio thay đổi phù hợp với việc phóng lớn ảnh người nhưng cũng có thể chịu ảnh hưởng người di chuyển.

Có **290 `ZOOM_CONTROL_DECISION` bị chặn bởi `STALE_RECOVERY_OBSERVATION`**, và 464 policy decision `RECOVERY`. Đây là số lần đánh giá/chặn, không phải 290 lệnh motor thất bại. Hàm `_recover_missing` dùng cùng reason cho timestamp không hợp lệ, thứ tự timestamp sai, observation quá tuổi hoặc publish quá tuổi; log hiện tại chưa phân biệt được điều kiện nào gây chặn.

Camera log có **308 transaction start, 307 end, 1 error**. Error lúc **20:18:04.581**, operation `finish`, gần thời điểm đóng ứng dụng. Event không ghi exception cụ thể; chưa thể kết luận là lỗi motor/STOP hay lỗi cleanup.

### Phiên B: auto zoom disabled

Toàn bộ **247 `ZOOM_CONTROL_DECISION` đều `HOLD / DISABLED`**. Không có `ZOOM_START` trong JSONL ứng dụng và không có `ZOOM_STARTED` trong zoom trace. Vì vậy phiên B chưa kiểm chứng được auto zoom theo target, recovery zoom hoặc zoom starvation.

Startup cả hai phiên đều in Auto Zoom=False, nhưng A có zoom chạy về sau. Không dùng giá trị startup để suy ra trạng thái toggle của cả phiên.

Không có `ZOOM_COMMAND_DEFERRED` trong phần trace được giữ; không đủ để kết luận vấn đề `OWNER_pan_tilt` cũ đã được giải quyết. Phiên A có thao tác manual pan-right trong phần latency trace còn lại; thao tác tay là yếu tố cần kiểm soát khi so sánh cảnh thử.

### Lỗi thống kê zoom cần sửa

CSV zoom của cả hai phiên cộng `number_of_zoom_commands=0`; với A, kết quả này mâu thuẫn với 4 xung có ACK. Source `telemetry/zoom_trace.py` chỉ tăng counter khi event `ZOOM_COMMAND_SENT` có `rpc_method == 'ptz.start'`. Đường HANET trong phiên A ghi lifecycle/ACK nhưng không có sự kiện theo dạng đó.

Do đó **không dùng cột số lệnh trong CSV làm số lệnh HANET thực tế**. Cần chuẩn hóa command-sent event theo backend, tránh đếm trùng cả ACK và start.

## 6. Face và chất lượng quan sát

Face recognition đã có MATCH trong cả hai phiên; các MATCH được in cùng một nhãn danh tính. Điều này là bằng chứng pipeline nhận diện đang hoạt động, không phải ground truth xác nhận toàn bộ quá trình theo đúng người.

Có **111/169 observation bị từ chối vì `FACE_ASSOCIATION_AMBIGUOUS`**. Cần phân tích cạnh tranh giữa các bbox person/face và timestamp, không coi đây chỉ là lỗi ArcFace hoặc hạ face threshold. Giữ rejection khi association chưa chắc chắn là cần thiết để tránh gán mặt người khác.

JSONL có 19/26 `FACE_LOST`, trong khi person full-loss chỉ 1/3. Hai counter không có quan hệ một-một; log có các đoạn face-miss vẫn giữ person tracking.

Không ghi nhận `RTSP_DISCONNECTED` hoặc traceback trong log ứng dụng của hai phiên. Cả hai có thông báo ứng dụng đã dừng; chưa suy ra lý do người dùng đóng hoặc trạng thái motor vật lý từ dòng này.

## 7. Ưu tiên xử lý tiếp theo

1. **P0 — Overlap và reacquisition:** lưu bbox của tất cả ứng viên, track ID, overlap ratio, score/margin và ảnh frame tại các mốc vừa reacquire đã hold. Kiểm tra detection trùng, người thật giao nhau và khác biệt gate giữa acquisition/tracking. Chưa có bằng chứng để nới gate.
2. **P0 — Recovery freshness:** tách `STALE_RECOVERY_OBSERVATION` thành lý do cụ thể, ghi observation/publish age và thứ tự timestamp. Điều tra 290 lần chặn trước khi điều chỉnh tuổi cho phép; không bỏ freshness guard.
3. **P1 — Telemetry HANET:** sửa counter zoom để phản ánh đủ backend; bổ sung exception/detail cho transaction `finish`; ghi cấu hình có hiệu lực và trạng thái Auto Zoom/Auto Pan theo thời gian.
4. **P1 — Face association:** lấy mẫu các rejection ambiguity kèm bbox/body session và tuổi observation; xác định rejection hợp lý hay association quá nhạy.
5. **P1 — Performance:** tách ReID latency theo SEARCHING, recovery và periodic background; ghi trigger/candidate count. Aggregate hiện tại không đủ để quyết định thay model hoặc giảm verification.
6. **P2 — Thử nghiệm có kiểm soát:** dùng cùng video gán nhãn hoặc cùng kịch bản, xác nhận auto pan/zoom bật, ghi thao tác manual, rồi đo đúng-target continuity, wrong-target switch và thời gian hold. Hai phiên này chưa đủ để đánh giá GMC/BoT-SORT, đa góc nhìn hoặc fairness motor.

Không coi cấu hình source hiện tại là snapshot cấu hình hai phiên: chẳng hạn `MAX_CONTROL_OBSERVATION_AGE` đang là 0,8 s, nhưng có stale event với tuổi frame thấp hơn mức này; nhánh stale còn xét reset và phiên có thể dùng cấu hình khác. Cần effective-config snapshot để giải thích chính xác, thay vì khẳng định các event đó vi phạm threshold.

## 8. Đánh giá hiện tại

| Hạng mục | Đánh giá dựa trên hai log |
|---|---|
| Periodic verification không tự làm mất khóa | Có bằng chứng hoạt động; không còn TEMP_LOST ping-pong trong log ứng dụng |
| Chịu được một score thấp | Có ví dụ thực tế 0,467 rồi phục hồi, không hold ngay |
| Tracking liên tục qua mọi tình huống | Chưa đạt; còn hold, full loss và vừa reacquire đã overlap |
| Reacquisition | Hoạt động ở B nhưng độ bền sau bắt lại còn kém ở cuối phiên |
| Face pipeline | Có MATCH; nhiều rejection do association ambiguity |
| Auto zoom HANET | A có 4 xung ACK; B disabled; chưa đủ để đánh giá bám/zoom toàn diện |
| Recovery zoom | A có 290 quyết định bị chặn freshness, cần điều tra |
| Camera queue | Mean/P95 thấp; một error cleanup A thiếu chi tiết |
| Độ tin cậy telemetry | Cần sửa đếm lệnh HANET và tăng khả năng giữ trace đầu phiên |

**Trạng thái cuối của cả hai phiên là đang tìm lại target trước khi đóng ứng dụng. Chưa thể coi mục tiêu tracking liên tục đã hoàn thành.**
