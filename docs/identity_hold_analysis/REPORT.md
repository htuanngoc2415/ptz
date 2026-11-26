# Phân tích và sửa HOLD kéo dài của Phase 1

Đã xem ảnh các đoạn 26–34 s, 70–83 s, 140–146 s trong capture 150 s; đối chiếu frame, bbox, track, reference similarity và lý do HOLD. Đã sửa policy offline và replay lại cả 3 capture thực. Không đổi baseline live, không bật Phase 1 điều khiển camera, không mở camera/motor.

## Nguyên nhân

Ảnh mẫu trong `segment0.jpg`, `segment1.jpg`, `segment2.jpg` cho thấy người mặc áo đen thay đổi hướng nhìn/đi lại, có lúc hai bbox chồng nhau hoặc xuất hiện detection phụ. Đây là quan sát mẫu hỗ trợ chẩn đoán, không phải nhãn GT đầy đủ.

Policy cũ chỉ cho dùng ngưỡng keep=0,50 khi state ngay trước là CONFIRMED_VISIBLE. Một frame overlap/score yếu làm state chuyển AMBIGUOUS. Khi frame đã rõ lại, dù cùng track và vị trí liên tục, policy yêu cầu reacquire=0,70. Điểm 0,60–0,67 không vượt qua, nên HOLD kéo dài cho đến khi góc nhìn phù hợp reference hơn.

- +70,964 s: track 17 được xác nhận ngay trước đó; 3 frame overlap làm gián đoạn. Đến +71,164 s đã hết overlap của target nhưng điểm ~0,62 bị ngưỡng 0,70 từ chối. HOLD kéo tới ~81,7 s.
- +141,632 s: một frame overlap với track 33; sau đó chỉ còn target track 17, điểm khoảng 0,64–0,67. HOLD vẫn kéo dài ~2,63 s.
- +27–33 s: vừa có điểm thấp hơn keep vừa có overlap thực trong bbox. Không nên ép toàn đoạn thành MATCH.

## Thay đổi

`ai/target_identity.py`: thêm đường khôi phục ngắn hạn dùng **quan sát đã được xác nhận cuối cùng** làm mốc. Điều kiện đồng thời:

1. Gián đoạn chưa quá `REID_CONFIRM_MAX_GAP` hiện có (1 s); frame HOLD không được gia hạn mốc.
2. Cùng track ID đã biết, IoU với bbox mốc ít nhất 0,40.
3. Embedding hiện tại tương đồng với embedding mốc ít nhất 0,70.
4. Vẫn đạt reference keep 0,50, margin, confidence và các gate appearance/evidence hiện có; không overlap, không crop occluded, không policy rejection.
5. Xác nhận nhiều quan sát liên tiếp theo cơ chế cũ (3 frame); pending không vượt thời hạn mốc.

Không có observation thì quyền khôi phục ngắn hạn bị hủy cho tới lần xác nhận mạnh tiếp theo; tránh việc frame sau chuyển AMBIGUOUS rồi vô tình mở lại đường keep. Track đổi/không biết, hình học lệch, embedding khác hoặc mốc quá hạn vẫn dùng reacquire mạnh. Không thay ngưỡng toàn cục, không cập nhật gallery, không dùng nhãn tự động để huấn luyện. `candidate_audit` bổ sung `reference_threshold` và `continuity_mode` để giải thích quyết định.

## Kết quả cùng evidence

Kết quả chính thức nằm trong `final/`; các output ở thư mục capture ngay dưới báo cáo là thử nghiệm trung gian, không dùng làm kết quả cuối. Cache OSNet giữ nguyên; mỗi final report ghi checksum source mới. Baseline output giữ nguyên mọi trường trừ target_uid ngẫu nhiên của mỗi lần replay (đã assert).

| Capture | Baseline replay | Phase 1 trước | Phase 1 sau |
|---|---:|---:|---:|
| 150 s / 1667 frame | 1513 | 1270 | **1443** |
| 11,27 s / 114 frame | 80 | 59 | 59 |
| 2,83 s / 26 frame | 0 | 0 | 0 |

Bản 150 s thêm 173 frame chọn target, không bỏ frame đã chọn trước đó. Không có frame cả baseline và policy mới cùng chọn nhưng chọn khác candidate. Đây là agreement với prediction baseline, không phải 173 frame đã được chứng minh đúng bằng GT.

- Đoạn +70,964–81,699 s: trước HOLD 124/124; sau chỉ HOLD 3 frame overlap + 2 frame xác nhận, chọn 119 frame. Khôi phục tại **+71,435 s**, khoảng **0,47 s** từ lần HOLD đầu.
- Đoạn +141,632–144,264 s: trước HOLD 32/32; sau HOLD 1 overlap + 2 xác nhận, chọn 29 frame. Khôi phục tại **+141,932 s**, khoảng **0,30 s**.
- Đoạn +27,166–32,533 s: khôi phục 21/54; giữ 33 HOLD vì 19 overlap, 7 score yếu, 7 chờ xác nhận.
- Đoạn dài nhất còn baseline chọn nhưng Phase 1 HOLD là +30,534–32,533 s (~2 s giữa frame đầu/cuối). Không nới overlap gate để loại bỏ đoạn này.

Tổng lý do policy mới trên 1667 frame: 1443 CONFIRMED_MULTI_CUE, 116 PERSON_OVERLAP, 63 LOW_REFERENCE_SIMILARITY, 32 chờ xác nhận, 13 LOW_DETECTION_CONFIDENCE. `comparison_summary.json` chứa thống kê và các khoảng thời gian; `compare.py` tái lập đối chiếu.

## Kiểm chứng và giới hạn

**268 test pass** trong `final_tests.log`; thêm 6 regression về: xác nhận sau gián đoạn ngắn; frame bị từ chối không gia hạn mốc; track/geometry/appearance không khớp; margin/overlap không bị bỏ qua; mất observation buộc reacquire mạnh; cùng ID và reference score nhưng embedding khác mốc không được khôi phục. Compile và diff check pass. Ba replay hoàn tất, mọi GT identity accuracy vẫn null, không phát lệnh vật lý.

Sửa giải quyết được một cơ chế HOLD kéo dài quan sát thấy trong dữ liệu. Chưa chứng minh giảm ID switch hoặc wrong-person tracking: cùng track, IoU và embedding tương đồng vẫn có thể đánh lừa policy nếu hai người rất giống nhau; chưa có GT độc lập cho toàn bộ capture. Những capture này cũng đã dùng để tìm lỗi, không phải tập kiểm định độc lập. Chưa đánh giá scheduler/PTZ thực vì vẫn là policy replay.

Giữ Phase 1 và gallery OFF trong live. Không cần record lại để hoàn tất sửa này. Trước khi đưa vào motor, cần kiểm định độc lập hoặc shadow chạy cùng baseline để thu quyết định của policy mới và kiểm tra những trường hợp khác nhau, đặc biệt crossing với người giống nhau. Không có yêu cầu người dùng phải gán lại toàn bộ nhãn ở bước này.

## Tệp và phục hồi

Chỉ sửa `ai/target_identity.py`, `test/test_target_identity.py`; snapshot trước sửa ở `../validation/identity_hold_before/`. `changes.json` ghi checksum trước/sau. Không dùng git reset vì workspace có thay đổi cũ của người dùng. Báo cáo/replay cũ được giữ nguyên; không thay checksum của rollback Phase 1 cũ để che giấu sửa mới.
