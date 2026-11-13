"""Dahua 37x RPC2, adapted from phanmem_v1.0/phanmem/loi/dieu_khien.py.
Independent runtime module: never imports or writes to the reference folder.
"""
import base64
import os
import hashlib
import json
import random
import threading
import time
import requests
from telemetry import latency
from uuid import uuid4

ZOOM_MAX = 128.0
ZOOM_MIN_RAW = 0.0
ZOOM_MAX_RAW = 1.0
ZOOM_VALUE_MIN = 10.0
ZOOM_VALUE_MAX = 370.0

# Nac nhich cua truc ZOOM, tinh theo BOI SO PHONG DAI chu khong theo mili
# giay. Cung ly do nhu FOCUS_NAC ben duoi: mot xung 100ms nhay bao nhieu x
# phu thuoc dang o dau trong dai 1-37x, nen bam cung mot nut ma ket qua
# khac han. Zoom co lenh tuyet doi (PositionABS) nen di theo BOI SO duoc.
ZOOM_NAC = [0.5, 1.0, 2.0, 5.0]

# Nac nhich cua truc FOCUS, tinh theo QUANG DUONG (dvi PTZFocusHD) chu
# khong theo mili giay. Ly do: van toc truc bien thien 8 lan theo vi tri
# (8.2 dvi/ms o dau xa, 1.0 dvi/ms quanh hd 22000), nen mot xung 150ms di
# 1230 dvi o day va 150 dvi o kia - bam cung mot nut ma nhay khac han.
FOCUS_NAC = [256, 512, 1024, 2048, 4096]

HD_MAX = 32768.0        # PTZFocusHD o dau gan nhat

# HAI nguong khac nhau, truoc day bi gop lam mot va gay loi:
#
# FOCUS_DUNG_SAI - "do duoc chinh xac den dau". Dat theo CUC NHAY LON NHAT
#   cua phep DOC vi tri trong luc chay lien tuc (819 dvi), lam tron len.
#   Ep nho hon la vo nghia: camera chi bao PTZFocusHD ~12.6 lan/giay va moi
#   lan nhay ca cuc. Chi dung de tra loi "co bao la da toi khong".
#
# FOCUS_BUOC_TOI_THIEU - "di duoc ngan den dau". KHAC HAN so tren. Do bang
#   6_do_dac/do_buoc_toi_thieu.py: xin mot buoc ngan roi doc lai quang duong
#   THAT, 6 lan moi cau hinh, 5 vi tri doc thang:
#
#       tai hd    xin 128 ->   xin 256 ->   xin 512 ->
#         1500    163 (1.27x)  326 (1.27x)  489 (0.96x)
#         5000    146 (1.14x)  365 (1.43x)  511 (1.00x)
#        12000    148 (1.16x)  370 (1.45x)  518 (1.01x)
#        22000    145 (1.13x)  261 (1.02x)  522 (1.02x)
#        28000    145 (1.13x)  261 (1.02x)  522 (1.02x)
#
#   KHONG vi tri nao bo qua buoc 128 - ke ca tren 24000 la vung ma phep doc
#   rat tho. Truc bam nac tron ven (~145-163 dvi) nen buoc nho vot len
#   1.13-1.45x; vot co gioi han va biet truoc. Buoc 512 la cho ngot,
#   ti le 0.96-1.02 o moi vi tri.
#
#   Truoc khi tach, chay_toi lay FOCUS_DUNG_SAI lam luon nguong nay va bo
#   qua moi lenh ngan hon 1024 - gap BAY lan san that. Do la ly do mang
#   doan +796 tu hd=0 ma ong kinh khong nhuc nhich: 796 < 1024.
FOCUS_DUNG_SAI = 1024
FOCUS_BUOC_TOI_THIEU = 128

# Do THO cua so bao ve, do bang mot lan chay lien tuc het dai:
#     doan hd        so lan doi   cuc nhay tv   cuc lon nhat
#     0    - 8000        15           546            819
#     8000 - 16000        7           924           1792
#     16000- 24000       12           650            819
#     24000- 32768        3          3834           4096   <-- rat tho
# Tren 24000 camera chi bao vi tri 3 lan cho ca 1/4 dai cuoi. Moi dieu
# khien vong kin qua mang deu mu o do - da bat duoc ca vot 6768 dvi.
FOCUS_VUNG_THO = 24000
STREAM_TEN = {0: "main 4K", 1: "sub D1"}

# Dai zoom quang hoc that: ZoomValue chay 10..370 tuc 1.00x .. 37.00x
ZOOM_X_MIN = 1.0
ZOOM_X_MAX = 37.0


# Chuyen focus sang tay. Gia tri bat duoc tu chinh web local cua camera:
# VideoInFocus profile dau, Mode=4 la tay, AutoFocusTrace=0 de focus khoi bi
# keo theo zoom. Camera nay KHONG co khoa "FocusMode" nhu ban cu tuong.
FOCUS_CFG = "VideoInFocus"
FOCUS_TAY = {"Mode": 4, "AutoFocusTrace": 0}

# Ten truc -> cap lenh ptz nguoc chieu nhau (chieu duong, chieu am)
PTZ_CODE = {
    "zoom":  ("ZoomTele", "ZoomWide"),
    "focus": ("FocusNear", "FocusFar"),
}

# Duong cong (vi tri chuan hoa 0..1  ->  do phong dai x) do duoc trong
# zoom_probe.log. Chi dung khi khong doc duoc ZoomValue truc tiep.
ZOOM_CURVE = [
    (0.000000, 1.0), (0.072815, 1.1), (0.112488, 1.3), (0.152222, 1.4),
    (0.191895, 1.5), (0.231567, 1.7), (0.271301, 1.9), (0.304382, 2.0),
    (0.344116, 2.3), (0.383789, 2.5), (0.423462, 2.9), (0.463196, 3.2),
    (0.502869, 3.6), (0.544250, 4.2), (0.584778, 4.8), (0.620361, 5.4),
    (0.660034, 6.2), (0.703064, 7.3), (0.742737, 8.6), (0.779968, 10.0),
    (0.817200, 11.8), (0.856079, 14.2), (0.892456, 17.2), (0.928894, 21.4),
    (0.957825, 25.6), (0.980164, 30.1), (0.997498, 35.9), (1.000000, 37.0),
]


def _interp(value, table, src, dst):
    """Noi suy tuyen tinh tren ZOOM_CURVE theo cot src -> cot dst."""
    lo, hi = table[0], table[-1]
    if value <= lo[src]:
        return lo[dst]
    if value >= hi[src]:
        return hi[dst]
    for a, b in zip(table, table[1:]):
        if value <= b[src]:
            span = b[src] - a[src]
            if span <= 0:
                return b[dst]
            return a[dst] + (b[dst] - a[dst]) * (value - a[src]) / span
    return hi[dst]


def zoom_x_from_pos(p):
    return _interp(min(1.0, max(0.0, float(p))), ZOOM_CURVE, 0, 1)


def zoom_pos_from_x(x):
    return _interp(min(ZOOM_X_MAX, max(ZOOM_X_MIN, float(x))), ZOOM_CURVE, 1, 0)


class RPCError(RuntimeError):
    def __init__(self, method, response):
        self.method = method
        self.response = {key: response.get(key) for key in ('result', 'error', 'params')}
        super().__init__(f"RPC2 {method} rejected ({(response.get('error') or {}).get('code', 'unknown')})")


class ZoomRPC:
    """Client RPC2 toi thieu - giong het cach web local gui lenh."""

    def __init__(self, host, port, user, password, channel=0, timeout=5.0,
                 session_factory=requests.Session, read_only=False):
        self.read_only = bool(read_only) or os.environ.get('PTZ_BENCHMARK_READ_ONLY') == '1'
        self.channel = int(channel)
        self.timeout = float(timeout)
        base = f"http://{host}:{port}"
        self.url = base + "/RPC2"
        self.login_url = base + "/RPC2_Login"
        self.user = user
        self.password = password

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": base + "/",
        }
        # Hai ket noi rieng: lenh zoom khong xep hang sau request doc trang thai
        self.http = session_factory()
        self.http_poll = session_factory()
        self.http.headers.update(headers)
        self.http_poll.headers.update(headers)

        self._id = 0
        self._seq = random.randint(0, 0x7FFFFF00)
        self._lock = threading.Lock()
        self._http_lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self.session = None
        self.object = None
        self._running_code = {truc: None for truc in PTZ_CODE}
        self._focus_goc = None      # cau hinh focus truoc khi doi sang tay
        self._focus_manual_confirmed = False

        try:
            self._login()
            self._instance()
        except Exception:
            self.http.close()
            self.http_poll.close()
            raise

        self._ka_stop = threading.Event()
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, name='rpc-keepalive', daemon=True)
        if not self.read_only:
            self._keepalive_thread.start()
        else:
            self._keepalive_thread = None

    def _call(self, method, params=None, url=None, obj=None, background=False, allow_false=False):
        if getattr(self, 'read_only', False):
            allowed = {'global.login', 'global.keepAlive', 'ptz.factory.instance',
                       'ptz.getStatus', 'ptz.getCurrentProtocolCaps', 'ptz.isMoving',
                       'ptz.listMethod', 'magicBox.getSystemInfo', 'magicBox.getSoftwareVersion',
                       'configManager.getConfig'}
            if method not in allowed:
                latency.profiler.count('camera_writes_blocked')
                raise RuntimeError('Read-only RPC session rejects writes and motor commands')
        with self._lock:
            self._id += 1
            self._seq += 1
            payload = {"method": method, "params": params, "id": self._id}
            if self.session is not None:
                payload["session"] = self.session
            if obj is not None:
                payload["object"] = obj
            payload["seq"] = self._seq
        http = self.http_poll if background else self.http
        with self._poll_lock if background else self._http_lock:
            code = (params or {}).get('code', '')
            axis = ('ZOOM' if code.startswith('Zoom') or method == 'ptz.moveAbsolutely' or code == 'PositionABS'
                    else 'FOCUS' if code.startswith('Focus') else None)
            motor = axis is not None and method.startswith('ptz.')
            fields = dict(rpc_method=method, rpc_code=code, command_type='STOP' if method=='ptz.stop' else code,
                          decision_reason=latency.context.get().get('decision_reason') or ('ZOOM_OUT_MANUAL' if method=='ptz.start' and code=='ZoomWide' and not latency.context.get().get('lock_session_id') else None),
                          actual_command_method=method+(':'+code if code else ''))
            if code == 'PositionABS' and isinstance((params or {}).get('arg3'), (int, float)):
                fields.update(requested_zoom_position=((params or {})['arg3']-1)/127.,
                              requested_zoom_gui_value=(params or {})['arg3'],
                              command_coordinate='PositionABS_normalized')
            if motor and method != 'ptz.stop':
                if axis == 'ZOOM' and not getattr(self, 'zoom_intent_valid', lambda fields: True)(latency.context.get()):
                    raise RuntimeError('Zoom intent rejected at RPC dispatch')
                if axis == 'ZOOM' and not getattr(self, 'zoom_policy_valid', lambda fields: True)(latency.context.get()):
                    latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason='STALE_PERSON_ZOOM_POLICY')
                    raise RuntimeError('Person framing superseded by face tracking')
                session = latency.context.get().get('lock_session_id')
                validator = getattr(self, 'session_valid', lambda session: True)
                if session and not validator(session):
                    raise RuntimeError('Stale target session; motor command rejected')
            if motor:
                fields['command_id'] = latency.context.get().get('command_id') or 'cmd_'+uuid4().hex
                sent = latency.profiler.event(axis+'_COMMAND_SENT', **fields)
                if code == 'PositionABS' and method == 'ptz.start':
                    latency.profiler.count('absolute_zoom_command_count')
                locked = latency.context.get().get('locked_ns')
                session = latency.context.get().get('lock_session_id')
                if axis == 'ZOOM' and method != 'ptz.stop' and locked and session:
                    if getattr(self, '_latency_zoom_session', None) != session:
                        self._latency_zoom_session = session
                        latency.profiler.elapsed('FIRST_ZOOM_COMMAND_SENT', locked, 'time_to_zoom_command_ms', **fields)
            try:
                r = http.post(url or self.url, data=json.dumps(payload), timeout=self.timeout)
                r.raise_for_status()
                result = r.json()
            except Exception as exc:
                if motor:
                    latency.profiler.event(axis+'_RPC_ERROR', error=type(exc).__name__, **fields)
                raise
            if motor:
                latency.profiler.elapsed(axis+'_RPC_RESPONSE', sent, axis.lower()+'_command_latency_ms',
                                         acknowledged=bool(result.get('result')) if isinstance(result, dict) else False, **fields)
        if not isinstance(result, dict):
            raise RuntimeError("RPC2: invalid response")
        if method != "global.login" and (result.get("error") or
                (not result.get("result") and not (allow_false and result.get("result") is False))):
            raise RPCError(method, result)
        return result

    @staticmethod
    def _md5(text):
        return hashlib.md5(text.encode("utf-8")).hexdigest().upper()

    def _login(self):
        first = self._call(
            "global.login",
            {"userName": self.user, "password": "",
             "clientType": "Web3.0", "loginType": "Direct"},
            url=self.login_url,
        )
        self.session = first.get("session")
        p = first.get("params") or {}
        realm, rnd = p.get("realm"), p.get("random")
        if not realm or not rnd:
            raise RuntimeError("RPC2 login challenge missing")

        if p.get("encryption") == "Basic":
            secret = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        else:
            inner = self._md5(f"{self.user}:{realm}:{self.password}")
            secret = self._md5(f"{self.user}:{rnd}:{inner}")

        second = self._call(
            "global.login",
            {"userName": self.user, "password": secret, "clientType": "Web3.0",
             "loginType": "Direct", "authorityType": "Default",
             "passwordType": "Default"},
            url=self.login_url,
        )
        if not second.get("result"):
            raise RuntimeError("RPC2 authentication rejected")
        self.session = second.get("session", self.session)
        self.http.cookies.set("VSWebClientSessionID", str(self.session))

    def _instance(self):
        r = self._call("ptz.factory.instance", {"channel": self.channel})
        self.object = r.get("result")
        if not self.object:
            raise RuntimeError("RPC2 PTZ object missing")

    def _keepalive_loop(self, interval=60):
        while not self._ka_stop.wait(interval):
            try:
                self._call("global.keepAlive", {"timeout": 300, "active": True})
            except Exception:
                self.connection_failed = True
                try:
                    # Reauthenticate like the reference, but always stop after a
                    # renewed session. Never replay the interrupted movement.
                    with self._http_lock:
                        self._login()
                        self._instance()
                        self.stop_all()
                    self.connection_failed = False
                except Exception:
                    pass

    def close(self, stop_motors=True):
        self._ka_stop.set()
        worker = getattr(self, '_keepalive_thread', None)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self.timeout*3+1.)
        try:
            try:
                if stop_motors and not getattr(self, "read_only", False):
                    self.stop_all()
            finally:
                if stop_motors and not getattr(self, "read_only", False):
                    ok, message = self.focus_manual(False)
                    if not ok:
                        raise RuntimeError(message)
        finally:
            self.http.close()
            self.http_poll.close()

    # -- lenh zoom / focus ---------------------------------------
    def _ptz(self, action, code, speed):
        return self._call(
            f"ptz.{action}",
            {"code": code, "arg1": int(speed), "arg2": 0, "arg3": 0, "arg4": None},
            obj=self.object,
        )

    def start(self, direction, speed, truc="zoom"):
        duong, am = PTZ_CODE[truc]
        code = duong if direction > 0 else am
        # Phai nho code cua TUNG truc: camera chi dung dung dong co ma lenh
        # stop goi ten, nen mot bien chung se lam ket zoom hoac focus.
        self._running_code[truc] = code
        return self._ptz("start", code, speed)

    def stop(self, truc="zoom"):
        code = self._running_code.get(truc) or PTZ_CODE[truc][0]
        result = self._ptz("stop", code, 0)
        self._running_code[truc] = None
        return result

    def stop_all(self):
        failures = []
        for axis in PTZ_CODE:
            try:
                self.stop(axis)
            except Exception:
                failures.append(axis)
        if failures:
            raise RuntimeError("RPC2 STOP unconfirmed: " + ", ".join(failures))

    def pulse(self, direction, speed, ms, truc="zoom"):
        """Chay trong `ms` roi dung.

        GIAO DIEN KHONG CON DUNG HAM NAY. Ca hai truc gio deu di theo QUANG
        DUONG bang vong kin (chay_toi/chay_them cho focus, PositionABS cho
        zoom), vi cung mot so mili giay di duoc quang duong khac han tuy dang
        o dau: focus 8.2 dvi/ms o dau xa nhung 1.0 dvi/ms quanh hd 22000.

        Van giu lai vi bay cong cu do dac dung no de quet tho:
        thu_dataset, leo_doi, quet_focus_dct, luong_anh, sweep_record,
        fast_semi, kiem_vung_hang.
        """
        self.start(direction, speed, truc)
        time.sleep(ms / 1000.0)
        self.stop(truc)

    # -- di chuyen bang BAT/TAT vong kin (thay cho bom xung) ------
    def doc_hd(self):
        """PTZFocusHD, doc nhanh nhat co the - danh cho vong dieu khien.

        Dung ket noi rieng (background) de khong xep hang sau lenh dieu
        khien, va KHONG ngu giay nao: dong co chay ~6000 dvi/giay, moi
        10ms cham tre la ~60 dvi vot qua dich.
        """
        try:
            r = self._call("ptz.getStatus", None, obj=self.object,
                           background=True)
        except Exception:
            return None
        v = ((r.get("params") or {}).get("status") or {}).get("PTZFocusHD")
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    def chay_toi(self, dich, dung_sai=512, timeout=15.0, truc="focus",
                 speed=8, dung_flag=None, t_dung=0.025,
                 buoc_min=FOCUS_BUOC_TOI_THIEU):
        """Chay LIEN TUC toi `dich` (thang PTZFocusHD), DUNG SOM mot khoang.

        Thay cho vong bom xung. Do tren camera that, 6 quang duong
        14000-24000 dvi: bom xung 6.23s/sai 1928, bat-tat 2.44s/sai 679.

        VI SAO PHAI DUNG SOM
        --------------------
        Ghi vet 120 lan doc trong mot lan chay (19ms mot lan, khong lan nao
        hong): so bao ve DUNG IM ~80ms roi NHAY MOT CUC 1792-4096 dvi. Tuc
        vi tri that co the di truoc so bao ve toi mot cuc nhu vay. Cong
        them lenh stop mat ~20ms moi toi noi, dong co van quay trong luc do.

        Da bat mot ca hong: bao 24601 suot 4 lan doc, roi nhay thang len
        28697. Dich 26000 lot giua cu nhay. Vong lap dung dung luc nhung
        ong kinh da o 32768 - vot 6768 dvi.

        Nen dieu kien dung khong phai "da toi dich" ma "SE toi dich":

            lead = cuc nhay lon nhat vua thay + van toc x t_dung

        Ca hai deu do TAI CHO trong chinh lan chay nay, khong dat cung: cuc
        nhay va van toc deu bien thien manh theo vi tri (van toc 8.2 dvi/ms
        o dau xa nhung 1.0 dvi/ms quanh hd 22000).

        `speed` KHONG co tac dung tren truc focus (do 1.5s x 3 lan o hai vi
        tri: ti le speed8/speed1 la 1.00x va 1.03x). Giu lai cho dong bo
        chu ky voi truc zoom.

        GIOI HAN: tren hd 24000 camera chi bao vi tri 3 lan cho ca 1/4 dai
        cuoi, moi cuc 4096 dvi (xem FOCUS_VUNG_THO). Dich nam trong vung do
        thi sai so co the toi 6768 - da do duoc. Ham tra ve co 'vung_tho'
        de nguoi goi biet ma khong tin con so.
        """
        t0 = time.perf_counter()
        chieu = 0
        v_truoc, t_truoc = None, None
        cuc, van_toc = 0.0, 0.0
        try:
            while time.perf_counter() - t0 < timeout:
                if dung_flag is not None and dung_flag.is_set():
                    break
                v = self.doc_hd()
                if v is None:
                    continue
                t = time.perf_counter()
                if v_truoc is not None and v != v_truoc:
                    d_v = abs(v - v_truoc)
                    d_t = max(1e-3, t - t_truoc)
                    # Cuc nhay: nho cai lon nhat vua thay, nhung quen dan de
                    # khoi giu mai mot cuc lon o doan truoc.
                    cuc = max(d_v, cuc * 0.7)
                    van_toc = d_v / d_t
                if v != v_truoc:
                    v_truoc, t_truoc = v, t
                # CHI tru quang di trong luc lenh stop bay toi. KHONG tru
                # them cuc nhay: so camera bao la mau DUNG tai thoi diem do,
                # khong phai gia tri cu - tru ca hai la tinh hai lan, do
                # duoc thanh hut 1927-2534 dvi.
                lead = van_toc * t_dung
                if chieu > 0 and v + lead >= dich:
                    break
                if chieu < 0 and v - lead <= dich:
                    break
                # Bo qua lenh CHI KHI ngan hon cai truc di noi. Truoc day
                # cho nay dung dung_sai (1024) nen nuot ca nhung buoc 200-900
                # dvi ma truc thua suc di - xem FOCUS_BUOC_TOI_THIEU.
                if chieu == 0 and abs(v - dich) < buoc_min:
                    break
                d = 1 if dich > v else -1
                if d != chieu:
                    self.start(d, speed, truc)
                    chieu = d
        finally:
            self.stop(truc)
        time.sleep(0.25)
        v = self.doc_hd()
        return {"hd": v, "xin": float(dich), "giay": time.perf_counter() - t0,
                "sai": None if v is None else abs(v - dich),
                "den_noi": v is not None and abs(v - dich) <= dung_sai,
                "vung_tho": float(dich) >= FOCUS_VUNG_THO}

    def chay_them(self, delta, **kw):
        """Nhich mot quang `delta` dvi ke tu cho dang dung.

        Thay cho cach cu la "chay trong N mili giay": cung mot xung 150ms
        di 1230 dvi o dau xa nhung chi 150 dvi quanh hd 22000 (do duoc,
        van toc bien thien 8.2 xuong 1.0 dvi/ms). Di theo QUANG DUONG thi
        moi nac bam nut deu nhau o moi vi tri.
        """
        v = self.doc_hd()
        if v is None:
            return {"hd": None, "sai": None, "giay": 0.0, "den_noi": False}
        dich = min(32768.0, max(0.0, v + float(delta)))
        return self.chay_toi(dich, **kw)

    def goto(self, position, speed=1):
        import math
        if not isinstance(position, (int, float)) or not math.isfinite(position) or not 0 <= position <= 1:
            raise ValueError('PositionABS normalized coordinate outside 0..1')
        self._running_code["zoom"] = "ZoomTele"  # reference STOP convention for ABS
        value = min(int(ZOOM_MAX), max(1, int(round(position * (ZOOM_MAX - 1))) + 1))
        return self._call(
            "ptz.start",
            {"code": "PositionABS", "arg1": 0, "arg2": 0, "arg3": value,
             "arg4": None},
            obj=self.object,
        )

    def protocol_caps(self):
        return self._call('ptz.getCurrentProtocolCaps', {'channel': self.channel}, obj=self.object)

    def motion_status(self):
        return self._call('ptz.isMoving', {'channel': self.channel}, obj=self.object, allow_false=True)

    def status_raw(self, background=False):
        return self._call("ptz.getStatus", None, obj=self.object,
                          background=background)

    @staticmethod
    def _norm(value, lo, hi):
        p = (float(value) - lo) / (hi - lo)
        return min(1.0, max(0.0, p))

    def state(self):
        """Tra ve (vi tri zoom 0..1, do phong dai x, vi tri focus 0..1).

        Mot lan goi RPC duy nhat cho ca ba.
        """
        r = self.status_raw(background=True)
        status = (r.get("params") or {}).get("status") or {}

        pos = None
        # "Postion" la loi chinh ta co that trong firmware
        for path in (("params", "status", "Postion"),
                     ("params", "status", "Position"),
                     ("params", "Postion"),
                     ("params", "Position")):
            node = r
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, (list, tuple)) and len(node) >= 3:
                try:
                    pos = self._norm(node[2], ZOOM_MIN_RAW, ZOOM_MAX_RAW)
                    break
                except (TypeError, ValueError):
                    pass

        zoom_x = None
        for key in ("ZoomValue", "Zoom", "zoom"):
            if key in status:
                try:
                    # ZoomValue = do phong dai nhan 10 (10 = 1.00x, 370 = 37x)
                    zoom_x = min(ZOOM_X_MAX,
                                 max(ZOOM_X_MIN, float(status[key]) / 10.0))
                    break
                except (TypeError, ValueError):
                    pass

        foc = None
        if "FocusPosition" in status:
            try:
                foc = self._norm(status["FocusPosition"], 0.0, 1.0)
            except (TypeError, ValueError):
                pass

        if pos is None and zoom_x is None:
            return None, None, foc
        if pos is None:
            pos = zoom_pos_from_x(zoom_x)
        elif zoom_x is None:
            zoom_x = zoom_x_from_pos(pos)
        return pos, zoom_x, foc

    def position(self):
        return self.state()[0]

    # -- che do focus --------------------------------------------
    def _focus_table(self):
        r = self._call("configManager.getConfig", {"name": FOCUS_CFG})
        if not r.get("result"):
            raise RuntimeError(json.dumps(r.get("error")))
        bang = (r.get("params") or {}).get("table")
        if bang is None:
            raise RuntimeError("khong co truong 'table'")
        return bang

    @staticmethod
    def _profile_dau(bang):
        """Web local chi sua profile dau (kenh 0, profile 0) - lam y het."""
        node = bang
        while isinstance(node, list) and node and isinstance(node[0], list):
            node = node[0]
        if isinstance(node, list) and node and isinstance(node[0], dict):
            return node[0]
        return None

    def _set_focus_table(self, bang):
        # setTemporaryConfig: giong het web local, khong ghi vao flash
        return self._call("configManager.setTemporaryConfig",
                          {"name": FOCUS_CFG, "table": bang, "options": []})

    def focus_manual(self, bat):
        """Bat/tat focus tay. Tra ve (thanh_cong, mo_ta)."""
        try:
            if bat:
                if self._focus_manual_confirmed:
                    return True, "dang o che do tay"
                bang = self._focus_table()
                p = self._profile_dau(bang)
                if p is None:
                    return False, "cau hinh focus la khuon la"
                original = {k: p[k] for k in FOCUS_TAY if k in p}
                if len(original) != len(FOCUS_TAY):
                    return False, f"khong co khoa {list(FOCUS_TAY)}"
                # Keep the original for restoration even if the ACK is lost,
                # but never mistake an unsuccessful write for manual mode.
                if self._focus_goc is None:
                    self._focus_goc = original
                p.update({k: v for k, v in FOCUS_TAY.items() if k in p})
                r = self._set_focus_table(bang)
                if not r.get("result"):
                    return False, json.dumps(r.get("error"))
                self._focus_manual_confirmed = True
                return True, "focus tay"

            if self._focus_goc is None:
                return True, "dang o che do tu dong"
            bang = self._focus_table()
            p = self._profile_dau(bang)
            if p is None:
                return False, "cau hinh focus la khuon la"
            p.update(self._focus_goc)
            r = self._set_focus_table(bang)
            if not r.get("result"):
                return False, json.dumps(r.get("error"))
            self._focus_goc = None
            self._focus_manual_confirmed = False
            return True, "focus tu dong"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
