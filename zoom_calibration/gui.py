"""Tkinter GUI consistent with existing desktop; all IO runs outside Tk."""
import copy
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from .config import camera_settings, resolve, validate
from .control import Motor, RPCAdapter
from .engine import Calibration
from .pipeline import Pipeline
from .session import Session
from .analysis import analyze

class App:
    def __init__(self,root,cfg):
        self.root,self.cfg = root,cfg
        self.busy = False
        self.closing = False
        self.motor = None
        self.session = None
        self.pipeline = None
        self.engine = None
        self.events = __import__('queue').Queue()
        self.display_packet = None
        root.title('Zoom Calibration & Profiling Tool')
        root.geometry('1280x850')
        root.protocol('WM_DELETE_WINDOW',self.close)
        toolbar = ttk.Frame(root); toolbar.pack(fill='x')
        for title,action in [('Connect RTSP + Camera',self.connect),('Replay video (no motor)',self.replay),
                             ('Analyze saved session',self.offline),('Load profile',self.load_profile),
                             ('Settings',self.settings),('Reset Session',self.reset)]:
            ttk.Button(toolbar,text=title,command=lambda a=action:self.call(a)).pack(side='left',padx=3,pady=5)
        pane = ttk.Panedwindow(root,orient='horizontal'); pane.pack(fill='both',expand=True)
        left = ttk.Frame(pane); right_outer = ttk.Frame(pane,width=360)
        pane.add(left,weight=4); pane.add(right_outer,weight=1)
        controls = tk.Canvas(right_outer,width=360,highlightthickness=0)
        scrollbar = ttk.Scrollbar(right_outer,orient='vertical',command=controls.yview)
        controls.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y'); controls.pack(fill='both',expand=True)
        right = ttk.Frame(controls)
        window = controls.create_window((0,0),window=right,anchor='nw')
        right.bind('<Configure>',lambda e:controls.configure(scrollregion=controls.bbox('all')))
        controls.bind('<Configure>',lambda e:controls.itemconfigure(window,width=e.width))
        controls.bind('<Button-4>',lambda e:controls.yview_scroll(-3,'units'))
        controls.bind('<Button-5>',lambda e:controls.yview_scroll(3,'units'))
        self.view = tk.Canvas(left,bg='#151c24',highlightthickness=0,height=440)
        self.view.pack(fill='both',expand=True)
        self.view.bind('<Button-1>',self.click)
        self.charts_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(left,text='Realtime charts',variable=self.charts_on).pack(anchor='w')
        self.chart = tk.Canvas(left,bg='#fafafa',height=240,highlightthickness=0); self.chart.pack(fill='x')
        self.metrics = tk.StringVar(value='Offline ready. Connect or load a session.')
        ttk.Label(right,textvariable=self.metrics,justify='left',wraplength=330).pack(anchor='w',padx=8,pady=10)
        ttk.Label(right,text='Manual bounded pulses').pack(anchor='w',padx=8)
        self.pulse_ms = tk.StringVar(value=str(cfg['pulse_ms']))
        ttk.Combobox(right,textvariable=self.pulse_ms,values=cfg['pulse_options'],width=12).pack(anchor='w',padx=8)
        for title,d in [('Zoom In','zoom_in'),('Zoom Out','zoom_out')]:
            ttk.Button(right,text=title,command=lambda d=d:self.call(lambda:self.manual(d))).pack(fill='x',padx=8,pady=3)
        ttk.Button(right,text='Zoom Stop / Cancel',command=lambda:self.stop('USER_STOP')).pack(fill='x',padx=8,pady=3)
        tk.Button(right,text='EMERGENCY STOP',bg='#a91526',fg='white',command=lambda:self.stop('EMERGENCY_STOP')).pack(fill='x',padx=8,pady=5)
        for title,action in [('Start Recording',self.record_start),('Stop Recording',self.record_stop),('Finish Session + Report',self.finish)]:
            ttk.Button(right,text=title,command=lambda a=action:self.call(a)).pack(fill='x',padx=8,pady=3)
        ttk.Separator(right).pack(fill='x',pady=7)
        self.mode = tk.StringVar(value='Automatic Zoom Sweep')
        ttk.Combobox(right,textvariable=self.mode,state='readonly',width=30,
                     values=['Manual Profiling','Automatic Zoom Sweep','Pulse Response Test','Bidirectional Zoom Test','Target Ratio Test','Offline Analysis']).pack(fill='x',padx=8)
        self.direction = tk.StringVar(value='zoom_in')
        ttk.Combobox(right,textvariable=self.direction,state='readonly',values=['zoom_in','zoom_out']).pack(fill='x',padx=8,pady=3)
        experiment = ttk.Frame(right); experiment.pack(fill='x',padx=8,pady=4)
        ttk.Label(experiment,text='Region').grid(row=0,column=0,sticky='w')
        self.region = tk.StringVar(value=str(cfg['region']))
        ttk.Combobox(experiment,textvariable=self.region,values=['A','B','C','D','E'],width=15).grid(row=0,column=1)
        ttk.Label(experiment,text='Target ratio').grid(row=1,column=0,sticky='w')
        self.target_ratio = tk.StringVar(value=str(cfg['target_ratio']))
        ttk.Combobox(experiment,textvariable=self.target_ratio,values=[.30,.40,.50,.60],width=15).grid(row=1,column=1)
        self.strategy = tk.StringVar(value=cfg['strategy'])
        ttk.Combobox(right,textvariable=self.strategy,state='readonly',values=['fixed','coarse_to_fine','adaptive']).pack(fill='x',padx=8,pady=3)
        self.confirm = tk.BooleanVar(value=False)
        ttk.Checkbutton(right,text='I confirm stationary full-body target\nand current initial baseline',variable=self.confirm).pack(anchor='w',padx=8,pady=4)
        ttk.Button(right,text='Start test at current baseline',command=lambda:self.call(self.start_test)).pack(fill='x',padx=8,pady=4)
        ttk.Label(right,text='After a tracking failure: STOP, click the same\nperson again, then explicitly restart.\nDo not run the production camera app\nat the same time.',wraplength=330).pack(anchor='w',padx=8,pady=8)
        self.status = tk.StringVar(value='Ready — no camera commands sent')
        ttk.Label(root,textvariable=self.status).pack(fill='x',padx=8,pady=4)
        root.after(80,self.refresh)

    def call(self,action):
        try:
            if self.busy:
                raise RuntimeError('Session is being finalized; STOP remains available')
            action()
        except Exception as exc:
            messagebox.showerror('Zoom Calibration',str(exc))
    def create_session(self):
        if self.session:
            return
        self.session = Session(resolve(self.cfg['session_root']),self.cfg)
        self.pipeline = Pipeline(self.cfg,self.session)
        self.engine = Calibration(self.cfg,self.session,None,self.pipeline.observations)
        self.pipeline.engine = self.engine
        self.status.set('Session: '+str(self.session.path))
    def connect(self):
        if self.pipeline and self.pipeline.capture:
            raise RuntimeError('Reset session before opening another stream')
        camera = camera_settings(self.cfg)
        self.create_session()
        self.motor = Motor(lambda:RPCAdapter(camera,self.cfg),self.cfg,self.session)
        self.engine.motor = self.motor
        self.pipeline.motor = self.motor
        self.pipeline.connect(camera['uri'],camera=camera)
        self.confirm.set(False)
    def replay(self):
        if self.motor:
            raise RuntimeError('Finish/reset connected session before offline replay')
        path = filedialog.askopenfilename(title='Replay video — never sends camera commands',filetypes=[('Video','*.mp4 *.avi *.mkv'),('All','*')])
        if path:
            self.create_session()
            self.pipeline.connect(path,offline=True)
    def click(self,event):
        p = self.display_packet
        if not p or not self.pipeline or self.busy:
            return
        if self.engine.thread and self.engine.thread.is_alive():
            self.stop('TARGET_SELECTION_CHANGED')
            return
        scale,ox,oy = self.view_transform
        x,y = (event.x-ox)/scale,(event.y-oy)/scale
        matches = [i for i,d in enumerate(p['detections']) if d['bbox'][0]<=x<=d['bbox'][2] and d['bbox'][1]<=y<=d['bbox'][3]]
        if len(matches)!=1:
            self.status.set('Select a single unambiguous person bbox')
            return
        self.stop('TARGET_CONFIRMATION')
        if self.pipeline.select(matches[0],p['frame_id']):
            self.confirm.set(False)
            self.status.set('Target selected; confirm stationary full-body baseline before testing')
    def manual(self,direction):
        if not self.motor or self.motor.fault:
            raise RuntimeError('Connect camera and obtain acknowledged STOP first')
        if self.engine.thread and self.engine.thread.is_alive():
            raise RuntimeError('Cancel automatic test before manual control')
        ms = float(self.pulse_ms.get())
        # Manual movement also respects frame/target safety; no blind zoom-in.
        self.engine.start('manual',direction,ms)
    def stop(self,reason):
        if self.engine:
            self.engine.cancel(reason)
        elif self.motor:
            self.motor.stop(reason)
        self.status.set(reason+' — STOP requested')
    def start_test(self):
        mode = {'Manual Profiling':'manual','Automatic Zoom Sweep':'sweep','Pulse Response Test':'pulse',
                'Bidirectional Zoom Test':'bidirectional','Target Ratio Test':'target','Offline Analysis':'offline'}[self.mode.get()]
        if mode=='offline':
            self.offline(); return
        if not self.engine:
            raise RuntimeError('Connect and select a person first')
        if not self.confirm.get():
            raise RuntimeError('Confirm stationary full-body target and current baseline')
        if self.engine.thread and self.engine.thread.is_alive():
            raise RuntimeError('Calibration already running')
        updated = dict(self.cfg,region=self.region.get(),target_ratio=float(self.target_ratio.get()),strategy=self.strategy.get())
        validate(updated)
        self.cfg.update(updated)
        if mode=='manual':
            self.status.set('Manual profiling ready — use bounded Zoom In/Out pulses and Start Recording')
            return
        self.engine.start(mode,self.direction.get(),float(self.pulse_ms.get()))
        self.confirm.set(False)
    def record_start(self):
        if not self.pipeline:
            raise RuntimeError('Connect or replay first')
        self.pipeline.recorder.start()
    def record_stop(self):
        if self.pipeline:
            self.pipeline.recorder.stop()
    def settings(self):
        if self.session:
            raise RuntimeError('Finish/reset session before changing configuration')
        dialog = tk.Toplevel(self.root); dialog.title('Configuration — saved YAML')
        canvas = tk.Canvas(dialog,width=680,height=580)
        scroll = ttk.Scrollbar(dialog,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y'); canvas.pack(fill='both',expand=True)
        form = ttk.Frame(canvas); canvas.create_window((0,0),window=form,anchor='nw')
        form.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        import yaml
        entries = {}
        for row,(key,value) in enumerate(self.cfg.items()):
            ttk.Label(form,text=key).grid(row=row,column=0,sticky='w',padx=8,pady=3)
            entry = ttk.Entry(form,width=49)
            entry.insert(0,yaml.safe_dump(value,default_flow_style=True).replace('\n...','').strip())
            entry.grid(row=row,column=1,padx=8,pady=3); entries[key] = entry
        def save():
            try:
                new = {k:yaml.safe_load(v.get()) for k,v in entries.items()}
                validate(new)
                path = filedialog.asksaveasfilename(parent=dialog,initialdir=str(Path(__file__).parent/'configs'),defaultextension='.yaml',initialfile='local.yaml')
                if not path:
                    return
                Path(path).write_text(yaml.safe_dump(new,sort_keys=False))
                self.cfg = new
                self.pulse_ms.set(str(new['pulse_ms']))
                self.region.set(str(new['region']))
                self.target_ratio.set(str(new['target_ratio']))
                self.strategy.set(new['strategy'])
                dialog.destroy()
                self.status.set('Configuration saved: '+path)
            except Exception as exc:
                messagebox.showerror('Configuration',str(exc),parent=dialog)
        ttk.Button(dialog,text='Validate, save and apply',command=save).pack(pady=7)
    def load_profile(self):
        if not self.engine:
            raise RuntimeError('Create connected session first')
        path = filedialog.askopenfilename(title='Load empirical zoom_profile.json',filetypes=[('JSON','*.json')])
        if path:
            profile = json.loads(Path(path).read_text())
            if profile.get('profile_version')!=1 or profile.get('camera_identifier')!=self.cfg['camera_identifier']:
                raise ValueError('Profile camera identifier/version mismatch')
            self.engine.profile = profile
            self.session.event('PROFILE_LOADED',path)
    def offline(self):
        path = filedialog.askdirectory(title='Select session containing data/session.json')
        if path:
            self.background(lambda:analyze(path),'Analysis complete')
    def background(self,action,title,on_done=None):
        self.busy = True
        self.status.set('Working: '+title)
        def run():
            try:
                result = action()
                self.events.put((title,str(result or ''),on_done))
            except Exception as exc:
                self.events.put(('ERROR',str(exc),None))
        threading.Thread(target=run,name='offline-analysis',daemon=True).start()
    def finalize(self):
        session,pipeline,engine,motor = self.session,self.pipeline,self.engine,self.motor
        if engine:
            engine.close()
        if motor:
            motor.close()
        if pipeline:
            pipeline.close()
        if session:
            session.close()
            return analyze(session.path)
    def clear(self):
        self.session = self.pipeline = self.engine = self.motor = None
        self.display_packet = None
        self.view.delete('all')
        self.confirm.set(False)
    def finish(self):
        if self.session:
            self.stop('SESSION_FINISH')
            self.background(self.finalize,'Session report complete',self.clear)
    def reset(self):
        if self.session:
            self.finish()
        else:
            self.clear()
    def close(self):
        if self.closing:
            return
        self.closing = True
        self.stop('WINDOW_CLOSE')
        if self.busy:
            self.root.after(100,self._close_when_ready)
        else:
            self.background(self.finalize,'Shutdown complete',self.root.destroy)
    def _close_when_ready(self):
        if self.busy:
            self.root.after(100,self._close_when_ready)
        else:
            self.background(self.finalize,'Shutdown complete',self.root.destroy)
    def refresh(self):
        try:
            while True:
                title,result,callback = self.events.get_nowait()
                self.busy = False
                self.status.set(title+': '+result)
                if callback:
                    callback()
                    if self.closing and title=='Shutdown complete':
                        return
                if title=='ERROR':
                    messagebox.showerror(title,result)
        except __import__('queue').Empty:
            pass
        if not self.root.winfo_exists():
            return
        p = self.pipeline.latest if self.pipeline else None
        if p:
            self.draw(p)
            age = time.monotonic()-p['receive_timestamp']
            s = self.engine.state if self.engine else 'OFFLINE'
            elapsed = max(.1,time.monotonic()-self.pipeline.started)
            value = lambda k: 'Unknown' if p.get(k) is None else str(round(p[k],3))
            self.metrics.set(f'Frame: {p["frame_width"]} × {p["frame_height"]}\n'
                             f'Person height: {value("bbox_height")} px\n'
                             f'Ratio: {100*p.get("person_ratio_raw",0):.1f}%\nTarget: {100*self.cfg["target_ratio"]:.1f}%\n'
                             f'Zoom position: {value("zoom_position")}\nSource: {p.get("zoom_position_source") or "Unknown"}\n'
                             f'Command: {self.motor.current if self.motor else "OFFLINE"}\nLast pulse: {self.pulse_ms.get()} ms\n'
                             f'Capture FPS: {self.pipeline.capture_count/elapsed:.1f}\nInference FPS: {self.pipeline.inference_count/elapsed:.1f}\n'
                             f'Frame age: {age:.2f} s\nConfidence: {value("confidence")}\n'
                             f'State: {s}\nProgress: {self.engine.progress}\nValid: {self.engine.valid}; errors: {self.engine.invalid}\n'
                             f'Recording: {self.pipeline.recorder.enabled.is_set()}\nReason: {p.get("invalid_reason","")}')
            if self.charts_on.get():
                self.draw_charts()
            else:
                self.chart.delete('all')
        self.root.after(80,self.refresh)
    def draw(self,p):
        from PIL import Image,ImageTk
        w,h = max(1,self.view.winfo_width()),max(1,self.view.winfo_height())
        fw,fh = p['frame_width'],p['frame_height']
        scale = min(w/fw,h/fh)
        dw,dh = max(1,int(fw*scale)),max(1,int(fh*scale))
        ox,oy = (w-dw)/2,(h-dh)/2
        image = Image.fromarray(p['image'][:,:,::-1]).resize((dw,dh))
        self.photo = ImageTk.PhotoImage(image)
        self.view.delete('all'); self.view.create_image(ox,oy,image=self.photo,anchor='nw')
        for i,d in enumerate(p['detections']):
            b = d['bbox']
            coords = [ox+b[0]*scale,oy+b[1]*scale,ox+b[2]*scale,oy+b[3]*scale]
            selected = p.get('bbox_x1')==b[0] and p.get('bbox_y1')==b[1]
            color = '#39ef86' if selected and p['measurement_valid'] else '#ffbb44'
            self.view.create_rectangle(*coords,outline=color,width=2)
            self.view.create_text(coords[0]+3,coords[1]+3,anchor='nw',fill=color,
                text=f'Person {i} conf {d["confidence"]:.2f} | h {b[3]-b[1]} / {fh} px | {(b[3]-b[1])/fh:.1%}')
        self.view.create_text(10,10,anchor='nw',fill='white',text=f'Target {p.get("target_id")} | Zoom {p.get("zoom_position") if p.get("zoom_position") is not None else "Unknown"} | {self.motor.current if self.motor else "OFFLINE"}\n{self.engine.state} | received {p["wall_timestamp"]:.3f}')
        self.display_packet = p
        self.view_transform = scale,ox,oy
    def draw_charts(self):
        rows = self.pipeline.observations()[-240:]
        self.chart.delete('all')
        if len(rows)<2:
            return
        valid = [r for r in rows if r['measurement_valid']]
        w,h = self.chart.winfo_width(),self.chart.winfo_height()
        t0,t1 = rows[0]['receive_timestamp'],rows[-1]['receive_timestamp']
        base = next((r.get('person_ratio_raw') for r in valid),None)
        series = [('Person ratio (%)',[(r['receive_timestamp'],r.get('person_ratio_raw',0)*100) for r in valid]),
                  ('BBox height (px)',[(r['receive_timestamp'],r.get('bbox_height',0)) for r in valid]),
                  ('Command (+1 IN / 0 STOP / -1 OUT)',[(r['receive_timestamp'],{'zoom_in':1,'zoom_out':-1}.get(r.get('current_command'),0)) for r in rows]),
                  ('Relative ratio response (to window baseline)',[(r['receive_timestamp'],r.get('person_ratio_raw',0)/base-1) for r in valid] if base else [])]
        for i,(title,points) in enumerate(series):
            ytop = i*h/4
            self.chart.create_text(5,ytop+3,anchor='nw',text=title,fill='#333333')
            if not points:
                continue
            values = [p[1] for p in points]
            lo,hi = min(values),max(values)
            if hi==lo:
                hi=lo+1
            coords = []
            for t,v in points:
                coords.extend((105+(w-115)*(t-t0)/max(.001,t1-t0),ytop+23+(h/4-28)*(1-(v-lo)/(hi-lo))))
            if len(coords)>=4:
                self.chart.create_line(*coords,fill=['#16804a','#3067d5','#d87819','#933daf'][i],width=2)
            self.chart.create_text(5,ytop+24,anchor='nw',text=f'{lo:.3g}…{hi:.3g}')
