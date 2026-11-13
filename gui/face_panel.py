"""Small bounded overlay rendered after Digital PTZ, with no inference or Tk calls."""
import cv2


def draw_face_panel(frame, panel, debug=False):
    if not panel:
        return
    h,w = frame.shape[:2]
    width, height = min(230,w), min(180,h)
    left = w-width
    cv2.rectangle(frame,(left,0),(w-1,height-1),(25,25,25),-1)
    crop = panel.get('crop')
    if crop is not None and width >= 100 and height >= 100:
        ch,cw = crop.shape[:2]
        scale = min(90/cw,90/ch)
        thumb = cv2.resize(crop,(max(1,int(cw*scale)),max(1,int(ch*scale))))
        th,tw = thumb.shape[:2]
        frame[5:5+th,left+5:left+5+tw] = thumb
    rec = panel.get('recognition') or {}
    status = dict(UNIDENTIFIED='Searching',COLLECTING='Searching',RECOGNIZING='Recognizing',
                  CANDIDATE='Searching',IDENTIFIED='Identified',UNKNOWN='Unknown',REVERIFY='Reverify')
    label = status.get(panel['state'],'Searching')
    if panel['state'] == 'IDENTIFIED' and not panel.get('face_visible', True):
        label += ' / face lost'
    lines = [label, str(rec.get('final_identity') or '')]
    if rec.get('score') is not None:
        lines.append(f"Cosine similarity: {rec['score']:.3f}")
    if debug:
        lines.append(f"S:{str(panel['session'])[:10]} T:{panel['track']}")
    for i,line in enumerate(lines):
        y = 111+i*17
        if y < height:
            cv2.putText(frame,line,(left+5,y),cv2.FONT_HERSHEY_SIMPLEX,.4,(240,240,240),1)
