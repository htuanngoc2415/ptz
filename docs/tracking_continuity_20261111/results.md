# Kết quả kiểm thử — 11/11/2026

- Môi trường: `/home/admin1/miniconda3/envs/py/bin/python`, Python 3.10, Ultralytics 8.4.25.
- Toàn bộ kiểm thử dùng mock/simulator; không kết nối camera thật.
- Bộ trọng tâm: **199 test đạt**, 6.504 s. [Log](scoped-tests.log).
- Runner toàn dự án: **407 mục test, 405 đạt, 2 lỗi import môi trường**, 12.007 s; exit code 1. Không được coi toàn bộ suite đã đạt. [Log](full-tests.log).
- Hai lỗi: `test_gstreamer_cpu` không import được module; `test_rtsp_unavailable_schedules_backoff_without_raising` không import được RTSPPlayer. Cả hai do `ModuleNotFoundError: gi` rồi `SystemExit(2)`. Runner đã thử ABI-compatible system fallback nhưng không có bản phù hợp.
- Các test OpenCV disconnect/mailbox và motor watchdog vẫn đạt. Không thay đổi dependency hay bỏ qua test để tạo kết quả xanh.

Lệnh bộ trọng tâm, tại thư mục `ptz/` chứa config.py:

```sh
PYTHONPATH=.:test /home/admin1/miniconda3/envs/py/bin/python -m unittest \
  test_hybrid_tracking test_reid_target test_crossing_retention \
  test_auto_pan_tilt test_zoom_ownership test_zoom_latency_safety \
  test_runtime_waiting test_hybrid_latency test_face_identity \
  test_hanet_controller test_hanet_rpc test_control_regression \
  test_dahua_camera test_dual_stream test_opencv_player
```

Runner toàn dự án:

```sh
/home/admin1/miniconda3/envs/py/bin/python tools/run_unit_tests.py
```

Chưa có benchmark video gán nhãn trước/sau, chưa đo motor vật lý, chưa đo giảm latency hay wrong-target switching ngoài thực tế. GMC/BoT-SORT, online gallery và thay đổi fairness pan/zoom cần benchmark riêng như [kế hoạch trong audit](implementation.md).
