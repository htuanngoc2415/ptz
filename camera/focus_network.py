"""Delta-focus architecture copied from the 37x reference (inference only)."""
import torch
from torch import nn
from torch.nn import functional as F

def roi_tam(w, h, canh=0.40):
    """Chep y het cach thu_dataset.sinh_roi(o_tam=True) cat vung giua.

    Phai trung tung pixel, khong duoc lam tron kieu khac: mang hoc tren
    dung o cua so nay. Lech di vai phan tram la doi ca ti le nhoe/khung -
    thu duy nhat mang co de doc.
    """
    canh_px = max(64, int(round(canh * min(w, h))))
    canh_px -= canh_px % 8
    x0, y0 = (w - canh_px) // 2, (h - canh_px) // 2
    return (x0 / w, y0 / h, (x0 + canh_px) / w, (y0 + canh_px) / h)

class Laplacian(nn.Module):
    """Dao ham bac hai, he so CO DINH - khong hoc.

    Ep mang nhin vao vien ngay tu dau vao. Nhan 4 lang gieng la ban kinh
    nho nhat, tuc bam sat tan so cao nhat ma anh con giu - dung dai ma do
    nhoe lam suy giam truoc tien.

    Giu lai ca kenh goc: bo han di thi mat luon manh moi QUANG SAI MAU DOC
    (vien mo truoc tieu diem nga tim/do, sau tieu diem nga xanh luc) - thu
    duy nhat co the pha duoc nhap nhang dau.
    """

    def __init__(self, giu_goc=True):
        super().__init__()
        k = torch.tensor([[0., 1., 0.], [1., -4., 1.], [0., 1., 0.]])
        self.register_buffer("nhan", k.view(1, 1, 3, 3))
        self.giu_goc = giu_goc

    def forward(self, x):
        c = x.shape[1]
        lap = F.conv2d(x, self.nhan.expand(c, 1, 3, 3), padding=1, groups=c)
        return torch.cat([x, lap], 1) if self.giu_goc else lap

class TrichXuatNong(nn.Module):
    """4 lop conv, KHONG max-pool. Thu nho bang stride.

    Max-pool lay gia tri LON NHAT trong cua so - no giu lai dinh canh
    nhung vut bo phan con lai, ma hinh dang suon canh moi la thu phan
    biet net voi mo. Stride giu duoc quan he lang gieng hon.

    Ket thuc bang gop TRUNG BINH va DO LECH CHUAN tren toan anh: do sac
    net la tinh chat toan cuc cua manh anh, khong phu thuoc vien o dau.
    Do lech chuan them thong tin ma trung binh khong co - anh net co dap
    ung canh tap trung o it cho, anh mo thi dan deu.
    """

    def __init__(self, kenh_vao=3, rong=(16, 32, 64, 64), lap=True):
        super().__init__()
        self.lap = Laplacian() if lap else None
        c0 = kenh_vao * 2 if lap else kenh_vao
        lop, truoc = [], c0
        for i, r in enumerate(rong):
            lop += [nn.Conv2d(truoc, r, 3, stride=1 if i == 0 else 2,
                              padding=1, bias=False),
                    nn.BatchNorm2d(r), nn.ReLU(inplace=True)]
            truoc = r
        self.than = nn.Sequential(*lop)
        self.so_dac_trung = truoc * 2          # trung binh + do lech chuan

    def forward(self, x):
        if self.lap is not None:
            x = self.lap(x)
        h = self.than(x)
        return torch.cat([h.mean((2, 3)), h.std((2, 3))], 1)

class MangDelta(nn.Module):
    """MOT anh + [zoom, z_current] -> delta_z can dich chuyen.

    Dau ra la mot so thuc KHONG CHAN: lop cuoi la Linear tran, khong tanh
    khong sigmoid khong clamp. Co y: delta mang DAU (am = lui ve phia xa,
    duong = tien ve phia gan) va do lon cua no la thu phai hoc, ep vao
    mot khoang cho truoc se lam bao hoa gradient ngay o nhung mau lech
    nhieu nhat - dung nhung mau quan trong nhat.

    Nhan duoc chia cho Z_MAX truoc khi hoc, nhung do chi la doi don vi
    cho gradient de chiu; no khong dat tran cho dau ra. Mang hoan toan co
    the xuat |delta| > Z_MAX, va viec chan lai vao [0, Z_MAX] la chuyen
    cua LUC DUNG (xem danh_gia: chi so 'chan'), khong phai cua mang.
    """

    def __init__(self, meta_chieu=2, rong=(16, 32, 64, 64), lap=True, an=64):
        super().__init__()
        self.trich = TrichXuatNong(3, rong, lap)
        d = self.trich.so_dac_trung
        self.meta = nn.Sequential(nn.Linear(meta_chieu, 16), nn.ReLU(True))
        self.dau = nn.Sequential(
            nn.Linear(d + 16, an), nn.ReLU(True), nn.Linear(an, 1))
        # Ablation (danh gia): xoa han mot nhanh.
        self.bo_anh = self.bo_meta = False
        # NGAT NGAU NHIEN (huan luyen): xac suat xoa mot nhanh, TUNG MAU.
        #
        #   che_meta > 0  ep mang doc ANH. Nhanh meta cho loi giai de
        #     ("hang_so - z_current") nen bo toi uu di duong do truoc va
        #     nhanh anh bi bo doi gradient. Meta vang mat thi khong con
        #     duong tat, va dau hop nhat buoc phai HOC CACH DUNG dac trung
        #     anh - dieu ma phat auxiliary khong ep duoc.
        #   che_anh > 0   ep nhanh meta tu du. Huong NGUOC lai; de doi
        #     chung chu khong phai de cai thien.
        #
        # Ngat theo TUNG MAU chu khong tung lo: ngat ca lo thi mot buoc
        # gradient chi thay mot che do, phuong sai cao han.
        self.che_meta = 0.0
        self.che_anh = 0.0

    def forward(self, a, meta):
        f = self.trich(a)
        if self.bo_anh:                       # ablation: xoa han nhanh anh
            f = torch.zeros_like(f)
        if self.bo_meta:                      # ablation: xoa han nhanh meta
            meta = torch.zeros_like(meta)
        if self.training:
            # KHONG chia lai cho (1-p) kieu dropout thuong: o day "vang
            # mat" la mot dau vao HOP LE ma mang phai xu ly duoc, khong
            # phai nhieu can bu lai ky vong.
            if self.che_meta > 0:
                m = (torch.rand(meta.shape[0], 1, device=meta.device)
                     >= self.che_meta).to(meta.dtype)
                meta = meta * m
            if self.che_anh > 0:
                m = (torch.rand(f.shape[0], 1, device=f.device)
                     >= self.che_anh).to(f.dtype)
                f = f * m
        return self.dau(torch.cat([f, self.meta(meta)], 1))
