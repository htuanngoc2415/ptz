# Phân tích bằng chứng runtime có sẵn

Dữ liệu chính là `logs/latency/latency_trace_20261020_133204_171028.jsonl` cùng `.1`–`.3`: **69.414 events**, 7 lock sessions, 5 sessions có `IDENTITY_CONFIRMED`. Báo cáo: [HTML tương tác](../logs/pipeline_profile/historical_20261020_133204/report.html), [JSON](../logs/pipeline_profile/historical_20261020_133204/report.json), [CSV](../logs/pipeline_profile/historical_20261020_133204/latency.csv).

Đây là log runtime lịch sử, không phải benchmark camera vừa chạy. Scenario/ground truth P1/P2/P3 chưa có nhãn. Không gán nhãn thành công nhận đúng người chỉ vì có `IDENTITY_CONFIRMED`. Không ghép với log test/mock. Source hiện tại có thể khác code khi log cũ được tạo, nên không dùng số này để khẳng định hiệu năng bản instrument mới.

## Xếp hạng các khoảng trễ quan sát được

Đơn vị ms. Các dòng inclusive và các nhánh song song **không cộng được**. Xếp hạng này xác định nơi cần điều tra; không khẳng định mọi khoảng trễ nằm trên critical path tới identity.

| Khoảng đo | n | p50 | p90 | p99 | Kết luận được phép |
|---|---:|---:|---:|---:|---|
| Zoom request → visual stability, gồm cả motor | 5 | 3414,11 | 5582,95 | 6731,89 | Chuỗi zoom/ảnh mất nhiều giây; chưa tách hết residual settling |
| Zoom motor duration theo polling | 5 | 1745,28 | 2343,55 | 2629,65 | Motor là khoảng thành phần dài nhất đã có đo riêng |
| Recognition scheduler wait | 27 | 137,05 | 170,06 | 188,18 | Chờ accelerator lớn hơn ArcFace inference p90 |
| Detection mailbox wait | 1790 | 20,05 | 95,41 | 162,23 | Ảnh có tuổi đáng kể trước khi chạy detector |
| OSNet inference, host wall | 1225 | 68,76 | 86,12 | 101,67 | Inference p90 lớn nhất trong 4 model ở trace này |
| YOLO inference, host wall | 1733 | 42,70 | 59,24 | 77,09 | Cùng OSNet chiếm nhiều thời gian mỗi vòng AI |
| SCRFD inference | 346 | 39,50 | 45,88 | 52,71 | p90 thấp hơn YOLO/OSNet, nhưng vẫn cạnh tranh tài nguyên |
| ArcFace inference | 27 | 36,62 | 40,26 | 605,12 | p99 cao, cần xem riêng outlier; không kết luận chỉ từ p90 |
| State-machine blocking riêng | — | N/A | N/A | N/A | Log cũ có reason/count nhưng thiếu đầy đủ begin/end |
| Focus movement / quality settling | — | N/A | N/A | N/A | Không có evidence đủ trong trace chính |

RPC zoom ACK p90 **61,34 ms** (137 responses, gồm các lệnh start/stop theo metric cũ); camera command queue không phải khoảng dài nhất. ACK nhanh không chứng minh motor tới đích nhanh. Face scheduler p90 chỉ 0,092 ms nhưng p99 209,82 ms và max 917,05 ms (347 mẫu), nên có outlier mà p90 che khuất.

Các interval recognition wait đều ghi `resource=accelerator`. Một phép đối chiếu union timestamp với YOLO-total và OSNet-inference cho overlap trung bình **66,9%** mỗi wait (0–93,9%). Điều này hỗ trợ giả thuyết cạnh tranh accelerator với detection/ReID. Nó không phải proof đầy đủ về ownership, vì trace cũ chưa có `RESOURCE_ACQUIRED/RELEASED`; phần mới đã instrument các span đó. Không cộng các overlap interval trùng nhau.

Recovery (`tracking_recovery_ms`) p50 **9.302,04 ms**, p90 **18.955,10 ms**, n=6. Đây là tổng hành trình từ mất target đến reacquire, bao gồm search/scene/điều khiển, không được gán toàn bộ cho state machine.

## E2E và các khoảng song song

Origin là receive của frame ứng viên ReID đầu tiên được chấp nhận, **không phải lần target thực sự xuất hiện hay sensor capture**. 5 phiên có đủ origin và identity cho p50 **5.189,67 ms**, p90 **7.630,22 ms**, p99 **8.286,57 ms**, min 1.857,49 ms, max 8.359,50 ms. Hai lock session còn lại không có endpoint identity: N/A, không tự xem là 0 hoặc completion timeout. n=5 quá nhỏ để coi p99 là ước lượng ổn định cho production.

Ví dụ session `lock_11ebac4f60974f28a3c45ca6a73bec7d`, elapsed theo cùng origin:

| Mốc | ms |
|---|---:|
| YOLO đầu tiên liên kết được | 304,34 |
| OSNet lock | 735,82 |
| Face detected | 815,08 |
| First zoom dispatch sau lock | 1520,34 |
| Identity confirmed | 5189,67 |
| Motor arrival đầu tiên có telemetry | 7650,64 |
| Visual stability đầu tiên có event | 8988,89 |

Identity hoàn tất **trước** motor arrival/visual stability trong session này. Vì vậy gọi tổng motor + face + inference là E2E sẽ sai. Các mốc motor/visual ở bảng là mốc đầu tiên quan sát được của session, có thể thuộc command khác first zoom; muốn chia thời gian từng command phải join `command_id`, không trừ các mốc session tuỳ ý. Framing, first usable-face và focus quality endpoint không có trong trace này nên N/A.

Khoảng lock → first zoom trong ví dụ ~784,51 ms; face đã xuất hiện chỉ ~79,26 ms sau lock. Có thể inspection reason/policy/command giúp giải thích gap, nhưng log cũ không chứng minh 784,51 ms bị block hoàn toàn bởi một guard cụ thể. Báo cáo giữ event/reason để điều tra thay vì tự gán nguyên nhân.

## FPS, stale và reject

Snapshot cuối trace chính: receive **7,648 FPS**, detection **5,645 FPS**, SCRFD **1,128 calls/s**. Đây là rates từ lúc profiler bắt đầu, bao gồm startup/idle; biểu đồ report dùng bucket 1 giây từ retained events, có scope khác. Không coi số này là FPS cảm biến.

Counter cuối: `dropped_frames=528` (mailbox gaps theo implementation cũ), `stale_inference_results=39`, `stale_inference_jobs_skipped=1`. Code cũ thiếu đếm riêng frame bị refresh supersede nên số drop này không phải tổng mọi frame không được YOLO xử lý; instrumentation mới có counter bổ sung. GUI FPS/receive→Tk và CPU/GPU cũ = N/A.

Lý do lặp nhiều: `RECOVERY_WIDE_LIMIT_OR_BUDGET` 505, `FACE_OWNER` 281, `WAIT_RECOVERY_IDLE_FEEDBACK` 164, `ERROR: RESET TARGET OR AUTO ZOOM TO RETRY` 108; `WAIT_VISUAL_STABILITY` 24, `ZOOM_HOLD_FACE_TEMPORARILY_LOST` 19, `VISUAL_SETTLING_TIMEOUT` 1. Đây là số event/reason (một hành động có thể ghi nhiều event), không phải số lần lỗi độc lập và không phải duration. Không suy ra occlusion thật chỉ từ face miss.

## Đề xuất tiếp theo, chưa áp dụng

1. **Zoom/ảnh:** ưu tiên thu đủ command ID + ACK + position polling + visual timestamps trên P1; chia motor travel và hậu motor bằng cùng command. Kiểm tra số correction, quãng di chuyển, poll cadence và tác động exposure/ảnh ổn định trước khi cân nhắc điều chỉnh control.
2. **Scheduler:** đo resource-hold overlap ở P2/P3, queue age và job drops. Nếu starvation được xác nhận, đánh giá lịch tài nguyên/CPU branch/nhịp verification hiện tại bằng A/B cùng workload. Chưa đổi scheduling priority hay cadence.
3. **Inference:** OSNet rồi YOLO là hai ứng viên đầu tiên trong trace này. Tách preprocess, kernel execution nếu cần trong phiên GPU profiling riêng, postprocess và tensor synchronization. Kiểm tra batch/candidate count và memory transfers trước khi đề xuất đổi precision/model. Chưa thay model.
4. **State/recovery:** cần live trace mới với gate dwell và context generation; phân biệt guard an toàn có chủ đích với delay không cần thiết. Không giảm freshness threshold để loại bỏ stale reject trong báo cáo.
5. **Focus:** chạy P1 với Auto Focus được người vận hành bật để thu CNN, frame wait, motor loop và fresh quality confirmation. Chưa có số thực đủ để xếp hạng focus hay sửa cooldown/threshold.
6. **Overhead:** chạy cùng camera workload ON/OFF để đo phần ảnh hưởng lên E2E/FPS. Microbenchmark đã đo chi phí producer và writer drain, nhưng không thay thế phép đo A/B workload.

Phiên đối chiếu 13:43:27 có 28.155 events, 4 locks, không có identity endpoint. Motor p90 2.493,23 ms (3 mẫu), recognition wait 160,90 ms, OSNet 83,76 ms, YOLO 57,78 ms. Đây chỉ là bằng chứng tham khảo độc lập, không gộp thành cùng scenario hay A/B performance claim.

## Giới hạn xác minh

Không có phiên camera P1/P2/P3 mới được kích hoạt trong lần triển khai; không có physical motor command nào được gửi. Tests chỉ kiểm tra measurement và các concurrency/settling boundaries bị instrument; model computation AST giữ nguyên sau khi bỏ timing wrappers. HTML có thể mở offline, có dữ liệu và controls; môi trường browser sandbox có lỗi GPU/Firefox shutdown nên không dùng browser timing làm benchmark GUI camera.
