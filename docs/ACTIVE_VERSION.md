# Phiên bản active — sau audit 16/11/2026

Đã rollback về **baseline B pre-asynchronous, có minor-overlap fixes**. Không phải bản 08:38 nguyên gốc.

Runtime: `/mnt/files/Project/ptz/ptz`; branch `baseline/verified-pre-async-20261116`; HEAD `ede31989fe6a39b04d7cd4d31675ebd811a28397` + source/config dirty changes đúng snapshot B.

Chạy từ `/mnt/files/Project/ptz`: `conda activate py`, sau đó `python ptz/main.py`.

Bản async cũ: `/mnt/files/Project/ptz_performance_experiment_20261116/installed_async_before_rollback`. Candidate: `/mnt/files/Project/ptz_performance_experiment_20261116/ptz`, branch `experiment/ptz-performance-recovery`, commit `07f6593`. Không chạy đồng thời các controller.

Báo cáo và hướng dẫn phục hồi: [comprehensive audit](comprehensive_audit_20261116/report.md).
