"""Manual identity labeling UI; no model/camera/controller and no auto-labels."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.identity_dataset import AnnotationStore, verified_image


class LabelWindow:
    def __init__(self, root, directory):
        import tkinter as tk
        from tkinter import ttk
        from PIL import Image, ImageTk
        self.root, self.store = root, AnnotationStore(directory)
        self.index = 0
        self.identities = {}
        self.Image, self.ImageTk = Image, ImageTk
        root.title('Gán nhãn identity — xác nhận thủ công từng frame')
        root.geometry('1300x850')
        top = ttk.Frame(root); top.pack(fill='x')
        self.info = tk.StringVar()
        ttk.Label(top,textvariable=self.info).pack(side='left')
        ttk.Label(top,text='Box/track của AI KHÔNG phải nhãn thật').pack(side='right')
        body = ttk.Frame(root); body.pack(fill='both',expand=True)
        self.canvas = tk.Canvas(body,width=1000,height=650,bg='#202020')
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.bind('<Button-1>',self.click)
        panel = ttk.Frame(body,padding=8); panel.pack(side='right',fill='y')
        reference = Image.open(self.store.directory/'target_reference.png')
        reference.thumbnail((180,180))
        self.reference_image = ImageTk.PhotoImage(reference)
        ttk.Label(panel,text='Ảnh tham chiếu target').pack()
        ttk.Label(panel,image=self.reference_image).pack()
        ttk.Label(panel,text='Chọn box trong ảnh hoặc danh sách:').pack()
        self.listbox = tk.Listbox(panel,width=34,height=9,exportselection=False)
        self.listbox.pack(fill='x')
        self.identity = tk.StringVar(value='target')
        ttk.Label(panel,text='Identity ổn định do bạn đặt:').pack()
        ttk.Combobox(panel,textvariable=self.identity,values=('target','person_2','person_3','person_4')).pack(fill='x')
        ttk.Button(panel,text='Gán identity cho box đã chọn',command=self.assign).pack(fill='x')
        self.visible = tk.StringVar(value='')
        ttk.Radiobutton(panel,text='Target có xuất hiện',variable=self.visible,value='yes').pack(anchor='w')
        ttk.Radiobutton(panel,text='Target không xuất hiện',variable=self.visible,value='no').pack(anchor='w')
        self.occluded = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel,text='Target bị che khuất',variable=self.occluded).pack(anchor='w')
        ttk.Label(panel,text='Giữ cùng tên người qua các frame.\nKhông lấy track_id làm identity.\nCó thể có target nhưng detector bỏ sót.',wraplength=260).pack(pady=8)
        buttons = ttk.Frame(root); buttons.pack(fill='x')
        ttk.Button(buttons,text='← Trước',command=lambda:self.navigate(-1)).pack(side='left')
        ttk.Button(buttons,text='Bỏ qua / Sau →',command=lambda:self.navigate(1)).pack(side='left')
        ttk.Button(buttons,text='Xác nhận nhãn & tiếp →',command=self.confirm).pack(side='left')
        self.message = tk.StringVar(value='Chỉ frame được xác nhận mới lưu thành ground truth.')
        ttk.Label(root,textvariable=self.message).pack(fill='x')
        self.show()

    def show(self):
        row = self.store.rows[self.index]
        self.row = row
        saved = self.store.labels.get(row['frame_id'],{})
        self.identities = dict(saved.get('candidate_identities',{}))
        self.visible.set('yes' if saved.get('target_visible') is True else 'no' if saved.get('target_visible') is False else '')
        self.occluded.set(saved.get('target_occluded',False))
        image = self.Image.open(verified_image(self.store.directory,row))
        self.scale = min(1000/image.width,650/image.height,1.)
        image = image.resize((round(image.width*self.scale),round(image.height*self.scale)))
        self.photo = self.ImageTk.PhotoImage(image)
        self.canvas.delete('all')
        self.canvas.create_image(0,0,image=self.photo,anchor='nw')
        for c in row['candidates']:
            box = [v*self.scale for v in c['bbox']]
            self.canvas.create_rectangle(*box,outline='#ffdf00',width=2)
            self.canvas.create_text(box[0],max(12,box[1]),text=c['candidate_id'],anchor='sw',fill='#ffdf00')
        self.refresh_list()
        self.info.set(f"Frame {self.index+1}/{len(self.store.rows)} | id={row['frame_id']} | t={row['timestamp']:.3f} | reviewed={len(self.store.labels)}")

    def refresh_list(self):
        self.listbox.delete(0,'end')
        for c in self.row['candidates']:
            self.listbox.insert('end',f"{c['candidate_id']} → {self.identities.get(c['candidate_id'],'CHƯA GÁN')}")

    def click(self,event):
        x,y = event.x/self.scale,event.y/self.scale
        hits = [(i,c) for i,c in enumerate(self.row['candidates']) if c['bbox'][0]<=x<=c['bbox'][2] and c['bbox'][1]<=y<=c['bbox'][3]]
        if hits:
            i,_ = min(hits,key=lambda p:(p[1]['bbox'][2]-p[1]['bbox'][0])*(p[1]['bbox'][3]-p[1]['bbox'][1]))
            self.listbox.selection_clear(0,'end'); self.listbox.selection_set(i)

    def assign(self):
        selected = self.listbox.curselection()
        value = self.identity.get().strip()
        if not selected or not value:
            self.message.set('Chọn một box và nhập tên identity trước.')
            return
        cid = self.row['candidates'][selected[0]]['candidate_id']
        self.identities[cid] = value
        self.refresh_list()
        self.message.set(f'Đã gán {cid} → {value}; chưa lưu tới khi Xác nhận.')

    def navigate(self, direction):
        self.index = max(0,min(len(self.store.rows)-1,self.index+direction))
        self.show()

    def confirm(self):
        try:
            visible = {'yes':True,'no':False}.get(self.visible.get())
            self.store.confirm(self.index,self.identities,visible,self.occluded.get())
            self.message.set('Đã lưu annotations.json. Frame chưa xác nhận không được tính là GT.')
            self.navigate(1)
        except (ValueError,OSError) as exc:
            self.message.set(str(exc))


def main():
    import tkinter as tk
    from tkinter import messagebox
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    args = parser.parse_args()
    root = tk.Tk()
    try:
        LabelWindow(root,args.capture)
    except (OSError,ValueError,KeyError) as exc:
        messagebox.showerror('Không mở được bản ghi',str(exc),parent=root)
        root.destroy()
        raise SystemExit(1)
    root.mainloop()


if __name__ == '__main__':
    main()
