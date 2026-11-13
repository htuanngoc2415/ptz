"""Application service construction. Heavy models are initialized once in the AI worker."""
import config as cfg
from event_logging import event_print as print
from camera import create_controller
from ai import PersonDetector, FaceDetector, FaceRecognizer, FaceDatabase, FaceMatcher
from control import TargetStateMachine, AutoZoomController, AutoFocusController, DigitalPTZController


def initialize_models(self):
    from runtime.device import resolve_device
    device = resolve_device(cfg.DEVICE, strict=True)
    # ReID shares the resolved device with YOLO, including target-image loading.
    cfg.REID_DEVICE = f'cuda:{device}' if isinstance(device, int) else str(device)
    self.person_detector = PersonDetector(
        model_path=cfg.PERSON_MODEL_PATH,
        conf_threshold=cfg.BYTE_TRACK_LOW_THRESHOLD,
        iou_threshold=cfg.PERSON_IOU_THRESHOLD,
        class_id=cfg.PERSON_CLASS_ID,
        device=device,
        imgsz=cfg.PERSON_IMGSZ,
    )

    self.face_detector = FaceDetector(
        model_path=cfg.FACE_DETECT_MODEL,
        providers=["CPUExecutionProvider"] if device == "cpu" else None,
        input_size=cfg.FACE_DET_INPUT_SIZE,
        conf_threshold=cfg.FACE_DET_CONF,
        nms_threshold=cfg.FACE_DET_NMS,
    )

    self.face_recognizer = FaceRecognizer(
        model_path=cfg.FACE_RECOGNITION_MODEL,
        # CUDA FP16 is the default; explicit CPU mode overrides the ArcFace setting.
        providers=(["CUDAExecutionProvider"]
                   if cfg.ARCFACE_DEVICE == "cuda" and device != "cpu"
                   else ["CPUExecutionProvider"]),
    )

    self.face_database = FaceDatabase(
        cfg.FACE_DATABASE_PATH
    )

    self.face_matcher = FaceMatcher(
        self.face_database,
        threshold=cfg.FACE_RECOGNITION_THRESHOLD,
    )

    print(
        f"[FaceDatabase] identities={len(self.face_database)} "
        f"embeddings={self.face_database.total_embeddings}"
    )

    print(f"[FaceDatabase] path={cfg.FACE_DATABASE_PATH}")
    for identity, templates in sorted(self.face_database.embeddings.items()):
        print(f"[FaceDatabase] identity={identity} templates={len(templates)}")
    if not len(self.face_database):
        print("[FaceDatabase] EMPTY: run python3 tools/build_face_database.py")



def configure_controls(self):
    self.ptz = create_controller(backend=cfg.PTZ_BACKEND,
        ip=cfg.PTZ_IP,
        username=cfg.PTZ_USERNAME,
        password=cfg.PTZ_PASSWORD,
        speed=cfg.PTZ_SPEED,
        channel=cfg.PTZ_CHANNEL,
        timeout=cfg.PTZ_TIMEOUT,
        port=cfg.PTZ_PORT,
        frame_provider=self.frames.latest,
        focus_model=cfg.FOCUS_MODEL_PATH,
    )

    self.target_state = TargetStateMachine(
        person_target_ratio=(
            cfg.AUTO_ZOOM_PERSON_TARGET_RATIO
        ),
        person_tolerance=(
            cfg.PERSON_RATIO_TOLERANCE
        ),
        person_ready_frames=(
            cfg.PERSON_READY_FRAMES
        ),

        face_target_ratio=(
            cfg.AUTO_ZOOM_FACE_TARGET_RATIO
        ),
        face_tolerance=(
            cfg.FACE_RATIO_TOLERANCE
        ),
        face_ready_frames=(
            cfg.FACE_READY_FRAMES
        ),

        face_acquire_frames=cfg.AUTO_ZOOM_FACE_ACQUIRE_FRAMES,
        face_lost_timeout=cfg.AUTO_ZOOM_FACE_LOST_TIMEOUT,
        face_lost_hard_timeout=cfg.FACE_LOST_HARD_TIMEOUT,
        ratio_filter=cfg.AUTO_ZOOM_RATIO_FILTER,
        ratio_history=cfg.AUTO_ZOOM_RATIO_HISTORY,
        face_min_ratio=cfg.AUTO_ZOOM_FACE_MIN_RATIO,
        face_max_ratio=cfg.AUTO_ZOOM_FACE_MAX_RATIO,

        target_lost_timeout=(
            cfg.TARGET_LOST_TIMEOUT
        ),
    )

    # ====================================================
    # AUTO ZOOM
    # ====================================================

    from camera.zoom_prediction import ZoomCalibration, ZoomEstimator
    calibration = None
    if cfg.ZOOM_CONTROL['calibration_path']:
        if not cfg.ZOOM_CONTROL['camera_id']:
            raise ValueError('zoom_control.camera_id is required for calibration')
        calibration = ZoomCalibration.load(cfg.ZOOM_CONTROL['calibration_path'], cfg.ZOOM_CONTROL['camera_id'])
        self.ptz.expected_camera_id = calibration.camera_id
    self.auto_zoom = AutoZoomController(
        control_mode=cfg.ZOOM_CONTROL['mode'],
        estimator=ZoomEstimator(calibration, cfg.ZOOM_CONTROL['correction_fraction']),
        absolute_timeout=cfg.ZOOM_CONTROL['absolute_timeout'],
        max_corrections=cfg.ZOOM_CONTROL['max_corrections'],
        absolute_settle_timeout=cfg.ZOOM_CONTROL['absolute_settle_timeout'],
        orchestrator=self.orchestrator,
        ptz=self.ptz,

        status_callback=self.set_status,

        zoom_callback=(
            self.apply_auto_zoom_estimate
        ),

        step_medium=cfg.AUTO_ZOOM_LONG_PULSE,
        step_small=cfg.AUTO_ZOOM_FINE_PULSE,
        continuous_error=cfg.AUTO_ZOOM_CONTINUOUS_ERROR,
        medium_error=cfg.AUTO_ZOOM_LONG_ERROR,
        continuous_timeout=cfg.AUTO_ZOOM_CONTINUOUS_TIMEOUT,
        feedback_timeout=cfg.AUTO_ZOOM_FEEDBACK_TIMEOUT,
        max_observation_age=cfg.MAX_CONTROL_OBSERVATION_AGE,
        pulse_recovery_without_telemetry=cfg.PULSE_RECOVERY_WITHOUT_TELEMETRY,
        pulse_recovery_budget=cfg.PULSE_RECOVERY_BUDGET,
        direction_hold=cfg.AUTO_ZOOM_DIRECTION_HOLD,
        direction_cooldown=cfg.AUTO_ZOOM_DIRECTION_COOLDOWN,
        reverse_error=cfg.AUTO_ZOOM_REVERSE_ERROR,
        recovery_delay=cfg.LOST_ZOOM_OUT_DELAY,
        recovery_pulse=cfg.LOST_ZOOM_OUT_STEP,
        recovery_interval=cfg.LOST_ZOOM_OUT_INTERVAL,
        recovery_max_duration=cfg.LOST_ZOOM_OUT_MAX_DURATION,
        min_zoom=cfg.MIN_OPTICAL_ZOOM,
        min_zoom_epsilon=cfg.MIN_ZOOM_EPSILON,
        stop_lead_time=cfg.AUTO_ZOOM_STOP_LEAD_TIME,
        frame_edge_margin=cfg.AUTO_ZOOM_FRAME_EDGE_MARGIN,
        frame_edge_release_margin=cfg.AUTO_ZOOM_FRAME_EDGE_RELEASE_MARGIN,
        short_error=cfg.AUTO_ZOOM_SHORT_ERROR,
        short_pulse=cfg.AUTO_ZOOM_SHORT_PULSE,
        debug=cfg.AUTO_ZOOM_DEBUG,
        debug_interval=cfg.AUTO_ZOOM_DEBUG_INTERVAL,

        wait_after_zoom=(
            cfg.AUTO_ZOOM_SETTLE_TIME
        ),

        allow_zoom_out=True,
    )

    self.auto_zoom.set_enabled(
        cfg.AUTO_ZOOM_DEFAULT
    )

    # ====================================================
    # AUTO FOCUS
    # ====================================================

    self.auto_focus = AutoFocusController(
        orchestrator=self.orchestrator,
        max_attempts=cfg.FOCUS_MAX_ATTEMPTS,
        method=cfg.FOCUS_METHOD,
        ptz=self.ptz,

        status_callback=self.set_status,

        sharpness_threshold=(
            cfg.AUTO_FOCUS_SHARPNESS_THRESHOLD
        ),

        cooldown=(
            cfg.AUTO_FOCUS_COOLDOWN
        ),

        settle_time=(
            cfg.AUTO_FOCUS_SETTLE_TIME
        ),
        min_settle_time=cfg.AUTO_FOCUS_MIN_SETTLE_TIME,
        confirm_frames=cfg.AUTO_FOCUS_CONFIRM_FRAMES,
        max_frame_age=cfg.AUTO_FOCUS_MAX_FRAME_AGE,
    )

    self.auto_focus.set_enabled(
        cfg.AUTO_FOCUS_DEFAULT
    )
    self.ptz.parallel_focus_enabled = lambda: self.auto_focus.enabled

    # ====================================================
    # DIGITAL PTZ
    # ====================================================

    self.digital_ptz = DigitalPTZController(
        enabled=cfg.DIGITAL_PTZ_DEFAULT,

        min_zoom=cfg.DIGITAL_PTZ_MIN_ZOOM,
        max_zoom=cfg.DIGITAL_PTZ_MAX_ZOOM,

        target_height_ratio=(
            cfg.DIGITAL_PTZ_TARGET_HEIGHT_RATIO
        ),

        center_smoothing=(
            cfg.DIGITAL_PTZ_CENTER_SMOOTHING
        ),
        deadzone_x=cfg.DIGITAL_PTZ_DEADZONE_X,
        deadzone_y=cfg.DIGITAL_PTZ_DEADZONE_Y,

        zoom_smoothing=(
            cfg.DIGITAL_PTZ_ZOOM_SMOOTHING
        ),

        lost_hold_time=(
            cfg.DIGITAL_PTZ_LOST_HOLD
        ),

        return_smoothing=(
            cfg.DIGITAL_PTZ_RETURN_SMOOTHING
        ),
    )

    from control.auto_pan_tilt import AutoPanTiltController
    self.auto_pan_tilt = AutoPanTiltController(self.ptz, self.orchestrator, cfg, self.set_status)
