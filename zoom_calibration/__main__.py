import argparse
from .config import load, DEFAULT

def main():
    p = argparse.ArgumentParser(description='Independent Zoom Calibration & Profiling Tool')
    p.add_argument('--config',default=str(DEFAULT))
    p.add_argument('--analyze',metavar='SESSION',help='Offline analysis; no GUI or camera connection')
    p.add_argument('--annotate',metavar='SESSION',help='Create annotated video offline')
    p.add_argument('--replay-session',metavar='SESSION',help='Recalculate bbox filtering/gain from saved rows and command log; never sends commands')
    args = p.parse_args()
    if args.analyze:
        from .analysis import analyze
        print(analyze(args.analyze)); return
    if args.annotate:
        from .replay import annotate
        print(annotate(args.annotate)); return
    if args.replay_session:
        from .replay import replay_session
        print(replay_session(args.replay_session)); return
    import tkinter as tk
    from .gui import App
    root = tk.Tk()
    App(root,load(args.config))
    root.mainloop()

if __name__=='__main__':
    main()
